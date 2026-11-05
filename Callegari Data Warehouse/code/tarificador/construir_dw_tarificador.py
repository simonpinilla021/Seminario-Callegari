#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
LEGADO compatible con modelos operacionales 3/4. El tarificador exacto 1/2 usa
tarificador.exacto y sus tablas propias; no lee las tasas ni la mezcla anual de este módulo.

Área TARIFICADOR del MVP: las reglas del tarificador del gerente como modelo dimensional (sin leer el Excel).

Los valores tienen dos orígenes:
  - manual: los ingresa la empresa en los CSV de esta carpeta (se iniciaron con el tarificador al 15.10.2026).
  - BBDD:   los calcula el DW: precio del combustible (cartas del día + % base/TCT del año anterior), costo fijo diario
            (área costos), y los derivados de los manuales (km por día, reposición diaria, neumáticos por km).

Tablas (esquema tarificador):
  dim_parametro                      catálogo de variables (dim_parametro.csv): nombre, unidad, categoría, origen,
                                     descripción y regla de cálculo
  fact_parametro_valor               valor de cada parámetro × ámbito con vigencia; manuales (parametros_valor_manual.csv)
                                     y calculados por la BBDD. Si en una fecha rigen los dos, manda el de la BBDD.
  fact_peaje_tarificador             peaje solo ida origen × destino con vigencia (peajes_tarificador_manual.csv);
                                     cada vigencia es una matriz completa
  fact_precio_combustible_tarificador un día: costo real base (granel 33.000 L) y TCT (Antofagasta), % base del año,
                                     recargo y precio del tarificador = pct × base + (1 − pct) × TCT + recargo

Necesita cargados: precios, combustible (cargas) y costos. Lo llama construir_todo.py; también solo:
  python tarificador\construir_dw_tarificador.py
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from comun import db  # noqa: E402
from comun.parametros_tarificador import Parametros, SQL_RESOLVER, SQL_VISTAS  # noqa: E402

VERSION = "2026-10-26-pg-parametros-efectivos"
DIM_CSV = AQUI / "dim_parametro.csv"
VALOR_CSV = AQUI / "parametros_valor_manual.csv"
PEAJE_CSV = AQUI / "peajes_tarificador_manual.csv"
FIN = "9999-12-31"
MESES_MINIMOS = 10          # un año con cargas en menos meses no sirve para el % base del año siguiente

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS tarificador;
DROP TABLE IF EXISTS tarificador.fact_parametro_valor, tarificador.dim_parametro, tarificador.fact_peaje_tarificador,
  tarificador.fact_precio_combustible_tarificador CASCADE;
CREATE TABLE tarificador.dim_parametro (
  sk_parametro INTEGER PRIMARY KEY,
  codigo TEXT UNIQUE, nombre TEXT, categoria TEXT, unidad TEXT,
  origen_valor TEXT,                 -- manual · BBDD
  descripcion TEXT,                  -- qué hace la variable en el cálculo (documentación)
  regla_calculo TEXT,                -- solo BBDD: cómo se calcula
  referencia_tarificador TEXT);      -- celda equivalente en el Excel del gerente (trazabilidad)
CREATE TABLE tarificador.fact_parametro_valor (
  sk_parametro INTEGER REFERENCES tarificador.dim_parametro,
  ambito TEXT,                       -- todos · tractoreo · frio · congelado · multi
  vigente_desde DATE, vigente_hasta DATE,   -- hasta exclusiva; 9999-12-31 = sigue vigente
  valor DOUBLE PRECISION,
  origen TEXT,                       -- manual · BBDD
  monto_base DOUBLE PRECISION, cantidad_inductor DOUBLE PRECISION,   -- solo calculados (auditoría)
  nota TEXT,
  fecha_registro TIMESTAMP,
  PRIMARY KEY (sk_parametro, ambito, vigente_desde, origen));
CREATE TABLE tarificador.fact_peaje_tarificador (
  origen TEXT, destino TEXT,         -- como los escribe el tarificador
  origen_norm TEXT, destino_norm TEXT,   -- sin tildes ni espacios, para cruzar con los tramos
  vigente_desde DATE, vigente_hasta DATE,
  peaje DOUBLE PRECISION,            -- $ solo ida
  nota TEXT,
  PRIMARY KEY (origen_norm, destino_norm, vigente_desde));
