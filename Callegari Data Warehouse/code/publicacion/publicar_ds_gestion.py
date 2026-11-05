"""Publicación integrada inicial. --validar (defecto) revierte; --guardar confirma.

Solo vistas; no reconstruye hechos ni implementa todavía resultados por servicio/ciclo.
"""
from comun.rutas import CODE as DW_CODE
import argparse
import json
from datetime import datetime
from pathlib import Path
from comun import db
from comun.carga_segura import Entradas

VERSION = '2026-10-30-ds-gestion-base-v1'
SQL = '''
CREATE SCHEMA IF NOT EXISTS ds_gestion;
CREATE OR REPLACE VIEW ds_gestion.v_movimiento AS
WITH m AS (
 SELECT 'R:'||v.nro_viaje::text AS movimiento_id,v.nro_viaje,
 false AS es_virtual,cy.id_viaje AS id_cadena,v.sk_fecha AS fecha_documental_id,
 'documental_erp'::text AS origen_fecha,v.equipo_id,v.rampla_id,v.conductor_id,
 v.cliente_id,v.producto_id,v.sit_stretch_id AS tramo_id,v.tipo_operador,
 cy.ciclo_comercial AS ciclo_id,cy.orden_ciclo_comercial,cy.rol_comercial,
 cy.clasificacion_viaje,cy.calidad_trayectoria,cy.km,
 CASE WHEN cy.id_viaje IS NULL THEN 'sin_cadena' ELSE 'en_cadena' END AS estado_cadena
 FROM ventas.fact_viaje_venta v LEFT JOIN ciclos.fact_viaje_ciclo cy
 ON cy.nro_viaje=v.nro_viaje AND cy.es_virtual='N'
 UNION ALL
 SELECT 'V:'||cy.id_viaje,NULL::bigint,true,cy.id_viaje,cy.sk_fecha,
 'heredada_reconstruccion',cy.equipo_id,NULL::bigint,NULL::bigint,
 cy.cliente_id,cy.producto_id,cy.sit_stretch_id,NULL::text,
 cy.ciclo_comercial,cy.orden_ciclo_comercial,cy.rol_comercial,
 cy.clasificacion_viaje,cy.calidad_trayectoria,cy.km,'virtual_reconstruido'
 FROM ciclos.fact_viaje_ciclo cy WHERE cy.es_virtual='S')
SELECT m.movimiento_id,m.nro_viaje,m.es_virtual,m.id_cadena,m.fecha_documental_id,m.origen_fecha,
 m.equipo_id,m.rampla_id,m.conductor_id,m.cliente_id,m.producto_id,m.tramo_id,m.tipo_operador,
 m.ciclo_id,m.orden_ciclo_comercial,m.rol_comercial,m.clasificacion_viaje,m.calidad_trayectoria,
 m.km,m.estado_cadena,c.nombre_completo AS conductor_nombre,
 CASE WHEN m.es_virtual THEN 'no_observado_virtual' WHEN m.conductor_id IS NULL THEN 'sin_id'
      WHEN c.conductor_id IS NULL THEN 'sin_catalogo' ELSE 'encontrado' END AS estado_conductor,
 e.nombre_fuente AS equipo_nombre,r.nombre_fuente AS rampla_nombre,
 s.razon_social AS cliente_nombre,p.producto AS producto_nombre,t.nombre_fuente AS tramo_nombre_actual
FROM m LEFT JOIN dim_comun.dim_conductor c ON c.conductor_id=m.conductor_id
LEFT JOIN dim_comun.dim_equipo e ON e.equipo_id=m.equipo_id
LEFT JOIN dim_comun.dim_equipo r ON r.equipo_id=m.rampla_id
LEFT JOIN dim_comun.dim_socio_negocio s ON s.socio_id=m.cliente_id
LEFT JOIN dim_comun.dim_producto_viaje p ON p.producto_id=m.producto_id
LEFT JOIN dim_comun.dim_tramo_comercial t ON t.tramo_id=m.tramo_id;

CREATE OR REPLACE VIEW ds_gestion.v_ingreso_servicio AS
SELECT 'R:'||nro_viaje::text AS movimiento_id,nro_viaje,sk_fecha AS fecha_documental_id,
 cliente_id,ingreso AS ingreso_estimado,
 CASE WHEN ingreso IS NULL THEN 'faltante' WHEN ingreso=0 THEN 'cero_registrado' ELSE 'registrado' END AS estado_ingreso
FROM ventas.fact_viaje_venta;

CREATE OR REPLACE VIEW ds_gestion.v_costo_fisico AS
SELECT 'R:'||nro_viaje::text AS movimiento_id,false AS es_virtual,
 sk_escenario AS escenario_id,sk_componente AS componente_id,sk_fecha AS fecha_documental_id,
 cantidad,tasa,monto,estado,'escenarios.fact_costo_viaje'::text AS origen
FROM escenarios.fact_costo_viaje
UNION ALL
SELECT 'V:'||id_viaje,true,sk_escenario,sk_componente,sk_fecha,
 cantidad,tasa,monto,estado,'escenarios.fact_costo_virtual'
FROM escenarios.fact_costo_virtual;

CREATE OR REPLACE VIEW ds_gestion.v_costo_comercial AS
SELECT 'R:'||nro_viaje::text AS registro_id,'saldo_comercial_real'::text AS tipo_registro,
 NULL::text AS movimiento_origen_id,'R:'||nro_viaje::text AS movimiento_destino_id,
 sk_escenario AS escenario_id,sk_componente AS componente_id,
 monto_comercial AS monto,estado_comercial AS estado,
 'saldo_redistribuido_sin_linaje_origen_completo'::text AS alcance_trazabilidad
FROM escenarios.fact_costo_viaje
UNION ALL
SELECT 'V:'||id_viaje,'imputacion_virtual','V:'||id_viaje,
 CASE WHEN nro_viaje_destino IS NOT NULL THEN 'R:'||nro_viaje_destino::text END,
 sk_escenario,sk_componente,monto,estado,'origen_y_destino_virtual'
FROM escenarios.fact_costo_virtual;

CREATE OR REPLACE VIEW ds_gestion.v_escenario AS
SELECT sk_escenario AS escenario_id,nombre,descripcion FROM escenarios.dim_escenario;
CREATE OR REPLACE VIEW ds_gestion.v_componente AS
SELECT sk_componente AS componente_id,nombre,unidad,tipo FROM escenarios.dim_componente_costo;
'''


