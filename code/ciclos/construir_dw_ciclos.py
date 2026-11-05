#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Área CICLOS del MVP Data Warehouse: el programa de ciclos de la práctica (Fact_Ventas_RT y Dim_Ciclos) llevado al DW,
partiendo de adempiere_sit_pbix_travelvta_v, más la clasificación Envío / Retorno / Vacío con las REGLAS DE RETORNO
(adempiere_sit_pbix_travelreglas_v) que en la práctica estaban solo en Power Query.

Pasos (mismos de la práctica, Documentación de Práctica: Ventas y Contabilidad §1.1 y §1.2.3):
  1. Tramos (paso1): ciudad de origen y destino del texto del tramo, limpias (sin paréntesis ni tildes) y su BASE
     según el maestro de ciudades (city_v.areas: BASE COQUIMBO · BASE SANTIAGO · NO APLICA).
     Tipo de movimiento base del tramo: Envío si el origen es una base; si no, Retorno.
  2. Viajes (paso2): travelvta + tramos. Una fila por c_orderline_id (travelvta repite el viaje por factura).
  3. Trayectorias (paso3, motor V3 SIN CAMBIOS, motor_practica.py): ordena los viajes de cada camión
     (Orden_Procesado), crea viajes VIRTUALES ("Creado") donde el destino de un viaje no calza con el origen del
     siguiente, y marca la calidad: N normal · NB conecta por base · IG inconsistencia geográfica · C virtual.
  4. Preparación de atributos por movimiento, sin construir ciclos de doble base.
  5. Reglas de retorno (Power Query de la práctica):
       ingreso 0 → Vacío · con código de roundtrip (c_project_id) → Envío ·
       BASE COQUIMBO → BASE SANTIAGO: Retorno si el cliente tiene una regla rompe_regla = Si (y, si carga_estricta = Si,
       solo con ese producto); si no, el tipo base del tramo · resto: tipo base del tramo.
       Si queda Retorno, el producto se renombra con nombre_carga (regla del cliente y producto; si no, la del cliente
       sin producto; si no, OTROS).

  6. CICLO ÚNICO con base Santiago: abre al salir de
     BASE SANTIAGO y cierra al llegar a BASE SANTIAGO (el tarificador cotiza la ida y la vuelta al origen). Corte: si el
     camión sale de BASE COQUIMBO con un Envío hacia un destino que no es Coquimbo ni Santiago, se cierra el ciclo en
     Coquimbo y se abre otro. Rol de cada viaje real en su ciclo: envío principal (el primer Envío; si no hay, el primer
     viaje con carga) · envío adicional en ruta · envío adicional local (Coquimbo ↔ La Serena) · retorno · vacío.
     Los virtuales quedan en el ciclo como vacíos. Las reglas de retorno (paso 5) no cambian.

Escribe solo en el esquema `ciclos` (y su fila en comun.registro_carga); al final rehace las vistas de comun.
  ciclos.dim_tramo_base · ciclos.regla_retorno · ciclos.fact_viaje_ciclo · ciclos.dim_ciclo_comercial

