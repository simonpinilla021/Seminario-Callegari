#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Rendimiento de la flota y asignación de viajes (solo lectura: no modifica la base).

Métrica: LITROS (no pesos), porque el precio subió mucho y los litros son estables en la historia.

Lee combustible.fact_bloque_combustible, combustible.fact_viaje_combustible, combustible.ref_regresion_camion
y, si existen, ventas.fact_viaje_venta + ventas.dim_cliente (para comparar por tramo × cliente).

Secciones:
  1. Rendimiento por camión: bruto (km/L de sus bloques ok) y AJUSTADO por lo que hizo (modelo sobre bloques:
     L/100 km ~ camión + trimestre + % km cargado + km por viaje del bloque + % km local). El ajustado responde
     "¿cuánto gastaría este camión haciendo el mismo trabajo que el promedio de la flota?".
  2. Por modelo de camión.
  3. Asignación: ¿los camiones eficientes hacen los viajes largos? (km por tipo de viaje según eficiencia).
     ¿La ventaja del camión eficiente es mayor en viajes largos que en cortos?
  4. Mismo tramo (× cliente), distinto camión: L/100 km de cada viaje cargado vs. la mediana de su grupo.
  5. Pérdida: litros sobre la referencia (mediana de la flota y cuartil eficiente), por camión y por año,
     y cuánto se ahorraría si los km de los camiones menos eficientes los hicieran los más eficientes.

Uso (en la carpeta MVP Data Warehouse):
    python combustible\analizar_flota.py  [--desde 2024-01-01] [--precio 1000]
Salida: combustible\salida\analisis_flota.txt y CSV de detalle.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

MIN_BLOQUES = 20          # bloques ok mínimos para estimar el efecto de un camión
MIN_VIAJES_GRUPO = 20     # viajes mínimos de un tramo (× cliente) para compararlo
MIN_CAMIONES_GRUPO = 4


def clase_viaje(km):
    return np.select([km < 200, km < 1000], ["local (< 200 km)", "corto (200–999 km)"], "largo (≥ 1.000 km)")


# ---------------------------------------------------------------------------------------------------- modelo
def modelo_bloques(b):
    """Mínimos cuadrados ponderados por km: L/100 ~ camión + trimestre + pc + pl + log(km/viaje)."""
    b = b.copy()
    X = pd.get_dummies(b[["equipo_id", "trimestre"]].astype(str), dtype=float)
    X = X.drop(columns=[X.filter(like="trimestre_").columns[0]])
    for c in ("pc", "pl", "lkv"):
        X[c] = b[c].values
    w = np.sqrt(b["km_viajes"].values)
    coef, *_ = np.linalg.lstsq(X.values * w[:, None], b["l100"].values * w, rcond=None)
    co = pd.Series(coef, X.columns)
    pred = X.values @ coef
    res = b["l100"].values - pred
    ww = b["km_viajes"].values
    r2 = 1 - np.average(res ** 2, weights=ww) / np.average((b["l100"] - np.average(b["l100"], weights=ww)) ** 2,
                                                           weights=ww)
    # ajustado = efecto del camión evaluado en el trabajo promedio de la flota (covariables y trimestres medios)
    medias = {c: np.average(b[c], weights=ww) for c in ("pc", "pl", "lkv")}
    tri = X.filter(like="trimestre_")
    base = sum(co[c] * medias[c] for c in medias) + float(np.average(tri.values, axis=0, weights=ww) @ co[tri.columns])
    fe = co.filter(like="equipo_id_")
    fe.index = fe.index.str.replace("equipo_id_", "").astype("int64")
    return (fe + base).rename("l100_ajustado"), co, r2, res


def pmediana(x, w):
    o = np.argsort(x.values)
    x, w = x.values[o], w.values[o]
    return x[np.searchsorted(np.cumsum(w), w.sum() / 2)]


