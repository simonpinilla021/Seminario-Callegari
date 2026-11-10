#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Área TALLER del MVP Data Warehouse: mantención y control de flota (indicadores de Simón + catálogo OE1, proceso 7).
Diseño: Notas/01 Claude - Contexto de diseño/Indicadores/Modelo taller y bodega - columnas por indicador y riesgos de datos.md

Escribe solo en el esquema `taller` (regla del MVP) y agrega su fila a comun.registro_carga. taller.dim_equipo es una vista de maestro compartido y soporte/actividad de taller: otras áreas la pueden leer por equipo_id, igual que peajes.dim_tramo. De otras áreas solo LEE, en vistas: combustible.fact_bloque_combustible y
combustible.ref_regresion_camion (rendimiento y control de combustible), si están cargadas.

Tablas
  taller.dim_equipo                un equipo (a_asset_id) con marca, modelo, año, rendimiento de ficha, estado, si es camión
                                   (centro de costo de parametros_taller_manual.csv) y su período en flota (primera y
                                   última actividad). Fila -1 = sin equipo.
  taller.dim_tipo_intervencion     combinación tipo de documento (OT · ST · SN) × preventiva/correctiva × área × medición
  taller.dim_pauta                 pauta de mantención (sit_workstandard_id) con su intervalo, si está en
                                   pauta_intervalo_manual.csv (hasta que TI dé permiso a sit_workstandard / sit_maintenancedetail)
  taller.intervalo_pauta_equipo    intervalo de una pauta para un equipo en particular (filas del CSV con a_asset_id)
  taller.dim_repuesto              un producto de bodega con su categoría y si es combustible o gasto de mantención
                                   (categoria_repuesto_manual.csv)
  taller.fact_orden_taller         una orden (sit_workorder: OT, ST y SN) con apertura, cierre, cierre imputado, pauta
                                   realizada y siguiente, odómetro y costo de repuestos con OT
  taller.fact_consumo_repuesto     una línea de consumo de bodega (prodconsumo_v), con el equipo resuelto por la OT o por la
                                   unidad de negocio (via_imputacion)
  taller.fact_estado_diario_equipo un tracto × un día en flota: horas y día en taller, con viaje, trabajado, km e ingreso
  taller.parametro_taller          parámetros usados en la carga (los lee las vistas)

Vistas (componentes sumables; los indicadores se calculan como Σ numerador / Σ denominador)
  v_flota_mensual          I2 disponibilidad · I3 días trabajados · N1 costo de oportunidad   (equipo × mes)
  v_ot_mantencion          I4 tiempo en taller por tipo                                      (una OT de mantención)
  v_costo_mantencion_mensual  I5 proporción preventiva (con OT) · I6a / I6b costo por km     (equipo × mes)
  v_cumplimiento_preventivo   I8 cumplimiento preventivo (necesita intervalos de pauta)      (una OT preventiva)
  v_reincidencia_correctiva   N2 días entre correctivas                                      (una OT correctiva)
  v_ot_abiertas               N3 OT abiertas y antigüedad
  v_consumo_km_mensual        N4 consumo de repuestos por km                                 (equipo × mes × categoría)
  v_rendimiento_combustible   I1 rendimiento · I7 control de combustible (si está cargado combustible)  (un bloque)

Reglas (decididas o verificadas, ver la nota de diseño):
  - Día en taller: horas de OT de mantención (1000385, sin VO ni DR) ≥ horas_taller_dia. Los intervalos de OT del mismo
    equipo se unen antes de repartir por día (no se cuentan dos veces). Preventivas cuentan como no disponible (24-10).
  - Cierre de la OT = COALESCE(enddate, updated); flag_cierre_imputado = 'S' cuando enddate es nulo.
  - Día con viaje: entre created de la línea del viaje y enddatetravel, con el tracto como sit_mainteam_id. Si
    enddatetravel es la fecha de relleno (2020-06-24), es anterior al inicio o el viaje dura más de tope_dias_viaje, la
    duración se estima con km del tramo ÷ km_por_dia (viaje_estimado = 1).
  - Costo de mantención = consumos de bodega sin combustible ni gasto no técnico (categoria_repuesto_manual.csv).
    El equipo sale de la OT; si no hay OT, de la unidad de negocio cuando es de un solo equipo (flota propia).

Uso (en la carpeta MVP Data Warehouse/taller, o lo llama construir_todo.py):
  python construir_dw_taller.py
Fuentes (config.ini, sección [fuentes]):
  erp    = carpeta con travelveh_v, travelvta_v, traveltramo_v, prodconsumo_v, prodmaster_v (y guiascombust_v)
  tablas = carpeta con sit_workorder.parquet (tabla completa del ERP; por defecto fuentes/erp_tablas_2026-10-19)
"""
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from comun import db  # noqa: E402

VERSION = "2026-10-30-taller-importes-y-nulos-v2"
ESQUEMAS = ("taller", "comun")
PARAM_MANUAL = AQUI / "parametros_taller_manual.csv"
CATEG_MANUAL = AQUI / "categoria_repuesto_manual.csv"
PAUTA_MANUAL = AQUI / "pauta_intervalo_manual.csv"
COSTOS_PARAM = AQUI.parent / "costos" / "parametros_costo_manual.csv"
DOC_OT, DOC_ST, DOC_SN = 1000385, 1000386, 1000395
TIPO_DOC = {DOC_OT: "OT", DOC_ST: "ST", DOC_SN: "SN"}

PARAM_DEFECTO = {
    "centro_costo_camiones": ("1000554", "c_activity_id", "Camiones (Simón). Da 104 tractos del grupo TC; ver nota de diseño"),
    "horas_taller_dia": ("8", "horas", "Un día cuenta en taller si las horas de OT de ese día suman esto o más"),
    "tope_dias_viaje": ("10", "días", "Viaje más largo que esto: la duración se estima por km"),
    "fecha_relleno_viaje": ("2020-06-24", "fecha", "enddatetravel de relleno en viajes antiguos (20.641 viajes)"),
    "km_por_dia": ("720.5", "km/día", "Si existe costos/parametros_costo_manual.csv se usa el vigente de allí"),
    "fecha_inicio_estado": ("2019-01-01", "fecha", "El estado diario parte aquí (las OT empiezan en 2019)"),
    "rendimiento_min": ("1", "km/L", "Rango válido de rendimiento del bloque (Simón 1–5; R1 usa 1,5–5)"),
    "rendimiento_max": ("5", "km/L", "Rango válido de rendimiento del bloque"),
    "tolerancia_preventiva_pct": ("10", "%", "La preventiva está a tiempo si se hizo antes de intervalo × (1 + tolerancia)"),
    "ingreso_dia_referencia": ("800000", "CLP/día", "Ingreso no percibido por día en taller (propuesta SIA); solo referencia"),
}

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS comun;
CREATE SCHEMA IF NOT EXISTS taller;
CREATE TABLE IF NOT EXISTS comun.dim_fecha (
  sk_fecha INTEGER PRIMARY KEY, fecha DATE UNIQUE, anio INTEGER, mes INTEGER, dia INTEGER, dia_semana INTEGER,
  nombre_dia TEXT, es_fin_de_semana TEXT);
CREATE TABLE IF NOT EXISTS comun.registro_carga (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, area TEXT, fecha_carga TIMESTAMP, version_script TEXT,
  fecha_cambio TEXT, detalle TEXT);
DROP VIEW IF EXISTS taller.v_rendimiento_combustible, taller.v_consumo_km_mensual, taller.v_ot_abiertas,
  taller.v_reincidencia_correctiva, taller.v_cumplimiento_preventivo, taller.v_costo_mantencion_mensual,
  taller.v_ot_mantencion, taller.v_flota_mensual CASCADE;
DROP TABLE IF EXISTS taller.fact_estado_diario_equipo CASCADE;
DROP TABLE IF EXISTS taller.fact_consumo_repuesto CASCADE;
DROP TABLE IF EXISTS taller.fact_orden_taller CASCADE;
DROP TABLE IF EXISTS taller.dim_tipo_intervencion CASCADE;
DROP TABLE IF EXISTS taller.intervalo_pauta_equipo CASCADE;
DROP TABLE IF EXISTS taller.dim_pauta CASCADE;
DROP TABLE IF EXISTS taller.dim_repuesto CASCADE;
DROP TABLE IF EXISTS taller.parametro_taller CASCADE;

CREATE TABLE taller.parametro_taller (parametro TEXT PRIMARY KEY, valor TEXT, unidad TEXT, nota TEXT);
CREATE TABLE taller.dim_tipo_intervencion (
  tipo_id INTEGER PRIMARY KEY,
  c_doctype_id BIGINT, tipo_documento TEXT,   -- OT · ST (solicitud) · SN (sanitización)
  tipo_mant TEXT,                          -- PREVENTIVA (tiene pauta) · CORRECTIVA · NO APLICA (sanitización)
  area_taller TEXT,                        -- workarea: TM, TP, TSP, TE, TR, TG, SN (nombres por confirmar con ad_ref_list)
  tipo_medicion TEXT,                      -- type: KM · HM (horómetro) · DAY
  es_sanitizacion TEXT);
CREATE TABLE taller.dim_pauta (
  pauta_id BIGINT PRIMARY KEY,             -- sit_workstandard_id; -1 = sin pauta
  nombre TEXT,
  intervalo DOUBLE PRECISION,              -- cada cuánto toca (por defecto, para todos los equipos)
  unidad TEXT,                             -- KM · HORAS · DIAS
  fuente TEXT);
CREATE TABLE taller.intervalo_pauta_equipo (
  equipo_id BIGINT, pauta_id BIGINT, intervalo DOUBLE PRECISION, unidad TEXT, fuente TEXT,
  PRIMARY KEY (equipo_id, pauta_id));
CREATE TABLE taller.dim_repuesto (
  producto_id BIGINT PRIMARY KEY,          -- m_product_id; -1 = sin producto
  codigo TEXT, descripcion TEXT, categoria TEXT, unidad TEXT, costo_estandar DOUBLE PRECISION,
  es_combustible TEXT, es_mantencion TEXT);
CREATE TABLE taller.fact_orden_taller (
  sit_workorder_id BIGINT PRIMARY KEY,     -- llave hacia el consumo
  nro_ot TEXT,                             -- documentno (para mostrar)
  tipo_id INTEGER REFERENCES taller.dim_tipo_intervencion,
  equipo_id BIGINT,                        -- taller.dim_equipo
  pauta_id BIGINT,                         -- pauta realizada (sit_workstandard_id); -1 = correctiva
  pauta_siguiente_id BIGINT,               -- sit_nextstandard_id
  seqno INTEGER,
  solicitud_id BIGINT, nro_solicitud TEXT, -- ST de origen (jobapplication_id)
  sk_fecha_apertura INTEGER, sk_fecha_cierre INTEGER,
  fecha_hora_apertura TIMESTAMP,           -- created
  fecha_hora_cierre TIMESTAMP,             -- COALESCE(enddate, updated)
  flag_cierre_imputado TEXT,               -- S: enddate nulo, se usó updated
  docstatus TEXT,                          -- CO, CL, AP, IP, DR, VO, IN
  horas_ciclo DOUBLE PRECISION,
  odometro DOUBLE PRECISION,               -- odometer, o el GPS si está vacío
  horometro DOUBLE PRECISION,              -- horometer, o el GPS si está vacío
  prioridad TEXT,
  costo_repuestos DOUBLE PRECISION,        -- Σ consumos de mantención con esta OT (sin combustible)
  lineas_consumo INTEGER);
CREATE TABLE taller.fact_consumo_repuesto (
  consumo_id BIGINT PRIMARY KEY,           -- correlativo (la vista no trae m_inventoryline_id)
  nro_documento TEXT, sk_fecha INTEGER, fecha DATE,
  producto_id BIGINT, bodega_id BIGINT, ubicacion_id BIGINT, centro_costo_id BIGINT, unidad_negocio_id BIGINT,
  sit_workorder_id BIGINT,                 -- NULL en ~82 %
  equipo_id BIGINT,                        -- -1 si no se puede resolver
  via_imputacion TEXT,                     -- OT · UN · NINGUNA
  cantidad DOUBLE PRECISION, costo DOUBLE PRECISION,
  odometro DOUBLE PRECISION);
CREATE TABLE taller.fact_estado_diario_equipo (
  equipo_id BIGINT, sk_fecha INTEGER,
  en_flota SMALLINT,                       -- 1 (la fila existe solo entre fecha_alta y fecha_baja)
  horas_taller DOUBLE PRECISION,           -- intervalos de OT unidos, tope 24
  horas_preventiva DOUBLE PRECISION, horas_correctiva DOUBLE PRECISION,
  horas_cierre_real DOUBLE PRECISION,      -- solo OT con enddate real (prueba de sensibilidad)
  en_taller SMALLINT,                      -- horas_taller >= horas_taller_dia
  en_taller_cierre_real SMALLINT,
  tipo_taller TEXT,                        -- preventiva · correctiva (la de más horas) en días en taller
  con_viaje SMALLINT, viaje_estimado SMALLINT,
  dia_disponible SMALLINT,                 -- 1 - en_taller
  dia_trabajado SMALLINT,                  -- con viaje y no en taller
  viajes_iniciados INTEGER, km_viajes DOUBLE PRECISION, ingreso_viajes DOUBLE PRECISION,
  PRIMARY KEY (equipo_id, sk_fecha));
CREATE INDEX ix_ot_equipo ON taller.fact_orden_taller (equipo_id, fecha_hora_apertura);
CREATE INDEX ix_consumo_equipo ON taller.fact_consumo_repuesto (equipo_id, sk_fecha);
CREATE INDEX ix_consumo_ot ON taller.fact_consumo_repuesto (sit_workorder_id);
CREATE INDEX ix_estado_fecha ON taller.fact_estado_diario_equipo (sk_fecha);
"""