Fuentes (carpeta [fuentes] erp de config.ini): adempiere_sit_pbix_travelvta_v, _traveltramo_v, _city_v, _travelreglas_v.
Uso:  python ciclos\construir_dw_ciclos.py
"""
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))
from comun import db, vista_viaje  # noqa: E402
import motor_practica as mp        # noqa: E402

VERSION = "2026-10-30-ciclo-santiago-tipos-numericos-v2"
ESQUEMAS = ("ciclos", "comun")
RUTA_CRITICA = ("BASE COQUIMBO", "BASE SANTIAGO")      # donde aplican las excepciones de retorno

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS ciclos;
CREATE TABLE IF NOT EXISTS comun.registro_carga (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, area TEXT, fecha_carga TIMESTAMP, version_script TEXT,
  fecha_cambio TEXT, detalle TEXT);
DROP TABLE IF EXISTS ciclos.fact_viaje_ciclo CASCADE;   -- CASCADE: las vistas de comun se rehacen al final
DROP TABLE IF EXISTS ciclos.dim_ciclo; -- retirar definición de doble base
DROP TABLE IF EXISTS ciclos.dim_ciclo_comercial CASCADE;
DROP TABLE IF EXISTS ciclos.dim_tramo_base CASCADE;
DROP TABLE IF EXISTS ciclos.regla_retorno CASCADE;
CREATE TABLE ciclos.dim_tramo_base (
  sit_stretch_id BIGINT PRIMARY KEY, tramo TEXT, km DOUBLE PRECISION,
  ciudad_origen TEXT, ciudad_origen_limpia TEXT, ciudad_destino TEXT, ciudad_destino_limpia TEXT,
  base_origen TEXT, base_destino TEXT,     -- BASE COQUIMBO · BASE SANTIAGO · NO APLICA · NULL (ciudad sin match)
  tipo_movimiento_base TEXT);              -- Envio · Retorno · Sin Info
CREATE TABLE ciclos.regla_retorno (
  orden INTEGER, rompe_regla TEXT, cliente_id BIGINT, razon_social TEXT, carga_estricta TEXT, producto_id BIGINT,
  tipo_carga TEXT, nombre_carga TEXT);
CREATE TABLE ciclos.fact_viaje_ciclo (
  id_viaje TEXT PRIMARY KEY,               -- c_orderline_id, o V-<camión>-<n> si es virtual
  nro_viaje BIGINT,                        -- c_orderline_id (NULL en virtuales)
  equipo_id BIGINT,                        -- camión (sit_mainteam_id)
  orden_procesado INTEGER,                 -- posición en la cadena del camión
  sk_fecha INTEGER,                        -- datepromised (AAAAMMDD); el virtual toma la del viaje que lo sigue
  sit_stretch_id BIGINT,                   -- tramo (-999 = virtual sin tramo en la tabla)
  km DOUBLE PRECISION,
  ciudad_origen_limpia TEXT, ciudad_destino_limpia TEXT, base_origen TEXT, base_destino TEXT,
  cliente_id BIGINT, producto_id BIGINT,   -- NULL en virtuales
  ingreso DOUBLE PRECISION,                -- estimado (0 en virtuales)
  ingreso_por_km DOUBLE PRECISION,
  tipo_movimiento TEXT,                    -- práctica (ciclos): Vacio · Envio · Retorno, sin excepciones
  clasificacion_viaje TEXT,                -- con reglas de retorno: Vacio · Envio · Retorno · Sin Info
  regla_clasificacion TEXT,                -- vacío · roundtrip · excepción cliente · excepción cliente y producto · tramo
  tipo_carga_final TEXT,                   -- producto, o nombre_carga si quedó Retorno
  calidad_trayectoria TEXT,                -- N normal · NB por base · IG inconsistencia geográfica · C creado (virtual)
  es_virtual TEXT, tipo_virtual TEXT,      -- No_Virtual · Virtual_Con_Tramo · Virtual_Sin_Tramo
  ciclo_comercial TEXT,                    -- ciclo con base Santiago (ciclos.dim_ciclo_comercial)
  orden_ciclo_comercial INTEGER,
  rol_comercial TEXT);                     -- envío principal · envío adicional en ruta · envío adicional local · retorno · vacío · sin carga
CREATE INDEX ix_vc_nro ON ciclos.fact_viaje_ciclo (nro_viaje);
CREATE INDEX ix_vc_cc ON ciclos.fact_viaje_ciclo (ciclo_comercial);
CREATE TABLE ciclos.dim_ciclo_comercial (
  ciclo_comercial TEXT PRIMARY KEY,        -- CC-<camión>-<n>
  equipo_id BIGINT, fecha_inicio DATE, fecha_fin DATE, dias INTEGER,
  motivo_apertura TEXT,                    -- sale de Santiago · corte en Coquimbo · local en Santiago · inicio de cadena · sin pasar por Santiago
  motivo_cierre TEXT,                      -- llega a Santiago · corte en Coquimbo · local · sin cerrar
  estado TEXT,                             -- completo · abierto · incompleto
  viajes INTEGER, viajes_virtuales INTEGER, envios INTEGER, retornos INTEGER, vacios INTEGER,
  km DOUBLE PRECISION, km_cargado DOUBLE PRECISION, ingreso DOUBLE PRECISION,
  patron TEXT,                             -- E envío · R retorno · V vacío (virtuales incluidos), en orden
  recorrido TEXT,
  viaje_principal TEXT);                   -- id_viaje del envío principal (NULL si el ciclo no tiene carga)
"""


