"""Contrato descriptivo y capacidad para dashboards; no recalcula hechos financieros."""
from publicacion.publicar_ds_gestion import SQL as SQL_BASE
from comun.resultado_ciclo import SQL_POLITICA

VERSION='2026-10-31-contrato-dashboard-v1'
# Ampliación al final: conserva nombres, orden y significado de columnas anteriores.
SQL_MOV=SQL_BASE[SQL_BASE.index('CREATE OR REPLACE VIEW ds_gestion.v_movimiento'):SQL_BASE.index('CREATE OR REPLACE VIEW ds_gestion.v_ingreso_servicio')]
SQL_MOV=SQL_MOV.replace('t.nombre_fuente AS tramo_nombre_actual', '''t.nombre_fuente AS tramo_nombre_actual,
 z.ciudad_origen_limpia AS origen,z.ciudad_destino_limpia AS destino,
 z.base_origen,z.base_destino,z.tipo_virtual,z.tipo_carga_final AS carga_descriptiva,
 q.tipo_carga AS carga_termica_costeo,
 CASE WHEN q.tipo_carga IS NOT NULL THEN 'escenarios.fact_cotizacion_viaje.tipo_carga' END AS origen_carga_termica,
 coalesce(tb.tramo,z.ciudad_origen_limpia || ' / ' || z.ciudad_destino_limpia) AS tramo_nombre_costeo,
 coalesce(m.km,tb.km) AS km_analisis,e.codigo AS equipo_codigo,
 CASE WHEN m.es_virtual THEN 'S' WHEN p.viaje_vacio_fuente IS NULL THEN NULL
 WHEN upper(trim(p.viaje_vacio_fuente)) IN('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END AS vacio_fisico,
 tb.tramo AS tramo_nombre_base''').replace('LEFT JOIN dim_comun.dim_tramo_comercial t ON t.tramo_id=m.tramo_id;', '''LEFT JOIN dim_comun.dim_tramo_comercial t ON t.tramo_id=m.tramo_id
LEFT JOIN ciclos.fact_viaje_ciclo z ON z.id_viaje=m.id_cadena
LEFT JOIN escenarios.fact_cotizacion_viaje q ON q.nro_viaje=m.nro_viaje
LEFT JOIN ciclos.dim_tramo_base tb ON tb.sit_stretch_id=m.tramo_id;''')

SQL_CICLO="""CREATE OR REPLACE VIEW ds_gestion.v_ciclo AS
WITH a AS (
 SELECT ciclo_id,
 (array_agg(origen ORDER BY orden_ciclo_comercial))[1] || ' → ' ||
 string_agg(destino,' → ' ORDER BY orden_ciclo_comercial) AS recorrido_completo,
 md5(json_agg(json_build_array(origen,destino,tramo_id,km,CASE WHEN es_virtual THEN 'S' ELSE 'N' END)
 ORDER BY orden_ciclo_comercial)::text) AS patron_recorrido,
 count(*) FILTER(WHERE NOT es_virtual) AS reales,count(*) FILTER(WHERE es_virtual) AS virtuales,
 count(*) FILTER(WHERE NOT es_virtual AND clasificacion_viaje='Retorno') AS retornos_reales,
 count(*) FILTER(WHERE NOT es_virtual AND clasificacion_viaje='Retorno' AND tipo_operador='propio') AS retornos_propios,
 count(*) FILTER(WHERE NOT es_virtual AND clasificacion_viaje='Retorno' AND tipo_operador IN('fletero','caterina')) AS retornos_terceros,
 count(*) FILTER(WHERE NOT es_virtual AND rol_comercial IN('envío adicional en ruta','envío adicional local')) AS envios_adicionales
 FROM ds_gestion.v_movimiento WHERE ciclo_id IS NOT NULL GROUP BY ciclo_id)
SELECT d.ciclo_comercial AS ciclo_id,p.movimiento_id AS movimiento_principal_id,p.nro_viaje AS nro_viaje_principal,
 p.fecha_documental_id AS fecha_documental_principal_id,p.cliente_id AS cliente_principal_id,
 p.cliente_nombre AS cliente_principal,p.producto_id AS producto_principal_id,p.producto_nombre AS producto_principal,
 p.tramo_id AS tramo_principal_id,p.tramo_nombre_costeo AS tramo_principal,p.origen AS origen_principal,
 p.destino AS destino_principal,p.equipo_id,p.equipo_codigo,p.equipo_nombre,p.conductor_nombre,
 p.tipo_operador AS operador_principal,coalesce(p.tipo_operador='propio',false) AS en_alcance_rentabilidad,
 d.fecha_inicio AS inicio_documental,d.fecha_fin AS fin_documental,d.motivo_apertura,d.motivo_cierre,
 CASE WHEN d.motivo_apertura='local en Santiago' AND d.motivo_cierre='local' THEN 'local Santiago'
 WHEN 'corte en Coquimbo' IN(d.motivo_apertura,d.motivo_cierre) THEN 'con corte Coquimbo'
 WHEN d.motivo_apertura IN('sale de Santiago','corte en Coquimbo') AND d.motivo_cierre IN('llega a Santiago','corte en Coquimbo')
 THEN 'Santiago-Santiago' ELSE 'abierto o inicio incompleto' END AS tipo_segmento,
 a.recorrido_completo,a.patron_recorrido,coalesce(d.retornos>0,false) AS contiene_retorno,
 a.reales,a.virtuales,a.retornos_reales,a.retornos_propios,a.retornos_terceros,a.envios_adicionales
FROM ciclos.dim_ciclo_comercial d LEFT JOIN ds_gestion.v_movimiento p ON p.id_cadena=d.viaje_principal
LEFT JOIN a ON a.ciclo_id=d.ciclo_comercial;"""