# ------------------------------------------------------------------------------------------------ utilidades
def ident(s):
    return pd.to_numeric(s, errors="coerce").round().astype("Int64")


def num(s):
    return pd.to_numeric(s, errors="coerce")


def sn(s):
    return s.astype(str).str.strip().str.upper().isin(["Y", "S", "SI", "TRUE", "1"]).map({True: "S", False: "N"})


def fecha_sk(s):
    return pd.to_datetime(s, errors="coerce").dt.strftime("%Y%m%d").astype("Int64")


def leer_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return pd.DataFrame(list(csv.DictReader(fh)))


def carpeta(clave, defecto):
    try:
        return db.ruta_config("fuentes", clave)
    except KeyError:
        return (db.MVP / defecto).resolve()


def buscar(carp, nombres, obligatorio=True):
    for n in nombres:
        if (carp / n).exists():
            return carp / n
    if obligatorio:
        sys.exit(f"No encuentro {nombres[0]} en {carp}. Ver README (Área taller: fuentes).")
    return None


def leer(ruta, cols):
    """Lee solo las columnas pedidas que existan (en minúsculas); las que faltan quedan en NaN con aviso."""
    df = pd.read_parquet(ruta)
    df.columns = [c.lower() for c in df.columns]
    faltan = [c for c in cols if c not in df.columns]
    if faltan:
        print(f"   aviso: {ruta.name} no trae {faltan}")
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in faltan:
        df[c] = np.nan
    return df


def sin_zona(s):
    s = pd.to_datetime(s, errors="coerce")
    try:
        return s.dt.tz_localize(None)
    except (TypeError, AttributeError):
        return s


# ------------------------------------------------------------------------------------------------ parámetros
def leer_parametros():
    par = {k: v[0] for k, v in PARAM_DEFECTO.items()}
    if PARAM_MANUAL.exists():
        for r in leer_csv(PARAM_MANUAL).itertuples():
            if str(r.parametro).strip() in par and str(r.valor).strip():
                par[str(r.parametro).strip()] = str(r.valor).strip()
    else:
        with open(PARAM_MANUAL, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["parametro", "valor", "unidad", "nota"])
            for k, (v, u, n) in PARAM_DEFECTO.items():
                w.writerow([k, v, u, n])
    origen_km = "parametros_taller_manual.csv"
    if COSTOS_PARAM.exists():                     # mismo km_por_dia que costos (vigente hoy, método tarificador)
        c = leer_csv(COSTOS_PARAM)
        c = c[(c["parametro"] == "km_por_dia") & (c["metodo"] == "tarificador")]
        hoy = date.today().isoformat()
        c = c[(c["vigente_desde"] <= hoy) & (c["vigente_hasta"] > hoy)]
        if len(c):
            par["km_por_dia"] = str(c.iloc[0]["valor"])
            origen_km = "costos/parametros_costo_manual.csv"
    par["_origen_km_por_dia"] = origen_km
    return par


def leer_categorias():
    if not CATEG_MANUAL.exists():
        sys.exit(f"Falta {CATEG_MANUAL.name} (clasificación de categorías de producto).")
    c = leer_csv(CATEG_MANUAL)
    c["categoria"] = c["categoria"].str.strip().str.upper()
    return {r.categoria: (r.es_combustible.strip().upper(), r.es_mantencion.strip().upper()) for r in c.itertuples()}


def leer_pautas_manual():
    if not PAUTA_MANUAL.exists():
        PAUTA_MANUAL.write_text("sit_workstandard_id,a_asset_id,nombre,intervalo,unidad,fuente,nota\n", encoding="utf-8-sig")
    p = leer_csv(PAUTA_MANUAL)
    if p.empty:
        return p
    p["pauta_id"] = ident(p["sit_workstandard_id"])
    p["equipo_id"] = ident(p.get("a_asset_id", pd.Series(dtype=str)).replace("", np.nan))
    p["intervalo"] = num(p["intervalo"])
    p["unidad"] = p["unidad"].str.strip().str.upper()
    return p[p["pauta_id"].notna()]


# ------------------------------------------------------------------------------------------------ lectura
def leer_equipos(ruta_veh):
    v = leer(ruta_veh, ["a_asset_id", "codigo", "patente", "vehiculo", "actualstate", "categoryequipment", "marca",
                        "modelo", "manufacturedyear", "isfleet", "isowned", "performancewithload", "grupoemp",
                        "c_activity_id", "sit_businessunit_id"])
    v["equipo_id"] = ident(v["a_asset_id"])
    return v[v["equipo_id"].notna()].drop_duplicates("equipo_id")


