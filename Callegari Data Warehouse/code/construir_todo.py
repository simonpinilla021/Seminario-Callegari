#!/usr/bin/env python3
"""Carga general protegida. Sin opciones/--plan: revisión sin conectar.
--validar: reconstruye, concilia y revierte PostgreSQL.
--guardar: confirma solo tras todas las etapas y controles.
--sin-litros exige --bloques con procedencia verificable.
"""
import argparse
import importlib.util
import json
import os
import runpy
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pandas as pd
from comun import db
from comun import auditoria_operativa as auditoria
from comun.carga_segura import Compartida, Entradas, sha256, huella_ciclos, comprobar_bloques
from comun import calendario
from comun import tramos
from comun import resultado_servicio
from comun import resultado_ciclo
from dimensiones import preparar_dimensiones_comunes as preparar_dim
from dimensiones import cargar_dimensiones_comunes as cargar_dim
from publicacion import publicar_ds_gestion as ds_gestion

MVP = Path(__file__).resolve().parent
VERSION = '2026-11-03-orquestador-conciliacion-operacional-v9'
ESQUEMAS = ('comun','peajes','combustible','precios','costos','ventas','ciclos','tarificador','escenarios','taller','dim_comun')
ETAPAS = [('peajes','construir_dw_peajes.py'),('ventas','construir_dw_ventas.py'),
          ('ciclos','construir_dw_ciclos.py'),('precios','construir_dw_precios.py'),
          ('combustible','construir_dw_combustible.py'),('combustible','estimar_costo_tarificador.py'),
          ('costos','construir_dw_costos.py'),('tarificador','construir_dw_tarificador.py'),
          ('escenarios','construir_dw_escenarios.py'),('taller','construir_dw_taller.py')]


def modulo(ruta,nombre):
    sys.path.insert(0,str(ruta.parent))
    spec = importlib.util.spec_from_file_location(nombre,ruta)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def revisar_fuentes():
    resultado = {}
    from comun.contrato_parametros import validar as validar_parametros
    contrato_parametros = validar_parametros()
    for clave in ('erp','combustible','contabilidad','tablas'):
        carpeta = db.ruta_config('fuentes',clave)
        if not carpeta.is_dir():
            raise FileNotFoundError(f'Fuente {clave} no existe: {carpeta}')
        archivos = list(carpeta.glob('*.parquet'))
        if not archivos:
            raise FileNotFoundError(f'Sin Parquet en fuente {clave}: {carpeta}')
        resultado[clave] = {'carpeta':str(carpeta),'parquet':len(archivos)}
    for clave,sufijos in {
        'erp':('travelvta_v.parquet','traveltramo_v.parquet','travelveh_v.parquet',
               'socneg_v.parquet','traveltcarga_v.parquet','traveldriv_v.parquet'),
        'combustible':('guiascombust_v.parquet',),
        'contabilidad':('rv_fact_acct.parquet',),
        'tablas':('sit_workorder.parquet',),
    }.items():
        carpeta = Path(resultado[clave]['carpeta'])
        for sufijo in sufijos:
            if not list(carpeta.glob('*'+sufijo)):
                raise FileNotFoundError(f'Falta {sufijo} en {carpeta}')
    resultado['contrato_parametros'] = contrato_parametros
    return resultado


def cargar_dimensiones(compartida, args, carpeta, reporte, entradas):
    fuentes = db.ruta_config('fuentes','erp')
    preparacion = carpeta/'dimensiones'
    extraccion = args.extraccion_dimensiones or fuentes.name
    preparar_dim.preparar(fuentes, extraccion, preparacion)
    # Incluye manifiesto y archivos de esta fotografía en el control general.
    for archivo in preparacion.iterdir():
        if archivo.is_file():
            entradas.registrar(archivo)
    tablas, huella = cargar_dim.leer_preparacion(preparacion)
    controles = cargar_dim.cargar(db.Base(compartida), tablas)
    reporte['dimensiones'] = dict(preparacion=str(preparacion), extraccion_id=extraccion,
                                  preparacion_sha256=huella, controles_lectura=controles)
    return tablas


