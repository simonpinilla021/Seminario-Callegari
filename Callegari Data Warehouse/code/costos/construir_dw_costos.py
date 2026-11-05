#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga los costos indirectos (costo fijo, reposición de flota y neumáticos) en el esquema `costos` de la base PostgreSQL
del MVP, con dos métodos lado a lado (Modelo dimensional §6.3; Costos indirectos §5.8–5.14 y §6.6):
  - tarificador: réplica de la planilla del gerente (una cuota diaria igual para todo viaje propio).
  - propuesto:   el costo fijo se separa en bolsas (flota propia, rampla, estructura común, directo fleteros) por centro
                 de costo y cuenta; cada tipo de viaje paga solo las bolsas que usa, con cuotas calculadas sobre la
                 capacidad práctica (equipos × 300 días). Lo que los viajes no absorben queda como capacidad ociosa.

Solo escribe en el esquema costos (y agrega su fila a comun.registro_carga). Al final rehace comun.v_fact_viaje.

  dim_cuenta_contable           una cuenta de resultado con su clasificación: clase_tarificador (regla del gerente),
                                clase_real (para el margen real) e inductor. Reglas en este script; las filas de
                                cuentas_clasificacion_manual.csv ganan sobre las reglas.
  fact_contabilidad_mensual     mes × cuenta × unidad de negocio × centro de costo, SIN asientos de cierre
                                (gl_category_id <> 1000096). La UN es 1 a 1 con el equipo propio; la UN "Fleteros" no.
  parametro_costo               parámetros con vigencia, método y ámbito. Los componentes vienen de
                                parametros_costo_manual.csv; los calculados (masa y costo fijo diario, reposición diaria,
                                costo de neumático por km) los arma este script con sus componentes como padre.
  dim_centro_costo              centro de costo (c_activity_id) con su bolsa y rol (bolsa_costo_manual.csv).
  fact_viaje_costo_indirecto    un viaje × método: días, km y costo fijo, reposición y neumáticos asignados con las tasas
                                vigentes a la fecha del viaje. Tarificador: solo flota propia (terceros en 0).
                                Propuesto: también fleteros y Caterina (estructura + rampla de Callegari).
  fact_capacidad_bolsa          año × bolsa (método propuesto): costo de la bolsa, capacidad, absorbido por los viajes y
                                capacidad ociosa.

Regla del costo fijo (gerente, reunión 20-10-2026): todo el balance es fijo salvo combustible (petróleo + impuesto
específico), peajes, neumáticos y depreciación (va aparte como reposición); fuera también corrección monetaria,
pérdida por venta de activo fijo, arriendo a terceros (pago a fleteros) e ingresos. Se usa el año contable anterior:
la masa del año A−1 rige para los viajes del año A, más los ajustes manuales vigentes.

Método propuesto: bolsas por centro de costo y cuenta (bolsa_costo_manual.csv); los mixtos (talleres, equipos,
aportes de remuneración, seguro) se reparten con proporciones calculadas cada año con los datos (o el valor por defecto
de parametros_costo_manual.csv). Misma ventana que el tarificador (año contable anterior).

Se recalcula completo en cada ejecución.

Uso (en la carpeta MVP Data Warehouse/costos):
  python construir_dw_costos.py
Fuentes (config.ini, sección [fuentes]):
  contabilidad = carpeta con rv_fact_acct.parquet (por defecto fuentes/contabilidad_2026-03-24)
  erp          = carpeta con adempiere_sit_pbix_travelvta_v.parquet y adempiere_sit_pbix_traveltramo_v.parquet
  combustible  = carpeta con sit_pbix_travelveh_v.parquet (equipo ↔ unidad de negocio)
