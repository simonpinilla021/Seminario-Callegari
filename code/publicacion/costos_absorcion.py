"""Referencias integradas aditivas para Costos y absorción del fijo.

No modifica hechos, parámetros ni reglas de escenarios. Los días son unidades
modeladas de flota propia, no observaciones de disponibilidad.
"""
VERSION='2026-11-04-costos-absorcion-v1'
SQL='''
CREATE OR REPLACE VIEW ds_gestion.v_rubros_contables_mensual AS
WITH base AS (
 SELECT f.anio*100+f.mes AS mes_id,f.monto::numeric AS monto,d.linea_eerr,
 CASE WHEN d.codigo='310020104' THEN 'comision'
      WHEN d.clase_tarificador='fijo' THEN 'fijo'
      WHEN d.clase_tarificador='combustible' OR d.linea_eerr='007' THEN 'combustible'
      WHEN d.clase_tarificador='peaje' THEN 'peajes'
      WHEN d.clase_tarificador='neumatico' THEN 'neumaticos'
      WHEN d.linea_eerr='006' THEN 'fleteros'
      ELSE 'otros' END AS rubro
 FROM costos.fact_contabilidad_mensual f JOIN costos.dim_cuenta_contable d USING(sk_cuenta)
 WHERE f.anio>=2025 AND d.linea_eerr IN('002','003','004','005','006','007','008','009')),
 gastos AS (SELECT mes_id,rubro,sum(monto) AS monto,count(*) AS registros FROM base GROUP BY 1,2)
SELECT c.mes_id,to_char(to_date(c.mes_id::text,'YYYYMM'),'YYYY-MM') AS mes,
 r.codigo,r.nombre,r.orden,
 CASE r.codigo WHEN 'ingreso' THEN c.ingreso_explotacion
 WHEN 'costo_total' THEN c.costo_explotacion+c.administracion_ventas
 WHEN 'resultado' THEN c.resultado_operacional_antes_depreciacion
 WHEN 'margen_pct' THEN c.margen_operacional_antes_depreciacion_pct
 WHEN 'depreciacion' THEN CASE WHEN c.registros_depreciacion>0 THEN c.depreciacion_registrada END
 ELSE coalesce(g.monto,0) END::float8 AS monto,
 CASE WHEN r.codigo='depreciacion' THEN c.registros_depreciacion ELSE coalesce(g.registros,c.registros_contables) END::bigint AS registros,
 c.ultimo_mes_observado,
 'Empresa completa por periodo contable; rubros de gasto operacional sin solapamiento. Fijo operacional excluye comision y financieros. Depreciacion informativa, fuera del resultado mostrado. No equivale a poblacion de ciclos.'::text AS alcance
FROM ds_gestion.v_referencia_contable_mensual c
CROSS JOIN (VALUES('ingreso','Ingreso de explotación',1),('combustible','Combustible, incluido impuesto específico',2),
 ('peajes','Peajes',3),('neumaticos','Neumáticos',4),('comision','Comisión de viajes',5),
 ('fleteros','Pagos a terceros',6),('fijo','Fijo operacional',7),('otros','Otros gastos operacionales',8),
 ('costo_total','Costo operacional total',9),('resultado','Resultado antes de depreciación',10),
 ('margen_pct','Margen operacional %',11),('depreciacion','Depreciación registrada · informativa',12)) r(codigo,nombre,orden)
LEFT JOIN gastos g ON g.mes_id=c.mes_id AND g.rubro=r.codigo
WHERE c.mes_id>=202501;

CREATE OR REPLACE VIEW ds_gestion.v_capacidad_flota_modelada_mensual AS
WITH m AS (
 SELECT mes_id,mes,anio,desde,hasta,parcial FROM ds_gestion.v_fijo_no_absorbido_mensual WHERE anio>=2025),
 practica AS (
 SELECT m.mes_id,count(*)::bigint AS dias_calendario,
 bool_and(p.n=1 AND p.capacidad IS NOT NULL) AS capacidad_completa,
 CASE WHEN bool_and(p.n=1 AND p.capacidad IS NOT NULL)
 THEN sum(p.capacidad/extract(doy FROM (make_date(m.anio+1,1,1)-1))) END::float8 AS dias_capacidad
 FROM m CROSS JOIN LATERAL generate_series(m.desde,m.hasta,interval '1 day') dia
 CROSS JOIN LATERAL (SELECT count(*) AS n,max(cantidad_inductor)::numeric AS capacidad
 FROM costos.parametro_costo WHERE metodo='propuesto' AND parametro='cuota_diaria' AND ambito='flota'
 AND dia::date>=vigente_desde AND dia::date<vigente_hasta) p GROUP BY m.mes_id),
 reales AS (
 SELECT sk_fecha_inicio/100 AS mes_id,sum(dias_viaje)::float8 AS dias_reales,
 count(*)::bigint AS viajes,count(*) FILTER(WHERE dias_viaje IS NULL)::bigint AS dias_reales_faltantes
 FROM costos.fact_viaje_costo_indirecto WHERE metodo='propuesto' AND es_flota_propia='S' GROUP BY 1),
 virtuales AS (
 SELECT sk_fecha/100 AS mes_id,sum(cantidad) FILTER(WHERE estado='ok')::float8 AS dias_virtuales,
 count(*)::bigint AS virtuales,count(*) FILTER(WHERE estado IS DISTINCT FROM 'ok' OR cantidad IS NULL)::bigint AS dias_virtuales_faltantes
 FROM escenarios.fact_costo_virtual WHERE sk_escenario=3 AND sk_componente=4 GROUP BY 1)
SELECT m.mes_id,m.mes,m.anio,m.desde::text,m.hasta::text,m.parcial,p.dias_calendario,p.capacidad_completa,
 p.dias_capacidad,coalesce(r.dias_reales,0) AS dias_reales_conocidos,coalesce(v.dias_virtuales,0) AS dias_virtuales_conocidos,
 (coalesce(r.dias_reales,0)+coalesce(v.dias_virtuales,0))::float8 AS dias_actividad_conocida,
 (p.dias_capacidad-coalesce(r.dias_reales,0)-coalesce(v.dias_virtuales,0))::float8 AS brecha_modelada,
 coalesce(r.viajes,0) AS viajes,coalesce(v.virtuales,0) AS virtuales,
 coalesce(r.dias_reales_faltantes,0)+coalesce(v.dias_virtuales_faltantes,0) AS referencias_dias_faltantes,
 'Solo flota propia. Capacidad anual del parametro vigente prorrateada por calendario; actividad conocida en dias-viaje reales y virtuales E3. No son dias observados de trabajo, taller, descanso o ahorro evitable.'::text AS alcance
FROM m LEFT JOIN practica p USING(mes_id) LEFT JOIN reales r USING(mes_id) LEFT JOIN virtuales v USING(mes_id);
'''

