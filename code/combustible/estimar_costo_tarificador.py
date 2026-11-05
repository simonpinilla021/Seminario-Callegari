#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Costo de combustible ESTIMADO como lo calcula el tarificador, por viaje (contraste con el costo real del método
por bloques). Escribe solo combustible.fact_viaje_combustible_tarificador (y su fila en comun.registro_carga).

  precio del tarificador (día del viaje) = pct_base × costo real granel (base, ATP) + (1 − pct_base) × costo real TCT
                                           (carretera, COP) + recargo        (precios.v_costo_combustible_dia)
     Parámetros del Excel del tarificador (escenarios/tarificador_out/parametros.csv, leído con leer_tarificador.py):
     al 15.10.2026: 68 % base / 32 % TCT, recargo + $100/L (planilla I4) y 2,65 km/L. Antes de 2026-10-24 este
     script usaba 78/22, sin recargo y 2,6 km/L (fotos del tarificador de agosto).

  Costo 1 · rendimiento del camión:  km del tramo ÷ rendimiento del camión × precio ponderado
            rendimiento del camión = mediana de los últimos N bloques certificados P7 cerrados ANTES del viaje,
            en km de tabla por litro. N tiene vigencia en combustible.parametro_rendimiento_tarificador (inicial: 5).
  Costo 2 · rendimiento del tarificador: km del tramo ÷ 2,65 × precio del tarificador

  km del tramo = peajes.dim_tramo.km_erp (tabla de tramos del ERP, la que usa el tarificador)

Necesita cargados: peajes (viajes y tramos), precios y combustible (bloques). Lo llama construir_todo.py después de
combustible; también se puede ejecutar solo:
  python estimar_costo_tarificador.py                  # parámetros por defecto
  La ventana se administra en la tabla de parámetros, no desde la línea de comandos.
  python estimar_costo_tarificador.py --pct-base 0.68 --rendimiento 2.65 --recargo 100
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from comun import db, vista_viaje  # noqa: E402

VERSION = "2026-11-03-rendimiento-certificado"
ESQUEMAS = ("combustible", "comun")
PARAMETROS_TARIFICADOR = AQUI.parent / "escenarios" / "tarificador_out" / "parametros.csv"


def _param(nombre, defecto):
    """Valor del Excel del tarificador (parametros.csv); si no está, el defecto."""
    try:
        t = pd.read_csv(PARAMETROS_TARIFICADOR, encoding="utf-8-sig")
        return float(t.loc[t["parametro"] == nombre, "valor"].iloc[0])
    except Exception:
        return defecto


PCT_BASE = _param("pct_base", 0.68)                 # tarificador al 15.10.2026: 68 % granel (base) / 32 % TCT
REND_TARIFICADOR = _param("rendimiento_kml", 2.65)  # km/L (planilla D27 = Parametros B5)
RECARGO = _param("recargo_precio", 100.0)           # $/L que el tarificador suma al precio promedio (planilla I4)

TABLA = """
DROP TABLE IF EXISTS fact_viaje_combustible_tarificador CASCADE;   -- CASCADE: la vista comun.v_fact_viaje se rehace al final
CREATE TABLE fact_viaje_combustible_tarificador (
  nro_viaje BIGINT PRIMARY KEY,
  fecha DATE,
  equipo_id BIGINT,                         -- tracto (sit_mainteam_id)
  codigo_camion TEXT,
  grupo_equipo TEXT,                        -- TC propio · FL fletero (ficha de equipos)
  sk_tramo BIGINT,
  km_tramo DOUBLE PRECISION,                -- peajes.dim_tramo.km_erp
  precio_base DOUBLE PRECISION,             -- costo real granel (ATP) del día, $/L
  precio_ruta DOUBLE PRECISION,             -- costo real TCT (COP) del día, $/L
  pct_base DOUBLE PRECISION,                -- 0,68
  recargo_precio DOUBLE PRECISION,          -- + $100/L del tarificador
  precio_ponderado DOUBLE PRECISION,        -- pct_base × base + (1 − pct_base) × ruta + recargo (precio del tarificador)
  rendimiento_camion DOUBLE PRECISION,      -- km/L: mediana de los últimos N bloques ok antes del viaje
  bloques_rendimiento INTEGER,              -- cuántos bloques se usaron (≤ N)
  fecha_ultimo_bloque TIMESTAMP,            -- cierre del bloque más reciente usado
  litros_rend_camion DOUBLE PRECISION,
  costo_rend_camion DOUBLE PRECISION,       -- Costo 1
  rendimiento_tarificador DOUBLE PRECISION, -- 2,65
  litros_tarificador DOUBLE PRECISION,
  costo_tarificador DOUBLE PRECISION,       -- Costo 2
  estado TEXT);                             -- ok · sin rendimiento del camión · sin precio · sin km
"""


