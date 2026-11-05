#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Área VENTAS del MVP Data Warehouse: lo que el viaje vende y lo que se paga por él (ingreso, comisión del conductor
propio, pago al tercero), desde la vista de viajes del ERP (travelvta_v). Escribe solo en el esquema `ventas`
(y su fila en comun.registro_carga); al final rehace comun.v_fact_viaje y comun.v_margen_viaje.

Tablas:
  ventas.fact_viaje_venta      grano: un viaje (c_orderline_id)
  ventas.dim_cliente           vista de rol del maestro dim_comun (sin copia propia)
  ventas.dim_producto_viaje    vista de dim_comun.dim_producto_viaje
  ventas.v_viaje_descriptivo   viaje real con nombres y estado de correspondencia del conductor

Reglas (Costos indirectos.md §5.6, verificadas con los datos):
  - ingreso = `estimado` (= linenetamt de la línea de la orden de venta). En vacíos es 0.
  - operador según `grupo2`: "1." Callegari (flota propia) · "2." Caterina · "3." fletero.
  - comisión del conductor propio = comisionfunc + comisionfuncadic, SOLO si el viaje es de Callegari y el
    conductor es interno (en viajes de terceros comisionfunc trae el pago al tercero).
  - pago al tercero (fletero o Caterina) = comisionflet: paga TODO el servicio (camión, conductor y petróleo);
    porccomisionfle = % del ingreso (75 % / 85 % fleteros; 90 % / 100 % Caterina).
  - travelvta repite el viaje por factura: se deja una fila por c_orderline_id; valorfactura = suma por factura distinta.

Uso (en la carpeta ventas, o lo llama construir_todo.py):
  python construir_dw_ventas.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from comun import db, vista_viaje  # noqa: E402
from dimensiones.preparar_dimensiones_comunes import enteros

VERSION = "2026-10-30-ventas-importes-numericos-v2"
ESQUEMAS = ("ventas", "comun")

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS ventas;
CREATE SCHEMA IF NOT EXISTS comun;
CREATE TABLE IF NOT EXISTS comun.dim_fecha (
  sk_fecha INTEGER PRIMARY KEY, fecha DATE, anio INTEGER, mes INTEGER, dia INTEGER, dia_semana INTEGER,
  nombre_dia TEXT, es_fin_de_semana TEXT);
CREATE TABLE IF NOT EXISTS comun.registro_carga (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, area TEXT, fecha_carga TIMESTAMP, version_script TEXT,
  fecha_cambio TEXT, detalle TEXT);
-- Recrear solo las vistas gestionadas; otras dependencias bloquean la migración.
DROP VIEW IF EXISTS comun.v_margen_viaje;
DROP VIEW IF EXISTS comun.v_fact_viaje;
DROP VIEW IF EXISTS escenarios.v_costo_viaje_resumen;
-- Sustituir copias antiguas por vistas; no eliminar dependencias desconocidas.
DO $$
DECLARE nombre TEXT;
BEGIN
  FOREACH nombre IN ARRAY ARRAY['dim_cliente','dim_producto_viaje'] LOOP
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='ventas' AND c.relname=nombre AND c.relkind='r') THEN
      EXECUTE format('DROP TABLE ventas.%I', nombre);
    END IF;
  END LOOP;
END $$;
CREATE TABLE IF NOT EXISTS ventas.fact_viaje_venta (
  nro_viaje BIGINT PRIMARY KEY,            -- c_orderline_id
  nro_orden TEXT,                          -- documentno de la orden de venta
  sk_fecha INTEGER,                        -- datepromised (AAAAMMDD)
  cliente_id BIGINT,
  producto_id BIGINT,
  sit_stretch_id BIGINT,                   -- tramo del ERP
  equipo_id BIGINT,                        -- tracto (sit_mainteam_id)
  rampla_id BIGINT,                        -- sit_secondaryteam_id
  operador_grupo TEXT,                     -- grupo2 tal como viene
  tipo_operador TEXT,                      -- propio · caterina · fletero · otro
  transportista_id BIGINT,                 -- sit_carrier_id
  conductor_id BIGINT,                    -- sit_driver_v_id; NULL o no encontrado se conserva
  conductor_interno TEXT,                  -- S/N
  es_retorno TEXT, es_tractoreo TEXT, estado_viaje TEXT,    -- isreturn, tractoreo, sitdocstatus
  cantidad DOUBLE PRECISION,
  ingreso DOUBLE PRECISION,                -- estimado (linenetamt)
  valor_orden_venta DOUBLE PRECISION,      -- valorordventa
  precio_cotizado DOUBLE PRECISION,        -- preciocot
  valor_facturado DOUBLE PRECISION,        -- Σ valorfactura por factura distinta (NULL si no está facturado)
  facturas INTEGER,
  comision_conductor DOUBLE PRECISION,     -- conductor propio interno: comisionfunc + comisionfuncadic
  pago_tercero DOUBLE PRECISION,           -- fletero o Caterina: comisionflet
  pct_pago_tercero DOUBLE PRECISION,       -- porccomisionfle
  comision_conductor_observada DOUBLE PRECISION, -- NULL si falta monto principal o conductor interno
  pago_tercero_observado DOUBLE PRECISION);      -- NULL si falta monto; no convertir desconocido en cero