def leer_ot(ruta_wo):
    w = leer(ruta_wo, ["sit_workorder_id", "ad_client_id", "c_doctype_id", "documentno", "docstatus", "created", "updated",
                       "enddate", "a_asset_id", "jobapplication_id", "sit_workstandard_id", "sit_nextstandard_id", "seqno",
                       "odometer", "odometrogps", "horometer", "horometrogps", "priority", "workarea", "type",
                       "categoryequipment"])
    w = w[ident(w["c_doctype_id"]).isin(list(TIPO_DOC))].copy()
    w["sit_workorder_id"] = ident(w["sit_workorder_id"])
    w["c_doctype_id"] = ident(w["c_doctype_id"])
    w["tipo_documento"] = w["c_doctype_id"].map(TIPO_DOC)
    w["apertura"] = sin_zona(w["created"])
    w["cierre"] = sin_zona(w["enddate"]).fillna(sin_zona(w["updated"]))
    w["flag_cierre_imputado"] = np.where(w["enddate"].isna(), "S", "N")
    w["pauta_id"] = ident(w["sit_workstandard_id"]).fillna(0)
    w["pauta_id"] = w["pauta_id"].where(w["pauta_id"] > 0, -1)
    w["tipo_mant"] = np.where(w["tipo_documento"] == "SN", "NO APLICA",
                              np.where(w["pauta_id"] > 0, "PREVENTIVA", "CORRECTIVA"))
    w["area_taller"] = w["workarea"].astype("string").str.strip().fillna("SIN AREA")
    w["tipo_medicion"] = w["type"].astype("string").str.strip().str.upper().fillna("KM")
    w["equipo_id"] = ident(w["a_asset_id"]).fillna(-1)
    w["odometro"] = num(w["odometer"]).where(num(w["odometer"]) > 0, num(w["odometrogps"]).where(num(w["odometrogps"]) > 0))
    w["horometro"] = num(w["horometer"]).where(num(w["horometer"]) > 0, num(w["horometrogps"]).where(num(w["horometrogps"]) > 0))
    return w


def leer_consumos(ruta_cons, ruta_master, categorias):
    c = leer(ruta_cons, ["documentno", "movementdate", "m_warehouse_id", "m_locator_id", "m_product_id", "qtyinternaluse",
                         "odometer", "odometrogps", "c_activity_id", "sit_businessunit_id", "costo", "sit_workorder_id"])
    # Los totales por OT se agrupan antes de crear fact_consumo_repuesto.
    # Normalizar importes/cantidades al leer, evitando concatenación de NUMERIC textual.
    for campo in ('costo','qtyinternaluse'):
        try:
            c[campo]=pd.to_numeric(c[campo],errors='raise')
        except (ValueError,TypeError) as error:
            raise ValueError(f'prodconsumo.{campo} contiene un valor no numérico; revisar fuente') from error
    n0 = len(c)
    c = c.drop_duplicates()                                     # 29 filas repetidas exactas en la vista (sin id de línea)
    dup = n0 - len(c)
    c["fecha"] = sin_zona(c["movementdate"]).dt.normalize()
    c = c.sort_values(["fecha", "documentno", "m_product_id"], kind="stable").reset_index(drop=True)
    m = leer(ruta_master, ["m_product_id", "codigo", "description", "categoria", "umedida", "coststandar"])
    m["producto_id"] = ident(m["m_product_id"])
    m = m[m["producto_id"].notna()].drop_duplicates("producto_id")
    m["categoria"] = m["categoria"].astype("string").str.strip().str.upper().fillna("SIN CATEGORIA")
    sin_regla = sorted(set(m["categoria"]) - set(categorias))
    if sin_regla:
        print(f"   aviso: categorías sin fila en {CATEG_MANUAL.name} (se tratan como mantención, no combustible): {sin_regla}")
    m["es_combustible"] = m["categoria"].map(lambda k: categorias.get(k, ("N", "S"))[0])
    m["es_mantencion"] = m["categoria"].map(lambda k: categorias.get(k, ("N", "S"))[1])
    rep = pd.DataFrame({"producto_id": m["producto_id"], "codigo": m["codigo"].astype("string"),
                        "descripcion": m["description"].astype("string"), "categoria": m["categoria"],
                        "unidad": m["umedida"].astype("string"), "costo_estandar": num(m["coststandar"]),
                        "es_combustible": m["es_combustible"], "es_mantencion": m["es_mantencion"]})
    faltan = sorted(set(ident(c["m_product_id"]).dropna()) - set(rep["producto_id"]))
    extra = [{"producto_id": -1, "descripcion": "sin producto", "categoria": "SIN CATEGORIA", "es_combustible": "N",
              "es_mantencion": "N"}]
    extra += [{"producto_id": p, "descripcion": "no está en prodmaster_v", "categoria": "SIN CATEGORIA",
               "es_combustible": "N", "es_mantencion": "S"} for p in faltan]
    rep = pd.concat([rep, pd.DataFrame(extra)], ignore_index=True)
    return c, rep, dup


def leer_viajes(ruta_vta, ruta_tramo, par):
    v = leer(ruta_vta, ["c_orderline_id", "created", "datepromised", "enddatetravel", "sit_mainteam_id", "sit_stretch_id",
                        "sitdocstatus", "estimado"])
    v = v.drop_duplicates("c_orderline_id")                     # travelvta repite el viaje por factura
    v = v[v["sitdocstatus"].astype(str).str.upper() != "ER"]
    tr = leer(ruta_tramo, ["sit_stretch_id", "kilometers"]).drop_duplicates("sit_stretch_id")
    v = v.merge(tr, on="sit_stretch_id", how="left")
    v["equipo_id"] = ident(v["sit_mainteam_id"])
    v = v[v["equipo_id"].notna()].copy()
    v["inicio"] = sin_zona(v["created"])
    v["fin_erp"] = sin_zona(v["enddatetravel"])
    v["km"] = num(v["kilometers"])
    v["ingreso"] = num(v["estimado"])
    relleno = pd.Timestamp(par["fecha_relleno_viaje"])
    tope = float(par["tope_dias_viaje"])
    dur = (v["fin_erp"] - v["inicio"]).dt.total_seconds() / 86400
    valido = v["fin_erp"].notna() & (v["fin_erp"].dt.normalize() != relleno) & (dur >= 0) & (dur <= tope)
    dias_est = (v["km"] / float(par["km_por_dia"])).fillna(1.0).clip(lower=0.1, upper=tope)
    v["fin"] = v["fin_erp"].where(valido, v["inicio"] + pd.to_timedelta(dias_est, unit="D"))
    v["viaje_estimado"] = np.where(valido, 0, 1)
    return v[v["inicio"].notna()]


def leer_cargas(ruta_carga):
    if ruta_carga is None:
        return pd.DataFrame(columns=["equipo_id", "fecha"])
    g = leer(ruta_carga, ["a_asset_id", "datetrx"])
    return pd.DataFrame({"equipo_id": ident(g["a_asset_id"]), "fecha": sin_zona(g["datetrx"])}).dropna()


# ------------------------------------------------------------------------------------------------ transformación
def mapa_un_equipo(veh):
    """UN → equipo cuando la UN tiene un solo equipo (flota propia). La UN con varios equipos es la de fleteros."""
    x = veh.dropna(subset=["sit_businessunit_id"]).copy()
    x["un"] = ident(x["sit_businessunit_id"])
    n = x.groupby("un")["equipo_id"].nunique()
    unicas = x[x["un"].isin(n[n == 1].index)]
    return dict(zip(unicas["un"], unicas["equipo_id"])), set(n[n > 1].index)


def resolver_equipo_consumo(c, ot, un_equipo):
    c["sit_workorder_id"] = ident(c["sit_workorder_id"])
    c["unidad_negocio_id"] = ident(c["sit_businessunit_id"])
    eq_ot = dict(zip(ot["sit_workorder_id"], ot["equipo_id"]))
    por_ot = c["sit_workorder_id"].map(lambda x: eq_ot.get(x) if pd.notna(x) else None)
    por_un = c["unidad_negocio_id"].map(lambda x: un_equipo.get(x) if pd.notna(x) else None)
    tiene_ot = por_ot.notna() & (por_ot != -1)
    c["via_imputacion"] = np.select([tiene_ot, por_un.notna()], ["OT", "UN"], "NINGUNA")
    c["equipo_id"] = pd.array(np.where(tiene_ot, por_ot, np.where(por_un.notna(), por_un, -1)), dtype="Int64")
    return c


def armar_tipos(ot):
    t = (ot[["c_doctype_id", "tipo_documento", "tipo_mant", "area_taller", "tipo_medicion"]]
         .drop_duplicates().sort_values(["c_doctype_id", "tipo_mant", "area_taller", "tipo_medicion"]).reset_index(drop=True))
    t["tipo_id"] = t.index + 1
    t["es_sanitizacion"] = np.where((t["tipo_documento"] == "SN") | (t["area_taller"] == "SN"), "S", "N")
    return t


