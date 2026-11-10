#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resumen de márgenes por viaje desde comun.v_margen_viaje (solo lectura: no modifica la base).
Escribe ventas/salida/resumen_margen.txt y los CSV de detalle, para revisar sin abrir Power BI.

Uso (en la carpeta MVP Data Warehouse):  python ventas\\resumen_margen.py
"""
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from comun import db  # noqa: E402

DESDE, HASTA = "2025-01-01", "2026-03-31"     # ventana con combustible real y peajes


def principal(argv=None, _conn=None):
    con = db.conectar(("comun",), _conn=_conn)
    salida = AQUI / "salida"
    salida.mkdir(exist_ok=True)
    lineas = []

    def p(titulo, df=None):
        bloque = ["", "=" * 100, titulo, "=" * 100] + ([df.to_string(index=False)] if df is not None else [])
        lineas.extend(bloque)
        print("\n".join(bloque))

    pd.set_option("display.width", 250, "display.max_columns", 40)
    p('Definiciones: operacional = escenario 3 fisico; comercial = escenario 3 redistribuido. '
      'Contribucion es parcial (combustible, peaje y comision; terceros: pago). '
      'Las columnas de costos tarificador son referencia, no una resta adicional al operacional.')
    mm = lambda c: f"ROUND((SUM({c}) / 1e6)::numeric, 1)"   # noqa: E731

    p("1. Cobertura del margen por año y operador (MM$)", con.df(f"""
        SELECT EXTRACT(YEAR FROM fecha)::int AS anio, tipo_operador, cobertura_margen, COUNT(*) AS viajes,
               {mm('ingreso')} AS ingreso, {mm('margen_contribucion')} AS margen_contrib,
               {mm('margen_operacional')} AS margen_oper
        FROM comun.v_margen_viaje WHERE fecha >= DATE '2022-01-01'
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""))

    base = f"FROM comun.v_margen_viaje WHERE fecha BETWEEN DATE '{DESDE}' AND DATE '{HASTA}'"
    p(f"2. Resultado {DESDE} a {HASTA} por operador (viajes con margen completo, MM$)", con.df(f"""
        SELECT tipo_operador, COUNT(*) AS viajes, SUM(CASE WHEN ingreso = 0 THEN 1 ELSE 0 END) AS con_ingreso_0,
               {mm('ingreso')} AS ingreso, {mm('costo_combustible')} AS combustible, {mm('peaje_referencial')} AS peaje,
               {mm('comision_conductor')} AS comision, {mm('pago_tercero')} AS pago_tercero,
               {mm('margen_contribucion')} AS margen_contrib,
               ROUND((100 * SUM(margen_contribucion) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_contrib,
               {mm('costo_indirecto_total')} AS indirecto_tarif, {mm('costo_indirecto_propuesto')} AS indirecto_prop,
               {mm('margen_operacional')} AS margen_oper,
               ROUND((100 * SUM(margen_operacional) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_oper
        {base} AND cobertura_margen IN ('completo', 'completo (tercero)') GROUP BY 1 ORDER BY 2 DESC"""))

    p("3. Propios por tipo de movimiento (vista física: los vacíos no tienen ingreso)", con.df(f"""
        SELECT COALESCE(tipo_movimiento, '(sin combustible)') AS tipo_movimiento, COUNT(*) AS viajes,
               {mm('ingreso')} AS ingreso, {mm('costo_combustible')} AS combustible, {mm('peaje_referencial')} AS peaje,
               {mm('margen_contribucion')} AS margen_contrib, {mm('margen_operacional')} AS margen_oper
        {base} AND tipo_operador = 'propio' AND cobertura_margen = 'completo' GROUP BY 1 ORDER BY 2 DESC"""))

    p("4. Contribucion parcial: combustible asignado vs estimado (mismos viajes disponibles)", con.df(f"""
        SELECT COUNT(*) AS viajes, {mm('costo_combustible')} AS comb_real, {mm('costo_comb_rend_camion')} AS comb_rend_camion,
               {mm('costo_comb_tarificador')} AS comb_2_6, {mm('margen_contribucion')} AS margen_real,
               {mm('margen_contribucion_comb_estimado')} AS margen_comb_estimado
        {base} AND tipo_operador = 'propio' AND cobertura_margen = 'completo'
          AND cobertura_contribucion='disponible dentro del alcance parcial'
          AND cobertura_contribucion_comb_estimado='disponible dentro del alcance parcial'"""))

    cli = con.df(f"""
        SELECT COALESCE(cliente, cliente_id::text) AS cliente, tipo_operador, COUNT(*) AS viajes,
               {mm('ingreso')} AS ingreso, {mm('margen_contribucion')} AS margen_contrib,
               ROUND((100 * SUM(margen_contribucion) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_contrib,
               {mm('margen_operacional')} AS margen_oper
        {base} AND cobertura_margen IN ('completo', 'completo (tercero)') AND ingreso > 0
        GROUP BY 1, 2 ORDER BY 4 DESC""")
    p("5. Clientes por ingreso (solo viajes con ingreso; sin el costo de sus vacíos: vista física)", cli.head(25))
    cli.to_csv(salida / "margen_por_cliente.csv", index=False, encoding="utf-8-sig")

    tra = con.df(f"""
        SELECT texto_tramo, COUNT(*) AS viajes, {mm('ingreso')} AS ingreso, {mm('costo_combustible')} AS combustible,
               {mm('peaje_referencial')} AS peaje, {mm('margen_contribucion')} AS margen_contrib,
               ROUND((100 * SUM(margen_contribucion) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_contrib
        {base} AND tipo_operador = 'propio' AND cobertura_margen = 'completo' AND ingreso > 0
        GROUP BY 1 ORDER BY 3 DESC""")
    p("6. Tramos de propios por ingreso (solo viajes con ingreso)", tra.head(25))
    tra.to_csv(salida / "margen_por_tramo.csv", index=False, encoding="utf-8-sig")

    p("7. Controles", con.df(f"""
        SELECT SUM(CASE WHEN ingreso IS NULL THEN 1 ELSE 0 END) AS sin_ingreso,
               SUM(CASE WHEN ingreso < 0 THEN 1 ELSE 0 END) AS ingreso_negativo,
               SUM(CASE WHEN tipo_operador = 'propio' AND costo_combustible IS NOT NULL AND ingreso > 0
                        AND margen_contribucion < 0 THEN 1 ELSE 0 END) AS propios_con_ingreso_y_margen_neg,
               SUM(CASE WHEN tipo_operador <> 'propio' AND pago_tercero > ingreso THEN 1 ELSE 0 END) AS terceros_pago_mayor_ingreso,
               SUM(CASE WHEN valor_facturado IS NOT NULL AND ABS(valor_facturado - ingreso) > 1 THEN 1 ELSE 0 END)
                   AS facturado_distinto_de_ingreso
        {base}"""))

    cols = set(con.df("SELECT * FROM comun.v_margen_viaje LIMIT 0").columns)
    if {"ciclo_comercial", "clasificacion_viaje"} <= cols:
        secciones_ciclos(con, p, mm, base, salida)
    else:
        p("8–12. Ciclos: no disponibles (cargar ciclos\\construir_dw_ciclos.py)")

    (salida / "resumen_margen.txt").write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nListo: {salida / 'resumen_margen.txt'}")
    con.close()


