#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga las cartas de precio de Copec (salida de leer_cartas.py) en el esquema `precios` de la base PostgreSQL del MVP.

Solo escribe en el esquema precios (y agrega su fila a comun.registro_carga); no toca peajes ni combustible.

  dim_fuente_precio               GRANEL (base, oilsupplier ATP) · TCT (ruta, oilsupplier COP)
  dim_carta_precio                una carta única (las copias a otros destinatarios no se repiten)
  fact_precio_combustible         una línea de precio de una carta, tal como viene en el documento
  carta_archivo                   un PDF recibido → a qué carta corresponde y de qué correo vino (trazabilidad)
  parametro_precio                parámetros con vigencia (% de recuperación del impuesto específico)
  fact_costo_combustible_vigente  fuente × período de vigencia: precio facturado, desembolso neto de IVA y COSTO REAL
  v_costo_combustible_dia         (vista) una fila por día y fuente: el costo vigente ese día, para valorizar cargas

Se recalcula completo en cada ejecución (son datos derivados de los PDF, que se conservan en cartas_copec/pdf).

Uso (en la carpeta MVP Data Warehouse/precios):
  python leer_cartas.py                 # PDF → cartas_out/*.csv (solo si hay cartas nuevas)
  python construir_dw_precios.py        # cartas_out → PostgreSQL
