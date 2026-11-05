"""Medición de almacenamiento. No lee contenidos privados ni elimina archivos."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4
from comun.rutas import PROYECTO


def medir_conexion(conn,detalle=True):
    nombre,tamano=conn.execute('SELECT current_database(),pg_database_size(current_database())').fetchone()
    r=dict(estado='medido',base=nombre,bytes=int(tamano),MB=round(tamano/1000000,2),MiB=round(tamano/1048576,2),fecha_utc=datetime.now(timezone.utc).isoformat())
    if detalle:
        filas=conn.execute("""SELECT n.nspname,sum(pg_total_relation_size(t.oid))::bigint
          FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
          WHERE t.relkind IN ('r','m') AND n.nspname NOT LIKE 'pg_%'
          AND n.nspname<>'information_schema' GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        r['esquemas']=[dict(esquema=n,bytes=int(b),MB=round(b/1000000,2)) for n,b in filas]
        filas=conn.execute("""SELECT n.nspname||'.'||t.relname,pg_total_relation_size(t.oid)
          FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
          WHERE t.relkind IN ('r','m') AND n.nspname NOT LIKE 'pg_%'
          AND n.nspname<>'information_schema' ORDER BY 2 DESC LIMIT 15""").fetchall()
        r['mayores_tablas']=[dict(tabla=n,bytes=int(b),MB=round(b/1000000,2)) for n,b in filas]
    return r


def medir_carga(conn):
    """Métrica posterior al commit; su fallo no invalida una carga confirmada."""
    from comun import auditoria_operativa as auditoria
    try:
        r=medir_conexion(conn,detalle=False)
        auditoria.evento('tamano_dw',bytes_dw=r['bytes'])
        return r
    except Exception as e:
        auditoria.evento('medicion_no_disponible',tipo_error=type(e).__name__)
        return dict(estado='no_disponible',tipo_error=type(e).__name__)


def categoria(rel):
    p=rel.parts
    if p[0]=='.ingesta' and len(p)>2:return '/'.join(p[:2])
    if p[:2]==('code','validacion'):return 'code/validacion'
    return p[0] if len(p)>1 else '(raiz)'


def unidad(rel):
    p=rel.parts
    if len(p)>3 and p[0]=='.ingesta' and p[1] in ('paquetes','extracciones','ejecuciones','pruebas_incrementales','auditoria'):
        return '/'.join(p[:3])
    if len(p)>3 and p[0]=='validacion':return '/'.join(p[:3])
    if len(p)>4 and p[:3]==('code','validacion','ejecuciones'):return '/'.join(p[:4])
    if len(p)>2 and p[0]=='.local':return '/'.join(p[:2])
    return None


def inventariar(raiz):
    raiz=Path(raiz).resolve()
    grupos=defaultdict(lambda:dict(archivos=0,bytes_logicos=0))
    unidades=defaultdict(lambda:dict(archivos=0,bytes_logicos=0))
    extensiones=defaultdict(lambda:dict(archivos=0,bytes_logicos=0))
    errores,enlaces,mayores=[],[],[]
    vistos=set();unicos=0
    pendientes=[raiz]
    while pendientes:
        carpeta=pendientes.pop()
        try:
            with os.scandir(carpeta) as entradas:
                for entrada in entradas:
                    p=Path(entrada.path);rel=p.relative_to(raiz)
                    try:
                        s=entrada.stat(follow_symlinks=False)
                        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                            enlaces.append(str(rel));continue
                        if stat.S_ISDIR(s.st_mode):pendientes.append(p);continue
                        if not stat.S_ISREG(s.st_mode):continue
                        cat=categoria(rel)
                        for grupo in [grupos[cat],extensiones[p.suffix.lower() or '(sin_extension)']]:
                            grupo['archivos']+=1;grupo['bytes_logicos']+=s.st_size
                        u=unidad(rel)
                        if u:
                            unidades[u]['archivos']+=1;unidades[u]['bytes_logicos']+=s.st_size
                        identidad=(s.st_dev,s.st_ino) if s.st_ino else str(rel)
                        if identidad not in vistos:unicos+=s.st_size;vistos.add(identidad)
                        mayores.append(dict(archivo=str(rel),bytes_logicos=s.st_size))
                        if len(mayores)>100:
                            mayores=sorted(mayores,key=lambda r:r['bytes_logicos'],reverse=True)[:30]
                    except OSError as e:errores.append(dict(ruta=str(rel),tipo_error=type(e).__name__))
        except OSError as e:errores.append(dict(ruta=str(carpeta.relative_to(raiz)),tipo_error=type(e).__name__))
    def ordenar(d,clave):
        return sorted([dict(**{clave:n},**v,MB=round(v['bytes_logicos']/1000000,2)) for n,v in d.items()],key=lambda v:v['bytes_logicos'],reverse=True)
    total=sum(v['bytes_logicos'] for v in grupos.values())
    disco=shutil.disk_usage(raiz)
    return dict(estado='medido_parcial' if errores else 'medido',raiz=str(raiz),
        archivos=sum(v['archivos'] for v in grupos.values()),bytes_logicos=total,
        MB=round(total/1000000,2),GiB=round(total/1073741824,3),
        bytes_unicos_identidad=unicos,
        nota='Tamaños lógicos; identidad evita contar hardlinks dos veces en bytes_unicos_identidad. No mide bloques físicos, compresión ni deduplicación por contenido.',
        volumen=dict(total_bytes=disco.total,libres_bytes=disco.free),
        categorias=ordenar(grupos,'categoria'),unidades=ordenar(unidades,'unidad'),
        extensiones=ordenar(extensiones,'extension'),
        mayores_archivos=sorted(mayores,key=lambda r:r['bytes_logicos'],reverse=True)[:30],
        enlaces_no_seguidos=enlaces,errores=errores)


