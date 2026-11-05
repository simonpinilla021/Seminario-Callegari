#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Área ESCENARIOS: modelos operacionales 3/4 y adaptador del tarificador exacto 1/2.

  Escenarios 1/2: tarificador.exacto es el único dueño del cálculo. Usa la base de km
  entregada, parámetros literales del Excel y cartas de la localidad requerida.
  Cotiza servicios propios con ingreso, sin vacíos, virtuales ni reparto por ciclo.
  Escenario 2 sustituye solo el rendimiento por el del camión. Los resultados y
  celdas viven en tarificador.fact_cotizacion_servicio; escenarios lee de allí.

  Escenario 3 · real                    combustible certificado P7 (original y ajuste separados); termo con
                                        termo_factor_real × precio de la base; peaje referencial (área peajes); costo fijo,
                                        reposición y neumáticos del método propuesto (área costos)
  Escenario 4 · absorcion_total         igual que el 3 salvo fijo: masa mensual / días-viaje medios de meses previos
  Fleteros en 1/2: comisión observada únicamente; no son cotizaciones Excel.
  Caterina sigue fuera de 1/2. En 3 y 4: pago_tercero más estructura,
  rampla, reposición y neumáticos que determine el método propuesto.

  Termo solo en viajes cargados que no son tractoreo, según el tipo de carga del producto
  (costos/tipo_carga_producto_manual.csv). Comisión incluida en fijo en 1/2;
  en 3/4 se excluye de la masa y se agrega el monto observado por viaje propio.

  Cotización del tarificador (precio que habría cotizado), según la clasificación de ciclos:
    envío = ida y vuelta (km × 2, peaje × 2, termo solo ida) · retorno = solo ida, termo × 0,5,
    total × factor_retorno · vacío = no se cotiza.
    Días = km ÷ km_dia; costo fijo = máx(1, días) × tasa; reposición = días redondeados hacia arriba × tasa;
    intermedios = destinos intermedios del tramo × valor_intermedio (si cobra_intermedios = 1);
    cotización = costo × (1 + margen) redondeado hacia arriba a "redondeo".

  VISTA COMERCIAL SOLO 3/4 (monto_comercial, ciclo comercial con base Santiago de ciclos.fact_viaje_ciclo): el envío principal
  de cada ciclo paga los vacíos y la estructura del ciclo; los demás viajes con carga pagan solo lo que agregan:
    retorno y envío adicional en ruta  → combustible extra por ir cargado (combustible × (1 − 1 ÷ peso)) + termo
    envío adicional local (Coquimbo ↔ La Serena) → sus propios combustible, termo, peaje y neumáticos (sin estructura)
    vacío → 0 (lo paga el envío principal) · ciclo sin carga o viaje sin ciclo → igual que el físico
  Por ciclo, escenario y componente: Σ comercial = Σ físico (solo cambia quién paga).
  En 3/4, fact_costo_virtual conserva los costos recuperables de virtuales y sus faltantes;
  peaje/fijo/reposición/neumáticos requieren tramo válido y contexto propio concordante
  inferido de vecinos reales; tarifas y parámetros vigentes. Los restantes siguen pendientes.
  se agregan al envío principal propio en el resumen comercial. Sin destino quedan pendientes.
  Los subtotales conocidos se muestran separados; un costo incompleto no produce margen en 3/4.

Tablas:
  dim_escenario, dim_componente_costo
  fact_costo_viaje            viaje × escenario × componente: cantidad, unidad, tasa, monto, estado
  fact_cotizacion_viaje       interfaz LEGADA de diagnóstico; no usar como tarificador exacto
  v_costo_viaje_resumen       (vista) una fila por viaje: ingreso, cotización y costo y margen de cada escenario