"""
import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))       # carpeta MVP Data Warehouse (comun/db.py)
from comun import db                                                   # noqa: E402

VERSION = "2026-10-19-pg"
AQUI = Path(__file__).resolve().parent
SALIDAS = AQUI / "cartas_out"
PARAMETROS = AQUI / "parametros_precio_manual.csv"
ESQUEMAS = ("precios", "comun")

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS comun;
CREATE SCHEMA IF NOT EXISTS precios;
CREATE TABLE IF NOT EXISTS comun.dim_fecha (
  sk_fecha INTEGER PRIMARY KEY, fecha DATE UNIQUE, anio INTEGER, mes INTEGER, dia INTEGER, dia_semana INTEGER,
  nombre_dia TEXT, es_fin_de_semana TEXT);
CREATE TABLE IF NOT EXISTS comun.registro_carga (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, area TEXT, fecha_carga TIMESTAMP, version_script TEXT,
  fecha_cambio TEXT, detalle TEXT);

DROP VIEW IF EXISTS v_costo_combustible_dia;
DROP TABLE IF EXISTS fact_costo_combustible_vigente, fact_precio_combustible, carta_archivo, dim_carta_precio,
                     dim_fuente_precio, parametro_precio;

CREATE TABLE dim_fuente_precio (
  sk_fuente INTEGER PRIMARY KEY,
  fuente TEXT UNIQUE,              -- GRANEL, TCT
  oilsupplier TEXT,                -- código del registro de cargas que se valoriza con esta fuente: ATP, COP
  descripcion TEXT);

CREATE TABLE dim_carta_precio (
  sk_carta INTEGER PRIMARY KEY,
  id_carta TEXT UNIQUE,            -- GRANEL-<N° carta> o TCT-<AAAAMMDD>-<huella>
  sk_fuente INTEGER REFERENCES dim_fuente_precio,
  nro_carta TEXT,                  -- conserva ceros; vacío en TCT
  fecha_emision DATE,
  hora_emision TIME,
  fecha_vigencia DATE,             -- "a partir de las cero horas del día ..."
  fecha_hora_correo TIMESTAMP,     -- primer correo en que llegó
  destinatarios TEXT,              -- a quiénes la mandó Copec
  copias INTEGER,                  -- PDF recibidos con esta misma carta
  es_rectificacion TEXT,           -- S si el texto dice que rectifica
  anulada TEXT,                    -- S si otra carta de la misma fecha la reemplazó
  reemplazada_por TEXT,
  ie_fijo NUMERIC(12,5), ie_variable NUMERIC(12,5), ie_total NUMERIC(12,5),
  archivo TEXT, hash_sha256 TEXT);

CREATE TABLE fact_precio_combustible (
  sk_precio INTEGER PRIMARY KEY,
  sk_carta INTEGER REFERENCES dim_carta_precio,
  sk_fuente INTEGER REFERENCES dim_fuente_precio,
  localidad_region TEXT,
  solicitante TEXT,
  producto TEXT,
  volumen_camion NUMERIC(10,3),    -- litros del camión cisterna; NULL en TCT y en filas sin volumen
  neto NUMERIC(14,5),
  neto_deducido TEXT,              -- S en TCT: (final − IE) / 1,19
  fepp NUMERIC(14,5),
  monto_afecto NUMERIC(14,5),
  iva NUMERIC(14,5),
  ie NUMERIC(14,5),
  precio_final NUMERIC(14,5),
  es_base TEXT,                    -- S: fila que puede valer como costo (granel La Serena 631216 / TCT región elegida)
  es_elegida TEXT,                 -- S: la fila que se usa (granel: la más cara de las de base)
  es_tct_historico TEXT);          -- S: fila 641073 de la carta de granel = precio TCT antes de 2026-05-21

CREATE TABLE carta_archivo (
  archivo TEXT PRIMARY KEY, hash_sha256 TEXT, tipo TEXT, sk_carta INTEGER REFERENCES dim_carta_precio,
  estado TEXT,                     -- cargada, copia, rechazada, no_carta
  motivo TEXT, remitente TEXT, fecha_hora_correo TIMESTAMP, asunto TEXT, message_id TEXT);

CREATE TABLE parametro_precio (
  clave TEXT, valor NUMERIC(12,6), vigente_desde DATE, vigente_hasta DATE, fuente TEXT, nota TEXT,
  PRIMARY KEY (clave, vigente_desde));

CREATE TABLE fact_costo_combustible_vigente (
  sk_fuente INTEGER REFERENCES dim_fuente_precio,
  vigente_desde DATE,
  vigente_hasta DATE,              -- exclusiva: fecha de la carta siguiente; 9999-12-31 = sigue vigente
  sk_carta INTEGER REFERENCES dim_carta_precio,
  sk_precio INTEGER REFERENCES fact_precio_combustible,
  origen_precio TEXT,              -- carta granel · carta TCT · carta granel fila 641073 (TCT histórico)
  localidad_region TEXT,
  volumen_camion NUMERIC(10,3),
  neto NUMERIC(14,5),              -- en TCT, recalculado con el IE aplicado
  ie_carta NUMERIC(14,5),
  ie_aplicado NUMERIC(14,5),       -- TCT: IE de la carta de granel de la misma fecha si difiere (lo fija la ley)
  ie_corregido TEXT,               -- S si ie_aplicado ≠ ie_carta
  pct_recuperacion_ie NUMERIC(8,5),
  precio_facturado NUMERIC(14,5),  -- neto + IVA + IE (precio final)
  desembolso_neto_iva NUMERIC(14,5), -- neto + IE
  costo_real NUMERIC(14,5),        -- neto + IE × (1 − % recuperación): el que valoriza cargas y viajes
  PRIMARY KEY (sk_fuente, vigente_desde));
"""