def armar_dim_equipo(veh, ot, cons, viajes, cargas, par, fecha_corte):
    centro = int(float(par["centro_costo_camiones"]))
    act = pd.concat([ot[["equipo_id"]].assign(f=ot["apertura"]),
                     cons[["equipo_id"]].assign(f=cons["fecha"]),
                     viajes[["equipo_id"]].assign(f=viajes["inicio"]),
                     cargas[["equipo_id"]].assign(f=cargas["fecha"])])
    act = act[act["equipo_id"].notna() & (act["equipo_id"] != -1) & act["f"].notna()]
    rango = act.groupby("equipo_id")["f"].agg(["min", "max"])
    cat = veh["categoryequipment"].astype("string").str.strip().str.upper()
    from comun.tipos_equipo import clasificar
    tipo = clasificar(cat)
    rend = num(veh["performancewithload"])
    d = pd.DataFrame({
        "equipo_id": veh["equipo_id"], "codigo": veh["codigo"].astype("string"), "patente": veh["patente"].astype("string"),
        "nombre": veh["vehiculo"].astype("string"), "categoria": cat, "tipo_equipo": tipo,
        "grupo_empresa": veh["grupoemp"].astype("string"), "es_propio": sn(veh["isowned"]), "es_flota": sn(veh["isfleet"]),
        "es_camion": np.where(ident(veh["c_activity_id"]).eq(centro).fillna(False), "S", "N"),
        "estado_actual": veh["actualstate"].astype("string"), "marca": veh["marca"].astype("string"),
        "modelo": veh["modelo"].astype("string"), "anio_fabricacion": ident(veh["manufacturedyear"]),
        "rendimiento_ficha": rend.where(rend > 0), "centro_costo_id": ident(veh["c_activity_id"]),
        "unidad_negocio_id": ident(veh["sit_businessunit_id"]), "fuente": "travelveh_v"})
    # equipos que aparecen en OT o consumos pero no en travelveh_v (p. ej. equipos IN)
    otros = sorted(set(act["equipo_id"]) | set(ot.loc[ot["equipo_id"] != -1, "equipo_id"]))
    otros = [e for e in otros if e not in set(d["equipo_id"])]
    if otros:
        cat_ot = ot[ot["equipo_id"].isin(otros)].groupby("equipo_id")["categoryequipment"].first()
        d = pd.concat([d, pd.DataFrame({"equipo_id": otros, "categoria": [cat_ot.get(e) for e in otros],
                                        "tipo_equipo": "otro", "es_camion": "N", "fuente": "solo_ot"})], ignore_index=True)
    d = pd.concat([d, pd.DataFrame([{"equipo_id": -1, "codigo": "SIN EQUIPO", "nombre": "sin equipo", "tipo_equipo": "otro",
                                     "es_camion": "N", "fuente": "sin_equipo"}])], ignore_index=True)
    d["primera_actividad"] = d["equipo_id"].map(rango["min"]).dt.normalize()
    d["ultima_actividad"] = d["equipo_id"].map(rango["max"]).dt.normalize()
    inicio = pd.Timestamp(par["fecha_inicio_estado"])
    d["fecha_alta"] = d["primera_actividad"].where(d["primera_actividad"] > inicio, inicio)
    d.loc[d["primera_actividad"].isna(), "fecha_alta"] = pd.NaT
    activo = d["estado_actual"].astype("string").str.upper().eq("O").fillna(False)
    d["fecha_baja"] = d["ultima_actividad"].where(~activo, fecha_corte)
    d["fecha_carga"] = pd.Timestamp(db.ahora())
    for c in ("primera_actividad", "ultima_actividad", "fecha_alta", "fecha_baja"):
        d[c] = d[c].dt.date
    return d


def unir_intervalos(df):
    """df: equipo_id, ini, fin (Timestamp). Devuelve los intervalos unidos por equipo (sin traslapes)."""
    salida = []
    for eq, g in df.sort_values(["equipo_id", "ini"]).groupby("equipo_id", sort=False):
        cur_i = cur_f = None
        for i, f in zip(g["ini"], g["fin"]):
            if cur_i is None:
                cur_i, cur_f = i, f
            elif i <= cur_f:
                cur_f = max(cur_f, f)
            else:
                salida.append((eq, cur_i, cur_f))
                cur_i, cur_f = i, f
        if cur_i is not None:
            salida.append((eq, cur_i, cur_f))
    return pd.DataFrame(salida, columns=["equipo_id", "ini", "fin"])


def horas_por_dia(intervalos):
    """Reparte intervalos (ya unidos) en horas por equipo y día calendario."""
    filas = []
    un_dia = timedelta(days=1)
    for eq, i, f in intervalos.itertuples(index=False):
        d = i.normalize()
        while d < f:
            sig = d + un_dia
            h = (min(f, sig) - max(i, d)).total_seconds() / 3600
            if h > 0:
                filas.append((eq, d, h))
            d = sig
    out = pd.DataFrame(filas, columns=["equipo_id", "fecha", "horas"])
    return out.groupby(["equipo_id", "fecha"], as_index=False)["horas"].sum()


def dias_con_viaje(viajes):
    filas = []
    for eq, i, f, est in viajes[["equipo_id", "inicio", "fin", "viaje_estimado"]].itertuples(index=False):
        d, ult = i.normalize(), max(f, i).normalize()
        while d <= ult:
            filas.append((eq, d, est))
            d += timedelta(days=1)
    out = pd.DataFrame(filas, columns=["equipo_id", "fecha", "viaje_estimado"])
    return out.groupby(["equipo_id", "fecha"], as_index=False)["viaje_estimado"].max()


def armar_estado_diario(dim_eq, ot, viajes, par):
    tc = dim_eq["grupo_empresa"].astype("string").eq("TC").fillna(False)
    alcance = dim_eq[dim_eq["tipo_equipo"].eq("tracto").fillna(False) & (tc | dim_eq["es_camion"].eq("S").fillna(False))
                     & dim_eq["fecha_alta"].notna() & dim_eq["fecha_baja"].notna()]
    alcance = alcance[pd.to_datetime(alcance["fecha_alta"]) <= pd.to_datetime(alcance["fecha_baja"])]
    eqs = set(alcance["equipo_id"])
    base = [(e, d) for e, a, b in zip(alcance["equipo_id"], alcance["fecha_alta"], alcance["fecha_baja"])
            for d in pd.date_range(a, b, freq="D")]
    est = pd.DataFrame(base, columns=["equipo_id", "fecha"])

    o = ot[(ot["tipo_documento"] == "OT") & ~ot["docstatus"].isin(["VO", "DR"]) & ot["equipo_id"].isin(eqs)
           & ot["apertura"].notna() & ot["cierre"].notna()].copy()
    o["fin"] = o[["apertura", "cierre"]].max(axis=1)
    o = o.rename(columns={"apertura": "ini"})

    def horas(mask, nombre):
        h = horas_por_dia(unir_intervalos(o.loc[mask, ["equipo_id", "ini", "fin"]]))
        return h.rename(columns={"horas": nombre})

    todas = pd.Series(True, index=o.index)
    for mask, nombre in [(todas, "horas_taller"), (o["tipo_mant"] == "PREVENTIVA", "horas_preventiva"),
                         (o["tipo_mant"] == "CORRECTIVA", "horas_correctiva"),
                         (o["flag_cierre_imputado"] == "N", "horas_cierre_real")]:
        est = est.merge(horas(mask, nombre), on=["equipo_id", "fecha"], how="left")
    for c in ("horas_taller", "horas_preventiva", "horas_correctiva", "horas_cierre_real"):
        est[c] = est[c].fillna(0.0).clip(upper=24.0)
    umbral = float(par["horas_taller_dia"])
    est["en_flota"] = 1
    est["en_taller"] = (est["horas_taller"] >= umbral).astype(int)
    est["en_taller_cierre_real"] = (est["horas_cierre_real"] >= umbral).astype(int)
    est["tipo_taller"] = np.where(est["en_taller"] == 1,
                                  np.where(est["horas_preventiva"] >= est["horas_correctiva"], "preventiva", "correctiva"),
                                  None)

    v = viajes[viajes["equipo_id"].isin(eqs)]
    est = est.merge(dias_con_viaje(v), on=["equipo_id", "fecha"], how="left")
    est["con_viaje"] = est["viaje_estimado"].notna().astype(int)
    est["viaje_estimado"] = est["viaje_estimado"].fillna(0).astype(int)
    ini = v.assign(fecha=v["inicio"].dt.normalize()).groupby(["equipo_id", "fecha"]).agg(
        viajes_iniciados=("c_orderline_id", "size"), km_viajes=("km", "sum"), ingreso_viajes=("ingreso", "sum")).reset_index()
    est = est.merge(ini, on=["equipo_id", "fecha"], how="left")
    est["viajes_iniciados"] = est["viajes_iniciados"].fillna(0).astype(int)
    est["km_viajes"] = est["km_viajes"].fillna(0.0)
    est["ingreso_viajes"] = est["ingreso_viajes"].fillna(0.0)
    est["dia_disponible"] = 1 - est["en_taller"]
    est["dia_trabajado"] = ((est["con_viaje"] == 1) & (est["en_taller"] == 0)).astype(int)
    est["sk_fecha"] = est["fecha"].dt.strftime("%Y%m%d").astype(int)
    return est[["equipo_id", "sk_fecha", "en_flota", "horas_taller", "horas_preventiva", "horas_correctiva",
                "horas_cierre_real", "en_taller", "en_taller_cierre_real", "tipo_taller", "con_viaje", "viaje_estimado",
                "dia_disponible", "dia_trabajado", "viajes_iniciados", "km_viajes", "ingreso_viajes"]]


def cargar_fechas(con, hasta):
    """Delegar calendario al servicio común; no cambia fechas de hechos."""
    from comun import calendario
    calendario.publicar(con, calendario.generar(date(2018, 1, 1), date(max(hasta.year, 2030), 12, 31))[calendario.COLUMNAS])