CONTROLES={
 'rubros_duplicados':"SELECT count(*) FROM (SELECT mes_id,codigo FROM ds_gestion.v_rubros_contables_mensual GROUP BY 1,2 HAVING count(*)<>1) x",
 'costo_contable_sin_cuadrar':"""WITH r AS (SELECT mes_id,sum(monto) FILTER(WHERE codigo IN('combustible','peajes','neumaticos','comision','fleteros','fijo','otros')) desglose,max(monto) FILTER(WHERE codigo='costo_total') total FROM ds_gestion.v_rubros_contables_mensual GROUP BY 1) SELECT count(*) FROM r WHERE abs(desglose-total)>.01""",
 'fijo_operacional_distinto_referencia':"""SELECT count(*) FROM ds_gestion.v_rubros_contables_mensual r JOIN ds_gestion.v_referencia_contable_mensual c USING(mes_id) WHERE r.codigo='fijo' AND abs(r.monto-c.fijo_operacional)>.01""",
 'depreciacion_sin_registro_con_importe':"SELECT count(*) FROM ds_gestion.v_rubros_contables_mensual WHERE codigo='depreciacion' AND registros=0 AND monto IS NOT NULL",
 'capacidad_duplicada':"SELECT count(*) FROM (SELECT mes_id FROM ds_gestion.v_capacidad_flota_modelada_mensual GROUP BY 1 HAVING count(*)<>1) x",
 'capacidad_sin_parametros':"SELECT count(*) FROM ds_gestion.v_capacidad_flota_modelada_mensual WHERE NOT capacidad_completa OR dias_capacidad IS NULL",
 'brecha_sin_cuadrar':"SELECT count(*) FROM ds_gestion.v_capacidad_flota_modelada_mensual WHERE abs(brecha_modelada-(dias_capacidad-dias_actividad_conocida))>1e-8",
}
def publicar(c):
 c.execute(SQL)
 checks={n:int(c.uno(q)[0]) for n,q in CONTROLES.items()}
 if any(checks.values()):raise ValueError('Referencias V34 inconsistentes: '+str(checks))
 return dict(version=VERSION,controles=checks)
