"""Coordina extracción, paquete completo, ETL protegido y activación local.

Sin opciones: plan. --validar reconstruye/revierte DW sin activar fuentes.
--guardar confirma DW y activa fuentes. --paquete reutiliza una descarga ya verificada.
Si falla activación después del commit, --reanudar EJECUCION_JSON la completa sin repetir ETL.
"""
import argparse
import configparser
import io
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from comun import db,ingesta,credenciales
from comun import auditoria_operativa as auditoria
import construir_todo


def verificar_paquete(paquete):
    paquete=Path(paquete).resolve()
    m=json.loads((paquete/'manifiesto.json').read_text(encoding='utf-8'))
    _,_,catalogo=ingesta.configuracion()
    if m.get('estado')!='preparado' or set(m['fuentes'])!=set(catalogo):
        raise ValueError('Paquete incompleto o catálogo distinto')
    for n,s in catalogo.items():
        f=m['fuentes'][n];p=(paquete/s['grupo']/s['archivo']).resolve()
        if p!=Path(f['ruta']).resolve() or not p.is_relative_to(paquete):
            raise ValueError('Ruta de fuente fuera del paquete')
        if f.get('muestra') or f.get('tipo_extraccion')=='delta' or ingesta.huella(p)!=f['sha256']:
            raise ValueError('Muestra o huella distinta: '+n)
        if f.get('filtro_fecha')!=ingesta.filtro_fecha(s):
            raise ValueError('El corte temporal del paquete no coincide con ingesta.ini: '+n)
    return m


def verificar_externas(manifiestos):
    ajustes={}
    for ruta in manifiestos:
        ruta=Path(ruta).resolve();m=json.loads(ruta.read_text(encoding='utf-8'))
        if m.get('estado')!='preparada_no_activada': raise ValueError('Preparación externa no válida')
        tipo=m['fuente']
        if tipo not in ('peajes','copec'): raise ValueError('Fuente externa desconocida')
        if not m.get('archivos_sha256'): raise ValueError('Preparación externa vacía')
        base=ruta.parent/('peajes' if tipo=='peajes' else 'precios')
        for rel,h in m['archivos_sha256'].items():
            archivo=(base/rel).resolve()
            if not archivo.is_relative_to(base.resolve()) or ingesta.huella(archivo)!=h:
                raise ValueError('Fuente externa alterada')
        esperados={'peajes_base':base} if tipo=='peajes' else {'precios_salidas':base/'cartas_out'}
        if {k:str(v.resolve()) for k,v in esperados.items()}!=m['configuracion_externas']:
            raise ValueError('Ruta externa no coincide con su preparación')
        ajustes.update(m['configuracion_externas'])
    return ajustes


def configuracion_paquete(paquete,externas=()):
    c=db.leer_config()
    for grupo in ('erp','contabilidad','tablas'):
        c['fuentes'][grupo]=str((Path(paquete)/grupo).resolve())
    # Histórico combustible se conserva: el lector vigente une por ID sin duplicarlo.
    historico=db.ruta_config('fuentes','combustible')
    if historico.is_dir() and list(historico.glob('*.parquet')):
        c['fuentes']['combustible']=str(historico)
    else:
        # En equipo nuevo puede no existir el respaldo histórico privado.
        # El lote ERP actual alimenta ambos lectores; la unión existente deduplica por ID.
        c['fuentes']['combustible']=str((Path(paquete)/'erp').resolve())
    if externas:
        if not c.has_section('externas'): c.add_section('externas')
        c['externas'].update(verificar_externas(externas))
    if c['postgres'].get('password'):
        pg=c['postgres'];destino={k:pg.get(k,v) for k,v in dict(host='localhost',port='5432',dbname='dw_callegari',user='postgres').items()}
        credenciales.guardar('dw',destino,pg['password'])
        c.remove_option('postgres','password')
    return c


def serializar(c):
    s=io.StringIO();c.write(s);return s.getvalue().encode('utf-8')


@auditoria.operacion('activacion')
def activar(ejecucion):
    auditoria.evento('etapa_inicio',etapa='activar_paquete')
    e=json.loads(Path(ejecucion).read_text(encoding='utf-8'))
    if e['estado'] not in ('etl_confirmado_pendiente_activacion','confirmado'):
        raise ValueError('No hay commit ETL confirmado para activar')
    auditoria.evento('dw_confirmado')
    paquete=Path(e['paquete']);m=verificar_paquete(paquete)
    verificar_externas(e.get('externas',()))
    configuracion=Path(e['configuracion_propuesta'])
    if ingesta.huella(configuracion)!=e['configuracion_propuesta_sha256']:
        raise ValueError('Configuración propuesta alterada')
    actual=ingesta.huella(db.CONFIG)
    if actual not in (e['configuracion_original_sha256'],e['configuracion_propuesta_sha256']):
        raise ValueError('config.ini cambió después de la ejecución; revisar antes de activar')
    temporal=db.CONFIG.with_suffix('.tmp')
    temporal.write_bytes(configuracion.read_bytes());temporal.replace(db.CONFIG)
    _,raiz,_=ingesta.configuracion()
    ingesta.escribir_json(raiz/'estado.json',m['fuentes'])
    e['activado_utc']=ingesta.ahora().isoformat()
    ingesta.escribir_json(raiz/'actual.json',dict(paquete=str(paquete),ejecucion=str(Path(ejecucion).resolve()),activado_utc=e['activado_utc']))
    e['estado']='confirmado';ingesta.escribir_json(ejecucion,e)
    auditoria.evento('paquete_activado')
    return e