def publicar(con):
    # CREATE OR REPLACE conserva también las columnas añadidas al contrato público.
    from publicacion.contrato_dashboard import SQL_MOV
    a=SQL.index('CREATE OR REPLACE VIEW ds_gestion.v_movimiento')
    b=SQL.index('CREATE OR REPLACE VIEW ds_gestion.v_ingreso_servicio')
    con.execute(SQL[:a]+SQL_MOV+SQL[b:])
    consultas = {
      'movimientos_duplicados': '''SELECT count(*) FROM (SELECT movimiento_id FROM ds_gestion.v_movimiento GROUP BY 1 HAVING count(*)<>1) x''',
      'reales_no_conservados': '''SELECT abs((SELECT count(*) FROM ds_gestion.v_movimiento WHERE NOT es_virtual)-(SELECT count(*) FROM ventas.fact_viaje_venta))''',
      'virtuales_no_conservados': '''SELECT abs((SELECT count(*) FROM ds_gestion.v_movimiento WHERE es_virtual)-(SELECT count(*) FROM ciclos.fact_viaje_ciclo WHERE es_virtual='S'))''',
      'virtual_con_conductor_observado': '''SELECT count(*) FROM ds_gestion.v_movimiento WHERE es_virtual AND conductor_id IS NOT NULL''',
      'costos_sin_movimiento': '''SELECT count(*) FROM ds_gestion.v_costo_fisico f LEFT JOIN ds_gestion.v_movimiento m USING(movimiento_id) WHERE m.movimiento_id IS NULL''',
      'costos_duplicados': '''SELECT count(*) FROM (SELECT movimiento_id,escenario_id,componente_id FROM ds_gestion.v_costo_fisico GROUP BY 1,2,3 HAVING count(*)<>1) x''',
      'ingresos_distintos': '''SELECT count(*) FROM ds_gestion.v_ingreso_servicio p FULL JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE p.nro_viaje IS NULL OR v.nro_viaje IS NULL OR p.ingreso_estimado IS DISTINCT FROM v.ingreso''',
      'filas_fisicas_no_conservadas': '''SELECT abs((SELECT count(*) FROM ds_gestion.v_costo_fisico)-(SELECT count(*) FROM escenarios.fact_costo_viaje)-(SELECT count(*) FROM escenarios.fact_costo_virtual))''',
      'filas_comerciales_no_conservadas': '''SELECT abs((SELECT count(*) FROM ds_gestion.v_costo_comercial)-(SELECT count(*) FROM escenarios.fact_costo_viaje)-(SELECT count(*) FROM escenarios.fact_costo_virtual))''',
      'destinos_comerciales_inexistentes': '''SELECT count(*) FROM ds_gestion.v_costo_comercial c LEFT JOIN ds_gestion.v_movimiento m ON m.movimiento_id=c.movimiento_destino_id WHERE c.movimiento_destino_id IS NOT NULL AND m.movimiento_id IS NULL'''
    }
    # Comparación exacta de importes y estados por clave, no solo de totales.
    for tipo,tabla,clave,prefijo in [('real','fact_costo_viaje','nro_viaje','R:'),('virtual','fact_costo_virtual','id_viaje','V:')]:
        consultas['fisico_distinto_'+tipo]=f'''SELECT count(*) FROM ds_gestion.v_costo_fisico p
          JOIN escenarios.{tabla} f ON p.movimiento_id='{prefijo}'||f.{clave}::text
          AND p.escenario_id=f.sk_escenario AND p.componente_id=f.sk_componente
          WHERE ROW(p.cantidad,p.tasa,p.monto,p.estado) IS DISTINCT FROM ROW(f.cantidad,f.tasa,f.monto,f.estado)'''
        monto='monto_comercial' if tipo=='real' else 'monto'
        estado='estado_comercial' if tipo=='real' else 'estado'
        consultas['comercial_distinto_'+tipo]=f'''SELECT count(*) FROM ds_gestion.v_costo_comercial p
          JOIN escenarios.{tabla} f ON p.registro_id='{prefijo}'||f.{clave}::text
          AND p.escenario_id=f.sk_escenario AND p.componente_id=f.sk_componente
          WHERE ROW(p.monto,p.estado) IS DISTINCT FROM ROW(f.{monto},f.{estado})'''
    checks={}
    for nombre,q in consultas.items():
        print(f'Control ds_gestion: {nombre}...',flush=True)
        checks[nombre]=int(con.uno(q)[0])
    if any(checks.values()):
        raise RuntimeError(f'Publicación inconsistente: {checks}')
    conteos={v:int(con.uno('SELECT count(*) FROM ds_gestion.'+v)[0]) for v in
        ['v_movimiento','v_ingreso_servicio','v_costo_fisico','v_costo_comercial','v_escenario','v_componente']}
    return dict(controles=checks,filas=conteos)


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    modos=p.add_mutually_exclusive_group()
    modos.add_argument('--validar',action='store_true')
    modos.add_argument('--guardar',action='store_true')
    a=p.parse_args()
    carpeta=DW_CODE.parent/'validacion/ds_gestion'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    carpeta.mkdir(parents=True)
    informe=dict(version=VERSION,estado='iniciado',confirmado=False)
    entradas,con=Entradas(),None
    try:
        for f in [Path(__file__),Path(db.__file__),DW_CODE/'comun/carga_segura.py',db.CONFIG]:
            entradas.registrar(f)
        con=db.conectar()
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='15min'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra carga está en curso')
        informe.update(publicar(con))
        entradas.verificar()
        if a.guardar:
            con.commit()
            informe.update(estado='confirmado',confirmado=True)
        else:
            con.conn.rollback()
            informe['estado']='validado_y_revertido'
    except BaseException as e:
        if con is not None:
            con.conn.rollback()
        informe.update(estado='fallido',error=str(e))
        raise
    finally:
        if con is not None:
            con.close()
        informe['entradas']=list(entradas.archivos.values())
        (carpeta/'ejecucion.json').write_text(json.dumps(informe,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Informe: {carpeta}')
    print(json.dumps(informe,ensure_ascii=False,indent=2))


if __name__=='__main__':
    principal()