"""
import argparse
import csv
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))       # carpeta MVP Data Warehouse (comun/db.py)
from comun import db, vista_viaje                                      # noqa: E402

VERSION = "2026-10-30-pg-contabilidad-importes-numericos-v2"
CUENTAS_COMISION_DIRECTA = {"310020104"}
AQUI = Path(__file__).resolve().parent
CLASIF_MANUAL = AQUI / "cuentas_clasificacion_manual.csv"
PARAM_MANUAL = AQUI / "parametros_costo_manual.csv"
BOLSA_MANUAL = AQUI / "bolsa_costo_manual.csv"
BOLSAS = ("flota", "rampla", "estructura", "directo_fleteros")
MIXTOS = ("mixto_talleres", "mixto_equipos", "mixto_aportes", "mixto_seguros")
ESQUEMAS = ("costos", "comun")
CLIENTE = 1000007
CAT_CIERRE = 1000096
FIN = date(9999, 12, 31)
INICIO = date(1900, 1, 1)

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS comun;
CREATE SCHEMA IF NOT EXISTS costos;
CREATE TABLE IF NOT EXISTS comun.dim_fecha (
  sk_fecha INTEGER PRIMARY KEY, fecha DATE UNIQUE, anio INTEGER, mes INTEGER, dia INTEGER, dia_semana INTEGER,
  nombre_dia TEXT, es_fin_de_semana TEXT);
CREATE TABLE IF NOT EXISTS comun.registro_carga (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, area TEXT, fecha_carga TIMESTAMP, version_script TEXT,
  fecha_cambio TEXT, detalle TEXT);

DROP VIEW IF EXISTS comun.v_fact_viaje CASCADE;      -- CASCADE: v_margen_viaje depende de ella (se rehacen al final)
DROP VIEW IF EXISTS costos.v_cobertura_referencias_mensual;
DROP VIEW IF EXISTS costos.v_referencias_contabilidad;
DROP VIEW IF EXISTS costos.v_ref_centro_contable;
DROP VIEW IF EXISTS costos.v_ref_unidad_negocio;
DROP VIEW IF EXISTS costos.v_reglas_centro_costo;
DROP TABLE IF EXISTS fact_viaje_costo_indirecto, parametro_costo, fact_contabilidad_mensual,
  dim_centro_costo, dim_cuenta_contable;

CREATE TABLE dim_cuenta_contable (
  sk_cuenta INTEGER PRIMARY KEY,
  codigo TEXT UNIQUE,              -- accountvalue
  nombre TEXT,
  tipo TEXT,                       -- accounttype: R ingreso, E gasto
  linea_eerr TEXT,                 -- incstatgroup
  nombre_linea_eerr TEXT,
  clase_tarificador TEXT,          -- fijo · combustible · peaje · neumatico · depreciacion · excluida · ingreso
  clase_real TEXT,                 -- estructura · mantencion · financiero · ajuste_inventario · directo_viaje · combustible ...
  inductor TEXT,                   -- dia · km · tramo · viaje · ninguno (de la clase_tarificador)
  origen_clasificacion TEXT,       -- regla · manual
  nota TEXT);

CREATE TABLE dim_centro_costo (
  c_activity_id BIGINT PRIMARY KEY,
  nombre TEXT,                     -- value - name (sit_pbix_segactividad_v o bolsa_costo_manual.csv)
  bolsa TEXT,                      -- flota · rampla · estructura · directo_fleteros · mixto_talleres · (vacío = por cuenta)
  rol TEXT,                        -- conductores · tracto · rampla (para calcular las proporciones de los mixtos)
  origen TEXT);                    -- maestro · manual

CREATE TABLE fact_contabilidad_mensual (
  sk_fecha_mes INTEGER REFERENCES comun.dim_fecha,   -- primer día del mes (AAAAMM01)
  anio INTEGER, mes INTEGER,
  sk_cuenta INTEGER REFERENCES dim_cuenta_contable,
  unidad_negocio_id BIGINT,        -- userelement2_id; NULL si el asiento no tiene UN
  equipo_id BIGINT,                -- a_asset_id cuando la UN es de un solo equipo (flota propia); NULL si no
  es_un_fleteros TEXT,             -- S: UN genérica de fleteros (varios equipos de terceros)
  c_activity_id BIGINT,            -- centro de costo
  monto NUMERIC(16,2),             -- amtacct (gasto +, ingreso −), sin asientos de cierre
  n_lineas INTEGER,
  bolsa TEXT);                     -- método propuesto (solo cuentas de costo fijo): flota · rampla · estructura ·
                                   -- directo_fleteros · mixto_talleres · mixto_equipos · mixto_aportes · mixto_seguros
CREATE INDEX ix_contab_cuenta ON fact_contabilidad_mensual (sk_cuenta, anio);

CREATE TABLE parametro_costo (
  parametro TEXT, metodo TEXT, ambito TEXT,
  valor NUMERIC(18,6), unidad TEXT,
  vigente_desde DATE, vigente_hasta DATE,         -- hasta exclusiva; 9999-12-31 = sigue vigente
  parametro_padre TEXT,                           -- parámetro calculado del que es componente
  origen TEXT,                                    -- contabilidad · tarificador · externo · manual · calculado
  fuente_detalle TEXT,
  monto_base NUMERIC(18,2), cantidad_inductor NUMERIC(18,4),   -- solo en los calculados
  nota TEXT,
  PRIMARY KEY (parametro, metodo, ambito, vigente_desde));

CREATE TABLE fact_viaje_costo_indirecto (
  nro_viaje BIGINT,                -- c_orderline_id
  metodo TEXT,                     -- tarificador · propuesto
  sk_fecha_inicio INTEGER REFERENCES comun.dim_fecha,   -- misma fecha que peajes (datepromised)
  sit_stretch_id BIGINT,
  equipo_id BIGINT,                -- tracto (sit_mainteam_id)
  rampla_id BIGINT,                -- sit_secondaryteam_id
  operador TEXT,                   -- grupo2
  es_flota_propia TEXT,            -- S: Transportes Callegari; N: Caterina y fleteros
  es_tractoreo TEXT,               -- S: solo tracto (neumáticos y reposición del ámbito tractoreo)
  usa_rampla_callegari TEXT,       -- S: la rampla es de Callegari (paga la bolsa rampla aunque el tracto sea de un tercero)
  km_tramo DOUBLE PRECISION,       -- km de la tabla de tramos
  dias_viaje DOUBLE PRECISION,     -- km ÷ km_por_dia (duración fraccionaria)
  costo_fijo_asignado DOUBLE PRECISION,
  cf_flota DOUBLE PRECISION,       -- componentes del costo fijo (método propuesto)
  cf_rampla DOUBLE PRECISION,
  cf_estructura DOUBLE PRECISION,
  cf_directo_fleteros DOUBLE PRECISION,
  reposicion_asignada DOUBLE PRECISION,
  costo_neumatico_asignado DOUBLE PRECISION,
  costo_indirecto_total DOUBLE PRECISION,
  tasa_costo_fijo_aplicada DOUBLE PRECISION,   -- auditoría (no se suma): $/día aplicado
  tasa_reposicion_aplicada DOUBLE PRECISION,   -- auditoría
  tasa_neumatico_aplicada DOUBLE PRECISION,    -- auditoría
  km_por_dia_aplicado DOUBLE PRECISION,        -- auditoría
  estado_costo_indirecto TEXT,     -- ok · tercero · sin año base · sin km
  PRIMARY KEY (nro_viaje, metodo));

-- Conservar identidad y dependencias (escenarios.v_capacidad_con_virtuales).
-- La transacción protege el reemplazo completo de sus filas.
CREATE TABLE IF NOT EXISTS fact_capacidad_bolsa (
  anio INTEGER, bolsa TEXT,        -- año de los viajes; bolsa del método propuesto
  monto_bolsa NUMERIC(18,2),       -- costo de la bolsa que rige ese año (año base anterior + ajustes, prorrateado)
  capacidad_dias NUMERIC(18,2),    -- equipos × 300 días (directo fleteros: días-viaje de fleteros del año base)
  cuota_media NUMERIC(18,2),
  dias_usados NUMERIC(18,2),       -- días-viaje que pagaron la bolsa
  absorbido NUMERIC(18,2),         -- Σ asignado a viajes
  capacidad_ociosa NUMERIC(18,2),  -- monto − absorbido
  pct_absorbido NUMERIC(8,4),
  PRIMARY KEY (anio, bolsa));
DELETE FROM fact_capacidad_bolsa;
"""

# ------------------------------------------------------------------------------------------------ clasificación
MANTENCION = {"310010102", "310010103", "310010104", "310010105", "310010107", "310010109", "310010110"}
DIRECTO_VIAJE = {"310020104": "comisión del viaje", "310020105": "tiempos de espera",
                 "310060102": "viáticos de hoja de ruta", "310060101": "gastos de hoja de ruta"}


def clasificar(codigo, tipo):
    """Devuelve (clase_tarificador, clase_real, inductor, nota) según la regla del gerente (20-10-2026)."""
    c = str(codigo)
    if c.startswith("4") or tipo == "R":
        return "ingreso", "ingreso", "ninguno", ""
    if c in ("310010101", "310070102"):
        return "combustible", "combustible", "km", "Petróleo e impuesto específico: variable del tarificador"
    if c == "310010106":
        return "neumatico", "neumatico", "km", "Variable del tarificador (parámetro por km)"
    if c in ("310060104", "310050304"):
        return "peaje", "peaje", "tramo", "Variable del tarificador (tabla por ruta)"
    if c.startswith("32003"):
        return "depreciacion", "depreciacion", "ninguno", "Va aparte como reposición de flota"
    if c.startswith("32005"):
        return "excluida", "excluida", "ninguno", "Corrección monetaria: no operacional"
    if c == "320010104":
        return "excluida", "excluida", "ninguno", "Pérdida por venta de activo fijo: extraordinaria"
    if c == "310050403":
        return "excluida", "directo_tercero", "ninguno", "Arriendo a terceros: pago a fleteros"
    if c.startswith("3"):
        if c in DIRECTO_VIAJE:
            return "fijo", "directo_viaje", "dia", f"Fijo para el tarificador; {DIRECTO_VIAJE[c]} se conoce por viaje"
        if c in MANTENCION:
            return "fijo", "mantencion", "dia", ""
        if c in ("310010112", "310010113"):
            return "fijo", "ajuste_inventario", "dia", "Ajuste de inventario (el gerente: ajustes = fijo)"
        if c.startswith("31008") or c.startswith("3200"):
            return "fijo", "financiero", "dia", "Financieros, multas, donaciones, ajustes: fijo según el gerente"
        return "fijo", "estructura", "dia", ""
    return "excluida", "excluida", "ninguno", "Cuenta no de gasto"


# ------------------------------------------------------------------------------------------------ utilidades
def cargar_fechas(con):
    """Delegar calendario al servicio común; no cambia fechas de hechos."""
    from comun import calendario
    calendario.publicar(con, calendario.generar(date(2018, 1, 1), date(2030, 12, 31))[calendario.COLUMNAS])


def ruta_fuente(clave, defecto):
    try:
        return db.ruta_config("fuentes", clave)
    except KeyError:
        return (db.MVP / defecto).resolve()


def buscar(carpeta, nombres):
    for n in nombres:
        if (carpeta / n).exists():
            return carpeta / n
    return None


def leer_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return pd.DataFrame(list(csv.DictReader(fh)))


def a_fecha(s, defecto):
    s = str(s or "").strip()
    return date.fromisoformat(s) if s else defecto


