"""Calidad y cobertura de taller. No corrige fechas ni cambia indicadores existentes."""
VERSION = '2026-10-30-calidad-taller-v1'

CONSULTA_OT = """
WITH base AS (
 SELECT o.sit_workorder_id,o.nro_ot,o.equipo_id,o.tipo_id,t.tipo_documento,
 o.sk_fecha_apertura,o.fecha_hora_apertura,o.fecha_hora_cierre,o.docstatus,
 o.flag_cierre_imputado,
 CASE WHEN o.fecha_hora_cierre IS NULL THEN 'sin_fecha'
      WHEN o.flag_cierre_imputado='N' THEN 'enddate_fuente'
      WHEN o.flag_cierre_imputado='S' THEN 'updated_imputado'
      ELSE 'origen_no_identificado' END AS origen_cierre,
 CASE WHEN o.fecha_hora_apertura IS NULL THEN 'sin_apertura'
      WHEN o.fecha_hora_cierre IS NULL THEN 'sin_cierre'
      WHEN o.fecha_hora_cierre<o.fecha_hora_apertura THEN 'cierre_anterior_apertura'
      ELSE 'intervalo_no_negativo' END AS estado_intervalo,
 coalesce(o.docstatus IN ('CO','CL'),false) AS documento_cerrado
 FROM taller.fact_orden_taller o LEFT JOIN taller.dim_tipo_intervencion t USING(tipo_id)
), x AS (
 SELECT base.*,
 (origen_cierre='enddate_fuente' AND estado_intervalo='intervalo_no_negativo'
  AND documento_cerrado) AS evaluable_duracion_cierre_fuente,
 (origen_cierre='updated_imputado' AND estado_intervalo='intervalo_no_negativo'
  AND documento_cerrado) AS evaluable_duracion_cierre_imputado
 FROM base
)
SELECT x.*,
 CASE WHEN evaluable_duracion_cierre_fuente THEN
 extract(epoch FROM fecha_hora_cierre-fecha_hora_apertura)/3600 END AS horas_ciclo_cierre_fuente,
 CASE WHEN evaluable_duracion_cierre_imputado THEN
 extract(epoch FROM fecha_hora_cierre-fecha_hora_apertura)/3600 END AS horas_ciclo_cierre_imputado
FROM x
"""

CONSULTA_PREVENTIVO = """
WITH x AS (
 SELECT p.*,
 CASE WHEN intervalo IS NULL THEN 'sin_intervalo'
      WHEN intervalo<=0 THEN 'intervalo_no_positivo'
      WHEN unidad IS NULL OR unidad NOT IN ('KM','HORAS','DIAS') THEN 'unidad_no_admitida'
      WHEN ot_siguiente IS NULL THEN 'sin_siguiente_orden'
      WHEN uso_hasta_siguiente IS NULL THEN 'sin_lectura'
      WHEN uso_hasta_siguiente<0 THEN 'lectura_retrocede'
      WHEN estado NOT IN ('a tiempo','atrasada') OR estado IS NULL THEN 'estado_no_evaluable'
      ELSE 'evaluable' END AS motivo_cobertura
 FROM taller.v_cumplimiento_preventivo p
)
SELECT x.*,motivo_cobertura='evaluable' AS evaluable_cumplimiento,
 CASE WHEN motivo_cobertura='evaluable' THEN estado='a tiempo' END AS cumple
FROM x
"""


