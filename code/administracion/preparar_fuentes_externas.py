"""Reutiliza lectores Copec/MOP en una copia aislada; no activa ni escribe en el DW.

--fuente peajes: descarga MOP y tabula (o --solo-integrar para prueba sin red).
--fuente copec --origen CARPETA: importa PDFs recibidos y ejecuta lector existente.
Por defecto muestra plan. --ejecutar prepara una versión; --forzar ignora cadencia.
"""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timedelta
from comun import db,ingesta


def cargar_modulo(ruta,nombre):
    spec=importlib.util.spec_from_file_location(nombre,ruta)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def hashes(carpeta):
    return {str(p.relative_to(carpeta)):ingesta.huella(p) for p in sorted(carpeta.rglob('*')) if p.is_file()}


def estado_externas():
    c,raiz,_=ingesta.configuracion()
    ruta=raiz/'externas/estado.json'
    return c,raiz,ruta,json.loads(ruta.read_text(encoding='utf-8')) if ruta.exists() else {}


def ejecutar(fuente,origen=None,solo_integrar=False,forzar=False):
    c,raiz,estado_path,estado=estado_externas()
    nombre='precio_combustible' if fuente=='copec' else 'peajes'
    seccion=c['revision:'+nombre]
    horas=float(seccion.get('comprobar_cambios_horas',seccion['cada_horas']))
    previo=estado.get(fuente)
    if previo and not forzar and ingesta.ahora()<datetime.fromisoformat(previo['preparada_utc'])+timedelta(hours=horas):
        raise ValueError('Fuente externa aún vigente; usar --forzar si llegaron documentos nuevos')
    cfg=db.leer_config()
    externas=cfg['externas'] if cfg.has_section('externas') else {}
    base=(ingesta.CODE/externas.get('peajes_base','peajes')).resolve() if fuente=='peajes' else (ingesta.CODE/externas.get('precios_salidas','precios/cartas_out')).resolve().parent
    run=raiz/'externas'/fuente/(ingesta.ahora().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    run.mkdir(parents=True)
    informe=dict(fuente=fuente,estado='iniciada',base_anterior=str(base),
        preparada_utc=ingesta.ahora().isoformat(),solo_integrar=solo_integrar,configuracion_sha256=ingesta.huella(ingesta.CONFIG))
    try:
        if fuente=='peajes':
            destino=run/'peajes'
            for carpeta in ('peajes_datos','tramos_peajes','dw_peajes'):
                shutil.copytree(base/carpeta,destino/carpeta)
            # Relocalizar referencias a los PDF copiados; no depender de una carpeta descartable.
            import pandas as pd
            for csv in (destino/'peajes_datos').glob('*.csv'):
                try:
                    d=pd.read_csv(csv,dtype=str,keep_default_na=False)
                except pd.errors.EmptyDataError:
                    continue
                if 'archivo' not in d: continue
                def mover(valor):
                    p=Path(valor)
                    if valor and p.is_absolute() and p.is_relative_to(base):
                        nuevo=destino/p.relative_to(base)
                        if nuevo.exists(): return str(nuevo.resolve())
                    if valor and not p.is_absolute():
                        nuevo=destino/p
                        if nuevo.exists(): return str(nuevo.resolve())
                    return valor
                d['archivo']=d.archivo.map(mover);d.to_csv(csv,index=False)
            modulo=cargar_modulo(ingesta.CODE/'peajes/peajes_descarga.py','descarga_mop_aislada')
            informe['codigo_sha256']=ingesta.huella(ingesta.CODE/'peajes/peajes_descarga.py')
            args=['--carpeta',str(destino/'peajes_datos')]
            if solo_integrar: args+=['--solo-integrar']
            codigo=modulo.principal(args)
            if codigo not in (None,0): raise RuntimeError('Descargador MOP no terminó correctamente')
            requerida=destino/'peajes_datos/tabla_integrada.csv'
            if not requerida.is_file() or pd.read_csv(requerida).empty:
                raise ValueError('No se prepararon tarifas integradas')
            informe['configuracion_externas']={'peajes_base':str(destino.resolve())}
        else:
            destino=run/'precios'
            for carpeta in ('cartas_copec','cartas_out'):
                shutil.copytree(base/carpeta,destino/carpeta)
            modulo=cargar_modulo(ingesta.CODE/'precios/leer_cartas.py','lector_cartas_aislado')
            informe['codigo_sha256']=ingesta.huella(ingesta.CODE/'precios/leer_cartas.py')
            modulo.CARPETA_PDF=destino/'cartas_copec/pdf'
            modulo.CORREOS=destino/'cartas_copec/correos.csv'
            modulo.SALIDA=destino/'cartas_out'
            args=['--importar',str(Path(origen).resolve())] if origen else []
            modulo.principal(args)
            informe['recepcion_correo']='no_verificada; solo procesa archivos entregados'
            informe['configuracion_externas']={'precios_salidas':str(modulo.SALIDA.resolve())}
        informe['archivos_sha256']=hashes(destino)
        informe['estado']='preparada_no_activada'
        ingesta.escribir_json(run/'manifiesto.json',informe)
        # Solo una ejecución completa de descarga/proceso cuenta para la cadencia normal.
        if not solo_integrar:
            estado[fuente]=dict(preparada_utc=informe['preparada_utc'],manifiesto=str((run/'manifiesto.json').resolve()))
            ingesta.escribir_json(estado_path,estado)
        return run,informe
    except BaseException as e:
        informe.update(estado='fallida',tipo_error=type(e).__name__)
        raise
    finally:
        ingesta.escribir_json(run/'manifiesto.json',informe)


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fuente',choices=['copec','peajes'],required=True)
    p.add_argument('--origen',type=Path)
    p.add_argument('--ejecutar',action='store_true')
    p.add_argument('--solo-integrar',action='store_true')
    p.add_argument('--forzar',action='store_true')
    a=p.parse_args()
    if a.origen and a.fuente!='copec': p.error('--origen solo corresponde a cartas Copec')
    if a.solo_integrar and a.fuente!='peajes': p.error('--solo-integrar solo corresponde a peajes')
    if not a.ejecutar:
        c,_,_,estado=estado_externas()
        nombre='precio_combustible' if a.fuente=='copec' else 'peajes'
        print(json.dumps(dict(fuente=a.fuente,configuracion=dict(c['revision:'+nombre]),ultima_preparacion=estado.get(a.fuente),accion='preparar sin activar; no accede al correo'),ensure_ascii=False,indent=2));return
    run,r=ejecutar(a.fuente,a.origen,a.solo_integrar,a.forzar)
    print(json.dumps({k:v for k,v in r.items() if k!='archivos_sha256'},ensure_ascii=False,indent=2))
    print('Manifiesto externo: '+str(run/'manifiesto.json'))


if __name__=='__main__': principal()
