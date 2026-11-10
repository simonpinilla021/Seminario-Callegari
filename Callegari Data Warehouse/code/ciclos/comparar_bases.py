#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
¿Qué base conviene para el ciclo económico: Coquimbo o Santiago? (solo lectura: no modifica la base).
Lee ciclos.fact_viaje_ciclo y ventas.fact_viaje_venta. Solo camiones PROPIOS, viajes desde --desde.

Para cada base candidata arma el ciclo "abre al salir de la base y cierra al llegar a ella" y compara:
  1. Cobertura y tamaño: ciclos completos, viajes, días, km, % km cargado.
  2. Padre de los vacíos: % de km vacíos que quedan en un ciclo con al menos un viaje cargado.
  3. Días: dentro del ciclo vs. entre ciclos (días que ningún ciclo absorbe) y dónde espera el camión.
  4. Coherencia con la clasificación del ERP: un viaje cargado que SALE de la base es "envío natural" y uno que
     LLEGA a la base es "retorno natural". Se compara con tipo_movimiento (sin reglas) y clasificacion_viaje
     (con reglas de retorno): cuántas excepciones necesitaría cada base.
  5. Ciclos con varios envíos: cuántos son y cuánto cambia el reparto de vacíos y estructura si se reparte
     por km del envío o por ingreso del envío.

Uso (en la carpeta MVP Data Warehouse):  python ciclos\comparar_bases.py   [--desde 2024-01-01]
Salida: ciclos\salida\comparar_bases.txt y ciclos\salida\ciclos_<base>.csv
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

COQ, STGO = "BASE COQUIMBO", "BASE SANTIAGO"
BASES = {"coquimbo": COQ, "santiago": STGO}
KM_DIA = 650          # km que avanza un camión en un día (solo para estimar días de espera)


def cuantiles(s, dec=1):
    s = pd.Series(s).dropna()
    if not len(s):
        return "sin datos"
    q = s.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
    return " · ".join(f"p{int(k * 100)} {v:,.{dec}f}" for k, v in q.items()) + f" · n={len(s):,}"


def letra(c):
    return {"Envio": "E", "Retorno": "R", "Vacio": "V"}.get(c, "?")


def armar_ciclos(v, base):
    """Asigna a cada viaje (v ordenado por camión y orden) un ciclo que abre al salir de `base` y cierra al llegar."""
    ids, estados, cid = [], [], 0
    for _, gr in v.groupby("equipo_id", sort=False):
        abierto, inc = False, False
        for bo, bd in zip(gr["bo"], gr["bd"]):
            if not abierto:
                if bo == base and bd == base:
                    ids.append(None)
                    estados.append("local en la base")
                    continue
                cid += 1
                abierto, inc = True, bo != base
            ids.append(cid)
            estados.append("incompleto" if inc else "completo")
            if bd == base:
                abierto = False
    out = v.copy()
    out["ciclo"] = ids
    out["estado"] = estados
    ult = out.groupby("ciclo")["bd"].transform("last")
    out.loc[out["ciclo"].notna() & (ult != base), "estado"] = "abierto"
    return out


def resumir_ciclos(c):
    x = c[c["ciclo"].notna()].copy()
    x["km_c"] = x["km"].where(x["cargado"], 0.0)
    x["km_v"] = x["km"].where(~x["cargado"], 0.0)
    x["es_e"] = x["L"] == "E"
    x["es_r"] = x["L"] == "R"
    cy = x.groupby("ciclo").agg(
        equipo_id=("equipo_id", "first"), estado=("estado", "first"), inicio=("fecha", "min"), fin=("fecha", "max"),
        viajes=("id_viaje", "size"), km=("km", "sum"), km_cargado=("km_c", "sum"), km_vacio=("km_v", "sum"),
        ingreso=("ingreso", "sum"), envios=("es_e", "sum"), retornos=("es_r", "sum"),
        patron=("L", lambda s: "".join(s)),
        recorrido=("d", lambda s: " / ".join([x.loc[s.index[0], "o"]] + list(s))))
    cy = cy.sort_values(["equipo_id", "inicio"])
    cy["inicio_siguiente"] = cy.groupby("equipo_id")["inicio"].shift(-1)
    cy["dias"] = (cy["fin"] - cy["inicio"]).dt.days
    cy["dias_hasta_siguiente"] = (cy["inicio_siguiente"] - cy["fin"]).dt.days
    cy["pct_km_cargado"] = (100 * cy["km_cargado"] / cy["km"].where(cy["km"] > 0)).round(1)
    cy["cargados"] = cy["envios"] + cy["retornos"]
    return cy