# ------------------------------------------------------------------------------------------------ contabilidad
def leer_contabilidad(ruta):
    cols = ["ad_client_id", "dateacct", "accountvalue", "name", "accounttype", "incstatgroup", "incstatname", "amtacct",
            "userelement2_id", "c_activity_id", "gl_category_id", "postingtype"]
    fa = pd.read_parquet(ruta, columns=cols)
    fa = fa[pd.to_numeric(fa["ad_client_id"], errors="coerce") == CLIENTE]
    fa = fa[fa["postingtype"].astype(str) == "A"]
    n_cierre = int((pd.to_numeric(fa["gl_category_id"], errors="coerce") == CAT_CIERRE).sum())
    fa = fa[pd.to_numeric(fa["gl_category_id"], errors="coerce").fillna(0) != CAT_CIERRE]
    fa["incstatgroup"] = fa["incstatgroup"].astype(str).str.replace(".0", "", regex=False).str.zfill(3)
    fa["accountvalue"] = fa["accountvalue"].astype(str).str.replace(".0", "", regex=False)
    # Cuentas de resultado = código 3xxxxxxxx (gasto) o 4xxxxxxxx (ingreso). No se filtra por incstatgroup: hay cuentas
    # de gasto sin línea del EERR ('000', p. ej. 310020122 Ley 21735 y 310030104 Alojamiento) que el gerente sí suma.
    fa = fa[fa["accountvalue"].str.len().eq(9) & fa["accountvalue"].str[0].isin(["3", "4"])].copy()
    fa["dateacct"] = pd.to_datetime(fa["dateacct"], errors="coerce")
    # NUMERIC se conserva como texto en el Parquet crudo; sumar como número,
    # nunca concatenar los movimientos antes de calcular los costos anuales.
    try:
        fa['amtacct']=pd.to_numeric(fa['amtacct'],errors='raise')
    except (ValueError,TypeError) as error:
        raise ValueError('rv_fact_acct.amtacct contiene un importe no numérico; revisar fuente') from error
    return fa, n_cierre


def cargar_cuentas(con, fa):
    cu = (fa.groupby("accountvalue").agg(nombre=("name", "first"), tipo=("accounttype", "first"),
                                         linea_eerr=("incstatgroup", "first"), nombre_linea_eerr=("incstatname", "first"))
            .reset_index().rename(columns={"accountvalue": "codigo"}).sort_values("codigo"))
    reglas = [clasificar(c, t) for c, t in zip(cu["codigo"], cu["tipo"])]
    cu["clase_tarificador"], cu["clase_real"], cu["inductor"], cu["nota"] = zip(*reglas)
    cu["origen_clasificacion"] = "regla"
    n_man = 0
    if CLASIF_MANUAL.exists():
        man = leer_csv(CLASIF_MANUAL)
        for r in man.itertuples():
            m = cu["codigo"] == str(r.codigo).strip()
            if not m.any():
                continue
            for campo in ("clase_tarificador", "clase_real", "inductor", "nota"):
                v = str(getattr(r, campo, "") or "").strip()
                if v:
                    cu.loc[m, campo] = v
            cu.loc[m, "origen_clasificacion"] = "manual"
            n_man += 1
    else:
        CLASIF_MANUAL.write_text("codigo,clase_tarificador,clase_real,inductor,nota\n", encoding="utf-8-sig")
    cu = cu.reset_index(drop=True)
    cu["sk_cuenta"] = cu.index + 1
    con.copiar_df("dim_cuenta_contable", cu[["sk_cuenta", "codigo", "nombre", "tipo", "linea_eerr", "nombre_linea_eerr",
                                            "clase_tarificador", "clase_real", "inductor", "origen_clasificacion", "nota"]])
    return cu, n_man


def mapa_un_equipo(ruta_veh):
    """UN → equipo cuando la UN tiene un solo equipo (flota propia). La UN con varios equipos es la de fleteros."""
    if ruta_veh is None:
        return {}, set()
    veh = pd.read_parquet(ruta_veh, columns=["a_asset_id", "sit_businessunit_id"]).dropna(subset=["sit_businessunit_id"])
    veh["sit_businessunit_id"] = veh["sit_businessunit_id"].astype("int64")
    veh["a_asset_id"] = veh["a_asset_id"].astype("int64")
    n = veh.groupby("sit_businessunit_id")["a_asset_id"].nunique()
    unicas = veh[veh["sit_businessunit_id"].isin(n[n == 1].index)]
    return dict(zip(unicas["sit_businessunit_id"], unicas["a_asset_id"])), set(n[n > 1].index)


def leer_bolsas():
    """Reglas del método propuesto (bolsa_costo_manual.csv). Devuelve (centros, por_cuenta, sin_centro).
    tipo = centro            → clave c_activity_id; bolsa vacía = se decide por cuenta
    tipo = cuenta            → clave código de cuenta (o prefijo con *): gana sobre el centro
    tipo = cuenta_sin_centro → se aplica cuando el centro no está en la tabla o tiene bolsa vacía"""
    if not BOLSA_MANUAL.exists():
        sys.exit(f"Falta {BOLSA_MANUAL}: reglas de las bolsas del método propuesto.")
    b = leer_csv(BOLSA_MANUAL).fillna("")
    centros = {int(float(r.clave)): (r.bolsa.strip(), r.rol.strip(), r.nombre.strip())
               for r in b[b["tipo"] == "centro"].itertuples()}
    por_cuenta = [(r.clave.strip(), r.bolsa.strip()) for r in b[b["tipo"] == "cuenta"].itertuples()]
    sin_centro = [(r.clave.strip(), r.bolsa.strip()) for r in b[b["tipo"] == "cuenta_sin_centro"].itertuples()]
    return centros, por_cuenta, sin_centro


def _calza(codigo, patron):
    return codigo.startswith(patron[:-1]) if patron.endswith("*") else codigo == patron


def bolsa_de(codigo, act, reglas):
    centros, por_cuenta, sin_centro = reglas
    for pat, bol in por_cuenta:
        if _calza(codigo, pat):
            return bol
    if act is not None and centros.get(act, ("",))[0]:
        return centros[act][0]
    for pat, bol in sin_centro:
        if _calza(codigo, pat):
            return bol
    return "estructura"


def cargar_centros(con, ruta_act, reglas):
    """dim_centro_costo: maestro de centros (si está) + nombres, bolsa y rol de bolsa_costo_manual.csv."""
    centros = reglas[0]
    filas = {}
    if ruta_act is not None:
        m = pd.read_parquet(ruta_act)
        idc = next((c for c in m.columns if "activity" in c.lower() and c.lower().endswith("_id")), None)
        if idc:
            nom = (m["value"].astype(str) + " - " if "value" in m.columns else "") + \
                  (m["name"].astype(str) if "name" in m.columns else "")
            for i, n in zip(pd.to_numeric(m[idc], errors="coerce"), nom):
                if pd.notna(i):
                    filas[int(i)] = [n, "", "", "maestro"]
    for i, (bol, rol, nom) in centros.items():
        f = filas.setdefault(i, [nom, "", "", "manual"])
        f[0] = f[0] or nom
        f[1], f[2] = bol, rol
    df = pd.DataFrame([[i] + v for i, v in sorted(filas.items())],
                      columns=["c_activity_id", "nombre", "bolsa", "rol", "origen"])
    con.copiar_df("dim_centro_costo", df)
    return len(df)