def controles_finales(conn):
    from comun.controles_dw import controles
    return controles(conn,False)


def ejecutar(conn,args,carpeta,reporte,entradas):
    compartida = Compartida(conn)
    original = db.conectar

    def conectar(esquemas=None,dbname=None,_conn=None):
        if dbname is not None or (_conn is not None and _conn is not compartida):
            raise RuntimeError('Una etapa intentó utilizar otra conexión/base')
        return original(esquemas,_conn=compartida)

    if not conn.execute('SELECT pg_try_advisory_xact_lock(20261026,6)').fetchone()[0]:
        raise RuntimeError('Ya existe otra carga general en curso')
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='15min'")
    if args.desde_cero:
        for esquema in ESQUEMAS:
            conn.execute(f'DROP SCHEMA IF EXISTS {esquema} CASCADE')
    for archivo in MVP.rglob('*.py'):
        if not {'validacion','diagnostico_cobertura','tests','__pycache__','historico','desarrollo','administracion'} & set(archivo.relative_to(MVP).parts):
            entradas.registrar(archivo)
    for area in ESQUEMAS:
        for archivo in (MVP/area).glob('*.csv'):
            entradas.registrar(archivo)
    for archivo in (MVP/'tarificador/exacto/insumos').iterdir():
        if archivo.is_file():entradas.registrar(archivo)
    cfg=db.leer_config()
    if 'externas' in cfg:
        for valor in cfg['externas'].values():
            raiz=(MVP/valor).resolve()
            for archivo in raiz.rglob('*'):
                if archivo.is_file() and archivo.suffix.lower() in ('.csv','.pdf'):
                    entradas.registrar(archivo)
    bloques = Path(args.bloques).resolve() if args.sin_litros else carpeta/'bloques'
    reporte['bloques'] = str(bloques)
    with patch.object(db,'conectar',side_effect=conectar), \
         patch.object(pd,'read_csv',side_effect=entradas.lector(pd.read_csv)), \
         patch.object(pd,'read_parquet',side_effect=entradas.lector(pd.read_parquet)):
        reporte['fase'] = 'dimensiones_comunes'
        auditoria.evento('etapa_inicio',etapa='dimensiones_comunes')
        print('Preparando y cargando dimensiones comunes en la transacción general...',flush=True)
        tablas_dim = cargar_dimensiones(compartida,args,carpeta,reporte,entradas)
        auditoria.evento('etapa_fin',etapa='dimensiones_comunes')
        for i,(area,nombre) in enumerate(ETAPAS):
            if nombre == 'construir_dw_combustible.py':
                reporte['fase'] = 'asignacion_litros'
                auditoria.evento('etapa_inicio',etapa='asignacion_litros')
                cadena = huella_ciclos(conn)
                if args.sin_litros:
                    procedencia = comprobar_bloques(bloques,cadena)
                    for entrada in procedencia['entradas']:
                        entradas.registrar(entrada['archivo'])
                else:
                    print('Asignando litros en carpeta aislada de esta ejecución...',flush=True)
                    bloques.mkdir()
                    with patch.dict(os.environ,{'DW_BLOQUES_SALIDA':str(bloques)}):
                        contexto_litros = runpy.run_path(str(MVP/'combustible/asignar_litros.py'),run_name='asignar_litros')
                        from combustible.certificado_dw import ejecutar_p7
                        conn.execute("CREATE SCHEMA IF NOT EXISTS combustible")
                        ejecutar_p7(contexto_litros, db.Base(compartida))
                    entradas.verificar()
                    procedencia = dict(ciclos_sha256=cadena,entradas=list(entradas.archivos.values()),
                                       salidas={p.name:sha256(p) for p in bloques.glob('*.csv')})
                    for requerido in ('litros_por_viaje.csv','bloques.csv','cargas_bloque.csv',
                                      'puente_viaje_bloque.csv','bloques_certificados.csv',
                                      'rendimiento_camion_mes.csv','ficha_consumo_camion_mes.csv',
                                      'litros_viaje_certificado.csv','generacion_camion.csv'):
                        if requerido not in procedencia['salidas']:
                            raise RuntimeError(f'Asignación incompleta: falta {requerido}')
                    (bloques/'bloques_procedencia.json').write_text(json.dumps(procedencia,indent=2),encoding='utf-8')
                auditoria.evento('etapa_fin',etapa='asignacion_litros')
            reporte['fase'] = nombre
            auditoria.evento('etapa_inicio',etapa=nombre)
            print(f'Etapa {i+1}/{len(ETAPAS)}: {nombre}',flush=True)
            inicio = time.perf_counter()
            m = modulo(MVP/area/nombre,'carga_general_'+Path(nombre).stem)
            argv = ['--salidas',str(bloques)] if nombre == 'construir_dw_combustible.py' else []
            if 'externas' in cfg:
                if area=='peajes' and cfg['externas'].get('peajes_base'):
                    argv=['--base',str((MVP/cfg['externas']['peajes_base']).resolve())]
                if area=='precios' and cfg['externas'].get('precios_salidas'):
                    argv=['--salidas',str((MVP/cfg['externas']['precios_salidas']).resolve())]
            m.principal(argv,_conn=compartida)
            reporte['etapas'].append(dict(etapa=nombre,version=getattr(m,'VERSION',None),
                                          segundos=round(time.perf_counter()-inicio,2)))
            auditoria.evento('etapa_fin',etapa=nombre,segundos=round(time.perf_counter()-inicio,2))
            if compartida.abortada:
                raise RuntimeError('Una etapa revirtió la transacción compartida')
        reporte['fase'] = 'conciliaciones'
        auditoria.evento('etapa_inicio',etapa='conciliaciones')
        for esquema,tabla in conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname=ANY(%s)",
                                         (list(ESQUEMAS),)).fetchall():
            from psycopg import sql
            conn.execute(sql.SQL('ANALYZE {}.{}').format(sql.Identifier(esquema),sql.Identifier(tabla)))
        checks,resumen = controles_finales(conn)
        reporte.update(controles=checks,resumen=resumen)
        if any(checks.values()):
            raise RuntimeError('Fallaron conciliaciones: no se confirma ninguna etapa')
        reporte['dimensiones']['controles_finales'] = cargar_dim.verificar_lectura(db.Base(compartida),tablas_dim)
        reporte['calendario_referencias'] = calendario.verificar_referencias(db.Base(compartida))
        reporte['tramo_versiones'] = tramos.publicar(db.Base(compartida))
        reporte['ds_gestion'] = ds_gestion.publicar(db.Base(compartida))
        reporte['resultado_servicio'] = resultado_servicio.publicar(db.Base(compartida), materializar_controles=True)
        reporte['resultado_ciclo'] = resultado_ciclo.publicar(db.Base(compartida), materializar_controles=True)
        from publicacion import contrato_dashboard
        reporte['contrato_dashboard'] = contrato_dashboard.publicar(db.Base(compartida))
        from publicacion import contrato_retornos
        reporte['contrato_retornos'] = contrato_retornos.publicar(db.Base(compartida))
        from tarificador.exacto.publicacion import adaptar_resultados
        reporte['tarificador_exacto'] = adaptar_resultados(db.Base(compartida))
        from publicacion import conciliacion_operacional
        reporte['conciliacion_operacional'] = conciliacion_operacional.publicar(db.Base(compartida))
        from publicacion import costos_absorcion
        reporte['costos_absorcion'] = costos_absorcion.publicar(db.Base(compartida))
        auditoria.evento('etapa_fin',etapa='conciliaciones')
        entradas.verificar()