def reparto_envios(c, cy):
    """Ciclos completos con 2+ envíos: participación de cada envío por km y por ingreso."""
    multi = cy[(cy["estado"] == "completo") & (cy["envios"] >= 2)].index
    e = c[c["ciclo"].isin(multi) & (c["L"] == "E")].copy()
    if not len(e):
        return None, None
    e["sh_km"] = e["km"] / e.groupby("ciclo")["km"].transform("sum")
    e["sh_ing"] = e["ingreso"] / e.groupby("ciclo")["ingreso"].transform("sum")
    d = e.groupby("ciclo").apply(lambda g: 0.5 * (g["sh_km"] - g["sh_ing"]).abs().sum(), include_groups=False)
    ratio_km = e.groupby("ciclo")["km"].agg(lambda s: s.min() / s.max() if s.max() > 0 else np.nan)
    ratio_ing = e.groupby("ciclo")["ingreso"].agg(lambda s: s.min() / s.max() if s.max() > 0 else np.nan)
    ing_km = e.assign(ipk=e["ingreso"] / e["km"].where(e["km"] > 0)).groupby("ciclo")["ipk"].agg(
        lambda s: s.min() / s.max() if s.max() > 0 else np.nan)
    return pd.DataFrame({"diferencia_reparto": d, "ratio_km": ratio_km, "ratio_ingreso": ratio_ing,
                         "ratio_ingreso_por_km": ing_km}), e


