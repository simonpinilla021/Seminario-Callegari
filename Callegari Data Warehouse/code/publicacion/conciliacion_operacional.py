"""Referencia EERR y puente agregado aditivos; no recalcula escenarios.

Fecha del movimiento para ventas/costos; período contable para EERR. Toda la
empresa, sin filtro de ciclos. No equivale a conciliar documentos individuales.
La reposición se separa en el puente, se conserva en los hechos originales.
"""
VERSION = '2026-11-03-conciliacion-operacional-v2-fijo-e3'

SQL = """
CREATE OR REPLACE VIEW ds_gestion.v_referencia_contable_mensual AS
WITH c AS (
 SELECT f.anio*100+f.mes AS mes_id,count(*) AS registros_contables,
 -sum(f.monto::numeric) FILTER(WHERE d.linea_eerr='001') AS ingreso_explotacion,
 -sum(f.monto::numeric) FILTER(WHERE d.codigo LIKE '4%') AS ingreso_total,
 sum(f.monto::numeric) FILTER(WHERE d.codigo LIKE '3%') AS gasto_total,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.linea_eerr IN('002','003','004','005','006','007','008')),0) AS costo_explotacion,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.linea_eerr='009'),0) AS administracion_ventas,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.linea_eerr='014'),0) AS depreciacion_registrada,
 count(*) FILTER(WHERE d.linea_eerr='014') AS registros_depreciacion,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.clase_tarificador='fijo' AND d.codigo<>'310020104'),0) AS fijo_comparable,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.clase_tarificador='fijo' AND d.codigo<>'310020104'
 AND d.linea_eerr IN('002','003','004','005','006','007','008','009')),0) AS fijo_operacional,
 coalesce(sum(f.monto::numeric) FILTER(WHERE d.linea_eerr='000'),0) AS monto_sin_linea_eerr,
 count(*) FILTER(WHERE d.linea_eerr='000' OR d.linea_eerr IS NULL) AS registros_sin_linea_eerr
 FROM costos.fact_contabilidad_mensual f JOIN costos.dim_cuenta_contable d USING(sk_cuenta)
 GROUP BY 1),r AS (
 SELECT *,ingreso_total-ingreso_explotacion AS otros_ingresos,
 ingreso_explotacion-costo_explotacion-administracion_ventas AS resultado_operacional_antes_depreciacion,
 ingreso_total-gasto_total AS resultado_total FROM c)
SELECT *,resultado_operacional_antes_depreciacion-depreciacion_registrada AS resultado_operacional_despues_depreciacion,
 resultado_operacional_antes_depreciacion/nullif(ingreso_explotacion,0) AS margen_operacional_antes_depreciacion_pct,
 resultado_total/nullif(ingreso_total,0) AS margen_total_pct,
 mes_id=max(mes_id) OVER() AS ultimo_mes_observado,
 'EERR por periodo contable; ultimo mes observado no acredita cierre; porcentajes en fraccion; depreciacion sin registros no implica costo economico cero'::text AS alcance
FROM r;

CREATE OR REPLACE VIEW ds_gestion.v_fijos_escenario_mensual AS
WITH f AS (
 SELECT sk_fecha/100 AS mes_id,sk_escenario,monto,estado,false AS virtual
 FROM escenarios.fact_costo_viaje WHERE sk_componente=4
 UNION ALL SELECT sk_fecha/100,sk_escenario,monto,estado,true
 FROM escenarios.fact_costo_virtual WHERE sk_componente=4),m AS (
 SELECT mes_id,sk_escenario,count(*) AS registros,
 count(*) FILTER(WHERE monto IS NULL) AS montos_faltantes,
 count(*) FILTER(WHERE estado IS DISTINCT FROM 'ok' OR monto IS NULL) AS componentes_no_validos,
 sum(monto::numeric) AS fijo_conocido,
 sum(monto::numeric) FILTER(WHERE NOT virtual) AS fijo_real_conocido,
 sum(monto::numeric) FILTER(WHERE virtual) AS fijo_virtual_conocido
 FROM f WHERE sk_escenario IN(1,2,3) GROUP BY 1,2)
SELECT m.*,c.fijo_comparable AS fijo_contable_comparable,
 c.registros_contables IS NOT NULL AS hay_contabilidad,
 'Subtotales conocidos; 1/2 cotizaciones con otra cobertura y retornos sin costo; 3 reales y virtuales; totales entre escenarios no implican poblacion equivalente'::text AS alcance
FROM m LEFT JOIN ds_gestion.v_referencia_contable_mensual c USING(mes_id);

CREATE OR REPLACE VIEW ds_gestion.v_conciliacion_operacional_mensual AS
WITH ventas AS (
 SELECT sk_fecha/100 AS mes_id,count(*) AS viajes_reales,
 count(*) FILTER(WHERE ingreso IS NULL) AS ingresos_faltantes,
 sum(ingreso::numeric) AS ingreso_estimado_conocido,
 sum(valor_facturado::numeric) AS facturado_por_fecha_viaje,
 max(sk_fecha) AS ultima_fecha_viaje
 FROM ventas.fact_viaje_venta GROUP BY 1),fuente AS (
 SELECT sk_fecha/100 AS mes_id,sk_escenario,sk_componente,monto,estado,false AS virtual
 FROM escenarios.fact_costo_viaje WHERE sk_escenario =3
 UNION ALL SELECT sk_fecha/100,sk_escenario,sk_componente,monto,estado,true
 FROM escenarios.fact_costo_virtual WHERE sk_escenario =3),costos AS (
 SELECT mes_id,sk_escenario,count(*) AS registros_componentes,
 count(*) FILTER(WHERE monto IS NULL) AS montos_faltantes,
 count(*) FILTER(WHERE estado IS DISTINCT FROM 'ok' OR monto IS NULL) AS componentes_no_validos,
 count(*) FILTER(WHERE virtual) AS componentes_virtuales,
 sum(monto::numeric) AS costo_modelado_conocido,
 coalesce(sum(monto::numeric) FILTER(WHERE sk_componente=4),0) AS fijo_modelado_conocido,
 coalesce(sum(monto::numeric) FILTER(WHERE sk_componente=5),0) AS reposicion_modelada_conocida
 FROM fuente GROUP BY 1,2),meses AS (
 SELECT mes_id FROM ventas UNION SELECT mes_id FROM costos
 UNION SELECT mes_id FROM ds_gestion.v_referencia_contable_mensual),base AS (
 SELECT m.mes_id,e.sk_escenario,v.viajes_reales,v.ingresos_faltantes,
 v.ingreso_estimado_conocido,v.facturado_por_fecha_viaje,v.ultima_fecha_viaje,
 k.registros_componentes,k.montos_faltantes,k.componentes_no_validos,k.componentes_virtuales,
 k.costo_modelado_conocido,k.fijo_modelado_conocido,k.reposicion_modelada_conocida,
 c.ingreso_explotacion,c.ingreso_total,c.otros_ingresos,c.gasto_total,
 c.costo_explotacion,c.administracion_ventas,c.depreciacion_registrada,c.registros_depreciacion,
 c.fijo_comparable AS fijo_contable_comparable,c.fijo_operacional AS fijo_contable_operacional,
 c.resultado_operacional_antes_depreciacion,c.resultado_operacional_despues_depreciacion,c.resultado_total,
 c.registros_sin_linea_eerr,c.monto_sin_linea_eerr,
 v.mes_id IS NOT NULL AS hay_ventas,k.mes_id IS NOT NULL AS hay_costos,
 c.mes_id IS NOT NULL AS hay_contabilidad,
 v.ingreso_estimado_conocido-k.costo_modelado_conocido AS margen_modelado_subtotal
 FROM meses m CROSS JOIN (VALUES(3)) e(sk_escenario)
 LEFT JOIN ventas v USING(mes_id) LEFT JOIN costos k USING(mes_id,sk_escenario)
 LEFT JOIN ds_gestion.v_referencia_contable_mensual c USING(mes_id)),pasos AS (
 SELECT *,ingreso_explotacion-ingreso_estimado_conocido AS diferencia_ingresos,
 fijo_modelado_conocido-fijo_contable_operacional AS diferencia_fijos,
 (costo_modelado_conocido-fijo_modelado_conocido-reposicion_modelada_conocida)
 -(costo_explotacion+administracion_ventas-fijo_contable_operacional) AS diferencia_otros_costos,
 margen_modelado_subtotal+reposicion_modelada_conocida AS margen_sin_reposicion_subtotal,
 hay_ventas AND hay_costos AND hay_contabilidad AS periodo_con_fuentes_comunes,
 hay_ventas AND hay_costos AND ingresos_faltantes=0 AND componentes_no_validos=0 AS componentes_modelados_completos
 FROM base)
SELECT *,margen_modelado_subtotal+diferencia_ingresos+diferencia_fijos
 +reposicion_modelada_conocida+diferencia_otros_costos AS resultado_conciliado_antes_depreciacion,
 resultado_operacional_antes_depreciacion-(margen_modelado_subtotal+diferencia_ingresos+diferencia_fijos
 +reposicion_modelada_conocida+diferencia_otros_costos) AS residuo_aritmetico,
 CASE WHEN periodo_con_fuentes_comunes THEN
 CASE WHEN componentes_modelados_completos THEN 'Puente agregado; componentes presentes'
 ELSE 'Puente agregado de subtotales; hay faltantes o estados no validos' END
 ELSE 'Sin todas las fuentes del periodo; no conciliable' END AS estado_conciliacion,
 mes_id=max(mes_id) OVER() AS ultimo_mes_observado,
 'Toda la empresa por fecha de movimiento; sin filtro de ciclos; E3; conciliacion de agregados, no documental ni causal; no altera reposicion ni margenes fuente; ultimo mes puede estar incompleto'::text AS alcance
FROM pasos;

CREATE OR REPLACE VIEW ds_gestion.v_puente_operacional_mensual AS
SELECT c.mes_id,c.sk_escenario,p.orden,p.concepto,p.monto,
 c.periodo_con_fuentes_comunes,c.componentes_modelados_completos,c.estado_conciliacion,
 c.resultado_operacional_antes_depreciacion AS resultado_objetivo,
 c.residuo_aritmetico,c.alcance
FROM ds_gestion.v_conciliacion_operacional_mensual c
CROSS JOIN LATERAL (VALUES
 (1,'Margen modelado: subtotal conocido',c.margen_modelado_subtotal),
 (2,'Sustituir ingreso estimado por ingreso de explotacion contable',c.diferencia_ingresos),
 (3,'Sustituir fijo modelado por fijo contable operacional',c.diferencia_fijos),
 (4,'Separar reposicion para comparar antes de depreciacion',c.reposicion_modelada_conocida),
 (5,'Conciliar otros costos: diferencia agregada pendiente de detalle',c.diferencia_otros_costos)
) p(orden,concepto,monto);

CREATE OR REPLACE VIEW ds_gestion.v_fijo_no_absorbido_mensual AS
WITH presupuesto AS (
 SELECT anio,replace(mes,'-','')::integer AS mes_id,
 min(desde) AS desde,max(hasta) AS hasta,bool_or(parcial) AS parcial,
 bool_and(presupuesto_completo) AND count(*)=4 AS presupuesto_completo,
 CASE WHEN bool_and(presupuesto_completo) AND count(*)=4 THEN sum(presupuesto::numeric) END AS presupuesto,
 sum(absorcion_conocida::numeric) AS asignado_control_bolsas,
 max(viajes_pendientes) AS viajes_indirectos_pendientes,
 max(virtuales_pendientes) AS virtuales_pendientes
 FROM ds_gestion.v_capacidad_periodica GROUP BY 1,2)
SELECT c.mes_id,c.mes_id/100 AS anio,
 to_char(to_date(c.mes_id::text,'YYYYMM'),'YYYY-MM') AS mes,
 p.desde,p.hasta,p.parcial,p.presupuesto_completo,p.presupuesto,
 c.fijo_modelado_conocido AS asignado_conocido,
 f.fijo_real_conocido AS asignado_real_conocido,f.fijo_virtual_conocido AS asignado_virtual_conocido,
 p.presupuesto-c.fijo_modelado_conocido AS saldo_presupuesto_no_asignado,
 c.fijo_contable_comparable AS gasto_fijo_contable,
 c.fijo_contable_comparable-c.fijo_modelado_conocido AS diferencia_contable_no_asignada,
 c.fijo_modelado_conocido/nullif(p.presupuesto,0) AS absorcion_presupuesto_pct,
 c.fijo_modelado_conocido/nullif(c.fijo_contable_comparable,0) AS asignado_sobre_gasto_pct,
 p.asignado_control_bolsas,f.montos_faltantes AS fijos_faltantes,
 f.componentes_no_validos AS fijos_no_validos,p.viajes_indirectos_pendientes,p.virtuales_pendientes,
 c.hay_ventas,c.hay_costos,c.hay_contabilidad,c.ultimo_mes_observado,
 'Escenario 3; toda la empresa por mes de movimiento, sin filtro de ciclos/clientes. Presupuesto prorrateado por dias calendario con vigencias. Saldos con signo y tras asignacion conocida: no son ahorro evitable ni indisponibilidad fisica; faltantes pueden explicar parte del saldo. Fijo contable incluye financieros de la regla vigente y excluye comision ya modelada aparte.'::text AS alcance
FROM ds_gestion.v_conciliacion_operacional_mensual c
LEFT JOIN presupuesto p USING(mes_id)
LEFT JOIN ds_gestion.v_fijos_escenario_mensual f ON f.mes_id=c.mes_id AND f.sk_escenario=3
WHERE c.sk_escenario=3;
"""