def secciones_ciclos(con, p, mm, base, salida):
    """Margen con la clasificación con reglas de retorno y margen por ciclo (la ida junto con su vuelta vacía)."""
    p("8. Propios por clasificación con reglas de retorno (viajes con margen completo, MM$)", con.df(f"""
        SELECT COALESCE(clasificacion_viaje, '(sin ciclo)') AS clasificacion, COUNT(*) AS viajes,
               {mm('ingreso')} AS ingreso, {mm('costo_combustible')} AS combustible, {mm('peaje_referencial')} AS peaje,
               {mm('comision_conductor')} AS comision, {mm('margen_contribucion')} AS margen_contrib,
               ROUND((100 * SUM(margen_contribucion) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_contrib,
               {mm('margen_operacional')} AS margen_oper
        {base} AND tipo_operador = 'propio' AND cobertura_margen = 'completo' GROUP BY 1 ORDER BY 2 DESC"""))

    p("9. Retornos por carga (tipo_carga_final, todos los operadores, MM$)", con.df(f"""
        SELECT tipo_carga_final, tipo_operador, COUNT(*) AS viajes, {mm('ingreso')} AS ingreso,
               {mm('margen_contribucion')} AS margen_contrib,
               ROUND((100 * SUM(margen_contribucion) / NULLIF(SUM(ingreso), 0))::numeric, 1) AS pct_contrib
        {base} AND clasificacion_viaje = 'Retorno' AND cobertura_margen IN ('completo', 'completo (tercero)')
        GROUP BY 1, 2 ORDER BY 4 DESC"""))

    if not con.existe_tabla('ds_gestion','v_resultado_ciclo'):
        p('10. Resultado por ciclo Santiago/cortes Coquimbo: publicación ds_gestion pendiente.')
        return
    desde, hasta = int(DESDE.replace('-', '')), int(HASTA.replace('-', ''))
    datos = con.df("""SELECT r.escenario_id,count(*) AS ciclos,
      sum(r.ingreso_estimado) AS ingreso,sum(r.costo_comercial) AS costo_comercial,
      sum(r.margen_comercial) AS margen_comercial
      FROM ds_gestion.v_resultado_ciclo r JOIN ds_gestion.v_evaluacion_ciclo e USING(ciclo_id,escenario_id)
      WHERE e.politica_id='conservador' AND e.evaluable
      AND r.fecha_documental_principal_id BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""",(desde,hasta))
    p('10. Ciclos Santiago con cortes Coquimbo; política conservadora; escenarios separados, CLP',datos)


if __name__ == "__main__":
    principal(sys.argv[1:])