def analizar(v, desde, p, salida):
    w_ids = set(v.loc[v["fecha"] >= desde, "id_viaje"])
    res = {}
    for nombre, base in BASES.items():
        c = armar_ciclos(v, base)
        cy = resumir_ciclos(c)
        cy = cy[cy["inicio"] >= desde]
        c = c[c["id_viaje"].isin(w_ids)]
        res[nombre] = (c, cy)
        cy.to_csv(salida / f"ciclos_{nombre}.csv", encoding="utf-8-sig")

    # 1. cobertura y tamaño
    filas = {}
    for nombre, (c, cy) in res.items():
        cc = cy[cy["estado"] == "completo"]
        en_completo = c["ciclo"].isin(cc.index)
        filas[nombre] = {
            "ciclos completos": len(cc), "ciclos abiertos/incompletos": int((cy["estado"] != "completo").sum()),
            "% viajes en ciclo completo": round(100 * en_completo.mean(), 1),
            "% km en ciclo completo": round(100 * c.loc[en_completo, "km"].sum() / c["km"].sum(), 1),
            "viajes locales en la base": int((c["estado"] == "local en la base").sum()),
            "viajes por ciclo (mediana)": cc["viajes"].median(), "días por ciclo (mediana)": cc["dias"].median(),
            "km por ciclo (mediana)": cc["km"].median(), "% km cargado (mediana)": cc["pct_km_cargado"].median(),
            "ciclos sin ningún viaje cargado": int((cc["cargados"] == 0).sum()),
            "ciclos con 0 envíos": int((cc["envios"] == 0).sum()), "ciclos con 1 envío": int((cc["envios"] == 1).sum()),
            "ciclos con 2+ envíos": int((cc["envios"] >= 2).sum()),
            "% ingreso en ciclos 2+ envíos": round(100 * cc.loc[cc["envios"] >= 2, "ingreso"].sum()
                                                  / cc["ingreso"].sum(), 1),
        }
    p("1. Cobertura y tamaño de los ciclos según la base (solo ciclos que empiezan desde --desde)",
      pd.DataFrame(filas))
    for nombre, (c, cy) in res.items():
        cc = cy[cy["estado"] == "completo"]
        pat = cc.groupby("patron").agg(ciclos=("viajes", "size"), km_mediana=("km", "median"),
                                       dias_mediana=("dias", "median"), pct_km_cargado=("pct_km_cargado", "median"))
        p(f"1b. Patrones más frecuentes — base {nombre}", pat.sort_values("ciclos", ascending=False).head(15))

    # 2. padre de los vacíos
    filas = {}
    for nombre, (c, cy) in res.items():
        cc = cy[cy["estado"] == "completo"]
        vac = c[~c["cargado"]]
        con_padre = vac["ciclo"].isin(cc[cc["cargados"] > 0].index)
        sin_carga = vac["ciclo"].isin(cc[cc["cargados"] == 0].index)
        fuera = ~vac["ciclo"].isin(cc.index)
        filas[nombre] = {
            "km vacíos": vac["km"].sum(),
            "% en ciclo con carga (tiene padre)": round(100 * vac.loc[con_padre, "km"].sum() / vac["km"].sum(), 1),
            "% en ciclo sin carga (huérfano)": round(100 * vac.loc[sin_carga, "km"].sum() / vac["km"].sum(), 1),
            "% fuera de ciclo completo (local/abierto)": round(100 * vac.loc[fuera, "km"].sum() / vac["km"].sum(), 1),
        }
    p("2. Padre de los vacíos (km vacíos según el ciclo en que quedan)", pd.DataFrame(filas))

    # 3. días dentro / entre ciclos y dónde espera el camión
    filas = {}
    for nombre, (c, cy) in res.items():
        cc = cy[cy["estado"] == "completo"]
        dentro, entre = cc["dias"].sum(), cc["dias_hasta_siguiente"].clip(lower=0).sum()
        filas[nombre] = {"días dentro de ciclos": dentro, "días entre ciclos": entre,
                         "% días que ningún ciclo absorbe": round(100 * entre / (dentro + entre), 1),
                         "días entre ciclos (mediana)": cc["dias_hasta_siguiente"].median()}
    p("3. Días del camión: dentro de los ciclos vs. entre ciclos (desde la fecha del último viaje del ciclo hasta la "
      "del primero del siguiente; incluye el tiempo de viaje del último tramo)", pd.DataFrame(filas))
    c0 = res["coquimbo"][0]
    lugar = np.select([c0["bd"] == COQ, c0["bd"] == STGO], ["Coquimbo / La Serena", "Santiago"], "otro lugar")
    esp = (c0["dias_al_siguiente"] - (c0["km"].fillna(0) / KM_DIA).round()).clip(lower=0)
    t = pd.DataFrame({"lugar": lugar, "espera": esp}).groupby("lugar")["espera"].agg(
        llegadas="size", dias_espera_total="sum", mediana="median", p75=lambda s: s.quantile(0.75))
    t["pct_dias"] = (100 * t["dias_espera_total"] / t["dias_espera_total"].sum()).round(1)
    p(f"3b. Dónde espera el camión: días entre un viaje y el siguiente, menos los días de viaje estimados "
      f"(km ÷ {KM_DIA}), según dónde terminó el viaje", t.round(1),
      "", "Con base Coquimbo, la espera en Coquimbo queda ENTRE ciclos y la de Santiago DENTRO; con base Santiago, "
          "al revés.")

    # 4. coherencia con la clasificación
    filas = []
    for nombre, base in BASES.items():
        c = res[nombre][0]
        cg = c[c["cargado"]]
        natural = np.select([cg["bd"] == base, cg["bo"] == base], ["retorno natural", "envío natural"],
                            "intermedio (ni sale ni llega a la base)")
        for col, etiqueta in (("tipo_movimiento", "ERP sin reglas"), ("clasificacion_viaje", "con reglas de retorno")):
            ct = pd.crosstab(pd.Series(natural, index=cg.index, name=f"base {nombre}"), cg[col].rename(etiqueta))
            p(f"4. Viajes cargados: tipo natural con base {nombre} (filas) vs. {etiqueta} (columnas)", ct)
            ok = ((natural == "envío natural") & (cg[col] == "Envio")) | \
                 ((natural == "retorno natural") & (cg[col] == "Retorno"))
            filas.append({"base": nombre, "comparado con": etiqueta, "coinciden %": round(100 * ok.mean(), 1),
                          "no coinciden (excepciones)": int((~ok).sum())})
    p("4b. Resumen: % de viajes cargados cuya clasificación coincide con la de la base", pd.DataFrame(filas))

    # 5. ciclos con varios envíos
    for nombre, (c, cy) in res.items():
        r, e = reparto_envios(c, cy)
        if r is None:
            continue
        cc = cy.loc[r.index]
        p(f"5. Base {nombre}: ciclos completos con 2+ envíos ({len(r):,})",
          "patrones: " + str(cc["patron"].value_counts().head(8).to_dict()),
          "recorridos: " + str(cc["recorrido"].value_counts().head(6).to_dict()),
          "km del envío más corto ÷ más largo: " + cuantiles(r["ratio_km"], 2),
          "ingreso del envío menor ÷ mayor: " + cuantiles(r["ratio_ingreso"], 2),
          "ingreso/km del envío menor ÷ mayor: " + cuantiles(r["ratio_ingreso_por_km"], 2),
          "parte de los costos compartidos que cambia de envío si se reparte por km en vez de por ingreso: "
          + cuantiles(100 * r["diferencia_reparto"], 1) + " (%)",
          f"km vacíos + días de estos ciclos = {cc['km_vacio'].sum():,.0f} km vacíos · {cc['dias'].sum():,} días")
        e.to_csv(salida / f"envios_multiples_{nombre}.csv", index=False, encoding="utf-8-sig")