def leer(nombre, columnas=None):
    carpeta = db.ruta_config("fuentes", "erp")
    for n in (f"adempiere_{nombre}.parquet", f"{nombre}.parquet"):
        if (carpeta / n).exists():
            df = pd.read_parquet(carpeta / n, columns=columnas)
            print(f"   {n}: {len(df):,} filas")
            return df
    raise SystemExit(f"Falta adempiere_{nombre}.parquet en {carpeta}. Copiarlo desde la extracción del ERP.")


# ------------------------------------------------------------------------------------------------ paso 1
def numerica(serie,campo):
    """Convertir NUMERIC textual al entrar al motor, sin ocultar datos inválidos."""
    try:
        return pd.to_numeric(serie,errors='raise').astype('float64')
    except (ValueError,TypeError) as error:
        raise ValueError(f'{campo} contiene un valor no numérico; revisar fuente') from error


def clasificar_tramos(tr, ciudades):
    """paso1 de la práctica: ciudades limpias, base de origen y destino, tipo de movimiento base."""
    tr = tr.copy()
    tr['kilometers']=numerica(tr['kilometers'],'traveltramo.kilometers')
    tr["Ciudad_Origen"] = tr["tramo"].str.split("/").str[0].str.strip()
    tr["Ciudad_Destino"] = tr["tramo"].str.split("/").str[-1].str.strip()
    sin_barra = tr[~tr["tramo"].str.contains("/", na=False)]
    if len(sin_barra):
        print(f"   aviso: {len(sin_barra)} tramos sin '/'")
    limpia = lambda x: re.sub(r"\([^)]*\)", "", x).strip() if isinstance(x, str) else x   # noqa: E731
    tr["Ciudad_Origen_Limpia"] = tr["Ciudad_Origen"].apply(limpia).apply(mp.normalizar_texto)
    tr["Ciudad_Destino_Limpia"] = tr["Ciudad_Destino"].apply(limpia).apply(mp.normalizar_texto)
    ci = ciudades.copy()
    ci["namecity_norm"] = ci["namecity"].apply(mp.normalizar_texto)
    ci["Base_Normalizada"] = ci["areas"].apply(mp.normalizar_texto)
    ci = ci[["namecity_norm", "Base_Normalizada"]].drop_duplicates(subset=["namecity_norm"])
    tr = tr.merge(ci, left_on="Ciudad_Origen_Limpia", right_on="namecity_norm", how="left") \
        .drop(columns=["namecity_norm"]).rename(columns={"Base_Normalizada": "Bas_O"})
    tr = tr.merge(ci, left_on="Ciudad_Destino_Limpia", right_on="namecity_norm", how="left") \
        .drop(columns=["namecity_norm"]).rename(columns={"Base_Normalizada": "Bas_D"})
    print(f"   tramos: {len(tr)}; sin base en origen {int(tr['Bas_O'].isna().sum())}, "
          f"en destino {int(tr['Bas_D'].isna().sum())}")
    tr["Tipo_Movimiento"] = tr["Bas_O"].apply(
        lambda b: "Sin Info" if pd.isna(b) else ("Envio" if b != "NO APLICA" else "Retorno"))
    return tr