@auditoria.operacion('carga_paquete')
def ejecutar_paquete(paquete,guardar=False,externas=()):
    auditoria.evento('etapa_inicio',etapa='verificar_paquete')
    verificar_paquete(paquete)
    _,raiz,_=ingesta.configuracion()
    carpeta=raiz/'ejecuciones'/(ingesta.ahora().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    carpeta.mkdir(parents=True)
    ruta=carpeta/'ejecucion.json'
    e=dict(estado='iniciado',paquete=str(Path(paquete).resolve()),dw_confirmado=False,
        configuracion_original_sha256=ingesta.huella(db.CONFIG))
    try:
        cfg=configuracion_paquete(paquete,externas)
        propuesta=carpeta/'config.ejecucion.ini';propuesta.write_bytes(serializar(cfg))
        e.update(configuracion_propuesta=str(propuesta.resolve()),configuracion_propuesta_sha256=ingesta.huella(propuesta))
        e['externas']=[str(Path(p).resolve()) for p in externas]
        with patch.object(db,'leer_config',return_value=cfg),patch.object(db,'CONFIG',propuesta):
            r=construir_todo.principal(['--guardar' if guardar else '--validar','--extraccion-dimensiones',Path(paquete).name])
        if r is None or (guardar and not r.get('confirmado')):
            raise RuntimeError('El orquestador no confirmó el resultado esperado')
        e['etl_id']=r['id']
        if guardar:
            auditoria.evento('dw_confirmado',informe_id=r['id'])
            e.update(dw_confirmado=True,estado='etl_confirmado_pendiente_activacion')
            ingesta.escribir_json(ruta,e)
            return activar(ruta)
        e['estado']='validado_y_revertido_sin_activacion'
        return e
    except BaseException as error:
        if e['dw_confirmado']:
            e.update(estado='etl_confirmado_pendiente_activacion',tipo_error=type(error).__name__)
        else:
            e.update(estado='fallido_sin_confirmacion',tipo_error=type(error).__name__)
        raise
    finally:
        # activar devuelve/guarda un objeto actualizado; no pisar su confirmación.
        if ruta.exists():
            persistido=json.loads(ruta.read_text(encoding='utf-8'))
            if persistido.get('estado')=='confirmado': e=persistido
        ingesta.escribir_json(ruta,e)
        print('Informe de actualización: '+str(ruta))


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    modos=p.add_mutually_exclusive_group()
    modos.add_argument('--validar',action='store_true')
    modos.add_argument('--guardar',action='store_true')
    p.add_argument('--modo',choices=['inicial','actualizar'],default='actualizar')
    p.add_argument('--paquete',type=Path)
    p.add_argument('--reanudar',type=Path)
    p.add_argument('--recalcular',action='store_true')
    p.add_argument('--externas',nargs='+',type=Path,default=[],help='Manifiestos de fuentes externas preparadas')
    a=p.parse_args()
    if a.reanudar:
        if a.paquete or a.validar or a.guardar: p.error('--reanudar no se combina con otras acciones')
        r=activar(a.reanudar)
        print(json.dumps(r,ensure_ascii=False,indent=2));return r
    if not (a.validar or a.guardar):
        print('Plan: descarga por frecuencia, paquete completo, carga transaccional y activación tras commit.')
        print('Para ver fuentes/frecuencias: python -B -m conectores.erp.descargar_fuentes --plan');return dict(estado='plan_sin_ejecucion')
    paquete=a.paquete
    if paquete is None:
        run,r=ingesta.ejecutar(a.modo)
        if r['estado']=='sin_fuentes_vencidas':
            if not a.recalcular:
                print('No hay fuentes vencidas; no se ejecutó ETL.');return dict(estado='sin_fuentes_vencidas')
            _,raiz,_=ingesta.configuracion()
            paquete=Path(json.loads((raiz/'actual.json').read_text(encoding='utf-8'))['paquete'])
        else:
            paquete=ingesta.empaquetar(run)
        print('Paquete: '+str(paquete))
    r=ejecutar_paquete(paquete,a.guardar,a.externas)
    print(json.dumps(r,ensure_ascii=False,indent=2))
    return r


if __name__=='__main__':
    auditoria.ejecutar_cli('actualizacion',principal)
