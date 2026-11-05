"""Contrato de retornos y km por corredor para dashboards.

Lleva al DW dos reglas que en Power BI V10 vivían dentro de las consultas del informe:
- el corredor de un movimiento (origen → destino normalizados);
- cuándo los km de un movimiento propio son evaluables.

Vistas aditivas sobre ds_gestion.v_movimiento: no recalculan ni modifican hechos financieros.
Los controles comparan contra las consultas exactas de V10 sobre todo el histórico, de modo que
la migración del informe no cambie ningún conteo ni km.
"""

VERSION = '2026-10-31-contrato-retornos-v1'

# Reglas únicas: se reutilizan en las vistas y en los controles.
CORREDOR = "coalesce(origen||' → '||destino,'Sin corredor')"
KM_EVALUABLE = ("km>=0 AND km<'Infinity'::float8 AND vacio_fisico IN('S','N') "
                "AND calidad_trayectoria IN('N','NB') AND NOT(km=0 AND origen IS DISTINCT FROM destino)")
MES = "to_char(to_date(fecha_documental_id::text,'YYYYMMDD'),'YYYY-MM')"

SQL_RETORNO = f"""CREATE OR REPLACE VIEW ds_gestion.v_retorno AS
SELECT movimiento_id,nro_viaje,nro_viaje::text AS viaje_id,fecha_documental_id,
 fecha_documental_id/100 AS mes_id,{MES} AS mes,
 origen,destino,{CORREDOR} AS corredor,
 cliente_id,coalesce(cliente_nombre,'Sin cliente') AS cliente,producto_id,producto_nombre,
 tipo_operador,coalesce(tipo_operador='propio',false) AS es_propio,
 ciclo_id,rol_comercial,vacio_fisico,equipo_id,equipo_nombre,conductor_nombre,
 'viaje real clasificado Retorno en el ciclo comercial; fecha documental ERP, no GPS'::text AS alcance
FROM ds_gestion.v_movimiento
WHERE NOT es_virtual AND clasificacion_viaje='Retorno';
"""

SQL_KM = f"""CREATE OR REPLACE VIEW ds_gestion.v_km_corredor AS
WITH m AS (
 SELECT fecha_documental_id/100 AS mes_id,{MES} AS mes,{CORREDOR} AS corredor,km,vacio_fisico,
 ({KM_EVALUABLE}) IS TRUE AS km_evaluable
 FROM ds_gestion.v_movimiento WHERE NOT es_virtual AND tipo_operador='propio')
SELECT mes_id,mes,corredor,count(*) AS movimientos_alcance,
 count(*) FILTER(WHERE km_evaluable) AS movimientos_evaluables,
 sum(km) FILTER(WHERE km_evaluable) AS km_reales_evaluables,
 CASE WHEN count(*) FILTER(WHERE km_evaluable)>0
  THEN coalesce(sum(km) FILTER(WHERE km_evaluable AND vacio_fisico='S'),0) END AS km_vacios_reales,
 'propios reales; km evaluable: distancia finita no negativa, vacío S/N conocido, trayectoria N/NB, sin 0 km entre ciudades distintas'::text AS regla_km
FROM m GROUP BY mes_id,mes,corredor;
"""

SQL = SQL_RETORNO + SQL_KM

# Consultas exactas de Power BI V10 (sin el filtro de fecha), para conciliar sobre todo el histórico.
V10_RETORNO = f"""SELECT nro_viaje::text AS viaje_id,{MES} AS mes_orden,
 {CORREDOR} AS corredor,coalesce(cliente_nombre,'Sin cliente') AS cliente,
 tipo_operador,coalesce(tipo_operador='propio',false) AS propio FROM ds_gestion.v_movimiento
 WHERE NOT es_virtual AND clasificacion_viaje='Retorno'"""
V10_KM = f"""SELECT {MES} AS mes_orden,
 {CORREDOR} AS corredor,count(*) AS movimientos_alcance,
 count(*) FILTER(WHERE {KM_EVALUABLE}) AS movimientos_evaluables,
 sum(km) FILTER(WHERE {KM_EVALUABLE}) AS km_reales_evaluables,
 CASE WHEN count(*) FILTER(WHERE {KM_EVALUABLE})>0 THEN coalesce(sum(km) FILTER(WHERE {KM_EVALUABLE} AND vacio_fisico='S'),0) END AS km_vacios_reales
 FROM ds_gestion.v_movimiento WHERE NOT es_virtual AND tipo_operador='propio' GROUP BY 1,2"""

