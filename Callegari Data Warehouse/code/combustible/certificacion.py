"""
Certificación del rendimiento y litros para el margen (niveles 1 a 4). Lo llama asignar_litros.py al final (P7).

Idea: los litros reales de un viaje se usan en el margen solo si vienen de un bloque confiable ("certificado").
Si no, se usan litros teóricos = km del viaje × consumo normal del camión, calculado SOLO con bloques certificados.

  Nivel 1  lectura del odómetro en los dos llenados que cierran el bloque (sistema y GPS)
           A = las dos fuentes avanzan lo mismo (±5 % o ±50 km) · B = una sola fuente creíble · C = ninguna
  Nivel 2  bloque certificado = lectura A o B · km odómetro ÷ km viajes a ±10 % de lo habitual del camión ·
           rendimiento (km viajes ÷ litros) entre 1,2 y el máximo de su generación y mezcla cargado/vacío
  Nivel 3  nota mensual del camión = km ÷ litros de sus bloques certificados del mes; se publica con ≥ 3.000 km
           y ≥ 60 % de los litros del mes certificados; alerta si se aleja > ±15 % de su mediana de 12 meses
  Nivel 4  ficha del camión por mes = L/100 km cargado y vacío, regresión con 12 meses de bloques certificados
           (≥ 20 bloques); si no alcanza o da un valor imposible: ficha del modelo, luego de la flota
  Viaje    real certificado (todo el viaje en bloques certificados y litros a ±15 % del teórico) ·
           real con alerta (bloques certificados, fuera de ±15 %: se mantienen los reales) · teórico (resto)

Salidas nuevas en bloques_out (no cambian las que carga construir_dw_combustible.py):
  bloques_certificados.csv · rendimiento_camion_mes.csv · ficha_consumo_camion_mes.csv ·
  litros_viaje_certificado.csv · generacion_camion.csv
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- parámetros (editables)
TOL_FUENTES = 0.05           # nivel 1: diferencia tolerada entre el avance del odómetro del sistema y el del GPS
TOL_FUENTES_KM = 50          #          … o en km, lo que sea mayor
PLAUSIBLE_FUENTE = (0.5, 1.5)  # nivel 1: una fuente sola es creíble si sus km ÷ km esperados caen en este rango
TOL_CERT = 0.10              # nivel 2: km odómetro ÷ km viajes a ±10 % de la razón habitual del camión
REND_MIN = 1.2               # km/L mínimo (viajes de más de KM_LOCAL)
REND_MAX = {"moderno": (3.6, 4.5), "antiguo": (3.3, 4.0), "rigido": (4.0, 5.0)}   # (cargado, rampla vacía)
ANIO_MODERNO = 2015          # con año de fabricación: anterior = antiguo
N3_KM_MIN, N3_LITROS_CERT_MIN, N3_ALERTA = 3000, 0.60, 0.15
N4_MESES, N4_BLOQUES_MIN = 12, 20
BANDA_VIAJE = 0.15           # viaje: litros reales a ±15 % del teórico
KM_LOCAL = 50


def generacion(modelo, anio=None):
    m = str(modelo).upper()
    if "ATEGO" in m:
        return "rigido"
    if anio is not None and pd.notna(anio) and 1950 < anio < 2100:
        return "antiguo" if anio < ANIO_MODERNO else "moderno"
    if m.startswith("9200") or m.startswith("CA113"):
        return "antiguo"
    return "moderno"


def _ajuste(X, y):
    """Mínimos cuadrados sin constante con un recorte de residuos extremos (como P5)."""
    for _ in range(2):
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        r = y - X @ beta
        mad = np.median(np.abs(r - np.median(r))) or 1.0
        keep = np.abs(r - np.median(r)) <= 3.5 * 1.4826 * mad
        if keep.all():
            break
        X, y = X[keep], y[keep]
    return np.linalg.lstsq(X, y, rcond=None)[0]


def _ficha_valida(a, b, gen):
    """a, b = litros por km cargado y vacío. Válida si ambos rendimientos son físicamente posibles."""
    if not (a > 0 and b > 0):
        return False
    mc, mv = REND_MAX[gen]
    return REND_MIN <= 1 / a <= mc and REND_MIN <= 1 / b <= mv and a >= 0.95 * b


def ejecutar(pa, pegado, ct, b0, bl, pzb, res, eq, eq_crudo, salida, p, titulo, l100_local=None):
    titulo("P7", "Certificación del rendimiento (niveles 1 a 4) y litros para el margen")

    # ---------------------------------------------------------------- generación de cada camión
    anio_cam, col_anio = {}, None
    if eq_crudo is not None:
        cand = [c for c in eq_crudo.columns if any(k in c.lower() for k in ("year", "anio", "año", "fabric"))]
        if cand:
            col_anio = cand[0]
            idcol = next((c for c in eq_crudo.columns if c.lower() == "a_asset_id"), None)
            if idcol:
                anio_cam = dict(zip(pd.to_numeric(eq_crudo[idcol], errors="coerce"),
                                    pd.to_numeric(eq_crudo[col_anio], errors="coerce")))
    eqm = eq.drop_duplicates("equipo").set_index("equipo")
    gen = {e: generacion(eqm.at[e, "modelo"] if e in eqm.index else "", anio_cam.get(e)) for e in bl["equipo"].unique()}
    tg = pd.DataFrame({"equipo": list(gen)}).assign(
        codigo=lambda d: d["equipo"].map(eqm["codigo"]), modelo=lambda d: d["equipo"].map(eqm["modelo"]),
        anio=lambda d: d["equipo"].map(anio_cam), generacion=lambda d: d["equipo"].map(gen))
    tg["max_cargado"] = tg["generacion"].map(lambda g: REND_MAX[g][0])
    tg["max_vacio"] = tg["generacion"].map(lambda g: REND_MAX[g][1])
    tg.to_csv(salida / "generacion_camion.csv", index=False)
    p(f"Año del camión: {'columna ' + col_anio if col_anio else 'no viene en el maestro (generación según el modelo)'}",
      f"Camiones por generación: {tg['generacion'].value_counts().to_dict()} · máximos km/L (cargado, vacío): {REND_MAX}")

    # ---------------------------------------------------------------- nivel 1: lecturas en los extremos del bloque
    c = ct[["equipo", "c", "odo"]].copy()
    c["gps"] = pa.loc[ct.index, "odo_gps"].where(~pegado.loc[ct.index]).values
    ini = b0.groupby(["equipo", "bloque_u"])["bloque"].agg(b_ini="min", b_fin="max").reset_index()
    ini = ini.merge(c.rename(columns={"c": "b_ini", "odo": "sis_ini", "gps": "gps_ini"}).assign(b_ini=lambda d: d["b_ini"] + 1),
                    on=["equipo", "b_ini"], how="left")
    ini = ini.merge(c.rename(columns={"c": "b_fin", "odo": "sis_fin", "gps": "gps_fin"}), on=["equipo", "b_fin"], how="left")
    b = bl.merge(ini[["equipo", "bloque_u", "sis_ini", "sis_fin", "gps_ini", "gps_fin"]], on=["equipo", "bloque_u"], how="left")
    b["km_sis"] = b["sis_fin"] - b["sis_ini"]
    b["km_gps"] = b["gps_fin"] - b["gps_ini"]
    ref = b["km_viajes"] * b["centro"]
    ok_s = b["km_sis"] > 0
    ok_g = b["km_gps"] > 0
    acuerdo = ok_s & ok_g & ((b["km_sis"] - b["km_gps"]).abs() <= np.maximum(TOL_FUENTES * b[["km_sis", "km_gps"]].max(axis=1), TOL_FUENTES_KM))
    rs, rg = b["km_sis"] / ref.where(ref > 0), b["km_gps"] / ref.where(ref > 0)
    pl_s = ok_s & rs.between(*PLAUSIBLE_FUENTE)
    pl_g = ok_g & rg.between(*PLAUSIBLE_FUENTE)
    usa_gps = pl_g & (~pl_s | ((rg - 1).abs() < (rs - 1).abs()))
    b["grado_lectura"] = np.select([acuerdo, pl_s | pl_g], ["A", "B"], "C")
    b["fuente_km_cert"] = np.select([acuerdo, usa_gps, pl_s], ["sistema=GPS", "GPS", "sistema"], "ninguna")
    b["km_cert"] = np.select([acuerdo, usa_gps, pl_s], [b["km_sis"], b["km_gps"], b["km_sis"]], np.nan)

    # ---------------------------------------------------------------- nivel 2: bloque certificado
    g_ = b["equipo"].map(gen).fillna("moderno")
    fc = (b["km_c"] / b["km_viajes"].where(b["km_viajes"] > 0)).fillna(0.5)
    b["rend_max"] = 1 / (fc / g_.map(lambda k: REND_MAX[k][0]) + (1 - fc) / g_.map(lambda k: REND_MAX[k][1]))
    b["razon_cert"] = b["km_cert"] / ref.where(ref > 0)
    b["rend_cert"] = b["km_viajes"] / b["litros"].where(b["litros"] > 0)
    b["motivo"] = np.select(
        [b["km_viajes"] <= 0, b["grado_lectura"] == "C", (b["razon_cert"] - 1).abs() > TOL_CERT,
         b["rend_cert"] < REND_MIN, b["rend_cert"] > b["rend_max"]],
        ["sin viajes", "sin lectura creíble (nivel 1)", f"km odómetro vs viajes fuera de ±{TOL_CERT:.0%}",
         "rendimiento bajo el mínimo", "rendimiento sobre el máximo de su generación"], "certificado")
    b["certificado"] = b["motivo"] == "certificado"
    b["mes"] = pd.to_datetime(b["t_fin"]).dt.to_period("M")
    cols_b = ["equipo", "bloque_u", "t_ini", "t_fin", "litros", "km_viajes", "km_c", "km_v", "centro", "km_odo", "calidad",
              "sis_ini", "sis_fin", "gps_ini", "gps_fin", "km_sis", "km_gps", "grado_lectura", "fuente_km_cert", "km_cert",
              "razon_cert", "rend_cert", "rend_max", "motivo", "certificado"]
    b[cols_b].to_csv(salida / "bloques_certificados.csv", index=False)
    L = b["litros"].sum()
    t1 = b.groupby("grado_lectura")["litros"].sum().div(L).mul(100).round(1)
    t2 = b.groupby("motivo")["litros"].sum().div(L).mul(100).round(1).sort_values(ascending=False)
    t3 = b.groupby(b["mes"].dt.year).apply(lambda x: 100 * x.loc[x["certificado"], "litros"].sum() / x["litros"].sum()).round(1)
    p("Nivel 1 — lectura del odómetro (% de litros): A doble fuente · B una fuente · C ninguna", t1.to_frame("% litros").T,
      f"  bloques con ambas fuentes que no coinciden y se resolvieron por km de viajes: "
      f"{int((ok_s & ok_g & ~acuerdo & (b['grado_lectura'] == 'B')).sum()):,}",
      "", "Nivel 2 — resultado del bloque (% de litros):", t2.to_frame("% litros"),
      "", "Litros en bloques certificados, por año (%):", t3.to_frame("% certificado").T)

    # ---------------------------------------------------------------- nivel 3: nota mensual del camión
    bc = b[b["certificado"]]
    m = b.groupby(["equipo", "mes"]).agg(litros_mes=("litros", "sum"), bloques=("litros", "size"))
    m = m.join(bc.groupby(["equipo", "mes"]).agg(litros_cert=("litros", "sum"), km_cert=("km_viajes", "sum"),
                                                 bloques_cert=("litros", "size"))).fillna(0).reset_index()
    m["pct_litros_cert"] = m["litros_cert"] / m["litros_mes"]
    m["rendimiento"] = m["km_cert"] / m["litros_cert"].where(m["litros_cert"] > 0)
    m["publicado"] = (m["km_cert"] >= N3_KM_MIN) & (m["pct_litros_cert"] >= N3_LITROS_CERT_MIN)
    m = m.sort_values(["equipo", "mes"])
    med = []
    for _, d in m.groupby("equipo", sort=False):
        r = d["rendimiento"].where(d["publicado"])
        meses = d["mes"]
        med += [r[(meses > M - N4_MESES - 1) & (meses < M)].median() for M in meses]
    m["mediana_12m"] = med
    m["desvio"] = m["rendimiento"] / m["mediana_12m"] - 1
    m["alerta"] = m["publicado"] & (m["desvio"].abs() > N3_ALERTA)
    m.assign(mes=m["mes"].astype(str)).round(4).to_csv(salida / "rendimiento_camion_mes.csv", index=False)
    pc = m.groupby("equipo").agg(meses=("mes", "size"), publicados=("publicado", "sum"))
    p("", "Nivel 3 — nota mensual del camión:",
      f"  camión-meses con nota publicada: {100 * m['publicado'].mean():.1f} % · con alerta (> ±{N3_ALERTA:.0%} de su mediana): "
      f"{100 * m['alerta'].sum() / max(m['publicado'].sum(), 1):.1f} % de los publicados",
      f"  camión típico: {pc['publicados'].median():.0f} meses publicados de {pc['meses'].median():.0f} · "
      f"camiones con < 25 % de meses publicados: {int((pc['publicados'] < 0.25 * pc['meses']).sum())} de {len(pc)}")

    # ---------------------------------------------------------------- nivel 4: ficha de consumo por camión y mes
    res = res.copy()
    res["mes"] = pd.to_datetime(res["inicio"]).dt.to_period("M")
    meses_cam = res.dropna(subset=["mes"]).groupby("camion")["mes"].unique()
    bc = bc.assign(modelo=bc["equipo"].map(eqm["modelo"]).fillna("?"))
    X_all, y_all = bc[["km_c", "km_v"]].values.astype(float), bc["litros"].values.astype(float)
    a_g, b_g = _ajuste(X_all, y_all) if len(bc) >= 50 else (np.nan, np.nan)
    cache_mod, filas = {}, []
    for cam, meses in meses_cam.items():
        gq = gen.get(cam, "moderno")
        mod = eqm.at[cam, "modelo"] if cam in eqm.index else "?"
        dc = bc[bc["equipo"] == cam]
        for M in sorted(meses):
            ven = (dc["mes"] > M - N4_MESES) & (dc["mes"] <= M)
            fuente, a, bb, n = None, np.nan, np.nan, int(ven.sum())
            if n >= N4_BLOQUES_MIN:
                a, bb = _ajuste(dc.loc[ven, ["km_c", "km_v"]].values.astype(float), dc.loc[ven, "litros"].values.astype(float))
                fuente = "camion" if _ficha_valida(a, bb, gq) else None
            if fuente is None:
                k = (mod, M)
                if k not in cache_mod:
                    dm = bc[(bc["modelo"] == mod) & (bc["mes"] > M - N4_MESES) & (bc["mes"] <= M)]
                    cache_mod[k] = _ajuste(dm[["km_c", "km_v"]].values.astype(float), dm["litros"].values.astype(float)) \
                        if len(dm) >= N4_BLOQUES_MIN else (np.nan, np.nan)
                a, bb = cache_mod[k]
                fuente = "modelo" if _ficha_valida(a, bb, gq) else None
            if fuente is None:
                k = ("_flota", M)
                if k not in cache_mod:
                    dm = bc[(bc["mes"] > M - N4_MESES) & (bc["mes"] <= M)]
                    cache_mod[k] = _ajuste(dm[["km_c", "km_v"]].values.astype(float), dm["litros"].values.astype(float)) \
                        if len(dm) >= 50 else (np.nan, np.nan)
                a, bb = cache_mod[k]
                fuente = "flota 12 meses" if _ficha_valida(a, bb, gq) else "flota total"
                if fuente == "flota total":
                    a, bb = a_g, b_g
            filas.append(dict(camion=cam, mes=M, generacion=gq, bloques_cert_12m=n, fuente=fuente,
                              L100_cargado=100 * a, L100_vacio=100 * bb))
    fi = pd.DataFrame(filas)
    fi["kmL_cargado"], fi["kmL_vacio"] = 100 / fi["L100_cargado"], 100 / fi["L100_vacio"]
    fi.assign(mes=fi["mes"].astype(str)).round(3).to_csv(salida / "ficha_consumo_camion_mes.csv", index=False)
    p("", f"Nivel 4 — ficha de consumo (12 meses, ≥ {N4_BLOQUES_MIN} bloques certificados), origen por camión-mes (%):",
      fi["fuente"].value_counts(normalize=True).mul(100).round(1).to_frame("%").T,
      f"  flota total (todo el período, bloques certificados): cargado {100 * a_g:.1f} · vacío {100 * b_g:.1f} L/100 km")

    # ---------------------------------------------------------------- viajes: estado y litros para el margen
    z = pzb[["id", "equipo", "bloque_u", "parte"]].dropna(subset=["bloque_u"]).copy()
    z["bloque_u"] = z["bloque_u"].astype(int)
    z = z.merge(b[["equipo", "bloque_u", "certificado"]].rename(columns={"certificado": "cert"}), on=["equipo", "bloque_u"], how="left")
    z["cert"] = z["cert"].fillna(False).astype(bool)
    share = z[z["cert"]].groupby("id")["parte"].sum()
    # motivo del bloque principal de cada viaje (el que tiene la mayor parte del viaje), para explicar el ajuste
    zm = z.merge(b[["equipo", "bloque_u", "motivo"]], on=["equipo", "bloque_u"], how="left")
    mot = zm.sort_values("parte").drop_duplicates("id", keep="last").set_index("id")["motivo"]
    v = res.merge(fi[["camion", "mes", "fuente", "L100_cargado", "L100_vacio", "generacion"]], on=["camion", "mes"], how="left")
    v["fraccion_certificada"] = v["id"].map(share).fillna(0.0).clip(upper=1.0)
    local = v["km"] <= KM_LOCAL
    v["l100_teo"] = np.where(v["vacio_fis"].astype(bool), v["L100_vacio"], v["L100_cargado"])
    if l100_local is not None and np.isfinite(l100_local):
        v.loc[local, "l100_teo"] = l100_local
    v["litros_teo"] = v["km"] * v["l100_teo"] / 100
    v["razon"] = v["litros"] / v["litros_teo"].where(v["litros_teo"] > 0)
    v["kmL"] = v["km"] / v["litros"].where(v["litros"] > 0)
    gmax = v["generacion"].fillna("moderno").map(REND_MAX)
    v["kmL_max"] = np.where(v["vacio_fis"].astype(bool), gmax.str[1], gmax.str[0])
    rend_ok = local | ((v["kmL"] >= REND_MIN) & (v["kmL"] <= v["kmL_max"]))
    todo_cert = v["fraccion_certificada"] >= 0.999
    en_banda = (v["razon"] - 1).abs() <= BANDA_VIAJE
    v["estado_litros"] = np.select([todo_cert & en_banda & rend_ok, todo_cert], ["real certificado", "real con alerta"], "teórico")
    v["litros_margen"] = np.where(v["estado_litros"] == "teórico", v["litros_teo"], v["litros"])
    v["ajuste_litros"] = v["litros_margen"] - v["litros"]
    v["motivo_bloque"] = v["id"].map(mot).fillna("sin bloque (estimado)")
    out = ["id", "id_orig", "camion", "inicio", "km", "vacio_fis", "metodo", "litros", "fraccion_certificada", "fuente",
           "l100_teo", "litros_teo", "razon", "kmL", "kmL_max", "motivo_bloque", "estado_litros", "litros_margen",
           "ajuste_litros"]
    v[out].assign(inicio=v["inicio"].astype(str)).rename(columns={"fuente": "fuente_ficha", "litros": "litros_asignados"}).round(4) \
        .to_csv(salida / "litros_viaje_certificado.csv", index=False)

    t = v.groupby("estado_litros").agg(viajes=("id", "size"), km=("km", "sum"), litros=("litros", "sum"), litros_margen=("litros_margen", "sum"))
    t["viajes_%"], t["km_%"] = (100 * t["viajes"] / t["viajes"].sum()).round(1), (100 * t["km"] / t["km"].sum()).round(1)
    an = v.assign(anio=pd.to_datetime(v["inicio"]).dt.year).groupby("anio").agg(
        litros_asignados=("litros", "sum"), litros_margen=("litros_margen", "sum"),
        pct_km_real_cert=("km", lambda s: 100 * s[v.loc[s.index, "estado_litros"] == "real certificado"].sum() / s.sum()))
    an["ajuste_%"] = 100 * (an["litros_margen"] / an["litros_asignados"] - 1)
    p("", "Viajes — estado de los litros que usa el margen:", t,
      "", "Por año (el ajuste = litros teóricos que reemplazan a los reales; se informa como 'litros no asignables'):", an.round(1),
      "", "Litros no asignables (teórico − asignado) según el problema del bloque del viaje:",
      v[v["estado_litros"] == "teórico"].groupby("motivo_bloque").agg(
          viajes=("id", "size"), litros_asignados=("litros", "sum"), litros_teoricos=("litros_teo", "sum"),
          ajuste=("ajuste_litros", "sum")).assign(pct_ajuste=lambda d: (100 * d["ajuste"] / d["ajuste"].sum()).round(1))
       .sort_values("ajuste", ascending=False),
      "", f"Rendimiento del viaje en el margen fuera de {REND_MIN}–máximo: "
          f"{int((~local & ((v['km'] / v['litros_margen'] < REND_MIN) | (v['km'] / v['litros_margen'] > v['kmL_max']))).sum()):,} viajes")
    return b, m, fi, v