# ---------------------------------------------------------------------------------------------------- análisis
def analizar(b, v, cam, desde, precio, p, salida):
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_rows", 300)
    b = b[(b["calidad"] == "ok") & (b["inicio"] >= desde) & (b["km_viajes"] > 0) & (b["viajes"] > 0)].copy()
    b["l100"] = 100 * b["litros"] / b["km_viajes"]
    b["pc"] = b["km_cargados"] / b["km_viajes"]
    b["pl"] = b["km_local"].fillna(0) / b["km_viajes"]
    b["lkv"] = np.log(b["km_viajes"] / b["viajes"])
    b["trimestre"] = b["inicio"].dt.to_period("Q").astype(str)
    hasta = b["termino"].max()
    v = v[v["fecha"] >= desde].copy()
    v["clase"] = clase_viaje(v["km_tramo"].fillna(0))
    v["cargado"] = v["vacio_fisico"] != "S"

    # ------------------------------------------------------------ 1. por camión
    nb = b.groupby("equipo_id").size()
    bm = b[b["equipo_id"].isin(nb[nb >= MIN_BLOQUES].index)]
    aj, co, r2, _ = modelo_bloques(bm)
    t = b.groupby("equipo_id").agg(bloques=("bloque", "size"), km=("km_viajes", "sum"), litros=("litros", "sum"),
                                   km_cargados=("km_cargados", "sum"), dias=("dias", "sum"))
    t["kmL_bruto"] = t["km"] / t["litros"]
    t["l100_bruto"] = 100 * t["litros"] / t["km"]
    t["pct_km_cargado"] = (100 * t["km_cargados"] / t["km"]).round(1)
    t = t.join(aj)
    t["kmL_ajustado"] = 100 / t["l100_ajustado"]
    vc = v[v["cargado"] & (v["es_virtual"] != "S")]
    t = t.join(vc.groupby("equipo_id")["km_tramo"].mean().rename("km_medio_viaje_cargado"))
    t = t.join(v.groupby(["equipo_id", "clase"])["km_tramo"].sum().unstack(fill_value=0)
               .pipe(lambda d: (100 * d.div(d.sum(axis=1), axis=0)).round(1)).add_prefix("% km "))
    t = cam.set_index("equipo_id")[["codigo", "modelo"]].join(t, how="right")
    t["revisar"] = np.select(
        [t["bloques"] < MIN_BLOQUES, t["kmL_bruto"] > 3.4, t["kmL_bruto"] < 2.0],
        ["pocos bloques: sin ajuste", "rinde demasiado: ¿cargas no registradas?", "rinde muy poco: revisar"], "")
    conf = t["l100_ajustado"].notna() & (t["revisar"] == "")      # los dudosos no fijan referencias ni grupos
    terc = t.loc[conf, "l100_ajustado"].rank(pct=True)
    t["grupo"] = "sin ajuste"
    t.loc[conf, "grupo"] = np.select([terc <= 1 / 3, terc <= 2 / 3], ["eficiente", "medio"], "ineficiente")
    t.loc[t["l100_ajustado"].notna() & ~conf, "grupo"] = "dudoso"
    ref_med = pmediana(t.loc[conf, "l100_ajustado"], t.loc[conf, "km"])
    t["indice"] = (100 * t["l100_ajustado"] / ref_med).round(1)         # 100 = mediana de la flota; > 100 gasta más
    cols = ["codigo", "modelo", "bloques", "km", "litros", "kmL_bruto", "kmL_ajustado", "l100_ajustado", "indice",
            "grupo", "pct_km_cargado", "km_medio_viaje_cargado"] + [c for c in t.columns if c.startswith("% km ")] + \
           ["revisar"]
    t = t[cols].sort_values("l100_ajustado")
    p(f"ANÁLISIS DE RENDIMIENTO DE LA FLOTA — bloques ok desde {desde.date()} hasta {hasta.date()} "
      f"({len(b):,} bloques, {b['equipo_id'].nunique()} camiones)")
    p("1. Rendimiento por camión (km de tabla de tramos). Ajustado = mismo trabajo que el promedio de la flota. "
      "Índice 100 = mediana de la flota (ponderada por km); 110 = gasta 10 % más",
      f"Modelo sobre bloques: R² {r2:.2f} · por cada 10 puntos más de km cargado: {co['pc'] / 10:+.2f} L/100 · "
      f"si los viajes del bloque son el doble de largos: {co['lkv'] * np.log(2):+.2f} L/100 · "
      f"mediana ajustada de la flota: {ref_med:.1f} L/100 ({100 / ref_med:.2f} km/L)",
      "Grupo: tercil de eficiencia ajustada entre camiones confiables; 'dudoso' = rendimiento fuera de lo posible "
      "(no fija referencias ni entra en las secciones 3–5).", "", t.round(2))
    t.to_csv(salida / "flota_rendimiento.csv", encoding="utf-8-sig")

    # ------------------------------------------------------------ 2. por modelo
    m = t[t["grupo"].isin(["eficiente", "medio", "ineficiente"])].groupby(t["modelo"].fillna("sin modelo")).apply(
        lambda g: pd.Series({"camiones": len(g), "km": g["km"].sum(),
                             "kmL_bruto": g["km"].sum() / g["litros"].sum(),
                             "l100_ajustado": np.average(g["l100_ajustado"], weights=g["km"]),
                             "min_indice": g["indice"].min(), "max_indice": g["indice"].max(),
                             "km_medio_viaje_cargado": np.average(g["km_medio_viaje_cargado"].fillna(0),
                                                                  weights=g["km"])}),
        include_groups=False)
    m["kmL_ajustado"] = 100 / m["l100_ajustado"]
    p("2. Por modelo (promedios ponderados por km)", m.sort_values("l100_ajustado").round(2))

    # ------------------------------------------------------------ 3. asignación
    va = v.merge(t[["grupo", "l100_ajustado"]], left_on="equipo_id", right_index=True)
    va = va[va["grupo"].isin(["eficiente", "medio", "ineficiente"])]
    mk = pd.crosstab(va["grupo"], va["clase"], values=va["km_tramo"], aggfunc="sum").fillna(0)
    mk_pct = (100 * mk.div(mk.sum(axis=1), axis=0)).round(1)
    mk_col = (100 * mk.div(mk.sum(axis=0), axis=1)).round(1)
    tt = t[t["grupo"].isin(["eficiente", "medio", "ineficiente"])]
    corr_largo = tt["l100_ajustado"].corr(tt.get("% km largo (≥ 1.000 km)", pd.Series(dtype=float)))
    corr_km = tt["l100_ajustado"].corr(tt["km"])
    # ¿la brecha entre camiones es mayor en bloques largos? modelo por separado en bloques cortos y largos
    corte = bm["lkv"].median()
    aj_c, *_ = modelo_bloques(bm[bm["lkv"] <= corte])
    aj_l, *_ = modelo_bloques(bm[bm["lkv"] > corte])
    gg = pd.DataFrame({"corto": aj_c, "largo": aj_l}).join(t["grupo"]).dropna()
    brecha = gg.groupby("grupo")[["corto", "largo"]].median()
    p("3. Asignación: ¿quién hace los viajes largos? (grupo = tercil de eficiencia ajustada)",
      "km por clase de viaje — % de los km de cada grupo:", mk_pct, "",
      "% de los km de cada clase que hizo cada grupo:", mk_col, "",
      f"correlación eficiencia (L/100 ajustado) vs % de km en viajes largos: {corr_largo:+.2f} "
      "(negativa = los eficientes hacen más viajes largos, que es lo deseable)",
      f"correlación eficiencia vs km totales del camión: {corr_km:+.2f} (negativa = los eficientes trabajan más)",
      "", f"L/100 ajustado según bloques cortos (viaje medio ≤ {np.exp(corte):,.0f} km) y largos, mediana por grupo:",
      brecha.round(2),
      f"brecha ineficiente − eficiente: corto {brecha.loc['ineficiente', 'corto'] - brecha.loc['eficiente', 'corto']:.2f}"
      f" · largo {brecha.loc['ineficiente', 'largo'] - brecha.loc['eficiente', 'largo']:.2f} L/100 "
      "(si es mayor en largos, conviene que los eficientes hagan los largos)")

    # ------------------------------------------------------------ 4. mismo tramo (× cliente), distinto camión
    vt = v[v["cargado"] & (v["es_virtual"] != "S") & (v["metodo_asignacion"] == "bloque ok")
           & ~v["equipo_id"].isin(t.index[t["grupo"] == "dudoso"])
           & (v["km_tramo"] > 0) & (v["litros_asignados"] > 0)].copy()
    vt["l100"] = 100 * vt["litros_asignados"] / vt["km_tramo"]
    claves = ["tramo_texto"] + (["cliente"] if vt["cliente"].notna().any() else [])
    vt["cliente"] = vt["cliente"].fillna("(sin cliente)")
    gk = vt.groupby(claves)
    vt["n"] = gk["l100"].transform("size")
    vt["ncam"] = gk["equipo_id"].transform("nunique")
    vt = vt[(vt["n"] >= MIN_VIAJES_GRUPO) & (vt["ncam"] >= MIN_CAMIONES_GRUPO)].copy()
    gk = vt.groupby(claves)
    vt["l100_grupo"] = gk["l100"].transform("median")
    vt["litros_ref"] = vt["l100_grupo"] * vt["km_tramo"] / 100
    vt["litros_extra"] = vt["litros_asignados"] - vt["litros_ref"]
    por_cam = vt.groupby(claves + ["equipo_id"]).agg(viajes=("l100", "size"), l100=("l100", "median"))
    g = gk.agg(viajes=("l100", "size"), camiones=("equipo_id", "nunique"), km_tramo=("km_tramo", "median"),
               l100_mediana=("l100", "median"), l100_p25=("l100", lambda s: s.quantile(.25)),
               l100_p75=("l100", lambda s: s.quantile(.75)), litros_viaje=("litros_asignados", "median"),
               litros_extra=("litros_extra", lambda s: s[s > 0].sum()))
    g["mejor_camion_l100"] = por_cam[por_cam["viajes"] >= 3].groupby(claves)["l100"].min()
    g["peor_camion_l100"] = por_cam[por_cam["viajes"] >= 3].groupby(claves)["l100"].max()
    g["dif_p75_p25_pct"] = (100 * (g["l100_p75"] / g["l100_p25"] - 1)).round(1)
    idx = vt.assign(r=vt["l100"] / vt["l100_grupo"]).groupby("equipo_id")["r"].median().rename("indice_tramos") * 100
    cons = t[["indice"]].join(idx, how="inner").dropna()
    p(f"4. Mismo {' × '.join(claves)}, distinto camión — viajes cargados con litros de bloques ok "
      f"(grupos con ≥ {MIN_VIAJES_GRUPO} viajes y ≥ {MIN_CAMIONES_GRUPO} camiones: {len(g):,} grupos, {len(vt):,} viajes)",
      "Sin camiones dudosos. OJO: los litros de un viaje salen del bloque (consumo real del camión en ese trecho) repartidos por km; "
      "comparan bien camiones, pero no ven diferencias de consumo entre tramos dentro de un mismo bloque.",
      "", "Los 25 grupos con más litros sobre su mediana:",
      g.sort_values("litros_extra", ascending=False).head(25).round(1), "",
      f"Coherencia: correlación entre el índice por bloques (sección 1) y el índice por tramos: "
      f"{cons['indice'].corr(cons['indice_tramos']):+.2f} ({len(cons)} camiones)")
    g.to_csv(salida / "tramo_camion_grupos.csv", encoding="utf-8-sig")
    por_cam.to_csv(salida / "tramo_camion_detalle.csv", encoding="utf-8-sig")

    # ------------------------------------------------------------ 5. pérdida
    q = t[t["grupo"].isin(["eficiente", "medio", "ineficiente"])].copy()
    efi = q[q["grupo"] == "eficiente"]
    ref_efi = np.average(efi["l100_ajustado"], weights=efi["km"])
    q["litros_extra_vs_mediana"] = (q["l100_ajustado"] - ref_med) / 100 * q["km"]
    q["litros_extra_vs_eficientes"] = (q["l100_ajustado"] - ref_efi) / 100 * q["km"]
    bb = bm.merge(q[["l100_ajustado"]], left_on="equipo_id", right_index=True)
    bb["anio"] = bb["inicio"].dt.year
    bb["extra_med"] = (bb["l100_ajustado"] - ref_med) / 100 * bb["km_viajes"]
    bb["extra_efi"] = (bb["l100_ajustado"] - ref_efi) / 100 * bb["km_viajes"]
    pa = bb.groupby("anio").agg(km=("km_viajes", "sum"), litros=("litros", "sum"),
                                sobre_mediana=("extra_med", lambda s: s[s > 0].sum()),
                                sobre_eficientes=("extra_efi", lambda s: s[s > 0].sum()))
    pa["% litros sobre eficientes"] = (100 * pa["sobre_eficientes"] / pa["litros"]).round(1)
    inef = q[q["grupo"] == "ineficiente"]
    ahorro = ((inef["l100_ajustado"] - ref_efi) / 100 * inef["km"]).sum()
    total_l = q["litros"].sum()
    p(f"5. Pérdida por ineficiencia (litros; valor referencial a ${precio:,.0f}/L)",
      f"Referencias: mediana de la flota {ref_med:.1f} L/100 · tercil eficiente {ref_efi:.1f} L/100",
      f"Litros totales de los camiones con ajuste: {total_l:,.0f}",
      f"Litros sobre la mediana (solo camiones que gastan más): "
      f"{q['litros_extra_vs_mediana'].clip(lower=0).sum():,.0f} L "
      f"(${q['litros_extra_vs_mediana'].clip(lower=0).sum() * precio / 1e6:,.1f} MM)",
      f"Litros sobre el tercil eficiente: {q['litros_extra_vs_eficientes'].clip(lower=0).sum():,.0f} L "
      f"(${q['litros_extra_vs_eficientes'].clip(lower=0).sum() * precio / 1e6:,.1f} MM) = "
      f"{100 * q['litros_extra_vs_eficientes'].clip(lower=0).sum() / total_l:.1f} % de los litros",
      f"Si los km del tercil ineficiente los hubiera hecho un camión con el rendimiento del tercil eficiente: "
      f"{ahorro:,.0f} L (${ahorro * precio / 1e6:,.1f} MM) — tope teórico, supone que hay camiones eficientes "
      "disponibles", "", "Por año:", pa.round(0), "",
      "Camiones con más litros sobre el tercil eficiente:",
      q.sort_values("litros_extra_vs_eficientes", ascending=False)[
          ["codigo", "modelo", "km", "kmL_ajustado", "indice", "litros_extra_vs_mediana",
           "litros_extra_vs_eficientes", "revisar"]].head(20).round(1))
    q.to_csv(salida / "flota_perdida.csv", encoding="utf-8-sig")


