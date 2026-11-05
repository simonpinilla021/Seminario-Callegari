"""Extracciones completas, inmutables y de solo lectura; activación separada."""
import configparser
import hashlib
import json
import os
import re
import shutil
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from comun import credenciales,db,incremental
from comun import auditoria_operativa as auditoria

CODE=Path(__file__).resolve().parent.parent
CONFIG=CODE/'ingesta.ini'
VERSION='2026-10-30-ingesta-incremental-v3'


def ahora(): return datetime.now(timezone.utc)
def huella(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def escribir_json(ruta,datos):
    ruta=Path(ruta);ruta.parent.mkdir(parents=True,exist_ok=True)
    tmp=ruta.with_suffix('.tmp')
    tmp.write_text(json.dumps(datos,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    tmp.replace(ruta)


def configuracion(ruta=CONFIG):
    c=configparser.ConfigParser(interpolation=None);c.read(ruta,encoding='utf-8-sig')
    raiz=(CODE/c['ingesta']['directorio']).resolve()
    desde=c['ingesta'].get('fecha_desde','2018-01-01')
    date.fromisoformat(desde)
    fuentes={}
    for seccion in c.sections():
        if not seccion.startswith('fuente:'): continue
        nombre=seccion.split(':',1)[1];s=c[seccion]
        if not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*',s['relacion']):
            raise ValueError('Relación inválida en catálogo')
        archivo=s['archivo']
        if Path(archivo).name!=archivo or not re.fullmatch(r'[a-zA-Z0-9_]+\.parquet',archivo):
            raise ValueError('Nombre de archivo inválido')
        if s['grupo'] not in ('erp','contabilidad','tablas'):
            raise ValueError('Grupo inválido')
        horas=float(s['cada_horas'])
        if horas<=0: raise ValueError('Frecuencia debe ser positiva')
        unica=s.get('unica','no').strip().lower()
        if unica not in ('si','sí','no','yes','true','false','1','0'):
            raise ValueError('Valor de unicidad inválido')
        columna_fecha=s.get('columna_fecha')
        if columna_fecha and not re.fullmatch(r'[a-z_][a-z0-9_]*',columna_fecha):
            raise ValueError('Columna de fecha inválida')
        habilitado=s.get('incremental','no').strip().lower()
        if habilitado not in ('si','sí','no'): raise ValueError('Incremental debe ser si/no')
        actualizacion=s.get('columna_actualizacion')
        if habilitado in ('si','sí') and (not columna_fecha or not s.get('clave') or not actualizacion):
            raise ValueError('Incremental requiere fecha, clave de grupo y columna de actualización')
        if actualizacion and not re.fullmatch(r'[a-z_][a-z0-9_]*',actualizacion):
            raise ValueError('Columna de actualización inválida')
        fuentes[nombre]=dict(nombre=nombre,relacion=s['relacion'],grupo=s['grupo'],archivo=archivo,
            cada_horas=horas,columnas=[x.strip() for x in s['columnas'].split(',')],
            clave=s.get('clave'),unica=unica in ('si','sí','yes','true','1'),
            columna_fecha=columna_fecha,fecha_desde=desde if columna_fecha else None,
            incremental=habilitado in ('si','sí'),columna_actualizacion=actualizacion)
        if columna_fecha and columna_fecha not in fuentes[nombre]['columnas']:
            fuentes[nombre]['columnas'].append(columna_fecha)
        if actualizacion and actualizacion not in fuentes[nombre]['columnas']:
            fuentes[nombre]['columnas'].append(actualizacion)
    if c.getint('ingesta','ventana_meses',fallback=5)<1 or c.getint('ingesta','solape_cambios_horas',fallback=48)<0:
        raise ValueError('Ventana o solape inválidos')
    if len({(s['grupo'],s['archivo']) for s in fuentes.values()})!=len(fuentes):
        raise ValueError('Dos fuentes escriben el mismo archivo')
    return c,raiz,fuentes


def pendientes(fuentes,estado,modo='actualizar',fecha=None):
    fecha=fecha or ahora()
    return [n for n,s in fuentes.items() if modo=='inicial' or n not in estado or
        ('columna_fecha' in s and estado[n].get('filtro_fecha')!=filtro_fecha(s)) or
        fecha>=datetime.fromisoformat(estado[n]['extraida_utc'])+timedelta(hours=s['cada_horas'])]


def filtro_fecha(fuente):
    return dict(columna=fuente['columna_fecha'],desde=fuente['fecha_desde']) if fuente.get('columna_fecha') else None


def consulta_fuente(fuente,muestra=None):
    from psycopg import sql
    consulta=sql.SQL('SELECT * FROM {}.{}').format(*map(sql.Identifier,fuente['relacion'].split('.')))
    parametros=()
    if fuente.get('columna_fecha'):
        consulta+=sql.SQL(' WHERE {} >= %s').format(sql.Identifier(fuente['columna_fecha']))
        parametros=(date.fromisoformat(fuente['fecha_desde']),)
    if muestra: consulta+=sql.SQL(' LIMIT {}').format(sql.Literal(muestra))
    return consulta,parametros


def conexion_erp():
    import psycopg
    datos=credenciales.leer('erp')
    if not datos:
        raise RuntimeError('Falta perfil ERP: ejecutar python -B -m administracion.configurar_credenciales --perfil erp')
    con=psycopg.connect(**datos,connect_timeout=10)
    con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    return con


def revisar_columnas(fuente,descripcion):
    nombres=[d.name for d in descripcion]
    if len(set(nombres))!=len(nombres): raise ValueError('Columnas duplicadas en origen')
    faltan=set(fuente['columnas'])-set(nombres)
    if faltan: raise ValueError('Columnas requeridas ausentes: '+', '.join(sorted(faltan)))


def esquema_arrow(descripcion):
    tipos={16:pa.bool_(),17:pa.binary(),20:pa.int64(),21:pa.int16(),23:pa.int32(),
           700:pa.float32(),701:pa.float64(),1082:pa.date32(),
           1114:pa.timestamp('us'),1184:pa.timestamp('us',tz='UTC')}
    return pa.schema([pa.field(d.name,tipos.get(d.type_code,pa.string())) for d in descripcion])


def tabla_arrow(filas,esquema):
    columnas=[]
    for i,campo in enumerate(esquema):
        valores=[f[i] for f in filas]
        if pa.types.is_string(campo.type):
            valores=[None if v is None else json.dumps(v,ensure_ascii=False,default=str)
                if isinstance(v,(dict,list)) else str(v) for v in valores]
        columnas.append(pa.array(valores,type=campo.type))
    return pa.Table.from_arrays(columnas,schema=esquema)


def probar(con,fuentes,timeout):
    from psycopg import sql
    resultado={}
    for nombre,s in fuentes.items():
        # Savepoint permite informar todos los permisos faltantes sin abortar las demás pruebas.
        con.execute('SAVEPOINT prueba_fuente')
        try:
            con.execute("SELECT set_config('statement_timeout',%s,true)",(str(timeout*1000),))
            with con.cursor() as cur:
                cur.execute(sql.SQL('SELECT * FROM {}.{} LIMIT 0').format(*map(sql.Identifier,s['relacion'].split('.'))))
                revisar_columnas(s,cur.description)
            resultado[nombre]={'estado':'accesible','relacion':s['relacion']}
        except Exception as e:
            con.execute('ROLLBACK TO SAVEPOINT prueba_fuente')
            resultado[nombre]={'estado':'fallido','relacion':s['relacion'],'tipo_error':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None)}
        finally:
            con.execute('RELEASE SAVEPOINT prueba_fuente')
    return resultado


def descargar_una(con,s,destino,lote,timeout,muestra=None,plan=None):
    from psycopg import sql
    con.execute("SELECT set_config('statement_timeout',%s,true)",(str(timeout*1000),))
    destino=Path(destino);destino.parent.mkdir(parents=True,exist_ok=True)
    tmp=destino.with_suffix('.part')
    es_delta=plan and plan['tipo']=='incremental'
    if es_delta and muestra: raise ValueError('Una muestra no puede ser un delta consolidable')
    consulta,parametros=incremental.consulta(s,plan) if es_delta else consulta_fuente(s,muestra)
    escritor=None;filas=0
    try:
        with con.cursor(name='extraer_'+uuid4().hex) as cur:
            cur.execute(consulta,parametros)
            # La primera lectura aporta metadatos Y filas: conservarlas siempre.
            bloque=cur.fetchmany(lote)
            revisar_columnas(s,cur.description)
            esquema=esquema_arrow(cur.description)
            tipos_postgres={d.name:d.type_code for d in cur.description}
            escritor=pq.ParquetWriter(tmp,esquema,compression='snappy')
            while bloque:
                escritor.write_table(tabla_arrow(bloque,esquema));filas+=len(bloque)
                bloque=cur.fetchmany(lote)
        escritor.close();escritor=None
        if not filas and not es_delta: raise ValueError('Fuente obligatoria vacía; no se acepta para reconstruir')
        if pq.read_metadata(tmp).num_rows!=filas: raise ValueError('Conteo Parquet distinto')
        estadisticas={}
        if s.get('clave'):
            import pandas as pd
            clave=pq.read_table(tmp,columns=[s['clave']]).column(0).to_pandas()
            estadisticas=dict(claves_nulas=int(clave.isna().sum()),filas_clave_repetida=int(clave.duplicated().sum()))
            if estadisticas['claves_nulas'] or (s['unica'] and estadisticas['filas_clave_repetida']):
                raise ValueError('Clave inválida en '+s['nombre'])
        tmp.replace(destino)
        return dict(ruta=str(destino.resolve()),sha256=huella(destino),filas=filas,
            extraida_utc=ahora().isoformat(),relacion=s['relacion'],grupo=s['grupo'],
            archivo=s['archivo'],muestra=bool(muestra),claves=estadisticas,
            tipos_postgres=tipos_postgres,filtro_fecha=filtro_fecha(s),
            tipo_extraccion='delta' if es_delta else 'completa')
    finally:
        if escritor is not None: escritor.close()


def fuentes_paquete(paquete,fuentes):
    paquete=Path(paquete).resolve()
    m=json.loads((paquete/'manifiesto.json').read_text(encoding='utf-8'))
    if m.get('estado')!='preparado' or set(m['fuentes'])!=set(fuentes): raise ValueError('Paquete base incompleto')
    for nombre,s in fuentes.items():
        f=m['fuentes'][nombre];p=(paquete/s['grupo']/s['archivo']).resolve()
        if p!=Path(f['ruta']).resolve() or not p.is_relative_to(paquete): raise ValueError('Ruta base inválida')
        if f.get('muestra') or f.get('tipo_extraccion')=='delta' or huella(p)!=f['sha256']:
            raise ValueError('Fuente base alterada o parcial')
        if f.get('filtro_fecha')!=filtro_fecha(s): raise ValueError('Corte base distinto: '+nombre)
    return m['fuentes']


@auditoria.operacion('extraccion')
def ejecutar(modo='actualizar',solo=None,muestra=None,prueba=False,ruta_config=CONFIG,base_paquete=None):
    auditoria.evento('etapa_inicio',etapa='preparar_extraccion',modo=modo)
    c,raiz,fuentes=configuracion(ruta_config)
    estado_path=raiz/'estado.json'
    estado=json.loads(estado_path.read_text(encoding='utf-8')) if estado_path.exists() else {}
    if base_paquete:
        if modo=='inicial': raise ValueError('--base-paquete solo se usa en modo actualizar')
        estado=fuentes_paquete(base_paquete,fuentes)
    nombres=solo or pendientes(fuentes,estado,modo)
    if not set(nombres)<=set(fuentes): raise ValueError('Fuente no registrada')
    if muestra is not None and muestra<=0: raise ValueError('Muestra debe ser positiva')
    run=raiz/'extracciones'/(ahora().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    run.mkdir(parents=True)
    auditoria.evento('manifiesto',informe_id=run.name)
    informe=dict(version=VERSION,estado='iniciada',modo=modo,muestra=bool(muestra),
        solo_lectura_erp=True,configuracion_sha256=huella(ruta_config),fuentes={},
        codigo_sha256=huella(__file__),inicio_utc=ahora().isoformat(),
        incremental_codigo_sha256=huella(incremental.__file__),base_fuentes=estado,fuente_en_curso=None)
    con=None
    try:
        if not nombres:
            informe['estado']='sin_fuentes_vencidas'
            return run,informe
        con=conexion_erp()
        if con.execute('SHOW transaction_read_only').fetchone()[0]!='on':
            raise RuntimeError('La conexión ERP no está en modo solo lectura')
        if prueba:
            informe['pruebas']=probar(con,{n:fuentes[n] for n in nombres},c.getint('ingesta','timeout_seg'))
            informe['estado']='prueba_accesos'
            return run,informe
        marca=con.execute("SELECT transaction_timestamp(), transaction_timestamp() AT TIME ZONE current_setting('TimeZone'), current_setting('TimeZone')").fetchone()
        marcas=dict(marca_cambios_utc=marca[0].astimezone(timezone.utc).isoformat(),marca_cambios_local=marca[1].isoformat(),zona_fuente=marca[2])
        for nombre in nombres:
            auditoria.evento('etapa_inicio',etapa='descargar_'+nombre,fuente=nombre)
            print('Descargando '+nombre+'...',flush=True)
            s=fuentes[nombre]
            informe['fuente_en_curso']=nombre
            base=estado.get(nombre) if modo=='actualizar' and not muestra else None
            if base and base.get('filtro_fecha')!=filtro_fecha(s): base=None
            plan=incremental.planificar(s,base,marca[0],c.getint('ingesta','ventana_meses',fallback=5),
                c.getint('ingesta','solape_cambios_horas',fallback=48))
            if base and plan['tipo']=='incremental' and base.get('zona_fuente')!=marcas['zona_fuente']:
                plan=dict(tipo='completa',motivo='cambio de zona horaria de la fuente')
            destino=run/s['grupo']/s['archivo']
            delta=run/'deltas'/s['grupo']/s['archivo'] if plan['tipo']=='incremental' else destino
            if plan['tipo']=='incremental' and (base.get('muestra') or huella(base['ruta'])!=base['sha256']):
                raise ValueError('Fotografía base alterada o muestra: '+nombre)
            f=descargar_una(con,s,delta,c.getint('ingesta','lote_filas'),c.getint('ingesta','timeout_seg'),muestra,plan)
            if plan['tipo']=='incremental':
                controles=incremental.consolidar(base['ruta'],delta,s,destino,plan,c.getint('ingesta','lote_filas'))
                if huella(base['ruta'])!=base['sha256'] or huella(delta)!=f['sha256']:
                    raise ValueError('Una entrada cambió durante la consolidación: '+nombre)
                f.update(tipo_extraccion='consolidada',delta=dict(ruta=str(delta.resolve()),sha256=f['sha256']),
                    base=dict(ruta=base['ruta'],sha256=base['sha256']),controles_incremental=controles,
                    filas=pq.read_metadata(destino).num_rows,ruta=str(destino.resolve()),sha256=huella(destino))
                clave=pq.read_table(destino,columns=[s['clave']]).column(0).to_pandas()
                f['claves']=dict(claves_nulas=int(clave.isna().sum()),filas_clave_repetida=int(clave.duplicated().sum()))
                if controles['claves_ausentes_ventana']:
                    print('AVISO: claves ausentes conservadas en '+nombre+': '+str(controles['claves_ausentes_ventana']),flush=True)
            f.update(marcas,politica_actualizacion=plan)
            informe['fuentes'][nombre]=f
            escribir_json(run/'manifiesto.json',informe)
            auditoria.evento('etapa_fin',etapa='descargar_'+nombre,fuente=nombre,filas=int(f['filas']))
        if huella(ruta_config)!=informe['configuracion_sha256'] or huella(__file__)!=informe['codigo_sha256'] or huella(incremental.__file__)!=informe['incremental_codigo_sha256']:
            raise ValueError('Cambió el código o catálogo durante la extracción')
        informe['fuente_en_curso']=None
        informe['estado']='muestra_no_activable' if muestra else 'descargada'
        return run,informe
    except Exception as e:
        auditoria.diagnostico_error(e)
        informe.update(estado='fallida',tipo_error=type(e).__name__,sqlstate=getattr(e,'sqlstate',None))
        if isinstance(e,ValueError): informe['diagnostico']=str(e)
        raise RuntimeError(f'Extracción fallida ({type(e).__name__}); revisar manifiesto de {run}') from None
    finally:
        if con is not None:
            # Incluso una desconexión durante rollback/close debe dejar el informe.
            # La transacción ERP es de solo lectura; nunca hay un commit pendiente.
            for accion in ('rollback','close'):
                try: getattr(con,accion)()
                except Exception as cierre:
                    informe.setdefault('advertencias_cierre',[]).append(dict(accion=accion,tipo_error=type(cierre).__name__))
        informe['fin_utc']=ahora().isoformat()
        escribir_json(run/'manifiesto.json',informe)


@auditoria.operacion('preparacion_paquete')
def empaquetar(run,ruta_config=CONFIG):
    auditoria.evento('etapa_inicio',etapa='preparar_paquete')
    c,raiz,fuentes=configuracion(ruta_config)
    origen=json.loads((Path(run)/'manifiesto.json').read_text(encoding='utf-8'))
    if origen['estado']!='descargada' or origen['muestra']:
        raise ValueError('Solo extracciones completas pueden formar paquete')
    estado_path=raiz/'estado.json'
    anteriores=json.loads(estado_path.read_text(encoding='utf-8')) if estado_path.exists() else {}
    elegidas=dict(origen.get('base_fuentes',anteriores),**origen['fuentes'])
    if not set(fuentes)<=set(elegidas):
        raise ValueError('Faltan fuentes para un paquete completo: '+', '.join(sorted(set(fuentes)-set(elegidas))))
    paquete=raiz/'paquetes'/Path(run).name
    paquete.mkdir(parents=True,exist_ok=False)
    manifiesto=dict(version=VERSION,estado='preparado',extraccion=str(Path(run).resolve()),fuentes={})
    for nombre,s in fuentes.items():
        f=elegidas[nombre];p=Path(f['ruta'])
        if f.get('filtro_fecha')!=filtro_fecha(s): raise ValueError('Corte de fechas distinto en '+nombre+'; descargar nuevamente')
        if f.get('muestra') or f.get('tipo_extraccion')=='delta' or huella(p)!=f['sha256']: raise ValueError('Fuente alterada o parcial: '+nombre)
        destino=paquete/s['grupo']/s['archivo'];destino.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,destino)
        if huella(destino)!=f['sha256']: raise ValueError('Copia distinta')
        manifiesto['fuentes'][nombre]=dict(f,ruta=str(destino.resolve()))
    escribir_json(paquete/'manifiesto.json',manifiesto)
    return paquete
