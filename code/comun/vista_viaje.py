# -*- coding: utf-8 -*-
"""
Vista integrada del viaje: comun.v_fact_viaje.

Une por nro_viaje (c_orderline_id) los hechos de viaje de cada área que estén cargados:
  peajes.fact_viaje_peaje (base, obligatoria) · combustible.fact_viaje_combustible ·
  combustible.fact_viaje_combustible_tarificador · costos.fact_viaje_costo_indirecto · ventas.fact_viaje_venta ·
  ciclos.fact_viaje_ciclo

Además arma comun.v_margen_viaje (ingreso − costos por viaje) cuando están cargadas las áreas que necesita.

La llaman los scripts de las áreas al terminar su carga. Se arma con las tablas que existan, así que cargar un área
no borra las columnas de otra (regla de integración del MVP: cada área escribe solo en su esquema; lo compartido va
en comun).
"""


def _existe(con, esquema, tabla):
    return con.existe_tabla(esquema, tabla)


def _columnas(con, esquema, tabla):
    return {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ?",
        (esquema, tabla)).fetchall()}


def _solo_existentes(con, columnas, alias):
    """Quita las columnas cuya tabla todavía no las tiene (un área cargada con una versión anterior de su script),
    para que la vista no falle por otra área. alias: {"c": ("combustible", "fact_viaje_combustible"), ...}"""
    cache, salida, omitidas = {}, [], []
    for col in columnas:
        expr = col.split(" AS ")[0].strip()
        a, _, nombre = expr.partition(".")
        if a in alias and nombre:
            if a not in cache:
                cache[a] = _columnas(con, *alias[a])
            if nombre not in cache[a]:
                omitidas.append(expr)
                continue
        salida.append(col)
    if omitidas:
        print("   vista comun.v_fact_viaje: se omiten columnas que aún no existen (volver a cargar su área): "
              + ", ".join(omitidas))
    return salida