# ------------------------------------------------------------------------------------------------ vistas
VISTAS = """
CREATE OR REPLACE FUNCTION taller.par(p TEXT) RETURNS TEXT LANGUAGE sql STABLE AS
  $$ SELECT valor FROM taller.parametro_taller WHERE parametro = p $$;

-- I2 disponibilidad · I3 días trabajados · N1 costo de oportunidad (equipo × mes; todo sumable)
CREATE VIEW taller.v_flota_mensual AS
WITH m AS (
  SELECT e.equipo_id, f.anio, f.mes,
         COUNT(*) AS dias_flota,
         SUM(e.en_taller) AS dias_taller,
         SUM(CASE WHEN e.tipo_taller = 'preventiva' THEN 1 ELSE 0 END) AS dias_taller_preventiva,
         SUM(CASE WHEN e.tipo_taller = 'correctiva' THEN 1 ELSE 0 END) AS dias_taller_correctiva,
         SUM(e.en_taller_cierre_real) AS dias_taller_cierre_real,
         SUM(e.dia_disponible) AS dias_disponibles,
         SUM(e.con_viaje) AS dias_con_viaje,
         SUM(e.dia_trabajado) AS dias_trabajados,
         SUM(e.viaje_estimado) AS dias_viaje_estimado,
         SUM(e.viajes_iniciados) AS viajes, SUM(e.km_viajes) AS km_viajes, SUM(e.ingreso_viajes) AS ingreso_viajes
  FROM taller.fact_estado_diario_equipo e JOIN comun.dim_fecha f ON f.sk_fecha = e.sk_fecha
  GROUP BY 1, 2, 3)
SELECT m.*, d.codigo, d.es_camion, d.es_propio, d.marca, d.modelo,
       m.ingreso_viajes / NULLIF(m.dias_trabajados, 0) AS ingreso_por_dia_trabajado,
       m.dias_taller * COALESCE(m.ingreso_viajes / NULLIF(m.dias_trabajados, 0),
                                SUM(m.ingreso_viajes) OVER w / NULLIF(SUM(m.dias_trabajados) OVER w, 0)) AS costo_oportunidad,
       m.dias_taller * taller.par('ingreso_dia_referencia')::numeric AS costo_oportunidad_referencia
FROM m JOIN taller.dim_equipo d ON d.equipo_id = m.equipo_id
WINDOW w AS (PARTITION BY m.anio, m.mes);

-- I4 tiempo en taller por tipo (una OT de mantención; se asigna al mes de cierre)
CREATE VIEW taller.v_ot_mantencion AS
SELECT o.sit_workorder_id, o.nro_ot, o.equipo_id, d.codigo, d.es_camion, d.tipo_equipo, t.tipo_mant, t.area_taller,
       t.tipo_medicion, o.docstatus, o.fecha_hora_apertura, o.fecha_hora_cierre, f.anio AS anio_cierre, f.mes AS mes_cierre,
       o.flag_cierre_imputado, o.horas_ciclo / 24.0 AS dias_ciclo,
       CASE WHEN o.flag_cierre_imputado = 'N' THEN o.horas_ciclo / 24.0 END AS dias_ciclo_cierre_real,
       o.costo_repuestos
FROM taller.fact_orden_taller o
JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
LEFT JOIN taller.dim_equipo d ON d.equipo_id = o.equipo_id
LEFT JOIN comun.dim_fecha f ON f.sk_fecha = o.sk_fecha_cierre
WHERE t.tipo_documento = 'OT' AND o.docstatus NOT IN ('VO', 'DR');

-- I5 proporción preventiva (solo con OT) · I6a costo con OT por km · I6b costo total sin combustible por km
CREATE VIEW taller.v_costo_mantencion_mensual AS
WITH c AS (
  SELECT c.equipo_id, f.anio, f.mes,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'PREVENTIVA' THEN c.costo ELSE 0 END) AS costo_ot_preventiva,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'CORRECTIVA' THEN c.costo ELSE 0 END) AS costo_ot_correctiva,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'NO APLICA' THEN c.costo ELSE 0 END) AS costo_ot_sanitizacion,
         SUM(CASE WHEN c.via_imputacion = 'UN' THEN c.costo ELSE 0 END) AS costo_sin_ot,
         SUM(c.costo) AS costo_mantencion_total
  FROM taller.fact_consumo_repuesto c
  JOIN taller.dim_repuesto r ON r.producto_id = c.producto_id
  JOIN comun.dim_fecha f ON f.sk_fecha = c.sk_fecha
  LEFT JOIN taller.fact_orden_taller o ON o.sit_workorder_id = c.sit_workorder_id
  LEFT JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
  WHERE r.es_combustible = 'N' AND r.es_mantencion = 'S' AND c.equipo_id <> -1
  GROUP BY 1, 2, 3),
k AS (SELECT equipo_id, anio, mes, km_viajes FROM taller.v_flota_mensual)
SELECT COALESCE(c.equipo_id, k.equipo_id) AS equipo_id, COALESCE(c.anio, k.anio) AS anio, COALESCE(c.mes, k.mes) AS mes,
       d.codigo, d.es_camion, d.tipo_equipo,
       COALESCE(c.costo_ot_preventiva, 0) AS costo_ot_preventiva, COALESCE(c.costo_ot_correctiva, 0) AS costo_ot_correctiva,
       COALESCE(c.costo_ot_sanitizacion, 0) AS costo_ot_sanitizacion, COALESCE(c.costo_sin_ot, 0) AS costo_sin_ot,
       COALESCE(c.costo_mantencion_total, 0) AS costo_mantencion_total, COALESCE(k.km_viajes, 0) AS km_viajes
FROM c FULL JOIN k ON k.equipo_id = c.equipo_id AND k.anio = c.anio AND k.mes = c.mes
JOIN taller.dim_equipo d ON d.equipo_id = COALESCE(c.equipo_id, k.equipo_id);

-- I8 cumplimiento preventivo: cada OT preventiva contra la siguiente preventiva del mismo equipo
CREATE VIEW taller.v_cumplimiento_preventivo AS
WITH p AS (
  SELECT o.*, t.tipo_medicion,
         LEAD(o.sit_workorder_id) OVER w AS ot_siguiente, LEAD(o.fecha_hora_apertura) OVER w AS fecha_siguiente,
         LEAD(o.odometro) OVER w AS odometro_siguiente, LEAD(o.horometro) OVER w AS horometro_siguiente
  FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
  WHERE t.tipo_documento = 'OT' AND t.tipo_mant = 'PREVENTIVA' AND o.docstatus NOT IN ('VO', 'DR')
  WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura)),
i AS (
  SELECT p.*, COALESCE(ie.intervalo, dp.intervalo) AS intervalo, COALESCE(ie.unidad, dp.unidad) AS unidad
  FROM p LEFT JOIN taller.intervalo_pauta_equipo ie ON ie.equipo_id = p.equipo_id AND ie.pauta_id = p.pauta_siguiente_id
         LEFT JOIN taller.dim_pauta dp ON dp.pauta_id = p.pauta_siguiente_id),
x AS (
  SELECT i.*,
         CASE i.unidad WHEN 'KM' THEN i.odometro_siguiente - i.odometro
                       WHEN 'HORAS' THEN i.horometro_siguiente - i.horometro
                       WHEN 'DIAS' THEN EXTRACT(EPOCH FROM i.fecha_siguiente - i.fecha_hora_apertura) / 86400 END AS uso_hasta_siguiente
  FROM i)
SELECT x.sit_workorder_id, x.nro_ot, x.equipo_id, x.pauta_id, x.pauta_siguiente_id, x.fecha_hora_apertura,
       x.odometro, x.horometro, x.ot_siguiente, x.fecha_siguiente, x.intervalo, x.unidad, x.uso_hasta_siguiente,
       x.uso_hasta_siguiente - x.intervalo AS desvio,
       CASE WHEN x.intervalo IS NULL THEN 'sin intervalo'
            WHEN x.ot_siguiente IS NULL THEN 'pendiente'
            WHEN x.uso_hasta_siguiente IS NULL THEN 'sin lectura'
            WHEN x.uso_hasta_siguiente <= x.intervalo * (1 + taller.par('tolerancia_preventiva_pct')::numeric / 100)
                 THEN 'a tiempo'
            ELSE 'atrasada' END AS estado
FROM x;

-- N2 días entre correctivas del mismo equipo
CREATE VIEW taller.v_reincidencia_correctiva AS
SELECT o.sit_workorder_id, o.nro_ot, o.equipo_id, t.area_taller, o.fecha_hora_apertura,
       LAG(o.fecha_hora_cierre) OVER w AS cierre_correctiva_anterior,
       EXTRACT(EPOCH FROM o.fecha_hora_apertura - LAG(o.fecha_hora_cierre) OVER w) / 86400 AS dias_desde_anterior
FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
WHERE t.tipo_documento = 'OT' AND t.tipo_mant = 'CORRECTIVA' AND o.docstatus NOT IN ('VO', 'DR')
WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura);

-- N3 OT abiertas a la fecha de extracción de sit_workorder
CREATE VIEW taller.v_ot_abiertas AS
SELECT o.sit_workorder_id, o.nro_ot, t.tipo_documento, t.tipo_mant, t.area_taller, o.equipo_id, o.docstatus,
       o.fecha_hora_apertura, taller.par('fecha_corte_ot')::date - o.fecha_hora_apertura::date AS dias_abierta
FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
WHERE o.docstatus NOT IN ('CO', 'CL', 'VO');

-- N4 consumo de repuestos por km (equipo × mes × categoría)
CREATE VIEW taller.v_consumo_km_mensual AS
SELECT c.equipo_id, f.anio, f.mes, r.categoria, SUM(c.cantidad) AS cantidad, SUM(c.costo) AS costo,
       MAX(k.km_viajes) AS km_viajes_equipo_mes
FROM taller.fact_consumo_repuesto c
JOIN taller.dim_repuesto r ON r.producto_id = c.producto_id
JOIN comun.dim_fecha f ON f.sk_fecha = c.sk_fecha
LEFT JOIN taller.v_flota_mensual k ON k.equipo_id = c.equipo_id AND k.anio = f.anio AND k.mes = f.mes
WHERE r.es_combustible = 'N' AND r.es_mantencion = 'S' AND c.equipo_id <> -1
GROUP BY 1, 2, 3, 4;
"""