def cargar_contabilidad(con, fa, cu, un_equipo, un_compartidas, reglas):
    sk = dict(zip(cu["codigo"], cu["sk_cuenta"]))
    fa = fa.assign(anio=fa["dateacct"].dt.year, mes=fa["dateacct"].dt.month,
                   un=pd.to_numeric(fa["userelement2_id"], errors="coerce").astype("Int64"),
                   act=pd.to_numeric(fa["c_activity_id"], errors="coerce").astype("Int64"))
    g = (fa.groupby(["anio", "mes", "accountvalue", "un", "act"], dropna=False)
           .agg(monto=("amtacct", "sum"), n_lineas=("amtacct", "size")).reset_index())
    g = g[g["anio"].notna()]
    out = pd.DataFrame({
        "sk_fecha_mes": (g["anio"].astype(int) * 10000 + g["mes"].astype(int) * 100 + 1),
        "anio": g["anio"].astype(int), "mes": g["mes"].astype(int), "sk_cuenta": g["accountvalue"].map(sk),
        "unidad_negocio_id": g["un"], "equipo_id": g["un"].map(lambda u: un_equipo.get(int(u)) if pd.notna(u) else None),
        "es_un_fleteros": g["un"].map(lambda u: "S" if pd.notna(u) and int(u) in un_compartidas else "N"),
        "c_activity_id": g["act"], "monto": g["monto"].round(2), "n_lineas": g["n_lineas"]})
    out["equipo_id"] = pd.to_numeric(out["equipo_id"], errors="coerce").astype("Int64")
    fijo = set(cu.loc[cu["clase_tarificador"] == "fijo", "codigo"])
    pares = {(c, a) for c, a in zip(g["accountvalue"], g["act"]) if c in fijo}
    mapa = {(c, a): bolsa_de(c, None if pd.isna(a) else int(a), reglas) for c, a in pares}
    out["bolsa"] = [mapa.get((c, a)) if c in fijo else None for c, a in zip(g["accountvalue"], g["act"])]
    out["codigo"] = g["accountvalue"].values
    out_db = out.drop(columns=["codigo"])
    con.copiar_df("fact_contabilidad_mensual", out_db)
    return out


# ------------------------------------------------------------------------------------------------ parámetros
def leer_parametros_manual():
    if not PARAM_MANUAL.exists():
        sys.exit(f"Falta {PARAM_MANUAL}: es el archivo de parámetros del tarificador (no se genera solo).")
    p = leer_csv(PARAM_MANUAL)
    p["valor"] = pd.to_numeric(p["valor"])
    p["desde"] = p["vigente_desde"].map(lambda s: a_fecha(s, INICIO))
    p["hasta"] = p["vigente_hasta"].map(lambda s: a_fecha(s, FIN))
    p["ambito"] = p["ambito"].replace("", "todos")
    p["metodo"] = p["metodo"].replace("", "tarificador")
    return p


def valor_en(p, parametro, fecha, ambito="todos", metodo="tarificador"):
    """Valor vigente en la fecha: primero el del ámbito pedido; si no hay, el de 'todos'."""
    for amb in ([ambito, "todos"] if ambito != "todos" else ["todos"]):
        m = p[(p["parametro"] == parametro) & (p["ambito"] == amb) & (p["metodo"] == metodo)
              & (p["desde"] <= fecha) & (fecha < p["hasta"])]
        if len(m):
            return float(m["valor"].iloc[0])
    return None


def suma_en(p, parametro, fecha, metodo="tarificador"):
    m = p[(p["parametro"] == parametro) & (p["metodo"] == metodo) & (p["desde"] <= fecha) & (fecha < p["hasta"])]
    return float(m["valor"].sum())


def cortes(p, parametros, extra=()):
    c = {INICIO}
    for r in p[p["parametro"].isin(parametros)].itertuples():
        c.update([r.desde, r.hasta])
    c.update(extra)
    return sorted(x for x in c if x < FIN)


def fila(parametro, ambito, valor, unidad, desde, hasta, origen, fuente, padre="", base=None, inductor=None, nota="",
         metodo="tarificador"):
    return dict(parametro=parametro, metodo=metodo, ambito=ambito, valor=valor, unidad=unidad, vigente_desde=desde,
                vigente_hasta=hasta, parametro_padre=padre, origen=origen, fuente_detalle=fuente, monto_base=base,
                cantidad_inductor=inductor, nota=nota)


def calcular_parametros(p, masa_anual, anios_completos):
    """Arma los parámetros calculados con sus componentes. Devuelve lista de filas (dict)."""
    filas = []
    # componentes tal como vienen del archivo manual
    for r in p.itertuples():
        filas.append(dict(parametro=r.parametro, metodo=r.metodo, ambito=r.ambito, valor=r.valor, unidad=r.unidad,
                          vigente_desde=r.desde, vigente_hasta=r.hasta, parametro_padre=r.parametro_padre,
                          origen=r.origen, fuente_detalle=r.fuente_detalle, monto_base=None, cantidad_inductor=None,
                          nota=r.nota))

    # masa contable por año (componente del costo fijo; rige para el año siguiente)
    for anio in anios_completos:
        hasta_m = FIN if anio == anios_completos[-1] else date(anio + 2, 1, 1)
        filas.append(fila("masa_costo_fijo", "todos", masa_anual[anio], "CLP/año", date(anio + 1, 1, 1), hasta_m,
                          "contabilidad", f"Σ cuentas clase_tarificador = fijo, año contable {anio} (sin cierres)",
                          padre="costo_fijo_diario", nota=f"Año base {anio} → viajes de {anio + 1}"))

    # costo fijo diario: (masa del año anterior + ajustes vigentes) ÷ divisor ÷ meses ÷ días
    anios_aplic = [a + 1 for a in anios_completos]
    extra = [date(a, 1, 1) for a in anios_aplic] + [date(anios_aplic[-1] + 1, 1, 1)] if anios_aplic else []
    cs = cortes(p, ["ajuste_manual_costo_fijo", "divisor_flota", "meses_anio", "dias_mes"], extra)
    for i, d in enumerate(cs):
        if d.year not in anios_aplic:
            continue
        hasta = cs[i + 1] if i + 1 < len(cs) else FIN
        if d.year == anios_aplic[-1] and hasta == date(d.year + 1, 1, 1):
            hasta = FIN                                    # el último año base sigue vigente hasta que haya otro
        masa = masa_anual[d.year - 1]
        ajuste = suma_en(p, "ajuste_manual_costo_fijo", d)
        div, meses, dias = (valor_en(p, k, d) for k in ("divisor_flota", "meses_anio", "dias_mes"))
        if not (div and meses and dias):
            continue
        dias_equipo = div * meses * dias
        filas.append(fila("costo_fijo_diario", "todos", round((masa + ajuste) / dias_equipo, 2), "CLP/día-equipo", d, hasta,
                          "calculado", f"(masa {d.year - 1} + ajustes) ÷ {div:g} ÷ {meses:g} ÷ {dias:g}",
                          base=round(masa + ajuste, 2), inductor=dias_equipo))

    # reposición diaria por ámbito: (USD × dólar + termo − remanente) ÷ meses ÷ días
    comp_rep = ["valor_camion_usd", "tipo_cambio_usd", "valor_termo", "remanente", "meses_reposicion", "dias_mes"]
    for amb in ("todos", "tractoreo"):
        cs = cortes(p, comp_rep)
        for i, d in enumerate(cs):
            hasta = cs[i + 1] if i + 1 < len(cs) else FIN
            v = {k: valor_en(p, k, d, amb) for k in comp_rep}
            if None in (v["valor_camion_usd"], v["tipo_cambio_usd"], v["meses_reposicion"], v["dias_mes"]):
                continue
            saldo = v["valor_camion_usd"] * v["tipo_cambio_usd"] + (v["valor_termo"] or 0) - (v["remanente"] or 0)
            filas.append(fila("reposicion_diaria", amb, round(saldo / v["meses_reposicion"] / v["dias_mes"], 2), "CLP/día",
                              d, hasta, "calculado", "(USD × dólar + termo − remanente) ÷ meses ÷ días",
                              base=round(saldo, 2), inductor=v["meses_reposicion"] * v["dias_mes"]))

    # neumáticos por ámbito: n × precio ÷ vida útil
    comp_neu = ["n_neumaticos", "precio_neumatico", "vida_util_km"]
    for amb in ("todos", "tractoreo"):
        cs = cortes(p, comp_neu)
        for i, d in enumerate(cs):
            hasta = cs[i + 1] if i + 1 < len(cs) else FIN
            v = {k: valor_en(p, k, d, amb) for k in comp_neu}
            if None in v.values():
                continue
            filas.append(fila("costo_neumatico_km", amb, round(v["n_neumaticos"] * v["precio_neumatico"] / v["vida_util_km"], 4),
                              "CLP/km", d, hasta, "calculado", "n_neumaticos × precio_neumatico ÷ vida_util_km",
                              base=v["n_neumaticos"] * v["precio_neumatico"], inductor=v["vida_util_km"]))
    return filas