TOL = 0.001  # km; las sumas en coma flotante pueden variar en el último decimal según el plan

CONTROLES = {
    'retornos_duplicados': "SELECT count(*) FROM (SELECT movimiento_id FROM ds_gestion.v_retorno GROUP BY 1 HAVING count(*)<>1) x",
    'retornos_no_conservados': """SELECT abs((SELECT count(*) FROM ds_gestion.v_retorno)-
        (SELECT count(*) FROM ds_gestion.v_movimiento WHERE NOT es_virtual AND clasificacion_viaje='Retorno'))""",
    'retornos_virtuales': "SELECT count(*) FROM ds_gestion.v_retorno r JOIN ds_gestion.v_movimiento m USING(movimiento_id) WHERE m.es_virtual",
    'retorno_distinto_v10': f"""SELECT count(*) FROM ds_gestion.v_retorno n FULL JOIN ({V10_RETORNO}) o USING(viaje_id)
        WHERE n.viaje_id IS NULL OR o.viaje_id IS NULL
        OR ROW(n.mes,n.corredor,n.cliente,n.tipo_operador,n.es_propio) IS DISTINCT FROM
           ROW(o.mes_orden,o.corredor,o.cliente,o.tipo_operador,o.propio)""",
    'km_corredor_duplicado': "SELECT count(*) FROM (SELECT mes,corredor FROM ds_gestion.v_km_corredor GROUP BY 1,2 HAVING count(*)<>1) x",
    'km_movimientos_no_conservados': """SELECT abs((SELECT coalesce(sum(movimientos_alcance),0) FROM ds_gestion.v_km_corredor)-
        (SELECT count(*) FROM ds_gestion.v_movimiento WHERE NOT es_virtual AND tipo_operador='propio'))""",
    'km_vacios_mayor_evaluables': f"SELECT count(*) FROM ds_gestion.v_km_corredor WHERE km_vacios_reales>km_reales_evaluables+{TOL}",
    'km_corredor_distinto_v10': f"""SELECT count(*) FROM ds_gestion.v_km_corredor n
        FULL JOIN ({V10_KM}) o ON coalesce(n.mes,'~')=coalesce(o.mes_orden,'~') AND n.corredor=o.corredor
        WHERE n.corredor IS NULL OR o.corredor IS NULL
        OR n.movimientos_alcance<>o.movimientos_alcance OR n.movimientos_evaluables<>o.movimientos_evaluables
        OR (n.km_reales_evaluables IS NULL)<>(o.km_reales_evaluables IS NULL)
        OR abs(n.km_reales_evaluables-o.km_reales_evaluables)>{TOL}
        OR (n.km_vacios_reales IS NULL)<>(o.km_vacios_reales IS NULL)
        OR abs(n.km_vacios_reales-o.km_vacios_reales)>{TOL}""",
}

RESUMEN = """SELECT (SELECT count(*) FROM ds_gestion.v_retorno) AS retornos,
 (SELECT count(*) FROM ds_gestion.v_retorno WHERE es_propio) AS retornos_propios,
 (SELECT count(*) FROM ds_gestion.v_retorno WHERE fecha_documental_id>=20250101) AS retornos_desde_2025,
 (SELECT count(*) FROM ds_gestion.v_km_corredor) AS filas_km_corredor,
 (SELECT count(DISTINCT corredor) FROM ds_gestion.v_km_corredor) AS corredores_propios,
 (SELECT max(mes) FROM ds_gestion.v_retorno) AS ultimo_mes_retorno"""


def publicar(con):
    """Crea o reemplaza las vistas y exige todos los controles en cero."""
    con.execute(SQL)
    controles = {}
    for nombre, q in CONTROLES.items():
        print(f'Control contrato retornos: {nombre}...', flush=True)
        controles[nombre] = int(con.uno(q)[0])
    if any(controles.values()):
        raise ValueError(f'Contrato de retornos inconsistente: {controles}')
    cols = ['retornos', 'retornos_propios', 'retornos_desde_2025', 'filas_km_corredor',
            'corredores_propios', 'ultimo_mes_retorno']
    fila = con.uno(RESUMEN)
    return {'version': VERSION, 'controles': controles,
            'filas': {c: (v if isinstance(v, (str, type(None))) else int(v)) for c, v in zip(cols, fila)}}