def sql_vistas():
    return f"""
CREATE OR REPLACE VIEW taller.v_calidad_ot AS {CONSULTA_OT};
COMMENT ON VIEW taller.v_calidad_ot IS 'Calidad documental: created y enddate no acreditan por sí solos inicio/fin físico de taller. CO/CL identifica documento cerrado. No se modifican fechas.';
CREATE OR REPLACE VIEW taller.v_calidad_ot_mensual AS
SELECT date_trunc('month',fecha_hora_apertura)::date AS mes_apertura,tipo_documento,
 count(*) AS ordenes,
 count(*) FILTER(WHERE origen_cierre='enddate_fuente') AS con_cierre_fuente,
 count(*) FILTER(WHERE origen_cierre='updated_imputado') AS con_cierre_imputado,
 count(*) FILTER(WHERE origen_cierre='sin_fecha') AS sin_fecha_cierre,
 count(*) FILTER(WHERE origen_cierre='origen_no_identificado') AS origen_no_identificado,
 count(*) FILTER(WHERE estado_intervalo='sin_apertura') AS sin_apertura,
 count(*) FILTER(WHERE estado_intervalo='cierre_anterior_apertura') AS cierre_anterior_apertura,
 count(*) FILTER(WHERE documento_cerrado) AS documentos_cerrados,
 count(*) FILTER(WHERE evaluable_duracion_cierre_fuente) AS evaluables_cierre_fuente,
 sum(horas_ciclo_cierre_fuente) AS horas_ciclo_cierre_fuente,
 count(*) FILTER(WHERE evaluable_duracion_cierre_imputado) AS evaluables_cierre_imputado,
 sum(horas_ciclo_cierre_imputado) AS horas_ciclo_cierre_imputado
FROM taller.v_calidad_ot GROUP BY 1,2;
CREATE OR REPLACE VIEW taller.v_cobertura_preventiva AS {CONSULTA_PREVENTIVO};
CREATE OR REPLACE VIEW taller.v_cobertura_preventiva_mensual AS
SELECT date_trunc('month',fecha_hora_apertura)::date AS mes_apertura,
 count(*) AS ordenes_preventivas,
 count(*) FILTER(WHERE evaluable_cumplimiento) AS evaluables,
 count(*) FILTER(WHERE NOT evaluable_cumplimiento) AS no_evaluables,
 count(*) FILTER(WHERE motivo_cobertura='sin_intervalo') AS sin_intervalo,
 count(*) FILTER(WHERE evaluable_cumplimiento AND cumple) AS a_tiempo,
 count(*) FILTER(WHERE evaluable_cumplimiento AND NOT cumple) AS atrasadas,
 100.0*count(*) FILTER(WHERE evaluable_cumplimiento)/nullif(count(*),0) AS cobertura_pct,
 100.0*count(*) FILTER(WHERE evaluable_cumplimiento AND cumple)
 /nullif(count(*) FILTER(WHERE evaluable_cumplimiento),0) AS cumplimiento_pct
FROM taller.v_cobertura_preventiva GROUP BY 1;
CREATE OR REPLACE VIEW taller.v_cobertura_imputacion_mensual AS
SELECT date_trunc('month',c.fecha)::date AS mes_consumo,
 count(*) AS lineas_mantencion,sum(c.costo::numeric) AS costo_mantencion,
 count(*) FILTER(WHERE c.equipo_id IS NOT NULL AND c.equipo_id<>-1) AS lineas_con_equipo,
 count(*) FILTER(WHERE c.equipo_id IS NULL OR c.equipo_id=-1) AS lineas_sin_equipo,
 sum(c.costo::numeric) FILTER(WHERE c.equipo_id IS NOT NULL AND c.equipo_id<>-1) AS costo_con_equipo,
 sum(c.costo::numeric) FILTER(WHERE c.equipo_id IS NULL OR c.equipo_id=-1) AS costo_sin_equipo,
 100.0*count(*) FILTER(WHERE c.equipo_id IS NOT NULL AND c.equipo_id<>-1)/nullif(count(*),0) AS cobertura_lineas_pct
FROM taller.fact_consumo_repuesto c JOIN taller.dim_repuesto p USING(producto_id)
WHERE p.es_mantencion='S' AND p.es_combustible='N' GROUP BY 1;
"""


def publicar(con):
    con.execute(sql_vistas())
    consultas = {
      'ot_filas_no_conservadas': 'SELECT abs((SELECT count(*) FROM taller.v_calidad_ot)-(SELECT count(*) FROM taller.fact_orden_taller))',
      'ot_reales_con_imputacion': "SELECT count(*) FROM taller.v_calidad_ot WHERE evaluable_duracion_cierre_fuente AND (flag_cierre_imputado IS DISTINCT FROM 'N' OR estado_intervalo<>'intervalo_no_negativo' OR NOT documento_cerrado)",
      'ot_horas_fuera_evaluacion': 'SELECT count(*) FROM taller.v_calidad_ot WHERE (horas_ciclo_cierre_fuente IS NOT NULL)<>evaluable_duracion_cierre_fuente OR (horas_ciclo_cierre_imputado IS NOT NULL)<>evaluable_duracion_cierre_imputado',
      'ot_resumen_no_conservado': 'SELECT abs((SELECT coalesce(sum(ordenes),0) FROM taller.v_calidad_ot_mensual)-(SELECT count(*) FROM taller.fact_orden_taller))',
      'preventivo_filas_no_conservadas': 'SELECT abs((SELECT count(*) FROM taller.v_cobertura_preventiva)-(SELECT count(*) FROM taller.v_cumplimiento_preventivo))',
      'preventivo_cumple_sin_evaluar': 'SELECT count(*) FROM taller.v_cobertura_preventiva WHERE (cumple IS NOT NULL)<>evaluable_cumplimiento',
      'preventivo_resumen_no_cuadra': 'SELECT count(*) FROM taller.v_cobertura_preventiva_mensual WHERE ordenes_preventivas<>evaluables+no_evaluables OR evaluables<>a_tiempo+atrasadas OR (evaluables=0 AND cumplimiento_pct IS NOT NULL)',
      'mantencion_lineas_no_conservadas': "SELECT abs((SELECT coalesce(sum(lineas_mantencion),0) FROM taller.v_cobertura_imputacion_mensual)-(SELECT count(*) FROM taller.fact_consumo_repuesto c JOIN taller.dim_repuesto p USING(producto_id) WHERE p.es_mantencion='S' AND p.es_combustible='N'))",
      'mantencion_resumen_no_cuadra': 'SELECT count(*) FROM taller.v_cobertura_imputacion_mensual WHERE lineas_mantencion<>lineas_con_equipo+lineas_sin_equipo OR abs(costo_mantencion-coalesce(costo_con_equipo,0)-coalesce(costo_sin_equipo,0))>0.01',
    }
    controles={n:int(con.uno(q)[0]) for n,q in consultas.items()}
    if any(controles.values()):
        raise ValueError('Fallaron controles de calidad de taller: '+str(controles))
    resumen=con.uno("""SELECT count(*),count(*) FILTER(WHERE origen_cierre='updated_imputado'),
      count(*) FILTER(WHERE estado_intervalo='cierre_anterior_apertura') FROM taller.v_calidad_ot""")
    return dict(version=VERSION,controles=controles,ordenes=int(resumen[0]),
                cierres_imputados=int(resumen[1]),cierres_anteriores_apertura=int(resumen[2]))