Uso (en la carpeta MVP Data Warehouse):  python escenarios\construir_dw_escenarios.py
"""
import math
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI.parent / "tarificador"))
from comun import db, vista_viaje  # noqa: E402
from comun.calidad_tramos_virtuales import estado_tramo_virtual, VISTAS_CALIDAD  # noqa: E402
from escenarios.costeo_virtuales import CONSULTA_CONTEXTO, componentes_adicionales, VISTAS_COSTEO  # noqa: E402
from tarificador.cotizaciones import calcular_cotizaciones, factor_termo_excel  # noqa: E402

VERSION = "2026-11-03-certificado-absorcion"
PESO_CARGADO = 1.21        # km cargado ÷ km vacío en litros (regresión de bloques, combustible P5); fija el combustible extra
                           # por ir cargado = combustible × (1 − 1 ÷ peso)
TIPO_CARGA = AQUI.parent / "costos" / "tipo_carga_producto_manual.csv"

ESCENARIOS = [
    (1, "tarificador", "Reglas y parámetros del tarificador; precio del combustible y costo fijo desde la BBDD"),
    (2, "tarificador_rend_camion", "Escenario 1 con el rendimiento propio del camión"),
    (3, "real", "Combustible certificado; costo fijo por capacidad práctica; residual de capacidad separado"),
    (4, "absorcion_total", "Combustible certificado igual a 3; fijo por promedio de días-viaje de los 3 meses previos"),
]
COMPONENTES = [
    (1, "combustible", "L", "variable"), (2, "termo", "L", "variable"), (3, "peaje", "viaje", "variable"),
    (4, "costo_fijo", "días", "fijo"), (5, "reposicion", "días", "fijo"), (6, "neumaticos", "km", "variable"),
    (7, "intermedios", "paradas", "variable"), (9, "pago_tercero", "viaje", "tercero"),
    (10, "comision_conductor", "viaje", "directo"),
]
SK_COMP = {n: k for k, n, _, _ in COMPONENTES}

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS escenarios;
DROP VIEW IF EXISTS escenarios.v_costo_viaje_escenarios, escenarios.v_costo_viaje_resumen CASCADE;
DROP TABLE IF EXISTS escenarios.fact_viaje_costo_escenario, escenarios.fact_viaje_cotizacion,
  escenarios.parametro_tarificador, escenarios.peaje_tarificador,
  escenarios.fact_costo_viaje, escenarios.fact_cotizacion_viaje, escenarios.dim_escenario,
  escenarios.dim_componente_costo CASCADE;
DROP TABLE IF EXISTS escenarios.fact_costo_virtual CASCADE;
CREATE TABLE escenarios.dim_escenario (sk_escenario INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT);
CREATE TABLE escenarios.dim_componente_costo (
  sk_componente INTEGER PRIMARY KEY, nombre TEXT, unidad TEXT,
  tipo TEXT);                        -- variable · fijo · tercero
CREATE TABLE escenarios.fact_costo_viaje (
  nro_viaje BIGINT,
  sk_escenario INTEGER REFERENCES escenarios.dim_escenario,
  sk_componente INTEGER REFERENCES escenarios.dim_componente_costo,
  sk_fecha INTEGER,
  cantidad DOUBLE PRECISION,         -- litros, días, km, paradas o 1 viaje
  tasa DOUBLE PRECISION,             -- $ por unidad de la cantidad
  monto DOUBLE PRECISION,            -- cantidad × tasa (NULL si falta un dato): vista FÍSICA
  monto_comercial DOUBLE PRECISION,  -- vista COMERCIAL (ciclo con base Santiago)
  rol_comercial TEXT,                -- envío principal · envío adicional en ruta · envío adicional local · retorno · vacío · sin carga
  ciclo_comercial TEXT,
  estado TEXT,                       -- ok · sin dato
  estado_comercial TEXT,             -- propaga faltantes del ciclo al envío principal
  PRIMARY KEY (nro_viaje, sk_escenario, sk_componente));
CREATE TABLE escenarios.fact_costo_virtual (
  id_viaje TEXT,
  ciclo_comercial TEXT,
  nro_viaje_destino BIGINT,           -- principal propio; NULL si no existe uno apto
  sk_fecha INTEGER,
  sk_escenario INTEGER REFERENCES escenarios.dim_escenario,
  sk_componente INTEGER REFERENCES escenarios.dim_componente_costo,
  cantidad DOUBLE PRECISION, tasa DOUBLE PRECISION, monto DOUBLE PRECISION,
  estado TEXT, estado_asignacion TEXT, estado_tramo TEXT,
  estado_contexto TEXT, origen_calculo TEXT,
  cf_flota_virtual DOUBLE PRECISION,cf_rampla_virtual DOUBLE PRECISION,cf_estructura_virtual DOUBLE PRECISION,
  PRIMARY KEY (id_viaje, sk_escenario, sk_componente));
CREATE TABLE escenarios.fact_cotizacion_viaje (
  nro_viaje BIGINT PRIMARY KEY,
  sk_fecha INTEGER, clasificacion_viaje TEXT,
  modalidad TEXT,                    -- ida y vuelta · solo ida × factor · no se cotiza
  tipo_carga TEXT, es_tractoreo TEXT, n_intermedios INTEGER,
  km_cotizados DOUBLE PRECISION, dias DOUBLE PRECISION,
  combustible DOUBLE PRECISION, termo DOUBLE PRECISION, peaje DOUBLE PRECISION, costo_fijo DOUBLE PRECISION,
  reposicion DOUBLE PRECISION, neumaticos DOUBLE PRECISION, intermedios DOUBLE PRECISION,
  costo_total DOUBLE PRECISION,      -- con el factor de retorno si corresponde
  margen DOUBLE PRECISION,
  cotizacion DOUBLE PRECISION,       -- redondeada hacia arriba
  subtotal_componentes_conocidos DOUBLE PRECISION, -- antes del factor de retorno
  subtotal_conocido DOUBLE PRECISION, -- con factor; no equivale a total válido
  costo_completo BOOLEAN, cotizacion_completa BOOLEAN,
  n_componentes_faltantes INTEGER, faltantes TEXT,
  estado TEXT);
"""