COSTO = """
INSERT INTO fact_costo_combustible_vigente
WITH directas AS (
  SELECT c.sk_carta, c.sk_fuente, c.fecha_vigencia, p.sk_precio, p.localidad_region, p.volumen_camion,
         p.neto, p.ie, p.precio_final, f.fuente,
         CASE WHEN f.fuente = 'GRANEL' THEN 'carta granel' ELSE 'carta TCT' END AS origen_precio
  FROM dim_carta_precio c
  JOIN fact_precio_combustible p ON p.sk_carta = c.sk_carta AND p.es_elegida = 'S'
  JOIN dim_fuente_precio f ON f.sk_fuente = c.sk_fuente
  WHERE c.anulada = 'N'),
historicas AS (       -- TCT antes de la primera carta TCT: fila 641073 de la carta de granel
  SELECT c.sk_carta, 2 AS sk_fuente, c.fecha_vigencia, p.sk_precio, p.localidad_region, p.volumen_camion,
         p.neto, p.ie, p.precio_final, 'TCT' AS fuente, 'carta granel fila 641073 (TCT histórico)' AS origen_precio
  FROM dim_carta_precio c
  JOIN fact_precio_combustible p ON p.sk_carta = c.sk_carta AND p.es_tct_historico = 'S'
  WHERE c.anulada = 'N'
    AND c.fecha_vigencia < COALESCE((SELECT MIN(fecha_vigencia) FROM directas WHERE fuente = 'TCT'), DATE '9999-12-31')),
elegidas AS (SELECT * FROM directas UNION ALL SELECT * FROM historicas),
con_ie AS (
  SELECT e.*,
         CASE WHEN e.fuente = 'TCT' THEN COALESCE(g.ie, e.ie) ELSE e.ie END AS ie_aplicado,
         LEAD(e.fecha_vigencia) OVER (PARTITION BY e.sk_fuente ORDER BY e.fecha_vigencia) AS siguiente
  FROM elegidas e
  LEFT JOIN elegidas g ON g.fuente = 'GRANEL' AND g.fecha_vigencia = e.fecha_vigencia
                      AND e.fuente = 'TCT' AND e.origen_precio = 'carta TCT'),
calc AS (
  SELECT x.*,
         CASE WHEN x.origen_precio = 'carta TCT' THEN ROUND((x.precio_final - x.ie_aplicado) / 1.19, 5) ELSE x.neto END AS neto_aplicado,
         (SELECT p.valor FROM parametro_precio p WHERE p.clave = 'pct_recuperacion_ie'
            AND x.fecha_vigencia >= p.vigente_desde AND x.fecha_vigencia < p.vigente_hasta) AS pct
  FROM con_ie x)
SELECT sk_fuente, fecha_vigencia, COALESCE(siguiente, DATE '9999-12-31'), sk_carta, sk_precio, origen_precio, localidad_region,
       volumen_camion, neto_aplicado, ie, ie_aplicado, CASE WHEN ie_aplicado <> ie THEN 'S' ELSE 'N' END, pct,
       precio_final, neto_aplicado + ie_aplicado, ROUND(neto_aplicado + ie_aplicado * (1 - pct), 5)
FROM calc;
"""

VISTA_DIA = """
CREATE VIEW v_costo_combustible_dia AS
SELECT d.sk_fecha, d.fecha, f.fuente, f.oilsupplier, v.sk_carta, v.precio_facturado, v.desembolso_neto_iva,
       v.costo_real, v.vigente_desde
FROM fact_costo_combustible_vigente v
JOIN dim_fuente_precio f ON f.sk_fuente = v.sk_fuente
JOIN comun.dim_fecha d ON d.fecha >= v.vigente_desde AND d.fecha < v.vigente_hasta AND d.fecha <= CURRENT_DATE;
"""


def cargar_fechas(con):
    """Crea comun.dim_fecha si está vacía (misma definición que peajes y combustible; no la modifica si ya tiene datos)."""
    if con.execute("SELECT COUNT(*) FROM comun.dim_fecha").fetchone()[0]:
        return
    nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    d, filas = date(2015, 1, 1), []
    while d <= date(2030, 12, 31):
        filas.append((int(d.strftime("%Y%m%d")), d.isoformat(), d.year, d.month, d.day, d.isoweekday(),
                      nombres[d.weekday()], "S" if d.weekday() >= 5 else "N"))
        d += timedelta(days=1)
    con.copiar("comun.dim_fecha", ["sk_fecha", "fecha", "anio", "mes", "dia", "dia_semana", "nombre_dia", "es_fin_de_semana"], filas)


def leer(ruta):
    return pd.read_csv(ruta, dtype=str, keep_default_na=False)


def nulo(s):
    return s.replace("", None)