def crear(con):
    """(Re)crea comun.v_fact_viaje. Devuelve False si todavía no está cargado peajes (la base de la vista)."""
    if not (_existe(con, "peajes", "fact_viaje_peaje") and _existe(con, "peajes", "dim_tramo")):
        return False
    columnas = ["p.nro_viaje", "p.nro_hoja_ruta", "f.fecha", "p.sk_tramo", "t.texto_tramo", "p.es_retorno",
                "p.peaje_referencial", "p.peaje_referencial_maximo", "p.estado_peaje"]
    uniones = ["FROM peajes.fact_viaje_peaje p",
               "JOIN comun.dim_fecha f ON f.sk_fecha = p.sk_fecha_inicio",
               "LEFT JOIN peajes.dim_tramo t ON t.sk_tramo = p.sk_tramo"]
    if _existe(con, "combustible", "fact_viaje_combustible"):
        columnas += ["c.equipo_id", "c.orden_cadena", "c.km_tramo", "c.tipo_movimiento", "c.vacio_fisico", "c.grupo_carga",
                     "c.litros_asignados", "c.metodo_asignacion AS metodo_asignacion_comb",
                     "c.costo_combustible", "c.precio_litro_aplicado AS precio_litro_comb"]
        uniones.append("LEFT JOIN combustible.fact_viaje_combustible c ON c.nro_viaje = p.nro_viaje")
    if _existe(con, "ventas", "fact_viaje_venta"):
        # ingreso, operador y pagos del viaje (ventas/construir_dw_ventas.py)
        columnas += ["vv.cliente_id", "cl.razon_social AS cliente", "vv.producto_id", "pr.producto AS producto_viaje",
                     "vv.tipo_operador", "vv.equipo_id AS tracto_id", "vv.rampla_id", "vv.conductor_interno",
                     "vv.estado_viaje", "vv.ingreso", "vv.valor_facturado", "vv.comision_conductor", "vv.pago_tercero",
                     "vv.pct_pago_tercero", "vv.comision_conductor_observada", "vv.pago_tercero_observado"]
        uniones += ["LEFT JOIN ventas.fact_viaje_venta vv ON vv.nro_viaje = p.nro_viaje",
                    "LEFT JOIN ventas.dim_cliente cl ON cl.cliente_id = vv.cliente_id",
                    "LEFT JOIN ventas.dim_producto_viaje pr ON pr.producto_id = vv.producto_id"]
    if _existe(con, "ciclos", "fact_viaje_ciclo"):
        # ciclo, orden y clasificación con reglas de retorno (ciclos/construir_dw_ciclos.py)
        columnas += ["cy.orden_procesado", "cy.clasificacion_viaje", "cy.regla_clasificacion",
                     "cy.tipo_carga_final", "cy.calidad_trayectoria",
                     "cy.ciclo_comercial", "cy.rol_comercial"]
        uniones.append("LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje = p.nro_viaje")
    if _existe(con, "combustible", "fact_viaje_combustible_tarificador"):
        # costo de combustible estimado como el tarificador (combustible/estimar_costo_tarificador.py)
        columnas += ["ct.precio_ponderado AS precio_litro_tarificador", "ct.rendimiento_camion",
                     "ct.costo_rend_camion AS costo_comb_rend_camion", "ct.costo_tarificador AS costo_comb_tarificador",
                     "ct.estado AS estado_comb_tarificador"]
        uniones.append("LEFT JOIN combustible.fact_viaje_combustible_tarificador ct ON ct.nro_viaje = p.nro_viaje")
    tiene_metodo = _existe(con, "costos", "fact_viaje_costo_indirecto") and con.uno(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'costos' "
        "AND table_name = 'fact_viaje_costo_indirecto' AND column_name = 'metodo'") is not None
    if tiene_metodo:
        # método tarificador (nombres de siempre) y método propuesto (sufijo _propuesto), lado a lado
        columnas += ["ci.es_flota_propia", "ci.es_tractoreo", "ci.km_tramo AS km_tramo_costos",
                     "cp.dias_viaje", "cp.usa_rampla_callegari",
                     "ci.costo_fijo_asignado", "ci.reposicion_asignada", "ci.costo_neumatico_asignado",
                     "ci.costo_indirecto_total", "ci.estado_costo_indirecto",
                     "cp.costo_fijo_asignado AS costo_fijo_propuesto", "cp.cf_flota", "cp.cf_rampla", "cp.cf_estructura",
                     "cp.cf_directo_fleteros", "cp.reposicion_asignada AS reposicion_propuesta",
                     "cp.costo_neumatico_asignado AS neumatico_propuesto",
                     "cp.costo_indirecto_total AS costo_indirecto_propuesto",
                     "cp.estado_costo_indirecto AS estado_costo_propuesto"]
        uniones.append("LEFT JOIN costos.fact_viaje_costo_indirecto ci ON ci.nro_viaje = p.nro_viaje "
                       "AND ci.metodo = 'tarificador'")
        uniones.append("LEFT JOIN costos.fact_viaje_costo_indirecto cp ON cp.nro_viaje = p.nro_viaje "
                       "AND cp.metodo = 'propuesto'")
    columnas = _solo_existentes(con, columnas, {
        "p": ("peajes", "fact_viaje_peaje"), "t": ("peajes", "dim_tramo"),
        "c": ("combustible", "fact_viaje_combustible"), "ct": ("combustible", "fact_viaje_combustible_tarificador"),
        "ci": ("costos", "fact_viaje_costo_indirecto"), "cp": ("costos", "fact_viaje_costo_indirecto"),
        "cy": ("ciclos", "fact_viaje_ciclo"), "vv": ("ventas", "fact_viaje_venta"), "cl": ("ventas", "dim_cliente"), "pr": ("ventas", "dim_producto_viaje")})
    con.execute("DROP VIEW IF EXISTS comun.v_fact_viaje CASCADE")      # CASCADE: arrastra v_margen_viaje
    con.execute("CREATE VIEW comun.v_fact_viaje AS\nSELECT " + ",\n       ".join(columnas) + "\n" + "\n".join(uniones))
    crear_margen(con)
    return True