VISTA = """
CREATE VIEW escenarios.v_costo_viaje_resumen AS
WITH detalle AS (
  SELECT nro_viaje, sk_escenario, monto, monto_comercial, estado, estado_comercial,
         rol_comercial, ciclo_comercial, FALSE AS es_virtual
  FROM escenarios.fact_costo_viaje
  UNION ALL
  SELECT nro_viaje_destino, sk_escenario, NULL, monto, estado, estado, NULL, NULL, TRUE
  FROM escenarios.fact_costo_virtual WHERE nro_viaje_destino IS NOT NULL),
c AS (
  SELECT nro_viaje,
         SUM(monto) FILTER (WHERE sk_escenario = 1) AS costo_1_tarificador,
         SUM(monto) FILTER (WHERE sk_escenario = 2) AS costo_2_tarif_rend_camion,
         CASE WHEN BOOL_AND(estado = 'ok' AND monto IS NOT NULL) FILTER (WHERE sk_escenario = 3 AND NOT es_virtual)
              THEN SUM(monto) FILTER (WHERE sk_escenario = 3) END AS costo_3_real,
         CASE WHEN BOOL_AND(estado = 'ok' AND monto IS NOT NULL) FILTER (WHERE sk_escenario = 4 AND NOT es_virtual)
              THEN SUM(monto) FILTER (WHERE sk_escenario = 4) END AS costo_4_absorcion_total,
         SUM(monto_comercial) FILTER (WHERE sk_escenario = 1) AS costo_comercial_1,
         SUM(monto_comercial) FILTER (WHERE sk_escenario = 2) AS costo_comercial_2,
         SUM(monto) FILTER (WHERE sk_escenario = 3) AS subtotal_conocido_3,
         SUM(monto) FILTER (WHERE sk_escenario = 4) AS subtotal_conocido_4,
         SUM(monto_comercial) FILTER (WHERE sk_escenario = 3) AS subtotal_comercial_conocido_3,
         SUM(monto_comercial) FILTER (WHERE sk_escenario = 4) AS subtotal_comercial_conocido_4,
         BOOL_AND(estado = 'ok' AND monto IS NOT NULL)
           FILTER (WHERE sk_escenario = 3 AND NOT es_virtual) AS completo_3,
         BOOL_AND(estado = 'ok' AND monto IS NOT NULL)
           FILTER (WHERE sk_escenario = 4 AND NOT es_virtual) AS completo_4,
         BOOL_AND(estado_comercial = 'ok' AND monto_comercial IS NOT NULL)
           FILTER (WHERE sk_escenario = 3) AS completo_comercial_3,
         BOOL_AND(estado_comercial = 'ok' AND monto_comercial IS NOT NULL)
           FILTER (WHERE sk_escenario = 4) AS completo_comercial_4,
         MAX(rol_comercial) AS rol_comercial, MAX(ciclo_comercial) AS ciclo_comercial,
         BOOL_AND(estado = 'ok') FILTER (WHERE NOT es_virtual) AS completo
  FROM detalle GROUP BY nro_viaje)
SELECT vv.nro_viaje, f.fecha, vv.equipo_id, vv.tipo_operador, cy.clasificacion_viaje, vv.cliente_id,
       cl.razon_social AS cliente, tb.tramo AS texto_tramo, vv.ingreso, q.cotizacion AS cotizacion_tarificador,
       c.costo_1_tarificador, c.costo_2_tarif_rend_camion, c.costo_3_real, c.costo_4_absorcion_total,
       vv.ingreso - c.costo_1_tarificador AS margen_1, vv.ingreso - c.costo_2_tarif_rend_camion AS margen_2,
       vv.ingreso - c.costo_3_real AS margen_3, vv.ingreso - c.costo_4_absorcion_total AS margen_4,
       c.ciclo_comercial, c.rol_comercial,
       c.costo_comercial_1, c.costo_comercial_2,
       CASE WHEN c.completo_comercial_3 THEN c.subtotal_comercial_conocido_3 END AS costo_comercial_3,
       CASE WHEN c.completo_comercial_4 THEN c.subtotal_comercial_conocido_4 END AS costo_comercial_4,
       vv.ingreso - c.costo_comercial_1 AS margen_comercial_1, vv.ingreso - c.costo_comercial_2 AS margen_comercial_2,
       CASE WHEN c.completo_comercial_3 THEN vv.ingreso - c.subtotal_comercial_conocido_3 END AS margen_comercial_3,
       CASE WHEN c.completo_comercial_4 THEN vv.ingreso - c.subtotal_comercial_conocido_4 END AS margen_comercial_4,
       c.completo AND COALESCE(c.completo_comercial_3, FALSE)
         AND COALESCE(c.completo_comercial_4, FALSE) AS costos_completos,
       c.subtotal_conocido_3, c.subtotal_conocido_4,
       c.subtotal_comercial_conocido_3, c.subtotal_comercial_conocido_4,
       c.completo_3, c.completo_4, c.completo_comercial_3, c.completo_comercial_4
FROM c
JOIN ventas.fact_viaje_venta vv ON vv.nro_viaje = c.nro_viaje
LEFT JOIN escenarios.fact_cotizacion_viaje q ON q.nro_viaje = c.nro_viaje
LEFT JOIN comun.dim_fecha f ON f.sk_fecha = vv.sk_fecha
LEFT JOIN ventas.dim_cliente cl ON cl.cliente_id = vv.cliente_id
LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje = c.nro_viaje
LEFT JOIN ciclos.dim_tramo_base tb ON tb.sit_stretch_id = COALESCE(cy.sit_stretch_id, vv.sit_stretch_id)
"""