def leer_parquet(clave, nombres, columnas):
    carpeta = db.ruta_config("fuentes", clave)
    for n in nombres:
        ruta = carpeta / n
        if ruta.exists():
            df = pd.read_parquet(ruta, columns=columnas)
            print(f"   {ruta.name}: {len(df):,} filas")
            return df
    raise SystemExit(f"No encuentro {nombres} en {carpeta}")


def rendimiento_al_dia(viajes, bloques, n):
    """Para cada viaje, mediana del rendimiento de los últimos n bloques ok del camión cerrados antes del viaje."""
    b = bloques.sort_values(["equipo_id", "termino"]).copy()
    g = b.groupby("equipo_id")["rendimiento_viajes"]
    b["rend_movil"] = g.transform(lambda s: s.rolling(n, min_periods=1).median())
    b["n_movil"] = g.transform(lambda s: s.rolling(n, min_periods=1).count())
    izq = viajes.dropna(subset=["equipo_id", "fecha"]).copy()
    izq["fecha_dt"] = pd.to_datetime(izq["fecha"])
    izq = izq.sort_values("fecha_dt")
    der = b.dropna(subset=["termino"]).sort_values("termino")[["equipo_id", "termino", "rend_movil", "n_movil"]]
    m = pd.merge_asof(izq, der, left_on="fecha_dt", right_on="termino", by="equipo_id", direction="backward",
                      allow_exact_matches=False)          # solo bloques cerrados antes del día del viaje
    return m[["nro_viaje", "rend_movil", "n_movil", "termino"]]