ALTER TABLE ventas.fact_viaje_venta ADD COLUMN IF NOT EXISTS conductor_id BIGINT;
DELETE FROM ventas.fact_viaje_venta;
CREATE INDEX IF NOT EXISTS ix_venta_fecha ON ventas.fact_viaje_venta (sk_fecha);
CREATE INDEX IF NOT EXISTS ix_venta_cliente ON ventas.fact_viaje_venta (cliente_id);
"""

COLS_VTA = ["c_orderline_id", "documentno", "datepromised", "c_bpartner_id", "m_product_id", "sit_stretch_id",
            "sit_mainteam_id", "sit_secondaryteam_id", "grupo2", "sit_carrier_id", "sit_internaldriver_id", "sit_driver_v_id",
            "isreturn", "tractoreo", "sitdocstatus", "cantidad", "estimado", "valorordventa", "preciocot",
            "valorfactura", "docfactura", "comisionfunc", "comisionfuncadic", "comisionflet", "porccomisionfle"]


def num(s):
    return pd.to_numeric(s, errors="coerce")


def ident(s):
    return pd.to_numeric(s, errors="coerce").round().astype("Int64")


def sn(s):
    return s.astype(str).str.strip().str.upper().isin(["Y", "S", "SI", "TRUE", "1"]).map({True: "S", False: "N"})


def buscar(clave, nombres):
    try:
        carpeta = db.ruta_config("fuentes", clave)
    except KeyError:
        return None
    for n in nombres:
        if (carpeta / n).exists():
            return carpeta / n
    return None


def leer_viajes():
    ruta = buscar("erp", ["adempiere_sit_pbix_travelvta_v.parquet", "sit_pbix_travelvta_v.parquet"])
    if ruta is None:
        raise SystemExit("No encuentro travelvta_v en la carpeta [fuentes] erp de config.ini")
    v = pd.read_parquet(ruta)
    minus = {c.lower(): c for c in v.columns}
    if "sit_driver_v_id" not in minus:
        raise ValueError("Falta sit_driver_v_id en travelvta")
    faltan = [c for c in COLS_VTA if c not in minus]
    if faltan:
        print(f"   aviso: {ruta.name} no trae {faltan}")
    v = v[[minus[c] for c in COLS_VTA if c in minus]]
    v.columns = [c.lower() for c in v.columns]
    for c in faltan:
        v[c] = np.nan
    print(f"   {ruta.name}: {len(v):,} filas, {v['c_orderline_id'].nunique():,} viajes")

    v['sit_driver_v_id'] = enteros(v['sit_driver_v_id'])
    if (v.groupby('c_orderline_id')['sit_driver_v_id'].nunique(dropna=False) > 1).any():
        raise ValueError('Conductores contradictorios en filas del mismo viaje')

    # una fila por viaje; facturado = suma de las facturas distintas del viaje
    v["nro_viaje"] = ident(v["c_orderline_id"])
    # La ingesta conserva NUMERIC como texto para no perder precisión en el crudo.
    # Convertir ANTES de deduplicar/agrupar: sum() de textos concatena importes.
    # Un importe realmente inválido bloquea la carga, sin convertirlo a cero/NULL.
    try:
        v['valorfactura']=pd.to_numeric(v['valorfactura'],errors='raise')
    except (ValueError,TypeError) as error:
        raise ValueError('travelvta.valorfactura contiene un importe no numérico; revisar fuente') from error
    fac = v.dropna(subset=["valorfactura"]).drop_duplicates(["nro_viaje", "docfactura", "valorfactura"])
    fac = fac.groupby("nro_viaje").agg(valor_facturado=("valorfactura", "sum"), facturas=("docfactura", "nunique"))
    v = v.drop_duplicates("nro_viaje").merge(fac, left_on="nro_viaje", right_index=True, how="left")
    v = v[v["nro_viaje"].notna()]

    grupo = v["grupo2"].astype(str).str.strip()
    tipo = np.select([grupo.str.startswith("1."), grupo.str.startswith("2."), grupo.str.startswith("3.")],
                     ["propio", "caterina", "fletero"], "otro")
    interno = v["sit_internaldriver_id"].notna()
    propio = tipo == "propio"
    fecha = pd.to_datetime(v["datepromised"], errors="coerce")
    df = pd.DataFrame({
        "nro_viaje": v["nro_viaje"], "nro_orden": v["documentno"].astype(str),
        "sk_fecha": fecha.dt.strftime("%Y%m%d").astype("Int64") if fecha.notna().any() else pd.NA,
        "cliente_id": ident(v["c_bpartner_id"]), "producto_id": ident(v["m_product_id"]),
        "sit_stretch_id": ident(v["sit_stretch_id"]), "equipo_id": ident(v["sit_mainteam_id"]),
        "rampla_id": ident(v["sit_secondaryteam_id"]), "operador_grupo": grupo, "tipo_operador": tipo,
        "transportista_id": ident(v["sit_carrier_id"]), "conductor_id": v["sit_driver_v_id"], "conductor_interno": np.where(interno, "S", "N"),
        "es_retorno": sn(v["isreturn"]), "es_tractoreo": sn(v["tractoreo"]),
        "estado_viaje": v["sitdocstatus"].astype(str), "cantidad": num(v["cantidad"]),
        "ingreso": num(v["estimado"]), "valor_orden_venta": num(v["valorordventa"]),
        "precio_cotizado": num(v["preciocot"]), "valor_facturado": v["valor_facturado"],
        "facturas": v["facturas"].astype("Int64"),
        "comision_conductor": np.where(propio & interno, num(v["comisionfunc"]).fillna(0)
                                       + num(v["comisionfuncadic"]).fillna(0), 0.0),
        "pago_tercero": np.where(~propio, num(v["comisionflet"]).fillna(0), 0.0),
        "pct_pago_tercero": np.where(~propio, num(v["porccomisionfle"]), np.nan),
        "comision_conductor_observada": np.where(propio & interno,
            num(v["comisionfunc"]) + num(v["comisionfuncadic"]).fillna(0), np.nan),
        "pago_tercero_observado": np.where(~propio, num(v["comisionflet"]), np.nan)})
    return df


VISTAS_MAESTRAS = """
CREATE OR REPLACE VIEW ventas.dim_cliente AS
SELECT s.socio_id AS cliente_id,s.razon_social,s.activo_fuente AS activo
FROM dim_comun.dim_socio_negocio s
WHERE EXISTS(SELECT 1 FROM dim_comun.roles_socio r WHERE r.socio_id=s.socio_id AND r.rol='cliente')
   OR EXISTS(SELECT 1 FROM ventas.fact_viaje_venta v WHERE v.cliente_id=s.socio_id)