def proporciones_anio(contab, centros, anio, p):
    """Proporciones de los mixtos para un año base, calculadas con los datos (si no hay datos: valor por defecto)."""
    d = contab[(contab["anio"] == anio) & contab["bolsa"].notna()]
    rol = d["c_activity_id"].map(lambda a: centros.get(int(a), ("", "", ""))[1] if pd.notna(a) else "")
    tracto, rampla = d.loc[rol == "tracto", "monto"].sum(), d.loc[rol == "rampla", "monto"].sum()
    ref = date(anio + 1, 1, 1)
    pct_t = float(tracto / (tracto + rampla)) if tracto + rampla > 0 else \
        valor_en(p, "pct_talleres_tracto_defecto", ref, metodo="propuesto")
    ge = d[(d["codigo"] == "310020101") & d["c_activity_id"].notna()]
    rol_ge = rol[ge.index]
    pct_a = float(ge.loc[rol_ge == "conductores", "monto"].sum() / ge["monto"].sum()) if ge["monto"].sum() > 0 else \
        valor_en(p, "pct_aportes_conductores_defecto", ref, metodo="propuesto")
    origen_t = "datos" if tracto + rampla > 0 else "defecto"
    origen_a = "datos" if ge["monto"].sum() > 0 else "defecto"
    return pct_t, pct_a, origen_t, origen_a


def calcular_propuesto(p, contab, centros, anios_completos, dias_fleteros):
    """Parámetros del método propuesto: masa por bolsa, proporciones de los mixtos y cuotas diarias por bolsa."""
    # La contabilidad original y el método tarificador conservan la comisión.
    # Excluirla aquí asegura la regla incluso al llamar esta función por separado.
    contab = contab[~contab["codigo"].isin(CUENTAS_COMISION_DIRECTA)].copy()
    filas = []
    masas, props = {}, {}
    for anio in anios_completos:
        d = contab[(contab["anio"] == anio) & contab["bolsa"].notna()]
        masas[anio] = d.groupby("bolsa")["monto"].sum().astype(float).to_dict()
        props[anio] = proporciones_anio(contab, centros, anio, p)
        hasta_m = FIN if anio == anios_completos[-1] else date(anio + 2, 1, 1)
        for bol in BOLSAS + MIXTOS:
            filas.append(fila("masa_bolsa", bol, round(masas[anio].get(bol, 0.0), 2), "CLP/año", date(anio + 1, 1, 1),
                              hasta_m, "contabilidad", f"Σ costo fijo con bolsa = {bol}, año contable {anio}",
                              padre="cuota_diaria", metodo="propuesto", nota=f"Año base {anio} → viajes de {anio + 1}"))
        pct_t, pct_a, ot, oa = props[anio]
        filas.append(fila("pct_talleres_tracto", "todos", round(pct_t, 6), "fracción", date(anio + 1, 1, 1), hasta_m,
                          "calculado" if ot == "datos" else "manual",
                          "costo fijo de centros con rol tracto ÷ (tracto + rampla)" if ot == "datos" else "valor por defecto",
                          padre="cuota_diaria", metodo="propuesto", nota=f"Año base {anio}"))
        filas.append(fila("pct_aportes_conductores", "todos", round(pct_a, 6), "fracción", date(anio + 1, 1, 1), hasta_m,
                          "calculado" if oa == "datos" else "manual",
                          "Gasto Empleados del centro Conductores ÷ Gasto Empleados con centro" if oa == "datos"
                          else "valor por defecto", padre="cuota_diaria", metodo="propuesto", nota=f"Año base {anio}"))

    # cuotas diarias por bolsa (mismos cortes que el costo fijo del tarificador)
    anios_aplic = [a + 1 for a in anios_completos]
    extra = [date(a, 1, 1) for a in anios_aplic] + [date(anios_aplic[-1] + 1, 1, 1)] if anios_aplic else []
    cs = cortes(p, ["ajuste_manual_costo_fijo", "meses_anio", "dias_mes", "capacidad_equipos", "pct_seguro_equipos"], extra)
    for i, d in enumerate(cs):
        if d.year not in anios_aplic:
            continue
        hasta = cs[i + 1] if i + 1 < len(cs) else FIN
        if d.year == anios_aplic[-1] and hasta == date(d.year + 1, 1, 1):
            hasta = FIN
        base = d.year - 1
        m = masas[base]
        pct_t, pct_a, _, _ = props[base]
        aj = suma_en(p, "ajuste_manual_costo_fijo", d)
        pct_s = valor_en(p, "pct_seguro_equipos", d, metodo="propuesto")
        meses, dias = valor_en(p, "meses_anio", d), valor_en(p, "dias_mes", d)
        if None in (pct_s, meses, dias):
            continue
        tal_eq = m.get("mixto_talleres", 0) + m.get("mixto_equipos", 0)
        aportes = m.get("mixto_aportes", 0) + aj
        seg = m.get("mixto_seguros", 0)
        monto = {
            "flota": m.get("flota", 0) + pct_t * tal_eq + pct_a * aportes + seg * pct_s * pct_t,
            "rampla": m.get("rampla", 0) + (1 - pct_t) * tal_eq + seg * pct_s * (1 - pct_t),
            "estructura": m.get("estructura", 0) + (1 - pct_a) * aportes + seg * (1 - pct_s),
            "directo_fleteros": m.get("directo_fleteros", 0)}
        for bol in BOLSAS:
            if bol == "directo_fleteros":
                cap = dias_fleteros.get(base) or 0
                fuente = f"masa directo fleteros {base} ÷ días-viaje de fleteros {base}"
            else:
                eq = valor_en(p, "capacidad_equipos", d, bol, metodo="propuesto")
                if not eq:
                    continue
                cap = eq * meses * dias
                fuente = f"(masa bolsa {base} + mixtos) ÷ ({eq:g} equipos × {meses:g} × {dias:g})"
            if cap <= 0:
                continue
            filas.append(fila("cuota_diaria", bol, round(monto[bol] / cap, 2), "CLP/día", d, hasta, "calculado", fuente,
                              base=round(monto[bol], 2), inductor=cap, metodo="propuesto"))

    # reposición de la rampla (terceros que usan rampla de Callegari): con termo (THERMO) o sin termo
    comp = ["valor_rampla_usd", "tipo_cambio_usd", "valor_termo", "remanente_rampla", "meses_reposicion", "dias_mes"]
    cs = cortes(p, comp)
    for i, d in enumerate(cs):
        hasta = cs[i + 1] if i + 1 < len(cs) else FIN
        usd = valor_en(p, "valor_rampla_usd", d, metodo="propuesto")
        tc, termo = valor_en(p, "tipo_cambio_usd", d), valor_en(p, "valor_termo", d) or 0
        rem = valor_en(p, "remanente_rampla", d, metodo="propuesto") or 0
        mr, dm = valor_en(p, "meses_reposicion", d), valor_en(p, "dias_mes", d)
        if None in (usd, tc, mr, dm):
            continue
        for amb, t in (("thermo", termo), ("sin_termo", 0)):
            saldo = usd * tc + t - rem
            filas.append(fila("reposicion_rampla_diaria", amb, round(saldo / mr / dm, 2), "CLP/día", d, hasta, "calculado",
                              "(rampla USD × dólar + termo − remanente rampla) ÷ meses ÷ días", base=round(saldo, 2),
                              inductor=mr * dm, metodo="propuesto"))

    # neumáticos de la rampla (terceros que usan rampla de Callegari)
    cs = cortes(p, ["n_neumaticos_rampla", "precio_neumatico", "vida_util_km"])
    for i, d in enumerate(cs):
        hasta = cs[i + 1] if i + 1 < len(cs) else FIN
        n = valor_en(p, "n_neumaticos_rampla", d, metodo="propuesto")
        pr, vu = valor_en(p, "precio_neumatico", d), valor_en(p, "vida_util_km", d)
        if None in (n, pr, vu):
            continue
        filas.append(fila("costo_neumatico_km", "rampla", round(n * pr / vu, 4), "CLP/km", d, hasta, "calculado",
                          "n_neumaticos_rampla × precio_neumatico ÷ vida_util_km", base=n * pr, inductor=vu,
                          metodo="propuesto"))
    return filas