def principal(argv=None, _conn=None):
    ap = argparse.ArgumentParser(description="Costo de combustible estimado como el tarificador, por viaje.")
    ap.add_argument("--pct-base", type=float, default=PCT_BASE, help="peso del precio de base (granel); resto = TCT")
    ap.add_argument("--rendimiento", type=float, default=REND_TARIFICADOR, help="km/L del tarificador")
    ap.add_argument("--recargo", type=float, default=RECARGO, help="$/L que se suma al precio ponderado")
    ap.add_argument("--bloques", type=int, default=None, help="compatibilidad: debe coincidir con el parámetro vigente de bloques certificados")
    a = ap.parse_args(argv)
    print(f"estimar_costo_tarificador.py versión {VERSION}")
    con = db.conectar(ESQUEMAS, _conn=_conn)
    for esq, tabla in [("peajes", "fact_viaje_peaje"), ("peajes", "dim_tramo"), ("combustible", "fact_bloque_combustible")]:
        if not con.existe_tabla(esq, tabla):
            raise SystemExit(f"Falta {esq}.{tabla}: cargar antes peajes y combustible")
    hay_precios = con.uno("SELECT 1 FROM information_schema.views WHERE table_schema='precios' "
                          "AND table_name='v_costo_combustible_dia'") is not None
    if not hay_precios:
        print("   aviso: falta precios.v_costo_combustible_dia (cargar precios): se calculan solo litros")

    viajes = con.df("""SELECT p.nro_viaje, f.fecha::text AS fecha, p.sk_tramo, t.km_erp::float8 AS km_tramo
                       FROM peajes.fact_viaje_peaje p
                       JOIN comun.dim_fecha f ON f.sk_fecha = p.sk_fecha_inicio
                       LEFT JOIN peajes.dim_tramo t ON t.sk_tramo = p.sk_tramo""")
    vta = leer_parquet("erp", ["adempiere_sit_pbix_travelvta_v.parquet", "sit_pbix_travelvta_v.parquet"],
                       ["c_orderline_id", "sit_mainteam_id"])
    vta = pd.DataFrame({"nro_viaje": pd.to_numeric(vta["c_orderline_id"], errors="coerce").astype("Int64"),
                        "equipo_id": pd.to_numeric(vta["sit_mainteam_id"], errors="coerce").astype("Int64")}
                       ).dropna(subset=["nro_viaje"]).drop_duplicates("nro_viaje")
    viajes["nro_viaje"] = viajes["nro_viaje"].astype("Int64")
    viajes = viajes.merge(vta, on="nro_viaje", how="left")
    try:
        veh = leer_parquet("combustible", ["sit_pbix_travelveh_v.parquet", "adempiere_sit_pbix_travelveh_v.parquet"],
                           ["a_asset_id", "codigo", "grupoemp"])
        veh = pd.DataFrame({"equipo_id": pd.to_numeric(veh["a_asset_id"], errors="coerce").astype("Int64"),
                            "codigo_camion": veh["codigo"].astype(str).str.strip(),
                            "grupo_equipo": veh["grupoemp"].astype(str).str.strip()}).drop_duplicates("equipo_id")
        viajes = viajes.merge(veh, on="equipo_id", how="left")
    except SystemExit as e:                                   # la ficha de equipos es solo descriptiva
        print(f"   aviso: {e}")
        viajes["codigo_camion"] = viajes["grupo_equipo"] = None

    from combustible.rendimiento_tarificador_certificado import publicar as publicar_rendimiento
    publicar_rendimiento(con)
    if a.bloques is not None:
        ventanas=con.df("SELECT DISTINCT valor FROM combustible.parametro_rendimiento_tarificador WHERE parametro='bloques_recientes'")
        if not ventanas.valor.eq(a.bloques).all():
            raise ValueError('Cambiar la ventana en la tabla de parámetros con vigencia; no sobrescribirla desde CLI')
    viajes["km_tramo"] = pd.to_numeric(viajes["km_tramo"], errors="coerce")
    rend = con.df('''SELECT nro_viaje,rendimiento_previo AS rend_movil,bloques_previos AS n_movil,
      fecha_ultimo_previo AS termino FROM combustible.v_rendimiento_tarificador_certificado''')
    viajes = viajes.merge(rend, on="nro_viaje", how="left")

    if hay_precios:
        pr = con.df("SELECT fecha::text AS fecha, oilsupplier, costo_real::float8 AS costo_real "
                    "FROM precios.v_costo_combustible_dia")
        pr["costo_real"] = pd.to_numeric(pr["costo_real"], errors="coerce")
        pr = pr.pivot_table(index="fecha", columns="oilsupplier", values="costo_real", aggfunc="first")
        viajes["precio_base"] = viajes["fecha"].map(pr.get("ATP", pd.Series(dtype=float)))
        viajes["precio_ruta"] = viajes["fecha"].map(pr.get("COP", pd.Series(dtype=float)))
    else:
        viajes["precio_base"] = viajes["precio_ruta"] = np.nan
    viajes["pct_base"] = a.pct_base
    viajes["recargo_precio"] = a.recargo
    viajes["precio_ponderado"] = (a.pct_base * viajes["precio_base"] + (1 - a.pct_base) * viajes["precio_ruta"]
                                  + a.recargo)

    km = viajes["km_tramo"].where(viajes["km_tramo"] > 0)
    viajes["rendimiento_camion"] = viajes["rend_movil"]
    viajes["litros_rend_camion"] = km / viajes["rendimiento_camion"]
    viajes["costo_rend_camion"] = viajes["litros_rend_camion"] * viajes["precio_ponderado"]
    viajes["rendimiento_tarificador"] = a.rendimiento
    viajes["litros_tarificador"] = km / a.rendimiento
    viajes["costo_tarificador"] = viajes["litros_tarificador"] * viajes["precio_ponderado"]
    viajes["estado"] = np.select(
        [km.isna(), viajes["precio_ponderado"].isna(), viajes["rendimiento_camion"].isna()],
        ["sin km", "sin precio", "sin rendimiento del camión"], "ok")

    salida = pd.DataFrame({
        "nro_viaje": viajes["nro_viaje"], "fecha": viajes["fecha"], "equipo_id": viajes["equipo_id"],
        "codigo_camion": viajes["codigo_camion"], "grupo_equipo": viajes["grupo_equipo"],
        "sk_tramo": viajes["sk_tramo"], "km_tramo": viajes["km_tramo"],
        "precio_base": viajes["precio_base"], "precio_ruta": viajes["precio_ruta"], "pct_base": viajes["pct_base"],
        "recargo_precio": viajes["recargo_precio"], "precio_ponderado": viajes["precio_ponderado"], "rendimiento_camion": viajes["rendimiento_camion"],
        "bloques_rendimiento": viajes["n_movil"].astype("Int64"), "fecha_ultimo_bloque": viajes["termino"],
        "litros_rend_camion": viajes["litros_rend_camion"], "costo_rend_camion": viajes["costo_rend_camion"],
        "rendimiento_tarificador": viajes["rendimiento_tarificador"], "litros_tarificador": viajes["litros_tarificador"],
        "costo_tarificador": viajes["costo_tarificador"], "estado": viajes["estado"]})
    salida["fecha_ultimo_bloque"] = salida["fecha_ultimo_bloque"].astype(object).where(salida["fecha_ultimo_bloque"].notna(), None)

    with con:
        con.execute(TABLA)
        con.copiar_df("fact_viaje_combustible_tarificador", salida)
        con.execute("CREATE INDEX IF NOT EXISTS ix_comb_tarif_fecha ON fact_viaje_combustible_tarificador (fecha)")
        estados = salida["estado"].value_counts().to_dict()
        ok = salida[salida["estado"] == "ok"]
        detalle = (f"viajes {len(salida)}; estados {estados}; pct_base {a.pct_base}; recargo {a.recargo}; rendimiento tarificador "
                   f"{a.rendimiento}; bloques certificados según parámetro vigente; costo 1 (rend. camión) {ok['costo_rend_camion'].sum():,.0f}; "
                   f"costo 2 (rend. {a.rendimiento}) {ok['costo_tarificador'].sum():,.0f} (viajes ok)")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("combustible_tarificador", db.ahora(), VERSION, "", detalle))
        vista = vista_viaje.crear(con)

    print(f"   fact_viaje_combustible_tarificador {len(salida):7d}")
    print("   estados: " + ", ".join(f"{k} {v:,}" for k, v in estados.items()))
    if len(ok):
        res = ok.assign(anio=pd.to_datetime(ok["fecha"]).dt.year).groupby("anio").agg(
            viajes=("nro_viaje", "size"), km=("km_tramo", "sum"), precio_pond=("precio_ponderado", "mean"),
            rend_camion=("rendimiento_camion", "median"), costo_rend_camion=("costo_rend_camion", "sum"),
            costo_tarificador=("costo_tarificador", "sum"))
        res["dif_pct"] = 100 * (res["costo_rend_camion"] / res["costo_tarificador"] - 1)
        for c in ("costo_rend_camion", "costo_tarificador"):
            res[c] = (res[c] / 1e6).round(1)
        print("   viajes ok por año (costos en MM$; dif = costo 1 vs costo 2):")
        print(res.round(2).to_string().replace("\n", "\n   "))
    print(f"   vista comun.v_fact_viaje: {'actualizada' if vista else 'no (falta peajes)'}")
    con.close()


if __name__ == "__main__":
    principal(sys.argv[1:])