MARGEN = """
CREATE VIEW comun.v_margen_viaje AS
SELECT v.*,
       -- Contribución PARCIAL: conserva el alcance anterior, pero usa importes
       -- observados y distingue dato faltante de cero. No es costo completo.
       CASE WHEN v.tipo_operador = 'propio' THEN
              v.ingreso - v.costo_combustible - v.peaje_referencial - v.comision_conductor_observada
            WHEN v.tipo_operador IN ('fletero', 'caterina') THEN v.ingreso - v.pago_tercero_observado END AS margen_contribucion,
       CASE WHEN v.tipo_operador = 'propio' THEN
              v.ingreso - v.costo_comb_rend_camion - v.peaje_referencial - v.comision_conductor_observada END
            AS margen_contribucion_comb_estimado,
       -- Referencia única: ya incluye comisión, termo y costos propios de
       -- terceros cuando corresponden. Nunca restarlos otra vez aquí.
       e.margen_3 AS margen_operacional,
       CASE WHEN v.ingreso IS NULL THEN 'sin ingreso'
            WHEN v.tipo_operador IS NULL OR v.tipo_operador NOT IN ('propio', 'fletero', 'caterina') THEN 'operador sin clasificar'
            WHEN e.nro_viaje IS NULL THEN 'sin escenario 3; recargar escenarios'
            WHEN NOT coalesce(e.completo_3,FALSE) THEN 'costos fisicos incompletos'
            WHEN v.tipo_operador IN ('fletero','caterina') THEN 'completo (tercero)'
            ELSE 'completo' END AS cobertura_margen,
       e.margen_comercial_3 AS margen_operacional_comercial,
       coalesce(e.completo_3,FALSE) AS costos_fisicos_completos,
       coalesce(e.completo_comercial_3,FALSE) AS costos_comerciales_completos,
       e.costo_3_real AS costo_fisico_escenario_3,
       e.costo_comercial_3 AS costo_comercial_escenario_3,
       'escenario 3; ingreso registrado; costo fisico modelado' AS definicion_margen_operacional,
       'escenario 3; ingreso registrado; costo redistribuido por ciclo' AS definicion_margen_comercial,
       'parcial: combustible y peaje referencial mas comision; terceros: pago observado' AS alcance_contribucion,
       CASE WHEN v.ingreso IS NULL THEN 'sin ingreso'
            WHEN v.tipo_operador='propio' AND v.comision_conductor_observada IS NULL THEN 'sin comision observada'
            WHEN v.tipo_operador='propio' AND v.costo_combustible IS NULL THEN 'sin combustible asignado'
            WHEN v.tipo_operador='propio' AND v.peaje_referencial IS NULL THEN 'sin peaje referencial'
            WHEN v.tipo_operador='propio' THEN 'disponible dentro del alcance parcial'
            WHEN v.tipo_operador IN ('fletero','caterina') AND v.pago_tercero_observado IS NULL THEN 'sin pago observado'
            WHEN v.tipo_operador IN ('fletero','caterina') THEN 'disponible dentro del alcance parcial'
            ELSE 'operador sin clasificar' END AS cobertura_contribucion,
       CASE WHEN v.ingreso IS NULL THEN 'sin ingreso'
            WHEN v.tipo_operador IS NULL OR v.tipo_operador<>'propio' THEN 'no aplica'
            WHEN v.comision_conductor_observada IS NULL THEN 'sin comision observada'
            WHEN v.costo_comb_rend_camion IS NULL THEN 'sin combustible estimado'
            WHEN v.peaje_referencial IS NULL THEN 'sin peaje referencial'
            ELSE 'disponible dentro del alcance parcial' END AS cobertura_contribucion_comb_estimado
FROM comun.v_fact_viaje v
LEFT JOIN escenarios.v_costo_viaje_resumen e ON e.nro_viaje=v.nro_viaje
"""
NECESARIAS = {"ingreso", "tipo_operador", "pago_tercero", "comision_conductor", "costo_combustible", "peaje_referencial",
              "costo_comb_rend_camion", "costo_indirecto_total", "comision_conductor_observada", "pago_tercero_observado"}
CANONICAS = {'nro_viaje','margen_3','margen_comercial_3','completo_3','completo_comercial_3',
             'costo_3_real','costo_comercial_3'}


def sql_margen(hay_escenarios=True):
    if hay_escenarios:
        return MARGEN
    # Durante una reconstrucción pueden no existir aún los escenarios.
    # Se permite la contribución parcial, nunca una fórmula operacional alternativa.
    vacio = '''(SELECT CAST(NULL AS BIGINT) AS nro_viaje,
        CAST(NULL AS DOUBLE PRECISION) AS margen_3, CAST(NULL AS DOUBLE PRECISION) AS margen_comercial_3,
        CAST(NULL AS BOOLEAN) AS completo_3, CAST(NULL AS BOOLEAN) AS completo_comercial_3,
        CAST(NULL AS DOUBLE PRECISION) AS costo_3_real, CAST(NULL AS DOUBLE PRECISION) AS costo_comercial_3)'''
    return MARGEN.replace('LEFT JOIN escenarios.v_costo_viaje_resumen e','LEFT JOIN '+vacio+' e')


def crear_margen(con):
    """Contribución parcial y márgenes canónicos físico/comercial del escenario 3."""
    con.execute("DROP VIEW IF EXISTS comun.v_margen_viaje")
    faltan = NECESARIAS - _columnas(con, "comun", "v_fact_viaje")
    if faltan:
        print("   vista comun.v_margen_viaje: no se crea, faltan " + ", ".join(sorted(faltan))
              + " (cargar ventas, combustible y costos)")
        return False
    hay = (_existe(con,'escenarios','v_costo_viaje_resumen') and
           CANONICAS <= _columnas(con,'escenarios','v_costo_viaje_resumen'))
    con.execute(sql_margen(hay))
    if not hay:
        print('   margen operacional pendiente de escenarios; contribucion parcial disponible')
    return True