# ---------------------------------------------------------------------------------------------------- carga
def cargar(con):
    b = con.df("SELECT equipo_id, bloque, litros::float8 AS litros, inicio, termino, km_viajes::float8 AS km_viajes, "
               "km_cargados::float8 AS km_cargados, km_vacios::float8 AS km_vacios, km_local::float8 AS km_local, "
               "viajes, dias::float8 AS dias, calidad FROM combustible.fact_bloque_combustible")
    hay_ventas = con.df("SELECT to_regclass('ventas.fact_viaje_venta') IS NOT NULL "
                        "AND to_regclass('ventas.dim_cliente') IS NOT NULL AS ok").iloc[0, 0]
    if hay_ventas:
        v = con.df("""SELECT f.nro_viaje, f.equipo_id, f.sk_fecha_inicio, f.tramo_texto, f.km_tramo::float8 AS km_tramo,
                             f.vacio_fisico, f.es_virtual, f.grupo_carga, f.litros_asignados::float8 AS litros_asignados,
                             f.metodo_asignacion, cl.razon_social AS cliente
                      FROM combustible.fact_viaje_combustible f
                      LEFT JOIN ventas.fact_viaje_venta vv ON vv.nro_viaje = f.nro_viaje
                      LEFT JOIN ventas.dim_cliente cl ON cl.cliente_id = vv.cliente_id""")
    else:                                        # sin esquema ventas: se compara solo por tramo
        print("aviso: no está el esquema ventas; se compara solo por tramo (sin cliente)")
        v = con.df("""SELECT nro_viaje, equipo_id, sk_fecha_inicio, tramo_texto, km_tramo::float8 AS km_tramo,
                             vacio_fisico, es_virtual, grupo_carga, litros_asignados::float8 AS litros_asignados,
                             metodo_asignacion, NULL::text AS cliente FROM combustible.fact_viaje_combustible""")
    cam = con.df("SELECT equipo_id, codigo, modelo FROM combustible.ref_regresion_camion")
    return preparar(b, v, cam)