VISTA_COMBUSTIBLE = """
-- I1 rendimiento · I7 control de combustible (un bloque entre dos llenados en base, del área combustible)
CREATE VIEW taller.v_rendimiento_combustible AS
WITH b AS (
  SELECT b.equipo_id, b.bloque, b.inicio, b.termino, EXTRACT(YEAR FROM b.termino)::int AS anio,
         EXTRACT(MONTH FROM b.termino)::int AS mes, b.litros, b.km_odometro, b.km_cargados, b.km_vacios,
         b.rendimiento_odo, b.calidad,
         (b.calidad = 'ok' AND b.rendimiento_odo BETWEEN taller.par('rendimiento_min')::numeric
                                                     AND taller.par('rendimiento_max')::numeric) AS es_valido
  FROM combustible.fact_bloque_combustible b),
med AS (
  SELECT d.modelo, percentile_cont(0.5) WITHIN GROUP (ORDER BY b.rendimiento_odo) AS rendimiento_mediana_modelo
  FROM b JOIN taller.dim_equipo d ON d.equipo_id = b.equipo_id WHERE b.es_valido GROUP BY 1)
SELECT b.*, d.codigo, d.modelo, d.rendimiento_ficha, med.rendimiento_mediana_modelo, r.l100_cargado, r.l100_vacio,
       (b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0 AS litros_esperados_regresion,
       b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0) AS litros_esperados_modelo,
       b.km_odometro / NULLIF(d.rendimiento_ficha, 0) AS litros_esperados_ficha,
       COALESCE((b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0,
                b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0)) AS litros_esperados,
       CASE WHEN r.l100_cargado IS NOT NULL THEN 'regresion' WHEN med.rendimiento_mediana_modelo IS NOT NULL
            THEN 'mediana_modelo' END AS referencia,
       b.litros - COALESCE((b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0,
                           b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0)) AS litros_diferencia
FROM b LEFT JOIN taller.dim_equipo d ON d.equipo_id = b.equipo_id
       LEFT JOIN med ON med.modelo = d.modelo
       LEFT JOIN combustible.ref_regresion_camion r ON r.equipo_id = b.equipo_id;
"""


# ------------------------------------------------------------------------------------------------ principal

# ------------------------------------------------------------------------------------------------ vistas
VISTAS = """
CREATE OR REPLACE FUNCTION taller.par(p TEXT) RETURNS TEXT LANGUAGE sql STABLE AS
  $$ SELECT valor FROM taller.parametro_taller WHERE parametro = p $$;

-- I2 disponibilidad · I3 días trabajados · N1 costo de oportunidad (equipo × mes; todo sumable)
CREATE VIEW taller.v_flota_mensual AS
WITH m AS (
  SELECT e.equipo_id, f.anio, f.mes,
         COUNT(*) AS dias_flota,
         SUM(e.en_taller) AS dias_taller,
         SUM(CASE WHEN e.tipo_taller = 'preventiva' THEN 1 ELSE 0 END) AS dias_taller_preventiva,
         SUM(CASE WHEN e.tipo_taller = 'correctiva' THEN 1 ELSE 0 END) AS dias_taller_correctiva,
         SUM(e.en_taller_cierre_real) AS dias_taller_cierre_real,
         SUM(e.dia_disponible) AS dias_disponibles,
         SUM(e.con_viaje) AS dias_con_viaje,
         SUM(e.dia_trabajado) AS dias_trabajados,
         SUM(e.viaje_estimado) AS dias_viaje_estimado,
         SUM(e.viajes_iniciados) AS viajes, SUM(e.km_viajes) AS km_viajes, SUM(e.ingreso_viajes) AS ingreso_viajes
  FROM taller.fact_estado_diario_equipo e JOIN comun.dim_fecha f ON f.sk_fecha = e.sk_fecha
  GROUP BY 1, 2, 3)
SELECT m.*, d.codigo, d.es_camion, d.es_propio, d.marca, d.modelo,
       m.ingreso_viajes / NULLIF(m.dias_trabajados, 0) AS ingreso_por_dia_trabajado,
       m.dias_taller * COALESCE(m.ingreso_viajes / NULLIF(m.dias_trabajados, 0),
                                SUM(m.ingreso_viajes) OVER w / NULLIF(SUM(m.dias_trabajados) OVER w, 0)) AS costo_oportunidad,
       m.dias_taller * taller.par('ingreso_dia_referencia')::numeric AS costo_oportunidad_referencia
FROM m JOIN taller.dim_equipo d ON d.equipo_id = m.equipo_id
WINDOW w AS (PARTITION BY m.anio, m.mes);

-- I4 tiempo en taller por tipo (una OT de mantención; se asigna al mes de cierre)
CREATE VIEW taller.v_ot_mantencion AS
SELECT o.sit_workorder_id, o.nro_ot, o.equipo_id, d.codigo, d.es_camion, d.tipo_equipo, t.tipo_mant, t.area_taller,
       t.tipo_medicion, o.docstatus, o.fecha_hora_apertura, o.fecha_hora_cierre, f.anio AS anio_cierre, f.mes AS mes_cierre,
       o.flag_cierre_imputado, o.horas_ciclo / 24.0 AS dias_ciclo,
       CASE WHEN o.flag_cierre_imputado = 'N' THEN o.horas_ciclo / 24.0 END AS dias_ciclo_cierre_real,
       o.costo_repuestos
FROM taller.fact_orden_taller o
JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
LEFT JOIN taller.dim_equipo d ON d.equipo_id = o.equipo_id
LEFT JOIN comun.dim_fecha f ON f.sk_fecha = o.sk_fecha_cierre
WHERE t.tipo_documento = 'OT' AND o.docstatus NOT IN ('VO', 'DR');

-- I5 proporción preventiva (solo con OT) · I6a costo con OT por km · I6b costo total sin combustible por km
CREATE VIEW taller.v_costo_mantencion_mensual AS
WITH c AS (
  SELECT c.equipo_id, f.anio, f.mes,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'PREVENTIVA' THEN c.costo ELSE 0 END) AS costo_ot_preventiva,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'CORRECTIVA' THEN c.costo ELSE 0 END) AS costo_ot_correctiva,
         SUM(CASE WHEN c.via_imputacion = 'OT' AND t.tipo_mant = 'NO APLICA' THEN c.costo ELSE 0 END) AS costo_ot_sanitizacion,
         SUM(CASE WHEN c.via_imputacion = 'UN' THEN c.costo ELSE 0 END) AS costo_sin_ot,
         SUM(c.costo) AS costo_mantencion_total
  FROM taller.fact_consumo_repuesto c
  JOIN taller.dim_repuesto r ON r.producto_id = c.producto_id
  JOIN comun.dim_fecha f ON f.sk_fecha = c.sk_fecha
  LEFT JOIN taller.fact_orden_taller o ON o.sit_workorder_id = c.sit_workorder_id
  LEFT JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
  WHERE r.es_combustible = 'N' AND r.es_mantencion = 'S' AND c.equipo_id <> -1
  GROUP BY 1, 2, 3),
k AS (SELECT equipo_id, anio, mes, km_viajes FROM taller.v_flota_mensual)
SELECT COALESCE(c.equipo_id, k.equipo_id) AS equipo_id, COALESCE(c.anio, k.anio) AS anio, COALESCE(c.mes, k.mes) AS mes,
       d.codigo, d.es_camion, d.tipo_equipo,
       COALESCE(c.costo_ot_preventiva, 0) AS costo_ot_preventiva, COALESCE(c.costo_ot_correctiva, 0) AS costo_ot_correctiva,
       COALESCE(c.costo_ot_sanitizacion, 0) AS costo_ot_sanitizacion, COALESCE(c.costo_sin_ot, 0) AS costo_sin_ot,
       COALESCE(c.costo_mantencion_total, 0) AS costo_mantencion_total, COALESCE(k.km_viajes, 0) AS km_viajes
FROM c FULL JOIN k ON k.equipo_id = c.equipo_id AND k.anio = c.anio AND k.mes = c.mes
JOIN taller.dim_equipo d ON d.equipo_id = COALESCE(c.equipo_id, k.equipo_id);

-- I8 cumplimiento preventivo: cada OT preventiva contra la siguiente preventiva del mismo equipo
CREATE VIEW taller.v_cumplimiento_preventivo AS
WITH p AS (
  SELECT o.*, t.tipo_medicion,
         LEAD(o.sit_workorder_id) OVER w AS ot_siguiente, LEAD(o.fecha_hora_apertura) OVER w AS fecha_siguiente,
         LEAD(o.odometro) OVER w AS odometro_siguiente, LEAD(o.horometro) OVER w AS horometro_siguiente
  FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
  WHERE t.tipo_documento = 'OT' AND t.tipo_mant = 'PREVENTIVA' AND o.docstatus NOT IN ('VO', 'DR')
  WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura)),
i AS (
  SELECT p.*, COALESCE(ie.intervalo, dp.intervalo) AS intervalo, COALESCE(ie.unidad, dp.unidad) AS unidad
  FROM p LEFT JOIN taller.intervalo_pauta_equipo ie ON ie.equipo_id = p.equipo_id AND ie.pauta_id = p.pauta_siguiente_id
         LEFT JOIN taller.dim_pauta dp ON dp.pauta_id = p.pauta_siguiente_id),
x AS (
  SELECT i.*,
         CASE i.unidad WHEN 'KM' THEN i.odometro_siguiente - i.odometro
                       WHEN 'HORAS' THEN i.horometro_siguiente - i.horometro
                       WHEN 'DIAS' THEN EXTRACT(EPOCH FROM i.fecha_siguiente - i.fecha_hora_apertura) / 86400 END AS uso_hasta_siguiente
  FROM i)
SELECT x.sit_workorder_id, x.nro_ot, x.equipo_id, x.pauta_id, x.pauta_siguiente_id, x.fecha_hora_apertura,
       x.odometro, x.horometro, x.ot_siguiente, x.fecha_siguiente, x.intervalo, x.unidad, x.uso_hasta_siguiente,
       x.uso_hasta_siguiente - x.intervalo AS desvio,
       CASE WHEN x.intervalo IS NULL THEN 'sin intervalo'
            WHEN x.ot_siguiente IS NULL THEN 'pendiente'
            WHEN x.uso_hasta_siguiente IS NULL THEN 'sin lectura'
            WHEN x.uso_hasta_siguiente <= x.intervalo * (1 + taller.par('tolerancia_preventiva_pct')::numeric / 100)
                 THEN 'a tiempo'
            ELSE 'atrasada' END AS estado
FROM x;

-- N2 días entre correctivas del mismo equipo
CREATE VIEW taller.v_reincidencia_correctiva AS
SELECT o.sit_workorder_id, o.nro_ot, o.equipo_id, t.area_taller, o.fecha_hora_apertura,
       LAG(o.fecha_hora_cierre) OVER w AS cierre_correctiva_anterior,
       EXTRACT(EPOCH FROM o.fecha_hora_apertura - LAG(o.fecha_hora_cierre) OVER w) / 86400 AS dias_desde_anterior
FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
WHERE t.tipo_documento = 'OT' AND t.tipo_mant = 'CORRECTIVA' AND o.docstatus NOT IN ('VO', 'DR')
WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura);

-- N3 OT abiertas a la fecha de extracción de sit_workorder
CREATE VIEW taller.v_ot_abiertas AS
SELECT o.sit_workorder_id, o.nro_ot, t.tipo_documento, t.tipo_mant, t.area_taller, o.equipo_id, o.docstatus,
       o.fecha_hora_apertura, taller.par('fecha_corte_ot')::date - o.fecha_hora_apertura::date AS dias_abierta
FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t ON t.tipo_id = o.tipo_id
WHERE o.docstatus NOT IN ('CO', 'CL', 'VO');

-- N4 consumo de repuestos por km (equipo × mes × categoría)
CREATE VIEW taller.v_consumo_km_mensual AS
SELECT c.equipo_id, f.anio, f.mes, r.categoria, SUM(c.cantidad) AS cantidad, SUM(c.costo) AS costo,
       MAX(k.km_viajes) AS km_viajes_equipo_mes
FROM taller.fact_consumo_repuesto c
JOIN taller.dim_repuesto r ON r.producto_id = c.producto_id
JOIN comun.dim_fecha f ON f.sk_fecha = c.sk_fecha
LEFT JOIN taller.v_flota_mensual k ON k.equipo_id = c.equipo_id AND k.anio = f.anio AND k.mes = f.mes
WHERE r.es_combustible = 'N' AND r.es_mantencion = 'S' AND c.equipo_id <> -1
GROUP BY 1, 2, 3, 4;
"""

