"""Puente provisional explícito: el residuo no se presenta como ajuste explicado.

Resultado contable de referencia: líneas EERR 001-009 y 014. Las cuentas sin línea
se muestran aparte; no se decide aquí su inclusión. Las diferencias por población,
fecha de reconocimiento y masas del año base requieren revisión contable.
"""

SQL="""
CREATE SCHEMA IF NOT EXISTS ds_gestion;
CREATE OR REPLACE VIEW costos.v_puente_absorcion_total AS
WITH ingreso AS (
 SELECT sk_fecha/10000 AS anio,sum(ingreso)::float8 AS ingreso_estimado
 FROM ventas.fact_viaje_venta GROUP BY 1),
costo AS (
 SELECT sk_fecha/10000 AS anio,sum(monto) AS costo_real_conocido,
 sum(monto) FILTER(WHERE sk_componente=5) AS reposicion_real,
 count(*) FILTER(WHERE estado<>'ok' OR monto IS NULL) AS componentes_reales_pendientes
 FROM escenarios.fact_costo_viaje WHERE sk_escenario=4 GROUP BY 1),
virtual AS (
 SELECT sk_fecha/10000 AS anio,sum(monto) AS costo_virtual_conocido,
 sum(monto) FILTER(WHERE sk_componente=5) AS reposicion_virtual,
 count(*) FILTER(WHERE estado<>'ok' OR monto IS NULL) AS componentes_virtuales_pendientes
 FROM escenarios.fact_costo_virtual WHERE sk_escenario=4 GROUP BY 1),
ajuste AS (
 SELECT c.sk_fecha_inicio/10000 AS anio,sum(c.costo_ajuste) AS costo_ajuste,
 count(*) FILTER(WHERE c.ajuste_litros<>0 AND c.costo_ajuste IS NULL) AS ajustes_sin_precio
 FROM combustible.fact_viaje_combustible c
 LEFT JOIN ventas.fact_viaje_venta v USING(nro_viaje)
 WHERE c.es_virtual='S' OR v.tipo_operador='propio' GROUP BY 1),
contable AS (
 SELECT f.anio,
 -sum(f.monto) FILTER(WHERE d.linea_eerr='001')::float8 AS ingreso_contable,
 sum(f.monto) FILTER(WHERE d.linea_eerr='014')::float8 AS depreciacion_contable,
 -sum(f.monto) FILTER(WHERE d.linea_eerr IN('001','002','003','004','005','006','007','008','009','014'))::float8 AS resultado_contable_referencia,
 sum(f.monto) FILTER(WHERE d.linea_eerr='000')::float8 AS cuentas_sin_linea_eerr
 FROM costos.fact_contabilidad_mensual f JOIN costos.dim_cuenta_contable d USING(sk_cuenta) GROUP BY 1),
x AS (
 SELECT i.anio,i.ingreso_estimado,
 c.costo_real_conocido,coalesce(v.costo_virtual_conocido,0) AS costo_virtual_conocido,
 i.ingreso_estimado-c.costo_real_conocido-coalesce(v.costo_virtual_conocido,0) AS margen_modelado_subtotal,
 coalesce(c.reposicion_real,0)+coalesce(v.reposicion_virtual,0) AS reposicion_modelada,
 t.depreciacion_contable,coalesce(a.costo_ajuste,0) AS litros_no_asignables_clp,
 t.ingreso_contable-i.ingreso_estimado AS diferencia_ingresos,
 t.resultado_contable_referencia,t.cuentas_sin_linea_eerr,
 c.componentes_reales_pendientes,v.componentes_virtuales_pendientes,a.ajustes_sin_precio
 FROM ingreso i LEFT JOIN costo c USING(anio) LEFT JOIN virtual v USING(anio)
 LEFT JOIN ajuste a USING(anio) LEFT JOIN contable t USING(anio)),
y AS (SELECT *,margen_modelado_subtotal+reposicion_modelada-depreciacion_contable
 +litros_no_asignables_clp+diferencia_ingresos AS resultado_tras_pasos_identificados FROM x)
SELECT *,resultado_contable_referencia-resultado_tras_pasos_identificados AS residuo_por_explicar,
 'provisional; lineas EERR 001-009 y 014; residuo no conciliado; revisar poblacion, desfase y cuentas 000'::text AS alcance
FROM y;
CREATE OR REPLACE VIEW ds_gestion.v_combustible_certificado AS
SELECT id_viaje_fuente AS movimiento_cadena_id,nro_viaje,equipo_id,sk_fecha_inicio AS fecha_documental_id,
 litros_asignados,litros_margen,ajuste_litros,estado_litros,fraccion_certificada,
 fuente_ficha,motivo_bloque,precio_litro_margen,costo_combustible AS costo_original,
 costo_combustible_margen,costo_ajuste
FROM combustible.fact_viaje_combustible;
CREATE OR REPLACE VIEW ds_gestion.v_absorcion_total AS
SELECT id_viaje AS movimiento_cadena_id,nro_viaje,es_virtual,sk_fecha AS fecha_documental_id,
 bolsa,mes,dias,cuota,monto,estado FROM costos.fact_absorcion_viaje;
"""


def publicar(con):
    con.execute(SQL)