CREATE TABLE tarificador.fact_precio_combustible_tarificador (
  fecha DATE PRIMARY KEY, sk_fecha INTEGER,
  costo_base DOUBLE PRECISION,       -- costo real granel del día (ATP)
  costo_tct DOUBLE PRECISION,        -- costo real TCT del día (COP)
  pct_base DOUBLE PRECISION, origen_pct TEXT,
  recargo DOUBLE PRECISION,
  precio_tarificador DOUBLE PRECISION);
"""


def norm(x):
    import re
    import unicodedata
    if x is None or (isinstance(x, float) and x != x):
        return ""
    x = re.sub(r"\(.*?\)", " ", str(x))
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", x.upper())


def leer_csv(ruta):
    if not ruta.exists():
        raise SystemExit(f"Falta {ruta}")
    return pd.read_csv(ruta, encoding="utf-8-sig", dtype=str).fillna("")


def calcular(con, dim, man):
    """Filas calculadas por la BBDD."""
    ahora = db.ahora()
    filas = []

    def fila(codigo, ambito, desde, hasta, valor, nota, base=None, cant=None):
        filas.append(dict(codigo=codigo, ambito=ambito, vigente_desde=str(desde), vigente_hasta=str(hasta),
                          valor=valor, origen="BBDD", monto_base=base, cantidad_inductor=cant, nota=nota,
                          fecha_registro=ahora))

    p = Parametros(man)
    # derivados de los manuales: en cada fecha en que cambia algún componente
    def cortes(*codigos):
        d = man[man["codigo"].isin(codigos)]
        return sorted((set(d["vigente_desde"]) | set(d["vigente_hasta"]) | {"1900-01-01"}) - {FIN})

    for amb in ("todos", "tractoreo"):
        cs = cortes("valor_camion", "remanente", "valor_termo", "meses_reposicion", "dias_mes")
        for i, d in enumerate(cs):
            h = cs[i + 1] if i + 1 < len(cs) else FIN
            vc, rm, vt = (p.uno(c, d, amb) for c in ("valor_camion", "remanente", "valor_termo"))
            me, dm = p.uno("meses_reposicion", d), p.uno("dias_mes", d)
            if pd.notna([vc, rm, vt, me, dm]).all():
                if me <= 0 or dm <= 0:
                    raise ValueError("Reposición: meses y días deben ser positivos")
                fila("reposicion_diaria", amb, d, h, round((vc - rm + vt) / me / dm, 2),
                     f"({vc:,.0f} − {rm:,.0f} + {vt:,.0f}) ÷ {me:.0f} meses ÷ {dm:.0f} días", vc - rm + vt, me * dm)
        cs = cortes("n_neumaticos", "precio_neumatico", "vida_util_km")
        for i, d in enumerate(cs):
            h = cs[i + 1] if i + 1 < len(cs) else FIN
            n, pr, vu = p.uno("n_neumaticos", d, amb), p.uno("precio_neumatico", d), p.uno("vida_util_km", d)
            if pd.notna([n, pr, vu]).all():
                if vu <= 0:
                    raise ValueError("Vida útil de neumáticos debe ser positiva")
                fila("costo_neumatico_km", amb, d, h, round(n * pr / vu, 4), f"{n:.0f} × {pr:,.0f} ÷ {vu:,.0f} km", n * pr, vu)
    cs = cortes("km_hora", "horas_dia")
    for i, d in enumerate(cs):
        h = cs[i + 1] if i + 1 < len(cs) else FIN
        kh, hd = p.uno("km_hora", d), p.uno("horas_dia", d)
        if pd.notna([kh, hd]).all():
            fila("km_dia", "todos", d, h, kh * hd, f"{kh:.0f} km/h × {hd:.0f} h")

    # costo fijo diario: el del área costos (método tarificador)
    if con.existe_tabla("costos", "parametro_costo"):
        cf = con.df("""SELECT vigente_desde::text AS d, vigente_hasta::text AS h, valor::float8 AS v,
                              monto_base::float8 AS b, cantidad_inductor::float8 AS c, nota
                       FROM costos.parametro_costo
                       WHERE parametro = 'costo_fijo_diario' AND metodo = 'tarificador' AND ambito = 'todos'""")
        for r in cf.itertuples():
            fila("costo_fijo_diario", "todos", r.d, r.h, r.v, f"costos.parametro_costo: {r.nota or ''}".strip(), r.b, r.c)
    else:
        print("   aviso: falta costos.parametro_costo (cargar costos): sin costo fijo diario")

    # % base del año = litros ATP ÷ (ATP + COP) del año anterior (cargas de camión)
    if con.existe_tabla("combustible", "fact_carga_combustible"):
        lt = con.df("""SELECT EXTRACT(YEAR FROM fecha)::int AS anio, oilsupplier, SUM(litros)::float8 AS litros,
                              COUNT(DISTINCT EXTRACT(MONTH FROM fecha))::int AS meses
                       FROM combustible.fact_carga_combustible WHERE oilsupplier IN ('ATP', 'COP')
                       GROUP BY 1, 2""")
        pv = lt.pivot_table(index="anio", columns="oilsupplier", values="litros", aggfunc="sum").fillna(0)
        meses = lt.groupby("anio")["meses"].max()
        for anio, r in pv.iterrows():
            atp, cop = r.get("ATP", 0), r.get("COP", 0)
            if meses.get(anio, 0) < MESES_MINIMOS:
                print(f"   % base: el año {int(anio)} tiene cargas en {meses.get(anio, 0)} meses (< {MESES_MINIMOS}); "
                      f"{int(anio) + 1} usa el valor manual")
                continue
            if atp + cop > 0:
                fila("pct_base", "todos", date(int(anio) + 1, 1, 1), date(int(anio) + 2, 1, 1), round(atp / (atp + cop), 4),
                     f"Litros {int(anio)}: ATP {atp:,.0f} · COP {cop:,.0f}", atp, atp + cop)
    else:
        print("   aviso: falta combustible.fact_carga_combustible: % base solo manual")
    return pd.DataFrame(filas)


def principal(argv=None, _conn=None):
    from comun.contrato_parametros import validar as validar_parametros
    validar_parametros()
    print(f"construir_dw_tarificador.py versión {VERSION}")
    dim = leer_csv(DIM_CSV)
    man = leer_csv(VALOR_CSV).rename(columns={"parametro": "codigo"})
    man["origen"] = "manual"
    desconocidos = set(man["codigo"]) - set(dim["codigo"])
    if desconocidos:
        raise SystemExit(f"Parámetros sin definir en dim_parametro.csv: {sorted(desconocidos)}")
    pj = leer_csv(PEAJE_CSV)
    pj["origen_norm"], pj["destino_norm"] = pj["origen"].map(norm), pj["destino"].map(norm)
    pj = pj.drop_duplicates(["origen_norm", "destino_norm", "vigente_desde"])

    con = db.conectar(("tarificador", "comun"), _conn=_conn)
    calc = calcular(con, dim, man)
    todos = pd.concat([man.assign(monto_base=None, cantidad_inductor=None, fecha_registro=db.ahora()), calc],
                      ignore_index=True)
    dim = dim.reset_index(drop=True)
    dim.insert(0, "sk_parametro", range(1, len(dim) + 1))
    sk = dict(zip(dim["codigo"], dim["sk_parametro"]))
    todos["sk_parametro"] = todos["codigo"].map(sk)
    todos["valor"] = pd.to_numeric(todos["valor"], errors="coerce")

    Parametros(todos)  # Rechazar valores inválidos y solapes antes de escribir.

    # precio diario del tarificador
    precios = pd.DataFrame()
    hay = con.uno("SELECT 1 FROM information_schema.views WHERE table_schema='precios' "
                  "AND table_name='v_costo_combustible_dia'") is not None
    if hay:
        pr = con.df("SELECT fecha::text AS fecha, oilsupplier, costo_real::float8 AS c FROM precios.v_costo_combustible_dia")
        precios = pr.pivot_table(index="fecha", columns="oilsupplier", values="c", aggfunc="first").reset_index()
        precios["sk_fecha"] = precios["fecha"].str.replace("-", "").astype(int)
        precios = precios.rename(columns={"ATP": "costo_base", "COP": "costo_tct"})
        fechas = pd.to_datetime(precios["fecha"])
        par = Parametros(todos)
        precios["pct_base"] = par.en("pct_base", fechas)
        anio_calc = set(pd.to_datetime(calc.loc[calc["codigo"] == "pct_base", "vigente_desde"]).dt.year)
        precios["origen_pct"] = ["BBDD" if f.year in anio_calc else "manual" for f in fechas]
        precios["recargo"] = par.en("recargo_precio", fechas)
        precios["precio_tarificador"] = (precios["pct_base"] * precios["costo_base"]
                                         + (1 - precios["pct_base"]) * precios["costo_tct"] + precios["recargo"])
        precios = precios[["fecha", "sk_fecha", "costo_base", "costo_tct", "pct_base", "origen_pct", "recargo",
                           "precio_tarificador"]]
    else:
        print("   aviso: falta precios.v_costo_combustible_dia (cargar precios): sin precio diario")

    with con:
        con.execute(ESQUEMA)
        con.copiar_df("tarificador.dim_parametro", dim[["sk_parametro", "codigo", "nombre", "categoria", "unidad",
                                                        "origen_valor", "descripcion", "regla_calculo",
                                                        "referencia_tarificador"]])
        con.copiar_df("tarificador.fact_parametro_valor", todos[[
            "sk_parametro", "ambito", "vigente_desde", "vigente_hasta", "valor", "origen", "monto_base",
            "cantidad_inductor", "nota", "fecha_registro"]])
        pj["peaje"] = pd.to_numeric(pj["peaje"], errors="coerce")
        con.copiar_df("tarificador.fact_peaje_tarificador", pj[["origen", "destino", "origen_norm", "destino_norm",
                                                                "vigente_desde", "vigente_hasta", "peaje", "nota"]])
        if len(precios):
            con.copiar_df("tarificador.fact_precio_combustible_tarificador", precios)
        con.execute(SQL_RESOLVER)
        con.execute(SQL_VISTAS)
        detalle = (f"parámetros {len(dim)}; valores manuales {len(man)}, calculados {len(calc)}; peajes {len(pj)}; "
                   f"precios diarios {len(precios)}")
        con.execute("INSERT INTO comun.registro_carga (area, fecha_carga, version_script, fecha_cambio, detalle) "
                    "VALUES (?,?,?,?,?)", ("tarificador", db.ahora(), VERSION, "", detalle))
    print(f"   tarificador.dim_parametro {len(dim):5d} · fact_parametro_valor {len(todos):5d} "
          f"(manuales {len(man)}, calculados {len(calc)}) · fact_peaje_tarificador {len(pj):,} · "
          f"fact_precio_combustible_tarificador {len(precios):,}")
    pb = calc[calc["codigo"] == "pct_base"]
    if len(pb):
        print("   % base por año (litros ATP del año anterior): "
              + ", ".join(f"{d[:4]} {float(v):.1%}" for d, v in zip(pb["vigente_desde"], pb["valor"])))
    hoy = Parametros(todos)
    for c, a in (("km_dia", "todos"), ("reposicion_diaria", "todos"), ("reposicion_diaria", "tractoreo"),
                 ("costo_neumatico_km", "todos"), ("costo_fijo_diario", "todos"), ("pct_base", "todos")):
        print(f"   vigente hoy {c:20s} {a:9s} {hoy.uno(c, date.today(), a):>14,.2f}")
    if len(precios):
        u = precios[precios["fecha"] <= str(date.today())].dropna(subset=["precio_tarificador"]).iloc[-1]
        print(f"   precio del tarificador {u['fecha']}: base {u['costo_base']:,.2f} · TCT {u['costo_tct']:,.2f} · "
              f"pct base {u['pct_base']:.1%} · recargo {u['recargo']:,.0f} → {u['precio_tarificador']:,.2f} $/L")
    con.close()


if __name__ == "__main__":
    principal(sys.argv[1:])