def norm(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    x = re.sub(r"\(.*?\)", " ", str(x))
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", x.upper())


def lugares(texto):
    """Ciudades del tramo, sin repetir consecutivas iguales."""
    out = []
    for x in str(texto or "").split("/"):
        n = norm(x)
        if n and (not out or out[-1] != n):
            out.append(n)
    return out


def peaje_tramo(texto, matriz):
    ls = lugares(texto)
    if len(ls) < 2:
        return 0.0 if ls else np.nan                  # local en la misma ciudad: sin peaje
    total = 0.0
    for o, d in zip(ls, ls[1:]):
        v = matriz.get((o, d))
        if v is None:
            return np.nan
        total += v
    return total


def fuentes_cotizacion(v, pt, precio):
    """Enriquece los mismos insumos en ETL y en la auditoría de solo lectura."""
    pr = precio.set_index('sk_fecha')
    v['precio_tarif'] = v['sk_fecha'].map(pr['precio_tarif'])
    v['precio_base'] = v['sk_fecha'].map(pr['costo_base'])
    # Precio almacenado = mezcla base/TCT + colchón; quitar solo el colchón.
    v['precio_ponderado_real'] = v['sk_fecha'].map(pr['precio_tarif'] - pr['recargo'])
    tc = pd.read_csv(TIPO_CARGA, encoding='utf-8-sig')
    tipo = dict(zip(pd.to_numeric(tc['producto_id'], errors='coerce'), tc['tipo_carga'].astype(str)))
    v['tipo_carga'] = v['producto_id'].map(tipo).fillna('sin clasificar')
    v.loc[v['es_tractoreo'].eq('S'), 'tipo_carga'] = 'sin termo'
    v['peaje_tarif'] = np.nan
    f = v['fecha']
    for (d, h), g in pt.groupby(['d', 'h']):
        matriz = {(o, x): p for o, x, p in zip(g.origen_norm, g.destino_norm, g.p)}
        m = (f >= pd.Timestamp(d)) & (f < pd.Timestamp(min(h, '2262-04-11')))
        cache = {t: peaje_tramo(t, matriz) for t in v.loc[m, 'texto_tramo'].dropna().unique()}
        v.loc[m, 'peaje_tarif'] = v.loc[m, 'texto_tramo'].map(cache)
    return v['texto_tramo'].map(lambda t: max(len(lugares(t)) - 2, 0)).astype(int)


def leer_cotizacion(con, nros=None):
    """Calcula la propuesta desde las áreas cargadas; no escribe ni ejecuta ETL."""
    from construir_dw_tarificador import Parametros
    par = Parametros(con.df("""SELECT d.codigo, v.ambito, v.vigente_desde::text AS vigente_desde,
      v.vigente_hasta::text AS vigente_hasta, v.valor, v.origen
      FROM tarificador.fact_parametro_valor v JOIN tarificador.dim_parametro d USING(sk_parametro)"""))
    pt = con.df("""SELECT origen_norm, destino_norm, vigente_desde::text AS d,
      vigente_hasta::text AS h, peaje::float8 AS p FROM tarificador.fact_peaje_tarificador""")
    precio = con.df("""SELECT sk_fecha, costo_base::float8 AS costo_base,
      precio_tarificador::float8 AS precio_tarif, recargo::float8 AS recargo FROM tarificador.fact_precio_combustible_tarificador""")
    sql_v = """SELECT vv.nro_viaje, vv.sk_fecha, vv.producto_id, vv.ingreso::float8 AS ingreso,
      vv.es_tractoreo, cy.clasificacion_viaje, COALESCE(tb.tramo,t.texto_tramo) AS texto_tramo,
      COALESCE(t.km_erp,cy.km)::float8 AS km
      FROM ventas.fact_viaje_venta vv
      LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=vv.nro_viaje
      LEFT JOIN ciclos.dim_tramo_base tb ON tb.sit_stretch_id=cy.sit_stretch_id
      LEFT JOIN peajes.fact_viaje_peaje p ON p.nro_viaje=vv.nro_viaje
      LEFT JOIN peajes.dim_tramo t ON t.sk_tramo=p.sk_tramo"""
    v = con.df(sql_v + " WHERE vv.nro_viaje=ANY(%s)", ([int(n) for n in nros],)) if nros is not None else con.df(sql_v)
    for c in ('nro_viaje', 'sk_fecha', 'producto_id', 'ingreso', 'km'):
        v[c] = pd.to_numeric(v[c], errors='coerce')
    for c in ('nro_viaje', 'sk_fecha', 'producto_id'):
        v[c] = v[c].round().astype('Int64')
    if v.nro_viaje.duplicated().any() or v.nro_viaje.isna().any():
        raise ValueError('Los insumos de cotización duplican viajes o carecen de identificador')
    v['fecha'] = pd.to_datetime(v.sk_fecha.astype(str), format='%Y%m%d', errors='coerce')
    n_int = fuentes_cotizacion(v, pt, precio)
    return calcular_cotizaciones(v, par, n_int)


def principal(argv=None, _conn=None):
    from construir_dw_tarificador import Parametros   # noqa: E402 (misma lógica de vigencias)
    print(f"construir_dw_escenarios.py versión {VERSION}")
    con = db.conectar(("escenarios", "comun"), _conn=_conn)
    for esq, tabla in [("ventas", "fact_viaje_venta"), ("ciclos", "fact_viaje_ciclo"), ("peajes", "fact_viaje_peaje"),
                       ("combustible", "fact_viaje_combustible"), ("combustible", "fact_viaje_combustible_tarificador"),
                       ("costos", "fact_viaje_costo_indirecto"), ("tarificador", "fact_parametro_valor"),
                       ("tarificador", "fact_precio_combustible_tarificador"),
                       ("ciclos", "dim_ciclo_comercial"), ("combustible", "fact_bloque_combustible")]:
        if not con.existe_tabla(esq, tabla):
            raise SystemExit(f"Falta {esq}.{tabla}: cargar antes esa área")
    if con.uno("SELECT 1 FROM costos.parametro_costo WHERE metodo = 'propuesto' "
               "AND parametro = 'comision_directa' AND valor = 1") is None:
        raise SystemExit("Recargar costos: falta excluir comisión de las bolsas del método propuesto")

    par = Parametros(con.df("""SELECT d.codigo, v.ambito, v.vigente_desde::text AS vigente_desde,
                                      v.vigente_hasta::text AS vigente_hasta, v.valor, v.origen
                               FROM tarificador.fact_parametro_valor v JOIN tarificador.dim_parametro d USING (sk_parametro)"""))
    pt = con.df("SELECT origen_norm, destino_norm, vigente_desde::text AS d, vigente_hasta::text AS h, peaje::float8 AS p "
                "FROM tarificador.fact_peaje_tarificador")
    precio = con.df("SELECT sk_fecha, costo_base::float8 AS costo_base, precio_tarificador::float8 AS precio_tarif, recargo::float8 AS recargo "
                    "FROM tarificador.fact_precio_combustible_tarificador")
    v = con.df("""
      SELECT vv.nro_viaje, vv.sk_fecha, vv.tipo_operador, vv.producto_id, vv.ingreso::float8 AS ingreso,
             vv.pago_tercero::float8 AS pago_tercero,
             vv.comision_conductor_observada::float8 AS comision_conductor,
             vv.pago_tercero_observado::float8 AS pago_tercero_observado,
             vv.es_tractoreo, cy.clasificacion_viaje,
             COALESCE(tb.tramo, t.texto_tramo) AS texto_tramo, COALESCE(t.km_erp, cy.km)::float8 AS km,
             p.peaje_referencial::float8 AS peaje_ref,
             c.litros_margen::float8 AS litros_real, c.costo_combustible_margen::float8 AS comb_real,
             ct.rendimiento_camion::float8 AS rend_camion,
             cp.dias_viaje::float8 AS dias_p, cp.km_tramo::float8 AS km_p, cp.costo_fijo_asignado::float8 AS cf_p,
             cp.reposicion_asignada::float8 AS rep_p, cp.costo_neumatico_asignado::float8 AS neu_p
      FROM ventas.fact_viaje_venta vv
      LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje = vv.nro_viaje
      LEFT JOIN ciclos.dim_tramo_base tb ON tb.sit_stretch_id = cy.sit_stretch_id
      LEFT JOIN peajes.fact_viaje_peaje p ON p.nro_viaje = vv.nro_viaje
      LEFT JOIN peajes.dim_tramo t ON t.sk_tramo = p.sk_tramo
      LEFT JOIN combustible.fact_viaje_combustible c ON c.nro_viaje = vv.nro_viaje
      LEFT JOIN combustible.fact_viaje_combustible_tarificador ct ON ct.nro_viaje = vv.nro_viaje
      LEFT JOIN costos.fact_viaje_costo_indirecto cp ON cp.nro_viaje = vv.nro_viaje AND cp.metodo = 'propuesto'""")
    for c in v.columns:
        if c not in ("tipo_operador", "es_tractoreo", "clasificacion_viaje", "texto_tramo"):
            v[c] = pd.to_numeric(v[c], errors="coerce")
    for c in ("nro_viaje", "sk_fecha", "producto_id"):
        v[c] = v[c].round().astype("Int64")
    v["fecha"] = pd.to_datetime(v["sk_fecha"].astype(str), format="%Y%m%d", errors="coerce")
    print(f"   viajes: {len(v):,}")

    # parámetros vigentes a la fecha de cada viaje
    trac = v["es_tractoreo"] == "S"
    f = v["fecha"]
    km_dia = par.en("km_dia", f)
    rend = par.en("rendimiento_kml", f)
    cf_dia = par.en("costo_fijo_diario", f)
    rep_dia = par.en("reposicion_diaria", f).where(~trac, par.en("reposicion_diaria", f, "tractoreo"))
    neu_km = par.en("costo_neumatico_km", f).where(~trac, par.en("costo_neumatico_km", f, "tractoreo"))
    f_termo_real = par.en("termo_factor_real", f)
    n_int = fuentes_cotizacion(v, pt, precio)

    # tipo de carga y termo
    cargado = (v["ingreso"].fillna(0) > 0) & (v["clasificacion_viaje"] != "Vacio")
    lh = pd.Series(0.0, index=v.index)
    for amb in ("frio", "congelado", "multi"):
        m = (v["tipo_carga"] == amb) & cargado & ~trac
        lh[m] = par.en("termo_lh", f[m], amb)
    horas_termo = (v["km"] / km_dia * 24).where(lh > 0, 0.0)

    propio = v["tipo_operador"] == "propio"
    operador_conocido = v["tipo_operador"].isin(["propio", "fletero", "caterina"])
    dias = v["km"] / km_dia
    filas = []

    def comp(esc, nombre, cantidad, tasa, monto=None, mask=propio):
        monto = cantidad * tasa if monto is None else monto
        x = pd.DataFrame({"nro_viaje": v["nro_viaje"], "sk_escenario": esc, "sk_componente": SK_COMP[nombre],
                          "sk_fecha": v["sk_fecha"], "cantidad": cantidad, "tasa": tasa, "monto": monto})[mask]
        x["estado"] = np.where(x["monto"].notna(), "ok", "sin dato")
        filas.append(x)

    litros_rend = v["km"] / v["rend_camion"]
    # 1/2 se construyen exclusivamente en tarificador.exacto, con km entregados,
    # fórmulas/versiones del Excel y sin redistribución comercial.
    for esc in (3, 4):
        comp(esc, "combustible", v["litros_real"], v["comb_real"] / v["litros_real"].where(v["litros_real"] > 0),
             v["comb_real"])
        comp(esc, "termo", horas_termo * lh * f_termo_real, v["precio_base"])
        comp(esc, "peaje", pd.Series(1.0, index=v.index), v["peaje_ref"], v["peaje_ref"])
        dp = v["dias_p"].where(v["dias_p"] > 0)
        comp(esc, "costo_fijo", v["dias_p"], v["cf_p"] / dp, v["cf_p"], mask=operador_conocido)
        comp(esc, "reposicion", v["dias_p"], v["rep_p"] / dp, v["rep_p"], mask=operador_conocido)
        comp(esc, "neumaticos", v["km_p"], v["neu_p"] / v["km_p"].where(v["km_p"] > 0), v["neu_p"],
             mask=operador_conocido)
        comp(esc, "comision_conductor", pd.Series(1.0, index=v.index), v["comision_conductor"])
    for esc in (3, 4):
        pago = v["pago_tercero"] if esc in (1, 2) else v["pago_tercero_observado"]
        mascara = ~propio & v["tipo_operador"].notna() if esc in (1, 2) else operador_conocido & ~propio
        comp(esc, "pago_tercero", pd.Series(1.0, index=v.index), pago, mask=mascara)
    fc = pd.concat(filas, ignore_index=True)
    roles = con.df("""SELECT nro_viaje, ciclo_comercial, rol_comercial FROM ciclos.fact_viaje_ciclo
                      WHERE es_virtual = 'N' AND nro_viaje IS NOT NULL""") \
        if con.uno("SELECT 1 FROM information_schema.columns WHERE table_schema = 'ciclos' "
                   "AND table_name = 'fact_viaje_ciclo' AND column_name = 'ciclo_comercial'") is not None \
        else pd.DataFrame(columns=["nro_viaje", "ciclo_comercial", "rol_comercial"])
    if not len(roles):
        print("   aviso: ciclos sin ciclo comercial (cargar ciclos de nuevo): vista comercial = física")
    roles["nro_viaje"] = pd.to_numeric(roles["nro_viaje"], errors="coerce").astype("Int64")
    roles = roles.drop_duplicates("nro_viaje")
    # Los costos de terceros quedan en su propio viaje; no se redistribuyen
    # como si fueran desplazamientos de flota propia. La comisión directa también.
    terceros = v.loc[v["tipo_operador"].isin(["fletero", "caterina"]), "nro_viaje"]
    virtuales = costos_virtuales(leer_virtuales(con), v)
    from costos.absorcion_total import aplicar as aplicar_absorcion
    fc, virtuales = aplicar_absorcion(con, fc, virtuales)
    fc = vista_comercial(fc, roles, PESO_CARGADO, terceros)
    validar_conservacion(fc)

    # Cotización independiente: los faltantes no se publican como precio final.
    q = calcular_cotizaciones(v, par, n_int)

    with con:
        con.execute(ESQUEMA)
        con.copiar_df("escenarios.dim_escenario", pd.DataFrame(ESCENARIOS, columns=["sk_escenario", "nombre", "descripcion"]))
        con.copiar_df("escenarios.dim_componente_costo",
                      pd.DataFrame(COMPONENTES, columns=["sk_componente", "nombre", "unidad", "tipo"]))
        con.copiar_df("escenarios.fact_costo_viaje", fc[["nro_viaje", "sk_escenario", "sk_componente", "sk_fecha",
                                                         "cantidad", "tasa", "monto", "monto_comercial",
                                                         "rol_comercial", "ciclo_comercial", "estado", "estado_comercial"]])
        con.copiar_df("escenarios.fact_costo_virtual", virtuales)
        con.copiar_df("escenarios.fact_cotizacion_viaje", q)
        # La tabla anterior se conserva como interfaz histórica, no es el
        # contrato exacto de consumo. La cotización vigente vive en tarificador.
        from tarificador.exacto.publicacion import construir as construir_exacto
        reporte_exacto = construir_exacto(con)
        con.execute("CREATE INDEX ix_costo_viaje_fecha ON escenarios.fact_costo_viaje (sk_fecha)")
        con.execute("CREATE INDEX ix_costo_virtual_destino ON escenarios.fact_costo_virtual (nro_viaje_destino)")
        # Las validaciones consultan las tablas antes del commit: autovacuum aún
        # no puede proporcionar estadísticas para planificar estas consultas.
        con.execute("ANALYZE escenarios.fact_costo_viaje")
        con.execute("ANALYZE escenarios.fact_costo_virtual")
        con.execute("ANALYZE escenarios.fact_cotizacion_viaje")
        con.execute(VISTA)
        con.execute(VISTAS_CALIDAD)
        con.execute(VISTAS_COSTEO)
        from costos.puente_absorcion import publicar as publicar_puente
        publicar_puente(con)
        # Ventas/costos pueden haber creado la vista común mientras aún no
        # existía el resumen de escenarios; enlazarla a la referencia recién creada.
        vista_viaje.crear_margen(con)
        resumen = resumir(fc, q, v)
        resumen += (f"\n   virtuales: {virtuales['id_viaje'].nunique():,}; "
                    f"componentes pendientes: {int((virtuales['estado'] != 'ok').sum()):,}; "
                    f"sin envío propio: {virtuales.loc[virtuales['nro_viaje_destino'].isna(), 'id_viaje'].nunique():,}")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("escenarios", db.ahora(), VERSION, "", resumen[:4000]))
    n_publicado = con.uno("SELECT count(*) FROM escenarios.fact_costo_viaje")[0]
    con.close()
    print(f"   fact_costo_viaje {n_publicado:,} filas · fact_cotizacion_viaje {len(q):,}")
    print(resumen)
    print(f"   Tarificador exacto 1/2: {reporte_exacto}")