def preparar(b, v, cam):
    b["inicio"] = pd.to_datetime(b["inicio"])
    b["termino"] = pd.to_datetime(b["termino"])
    v["fecha"] = pd.to_datetime(v["sk_fecha_inicio"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    for d in (b, v, cam):
        d["equipo_id"] = d["equipo_id"].astype("int64")
    return b, v, cam


def principal(argv=None, _conn=None):
    from comun import db  # noqa: E402
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2024-01-01")
    ap.add_argument("--precio", type=float, default=1000.0, help="$/L para valorizar los litros (referencial)")
    a = ap.parse_args(argv)
    con = db.conectar(("comun",), _conn=_conn)
    b, v, cam = cargar(con)
    con.close()
    salida = AQUI / "salida"
    salida.mkdir(exist_ok=True)
    lineas = []

    def p(titulo, *bloques):
        x = ["", "=" * 100, titulo, "=" * 100] + [y.to_string() if isinstance(y, (pd.DataFrame, pd.Series)) else str(y)
                                                  for y in bloques]
        lineas.extend(x)
        print("\n".join(x))

    analizar(b, v, cam, pd.Timestamp(a.desde), a.precio, p, salida)
    (salida / "analisis_flota.txt").write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nListo: {salida / 'analisis_flota.txt'}")


if __name__ == "__main__":
    principal(sys.argv[1:])