SQL_CAPACIDAD="""CREATE OR REPLACE VIEW ds_gestion.v_capacidad_periodica AS
WITH r AS MATERIALIZED (
 SELECT sk_fecha_inicio/100 AS mes_id, count(*) AS viajes,
 count(*) FILTER(WHERE estado_costo_indirecto IS DISTINCT FROM 'ok') AS viajes_pendientes,
 sum(cf_flota) FILTER(WHERE estado_costo_indirecto='ok') AS flota,
 sum(cf_rampla) FILTER(WHERE estado_costo_indirecto='ok') AS rampla,
 sum(cf_estructura) FILTER(WHERE estado_costo_indirecto='ok') AS estructura,
 sum(cf_directo_fleteros) FILTER(WHERE estado_costo_indirecto='ok') AS directo_fleteros,
 max(sk_fecha_inicio) AS ultima_fecha
 FROM costos.fact_viaje_costo_indirecto WHERE metodo='propuesto' GROUP BY 1),
 v AS MATERIALIZED (
 SELECT sk_fecha/100 AS mes_id,count(*) AS virtuales,
 count(*) FILTER(WHERE estado IS DISTINCT FROM 'ok' OR monto IS NULL) AS virtuales_pendientes,
 sum(cf_flota_virtual) FILTER(WHERE estado='ok') AS flota,
 sum(cf_rampla_virtual) FILTER(WHERE estado='ok') AS rampla,
 sum(cf_estructura_virtual) FILTER(WHERE estado='ok') AS estructura
 FROM escenarios.fact_costo_virtual WHERE sk_escenario=3 AND sk_componente=4 GROUP BY 1),
 cortes AS (SELECT mes_id/100 AS anio,to_date(max(ultima_fecha)::text,'YYYYMMDD') AS hasta FROM r GROUP BY 1),
 meses AS (SELECT c.anio,c.hasta,g::date AS desde,
 least((g+interval '1 month')::date,c.hasta+1) AS fin,
 (g+interval '1 month')::date AS fin_mes
 FROM cortes c CROSS JOIN LATERAL generate_series(make_date(c.anio,1,1),date_trunc('month',c.hasta),interval '1 month') g),
 base AS (SELECT m.*,b.bolsa,to_char(m.desde,'YYYY-MM') AS mes,
 coalesce(r.viajes,0) AS viajes,coalesce(r.viajes_pendientes,0) AS viajes_pendientes,
 coalesce(v.virtuales,0) AS virtuales,coalesce(v.virtuales_pendientes,0) AS virtuales_pendientes,
 coalesce(CASE b.bolsa WHEN 'flota' THEN r.flota WHEN 'rampla' THEN r.rampla
 WHEN 'estructura' THEN r.estructura ELSE r.directo_fleteros END,0)::float8 AS absorcion_real,
 coalesce(CASE b.bolsa WHEN 'flota' THEN v.flota WHEN 'rampla' THEN v.rampla
 WHEN 'estructura' THEN v.estructura ELSE 0 END,0)::float8 AS absorcion_virtual_conocida
 FROM meses m CROSS JOIN (VALUES('flota'),('rampla'),('estructura'),('directo_fleteros')) b(bolsa)
 LEFT JOIN r ON r.mes_id=m.anio*100+extract(month FROM m.desde)::int
 LEFT JOIN v ON v.mes_id=m.anio*100+extract(month FROM m.desde)::int),
 pres AS (SELECT b.mes,b.bolsa,
 bool_and(p.n=1 AND p.monto IS NOT NULL) AS presupuesto_completo,
 sum(p.monto/extract(doy FROM (make_date(b.anio+1,1,1)-1)))::float8 AS presupuesto
 FROM base b CROSS JOIN LATERAL generate_series(b.desde,b.fin-1,interval '1 day') dia
 CROSS JOIN LATERAL (SELECT count(*) AS n,max(monto_base)::float8 AS monto FROM costos.parametro_costo
 WHERE parametro='cuota_diaria' AND metodo='propuesto' AND ambito=b.bolsa
 AND dia::date>=vigente_desde AND dia::date<vigente_hasta) p GROUP BY b.mes,b.bolsa)
SELECT b.mes,b.anio,b.bolsa,b.desde,b.fin-1 AS hasta,b.fin<b.fin_mes AS parcial,
 CASE WHEN p.presupuesto_completo THEN p.presupuesto END AS presupuesto,b.absorcion_real,
 b.absorcion_virtual_conocida,b.absorcion_real+b.absorcion_virtual_conocida AS absorcion_conocida,
 CASE WHEN p.presupuesto_completo THEN p.presupuesto-b.absorcion_real-b.absorcion_virtual_conocida END AS residual_provisional,
 CASE WHEN p.presupuesto_completo AND p.presupuesto<>0 THEN 100*(b.absorcion_real+b.absorcion_virtual_conocida)/p.presupuesto END AS porcentaje_absorbido,
 p.presupuesto_completo,b.viajes,b.viajes_pendientes,b.virtuales,b.virtuales_pendientes,
 'prorrateo calendario; residual tras absorcion conocida, no disponibilidad fisica'::text AS alcance
FROM base b JOIN pres p USING(mes,bolsa);"""