def vista_comercial(fc, roles, peso, terceros=()):
    """monto_comercial: reparte el costo de cada ciclo comercial (envío principal paga vacíos y estructura)."""
    fc = fc.merge(roles, on="nro_viaje", how="left", validate="many_to_one")
    comp = {k: n for k, n, _, _ in COMPONENTES}
    fc["comp"] = fc["sk_componente"].map(comp)
    propio = ~fc["nro_viaje"].isin(terceros) & ~fc["comp"].isin(["pago_tercero", "comision_conductor"])
    rol = fc["rol_comercial"]
    share = pd.Series(1.0, index=fc.index)                                  # sin ciclo o ciclo sin carga: físico
    en_ruta = rol.isin(["retorno", "envío adicional en ruta"])
    share[en_ruta] = np.select([fc.loc[en_ruta, "comp"] == "combustible", fc.loc[en_ruta, "comp"] == "termo"],
                               [1 - 1 / peso, 1.0], 0.0)
    local = rol == "envío adicional local"
    share[local] = np.where(fc.loc[local, "comp"].isin(["costo_fijo", "reposicion"]), 0.0, 1.0)
    share[rol == "vacío"] = 0.0
    fc["monto_comercial"] = fc["monto"] * share
    # el envío principal recibe el resto del ciclo, por escenario y componente
    con_ciclo = propio & fc["ciclo_comercial"].notna()
    clave = ["ciclo_comercial", "sk_escenario", "sk_componente"]
    tot = fc[con_ciclo].groupby(clave)["monto"].sum(min_count=1)
    otros = fc[con_ciclo & (rol != "envío principal")].groupby(clave)["monto_comercial"].sum()
    resto = tot.sub(otros, fill_value=0)
    pri = con_ciclo & (rol == "envío principal")
    idx = pd.MultiIndex.from_frame(fc.loc[pri, clave])
    fc.loc[pri, "monto_comercial"] = resto.reindex(idx).values
    # ciclos cuyo envío principal no está entre estos viajes (p. ej. es de un tercero): vuelve al físico
    tiene_pri = fc[pri].groupby("ciclo_comercial").size()
    sin_pri = con_ciclo & ~fc["ciclo_comercial"].isin(tiene_pri.index)
    fc.loc[sin_pri, "monto_comercial"] = fc.loc[sin_pri, "monto"]
    fc.loc[~propio, "monto_comercial"] = fc.loc[~propio, "monto"]
    # Los faltantes en viajes redistribuidos afectan al principal aunque SUM
    # conserve los importes conocidos. No convertir ausencia de datos en cero.
    fc["estado_comercial"] = fc["estado"]
    incompleto = fc.loc[con_ciclo,clave].assign(
        incompleto=fc.loc[con_ciclo,'estado'].ne('ok')).groupby(clave)['incompleto'].any()
    fc.loc[pri & pd.Series(
        incompleto.reindex(pd.MultiIndex.from_frame(fc[clave])).fillna(False).to_numpy(dtype=bool),
        index=fc.index), "estado_comercial"] = "sin dato en ciclo"
    return fc.drop(columns=["comp"])