# ------------------------------------------------------------------------------------------------ paso 2
def enriquecer_viajes(vta, tr):
    """paso2 de la práctica (+ una fila por viaje)."""
    v = vta.copy()
    n0 = len(v)
    v = v.drop_duplicates("c_orderline_id")                 # la práctica no deduplicaba (travelvta repite por factura)
    if n0 != len(v):
        print(f"   viajes: {n0:,} filas → {len(v):,} (una por c_orderline_id)")
    v["sit_stretch_id"] = a_int(v["sit_stretch_id"]).astype('string')
    t = tr.copy()
    t["sit_stretch_id"] = a_int(t["sit_stretch_id"]).astype('string')
    cols = ["sit_stretch_id", "tramo", "kilometers", "Ciudad_Origen_Limpia", "Ciudad_Destino_Limpia",
            "Tipo_Movimiento", "Bas_O", "Bas_D"]
    v = v.merge(t[cols], on="sit_stretch_id", how="left")
    v['kilometers']=numerica(v['kilometers'],'traveltramo.kilometers')
    v["datepromised"] = pd.to_datetime(v["datepromised"], errors="coerce")
    v["c_orderline_id"] = v["c_orderline_id"].astype(str)
    v["sit_mainteam_id"] = pd.to_numeric(v["sit_mainteam_id"], errors="coerce").astype("Int64")
    v["estimado"] = pd.to_numeric(v["estimado"], errors="coerce").fillna(0.0)
    return v


# ------------------------------------------------------------------------------------------------ paso 3
def trayectorias(v, tr):
    """paso3 de la práctica: motor V3 por camión (sin cambios)."""
    df = v.copy()
    df['kilometers']=numerica(df['kilometers'],'viajes.kilometers')
    tr=tr.copy()
    tr['kilometers']=numerica(tr['kilometers'],'traveltramo.kilometers')
    df["sit_stretch_id"] = df["sit_stretch_id"].apply(mp._normalizar_stretch_id)
    tramos_lookup = {}
    for row in tr.to_dict(orient="records"):
        key = (row["Ciudad_Origen_Limpia"], row["Ciudad_Destino_Limpia"])
        km = row["kilometers"] if pd.notna(row["kilometers"]) else 0.0
        if key not in tramos_lookup or (km > 0 and tramos_lookup[key]["km"] <= 0):
            tramos_lookup[key] = {"stretch_id": str(int(row["sit_stretch_id"])) if row["sit_stretch_id"] else "-999",
                                  "km": km}
    camiones = [c for c in df["sit_mainteam_id"].dropna().unique().tolist() if c is not None]
    sin_camion = int(df["sit_mainteam_id"].isna().sum())
    t0, lista = time.time(), []
    for i, camion in enumerate(camiones, 1):
        grupo = df[df["sit_mainteam_id"] == camion].to_dict(orient="records")
        res = mp.resolver_camion_v3(grupo, tramos_lookup)
        for r in res:                                       # id único del virtual: la práctica repetía V_1001… por camión
            if r.get("Calidad_Trayectoria") == "C":
                r["id_viaje"] = f"V-{int(camion)}-{str(r['c_orderline_id']).split('_')[-1]}"
            else:
                r["id_viaje"] = str(int(float(r["c_orderline_id"])))
        lista.extend(res)
        if i % 50 == 0:
            print(f"   trayectorias: {i}/{len(camiones)} camiones ({time.time() - t0:.0f} s)")
    out = pd.DataFrame(lista)
    out["sit_stretch_id"] = out["sit_stretch_id"].apply(mp._normalizar_stretch_id)
    print(f"   trayectorias: {len(camiones)} camiones, {len(out):,} filas "
          f"({int((out['Calidad_Trayectoria'] == 'C').sum()):,} virtuales); viajes sin camión: {sin_camion}")
    return out


# ------------------------------------------------------------------------------------------------ paso 4
def preparar_movimientos(fact):
    """Atributos de movimientos; el único ciclo se asigna después con base Santiago."""
    fact = fact.sort_values(["sit_mainteam_id", "Orden_Procesado"]).reset_index(drop=True)
    fact["datepromised"] = pd.to_datetime(fact["datepromised"])
    fact["enddatetravel"] = pd.to_datetime(fact.get("enddatetravel"), errors="coerce")
    fact["estimado_fill"] = fact["estimado"].fillna(0)
    fact["Tipo_Movimiento"] = np.select(
        [fact["estimado_fill"] == 0, fact["Bas_O"].notna() & (fact["Bas_O"] != "NO APLICA")], ["Vacio", "Envio"],
        default="Retorno")
    fact["Es_Virtual"] = fact["Calidad_Trayectoria"] == "C"
    sid = fact["sit_stretch_id"].fillna(-999).astype(float).astype(int).astype(str)
    fact["Tipo_Virtual"] = np.where(fact["Calidad_Trayectoria"] != "C", "No_Virtual",
                                    np.where(sid != "-999", "Virtual_Con_Tramo", "Virtual_Sin_Tramo"))
    fact["Ingreso_Por_KM_Viaje"] = np.where(fact["kilometers"].isna() | (fact["kilometers"] <= 0), np.nan,
                                            fact["estimado_fill"] / fact["kilometers"])
    return fact