# Adaptador ancho para modelos existentes: la expresión de admisión procede de la
# MISMA definición de v_evaluacion_ciclo. Evita ejecutar dos veces resultado_ciclo.
_evaluacion=SQL_POLITICA[SQL_POLITICA.index('CREATE OR REPLACE VIEW ds_gestion.v_evaluacion_ciclo'):]
_cols=['fecha_documental_principal_id','ingreso_estimado','costo_comercial','margen_comercial',
 'submotivo_estructura','condiciones_estructura','estructura_conservadora','estructura_flexible',
 'propios','ingreso_completo','costos_reales_completos','costos_virtuales_completos',
 'metodo_virtual_disponible','recorrido_movimientos','conexiones']
SQL_EVALUADO=_evaluacion.replace('ds_gestion.v_evaluacion_ciclo AS','ds_gestion.v_ciclo_politica AS').replace(
 'END AS motivo\nFROM','END AS motivo,'+','.join('r.'+c for c in _cols)+'\nFROM')
SQL=SQL_MOV+SQL_CICLO+SQL_CAPACIDAD+SQL_EVALUADO

def publicar(con):
    con.execute(SQL)
    qs={
      'movimientos_duplicados':"SELECT count(*) FROM (SELECT movimiento_id FROM ds_gestion.v_movimiento GROUP BY 1 HAVING count(*)<>1) x",
      'ciclos_duplicados':"SELECT count(*) FROM (SELECT ciclo_id FROM ds_gestion.v_ciclo GROUP BY 1 HAVING count(*)<>1) x",
      'ciclos_no_conservados':"SELECT abs((SELECT count(*) FROM ds_gestion.v_ciclo)-(SELECT count(*) FROM ciclos.dim_ciclo_comercial))",
      'principales_desconectados':"SELECT count(*) FROM ds_gestion.v_ciclo WHERE en_alcance_rentabilidad AND movimiento_principal_id IS NULL",
      'presupuesto_incompleto_con_importe':"SELECT count(*) FROM ds_gestion.v_capacidad_periodica WHERE NOT presupuesto_completo AND presupuesto IS NOT NULL",
      'capacidad_duplicada':"SELECT count(*) FROM (SELECT mes,bolsa FROM ds_gestion.v_capacidad_periodica GROUP BY 1,2 HAVING count(*)<>1) x",
      'virtuales_conductor_inventado':"SELECT count(*) FROM ds_gestion.v_movimiento WHERE es_virtual AND conductor_id IS NOT NULL",
    }
    controles={k:int(con.uno(q)[0]) for k,q in qs.items()}
    if any(controles.values()):raise ValueError(f'Contrato inconsistente: {controles}')
    return {'version':VERSION,'controles':controles,
       'presupuesto_sin_cobertura':con.df("SELECT anio,count(*) AS filas FROM ds_gestion.v_capacidad_periodica WHERE NOT presupuesto_completo GROUP BY anio ORDER BY anio").to_dict('records')}