VISTA_COMBUSTIBLE = """
-- I1 rendimiento · I7 control de combustible (un bloque entre dos llenados en base, del área combustible)
CREATE VIEW taller.v_rendimiento_combustible AS
WITH b AS (
  SELECT b.equipo_id, b.bloque, b.inicio, b.termino, EXTRACT(YEAR FROM b.termino)::int AS anio,
         EXTRACT(MONTH FROM b.termino)::int AS mes, b.litros, b.km_odometro, b.km_cargados, b.km_vacios,
         b.rendimiento_odo, b.calidad,
         (b.calidad = 'ok' AND b.rendimiento_odo BETWEEN taller.par('rendimiento_min')::numeric
                                                     AND taller.par('rendimiento_max')::numeric) AS es_valido
  FROM combustible.fact_bloque_combustible b),
med AS (
  SELECT d.modelo, percentile_cont(0.5) WITHIN GROUP (ORDER BY b.rendimiento_odo) AS rendimiento_mediana_modelo
  FROM b JOIN taller.dim_equipo d ON d.equipo_id = b.equipo_id WHERE b.es_valido GROUP BY 1)
SELECT b.*, d.codigo, d.modelo, d.rendimiento_ficha, med.rendimiento_mediana_modelo, r.l100_cargado, r.l100_vacio,
       (b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0 AS litros_esperados_regresion,
       b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0) AS litros_esperados_modelo,
       b.km_odometro / NULLIF(d.rendimiento_ficha, 0) AS litros_esperados_ficha,
       COALESCE((b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0,
                b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0)) AS litros_esperados,
       CASE WHEN r.l100_cargado IS NOT NULL THEN 'regresion' WHEN med.rendimiento_mediana_modelo IS NOT NULL
            THEN 'mediana_modelo' END AS referencia,
       b.litros - COALESCE((b.km_cargados * r.l100_cargado + b.km_vacios * r.l100_vacio) / 100.0,
                           b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, 0)) AS litros_diferencia
FROM b LEFT JOIN taller.dim_equipo d ON d.equipo_id = b.equipo_id
       LEFT JOIN med ON med.modelo = d.modelo
       LEFT JOIN combustible.ref_regresion_camion r ON r.equipo_id = b.equipo_id;
"""