def validar_conservacion(fc):
    """Bloquea una publicación que pierda o multiplique costos al redistribuir."""
    if fc.duplicated(["nro_viaje", "sk_escenario", "sk_componente"]).any():
        raise ValueError("Costo repetido por viaje, escenario y componente")
    clave = ["ciclo_comercial", "sk_escenario", "sk_componente"]
    t = fc.groupby(clave, dropna=False)[["monto", "monto_comercial"]].sum()
    if not np.allclose(t["monto"], t["monto_comercial"], rtol=1e-10, atol=0.01):
        raise ValueError("La redistribución comercial no conserva el costo por ciclo y componente")


def leer_virtuales(con):
    """Solo consulta áreas ya cargadas; no conecta por sí misma ni lee fuentes ERP."""
    datos = con.df("""
      SELECT cy.id_viaje, cy.ciclo_comercial, cy.sk_fecha, cy.km,
             cy.tipo_virtual, cy.ciudad_origen_limpia, cy.ciudad_destino_limpia,
             dc.viaje_principal,
             cb.litros_margen AS litros_asignados, cb.costo_combustible_margen AS costo_combustible,
             r.rendimiento_camion, pr.costo_base AS precio_base,
             (pr.precio_tarificador-pr.recargo)::float8 AS precio_ponderado_real
      FROM ciclos.fact_viaje_ciclo cy
      LEFT JOIN ciclos.dim_ciclo_comercial dc USING (ciclo_comercial)
      LEFT JOIN combustible.fact_viaje_combustible cb
        ON cb.id_viaje_fuente = cy.id_viaje AND cb.es_virtual = 'S'
      LEFT JOIN tarificador.fact_precio_combustible_tarificador pr ON pr.sk_fecha = cy.sk_fecha
      LEFT JOIN LATERAL (
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY b.rendimiento_viajes) AS rendimiento_camion
        FROM (SELECT rendimiento_viajes
              FROM combustible.fact_bloque_combustible
              WHERE equipo_id = cy.equipo_id AND calidad = 'ok'
                AND rendimiento_viajes BETWEEN 1.2 AND 5.5
                AND termino < to_date(cy.sk_fecha::text, 'YYYYMMDD')
              ORDER BY termino DESC, bloque DESC LIMIT 5) b
      ) r ON TRUE
      WHERE cy.es_virtual = 'S'
    """)
    contexto=con.df(CONSULTA_CONTEXTO)
    return datos.merge(contexto,on='id_viaje',how='left',validate='one_to_one')


