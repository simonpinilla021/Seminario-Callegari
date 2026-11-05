#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Asignación de litros de combustible a viajes — método por bloques (Indicadores/Asignacion de combustible.md §3.3).

Es el código del notebook EDA_v0/bloques_combustible_v2.ipynb, sin cambios en la lógica del método. Cambió solo de
dónde salen los datos (2026-10-24):
  - VIAJES: ya no se usa Fact_Ventas_RT (resultado de ciclos de la práctica en Power BI), sino su equivalente en el DW:
    ciclos.fact_viaje_ciclo (orden de la cadena, viajes virtuales V-<camión>-<n>, ciclo_comercial), construido desde
    adempiere_sit_pbix_travelvta_v, más de travelvta: hora de creación de la línea, fin, rampla, conductor y hoja de ruta.
    Por eso hay que construir antes el área ciclos (construir_todo.py lo hace en orden).
  - CARGAS: unión por sit_petroleum_id de la extracción de octubre (config.ini [fuentes] erp; desde 2025-01) y la de
    marzo ([fuentes] combustible; desde 2022): si una carga está en las dos, manda octubre; las de marzo que no están
    en octubre se agregan (octubre no trae las COP de abril a junio de 2025).
  - EQUIPOS, TRAMOS y TIPO DE CARGA: de la extracción de octubre si está; si no, de la de marzo.
SALIDA = combustible/bloques_out.

Salidas en bloques_out/: litros_por_viaje.csv, bloques.csv, regresion_por_camion.csv, P1..P6.txt, bloques_resultados.txt
Después: python construir_dw_combustible.py   (carga las salidas en PostgreSQL, esquema combustible)

