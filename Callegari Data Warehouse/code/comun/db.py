# -*- coding: utf-8 -*-
"""
Conexión a PostgreSQL para los scripts del MVP Data Warehouse.

- La conexión sale de config.ini. Contraseña desde PGPASSWORD, configuración heredada o almacén
  local DPAPI; si no existe ninguna, se pregunta al ejecutar.
- `Base` envuelve la conexión con la misma forma de uso que tenían los scripts con SQLite:
    con.execute(sql, params) / con.executemany(sql, filas) / con.df(sql) / con.copiar(tabla, columnas, filas)
  Los parámetros se escriben con "?" (como en SQLite) o "%s"; se convierten solos. También convierte los tipos de
  numpy/pandas y los NaN a NULL, para que ningún NaN quede guardado como número.
- Cada área usa su propio esquema de PostgreSQL (peajes, combustible) y comparte el esquema comun.
"""
import configparser
import getpass
import math
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

MVP = Path(__file__).resolve().parent.parent              # carpeta MVP Data Warehouse
CONFIG = MVP / "config.ini"

try:
    import numpy as np
except ImportError:                                       # pragma: no cover
    np = None


def leer_config():
    cfg = configparser.ConfigParser(interpolation=None)
    if not CONFIG.exists():
        raise SystemExit(f"Falta {CONFIG}. Copiar config.ejemplo.ini como config.ini y completar la conexión.")
    cfg.read(CONFIG, encoding="utf-8")
    return cfg


def ruta_config(seccion, clave):
    """Ruta de config.ini, relativa a la carpeta MVP Data Warehouse."""
    return (MVP / leer_config()[seccion][clave]).resolve()


def parametros_conexion(dbname=None):
    pg = leer_config()["postgres"]
    clave = os.environ.get("PGPASSWORD")
    if clave is None and pg.get("password", ""):
        clave = pg["password"]
    if clave is None:
        from comun.credenciales import leer
        destino=dict(host=pg.get('host','localhost'),port=pg.get('port','5432'),
                     dbname=dbname or pg.get('dbname','dw_callegari'),user=pg.get('user','postgres'))
        guardado=leer('dw',destino)
        if guardado:
            clave=guardado['password']
    if clave is None:
        clave = getpass.getpass(f"Contraseña de PostgreSQL para {pg.get('user', 'postgres')}: ")
        os.environ["PGPASSWORD"] = clave                  # para no volver a preguntar en el mismo proceso
    return dict(host=pg.get("host", "localhost"), port=int(pg.get("port", "5432")), user=pg.get("user", "postgres"),
                password=clave, dbname=dbname or pg.get("dbname", "dw_callegari"))


def _valor(v):
    """Tipos de pandas/numpy a tipos de Python; NaN y NA a None."""
    if v is None:
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    if np is not None and isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def _params(p):
    if p is None:
        return None
    if isinstance(p, dict):
        return {k: _valor(v) for k, v in p.items()}
    return tuple(_valor(v) for v in p)


def _sql(s):
    return s.replace("?", "%s")


class Base:
    """Conexión con la forma de uso de sqlite3 (execute devuelve un cursor iterable)."""

    def __init__(self, conn, esquemas=None):
        self.conn = conn
        if esquemas:
            self.execute("SET search_path TO " + ", ".join(esquemas))

    # --- consultas
    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(_sql(sql), _params(params))
        return cur

    def executemany(self, sql, filas):
        filas = [_params(f) for f in filas]
        if filas:
            cur = self.conn.cursor()
            cur.executemany(_sql(sql), filas)

    def uno(self, sql, params=None):
        return self.execute(sql, params).fetchone()

    def df(self, sql, params=None):
        cur = self.execute(sql, params)
        cols = [getattr(d, "name", None) or d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)

    def copiar(self, tabla, columnas, filas):
        """Carga masiva con COPY (mucho más rápida que INSERT fila a fila)."""
        cur = self.conn.cursor()
        with cur.copy(f"COPY {tabla} ({', '.join(columnas)}) FROM STDIN") as cp:
            for f in filas:
                cp.write_row(_params(f))

    def copiar_df(self, tabla, df):
        self.copiar(tabla, list(df.columns), df.itertuples(index=False, name=None))

    def existe_tabla(self, esquema, tabla):
        return self.uno("SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                        (esquema, tabla)) is not None

    # --- transacción: "with con:" confirma al terminar o deshace si hubo error (igual que sqlite3)
    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, tb):
        if tipo is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def conectar(esquemas=None, dbname=None, _conn=None):
    """Conecta a la base del DW. `esquemas`: search_path (el primero es donde se crean las tablas sin esquema)."""
    if _conn is None:
        try:
            import psycopg
        except ImportError:
            raise SystemExit('Falta psycopg: pip install "psycopg[binary]"')
        _conn = psycopg.connect(**parametros_conexion(dbname))
    return Base(_conn, esquemas)


def crear_base_si_falta():
    """Crea la base de datos (dbname de config.ini) si no existe. Necesita permiso para crear bases."""
    import psycopg
    par = parametros_conexion()
    nombre = par["dbname"]
    with psycopg.connect(**dict(par, dbname="postgres"), autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (nombre,)).fetchone() is None:
            c.execute(f'CREATE DATABASE "{nombre}"')
            print(f"Base de datos {nombre} creada")


def hoy():
    return date.today().isoformat()


def ahora():
    return datetime.now().isoformat(timespec="seconds")