# ------------------------------------------------------------------------------------------------ viajes
def leer_viajes(ruta_vta, ruta_tramo, ruta_veh, p):
    """Viajes con km, días (km ÷ km_por_dia vigente), operador y si la rampla es de Callegari."""
    cols = ["c_orderline_id", "datepromised", "sit_stretch_id", "sit_mainteam_id", "sit_secondaryteam_id", "grupo2",
            "tractoreo"]
    v = pd.read_parquet(ruta_vta, columns=cols).drop_duplicates("c_orderline_id")
    tr = pd.read_parquet(ruta_tramo, columns=["sit_stretch_id", "kilometers"])
    v = v.merge(tr, on="sit_stretch_id", how="left")
    fecha = pd.to_datetime(v["datepromised"], errors="coerce")
    try:
        fecha = fecha.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    grupo = v["grupo2"].astype(str).str.strip()
    rampla = pd.to_numeric(v["sit_secondaryteam_id"], errors="coerce").astype("Int64")
    tractoreo = v["tractoreo"].astype(str).str.upper().isin(["Y", "S"])
    cat, propio = {}, {}
    if ruta_veh is not None:
        vc = [c for c in ("a_asset_id", "categoryequipment", "isowned") if c in pd.read_parquet(ruta_veh).columns]
        veh = pd.read_parquet(ruta_veh, columns=vc).drop_duplicates("a_asset_id")
        ids = pd.to_numeric(veh["a_asset_id"], errors="coerce")
        cat = dict(zip(ids, veh["categoryequipment"].astype(str).str.upper())) if "categoryequipment" in veh else {}
        propio = dict(zip(ids, veh["isowned"].astype(str).str.upper())) if "isowned" in veh else {}
    cat_r = rampla.map(lambda x: cat.get(int(x), "") if pd.notna(x) else "")
    usa = rampla.notna() & ~tractoreo & ~cat_r.str.startswith("TRACTO") & \
        rampla.map(lambda x: propio.get(int(x), "Y" if not propio else "N") == "Y" if pd.notna(x) else False)
    df = pd.DataFrame({
        "nro_viaje": pd.to_numeric(v["c_orderline_id"], errors="coerce").astype("Int64"),
        "fecha": fecha.dt.strftime("%Y-%m-%d"),
        "sit_stretch_id": pd.to_numeric(v["sit_stretch_id"], errors="coerce").astype("Int64"),
        "equipo_id": pd.to_numeric(v["sit_mainteam_id"], errors="coerce").astype("Int64"),
        "rampla_id": rampla,
        "operador": grupo,
        "es_flota_propia": grupo.str.startswith("1.").map({True: "S", False: "N"}),
        "es_fletero": grupo.str.startswith("3.").map({True: "S", False: "N"}),
        "es_tractoreo": tractoreo.map({True: "S", False: "N"}),
        "usa_rampla_callegari": usa.map({True: "S", False: "N"}),
        "rampla_termo": cat_r.str.contains("THERMO").map({True: "thermo", False: "sin_termo"}),
        "km_tramo": pd.to_numeric(v["kilometers"], errors="coerce")})
    df = df[df["nro_viaje"].notna()]
    # días-viaje de fleteros por año (inductor de la bolsa "directo fleteros")
    km_dia = {f: valor_en(p, "km_por_dia", date.fromisoformat(f)) for f in df["fecha"].dropna().unique()}
    dias = df["km_tramo"] / df["fecha"].map(km_dia).astype(float)
    anio = pd.to_datetime(df["fecha"], errors="coerce").dt.year
    dias_fleteros = dias[df["es_fletero"] == "S"].groupby(anio[df["es_fletero"] == "S"]).sum().to_dict()
    return df, {int(k): float(v) for k, v in dias_fleteros.items()}


def cargar_viajes(con, df):
    con.execute("DROP TABLE IF EXISTS tmp_viaje_ci")
    con.execute("CREATE TEMP TABLE tmp_viaje_ci (nro_viaje BIGINT, fecha DATE, sit_stretch_id BIGINT, equipo_id BIGINT, "
                "rampla_id BIGINT, operador TEXT, es_flota_propia TEXT, es_fletero TEXT, es_tractoreo TEXT, "
                "usa_rampla_callegari TEXT, rampla_termo TEXT, km_tramo DOUBLE PRECISION)")
    con.copiar_df("tmp_viaje_ci", df)
    con.execute(ASIGNAR)
    con.execute(ASIGNAR_PROPUESTO)
    con.execute("DROP TABLE tmp_viaje_ci")
    return len(df)


def _par(nombre, metodo, ambito_sql):
    return (f"(SELECT valor FROM parametro_costo p WHERE p.parametro = '{nombre}' AND p.metodo = '{metodo}' "
            f"AND p.ambito = {ambito_sql} AND v.fecha >= p.vigente_desde AND v.fecha < p.vigente_hasta)")


AMB_TRACTOREO = "CASE WHEN v.es_tractoreo = 'S' THEN 'tractoreo' ELSE 'todos' END"
COLUMNAS = ("nro_viaje, metodo, sk_fecha_inicio, sit_stretch_id, equipo_id, rampla_id, operador, es_flota_propia, "
            "es_tractoreo, usa_rampla_callegari, km_tramo, dias_viaje, costo_fijo_asignado, cf_flota, cf_rampla, "
            "cf_estructura, cf_directo_fleteros, reposicion_asignada, costo_neumatico_asignado, costo_indirecto_total, "
            "tasa_costo_fijo_aplicada, tasa_reposicion_aplicada, tasa_neumatico_aplicada, km_por_dia_aplicado, "
            "estado_costo_indirecto")

ASIGNAR = f"""
INSERT INTO fact_viaje_costo_indirecto ({COLUMNAS})
WITH t AS (
  SELECT v.*,
         {_par('km_por_dia', 'tarificador', "'todos'")} AS km_dia,
         {_par('costo_fijo_diario', 'tarificador', "'todos'")} AS cf,
         {_par('reposicion_diaria', 'tarificador', AMB_TRACTOREO)} AS rep,
         {_par('costo_neumatico_km', 'tarificador', AMB_TRACTOREO)} AS neu
  FROM tmp_viaje_ci v),
c AS (
  SELECT t.*, CASE WHEN es_flota_propia = 'S' THEN km_tramo / NULLIF(km_dia, 0) END AS dias,
         CASE WHEN es_flota_propia = 'N' THEN 'tercero'
              WHEN km_tramo IS NULL THEN 'sin km'
              WHEN cf IS NULL THEN 'sin año base'
              ELSE 'ok' END AS estado
  FROM t)
SELECT nro_viaje, 'tarificador', to_char(fecha, 'YYYYMMDD')::int, sit_stretch_id, equipo_id, rampla_id, operador,
       es_flota_propia, es_tractoreo, usa_rampla_callegari, km_tramo, dias,
       CASE WHEN estado = 'tercero' THEN 0 ELSE dias * cf END, NULL, NULL, NULL, NULL,
       CASE WHEN estado = 'tercero' THEN 0 ELSE dias * rep END,
       CASE WHEN estado = 'tercero' THEN 0 ELSE km_tramo * neu END,
       CASE WHEN estado = 'tercero' THEN 0 ELSE COALESCE(dias * cf, 0) + COALESCE(dias * rep, 0) + COALESCE(km_tramo * neu, 0) END,
       cf, rep, neu, km_dia, estado
FROM c;
"""