UNION ALL
SELECT DISTINCT v.cliente_id,NULL::text,NULL::text FROM ventas.fact_viaje_venta v
WHERE v.cliente_id IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM dim_comun.dim_socio_negocio s WHERE s.socio_id=v.cliente_id);
CREATE OR REPLACE VIEW ventas.dim_producto_viaje AS
SELECT producto_id,producto,
 CASE WHEN control_temperatura_fuente IS NULL THEN NULL
      WHEN upper(trim(control_temperatura_fuente)) IN ('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END AS control_temperatura,
 CASE WHEN multicarga_fuente IS NULL THEN NULL
      WHEN upper(trim(multicarga_fuente)) IN ('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END AS multicarga,
 CASE WHEN viaje_vacio_fuente IS NULL THEN NULL
      WHEN upper(trim(viaje_vacio_fuente)) IN ('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END AS viaje_vacio
FROM dim_comun.dim_producto_viaje
UNION ALL
SELECT DISTINCT v.producto_id,NULL::text,NULL::text,NULL::text,NULL::text
FROM ventas.fact_viaje_venta v WHERE v.producto_id IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM dim_comun.dim_producto_viaje p WHERE p.producto_id=v.producto_id);
CREATE OR REPLACE VIEW ventas.v_viaje_descriptivo AS
SELECT v.*,c.nombre_completo AS conductor_nombre,
 CASE WHEN v.conductor_id IS NULL THEN 'sin_id'
      WHEN c.conductor_id IS NULL THEN 'sin_catalogo' ELSE 'encontrado' END AS estado_conductor,
 cl.razon_social AS cliente_nombre,t.razon_social AS transportista_nombre,
 e.nombre_fuente AS tracto_nombre,r.nombre_fuente AS rampla_nombre,p.producto AS producto_nombre
FROM ventas.fact_viaje_venta v
LEFT JOIN dim_comun.dim_conductor c ON c.conductor_id=v.conductor_id
LEFT JOIN dim_comun.dim_socio_negocio cl ON cl.socio_id=v.cliente_id
LEFT JOIN dim_comun.dim_socio_negocio t ON t.socio_id=v.transportista_id
LEFT JOIN dim_comun.dim_equipo e ON e.equipo_id=v.equipo_id
LEFT JOIN dim_comun.dim_equipo r ON r.equipo_id=v.rampla_id
LEFT JOIN dim_comun.dim_producto_viaje p ON p.producto_id=v.producto_id;
"""


def principal(argv=None, _conn=None):
    print(f"construir_dw_ventas.py versión {VERSION}")
    viajes = leer_viajes()
    con = db.conectar(ESQUEMAS, _conn=_conn)
    with con:
        for tabla in ('dim_socio_negocio','roles_socio','dim_producto_viaje','dim_equipo','dim_conductor'):
            if not con.existe_tabla('dim_comun', tabla):
                raise RuntimeError(f'Falta dim_comun.{tabla}; cargar maestros primero')
        resumen_anterior = None
        if con.existe_tabla('escenarios','v_costo_viaje_resumen'):
            resumen_anterior = con.uno("SELECT pg_get_viewdef('escenarios.v_costo_viaje_resumen'::regclass,true)")[0]
        con.execute(ESQUEMA)
        con.copiar_df("ventas.fact_viaje_venta", viajes)
        con.execute(VISTAS_MAESTRAS)
        if con.uno('SELECT count(*) FROM ventas.v_viaje_descriptivo')[0] != len(viajes):
            raise RuntimeError('La unión de maestros multiplicó viajes')
        if resumen_anterior:
            con.execute('CREATE VIEW escenarios.v_costo_viaje_resumen AS ' + resumen_anterior)
        res = con.df("""SELECT tipo_operador, COUNT(*) AS viajes, SUM(ingreso) AS ingreso,
                               SUM(comision_conductor) AS comision, SUM(pago_tercero) AS pago_tercero,
                               SUM(CASE WHEN ingreso = 0 THEN 1 ELSE 0 END) AS viajes_ingreso_0
                        FROM ventas.fact_viaje_venta GROUP BY 1 ORDER BY 2 DESC""")
        detalle = (f"viajes {len(viajes)}; maestros dim_comun; conductor desde travelvta; "
                   + "; ".join(f"{r.tipo_operador} {int(r.viajes)}" for r in res.itertuples()))
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("ventas", db.ahora(), VERSION, "", detalle))
        vista = vista_viaje.crear(con)
    for t in ("ventas.dim_cliente", "ventas.dim_producto_viaje", "ventas.fact_viaje_venta"):
        print(f"   {t:30s} {con.uno(f'SELECT COUNT(*) FROM {t}')[0]:7d}")
    for c in ("ingreso", "comision", "pago_tercero"):
        res[c] = (res[c].astype(float) / 1e6).round(1)
    print("   por operador (MM$, todo el periodo):\n   " + res.to_string(index=False).replace("\n", "\n   "))
    print(f"   vistas comun.v_fact_viaje y comun.v_margen_viaje: {'actualizadas' if vista else 'no (falta peajes)'}")
    con.close()


if __name__ == "__main__":
    principal(sys.argv[1:])