CONTROLES = {
    'mes_escenario_duplicado': '''SELECT count(*) FROM (SELECT mes_id,sk_escenario FROM
        ds_gestion.v_conciliacion_operacional_mensual GROUP BY 1,2 HAVING count(*)<>1) x''',
    'residuo_aritmetico': '''SELECT count(*) FROM ds_gestion.v_conciliacion_operacional_mensual
        WHERE periodo_con_fuentes_comunes AND (residuo_aritmetico IS NULL OR abs(residuo_aritmetico)>0.01)''',
    'faltantes_presentados_como_completos': '''SELECT count(*) FROM ds_gestion.v_conciliacion_operacional_mensual
        WHERE componentes_modelados_completos AND (montos_faltantes>0 OR componentes_no_validos>0 OR ingresos_faltantes>0)''',
    'fuentes_incompletas_con_resultado_conciliado': '''SELECT count(*) FROM ds_gestion.v_conciliacion_operacional_mensual
        WHERE NOT periodo_con_fuentes_comunes AND resultado_conciliado_antes_depreciacion IS NOT NULL''',
    'otros_escenarios_en_puente': '''SELECT count(*) FROM ds_gestion.v_conciliacion_operacional_mensual WHERE sk_escenario <>3''',
    'formula_resultado_total': '''SELECT count(*) FROM ds_gestion.v_referencia_contable_mensual
        WHERE abs(resultado_total-(ingreso_total-gasto_total))>0.01''',
    'formula_operacional': '''SELECT count(*) FROM ds_gestion.v_referencia_contable_mensual
        WHERE abs(resultado_operacional_antes_depreciacion-(ingreso_explotacion-costo_explotacion-administracion_ventas))>0.01''',
    'fijo_e3_distinto_bolsas': '''SELECT count(*) FROM ds_gestion.v_fijo_no_absorbido_mensual
        WHERE abs(asignado_conocido-asignado_control_bolsas)>0.01''',
    'fijo_e3_saldo_presupuesto': '''SELECT count(*) FROM ds_gestion.v_fijo_no_absorbido_mensual
        WHERE abs(saldo_presupuesto_no_asignado-(presupuesto-asignado_conocido))>0.01''',
    'fijo_e3_saldo_contable': '''SELECT count(*) FROM ds_gestion.v_fijo_no_absorbido_mensual
        WHERE abs(diferencia_contable_no_asignada-(gasto_fijo_contable-asignado_conocido))>0.01''',
}


def publicar(con):
    con.execute(SQL)
    controles = {n: int(con.uno(sql)[0]) for n, sql in CONTROLES.items()}
    if any(controles.values()):
        raise ValueError(f'Conciliacion operacional no publicable: {controles}')
    return dict(version=VERSION, controles=controles)