ASIGNAR_PROPUESTO = f"""
INSERT INTO fact_viaje_costo_indirecto ({COLUMNAS})
WITH t AS (
  SELECT v.*,
         {_par('km_por_dia', 'tarificador', "'todos'")} AS km_dia,
         {_par('cuota_diaria', 'propuesto', "'flota'")} AS q_flota,
         {_par('cuota_diaria', 'propuesto', "'rampla'")} AS q_rampla,
         {_par('cuota_diaria', 'propuesto', "'estructura'")} AS q_estr,
         {_par('cuota_diaria', 'propuesto', "'directo_fleteros'")} AS q_dir,
         {_par('reposicion_diaria', 'tarificador', AMB_TRACTOREO)} AS rep_camion,
         {_par('reposicion_rampla_diaria', 'propuesto', 'v.rampla_termo')} AS rep_rampla,
         {_par('costo_neumatico_km', 'tarificador', AMB_TRACTOREO)} AS neu_camion,
         {_par('costo_neumatico_km', 'propuesto', "'rampla'")} AS neu_rampla
  FROM tmp_viaje_ci v),
c AS (
  SELECT t.*, km_tramo / NULLIF(km_dia, 0) AS dias,
         CASE WHEN km_tramo IS NULL THEN 'sin km' WHEN q_estr IS NULL THEN 'sin año base' ELSE 'ok' END AS estado
  FROM t),
k AS (
  SELECT c.*,
         CASE WHEN es_flota_propia = 'S' THEN dias * q_flota ELSE 0 END AS x_flota,
         CASE WHEN usa_rampla_callegari = 'S' THEN dias * q_rampla ELSE 0 END AS x_rampla,
         dias * q_estr AS x_estr,
         CASE WHEN es_fletero = 'S' THEN dias * COALESCE(q_dir, 0) ELSE 0 END AS x_dir,
         CASE WHEN es_flota_propia = 'S' THEN rep_camion WHEN usa_rampla_callegari = 'S' THEN rep_rampla ELSE 0 END AS tasa_rep,
         CASE WHEN es_flota_propia = 'S' THEN neu_camion WHEN usa_rampla_callegari = 'S' THEN neu_rampla ELSE 0 END AS tasa_neu
  FROM c)
SELECT nro_viaje, 'propuesto', to_char(fecha, 'YYYYMMDD')::int, sit_stretch_id, equipo_id, rampla_id, operador,
       es_flota_propia, es_tractoreo, usa_rampla_callegari, km_tramo, dias,
       x_flota + x_rampla + x_estr + x_dir, x_flota, x_rampla, x_estr, x_dir,
       dias * tasa_rep, km_tramo * tasa_neu,
       COALESCE(x_flota + x_rampla + x_estr + x_dir, 0) + COALESCE(dias * tasa_rep, 0) + COALESCE(km_tramo * tasa_neu, 0),
       (x_flota + x_rampla + x_estr + x_dir) / NULLIF(dias, 0), tasa_rep, tasa_neu, km_dia, estado
FROM k;
"""


def cargar_capacidad(con, par):
    """fact_capacidad_bolsa: por año de los viajes y bolsa, costo que rige (prorrateado por días de vigencia),
    capacidad, lo absorbido por los viajes y la capacidad ociosa."""
    q = par[(par["parametro"] == "cuota_diaria") & (par["metodo"] == "propuesto")].copy()
    if q.empty:
        return pd.DataFrame()
    abs_ = con.df("""
        SELECT f.anio,
               SUM(v.cf_flota) AS flota, SUM(v.cf_rampla) AS rampla, SUM(v.cf_estructura) AS estructura,
               SUM(v.cf_directo_fleteros) AS directo_fleteros,
               SUM(CASE WHEN v.cf_flota > 0 THEN v.dias_viaje END) AS d_flota,
               SUM(CASE WHEN v.cf_rampla > 0 THEN v.dias_viaje END) AS d_rampla,
               SUM(CASE WHEN v.cf_estructura > 0 THEN v.dias_viaje END) AS d_estructura,
               SUM(CASE WHEN v.cf_directo_fleteros > 0 THEN v.dias_viaje END) AS d_directo_fleteros
        FROM fact_viaje_costo_indirecto v JOIN comun.dim_fecha f ON f.sk_fecha = v.sk_fecha_inicio
        WHERE v.metodo = 'propuesto' AND v.estado_costo_indirecto = 'ok' GROUP BY 1""").set_index("anio")
    anios = sorted({d.year for d in q["vigente_desde"]})
    filas = []
    for a in anios:
        ini, fin = date(a, 1, 1), date(a + 1, 1, 1)
        if a not in abs_.index:
            continue
        for bol in BOLSAS:
            qb = q[q["ambito"] == bol]
            monto = cap = 0.0
            for r in qb.itertuples():
                desde, hasta = max(r.vigente_desde, ini), min(r.vigente_hasta, fin)
                if hasta <= desde:
                    continue
                frac = (hasta - desde).days / (fin - ini).days
                monto += float(r.monto_base) * frac
                cap += float(r.cantidad_inductor) * frac
            if cap <= 0:
                continue
            absorbido = float(abs_.loc[a, bol] or 0)
            filas.append(dict(anio=a, bolsa=bol, monto_bolsa=round(monto, 2), capacidad_dias=round(cap, 2),
                              cuota_media=round(monto / cap, 2), dias_usados=round(float(abs_.loc[a, f"d_{bol}"] or 0), 2),
                              absorbido=round(absorbido, 2), capacidad_ociosa=round(monto - absorbido, 2),
                              pct_absorbido=round(absorbido / monto, 4) if monto else None))
    df = pd.DataFrame(filas)
    if not df.empty:
        con.copiar_df("fact_capacidad_bolsa", df)
    return df