def probar_semantica(con):
    """Casos artificiales evaluados por PostgreSQL sin tablas ni cambios de datos."""
    prefijo = """WITH ot_fixture(sit_workorder_id,nro_ot,equipo_id,tipo_id,sk_fecha_apertura,
      fecha_hora_apertura,fecha_hora_cierre,docstatus,flag_cierre_imputado) AS (VALUES
      (1,'prueba',1,1,20260101,TIMESTAMP '2026-01-01',TIMESTAMP '2026-01-02','CO','N'),
      (2,'prueba',1,1,20260101,TIMESTAMP '2026-01-01',TIMESTAMP '2026-01-02','CO','S'),
      (3,'prueba',1,1,20260102,TIMESTAMP '2026-01-02',TIMESTAMP '2026-01-01','CO','N'),
      (4,'prueba',1,1,20260101,TIMESTAMP '2026-01-01',TIMESTAMP '2026-01-02','IP','N'),
      (5,'prueba',1,1,20260101,TIMESTAMP '2026-01-01',NULL,'CO','N'),
      (6,'prueba',1,1,NULL,NULL,TIMESTAMP '2026-01-02','CO','N'),
      (7,'prueba',1,1,20260101,TIMESTAMP '2026-01-01',TIMESTAMP '2026-01-01','CL','N')),
      tipos_fixture(tipo_id,tipo_documento) AS (VALUES(1,'OT')), """
    consulta=CONSULTA_OT.replace('taller.fact_orden_taller','ot_fixture').replace('taller.dim_tipo_intervencion','tipos_fixture')
    d=con.df(prefijo+consulta.lstrip()[5:]).set_index('sit_workorder_id')
    if d.loc[d.evaluable_duracion_cierre_fuente].index.tolist()!=[1,7]:
        raise ValueError('Prueba: cierre fuente inválido/abierto se consideró evaluable')
    if d.loc[d.evaluable_duracion_cierre_imputado].index.tolist()!=[2]:
        raise ValueError('Prueba: no se separó el cierre imputado')
    if d.loc[3,'estado_intervalo']!='cierre_anterior_apertura' or d.loc[7,'horas_ciclo_cierre_fuente']!=0:
        raise ValueError('Prueba: intervalo negativo o cero incorrecto')
    prefijo="""WITH preventiva_fixture(sit_workorder_id,intervalo,unidad,ot_siguiente,uso_hasta_siguiente,estado) AS (VALUES
      (1,NULL::numeric,'KM',2,100::numeric,'a tiempo'),
      (2,0,'KM',3,100,'a tiempo'),(3,100,'OTRA',4,90,'a tiempo'),
      (4,100,'KM',NULL,90,'a tiempo'),(5,100,'KM',6,NULL,'a tiempo'),
      (6,100,'KM',7,-1,'a tiempo'),(7,100,'KM',8,90,'a tiempo'),
      (8,100,'KM',9,120,'atrasada')), """
    consulta=CONSULTA_PREVENTIVO.replace('taller.v_cumplimiento_preventivo','preventiva_fixture')
    d=con.df(prefijo+consulta.lstrip()[5:]).set_index('sit_workorder_id')
    if d.loc[d.evaluable_cumplimiento].index.tolist()!=[7,8] or not bool(d.loc[7,'cumple']) or bool(d.loc[8,'cumple']):
        raise ValueError('Prueba: preventivas no evaluables tratadas como cumplimiento')
    if d.loc[~d.evaluable_cumplimiento,'cumple'].notna().any():
        raise ValueError('Prueba: cumple debe ser NULL si no evaluable')
    return dict(casos_ordenes=7,casos_preventivos=8,correctos=True)