@auditoria.operacion('construccion')
def principal(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__)
    modos = ap.add_mutually_exclusive_group()
    modos.add_argument('--plan',action='store_true')
    modos.add_argument('--validar',action='store_true')
    modos.add_argument('--guardar',action='store_true')
    ap.add_argument('--desde-cero',action='store_true')
    ap.add_argument('--sin-litros',action='store_true')
    ap.add_argument('--bloques',type=Path)
    ap.add_argument('--extraccion-dimensiones',help='Identidad de la extracción ERP; por defecto, nombre de su carpeta')
    args = ap.parse_args(argv)
    auditoria.evento('modo',modo='guardar' if args.guardar else 'validar' if args.validar else 'plan')
    if args.sin_litros and not args.bloques:
        ap.error('--sin-litros requiere --bloques y su bloques_procedencia.json')
    if args.bloques and not args.sin_litros:
        ap.error('--bloques se utiliza únicamente con --sin-litros')
    identificador = datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    carpeta = MVP/'validacion/ejecuciones'/identificador
    carpeta.mkdir(parents=True)
    reporte = dict(id=identificador,version=VERSION,fecha=datetime.now().astimezone().isoformat(),confirmado=False,
                   estado='iniciada',fase='fuentes',etapas=[],base_ingreso='estimado; punto 5 pendiente')
    entradas,conn = Entradas(),None
    try:
        auditoria.evento('etapa_inicio',etapa='fuentes')
        entradas.registrar(db.CONFIG)
        for archivo in (MVP/'parametros').glob('*.json'):
            entradas.registrar(archivo)
        reporte['fuentes'] = revisar_fuentes()
        if not (args.guardar or args.validar):
            reporte['estado'] = 'plan; sin conexión ni ETL'
            print(json.dumps(reporte['fuentes'],ensure_ascii=False,indent=2))
            print('Plan: dimensiones_comunes, '+', '.join(nombre for area,nombre in ETAPAS))
            print('Publicación final: versiones de tramo y ds_gestion, después de conciliaciones.')
            print('Conciliación operacional: referencias contables y puente mensual E3, sin modificar costos de escenarios.')
            print('Escenarios 1/2: tarificador exacto para propios y comisión observada para fleteros; 3: certificado y capacidad práctica.')
            print('No se conectó a PostgreSQL ni se ejecutó ninguna carga.')
            return reporte
        cfg = db.leer_config()['postgres']
        if cfg.get('host','localhost').lower() not in ('localhost','127.0.0.1','::1'):
            raise RuntimeError('Carga protegida limitada a PostgreSQL local')
        reporte['destino'] = {k:cfg.get(k,'') for k in ('host','port','dbname','user')}
        db.crear_base_si_falta()
        conn = db.conectar().conn
        ejecutar(conn,args,carpeta,reporte,entradas)
        reporte['entradas'] = list(entradas.archivos.values())
        if args.guardar:
            conn.execute('''CREATE TABLE IF NOT EXISTS comun.registro_ejecucion (
              id text PRIMARY KEY,fecha timestamptz,manifiesto jsonb)''')
            manifiesto = dict(reporte,confirmado=True,estado='confirmada')
            conn.execute('INSERT INTO comun.registro_ejecucion VALUES(%s,%s,%s::jsonb)',
                         (identificador,reporte['fecha'],json.dumps(manifiesto,ensure_ascii=False)))
            auditoria.evento('confirmacion_inicio',etapa='commit_dw')
            conn.commit()
            reporte.update(confirmado=True,estado='confirmada')
            auditoria.evento('dw_confirmado',informe_id=identificador)
            from comun.almacenamiento import medir_carga
            reporte['almacenamiento_dw']=medir_carga(conn)
        else:
            conn.rollback()
            reporte['estado'] = 'validada; PostgreSQL revertido'
            auditoria.evento('dw_revertido',etapa='rollback_validacion')
        print(reporte['estado'])
    except BaseException as exc:
        if conn is not None:
            conn.rollback()
        reporte.update(estado='fallo; sin confirmación de PostgreSQL',tipo_error=type(exc).__name__,error=str(exc))
        raise
    finally:
        if conn is not None:
            conn.close()
        reporte['entradas'] = list(entradas.archivos.values())
        (carpeta/'manifiesto.json').write_text(json.dumps(reporte,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(f'Manifiesto: {carpeta / "manifiesto.json"}')
    return reporte


if __name__=='__main__':
    auditoria.ejecutar_cli('construccion',principal)