def costos_virtuales(vt, viajes):
    """Detalle auditable de virtuales en 3/4 con procedencia y contexto explícitos.

    Escenarios 3/4: mismos litros certificados y valorización; solo difiere el costo fijo.
    Termo = 0 por ser vacío. Otros componentes solo se modelan con vecinos reales
    propios concordantes y parámetros vigentes; días por km, no duración observada.
    Si cambia rampla/operador o falta tramo, el dato se conserva pendiente.
    """
    vt = vt.copy()
    if vt["id_viaje"].duplicated().any():
        raise ValueError("Virtual repetido: revisar la unión con combustible")
    for c in ("km", "litros_asignados", "costo_combustible", "rendimiento_camion", "precio_ponderado_real"):
        vt[c] = pd.to_numeric(vt[c], errors="coerce")
    estado_tramo = estado_tramo_virtual(vt)
    adicionales = componentes_adicionales(vt)
    tramo_pendiente = estado_tramo.ne('disponible')
    propios = set(viajes.loc[viajes["tipo_operador"] == "propio", "nro_viaje"].astype(str))
    destino = pd.to_numeric(vt["viaje_principal"].where(vt["viaje_principal"].astype(str).isin(propios)),
                            errors="coerce").astype("Int64")
    filas = []
    for esc in (3, 4):
        litros = vt["litros_asignados"]
        importe = vt["costo_combustible"]
        for nombre in ("combustible", "termo", "peaje", "costo_fijo", "reposicion", "neumaticos"):
            x = vt[["id_viaje", "ciclo_comercial", "sk_fecha"]].copy()
            x["nro_viaje_destino"] = destino
            x["estado_tramo"] = estado_tramo
            x["estado_contexto"] = adicionales.estado_contexto
            x["origen_calculo"] = 'pendiente de datos'
            for bolsa in ('flota','rampla','estructura'):
                x[f'cf_{bolsa}_virtual'] = np.nan
            x["sk_escenario"], x["sk_componente"] = esc, SK_COMP[nombre]
            x["cantidad"], x["tasa"], x["monto"] = np.nan, np.nan, np.nan
            x["estado"] = "sin dato: componente virtual pendiente"
            if nombre == "combustible":
                x['origen_calculo'] = 'litros certificados P7; ajuste contable separado'
                x["cantidad"], x["monto"] = litros, importe
                x["tasa"] = importe / litros.where(litros > 0)
                x["estado"] = np.where(np.isfinite(importe), "ok", "sin dato: combustible virtual")
                x.loc[~np.isfinite(importe), "monto"] = np.nan
                # Una ausencia de tramo no se convierte en consumo gratuito.
                # Los datos crudos se conservan en combustible; aquí no se
                # publica como costo fiable la asignación dependiente de esos km.
                x.loc[tramo_pendiente,["cantidad","tasa","monto"]] = np.nan
                x.loc[tramo_pendiente,"estado"] = 'sin dato: tramo pendiente de revision'
            elif nombre == "termo":
                x[["cantidad", "tasa", "monto"]] = 0.0
                x["estado"] = "ok"
                x['origen_calculo'] = 'no aplica: movimiento vacio'
            else:
                for c in ('monto','cantidad','tasa','estado'):
                    x[c] = adicionales[f'{c}_{nombre}']
                x['origen_calculo'] = ('peaje referencial completo vigente; contexto propio inferido'
                    if nombre=='peaje' else 'parametro vigente del metodo propuesto; contexto propio inferido')
                if nombre=='costo_fijo':
                    for bolsa in ('flota','rampla','estructura'):
                        x[f'cf_{bolsa}_virtual'] = adicionales[f'cf_{bolsa}_virtual']
            x["estado_asignacion"] = np.where(destino.notna(), "al principal propio", "sin principal propio")
            filas.append(x)
    out = pd.concat(filas, ignore_index=True)
    return out[["id_viaje", "ciclo_comercial", "nro_viaje_destino", "sk_fecha", "sk_escenario", "sk_componente",
                "cantidad", "tasa", "monto", "estado", "estado_asignacion", "estado_tramo",
                "estado_contexto","origen_calculo","cf_flota_virtual","cf_rampla_virtual","cf_estructura_virtual"]]