def aplicar_reglas(fact, reglas, productos):
    """Clasificación con reglas de retorno (Power Query de la práctica) y nombre de carga final."""
    fact=fact.copy()
    fact['c_project_id']=numerica(fact['c_project_id'],'travelvta.c_project_id')
    r = reglas.copy()
    r["cliente"] = pd.to_numeric(r["c_bpartner_id"], errors="coerce")
    r["producto"] = pd.to_numeric(r["m_product_id"], errors="coerce")
    r["rompe"] = r["rompe_regla"].astype(str).str.strip().str.upper().isin(["SI", "SÍ", "S", "Y"])
    r["estricta"] = r["carga_estricta"].astype(str).str.strip().str.upper().isin(["SI", "SÍ", "S", "Y"])
    por_cliente = {c: g for c, g in r.groupby("cliente")}
    nombre_prod = dict(zip(pd.to_numeric(productos["m_product_id"], errors="coerce"), productos["producto"].astype(str)))

    clas, regla, carga = [], [], []
    for row in fact[["estimado", "c_project_id", "Bas_O", "Bas_D", "c_bpartner_id", "m_product_id",
                     "Calidad_Trayectoria"]].itertuples(index=False):
        est = row.estimado if pd.notna(row.estimado) else 0
        cli = pd.to_numeric(row.c_bpartner_id, errors="coerce")
        prod = pd.to_numeric(row.m_product_id, errors="coerce")
        base = "Sin Info" if pd.isna(row.Bas_O) else ("Envio" if row.Bas_O != "NO APLICA" else "Retorno")
        rc = por_cliente.get(cli)
        if est == 0:
            c, rg = "Vacio", "vacío"
        elif pd.notna(row.c_project_id) and row.c_project_id != 0:
            c, rg = "Envio", "roundtrip"
        elif (row.Bas_O, row.Bas_D) == RUTA_CRITICA and rc is not None and rc["rompe"].any():
            primera = rc[rc["rompe"]].iloc[0]                   # Power Query toma la primera regla que rompe
            if primera["estricta"]:
                es_ret = pd.notna(primera["producto"]) and primera["producto"] == prod
                c, rg = ("Retorno", "excepción cliente y producto") if es_ret else (base, "tramo")
            else:
                c, rg = "Retorno", "excepción cliente"
        else:
            c, rg = base, "tramo"
        if c == "Retorno":
            nombre = "OTROS"
            if rc is not None:
                esp = rc[rc["producto"] == prod]
                gen = rc[rc["producto"].isna()]
                if len(esp):
                    nombre = esp.iloc[0]["nombre_carga"]
                elif len(gen):
                    nombre = gen.iloc[0]["nombre_carga"]
        else:
            nombre = nombre_prod.get(prod) if row.Calidad_Trayectoria != "C" else "VIAJE VIRTUAL"
        clas.append(c)
        regla.append(rg)
        carga.append(nombre)
    fact["Clasificacion_Viaje"], fact["Regla_Clasificacion"], fact["Tipo_Carga_Final"] = clas, regla, carga
    return fact


# ------------------------------------------------------------------------------------------------ ciclo comercial
COQ, STGO = "BASE COQUIMBO", "BASE SANTIAGO"