def principal(argv=None, _conn=None):
    from comun import db  # noqa: E402
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2024-01-01")
    a = ap.parse_args(argv)
    con = db.conectar(("comun",), _conn=_conn)
    salida = AQUI / "salida"
    salida.mkdir(exist_ok=True)
    lineas = []

    def p(titulo, *bloques):
        b = ["", "=" * 100, titulo, "=" * 100] + [x.to_string() if isinstance(x, (pd.DataFrame, pd.Series)) else str(x)
                                                  for x in bloques]
        lineas.extend(b)
        print("\n".join(b))

    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_rows", 200)
    v = con.df("""SELECT c.id_viaje, c.equipo_id, c.orden_procesado, c.sk_fecha, c.km::float8 AS km,
                         c.ciudad_origen_limpia AS o, c.ciudad_destino_limpia AS d,
                         c.base_origen AS bo, c.base_destino AS bd, c.ingreso::float8 AS ingreso,
                         c.tipo_movimiento, c.clasificacion_viaje, c.es_virtual, vv.tipo_operador
                  FROM ciclos.fact_viaje_ciclo c
                  LEFT JOIN ventas.fact_viaje_venta vv ON vv.nro_viaje = c.nro_viaje""")
    con.close()
    v = preparar(v)
    p(f"COMPARACIÓN DE BASES PARA EL CICLO — camiones propios ({v['equipo_id'].nunique()}), desde {a.desde}")
    analizar(v, pd.Timestamp(a.desde), p, salida)
    (salida / "comparar_bases.txt").write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nListo: {salida / 'comparar_bases.txt'}")


def preparar(v):
    v["fecha"] = pd.to_datetime(v["sk_fecha"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    op = v[v["es_virtual"] == "N"].groupby("equipo_id")["tipo_operador"].agg(lambda s: (s == "propio").mean())
    v = v[v["equipo_id"].isin(set(op[op >= 0.5].index))]
    v = v.sort_values(["equipo_id", "orden_procesado"]).reset_index(drop=True)
    for c in ("bo", "bd"):
        v[c] = v[c].fillna("SIN BASE")
    v["km"] = v["km"].fillna(0.0)
    v["ingreso"] = v["ingreso"].fillna(0.0)
    v["cargado"] = v["ingreso"] > 0
    v["L"] = v["clasificacion_viaje"].map(letra)
    v["dias_al_siguiente"] = (v.groupby("equipo_id")["fecha"].shift(-1) - v["fecha"]).dt.days
    return v


if __name__ == "__main__":
    principal(sys.argv[1:])