def resumir(fc, q, v):
    nombres = {k: n for k, n, _ in ESCENARIOS}
    comps = {k: n for k, n, _, _ in COMPONENTES}
    p = fc[fc["nro_viaje"].isin(v.loc[v["tipo_operador"] == "propio", "nro_viaje"])]
    ok = p.groupby("nro_viaje")["estado"].apply(lambda s: (s == "ok").all())
    comp = p[p["nro_viaje"].isin(ok[ok].index)].copy()
    comp["anio"] = (comp["sk_fecha"] // 10000).astype("Int64")
    lin = [f"   viajes propios con escenarios operacionales 3/4 completos: {comp['nro_viaje'].nunique():,} "
           f"(de {p['nro_viaje'].nunique():,} propios)"]
    if len(comp):
        t = comp.pivot_table(index="anio", columns="sk_escenario", values="monto", aggfunc="sum") / 1e6
        t.columns = [nombres[c] for c in t.columns]
        ing = v.set_index("nro_viaje")["ingreso"]
        e1 = comp[(comp["sk_escenario"] == 3) & (comp["sk_componente"] == 1)]
        t.insert(0, "viajes", e1.groupby("anio").size())
        t.insert(1, "ingreso", e1.assign(i=e1["nro_viaje"].map(ing)).groupby("anio")["i"].sum() / 1e6)
        lin += ["   costo por año (MM$; mismos viajes en 3/4):", "   " + t.round(1).to_string().replace("\n", "\n   ")]
        c = comp[comp["anio"] >= 2025].pivot_table(index="sk_componente", columns="sk_escenario", values="monto",
                                                   aggfunc="sum") / 1e6
        c.index = [comps[i] for i in c.index]
        c.columns = [nombres[x] for x in c.columns]
        lin += ["   componentes desde 2025 (MM$):", "   " + c.round(1).to_string().replace("\n", "\n   ")]
    r = fc[(fc["sk_escenario"] == 3) & (fc["sk_componente"] != SK_COMP["pago_tercero"])
           & (pd.to_numeric(fc["sk_fecha"]) >= 20250101)].copy()
    if r["rol_comercial"].notna().any():
        ing = v.set_index("nro_viaje")["ingreso"]
        t = r.groupby(r["rol_comercial"].fillna("sin ciclo")).agg(viajes=("nro_viaje", "nunique"), fisico=("monto", "sum"),
                                                              comercial=("monto_comercial", "sum"))
        t["ingreso"] = r.drop_duplicates("nro_viaje").assign(i=lambda d: d["nro_viaje"].map(ing)).groupby(
            r.drop_duplicates("nro_viaje")["rol_comercial"].fillna("sin ciclo"))["i"].sum()
        for c in ("fisico", "comercial", "ingreso"):
            t[c] = t[c] / 1e6
        t["margen_fisico_%"] = (100 * (t["ingreso"] - t["fisico"]) / t["ingreso"].where(t["ingreso"] > 0)).round(1)
        t["margen_comercial_%"] = (100 * (t["ingreso"] - t["comercial"]) / t["ingreso"].where(t["ingreso"] > 0)).round(1)
        lin += ["   AVISO: subtotales provisionales; incluyen faltantes, mezclan operadores y no suman virtuales.",
                "   Estos porcentajes NO son los márgenes completos de v_costo_viaje_resumen.",
                f"   resumen parcial, escenario 3, desde 2025 (MM$; peso cargado {PESO_CARGADO}):",
                "   " + t.round(1).to_string().replace("\n", "\n   "),
                f"   diferencia del subconjunto mostrado, sin virtuales (no es conciliación completa): "
                f"{r['monto_comercial'].sum() - r['monto'].sum():,.0f}"]
    qq = q[q["estado"] == "ok"].merge(v[["nro_viaje", "ingreso"]], on="nro_viaje")
    qq = qq[pd.to_numeric(qq["sk_fecha"]) >= 20250101]
    if len(qq):
        lin.append(f"   cotización LEGADA de diagnóstico desde 2025 (no exacto): {len(qq):,} viajes · ingreso {qq['ingreso'].sum() / 1e6:,.1f} MM$ · "
                   f"cotización {qq['cotizacion'].sum() / 1e6:,.1f} MM$ · ingreso ÷ cotización "
                   f"{qq['ingreso'].sum() / qq['cotizacion'].sum():.2f}")
    lin.append(f"   estados de la cotización: {q['estado'].value_counts().to_dict()}")
    return "\n".join(lin)


if __name__ == "__main__":
    principal(sys.argv[1:])