Uso (en la carpeta MVP Data Warehouse/combustible):  python asignar_litros.py
"""
# ============================== 0. CONFIGURACIÓN ==============================
from pathlib import Path
import unicodedata, re
import os
import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent                 # MVP Data Warehouse/combustible/
import sys as _sys
_sys.path.insert(0, str(AQUI.parent))
from comun import db as _db
from combustible.fuentes import normalizar_cargas
RUTA = _db.ruta_config("fuentes", "combustible")       # extracción de marzo 2026 (cargas desde 2022)
RUTA_ERP = _db.ruta_config("fuentes", "erp")           # extracción de octubre 2026 (travelvta; cargas desde 2025-01)
SALIDA = Path(os.environ.get("DW_BLOQUES_SALIDA", str(AQUI / "bloques_out")))  # salida aislada del orquestador

ARCHIVOS = {
    "combustible": "sit_pbix_guiascombust_v.parquet",  # unión octubre + marzo (leer_cargas)
    "equipos":     "sit_pbix_travelveh_v.parquet",
    "viajes":      "ciclos.fact_viaje_ciclo",          # tabla del DW + travelvta (leer_viajes); antes Fact_Ventas_RT
    "tramos":      "sit_pbix_traveltramo_v.parquet",
    "tipocarga":   "sit_pbix_traveltcarga_v.parquet",   # opcional: si no existe, se omiten C6 y F4
}

# Para cada dato, lista de nombres posibles (se usa el primero que exista, sin distinguir mayúsculas).
# Si tu columna se llama distinto, agrégala al inicio de la lista.
COLS = {
    "combustible": {
        "id":        ["sit_petroleum_id", "sit_pretroleum_id"],
        "fecha":     ["datetrx", "created"],      # fecha de la carga (datetrx = fecha de la transacción)
        "fecha_reg": ["created"],                 # fecha en que se digitó el registro
        "equipo":    ["a_asset_id"],
        "litros":    ["liters"],
        "odo":       ["odometer"],
        "odo_gps":   ["odometergps", "odometrogps"],
        "horo":      ["horometrogps"],
        "proveedor": ["oilsupplier"],
        "direccion": ["address", "adress"],
        "voucher":   ["voucher"],
        "precio":    ["price", "priceactual", "unitprice", "precio", "priceentered"],   # opcional
        "conductor": ["sit_driver_v_id", "sit_internaldriver_id", "driver_id", "c_bpartner_id"],   # opcional
        "producto":  ["m_product_id"],            # opcional: tipo de combustible o producto
    },
    "viajes": {
        "id":        ["c_orderline_id"],
        "inicio":    ["datepromised"],
        "fin":       ["enddatetravel"],
        "orden":     ["orden_procesado"],
        "camion":    ["sit_mainteam_id"],
        "rampla":    ["sit_secondaryteam_id"],
        "tramo":     ["sit_stretch_id"],
        "producto":  ["m_product_id"],
        "origen":    ["Ciudad_Origen_Limpia", "ciuorigen_id"],
        "destino":   ["Ciudad_Destino_Limpia", "ciudestino_id"],
        "origen_id": ["ciuorigen_id"],
        "destino_id": ["ciudestino_id"],
        "creado":    ["created"],                 # fecha de creación de la línea del viaje
        "km_rt":     ["kilometers"],              # km del viaje en ciclos (incluye viajes virtuales)
        "tramo_rt":  ["tramo"],
        "tipo_mov":  ["Tipo_Movimiento"],
        "ciclo":     ["ciclo_comercial"],
        "virtual":   ["Es_Virtual"],
        "conductor": ["sit_driver_v_id"],
        "hoja":      ["sit_roadmap_id"],
        "doc":       ["documentno"],
    },
    "equipos":   {"equipo": ["a_asset_id"], "codigo": ["codigo"], "categoria": ["categoryequipment"],
                  "grupo": ["grupoemp"], "externo": ["external"], "modelo": ["modelo"],
                  "rend_ficha": ["performancewithload"]},
    "tramos":    {"tramo": ["sit_stretch_id"], "nombre": ["tramo"], "km": ["kilometers"]},
    "tipocarga": {"producto": ["m_product_id"], "nombre": ["producto"], "frio": ["temperaturecontrol"],
                  "multi": ["multiload"], "vacio": ["emptytrip"]},
}

ANIOS_VENTANA   = 4             # se analiza desde (última carga - 4 años)
ODO_PRINCIPAL   = "odo"         # "odo" u "odo_gps": fuente de km entre cargas (decidir tras B1)
PROVEEDOR_BASE  = "ATP"         # valor de oilsupplier que identifica la base
BASE_CIUDAD     = None          # ciudad de la base tal como aparece en origen/destino; None = se detecta sola en L3
RANGO_REND      = (1.2, 5.5)    # km/L plausibles para un tractocamión (banda amplia)
TOL_REND        = 0.35          # desviación tolerada respecto del rendimiento mediano del propio camión
LITROS_MIN_LLENO = 50           # bajo esto se duda de que sea un llenado completo
LITROS_MAX      = 1200          # sobre esto se duda del registro
DKM_MAX         = 3500          # km máximos razonables entre dos cargas
VEL_MAX         = 90            # km/h máximos razonables para un viaje
MIN_CARGAS      = 10            # mínimo de cargas para analizar un equipo por separado

# --- Parámetros del método por bloques ---
ZONA_BASE       = ["COQUIMBO", "LA SERENA"]   # ciudades (como aparecen en origen/destino) que cuentan como "en base"
EXCLUIR_CODIGOS = []            # códigos de camión a dejar fuera, p. ej. ["127", "128C", "131C", "134C"]
PRODUCTOS_NO_DIESEL = [1001354] # m_product_id de cargas que no son diésel (por confirmar con la empresa)
H_MISMA_PARADA  = 2             # cargas del mismo camión a menos de estas horas = una sola parada
TOL_DIF_ODO_KM  = 150           # desvío tolerado del odómetro del sistema respecto del GPS antes de considerarlo error
TOL_PASO        = 0.10          # tolerancia para decidir que un viaje pasa por la base (origen→base + base→destino ≈ km)
VEL_VIAJE       = 50            # km/h promedio de un viaje, incluidas detenciones (solo para estimar ventanas de tiempo)
TOL_POS_H       = 36            # holgura en horas al ubicar un llenado en la cadena de viajes
TOL_CIERRE      = 0.12          # un bloque "cierra" si km odómetro / km viajes está a ±12% de la razón habitual del camión
MAX_UNION       = 4             # máximo de bloques vecinos que se unen cuando no cierran por separado
KM_DIA_MAX      = 1200          # km máximos por día, para descartar lecturas de odómetro imposibles
FECHA_CAMBIO    = "2024-10-01"  # compara la regresión antes y después de esta fecha (fin del Round Trip Walmart); None = no comparar
PESO_POR_ANIO   = True          # usar el peso cargado/vacío de cada año cuando la regresión anual lo entrega
KM_LOCAL        = 50            # viajes de hasta estos km se consideran locales
PESO_CARGADO    = None          # peso del km cargado frente al vacío; None = usar el de la regresión

pd.set_option("display.width", 200, "display.max_columns", 30, "display.max_rows", 200,
              "display.float_format", lambda x: f"{x:,.2f}")
SALIDA.mkdir(exist_ok=True)
_archivo_actual = None

def titulo(codigo, texto):
    """Inicia un bloque: reinicia eda_out/<codigo>.txt y escribe el encabezado."""
    global _archivo_actual
    _archivo_actual = SALIDA / f"{codigo}.txt"
    _archivo_actual.write_text("", encoding="utf-8")
    p("=" * 78, f"{codigo}. {texto}", "=" * 78)

def p(*objs):
    """Imprime y guarda en el archivo del bloque."""
    for o in objs:
        s = o.to_string() if isinstance(o, (pd.DataFrame, pd.Series)) else str(o)
        print(s)
        if _archivo_actual is not None:
            with open(_archivo_actual, "a", encoding="utf-8") as f:
                f.write(s + "\n")

def pct(n, d):
    return f"{n:,} de {d:,} ({(100 * n / d if d else 0):.1f}%)"

def cuantiles(s, nombre=None):
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    q = s.quantile([.01, .05, .25, .5, .75, .95, .99]) if len(s) else pd.Series(dtype=float)
    out = pd.Series({"n": len(s), "min": s.min() if len(s) else np.nan,
                     "p01": q.get(.01), "p05": q.get(.05), "p25": q.get(.25), "p50": q.get(.5),
                     "p75": q.get(.75), "p95": q.get(.95), "p99": q.get(.99),
                     "max": s.max() if len(s) else np.nan}, name=nombre)
    return out

def tabla_cuantiles(d):
    """d = {nombre: serie} → tabla con una fila por serie."""
    return pd.DataFrame([cuantiles(s, k) for k, s in d.items()])

def a_num(s):
    return pd.to_numeric(s, errors="coerce").astype("float64")

def a_id(s):
    """ID numérico como int64; nulos = -1 (evita problemas con Decimal y nulos)."""
    return pd.to_numeric(s, errors="coerce").round().fillna(-1).astype("int64")

def a_fecha(s):
    s = pd.to_datetime(s, errors="coerce")
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("America/Santiago").dt.tz_localize(None)
    return s.astype("datetime64[ns]").dt.floor("s")

def a_txt(s):
    """Clave de texto; nulos = "". Si la columna es numérica, se usa el entero como texto."""
    if pd.api.types.is_numeric_dtype(s):
        return a_id(s).astype(str).replace("-1", "")
    return s.astype("object").where(s.notna(), "").astype(str).str.strip()

def a_bool(s):
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.upper().isin(["TRUE", "Y", "1", "S", "SI"])

def tiene(df, col):
    """True si la columna existe y tiene algún dato."""
    if col not in df.columns:
        return False
    s = df[col]
    if s.dtype == "int64":
        return bool((s != -1).any())
    if s.dtype == bool:
        return True
    return bool((s.notna() & (s.astype(str) != "")).any())

def normalizar_texto(s):
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s.upper())
    return re.sub(r"\s+", " ", s).strip()

print("Configuración lista. Salidas en:", SALIDA.resolve())

# ====================================================================================================
# ============================== 1. CARGA Y ESQUEMA ==============================
titulo("00_esquema", "Esquema de las tablas y mapeo de columnas")

crudo, MAPA = {}, {}
def buscar_parquet(archivo, rutas=None):
    """El archivo en la extracción de octubre (con o sin prefijo adempiere_) y, si no está, en la de marzo."""
    for r in (rutas or (RUTA_ERP, RUTA)):
        for pref in ("adempiere_", ""):
            if (r / (pref + archivo)).exists():
                return r / (pref + archivo)
    return None


def nombres_estandar(df, nombre):
    """Renombra las columnas al primer nombre de COLS (las extracciones no siempre usan el mismo)."""
    minus = {c.lower(): c for c in df.columns}
    ren = {}
    for k, cands in COLS[nombre].items():
        col = next((minus[x.lower()] for x in cands if x.lower() in minus), None)
        if col is not None and col != cands[0] and cands[0] not in df.columns:
            ren[col] = cands[0]
    df=df.rename(columns=ren)
    return normalizar_cargas(df) if nombre=='combustible' else df


def leer_cargas():
    """Cargas de combustible: octubre completo + las de marzo que no están en octubre (por sit_petroleum_id).
    2026-10-25: la extracción de octubre no trae cargas COP entre abril y junio de 2025 (0 litros en 2025-T2, contra
    ~150.000 L por trimestre); por eso ya no se descartan las de marzo dentro del periodo de octubre."""
    nuevo = buscar_parquet(ARCHIVOS["combustible"], (RUTA_ERP,))
    viejo = buscar_parquet(ARCHIVOS["combustible"], (RUTA,))
    partes, texto = [], []
    if nuevo is not None:
        n = nombres_estandar(pd.read_parquet(nuevo), "combustible")
        partes.append(n)
        texto.append(f"{nuevo.name} ({len(n):,} cargas, {pd.to_datetime(n['datetrx']).min():%Y-%m-%d} a "
                     f"{pd.to_datetime(n['datetrx']).max():%Y-%m-%d})")
    if viejo is not None and viejo != nuevo:
        v = nombres_estandar(pd.read_parquet(viejo), "combustible")
        if partes:
            corte = pd.to_datetime(partes[0]["datetrx"]).min()
            v = v[~v["sit_petroleum_id"].isin(partes[0]["sit_petroleum_id"])]
            dentro = int((pd.to_datetime(v["datetrx"]) >= corte).sum())
        else:
            dentro = 0
        partes.append(v)
        texto.append(f"{viejo.parent.name}/{viejo.name} ({len(v):,} cargas que no están en octubre; "
                     f"{dentro:,} de ellas dentro del periodo de octubre)")
    if not partes:
        return None, "no se encontró"
    return normalizar_cargas(pd.concat(partes, ignore_index=True, sort=False)), " + ".join(texto)


def leer_viajes():
    """Equivalente a Fact_Ventas_RT: ciclos.fact_viaje_ciclo (DW) + columnas de travelvta que usa el método."""
    con = _db.conectar(("ciclos",))
    c = con.df("""SELECT c.id_viaje, c.nro_viaje, c.equipo_id, c.orden_procesado, c.ciclo_comercial, c.sk_fecha,
                         c.sit_stretch_id, c.km::float8 AS km, c.ciudad_origen_limpia, c.ciudad_destino_limpia,
                         c.producto_id, c.tipo_movimiento, c.es_virtual, t.tramo
                  FROM ciclos.fact_viaje_ciclo c
                  LEFT JOIN ciclos.dim_tramo_base t ON t.sit_stretch_id = c.sit_stretch_id""")
    con.close()
    ruta_vta = buscar_parquet("sit_pbix_travelvta_v.parquet", (RUTA_ERP,))
    cols = ["c_orderline_id", "created", "enddatetravel", "sit_secondaryteam_id", "sit_driver_v_id", "sit_roadmap_id",
            "documentno", "ciuorigen_id", "ciudestino_id", "m_product_id"]
    vta = pd.read_parquet(ruta_vta, columns=cols).drop_duplicates("c_orderline_id")
    vta["c_orderline_id"] = pd.to_numeric(vta["c_orderline_id"], errors="coerce").astype("Int64")
    c["nro_viaje"] = pd.to_numeric(c["nro_viaje"], errors="coerce").astype("Int64")
    c = c.merge(vta, left_on="nro_viaje", right_on="c_orderline_id", how="left")
    df = pd.DataFrame({
        "c_orderline_id": c["id_viaje"].astype(str),                     # número del viaje, o V-<camión>-<n> si es virtual
        "datepromised": pd.to_datetime(c["sk_fecha"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce"),
        "enddatetravel": c["enddatetravel"], "created": c["created"],
        "orden_procesado": c["orden_procesado"], "sit_mainteam_id": c["equipo_id"],
        "sit_secondaryteam_id": c["sit_secondaryteam_id"],
        "sit_stretch_id": c["sit_stretch_id"].where(c["sit_stretch_id"] != -999),
        "m_product_id": c["producto_id"].where(c["producto_id"].notna(), c["m_product_id"]),
        "Ciudad_Origen_Limpia": c["ciudad_origen_limpia"], "Ciudad_Destino_Limpia": c["ciudad_destino_limpia"],
        "ciuorigen_id": c["ciuorigen_id"], "ciudestino_id": c["ciudestino_id"],
        "kilometers": c["km"], "tramo": c["tramo"].fillna(""), "Tipo_Movimiento": c["tipo_movimiento"],
        "ciclo_comercial": c["ciclo_comercial"], "Es_Virtual": c["es_virtual"].eq("S"),
        "sit_driver_v_id": c["sit_driver_v_id"], "sit_roadmap_id": c["sit_roadmap_id"], "documentno": c["documentno"]})
    return df, f"ciclos.fact_viaje_ciclo ({len(df):,} viajes) + {ruta_vta.name}"


for nombre, archivo in ARCHIVOS.items():
    if nombre == "combustible":
        df, origen = leer_cargas()
    elif nombre == "viajes":
        df, origen = leer_viajes()
    else:
        ruta = buscar_parquet(archivo)
        df, origen = (pd.read_parquet(ruta), f"{ruta.parent.name}/{ruta.name}") if ruta is not None else (None, "")
    if df is None:
        p(f"[{nombre}] no se encontró {archivo}" + (" (opcional, se omite)" if nombre == "tipocarga" else "  <-- REVISAR"))
        continue
    archivo = origen
    crudo[nombre] = df
    p("", f"[{nombre}] {archivo}: {len(df):,} filas, {df.shape[1]} columnas")
    esquema = pd.DataFrame({"tipo": df.dtypes.astype(str), "nulos_%": (100 * df.isna().mean()).round(1),
                            "distintos": [df[c].nunique(dropna=True) if df[c].dtype != "object" or not
                                          isinstance(df[c].dropna().iloc[0] if df[c].notna().any() else "", (bytes, bytearray))
                                          else -1 for c in df.columns]})
    p(esquema)
    minus = {c.lower(): c for c in df.columns}
    MAPA[nombre] = {k: next((minus[x.lower()] for x in cands if x.lower() in minus), None)
                    for k, cands in COLS[nombre].items()}

p("", "MAPEO DE COLUMNAS DETECTADO (revísalo; corrige COLS en la celda 0 si algo no corresponde)")
for nombre, m in MAPA.items():
    p(f"[{nombre}] " + ", ".join(f"{k}={v if v else 'NO ENCONTRADA'}" for k, v in m.items()))

# ====================================================================================================
# ============================== 2. PREPARACIÓN ==============================
# Tablas de trabajo con nombres estándar: cg (cargas), vj (viajes), eq (equipos), tr (tramos), tc (tipo carga)
titulo("01_preparacion", "Ventana de análisis y tablas de trabajo")

def estandar(nombre, ids=(), nums=(), fechas=(), textos=()):
    df, m = crudo[nombre], MAPA[nombre]
    out = pd.DataFrame(index=df.index)
    for k, col in m.items():
        if col is None:
            out[k] = -1 if k in ids else (pd.NaT if k in fechas else ("" if k in textos else np.nan))
        elif k in ids:
            out[k] = a_id(df[col])
        elif k in nums:
            out[k] = a_num(df[col])
        elif k in fechas:
            out[k] = a_fecha(df[col])
        elif k in textos:
            out[k] = a_txt(df[col])
        else:
            out[k] = df[col]
    return out.reset_index(drop=True)

cg = estandar("combustible", ids=("id", "equipo", "conductor", "producto"),
              nums=("litros", "odo", "odo_gps", "horo", "precio"), fechas=("fecha", "fecha_reg"))
vj = estandar("viajes", ids=("camion", "rampla", "tramo", "producto", "conductor", "hoja"), nums=("orden", "km_rt"),
              fechas=("inicio", "fin", "creado"),
              textos=("id", "origen", "destino", "origen_id", "destino_id", "tipo_mov", "ciclo", "tramo_rt"))
eq = estandar("equipos", ids=("equipo",), nums=("rend_ficha",))
tr = estandar("tramos", ids=("tramo",), nums=("km",))
tc = estandar("tipocarga", ids=("producto",)) if "tipocarga" in crudo else None

# Columnas propias de la tabla de viajes (ciclos) que no vienen de la vista original (clasificación, ciclo, etc.)
_vista = {"c_orderline_id", "ad_client_id", "ad_org_id", "isactive", "created", "createdby", "updated", "updatedby",
          "documentno", "line", "datepromised", "enddatetravel", "c_bpartner_id", "sit_stretch_id", "m_product_id",
          "cantidad", "preciocot", "preciopoc", "minimum", "estimado", "docentrega", "valorordventa", "docfactura",
          "valorfactura", "sit_driver_v_id", "sit_internaldriver_id", "sit_externaldriver_id", "sitdocstatus",
          "tractoreo", "sit_serviceagreementline_id", "sit_mainteam_id", "sit_secondaryteam_id", "sit_roadmap_id",
          "comisionfunc", "porccomision", "comisionfuncadic", "porccomisionfle", "comisionflet", "sit_carrier_id",
          "ciuorigen_id", "ciudestino_id", "sit_fleetvalorization_id", "multiload", "grupo1", "grupo2", "cuenta",
          "descuenta", "c_elementvalue_description", "c_activity_id", "sit_businessunit_id", "dateacct", "amtsource",
          "estadoadm", "pdffactura", "isreturn"}
COLS_EXTRA_VIAJES = [c for c in crudo["viajes"].columns if c.lower() not in _vista]
for c in COLS_EXTRA_VIAJES:
    vj["x_" + c] = crudo["viajes"][c].values

# Un viaje = una fila. El ID original puede ser texto y repetirse en los viajes virtuales, así que:
#  - se descartan solo las filas idénticas de viajes no virtuales (vista multiplicada por facturas o asientos)
#  - se crea un ID propio (0, 1, 2, ...) para los cruces internos
vj["id_orig"] = vj["id"]
vj["virtual"] = a_bool(vj["virtual"]) if tiene(vj, "virtual") else False
_dup = vj.duplicated(["id_orig", "camion", "rampla", "tramo", "inicio"]) & ~vj["virtual"] & (vj["id_orig"] != "")
N_DUP_VJ = int(_dup.sum())
vj = vj[~_dup].copy()
N_ID_REP = int((vj.loc[~vj["virtual"] & (vj["id_orig"] != ""), "id_orig"].duplicated()).sum())
vj["id"] = np.arange(len(vj))

# Ventana: última carga - N años, para cargas y viajes
FECHA_MAX = cg["fecha"].max()
FECHA_MIN = FECHA_MAX - pd.DateOffset(years=ANIOS_VENTANA)
n_cg, n_vj = len(cg), len(vj)
cg = cg[(cg["fecha"] >= FECHA_MIN) & (cg["fecha"] <= FECHA_MAX)].copy()
vj = vj[(vj["inicio"] >= FECHA_MIN) & (vj["inicio"] <= FECHA_MAX)].copy()
p(f"Fecha de la carga: {MAPA['combustible']['fecha']} | inicio del viaje: {MAPA['viajes']['inicio']} | "
  f"origen y destino: {MAPA['viajes']['origen']}, {MAPA['viajes']['destino']}",
  f"Ventana: {FECHA_MIN:%Y-%m-%d} a {FECHA_MAX:%Y-%m-%d}",
  f"Cargas en ventana: {pct(len(cg), n_cg)}", f"Viajes en ventana: {pct(len(vj), n_vj)}",
  f"Filas de viajes descartadas por estar repetidas: {N_DUP_VJ:,} | IDs repetidos con contenido distinto (no virtuales): {N_ID_REP:,}",
  f"Viajes virtuales en ventana: {pct(int(vj['virtual'].sum()), len(vj))}")

# Odómetro y horómetro en cero = sin dato
CEROS = {c: int((cg[c] == 0).sum()) for c in ("odo", "odo_gps", "horo")}
for c in ("odo", "odo_gps", "horo"):
    cg.loc[cg[c] <= 0, c] = np.nan

# Km y nombre del tramo: se usa el de ciclos (incluye virtuales) si existe; si no, el de la tabla de tramos
vj = vj.merge(tr.drop_duplicates("tramo").rename(columns={"nombre": "tramo_nombre", "km": "km_tabla"}), on="tramo", how="left")
N_KM_DIF = int((vj["km_rt"].notna() & vj["km_tabla"].notna() & ((vj["km_rt"] - vj["km_tabla"]).abs() > 0.5)).sum())
vj["km"] = vj["km_rt"].where(vj["km_rt"].notna(), vj["km_tabla"])
vj["tramo_nombre"] = vj["tramo_rt"].where(vj["tramo_rt"] != "", vj["tramo_nombre"])
INICIO_SIN_HORA = bool((vj["inicio"] == vj["inicio"].dt.normalize()).mean() > 0.95)

# Rol de cada equipo según cómo aparece en los viajes
CAMIONES, RAMPLAS = set(vj["camion"]) - {-1}, set(vj["rampla"]) - {-1}
def _rol(a):
    c, r = a in CAMIONES, a in RAMPLAS
    return "ambos" if c and r else "camion" if c else "rampla" if r else "sin_viajes"
cg["rol"] = cg["equipo"].map(_rol)
cg = cg.merge(eq[["equipo", "codigo", "categoria", "grupo"]].drop_duplicates("equipo"), on="equipo", how="left")
_n = cg.groupby("equipo").size()
CAMIONES_OK = {e for e in CAMIONES if _n.get(e, 0) >= MIN_CARGAS}     # camiones con cargas suficientes para analizar

# Intervalos entre cargas consecutivas del mismo equipo
cg = cg.sort_values(["equipo", "fecha", "id"]).reset_index(drop=True)
g = cg.groupby("equipo", sort=False)
cg["fecha_ant"] = g["fecha"].shift()
cg["d_horas"] = (cg["fecha"] - cg["fecha_ant"]).dt.total_seconds() / 3600
cg["d_odo"], cg["d_gps"], cg["d_horo"] = g["odo"].diff(), g["odo_gps"].diff(), g["horo"].diff()
cg["d_km"] = cg["d_odo"] if ODO_PRINCIPAL == "odo" else cg["d_gps"]
ok = (cg["litros"] > 0) & (cg["d_km"] > 0)
cg["rend"] = np.where(ok, cg["d_km"] / cg["litros"].where(cg["litros"] > 0), np.nan)
cg["dir_norm"] = cg["direccion"].map(normalizar_texto)
cg["es_base"] = cg["proveedor"].astype(str).str.strip().str.upper().eq(PROVEEDOR_BASE)

# Cadena de viajes por camión (según orden_procesado; si no existe, por fecha de inicio)
CLAVE_ORDEN = ["orden", "inicio", "id"] if tiene(vj, "orden") else ["inicio", "id"]
vj = vj.sort_values(["camion"] + CLAVE_ORDEN).reset_index(drop=True)
gv = vj.groupby("camion", sort=False)
vj["inicio_sig"] = gv["inicio"].shift(-1)
vj["origen_sig"] = gv["origen"].shift(-1).fillna("")
vj["fin_ant"] = gv["fin"].shift()
vj["n_mismo_inicio"] = vj.groupby(["camion", "inicio"])["id"].transform("size")
vj["ranura_h"] = (vj["inicio_sig"] - vj["inicio"]).dt.total_seconds() / 3600

def asignar_ranura(cargas, viajes, clave):
    """Para cada carga, el último viaje del mismo equipo que partió antes (ranura = inicio a inicio siguiente)."""
    cols = ["id", "inicio", "tramo", "km", "tramo_nombre", "origen", "destino", "camion", "rampla", "producto",
            "tipo_mov", "n_mismo_inicio"]
    c = cargas.dropna(subset=["fecha"]).sort_values("fecha")
    # con varios viajes del mismo día (inicio sin hora), la carga queda en el último según el orden de la cadena
    v = viajes[viajes[clave] != -1].dropna(subset=["inicio"]).sort_values(["inicio", "orden", "id"])[cols].copy()
    v.columns = ["v_" + x for x in cols]
    return pd.merge_asof(c, v, left_on="fecha", right_on="v_inicio", left_by="equipo", right_by="v_" + clave,
                         direction="backward")

# Cargas de camión y de rampla con su ranura
cgc = asignar_ranura(cg[cg["rol"].isin(["camion", "ambos"])], vj, "camion")
cgc = cgc.merge(vj[["id", "inicio_sig", "origen_sig", "ranura_h"]].rename(
    columns={"id": "v_id", "inicio_sig": "v_inicio_sig", "origen_sig": "v_origen_sig", "ranura_h": "v_ranura_h"}),
    on="v_id", how="left")
cgc["h_desde_inicio"] = (cgc["fecha"] - cgc["v_inicio"]).dt.total_seconds() / 3600
cgr = asignar_ranura(cg[cg["rol"] == "rampla"], vj, "rampla")
cgr["h_desde_inicio"] = (cgr["fecha"] - cgr["v_inicio"]).dt.total_seconds() / 3600

assert len(cgc) == int(cg["rol"].isin(["camion", "ambos"]).sum()), "el cruce carga-viaje duplicó filas: revisar el ID de viaje"
p(f"Cargas de camión: {len(cgc):,} | de rampla: {len(cgr):,} | de equipos sin viajes: {(cg['rol'] == 'sin_viajes').sum():,}",
  f"Camiones con viajes: {len(CAMIONES):,} | ramplas con viajes: {len(RAMPLAS):,}",
  f"Orden de la cadena de viajes: {' > '.join(CLAVE_ORDEN)}",
  f"Inicio del viaje sin hora (solo fecha): {'sí' if INICIO_SIN_HORA else 'no'}",
  f"Viajes cuyo km en ciclos difiere del de la tabla de tramos: {N_KM_DIF:,}",
  f"Columnas propias de la tabla de viajes (ciclos): {COLS_EXTRA_VIAJES}")

# ====================================================================================================
titulo("P1", "Paradas de carga de camión (diésel) y odómetro limpio")
_excl = set(eq.loc[eq["codigo"].astype(str).isin([str(x) for x in EXCLUIR_CODIGOS]), "equipo"])
CAMS = sorted(CAMIONES_OK - _excl)
c0 = cg[cg["equipo"].isin(CAMS)].copy()
no_diesel = c0["producto"].isin(PRODUCTOS_NO_DIESEL)
malas = c0["litros"] <= 0
p(f"Camiones analizados: {len(CAMS):,} (excluidos por código: {len(CAMIONES_OK & _excl):,})",
  f"Cargas: {len(c0):,} | litros: {c0['litros'].sum():,.0f}",
  f"  de otro producto {PRODUCTOS_NO_DIESEL}: {pct(int(no_diesel.sum()), len(c0))} | litros: {c0.loc[no_diesel, 'litros'].sum():,.0f}",
  f"  con litros <= 0: {int(malas.sum()):,}")
c0 = c0[~no_diesel & ~malas].sort_values(["equipo", "fecha", "id"])

# Parada = cargas del mismo camión separadas por menos de H_MISMA_PARADA horas
_gap = c0.groupby("equipo")["fecha"].diff().dt.total_seconds() / 3600
c0["parada"] = (_gap.isna() | (_gap > H_MISMA_PARADA)).cumsum()
pa = c0.groupby("parada").agg(equipo=("equipo", "first"), fecha=("fecha", "max"), litros=("litros", "sum"), odo=("odo", "max"),
                              odo_gps=("odo_gps", "max"), es_base=("es_base", "max"), n_cargas=("id", "size"),
                              dir_norm=("dir_norm", "first")).sort_values(["equipo", "fecha"]).reset_index()   # conserva el n° de parada
p(f"Paradas: {len(pa):,} | con más de una carga: {pct(int((pa['n_cargas'] > 1).sum()), len(pa))} | en base: {pct(int(pa['es_base'].sum()), len(pa))}")

# Odómetro limpio. El GPS no tiene errores de digitación pero a veces queda pegado; el del sistema es al revés.
# 1) lectura GPS pegada (igual a la anterior aunque el del sistema avanzó) = sin dato
# 2) la diferencia sistema - GPS es estable por camión: si una lectura del sistema se aleja de esa diferencia, es un error
# 3) nivel limpio = sistema si pasa el control; si no, GPS + diferencia habitual
g = pa.groupby("equipo", sort=False)
gps = pa["odo_gps"].copy()
pegado = (g["odo_gps"].diff() == 0) & (g["odo"].diff().abs() > 50)
gps[pegado] = np.nan
pa["_dif"] = pa["odo"] - gps
dif_med = pa.groupby("equipo", sort=False)["_dif"].transform(lambda s: s.rolling(11, center=True, min_periods=3).median())
dif_med = dif_med.groupby(pa["equipo"], sort=False).transform(lambda s: s.ffill().bfill())
man_ok = pa["_dif"].notna() & ((pa["_dif"] - dif_med).abs() <= TOL_DIF_ODO_KM)
gps_ok = gps.notna() & dif_med.notna()
pa["nivel"] = np.where(man_ok, pa["odo"], np.where(gps_ok, gps + dif_med, np.nan))
pa["fuente_km"] = np.where(man_ok, "sistema (verificado con GPS)", np.where(gps_ok, "GPS corregido", "sin dato"))
# sin GPS para comparar: se acepta el odómetro del sistema si avanza respecto de la última lectura válida
# y no supera la siguiente lectura válida
sig = pa.groupby("equipo", sort=False)["nivel"].transform(lambda s: s.bfill().shift(-1)).values
niv, fue, odo_, eqp = pa["nivel"].values.copy(), pa["fuente_km"].values.copy(), pa["odo"].values, pa["equipo"].values
ult, cam_ant = np.nan, None
for i in range(len(pa)):
    if eqp[i] != cam_ant:
        ult, cam_ant = np.nan, eqp[i]
    if np.isnan(niv[i]) and not np.isnan(odo_[i]):
        if (np.isnan(ult) or 0 <= odo_[i] - ult <= 3 * DKM_MAX) and (np.isnan(sig[i]) or odo_[i] <= sig[i]):
            niv[i], fue[i] = odo_[i], "sistema (sin GPS, coherente)"
    if not np.isnan(niv[i]):
        ult = niv[i]
pa["nivel"], pa["fuente_km"] = niv, fue
# Control final: se descarta una lectura que es incoherente tanto con la lectura válida anterior como con la siguiente
# (retrocede, o avanza más de lo posible para los días transcurridos)
N_SALTOS = 0
for _ in range(3):
    val = pa["nivel"].notna()
    _t = pa["fecha"].where(val)
    ge = pa["equipo"]
    ant_n = pa.groupby("equipo", sort=False)["nivel"].transform(lambda s: s.ffill().shift())
    sig_n = pa.groupby("equipo", sort=False)["nivel"].transform(lambda s: s.bfill().shift(-1))
    ant_t = _t.groupby(ge, sort=False).transform(lambda s: s.ffill().shift())
    sig_t = _t.groupby(ge, sort=False).transform(lambda s: s.bfill().shift(-1))
    lim_a = DKM_MAX + KM_DIA_MAX * (pa["fecha"] - ant_t).dt.total_seconds() / 86400
    lim_s = DKM_MAX + KM_DIA_MAX * (sig_t - pa["fecha"]).dt.total_seconds() / 86400
    mal_a = (pa["nivel"] - ant_n < 0) | (pa["nivel"] - ant_n > lim_a)
    mal_s = (sig_n - pa["nivel"] < 0) | (sig_n - pa["nivel"] > lim_s)
    quitar = val & ((mal_a & mal_s) | (mal_a & sig_n.isna()) | (mal_s & ant_n.isna()))
    if not quitar.any():
        break
    N_SALTOS += int(quitar.sum())
    pa.loc[quitar, ["nivel", "fuente_km"]] = [np.nan, "descartado por salto"]
p("", "Origen del odómetro limpio por parada:", (100 * pa["fuente_km"].value_counts(normalize=True)).round(1).to_frame("%").T,
  f"  lecturas GPS pegadas descartadas: {pct(int(pegado.sum()), len(pa))}",
  f"  lecturas del sistema descartadas por no calzar con el GPS: {pct(int((pa['_dif'].notna() & ~man_ok).sum()), len(pa))}")
_d = pa.groupby("equipo", sort=False)["nivel"].diff()
p(f"  lecturas descartadas en el control final por salto imposible: {N_SALTOS:,}",
  f"  nivel limpio que aún retrocede respecto de la parada anterior: {pct(int((_d < 0).sum()), int(_d.notna().sum()))}",
  "", "Km entre paradas con odómetro limpio:", cuantiles(_d[_d > 0]).to_frame().T)

# ====================================================================================================
titulo("P2", "Relación de cada viaje con la base y distancias que faltan")
Z = set(ZONA_BASE)
vp = vj[vj["camion"].isin(CAMS)].sort_values(["camion"] + CLAVE_ORDEN).copy()
vp["j"] = vp.groupby("camion", sort=False).cumcount()
vp["km"] = vp["km"].fillna(0.0)
_tok = vp["tramo_nombre"].fillna("").map(lambda s: [normalizar_texto(re.sub(r"\(.*?\)", " ", t)) for t in str(s).split("/")])
vp["n_tok"] = _tok.map(len)
vp["o_z"], vp["d_z"] = vp["origen"].isin(Z), vp["destino"].isin(Z)

# Distancia de cada ciudad a la base: km más frecuente de los tramos directos entre esa ciudad y la zona base
_dir = vp[~vp["virtual"] & (vp["km"] > 0) & (vp["n_tok"] == 2)]
_a = _dir[_dir["d_z"] & ~_dir["o_z"]][["origen", "km"]].rename(columns={"origen": "ciudad"})
_b = _dir[_dir["o_z"] & ~_dir["d_z"]][["destino", "km"]].rename(columns={"destino": "ciudad"})
db = pd.concat([_a, _b]).groupby("ciudad")["km"].agg(km_base=lambda s: s.mode().iloc[0], viajes="size", km_min="min", km_max="max")
DIST = db["km_base"].to_dict()
_manual = RUTA / "distancias_base_manual.csv"
if _manual.exists():
    _m = pd.read_csv(_manual).dropna(subset=["km_base"])
    DIST.update(dict(zip(_m["ciudad"].astype(str).str.strip(), _m["km_base"].astype(float))))
    p(f"Se cargaron {len(_m):,} distancias desde {_manual.name}")
for z in Z:
    DIST[z] = 0.0
vp["do"], vp["dd"] = vp["origen"].map(DIST), vp["destino"].map(DIST)
tri = ((vp["do"] + vp["dd"] - vp["km"]).abs() <= np.maximum(TOL_PASO * vp["km"], 40)) & (vp["do"] > 0) & (vp["dd"] > 0)
inter = _tok.map(lambda t: any(x in Z for x in t[1:-1]))             # la base aparece como parada intermedia del tramo
pasa = ~vp["o_z"] & ~vp["d_z"] & (tri | inter)
vp["rel"] = np.select([vp["o_z"] & vp["d_z"], vp["o_z"], vp["d_z"], pasa], ["local", "sale", "llega", "pasa"], "ajeno")
_pos_tok = _tok.map(lambda t: next((i / (len(t) - 1) for i, x in enumerate(t) if x in Z and 0 < i < len(t) - 1), np.nan))
vp["frac"] = (vp["do"] / (vp["do"] + vp["dd"])).where(tri, _pos_tok).clip(0.05, 0.95)
vp["sin_dist"] = ~vp["o_z"] & ~vp["d_z"] & ~inter & (vp["do"].isna() | vp["dd"].isna())

t = vp.groupby("rel").agg(viajes=("id", "size"), km=("km", "sum"))
t["km_%"] = (100 * t["km"] / t["km"].sum()).round(1)
p("Viajes de los camiones analizados según su relación con la zona base " + str(sorted(Z)) + ":", t)
p("", "Distancia a la base de las ciudades con más viajes (revísalas: de aquí sale el punto donde se parte un viaje de paso):",
  db.sort_values("viajes", ascending=False).head(25))
p("", "Tramos de paso más frecuentes y fracción del viaje recorrida al llegar a la base:",
  vp[vp["rel"] == "pasa"].groupby("tramo_nombre").agg(viajes=("id", "size"), km=("km", "first"), frac=("frac", "median"))
  .sort_values("viajes", ascending=False).head(15))
falt = pd.concat([vp.loc[vp["sin_dist"] & vp["do"].isna(), ["origen", "km"]].rename(columns={"origen": "ciudad"}),
                  vp.loc[vp["sin_dist"] & vp["dd"].isna(), ["destino", "km"]].rename(columns={"destino": "ciudad"})])
falt = falt.groupby("ciudad").agg(viajes=("km", "size"), km=("km", "sum")).sort_values("km", ascending=False)
p("", f"Viajes que no tocan la base y no se pueden clasificar por falta de distancia: {pct(int(vp['sin_dist'].sum()), len(vp))} "
  f"({100 * vp.loc[vp['sin_dist'], 'km'].sum() / max(vp['km'].sum(), 1):.1f}% de los km)")
if len(falt):
    p("Ciudades sin distancia a la base (las que más km afectan):", falt.head(20))
    falt.assign(km_base="").to_csv(SALIDA / "ciudades_sin_distancia.csv")
    p(f"Lista completa en {SALIDA / 'ciudades_sin_distancia.csv'}. Completa km_base y guárdala como {_manual.name} junto a los parquet.")
p("", "Tramos 'ajenos' con más km (no tocan la base; conviene revisar que realmente no pasen por ella):",
  vp[vp["rel"] == "ajeno"].groupby("tramo_nombre").agg(viajes=("id", "size"), km=("km", "sum"), km_tramo=("km", "first"),
                                                         do=("do", "first"), dd=("dd", "first")).sort_values("km", ascending=False).head(15))

# ====================================================================================================
titulo("P3", "Ubicación de los llenados en base en la cadena de viajes")
_ok = vp["creado"].notna() & ((vp["creado"] - vp["inicio"]).abs() <= pd.Timedelta(hours=36))
vp["ref"] = vp["creado"].where(_ok, vp["inicio"] + pd.Timedelta(hours=12))
vp["ref_h"] = vp["ref"].astype("int64") / 3.6e12
pa["t_h"] = pa["fecha"].astype("int64") / 3.6e12
pa["x"], pa["tipo_pos"], pa["dist_h"], pa["n_cand"] = np.nan, "", np.nan, 0
_viajes_cam = {k: v for k, v in vp.groupby("camion", sort=False)}
for cam, s in pa.groupby("equipo", sort=False):
    v = _viajes_cam.get(cam)
    if v is None or not len(v):
        continue
    n, ref, km = len(v), v["ref_h"].values, v["km"].values
    oz, dz, rel, frac = v["o_z"].values, v["d_z"].values, v["rel"].values, v["frac"].values
    do = np.where(np.isnan(v["do"].values), frac * km, v["do"].values)
    lleg = ref + km / VEL_VIAJE
    m = dz[:-1] | oz[1:]                                        # frontera entre el viaje i y el i+1
    X, LO, HI = [np.arange(1, n)[m].astype(float)], [lleg[:-1][m]], [ref[1:][m] + 6]
    if oz[0]:
        X.append([0.0]); LO.append([-np.inf]); HI.append([ref[0] + 6])
    if dz[-1]:
        X.append([float(n)]); LO.append([lleg[-1]]); HI.append([np.inf])
    nf = sum(len(a) for a in X)
    jp = np.where(rel == "pasa")[0]                             # dentro de un viaje de paso
    tp = ref[jp] + do[jp] / VEL_VIAJE
    X.append(jp + frac[jp]); LO.append(tp - 6); HI.append(tp + 12)
    X, LO, HI = np.concatenate(X), np.concatenate(LO), np.concatenate(HI)
    LO, HI = np.minimum(LO, HI), np.maximum(LO, HI)
    TIPO = np.r_[np.zeros(nf), np.ones(len(jp))]
    x_ant = -np.inf
    for idx, th in zip(s.index[s["es_base"].values], s.loc[s["es_base"].values, "t_h"].values):
        cand = np.where((X >= x_ant) & (th >= LO - TOL_POS_H) & (th <= HI + TOL_POS_H))[0]
        if not len(cand):
            continue
        d = np.maximum(np.maximum(LO[cand] - th, th - HI[cand]), 0)
        k = cand[np.lexsort((X[cand], TIPO[cand], d))[0]]       # menor distancia; a igualdad, frontera y la más temprana
        pa.at[idx, "x"], pa.at[idx, "tipo_pos"] = X[k], "frontera" if TIPO[k] == 0 else "de paso"
        pa.at[idx, "dist_h"], pa.at[idx, "n_cand"] = d[np.where(cand == k)[0][0]], len(cand)
        x_ant = X[k]
bs = pa[pa["es_base"]]
p(f"Llenados en base: {len(bs):,}",
  f"  ubicados en una frontera entre viajes: {pct(int((bs['tipo_pos'] == 'frontera').sum()), len(bs))}",
  f"  ubicados dentro de un viaje de paso: {pct(int((bs['tipo_pos'] == 'de paso').sum()), len(bs))}",
  f"  sin posición coherente (el camión no figura cerca de la base): {pct(int(bs['x'].isna().sum()), len(bs))}",
  "", f"  dentro de la ventana esperada (distancia 0): {pct(int((bs['dist_h'] == 0).sum()), int(bs['x'].notna().sum()))}",
  "  horas fuera de la ventana, cuando no calza exacto:", cuantiles(bs.loc[bs["dist_h"] > 0, "dist_h"]).to_frame().T,
  "", "  posiciones candidatas por llenado (1 = sin ambigüedad):",
  (100 * bs.loc[bs["x"].notna(), "n_cand"].clip(upper=5).value_counts(normalize=True).sort_index()).round(1).rename("% (5 = 5 o más)").to_frame().T)

# Cortes: llenados en base con posición que avanza en la cadena
pa["corte"] = False
for cam, s in pa.groupby("equipo", sort=False):
    ult = -np.inf
    for idx, x in zip(s.index, s["x"].values):
        if s.at[idx, "es_base"] and not np.isnan(x) and x > ult:
            pa.at[idx, "corte"] = True
            ult = x
pa["bloque"] = pa.groupby("equipo", sort=False)["corte"].transform(lambda s: s.cumsum().shift(fill_value=0)).astype(int)
p("", f"Llenados que cortan bloque: {pct(int(pa['corte'].sum()), len(bs))} (el resto repite posición o no la tiene; sus litros pasan al bloque siguiente)")

# ====================================================================================================
titulo("P4", "Bloques: litros, km de odómetro y km de viajes")
ct = pa[pa["corte"]].drop(columns=["bloque"]).copy()
ct["c"] = ct.groupby("equipo", sort=False).cumcount()
b0 = pa.groupby(["equipo", "bloque"]).agg(litros=("litros", "sum"), paradas=("litros", "size"),
                                         cargas_ruta=("es_base", lambda s: int((~s).sum()))).reset_index()
fin_ = ct.rename(columns={"c": "bloque", "fecha": "t_fin", "nivel": "nivel_fin"})[["equipo", "bloque", "t_fin", "nivel_fin"]]
ini_ = ct.assign(bloque=ct["c"] + 1).rename(columns={"fecha": "t_ini", "nivel": "nivel_ini"})[["equipo", "bloque", "t_ini", "nivel_ini"]]
b0 = b0.merge(ini_, on=["equipo", "bloque"], how="inner").merge(fin_, on=["equipo", "bloque"], how="inner")

# Tramos de viaje dentro de cada bloque (un viaje de paso se parte en el punto de la base)
if tc is not None:
    _vac = set(tc.loc[tc["vacio"].astype(str).str.upper().isin(["Y", "TRUE", "1", "S"]), "producto"])
    vp["vacio_fis"] = vp["virtual"] | vp["producto"].isin(_vac)
    vp = vp.merge(tc.drop_duplicates("producto")[["producto", "nombre"]].rename(columns={"nombre": "prod_nombre"}), on="producto", how="left")
else:
    vp["vacio_fis"] = vp["virtual"] | vp["tipo_mov"].str.upper().str.startswith("VAC")
    vp["prod_nombre"] = ""
_pn = vp["prod_nombre"].fillna("").map(normalizar_texto)
vp["grupo"] = np.select(
    [vp["vacio_fis"], _pn.str.contains("WALMART"), _pn.str.contains("PALLET|UULL|DEVOLUCION"),
     _pn.str.contains("VEGETAL|FRUTA|PALTA|TOMATE"), _pn.str.contains("FRIO|MULTITEMP|CONGELAD")],
    ["vacio", "walmart", "pallets_devol", "vegetales", "frio"], "seco_otros")
piezas = []
_cortes_cam = {k: v["x"].values for k, v in ct.groupby("equipo", sort=False)}
for cam, v in vp.groupby("camion", sort=False):
    cx = _cortes_cam.get(cam, np.array([]))
    j = v["j"].values.astype(float)
    k_lo, k_hi = np.searchsorted(cx, j, "right"), np.searchsorted(cx, j + 1, "left")
    simple = k_lo == k_hi
    piezas.append(pd.DataFrame({"id": v["id"].values[simple], "bloque": k_hi[simple], "parte": 1.0}))
    for i in np.where(~simple)[0]:
        pts = np.r_[j[i], cx[k_lo[i]:k_hi[i]], j[i] + 1]
        piezas.append(pd.DataFrame({"id": v["id"].values[i], "bloque": np.searchsorted(cx, pts[1:], "left"), "parte": np.diff(pts)}))
pz = pd.concat(piezas, ignore_index=True).merge(
    vp[["id", "camion", "km", "vacio_fis", "grupo", "inicio"]], on="id", how="left").rename(columns={"camion": "equipo"})
pz["km_p"] = pz["km"] * pz["parte"]
kb0 = pz.groupby(["equipo", "bloque"])["km_p"].sum().rename("km_viajes").reset_index()
b0 = b0.merge(kb0, on=["equipo", "bloque"], how="left").fillna({"km_viajes": 0.0}).sort_values(["equipo", "bloque"]).reset_index(drop=True)

# Razón habitual odómetro/viajes de cada camión (el odómetro suele marcar algo más que la tabla de tramos)
_r = (b0["nivel_fin"] - b0["nivel_ini"]) / b0["km_viajes"].where(b0["km_viajes"] > 0)
_rv = _r.where(_r.between(0.7, 1.4))
CENTRO_GLOBAL = float(_rv.median())
_cc = _rv.groupby(b0["equipo"]).agg(["median", "count"])
b0["centro"] = b0["equipo"].map(_cc["median"].where(_cc["count"] >= 20)).fillna(CENTRO_GLOBAL)

def _cierra(km_o, km_v, lit, centro):
    return bool(km_o > 0 and km_v > 0 and lit > 0 and centro * (1 - TOL_CIERRE) <= km_o / km_v <= centro * (1 + TOL_CIERRE)
                and RANGO_REND[0] <= km_o / lit <= RANGO_REND[1])

# Unión de bloques vecinos
grupo_u = np.zeros(len(b0), dtype=int)
for cam, d in b0.groupby("equipo", sort=False):
    idx = d.index.values
    ni, nf, kv, li, ce = d["nivel_ini"].values, d["nivel_fin"].values, d["km_viajes"].values, d["litros"].values, d["centro"].values
    g, i, ini_g = 0, 0, 0
    while i < len(d):
        if _cierra(nf[i] - ni[i], kv[i], li[i], ce[i]):
            grupo_u[idx[i]] = g; ini_g = i; g += 1; i += 1
            continue
        hecho = False
        for j in range(i + 1, min(i + MAX_UNION, len(d))):               # hacia adelante
            if _cierra(nf[j] - ni[i], kv[i:j + 1].sum(), li[i:j + 1].sum(), ce[i]):
                grupo_u[idx[i:j + 1]] = g; ini_g = i; g += 1; i = j + 1; hecho = True
                break
        if hecho:
            continue
        if i > 0 and i - ini_g < MAX_UNION and _cierra(nf[i] - ni[ini_g], kv[ini_g:i + 1].sum(), li[ini_g:i + 1].sum(), ce[i]):
            grupo_u[idx[i]] = g - 1                                       # con el bloque anterior
        else:
            grupo_u[idx[i]] = g; ini_g = i; g += 1
        i += 1
b0["bloque_u"] = grupo_u
bl = b0.groupby(["equipo", "bloque_u"]).agg(litros=("litros", "sum"), paradas=("paradas", "sum"), cargas_ruta=("cargas_ruta", "sum"),
                                           t_ini=("t_ini", "first"), t_fin=("t_fin", "last"), nivel_ini=("nivel_ini", "first"),
                                           nivel_fin=("nivel_fin", "last"), centro=("centro", "first"), unidos=("bloque", "size")).reset_index()
pz = pz.merge(b0[["equipo", "bloque", "bloque_u"]], on=["equipo", "bloque"], how="left")
pz["km_c"], pz["km_v"] = pz["km_p"].where(~pz["vacio_fis"], 0.0), pz["km_p"].where(pz["vacio_fis"], 0.0)
pz["km_local"] = pz["km_p"].where(pz["km"] <= KM_LOCAL, 0.0)
kb = pz[pz["bloque_u"].notna()].groupby(["equipo", "bloque_u"]).agg(viajes=("id", "nunique"), km_viajes=("km_p", "sum"), km_c=("km_c", "sum"),
                                                                   km_v=("km_v", "sum"), km_local=("km_local", "sum")).reset_index()
bl = bl.merge(kb, on=["equipo", "bloque_u"], how="left").fillna({"viajes": 0, "km_viajes": 0, "km_c": 0, "km_v": 0, "km_local": 0})
bl["dias"] = (bl["t_fin"] - bl["t_ini"]).dt.total_seconds() / 86400
bl["km_odo"] = bl["nivel_fin"] - bl["nivel_ini"]
bl["razon"] = bl["km_odo"] / bl["km_viajes"].where(bl["km_viajes"] > 0)
bl["razon_rel"] = bl["razon"] / bl["centro"]
bl["rend_odo"] = bl["km_odo"] / bl["litros"]
bl["rend_viajes"] = bl["km_viajes"] / bl["litros"]

def _calidad(d):
    return np.select([d["km_viajes"] <= 0, d["km_odo"].isna(), d["km_odo"] <= 0, d["razon_rel"] < 1 - TOL_CIERRE, d["razon_rel"] > 1 + TOL_CIERRE,
                      ~d["rend_odo"].between(*RANGO_REND)],
                     ["sin viajes", "sin odómetro", "odómetro retrocede", "viajes de más (km viajes > odómetro)",
                      "km sin viajes (odómetro > km viajes)", "rendimiento fuera de rango"], "ok")
bl["calidad"] = _calidad(bl)
_b0 = b0.assign(km_odo=b0["nivel_fin"] - b0["nivel_ini"])
_b0["razon_rel"], _b0["rend_odo"] = _b0["km_odo"] / _b0["km_viajes"].where(_b0["km_viajes"] > 0) / _b0["centro"], _b0["km_odo"] / _b0["litros"]
_b0["calidad"] = _calidad(_b0)

tot_l = pa["litros"].sum()
p(f"Bloques entre dos llenados en base: {len(b0):,} en {b0['equipo'].nunique():,} camiones | tras unir vecinos: {len(bl):,}",
  f"Litros dentro de bloques: {bl['litros'].sum():,.0f} de {tot_l:,.0f} ({100 * bl['litros'].sum() / tot_l:.1f}%)",
  "", f"Razón habitual km odómetro / km viajes: global {CENTRO_GLOBAL:.3f}; por camión:", cuantiles(_cc.loc[_cc["count"] >= 20, "median"]).to_frame().T,
  f"Un bloque cierra si su razón está a ±{TOL_CIERRE:.0%} de la habitual de su camión y el rendimiento cae en {RANGO_REND} km/L.", "")
def _tabla(d, nombre):
    t = d.groupby("calidad").agg(bloques=("litros", "size"), litros=("litros", "sum"))
    t["bloques_%"], t["litros_%"] = (100 * t["bloques"] / t["bloques"].sum()).round(1), (100 * t["litros"] / t["litros"].sum()).round(1)
    p(nombre, t.sort_values("litros", ascending=False), "")
_tabla(_b0, "Calidad ANTES de unir bloques vecinos:")
_tabla(bl, "Calidad DESPUÉS de unir:")
p("Bloques finales según cuántos bloques originales reúnen:",
  bl.assign(n=bl["unidos"].clip(upper=4)).groupby("n").agg(bloques=("litros", "size"), ok=("calidad", lambda s: 100 * (s == "ok").mean()),
                                                         litros=("litros", "sum")).rename_axis("unidos (4 = 4 o más)"))
p("", tabla_cuantiles({"días": bl["dias"], "litros": bl["litros"], "km odómetro": bl["km_odo"], "km viajes": bl["km_viajes"],
                       "viajes": bl["viajes"], "razón odo/viajes": bl["razon"], "razón relativa al camión": bl["razon_rel"],
                       "km/L (odómetro)": bl["rend_odo"], "km/L (viajes)": bl["rend_viajes"]}))
bl["anio"] = bl["t_fin"].dt.year
p("", "Calidad por año (% de litros):",
  (100 * bl.pivot_table(index="anio", columns="calidad", values="litros", aggfunc="sum", fill_value=0)
   .pipe(lambda t: t.div(t.sum(axis=1), axis=0))).round(1))
p("", "Por año:", bl.groupby("anio").agg(bloques=("litros", "size"), razon_p50=("razon", "median"), rend_odo_p50=("rend_odo", "median"),
                                         litros_p50=("litros", "median"), dias_p50=("dias", "median"), km_cargado_pct=("km_c", lambda s: 100 * s.sum() / max(bl.loc[s.index, "km_viajes"].sum(), 1))))
_pc = bl.groupby("equipo").agg(bloques=("litros", "size"), ok_pct=("calidad", lambda s: 100 * (s == "ok").mean()), litros=("litros", "sum"))
_pc = _pc.join(eq.drop_duplicates("equipo").set_index("equipo")["codigo"])
p("", "Camiones con menor % de bloques ok (20 bloques o más):", _pc[_pc["bloques"] >= 20].sort_values("ok_pct").head(12))
bl.to_csv(SALIDA / "bloques.csv", index=False)

# ====================================================================================================
titulo("P5", "Regresión de litros por bloque contra km cargado y km vacío")
def ajustar(df, cols):
    """Mínimos cuadrados sin constante, con un segundo ajuste sin los residuos extremos. Devuelve coef, error estándar, cov, R2, n."""
    X, y = df[cols].values.astype(float), df["litros"].values.astype(float)
    for _ in range(2):
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        res = y - X @ beta
        mad = np.median(np.abs(res - np.median(res))) or 1.0
        keep = np.abs(res - np.median(res)) <= 3.5 * 1.4826 * mad
        if keep.all():
            break
        X, y = X[keep], y[keep]
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ beta
    cov = (res @ res / max(len(y) - len(cols), 1)) * np.linalg.pinv(X.T @ X)
    return beta, np.sqrt(np.diag(cov)), cov, 1 - (res @ res) / (y @ y), len(y)

def fila_cv(nombre, d):
    bt, s_, cv, r2c, nc = ajustar(d, ["km_c", "km_v"])
    return {"grupo": nombre, "bloques": nc, "L100_cargado": 100 * bt[0], "±c": 100 * 1.96 * s_[0], "L100_vacio": 100 * bt[1],
            "±v": 100 * 1.96 * s_[1], "peso": bt[0] / bt[1] if bt[1] > 0 else np.nan, "km_cargado_%": 100 * d["km_c"].sum() / d["km_viajes"].sum()}

rb = bl[bl["calidad"] == "ok"].copy()
PESO_REG, PESO_ANIO = np.nan, {}
if len(rb) >= 50:
    rb["mix"] = rb["km_c"] / rb["km_viajes"]
    p(f"Bloques usados: {len(rb):,}", "Proporción de km cargados por bloque (si casi todos los bloques tienen la misma mezcla, los dos",
      "coeficientes no se pueden separar bien):", cuantiles(rb["mix"]).to_frame().T,
      f"  bloques 100% cargados: {pct(int((rb['mix'] >= 0.98).sum()), len(rb))} | 100% vacíos: {pct(int((rb['mix'] <= 0.02).sum()), len(rb))} | "
      f"correlación km cargado vs km vacío: {rb['km_c'].corr(rb['km_v']):.2f}")
    beta, se, cov, r2, n = ajustar(rb, ["km_c", "km_v"])
    a, b = beta
    p("", f"Modelo global ({n:,} bloques tras quitar extremos). Los km son los de la tabla de tramos:",
      f"  km cargado: {100 * a:.1f} L/100 km (± {100 * 1.96 * se[0]:.1f})" + (f"  = {1 / a:.2f} km/L" if a > 0 else ""),
      f"  km vacío:   {100 * b:.1f} L/100 km (± {100 * 1.96 * se[1]:.1f})" + (f"  = {1 / b:.2f} km/L" if b > 0 else ""))
    if a > 0 and b > 0:
        PESO_REG = a / b
        se_peso = PESO_REG * np.sqrt(max((se[0] / a) ** 2 + (se[1] / b) ** 2 - 2 * cov[0, 1] / (a * b), 0))
        p(f"  peso del km cargado respecto del vacío: {PESO_REG:.2f} (± {1.96 * se_peso:.2f})",
          f"  en km de odómetro (dividiendo por la razón habitual {CENTRO_GLOBAL:.2f}): cargado {CENTRO_GLOBAL / a:.2f} km/L | vacío {CENTRO_GLOBAL / b:.2f} km/L")
    beta3, se3, _, r23, n3 = ajustar(rb.assign(kc=rb["km_c"] - rb["km_local"].clip(upper=rb["km_c"]), kl=rb["km_local"]), ["kc", "km_v", "kl"])
    p("", f"Variante separando los km de viajes locales (<= {KM_LOCAL} km):",
      "  L/100 km: cargado largo {:.1f} (± {:.1f}) | vacío {:.1f} (± {:.1f}) | local {:.1f} (± {:.1f})".format(
          *np.ravel(np.column_stack([100 * beta3, 100 * 1.96 * se3]))))

    # Por año y antes/después de la fecha de cambio
    por_anio = [fila_cv(an, d) for an, d in rb.groupby("anio") if len(d) >= 50]
    if por_anio:
        ta = pd.DataFrame(por_anio).set_index("grupo").rename_axis("anio")
        p("", "Por año (estabilidad de los coeficientes):", ta)
        PESO_ANIO = {int(k): float(v) for k, v in ta["peso"].items() if 1.0 <= v <= 2.0}
    if FECHA_CAMBIO:
        fc = pd.Timestamp(FECHA_CAMBIO)
        per = [fila_cv(n_, d) for n_, d in ((f"antes de {FECHA_CAMBIO}", rb[rb["t_fin"] < fc]), (f"desde {FECHA_CAMBIO}", rb[rb["t_fin"] >= fc])) if len(d) >= 50]
        if per:
            p("", "Antes y después de la fecha de cambio:", pd.DataFrame(per).set_index("grupo"))

    # Por tipo de carga: litros = suma de (coeficiente del tipo × km de ese tipo)
    kg = pz[pz["bloque_u"].notna()].pivot_table(index=["equipo", "bloque_u"], columns="grupo", values="km_p", aggfunc="sum", fill_value=0.0)
    kg.columns = ["g_" + c for c in kg.columns]
    rg = rb.merge(kg.reset_index(), on=["equipo", "bloque_u"], how="left").fillna(0.0)
    gcols = [c for c in kg.columns if rg[c].sum() >= 0.005 * rg["km_viajes"].sum()]
    def tabla_tipos(d):
        bt, s_, _, _, nc = ajustar(d, gcols)
        t = pd.DataFrame({"L/100 km": 100 * bt, "±": 100 * 1.96 * s_, "km_%": [100 * d[c].sum() / d[gcols].sum().sum() for c in gcols]},
                         index=[c[2:] for c in gcols])
        t["km/L"] = (100 / t["L/100 km"]).where(t["L/100 km"] > 0)
        return t.sort_values("km_%", ascending=False), nc
    if len(gcols) >= 2:
        t, nc = tabla_tipos(rg)
        p("", f"Consumo por tipo de carga, todo el período ({nc:,} bloques). Grupos armados por palabras del nombre del producto:", t)
        if FECHA_CAMBIO:
            for n_, d in ((f"antes de {FECHA_CAMBIO}", rg[rg["t_fin"] < fc]), (f"desde {FECHA_CAMBIO}", rg[rg["t_fin"] >= fc])):
                if len(d) >= 200:
                    t, nc = tabla_tipos(d)
                    p("", f"Consumo por tipo de carga, {n_} ({nc:,} bloques):", t)

    filas = [dict(fila_cv(cam, d), equipo=cam) for cam, d in rb.groupby("equipo") if len(d) >= 40]
    if filas:
        pc = pd.DataFrame(filas).drop(columns=["grupo"])
        p("", f"Por camión (con 40 bloques o más: {len(pc):,} camiones):",
          tabla_cuantiles({"L/100 km cargado": pc["L100_cargado"], "L/100 km vacío": pc["L100_vacio"], "peso cargado/vacío": pc["peso"]}))
        pc.merge(eq[["equipo", "codigo", "modelo"]].drop_duplicates("equipo"), on="equipo", how="left").to_csv(SALIDA / "regresion_por_camion.csv", index=False)
else:
    p(f"Solo hay {len(rb):,} bloques ok: no alcanza para una regresión confiable.")

# ====================================================================================================
titulo("P6", "Reparto de litros a los viajes")
if PESO_CARGADO is not None:
    PESO, PESO_ANIO = float(PESO_CARGADO), {}
    p(f"Peso del km cargado fijado en la configuración: {PESO:.2f}")
elif 1.0 <= PESO_REG <= 2.0:
    PESO = float(PESO_REG)
    if not PESO_POR_ANIO:
        PESO_ANIO = {}
    p(f"Peso del km cargado: {PESO:.2f} (regresión global)" + (f"; por año: { {k: round(v, 2) for k, v in PESO_ANIO.items()} }" if PESO_ANIO else ""))
else:
    PESO, PESO_ANIO = 1.0, {}
    p(f"La regresión no dio un peso utilizable ({PESO_REG:.2f}); se reparte solo por km (peso 1).")
_w = pz["inicio"].dt.year.map(PESO_ANIO).fillna(PESO)
pz["kmw"] = pz["km_p"] * np.where(pz["vacio_fis"], 1.0, _w)
pzb = pz.merge(bl[["equipo", "bloque_u", "litros", "calidad"]], on=["equipo", "bloque_u"], how="left")
pzb["kmw_bloque"] = pzb.groupby(["equipo", "bloque_u"])["kmw"].transform("sum")
pzb["litros_p"] = np.where(pzb["litros"].notna() & (pzb["kmw_bloque"] > 0), pzb["litros"] * pzb["kmw"] / pzb["kmw_bloque"], np.nan)
pzb["en_bloque"] = pzb["litros_p"].notna()
pzb["en_ok"] = pzb["en_bloque"] & (pzb["calidad"] == "ok")
pzb["parte_bl"], pzb["parte_ok"] = pzb["parte"].where(pzb["en_bloque"], 0.0), pzb["parte"].where(pzb["en_ok"], 0.0)
lv = pzb.groupby("id").agg(litros_bloque=("litros_p", "sum"), cubierto=("parte_bl", "sum"), cubierto_ok=("parte_ok", "sum"),
                           kmw=("kmw", "sum")).reset_index()
# Respaldo para lo que no quedó en un bloque: consumo habitual del camión por km ponderado (de sus bloques ok)
_okb = pzb[pzb["en_ok"]].groupby("equipo").agg(l=("litros_p", "sum"), k=("kmw", "sum"))
_tasa = (_okb["l"] / _okb["k"]).to_dict()
_tasa_global = _okb["l"].sum() / _okb["k"].sum() if _okb["k"].sum() > 0 else np.nan
res = vp[["id", "id_orig", "camion", "orden", "inicio", "tramo_nombre", "origen", "destino", "km", "tipo_mov", "vacio_fis", "grupo", "virtual", "rel"]].merge(lv, on="id", how="left")
res["tasa"] = res["camion"].map(_tasa).fillna(_tasa_global)
res["litros_estimado"] = np.where(res["cubierto"] >= 0.999, 0.0, (1 - res["cubierto"]).clip(0, 1) * res["kmw"] * res["tasa"])
res["litros"] = res["litros_bloque"] + res["litros_estimado"]
res["metodo"] = np.select([res["cubierto_ok"] >= 0.999, res["cubierto"] >= 0.999, res["cubierto"] <= 0.001],
                          ["bloque ok", "bloque dudoso", "estimado"], "mixto")
asig, en_bl = res["litros_bloque"].sum(), bl.loc[bl["km_viajes"] > 0, "litros"].sum()
p("", f"Cuadratura: litros repartidos desde bloques {asig:,.0f} | litros de los bloques con viajes {en_bl:,.0f} | diferencia {asig - en_bl:,.1f}",
  f"Litros de bloques sin viajes (quedan sin repartir): {bl.loc[bl['km_viajes'] <= 0, 'litros'].sum():,.0f}",
  "", "Método por viaje:")
t = res.groupby("metodo").agg(viajes=("id", "size"), km=("km", "sum"), litros=("litros", "sum"))
t["viajes_%"], t["km_%"] = (100 * t["viajes"] / t["viajes"].sum()).round(1), (100 * t["km"] / t["km"].sum()).round(1)
p(t, "", "% de km con reparto desde un bloque ok, por año:",
  (100 * res.assign(anio=res["inicio"].dt.year, ok=res["km"].where(res["metodo"] == "bloque ok", 0.0)).groupby("anio")[["ok", "km"]].sum()
   .pipe(lambda d: d["ok"] / d["km"])).round(1).to_frame("km ok %").T)
for nombre, col in (("tipo de movimiento", "tipo_mov"), ("carga física (True = vacío)", "vacio_fis"), ("tipo de carga", "grupo")):
    t = res[res["km"] > 0].groupby(col).agg(viajes=("id", "size"), km=("km", "sum"), litros=("litros", "sum"))
    t["L/100 km"], t["km/L"] = (100 * t["litros"] / t["km"]).round(1), (t["km"] / t["litros"]).round(2)
    p("", f"Consumo asignado por {nombre}:", t)
t = res[(res["km"] > KM_LOCAL) & (res["metodo"] == "bloque ok")].groupby("tramo_nombre").agg(
    viajes=("id", "size"), km=("km", "first"), vacio_pct=("vacio_fis", lambda s: 100 * s.mean()), litros_p50=("litros", "median"),
    litros_p10=("litros", lambda s: s.quantile(.1)), litros_p90=("litros", lambda s: s.quantile(.9))).sort_values("viajes", ascending=False).head(20)
t["km/L_p50"] = (t["km"] / t["litros_p50"]).round(2)
p("", "Litros por viaje en los 20 tramos más frecuentes (solo viajes en bloques ok):", t)
res.drop(columns=["kmw", "tasa"]).to_csv(SALIDA / "litros_por_viaje.csv", index=False)

# --- Vínculos para valorizar (2026-10-19): carga → bloque y viaje → bloque. No cambian el cálculo de litros. ---
# cargas_bloque.csv: cada carga de camión usada en el método, con su parada y su bloque final (bloque_u).
#   bloque_u vacío = la carga quedó fuera de un bloque (antes del primer o después del último llenado en base ubicado).
cb = (c0[["id", "equipo", "fecha", "litros", "proveedor", "producto", "parada"]]
      .merge(pa[["parada", "bloque", "corte", "es_base"]], on="parada", how="left")
      .merge(b0[["equipo", "bloque", "bloque_u"]], on=["equipo", "bloque"], how="left"))
cb["proveedor"] = cb["proveedor"].astype(str).str.strip().str.upper()
cb.to_csv(SALIDA / "cargas_bloque.csv", index=False)
# puente_viaje_bloque.csv: cada parte de un viaje dentro de un bloque, con su km ponderado y sus litros (PUENTE_VIAJE_BLOQUE)
pv = (pzb[pzb["en_bloque"]][["id", "equipo", "bloque_u", "parte", "km_p", "kmw", "kmw_bloque", "litros_p", "calidad"]]
      .merge(vp[["id", "id_orig", "virtual"]], on="id", how="left"))
pv.to_csv(SALIDA / "puente_viaje_bloque.csv", index=False)
_lb = cb.loc[cb["bloque_u"].notna(), "litros"].sum()
p("", f"Vínculos para valorizar: cargas_bloque.csv ({len(cb):,} cargas; litros en bloques {_lb:,.0f} = bloques.csv {bl['litros'].sum():,.0f}) "
      f"y puente_viaje_bloque.csv ({len(pv):,} partes de viaje; litros {pv['litros_p'].sum():,.0f} = repartidos {asig:,.0f})")
p("", f"Guardado: {SALIDA / 'litros_por_viaje.csv'} (una fila por viaje; id_orig = ciclos.fact_viaje_ciclo.id_viaje) y {SALIDA / 'bloques.csv'}")

# ====================================================================================================
partes = [x for x in sorted(SALIDA.glob("*.txt")) if x.name != "bloques_resultados.txt"]
(SALIDA / "bloques_resultados.txt").write_text("\n\n".join(x.read_text(encoding="utf-8") for x in partes), encoding="utf-8")
print(f"Listo: {SALIDA / 'bloques_resultados.txt'} ({len(partes)} bloques de salida).")
print("Otros archivos:", ", ".join(x.name for x in sorted(SALIDA.glob('*.csv'))))