def ciclo_comercial(salida):
    """Ciclo con base Santiago + corte en Coquimbo; rol de cada viaje. Devuelve (salida con columnas, dim)."""
    x = salida.sort_values(["equipo_id", "orden_procesado"]).copy()
    ids, ords, aper, n_por_eq = [], [], {}, {}
    cierre = {}
    for eq, g in x.groupby("equipo_id", sort=False):
        abierto, cid, k, n = False, None, 0, 0
        for bo, bd, cl in zip(g["base_origen"], g["base_destino"], g["clasificacion_viaje"]):
            corte = abierto and k > 0 and bo == COQ and cl == "Envio" and bd not in (COQ, STGO)
            if corte:
                cierre[cid], abierto = "corte en Coquimbo", False
            if not abierto:
                n += 1
                cid, k = f"CC-{eq}-{n}", 0
                if bo == STGO and bd == STGO:
                    aper[cid], cierre[cid] = "local en Santiago", "local"
                    ids.append(cid); ords.append(1)
                    continue
                aper[cid] = ("sale de Santiago" if bo == STGO else "corte en Coquimbo" if corte
                             else "inicio de cadena" if n == 1 else "sin pasar por Santiago")
                abierto = True
            k += 1
            ids.append(cid); ords.append(k)
            if bd == STGO:
                cierre[cid], abierto = "llega a Santiago", False
        if abierto:
            cierre[cid] = "sin cerrar"
    x["ciclo_comercial"], x["orden_ciclo_comercial"] = ids, ords

    # rol de cada viaje
    cl = x["clasificacion_viaje"].fillna("")
    cargado = cl.isin(["Envio", "Retorno"]) & (x["es_virtual"] == "N")
    es_env = (cl == "Envio") & (x["es_virtual"] == "N")
    prim_env = x[es_env].groupby("ciclo_comercial")["id_viaje"].first()
    prim_carga = x[cargado].groupby("ciclo_comercial")["id_viaje"].first()
    principal = prim_env.reindex(x["ciclo_comercial"].unique()).fillna(prim_carga)
    x["es_principal"] = x["id_viaje"].values == x["ciclo_comercial"].map(principal).values
    local = (x["base_origen"] == COQ) & (x["base_destino"] == COQ)
    x["rol_comercial"] = np.select(
        [x["es_principal"], es_env & local, es_env, (cl == "Retorno") & (x["es_virtual"] == "N")],
        ["envío principal", "envío adicional local", "envío adicional en ruta", "retorno"], "vacío")
    sin_carga = ~x["ciclo_comercial"].isin(principal.dropna().index)
    x.loc[sin_carga, "rol_comercial"] = "sin carga"

    # dimensión
    x["L"] = np.select([cl == "Envio", cl == "Retorno"], ["E", "R"], "V")
    x["fecha"] = pd.to_datetime(x["sk_fecha"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    x["km_c"] = x["km"].where(cargado, 0.0)
    g = x.groupby("ciclo_comercial", sort=False)
    dim = pd.DataFrame({
        "equipo_id": g["equipo_id"].first(), "fecha_inicio": g["fecha"].min(), "fecha_fin": g["fecha"].max(),
        "viajes": g.size(), "viajes_virtuales": g["es_virtual"].apply(lambda s: int((s == "S").sum())),
        "envios": g["L"].apply(lambda s: int((s == "E").sum())), "retornos": g["L"].apply(lambda s: int((s == "R").sum())),
        "vacios": g["L"].apply(lambda s: int((s == "V").sum())),
        "km": g["km"].sum(), "km_cargado": g["km_c"].sum(), "ingreso": g["ingreso"].sum(),
        "patron": g["L"].agg("".join),
        "recorrido": g["ciudad_origen_limpia"].first().astype(str) + " / "
                     + g["ciudad_destino_limpia"].agg(lambda s: " / ".join(str(c) for c in s))})
    dim.index.name = "ciclo_comercial"
    dim = dim.reset_index()
    dim["dias"] = (dim["fecha_fin"] - dim["fecha_inicio"]).dt.days.astype("Int64")
    dim["motivo_apertura"] = dim["ciclo_comercial"].map(aper)
    dim["motivo_cierre"] = dim["ciclo_comercial"].map(cierre).fillna("sin cerrar")
    dim["estado"] = np.select(
        [dim["motivo_apertura"].isin(["inicio de cadena", "sin pasar por Santiago"]), dim["motivo_cierre"] == "sin cerrar"],
        ["incompleto", "abierto"], "completo")
    dim["viaje_principal"] = dim["ciclo_comercial"].map(principal)
    for c in ("fecha_inicio", "fecha_fin"):
        dim[c] = dim[c].dt.strftime("%Y-%m-%d")
    dim = dim[["ciclo_comercial", "equipo_id", "fecha_inicio", "fecha_fin", "dias", "motivo_apertura", "motivo_cierre",
               "estado", "viajes", "viajes_virtuales", "envios", "retornos", "vacios", "km", "km_cargado", "ingreso",
               "patron", "recorrido", "viaje_principal"]]
    out = salida.merge(x[["id_viaje", "ciclo_comercial", "orden_ciclo_comercial", "rol_comercial"]], on="id_viaje", how="left")
    return out, dim


# ------------------------------------------------------------------------------------------------ carga
def a_int(s):
    return pd.to_numeric(s, errors="coerce").round().astype("Int64")


def transformar(vta,tr,ciudades,reglas,productos):
    """Transformación completa de ciclos, reutilizable sin PostgreSQL."""
    if "c_project_id" not in vta.columns:
        print("   aviso: travelvta no trae c_project_id: no se reconocen roundtrips")
        vta["c_project_id"] = np.nan

    tr = clasificar_tramos(tr, ciudades)
    v = enriquecer_viajes(vta, tr)
    fact = trayectorias(v, tr)
    fact = preparar_movimientos(fact)
    fact = aplicar_reglas(fact, reglas, productos)

    real = fact["Calidad_Trayectoria"] != "C"
    salida = pd.DataFrame({
        "id_viaje": fact["id_viaje"], "nro_viaje": a_int(fact["c_orderline_id"].where(real)),
        "equipo_id": a_int(fact["sit_mainteam_id"]), "orden_procesado": a_int(fact["Orden_Procesado"]),
        "sk_fecha": a_int(fact["datepromised"].dt.strftime("%Y%m%d")), "sit_stretch_id": a_int(fact["sit_stretch_id"]),
        "km": pd.to_numeric(fact["kilometers"], errors="coerce"),
        "ciudad_origen_limpia": fact["Ciudad_Origen_Limpia"], "ciudad_destino_limpia": fact["Ciudad_Destino_Limpia"],
        "base_origen": fact["Bas_O"], "base_destino": fact["Bas_D"],
        "cliente_id": a_int(fact["c_bpartner_id"].where(real)), "producto_id": a_int(fact["m_product_id"].where(real)),
        "ingreso": fact["estimado_fill"], "ingreso_por_km": fact["Ingreso_Por_KM_Viaje"],
        "tipo_movimiento": fact["Tipo_Movimiento"], "clasificacion_viaje": fact["Clasificacion_Viaje"],
        "regla_clasificacion": fact["Regla_Clasificacion"], "tipo_carga_final": fact["Tipo_Carga_Final"],
        "calidad_trayectoria": fact["Calidad_Trayectoria"], "es_virtual": np.where(real, "N", "S"),
        "tipo_virtual": fact["Tipo_Virtual"]})
    dup = salida["id_viaje"].duplicated()
    if dup.any():
        print(f"   aviso: {int(dup.sum())} id_viaje repetidos; se deja el primero")
        salida = salida[~dup]
    salida, dim_cc = ciclo_comercial(salida)
    salida["orden_ciclo_comercial"] = a_int(salida["orden_ciclo_comercial"])
    dim_tramo = pd.DataFrame({
        "sit_stretch_id": a_int(tr["sit_stretch_id"]), "tramo": tr["tramo"], "km": tr["kilometers"],
        "ciudad_origen": tr["Ciudad_Origen"], "ciudad_origen_limpia": tr["Ciudad_Origen_Limpia"],
        "ciudad_destino": tr["Ciudad_Destino"], "ciudad_destino_limpia": tr["Ciudad_Destino_Limpia"],
        "base_origen": tr["Bas_O"], "base_destino": tr["Bas_D"], "tipo_movimiento_base": tr["Tipo_Movimiento"]}
    ).drop_duplicates("sit_stretch_id")
    reg = pd.DataFrame({
        "orden": range(1, len(reglas) + 1), "rompe_regla": reglas["rompe_regla"],
        "cliente_id": a_int(reglas["c_bpartner_id"]), "razon_social": reglas.get("razon_social"),
        "carga_estricta": reglas["carga_estricta"], "producto_id": a_int(reglas["m_product_id"]),
        "tipo_carga": reglas.get("tipo_carga"), "nombre_carga": reglas["nombre_carga"]})

    return salida,dim_cc,dim_tramo,reg


def principal(argv=None, _conn=None):
    print(f"construir_dw_ciclos.py versión {VERSION}")
    t0 = time.time()
    vta = leer("sit_pbix_travelvta_v")
    vta.columns = [c.lower() for c in vta.columns]
    tr = leer("sit_pbix_traveltramo_v")
    ciudades = leer("sit_pbix_city_v", ["namecity", "areas"])
    reglas = leer("sit_pbix_travelreglas_v")
    productos = leer("sit_pbix_traveltcarga_v", ["m_product_id", "producto"])
    salida,dim_cc,dim_tramo,reg = transformar(vta,tr,ciudades,reglas,productos)

    con = db.conectar(ESQUEMAS, _conn=_conn)
    with con:
        con.execute(ESQUEMA)
        con.copiar_df("ciclos.dim_tramo_base", dim_tramo)
        con.copiar_df("ciclos.regla_retorno", reg)
        con.copiar_df("ciclos.fact_viaje_ciclo", salida)
        con.copiar_df("ciclos.dim_ciclo_comercial", dim_cc)
        cl = salida[salida["es_virtual"] == "N"]
        cambios = cl[cl["tipo_movimiento"] != cl["clasificacion_viaje"]]
        detalle = (f"viajes {int((salida['es_virtual'] == 'N').sum())}; virtuales {int((salida['es_virtual'] == 'S').sum())}; "
                   f"ciclos Santiago/cortes Coquimbo {len(dim_cc)}; "
                   f"clasificación {cl['clasificacion_viaje'].value_counts().to_dict()}; "
                   f"cambios por reglas {len(cambios)}")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("ciclos", db.ahora(), VERSION, "", detalle))
        vista = vista_viaje.crear(con)
    for t in ("ciclos.dim_tramo_base", "ciclos.regla_retorno", "ciclos.fact_viaje_ciclo",
              "ciclos.dim_ciclo_comercial"):
        print(f"   {t:30s} {con.uno(f'SELECT COUNT(*) FROM {t}')[0]:8d}")
    print("   calidad de trayectoria: " + str(salida["calidad_trayectoria"].value_counts().to_dict()))
    print("   clasificación (viajes reales): práctica " + str(cl["tipo_movimiento"].value_counts().to_dict())
          + " → con reglas " + str(cl["clasificacion_viaje"].value_counts().to_dict()))
    print("   reglas que cambiaron la clasificación: "
          + str(cambios.groupby(["tipo_movimiento", "clasificacion_viaje", "regla_clasificacion"]).size().to_dict()))
    print("   ciclo comercial (base Santiago): " + str(dim_cc["estado"].value_counts().to_dict())
          + " · apertura " + str(dim_cc["motivo_apertura"].value_counts().to_dict()))
    re_ = salida[salida["es_virtual"] == "N"]["rol_comercial"].value_counts().to_dict()
    print("   rol de los viajes reales: " + str(re_))
    cc = dim_cc[(dim_cc["estado"] == "completo") & (dim_cc["fecha_inicio"] >= "2024-01-01")]
    print("   ciclos completos desde 2024: {:,} · viajes (mediana) {:.0f} · días (mediana) {:.0f} · 2+ envíos {:,} · sin carga {:,}".format(
        len(cc), cc["viajes"].median(), cc["dias"].median(), int((cc["envios"] >= 2).sum()),
        int((cc["envios"] + cc["retornos"] == 0).sum())))
    print(f"   vistas de comun: {'actualizadas' if vista else 'no (falta peajes)'}  ·  {time.time() - t0:.0f} s")
    con.close()


if __name__ == "__main__":
    principal(sys.argv[1:])