# ------------------------------------------------------------------------------------------------ programa
def principal(argv=None, _conn=None):
    from comun.contrato_parametros import validar as validar_parametros
    validar_parametros()
    print(f"construir_dw_costos.py versión {VERSION}")
    ap = argparse.ArgumentParser(description="Carga los costos indirectos en el esquema costos del MVP.")
    ap.add_argument("--contabilidad", default=None, help="ruta de rv_fact_acct.parquet (por defecto la de config.ini)")
    a = ap.parse_args(argv)

    carpeta_contab = ruta_fuente("contabilidad", "fuentes/contabilidad_2026-03-24")
    ruta_fa = Path(a.contabilidad) if a.contabilidad else buscar(carpeta_contab, ["rv_fact_acct.parquet", "adempiere_rv_fact_acct.parquet"])
    if ruta_fa is None or not ruta_fa.exists():
        sys.exit(f"No encuentro rv_fact_acct.parquet en {carpeta_contab}. Copiarlo ahí (ver README) o usar --contabilidad.")
    erp = ruta_fuente("erp", "fuentes/erp_2026-10-07")
    ruta_vta = buscar(erp, ["adempiere_sit_pbix_travelvta_v.parquet", "sit_pbix_travelvta_v.parquet"])
    ruta_tramo = buscar(erp, ["adempiere_sit_pbix_traveltramo_v.parquet", "sit_pbix_traveltramo_v.parquet"])
    ruta_veh = (buscar(erp, ["adempiere_sit_pbix_travelveh_v.parquet", "sit_pbix_travelveh_v.parquet"])
                or buscar(ruta_fuente("combustible", "fuentes/erp_2026-03"), ["sit_pbix_travelveh_v.parquet",
                                                                               "adempiere_sit_pbix_travelveh_v.parquet"]))
    if ruta_vta is None or ruta_tramo is None:
        sys.exit(f"No encuentro travelvta_v o traveltramo_v en {erp}")
    ruta_act = (buscar(erp, ["adempiere_sit_pbix_segactividad_v.parquet", "sit_pbix_segactividad_v.parquet"])
                or buscar(ruta_fuente("combustible", "fuentes/erp_2026-03"), ["sit_pbix_segactividad_v.parquet",
                                                                               "adempiere_sit_pbix_segactividad_v.parquet"]))

    print(f"   contabilidad: {ruta_fa}")
    fa, n_cierre = leer_contabilidad(ruta_fa)
    meses = fa.groupby(fa["dateacct"].dt.year)["dateacct"].apply(lambda s: s.dt.month.nunique())
    anios_completos = sorted(int(y) for y, n in meses.items() if n == 12)
    print(f"   {len(fa):,} líneas de resultado de Callegari ({n_cierre:,} líneas de cierre excluidas); "
          f"años completos {anios_completos[0]}–{anios_completos[-1]}")

    p = leer_parametros_manual()
    reglas = leer_bolsas()
    viajes, dias_fleteros = leer_viajes(ruta_vta, ruta_tramo, ruta_veh, p)

    con = db.conectar(ESQUEMAS, _conn=_conn)
    ventana_absorcion = (con.df("SELECT * FROM costos.parametro_costo WHERE metodo='absorcion_total' AND parametro IN('ventana_meses','km_por_dia')")
        if con.existe_tabla('costos','parametro_costo') else pd.DataFrame())
    with con:
        con.execute(ESQUEMA)
    with con:
        cargar_fechas(con)
        cu, n_man = cargar_cuentas(con, fa)
        n_centros = cargar_centros(con, ruta_act, reglas)
        un_equipo, un_compartidas = mapa_un_equipo(ruta_veh)
        contab = cargar_contabilidad(con, fa, cu, un_equipo, un_compartidas, reglas)

        fijo = set(cu.loc[cu["clase_tarificador"] == "fijo", "sk_cuenta"])
        masa = contab[contab["sk_cuenta"].isin(fijo)].groupby("anio")["monto"].sum().astype(float).to_dict()
        filas = calcular_parametros(p, masa, anios_completos)
        # Solo el método propuesto excluye comisión. Hoja de ruta, viáticos y
        # tiempos de espera conservan su tratamiento; la referencia comercial no cambia.
        filas += calcular_propuesto(p, contab, reglas[0], anios_completos, dias_fleteros)
        filas.append(fila("comision_directa", "todos", 1, "indicador", INICIO, FIN,
                          "regla", "310020104 excluida de las bolsas; comisión observada por viaje",
                          metodo="propuesto"))
        par = pd.DataFrame(filas)
        con.copiar_df("parametro_costo", par[["parametro", "metodo", "ambito", "valor", "unidad", "vigente_desde",
                                              "vigente_hasta", "parametro_padre", "origen", "fuente_detalle",
                                              "monto_base", "cantidad_inductor", "nota"]])
        if len(ventana_absorcion):
            con.copiar_df('costos.parametro_costo', ventana_absorcion)
        n_viajes = cargar_viajes(con, viajes)
        capacidad = cargar_capacidad(con, par)

        from costos import cobertura_referencias
        cobertura_contable = cobertura_referencias.publicar(con)

        # controles
        ctrl = {}
        ctrl['cobertura_referencias'] = cobertura_contable
        ctrl["un_fleteros"] = sorted(int(u) for u in un_compartidas)
        ctrl["lineas_con_un_sin_equipo"] = int(contab["unidad_negocio_id"].notna().sum() - contab["equipo_id"].notna().sum()
                                               - (contab["es_un_fleteros"] == "S").sum())
        ctrl["solapes_vigencia"] = con.uno(
            "SELECT COUNT(*) FROM parametro_costo a JOIN parametro_costo b ON a.parametro=b.parametro AND a.metodo=b.metodo "
            "AND a.ambito=b.ambito AND a.vigente_desde<b.vigente_desde AND b.vigente_desde<a.vigente_hasta")[0]
        if 2025 in masa:
            ctrl["masa_2025_vs_tarificador_sin_ajuste"] = round(masa[2025] - 3_354_394_081)
            ctrl["bolsas_2025_cuadran_con_masa"] = round(
                contab[(contab["anio"] == 2025) & contab["bolsa"].notna()]["monto"].astype(float).sum() - masa[2025])
        ctrl["centros_sin_regla_con_monto_fijo"] = int(contab[contab["bolsa"].notna() & contab["c_activity_id"].notna()
                                                             & ~contab["c_activity_id"].map(
            lambda a: int(a) in reglas[0] if pd.notna(a) else True)]["c_activity_id"].nunique())
        ctrl["viajes_por_estado"] = dict(con.execute(
            "SELECT metodo || ': ' || estado_costo_indirecto, COUNT(*) FROM fact_viaje_costo_indirecto "
            "GROUP BY 1 ORDER BY 1").fetchall())
        absorcion = con.df(
            "SELECT f.anio, SUM(v.costo_fijo_asignado) AS asignado, SUM(v.dias_viaje) AS dias "
            "FROM fact_viaje_costo_indirecto v JOIN comun.dim_fecha f ON f.sk_fecha = v.sk_fecha_inicio "
            "WHERE v.metodo = 'tarificador' AND v.estado_costo_indirecto = 'ok' GROUP BY 1 ORDER BY 1")
        detalle = (f"cuentas {len(cu)} (manuales {n_man}); centros {n_centros}; contabilidad {len(contab)} filas "
                   f"mes×cuenta×UN×centro, años completos {anios_completos[0]}-{anios_completos[-1]}, cierres excluidos "
                   f"{n_cierre} líneas; parámetros {len(par)}; viajes {n_viajes} × 2 métodos; controles {ctrl}")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("costos", db.ahora(), VERSION, "", detalle))
        vista = vista_viaje.crear(con)

    for t in ["dim_cuenta_contable", "dim_centro_costo", "fact_contabilidad_mensual", "parametro_costo",
              "fact_viaje_costo_indirecto", "fact_capacidad_bolsa"]:
        print(f"   {t:30s} {int(con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]):8d}")
    print("   " + detalle)
    vig = con.df("SELECT parametro, metodo, ambito, vigente_desde, valor FROM parametro_costo "
                 "WHERE parametro IN ('costo_fijo_diario', 'reposicion_diaria', 'costo_neumatico_km', 'cuota_diaria', "
                 "'reposicion_rampla_diaria') AND vigente_hasta = DATE '9999-12-31' ORDER BY 2 DESC, 1, 3")
    for r in vig.itertuples():
        print(f"   vigente {r.metodo:11s} {r.parametro:24s} {r.ambito:16s} desde {r.vigente_desde}: {float(r.valor):>14,.2f}")
    print("   masa de costo fijo por año base (MM$): " + ", ".join(f"{k}: {v / 1e6:,.1f}" for k, v in sorted(masa.items())))
    for r in absorcion.itertuples():
        print(f"   {int(r.anio)}: costo fijo asignado a viajes propios {float(r.asignado or 0) / 1e6:,.1f} MM$ "
              f"en {float(r.dias or 0):,.0f} días-viaje (tarificador)")
    if not capacidad.empty:
        ult = capacidad[capacidad["anio"] == capacidad["anio"].max() - 1] if capacidad["anio"].nunique() > 1 else capacidad
        print(f"   propuesto, capacidad por bolsa {int(ult['anio'].iloc[0])} (MM$): " + " · ".join(
            f"{r.bolsa} {r.monto_bolsa / 1e6:,.0f} (absorbido {r.absorbido / 1e6:,.0f}, ociosa {r.capacidad_ociosa / 1e6:,.0f})"
            for r in ult.itertuples()))
    print("   vista comun.v_fact_viaje: " + ("actualizada" if vista else "no creada (falta cargar peajes)"))
    con.close()


if __name__ == "__main__":
    principal()