def protecciones(raiz):
    raiz=Path(raiz).resolve()
    protegidas={};advertencias=[]
    def agregar(p,motivo):
        p=Path(p).resolve()
        if p.is_relative_to(raiz):protegidas[str(p.relative_to(raiz))]=motivo
    agregar(raiz/'src','Fuentes base e históricos: no son caché prescindible')
    agregar(raiz/'code/parametros','Contrato y evidencia administrativa')
    for p in ['code/config.ini','.local/credenciales.json','.ingesta/estado.json','.ingesta/actual.json']:
        agregar(raiz/p,'Configuración o estado operativo; no es un temporal')
    for modulo in ['costos','tarificador','taller']:
        for p in (raiz/'code'/modulo).glob('*.csv'):
            agregar(p,'Parametrización y reglas conservadas; verificar antes de archivar')
    for p in ['code/peajes/peajes_datos','code/peajes/tramos_peajes','code/precios/cartas_copec','code/precios/cartas_out']:
        agregar(raiz/p,'Insumos externos/manuales necesarios para reconstruir')
    actual_path=raiz/'.ingesta/actual.json'
    if actual_path.exists():
        try:
            actual=json.loads(actual_path.read_text(encoding='utf-8'))
            agregar(actual['paquete'],'Paquete activo')
            ejecucion=Path(actual['ejecucion'])
            agregar(ejecucion.parent,'Informe y configuración de la publicación activa')
            e=json.loads(ejecucion.read_text(encoding='utf-8'))
            if e.get('etl_id'):agregar(raiz/'code/validacion/ejecuciones'/e['etl_id'],'Evidencia y bloques de la publicación activa')
            pendientes=[Path(actual['paquete'])];visitados=set()
            while pendientes:
                paquete=pendientes.pop().resolve()
                if paquete in visitados:continue
                visitados.add(paquete)
                m=json.loads((paquete/'manifiesto.json').read_text(encoding='utf-8'))
                for f in m.get('fuentes',{}).values():
                    for campo in ['base','delta']:
                        if isinstance(f.get(campo),dict) and f[campo].get('ruta'):
                            p=Path(f[campo]['ruta']).resolve()
                            agregar(p,'Linaje declarado de la fotografía activa')
                            if p.is_relative_to(raiz/'.ingesta/paquetes') and len(p.relative_to(raiz/'.ingesta/paquetes').parts)>1:
                                pendientes.append(raiz/'.ingesta/paquetes'/p.relative_to(raiz/'.ingesta/paquetes').parts[0])
        except (OSError,ValueError,KeyError,TypeError) as e:
            advertencias.append(dict(tipo_error=type(e).__name__,contexto='No se pudo completar el linaje activo; no autoriza limpieza'))
    return dict(rutas=[dict(ruta=k,motivo=v) for k,v in protegidas.items()],advertencias=advertencias,
                alcance='Protecciones mínimas observadas; ausencia en esta lista no autoriza borrar archivos.')


def medir(con_dw=False,raiz=PROYECTO):
    raiz=Path(raiz).resolve()
    historico=raiz/'.ingesta/almacenamiento'
    previos=sorted(historico.glob('*/informe.json'),reverse=True)
    salida=historico/(datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    salida.mkdir(parents=True)
    r=dict(version='2026-10-31-almacenamiento-v1',codigo_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           inicio_utc=datetime.now(timezone.utc).isoformat(),estado='iniciado',borra_archivos=False)
    try:
        r['archivos']=inventariar(raiz)
        r['protecciones']=protecciones(raiz)
        for u in r['archivos']['unidades']:
            p=raiz/u['unidad']
            u['protegida_por_dependencia']=any(p.is_relative_to(raiz/x['ruta']) or (raiz/x['ruta']).is_relative_to(p) for x in r['protecciones']['rutas'])
        r['dw']=dict(estado='no_solicitado')
        if con_dw:
            from comun import db
            import psycopg
            cfg=db.leer_config()['postgres']
            if cfg.get('host','localhost').lower() not in ('localhost','127.0.0.1','::1'):
                raise ValueError('Medición DW limitada a PostgreSQL local')
            with psycopg.connect(**db.parametros_conexion(),connect_timeout=10) as con:
                con.execute('SET TRANSACTION READ ONLY')
                con.execute("SET LOCAL statement_timeout='30s'")
                r['dw']=medir_conexion(con)
        r['comparacion']=None
        for anterior in previos:
            try:
                a=json.loads(anterior.read_text(encoding='utf-8'))
                if a.get('estado') not in ('medido','medido_parcial'):continue
                r['comparacion']=dict(informe_anterior=str(anterior),delta_bytes_logicos=r['archivos']['bytes_logicos']-a['archivos']['bytes_logicos'])
                if r['dw'].get('base') and r['dw'].get('base')==a.get('dw',{}).get('base'):
                    r['comparacion']['delta_bytes_dw']=r['dw']['bytes']-a['dw']['bytes']
                break
            except (OSError,ValueError,KeyError):continue
        r['estado']=r['archivos']['estado']
        return salida,r
    except BaseException as e:
        r.update(estado='fallido',tipo_error=type(e).__name__)
        raise
    finally:
        r['fin_utc']=datetime.now(timezone.utc).isoformat()
        (salida/'informe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Informe:',salida/'informe.json',flush=True)