# ------------------------------------------------------------------------------------------------ principal
def principal(argv=None, _conn=None):
    print(f"construir_dw_taller.py versión {VERSION}")
    par = leer_parametros()
    categorias = leer_categorias()
    pautas_man = leer_pautas_manual()
    erp = carpeta("erp", "fuentes/erp_2026-10-07")
    tablas = carpeta("tablas", "fuentes/erp_tablas_2026-10-19")
    ruta_veh = buscar(erp, ["adempiere_sit_pbix_travelveh_v.parquet", "sit_pbix_travelveh_v.parquet"])
    ruta_vta = buscar(erp, ["adempiere_sit_pbix_travelvta_v.parquet", "sit_pbix_travelvta_v.parquet"])
    ruta_tramo = buscar(erp, ["adempiere_sit_pbix_traveltramo_v.parquet", "sit_pbix_traveltramo_v.parquet"])
    ruta_cons = buscar(erp, ["adempiere_sit_pbix_prodconsumo_v.parquet", "sit_pbix_prodconsumo_v.parquet"])
    ruta_mast = buscar(erp, ["adempiere_sit_pbix_prodmaster_v.parquet", "sit_pbix_prodmaster_v.parquet"])
    ruta_carga = buscar(erp, ["adempiere_sit_pbix_guiascombust_v.parquet", "sit_pbix_guiascombust_v.parquet"], False)
    ruta_wo = buscar(tablas, ["sit_workorder.parquet", "adempiere_sit_workorder.parquet"])

    veh = leer_equipos(ruta_veh)
    ot = leer_ot(ruta_wo)
    cons, repuestos, dup = leer_consumos(ruta_cons, ruta_mast, categorias)
    viajes = leer_viajes(ruta_vta, ruta_tramo, par)
    cargas = leer_cargas(ruta_carga)
    un_equipo, un_compartidas = mapa_un_equipo(veh)
    cons = resolver_equipo_consumo(cons, ot, un_equipo)
    # corte = la fuente que termina antes (las vistas son del 07-10 y sit_workorder del 19-10): así no quedan días
    # sin viajes al final solo porque esa fuente es más antigua
    fecha_corte = min(ot["apertura"].max(), viajes["inicio"].max(), cons["fecha"].max()).normalize()
    par["fecha_corte"] = fecha_corte.date().isoformat()
    par["fecha_corte_ot"] = ot["apertura"].max().date().isoformat()     # extracción de sit_workorder (para OT abiertas)
    print(f"   órdenes {len(ot):,} · consumos {len(cons):,} ({dup} repetidas quitadas) · viajes {len(viajes):,} · "
          f"corte {par['fecha_corte']} · km_por_dia {par['km_por_dia']} ({par['_origen_km_por_dia']})")

    tipos = armar_tipos(ot)
    ot = ot.merge(tipos[["c_doctype_id", "tipo_mant", "area_taller", "tipo_medicion", "tipo_id"]],
                  on=["c_doctype_id", "tipo_mant", "area_taller", "tipo_medicion"], how="left")
    # costo de repuestos por OT (mantención, sin combustible)
    mant = set(repuestos.loc[(repuestos["es_mantencion"] == "S") & (repuestos["es_combustible"] == "N"), "producto_id"])
    cm = cons[ident(cons["m_product_id"]).isin(mant) & cons["sit_workorder_id"].notna()]
    costo_ot = cm.groupby("sit_workorder_id").agg(costo_repuestos=("costo", "sum"), lineas_consumo=("costo", "size"))
    ot = ot.merge(costo_ot, left_on="sit_workorder_id", right_index=True, how="left")
    nro = dict(zip(ot["sit_workorder_id"], ot["documentno"]))
    ot["solicitud_id"] = ident(ot["jobapplication_id"])
    ot["solicitud_id"] = ot["solicitud_id"].where(ot["solicitud_id"] > 0)
    fact_ot = pd.DataFrame({
        "sit_workorder_id": ot["sit_workorder_id"], "nro_ot": ot["documentno"].astype("string"), "tipo_id": ot["tipo_id"],
        "equipo_id": ot["equipo_id"], "pauta_id": ot["pauta_id"],
        "pauta_siguiente_id": ident(ot["sit_nextstandard_id"]).where(ident(ot["sit_nextstandard_id"]) > 0),
        "seqno": ident(ot["seqno"]), "solicitud_id": ot["solicitud_id"],
        "nro_solicitud": ot["solicitud_id"].map(lambda x: nro.get(x) if pd.notna(x) else None),
        "sk_fecha_apertura": fecha_sk(ot["apertura"]), "sk_fecha_cierre": fecha_sk(ot["cierre"]),
        "fecha_hora_apertura": ot["apertura"], "fecha_hora_cierre": ot["cierre"],
        "flag_cierre_imputado": ot["flag_cierre_imputado"], "docstatus": ot["docstatus"].astype("string"),
        "horas_ciclo": ((ot["cierre"] - ot["apertura"]).dt.total_seconds() / 3600).where(ot["cierre"] >= ot["apertura"]),
        "odometro": ot["odometro"], "horometro": ot["horometro"], "prioridad": ot["priority"].astype("string"),
        "costo_repuestos": ot["costo_repuestos"].fillna(0.0), "lineas_consumo": ot["lineas_consumo"].fillna(0).astype(int)})

    fact_cons = pd.DataFrame({
        "consumo_id": np.arange(1, len(cons) + 1), "nro_documento": cons["documentno"].astype("string"),
        "sk_fecha": fecha_sk(cons["fecha"]), "fecha": cons["fecha"].dt.date,
        "producto_id": ident(cons["m_product_id"]).fillna(-1), "bodega_id": ident(cons["m_warehouse_id"]),
        "ubicacion_id": ident(cons["m_locator_id"]), "centro_costo_id": ident(cons["c_activity_id"]),
        "unidad_negocio_id": cons["unidad_negocio_id"], "sit_workorder_id": cons["sit_workorder_id"],
        "equipo_id": cons["equipo_id"], "via_imputacion": cons["via_imputacion"], "cantidad": num(cons["qtyinternaluse"]),
        "costo": num(cons["costo"]),
        "odometro": num(cons["odometer"]).where(num(cons["odometer"]) > 0, num(cons["odometrogps"]).where(num(cons["odometrogps"]) > 0))})

    dim_eq = armar_dim_equipo(veh, ot, cons, viajes, cargas, par, fecha_corte)
    estado = armar_estado_diario(dim_eq, ot, viajes, par)

    ids_pauta = sorted(set(fact_ot["pauta_id"].dropna()) | set(fact_ot["pauta_siguiente_id"].dropna()) | {-1})
    dp = pd.DataFrame({"pauta_id": ids_pauta})
    gen = pautas_man[pautas_man["equipo_id"].isna()] if len(pautas_man) else pautas_man
    if len(gen):
        dp = dp.merge(gen.drop_duplicates("pauta_id")[["pauta_id", "nombre", "intervalo", "unidad", "fuente"]],
                      on="pauta_id", how="outer")
    for c in ("nombre", "intervalo", "unidad", "fuente"):
        if c not in dp:
            dp[c] = None
    dp.loc[dp["pauta_id"] == -1, "nombre"] = "sin pauta (correctiva)"
    esp = pautas_man[pautas_man["equipo_id"].notna()] if len(pautas_man) else pd.DataFrame()
    esp = (esp.drop_duplicates(["equipo_id", "pauta_id"])[["equipo_id", "pauta_id", "intervalo", "unidad", "fuente"]]
           if len(esp) else pd.DataFrame(columns=["equipo_id", "pauta_id", "intervalo", "unidad", "fuente"]))

    params = pd.DataFrame([{"parametro": k, "valor": v, "unidad": PARAM_DEFECTO.get(k, ("", "", ""))[1],
                            "nota": PARAM_DEFECTO.get(k, ("", "", ""))[2]} for k, v in par.items() if not k.startswith("_")])
    params.loc[params["parametro"] == "fecha_corte", ["unidad", "nota"]] = ["fecha", "Fin del estado diario: la fuente que termina antes"]
    params.loc[params["parametro"] == "fecha_corte_ot", ["unidad", "nota"]] = ["fecha", "Última OT creada en sit_workorder"]

    con = db.conectar(ESQUEMAS, _conn=_conn)
    with con:
        from taller import equipo_comun
        con.execute("CREATE SCHEMA IF NOT EXISTS taller")
        equipo_comun.cargar(con, dim_eq)
        con.execute(ESQUEMA)
        cargar_fechas(con, fecha_corte.date())
        con.copiar_df("taller.parametro_taller", params)
        con.copiar_df("taller.dim_tipo_intervencion", tipos[["tipo_id", "c_doctype_id", "tipo_documento", "tipo_mant",
                                                             "area_taller", "tipo_medicion", "es_sanitizacion"]])
        con.copiar_df("taller.dim_pauta", dp[["pauta_id", "nombre", "intervalo", "unidad", "fuente"]])
        con.copiar_df("taller.intervalo_pauta_equipo", esp)
        con.copiar_df("taller.dim_repuesto", repuestos)
        con.copiar_df("taller.fact_orden_taller", fact_ot)
        con.copiar_df("taller.fact_consumo_repuesto", fact_cons)
        con.copiar_df("taller.fact_estado_diario_equipo", estado)
        con.execute(VISTAS)
        hay_comb = con.existe_tabla("combustible", "fact_bloque_combustible") and \
            con.existe_tabla("combustible", "ref_regresion_camion")
        if hay_comb:
            con.execute(VISTA_COMBUSTIBLE)

        from taller import calidad
        calidad_taller = calidad.publicar(con)

        # controles
        ctrl = {}
        ctrl['calidad_taller'] = calidad_taller
        ctrl["via_imputacion_costo_mantencion"] = {r[0]: round(float(r[1]) / 1e6, 1) for r in con.execute(
            "SELECT via_imputacion, SUM(costo) FROM taller.fact_consumo_repuesto c JOIN taller.dim_repuesto r USING (producto_id) "
            "WHERE r.es_mantencion = 'S' AND r.es_combustible = 'N' AND c.fecha >= DATE '2021-01-01' GROUP BY 1").fetchall()}
        ctrl["ot_cierre_imputado_pct_por_anio"] = {int(r[0]): round(float(r[1]), 1) for r in con.execute(
            "SELECT EXTRACT(YEAR FROM fecha_hora_apertura), 100.0 * AVG(CASE WHEN flag_cierre_imputado = 'S' THEN 1 ELSE 0 END) "
            "FROM taller.fact_orden_taller o JOIN taller.dim_tipo_intervencion t USING (tipo_id) "
            "WHERE t.tipo_documento = 'OT' GROUP BY 1 ORDER BY 1").fetchall()}
        ctrl["camiones_estado_diario"] = con.uno("SELECT COUNT(DISTINCT equipo_id) FROM taller.fact_estado_diario_equipo")[0]
        ctrl["un_compartidas"] = sorted(int(u) for u in un_compartidas)
        ctrl["pautas_con_intervalo"] = con.uno("SELECT COUNT(*) FROM taller.dim_pauta WHERE intervalo IS NOT NULL")[0]
        ctrl["vista_combustible"] = "si" if hay_comb else "no (falta cargar combustible)"
        detalle = (f"ordenes {len(fact_ot)}; consumos {len(fact_cons)} (repetidas quitadas {dup}); equipos {len(dim_eq)}; "
                   f"estado diario {len(estado)} filas; corte {par['fecha_corte']}; controles {ctrl}")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("taller", db.ahora(), VERSION, "", detalle))

    for t in ("taller.dim_equipo", "taller.dim_tipo_intervencion", "taller.dim_pauta", "taller.dim_repuesto",
              "taller.fact_orden_taller", "taller.fact_consumo_repuesto", "taller.fact_estado_diario_equipo"):
        print(f"   {t:34s} {con.uno(f'SELECT COUNT(*) FROM {t}')[0]:9d}")
    res = con.df("""SELECT anio, SUM(dias_flota) AS dias_flota, SUM(dias_taller) AS dias_taller,
                           ROUND(100.0 * SUM(dias_flota - dias_taller) / NULLIF(SUM(dias_flota), 0), 1) AS disponibilidad,
                           ROUND(100.0 * SUM(dias_trabajados) / NULLIF(SUM(dias_disponibles), 0), 1) AS pct_dias_trabajados,
                           SUM(dias_viaje_estimado) AS dias_viaje_estimado
                    FROM taller.v_flota_mensual WHERE es_camion = 'S' GROUP BY 1 ORDER BY 1""")
    print("   camiones (centro de costo del parámetro), por año:\n   " + res.to_string(index=False).replace("\n", "\n   "))
    print("   " + detalle)
    if not hay_comb:
        print("   aviso: v_rendimiento_combustible no se creó (cargar el área combustible y volver a correr taller)")
    con.close()


if __name__ == "__main__":
    principal(sys.argv[1:])