def parametros():
    if not PARAMETROS.exists():                      # valores por defecto si falta el archivo manual
        return pd.DataFrame([dict(clave="pct_recuperacion_ie", valor="0.31", vigente_desde="1900-01-01",
                                  vigente_hasta="9999-12-31", fuente="Entrevista y planilla del tarificador",
                                  nota="Por confirmar con el contador")])
    with open(PARAMETROS, encoding="utf-8-sig", newline="") as fh:
        return pd.DataFrame(list(csv.DictReader(fh)))


def principal(argv=None, _conn=None):
    print(f"construir_dw_precios.py versión {VERSION}")
    ap = argparse.ArgumentParser(description="Carga las cartas Copec en el esquema precios del MVP.")
    ap.add_argument("--salidas", default=str(SALIDAS), help="carpeta con cartas.csv, lineas.csv y archivos.csv")
    a = ap.parse_args(argv)
    carpeta = Path(a.salidas)
    if not (carpeta / "cartas.csv").exists():
        sys.exit(f"No encuentro {carpeta / 'cartas.csv'}: ejecutar antes leer_cartas.py")
    cartas, lineas, archivos = (leer(carpeta / n) for n in ("cartas.csv", "lineas.csv", "archivos.csv"))

    con = db.conectar(ESQUEMAS, _conn=_conn)
    with con:
        con.execute(ESQUEMA)
    with con:
        cargar_fechas(con)
        con.copiar("dim_fuente_precio", ["sk_fuente", "fuente", "oilsupplier", "descripcion"],
                   [(1, "GRANEL", "ATP", "Granel en la base (carta 'Carta precio'), fila La Serena más cara"),
                    (2, "TCT", "COP", "Tarjeta Copec en ruta: carta 'Combustible' región Antofagasta desde 2026-05-21; "
                                      "antes, fila 641073 de la carta de granel")])
        sk_f = {"GRANEL": 1, "TCT": 2}

        cartas = cartas.reset_index(drop=True)
        cartas["sk_carta"] = cartas.index + 1
        sk_c = dict(zip(cartas["id_carta"], cartas["sk_carta"]))
        dc = pd.DataFrame({
            "sk_carta": cartas["sk_carta"], "id_carta": cartas["id_carta"], "sk_fuente": cartas["fuente"].map(sk_f),
            "nro_carta": cartas["nro_carta"], "fecha_emision": cartas["fecha_emision"],
            "hora_emision": nulo(cartas["hora_emision"]), "fecha_vigencia": cartas["fecha_vigencia"],
            "fecha_hora_correo": nulo(cartas["fecha_hora_correo"]), "destinatarios": cartas["destinatarios"],
            "copias": cartas["copias"].astype(int), "es_rectificacion": cartas["es_rectificacion"],
            "anulada": cartas["anulada"], "reemplazada_por": cartas["reemplazada_por"],
            "ie_fijo": cartas["ie_fijo"], "ie_variable": cartas["ie_variable"], "ie_total": cartas["ie_total"],
            "archivo": cartas["archivo"], "hash_sha256": cartas["hash_archivo"]})
        con.copiar_df("dim_carta_precio", dc)

        lineas = lineas.reset_index(drop=True)
        fp = pd.DataFrame({
            "sk_precio": lineas.index + 1, "sk_carta": lineas["id_carta"].map(sk_c), "sk_fuente": lineas["fuente"].map(sk_f),
            "localidad_region": lineas["localidad"], "solicitante": lineas["solicitante"], "producto": lineas["producto"],
            "volumen_camion": nulo(lineas["volumen_camion"]), "neto": lineas["neto"], "neto_deducido": lineas["neto_deducido"],
            "fepp": nulo(lineas["fepp"]), "monto_afecto": lineas["monto_afecto"], "iva": lineas["iva"], "ie": lineas["ie"],
            "precio_final": lineas["precio_final"], "es_base": lineas["es_base"], "es_elegida": lineas["es_elegida"],
            "es_tct_historico": lineas["es_tct_historico"]})
        con.copiar_df("fact_precio_combustible", fp)

        ca = pd.DataFrame({
            "archivo": archivos["archivo"], "hash_sha256": archivos["hash_archivo"], "tipo": archivos["tipo"],
            "sk_carta": archivos["id_carta"].map(sk_c).astype("Int64"), "estado": archivos["estado"],
            "motivo": archivos["motivo"], "remitente": archivos["remitente"],
            "fecha_hora_correo": nulo(archivos["fecha_hora_correo"]), "asunto": archivos["asunto"],
            "message_id": archivos["message_id"]})
        con.copiar_df("carta_archivo", ca)

        par = parametros()
        con.copiar("parametro_precio", ["clave", "valor", "vigente_desde", "vigente_hasta", "fuente", "nota"],
                   par[["clave", "valor", "vigente_desde", "vigente_hasta", "fuente", "nota"]].itertuples(index=False, name=None))

        con.execute(COSTO)
        con.execute(VISTA_DIA)

        # controles (Modelo dimensional §12, pruebas 10–13)
        ctrl = {}
        ctrl["vigentes_por_fecha_repetidas"] = con.uno(
            "SELECT COUNT(*) FROM (SELECT sk_fuente, fecha_vigencia FROM dim_carta_precio WHERE anulada='N' "
            "GROUP BY 1,2 HAVING COUNT(*)>1) x")[0]
        ctrl["solapes_vigencia"] = con.uno(
            "SELECT COUNT(*) FROM fact_costo_combustible_vigente a JOIN fact_costo_combustible_vigente b "
            "ON a.sk_fuente=b.sk_fuente AND a.vigente_desde<b.vigente_desde AND b.vigente_desde<a.vigente_hasta "
            "AND b.vigente_desde<>a.vigente_hasta")[0]
        ctrl["costo_sin_pct"] = con.uno("SELECT COUNT(*) FROM fact_costo_combustible_vigente WHERE pct_recuperacion_ie IS NULL")[0]
        ctrl["ie_tct_corregido"] = con.uno("SELECT COUNT(*) FROM fact_costo_combustible_vigente WHERE ie_corregido='S'")[0]
        rango = con.df("SELECT f.fuente || ' (' || v.origen_precio || ')' AS fuente, MIN(v.vigente_desde) desde, "
                       "MAX(v.vigente_desde) ultima, COUNT(*) periodos FROM fact_costo_combustible_vigente v "
                       "JOIN dim_fuente_precio f USING (sk_fuente) GROUP BY 1 ORDER BY 1")
        detalle = (f"cartas {len(dc)} (anuladas {int((dc['anulada'] == 'S').sum())}); líneas {len(fp)}; PDF {len(ca)} "
                   f"({ca['estado'].value_counts().to_dict()}); controles {ctrl}; "
                   + "; ".join(f"{r.fuente} {r.desde}→{r.ultima} ({r.periodos} periodos)" for r in rango.itertuples()))
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("precios", db.ahora(), VERSION, "", detalle))

    for t in ["dim_fuente_precio", "dim_carta_precio", "fact_precio_combustible", "carta_archivo", "parametro_precio",
              "fact_costo_combustible_vigente"]:
        print(f"   {t:32s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:6d}")
    print("   " + detalle)
    ultimo = con.df("SELECT f.fuente, v.vigente_desde, v.precio_facturado, v.costo_real FROM fact_costo_combustible_vigente v "
                    "JOIN dim_fuente_precio f USING (sk_fuente) WHERE v.vigente_hasta = DATE '9999-12-31' ORDER BY 1")
    for r in ultimo.itertuples():
        print(f"   vigente {r.fuente:6s} desde {r.vigente_desde}: facturado {float(r.precio_facturado):,.2f} · "
              f"costo real {float(r.costo_real):,.2f} $/L")
    con.close()


if __name__ == "__main__":
    principal()
