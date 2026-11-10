"""Carga atómica de maestros preparados, sin alterar módulos vigentes.

Sin opciones: valida archivos sin conectar. --validar: escribe, verifica y revierte.
--guardar: confirma después de verificar todos los valores leídos desde PostgreSQL.
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from comun import db
from comun import calendario
from comun.tipos_equipo import vistas_comunes_sql
from dimensiones.preparar_dimensiones_comunes import CONTRATOS, enteros

VERSION = '2026-10-30-carga-dimensiones-tramo-v3'
ESPEC = {}
for _, (tabla, clave, columnas) in CONTRATOS.items():
    ESPEC[tabla] = (list(columnas.values()) + ['extraccion_id', 'fuente_sha256'], [clave])
ESPEC['dim_fecha'] = (['sk_fecha', 'fecha', 'anio', 'mes', 'dia', 'dia_semana'], ['sk_fecha'])
ESPEC['roles_socio'] = (['socio_id', 'rol', 'usado_en_viajes', 'marca_fuente'], ['socio_id', 'rol'])


def tipo(c):
    if c == 'km_fuente':
        return 'DOUBLE PRECISION'
    if c.endswith('_id') and c != 'extraccion_id' or c in ['sk_fecha', 'anio', 'mes', 'dia', 'dia_semana']:
        return 'BIGINT'
    if c == 'fecha':
        return 'DATE'
    if c == 'usado_en_viajes':
        return 'BOOLEAN'
    return 'TEXT'


def sql_esquema():
    sql = ['CREATE SCHEMA IF NOT EXISTS dim_comun;']
    for tabla, (columnas, claves) in ESPEC.items():
        if tabla == 'dim_fecha':
            continue
        campos = ', '.join(f'{c} {tipo(c)}' for c in columnas)
        sql.append(f'CREATE TABLE IF NOT EXISTS dim_comun.{tabla} ({campos}, PRIMARY KEY ({", ".join(claves)}));')
    sql.append(vistas_comunes_sql())
    return '\n'.join(sql)


def convertir(d):
    for c in d:
        if tipo(c) == 'BIGINT':
            d[c] = enteros(d[c])
        elif tipo(c) == 'DOUBLE PRECISION':
            d[c] = pd.to_numeric(d[c], errors='raise').astype('float64')
        elif tipo(c) == 'DATE':
            d[c] = pd.to_datetime(d[c], errors='raise').dt.date
        elif tipo(c) == 'BOOLEAN':
            if not d[c].isin(['True', 'False']).all():
                raise ValueError(f'Booleano inválido: {c}')
            d[c] = d[c].map({'True': True, 'False': False})
    return d


def leer_preparacion(carpeta):
    carpeta = Path(carpeta)
    ruta = carpeta/'manifiesto.json'
    huella = hashlib.sha256(ruta.read_bytes()).hexdigest()
    m = json.loads(ruta.read_text(encoding='utf-8'))
    if m.get('estado') != 'preparado_offline':
        raise ValueError('Preparación no aceptada')
    salidas = {s['archivo']: s['sha256'] for s in m['salidas']}
    tablas = {}
    for tabla, (columnas, claves) in ESPEC.items():
        archivo = carpeta/f'{tabla}.csv'
        if hashlib.sha256(archivo.read_bytes()).hexdigest() != salidas.get(archivo.name):
            raise ValueError(f'Huella distinta: {archivo.name}')
        # Cadenas NA/NULL originales no deben convertirse silenciosamente a nulos.
        d = pd.read_csv(archivo, dtype='string', keep_default_na=False).replace('', pd.NA)
        if list(d.columns) != columnas:
            raise ValueError(f'Contrato de columnas distinto: {tabla}')
        d = convertir(d)
        if d[claves].isna().any().any() or d.duplicated(claves).any() or d.empty:
            raise ValueError(f'Clave nula/duplicada o maestro vacío: {tabla}')
        if len(d) != m['tablas'][tabla]:
            raise ValueError(f'Conteo distinto: {tabla}')
        tablas[tabla] = d
    roles = tablas['roles_socio']
    if not roles.socio_id.isin(tablas['dim_socio_negocio'].socio_id).all():
        raise ValueError('Rol sin socio')
    if not roles.rol.isin(['cliente', 'transportista', 'proveedor']).all():
        raise ValueError('Rol no reconocido')
    return tablas, huella


def filas(d, claves):
    return [tuple(db._valor(v) for v in f) for f in
            d.sort_values(claves).itertuples(index=False, name=None)]


def verificar_lectura(con, tablas):
    controles = {}
    for tabla, (columnas, claves) in ESPEC.items():
        leido = con.df(f'SELECT {", ".join(columnas)} FROM dim_comun.{tabla}')
        comparable = leido[leido.sk_fecha.isin(tablas[tabla].sk_fecha)] if tabla == 'dim_fecha' else leido
        if filas(tablas[tabla], claves) != filas(comparable, claves):
            raise RuntimeError(f'Lectura posterior distinta: {tabla}')
        controles[tabla] = len(leido)
    particion = con.uno('SELECT (SELECT count(*) FROM dim_comun.v_dim_tracto) + '
                        '(SELECT count(*) FROM dim_comun.v_dim_rampla) + '
                        '(SELECT count(*) FROM dim_comun.v_dim_otros_equipos)')[0]
    if particion != len(tablas['dim_equipo']):
        raise RuntimeError('Las vistas no particionan el maestro de equipos')
    return controles


def cargar(con, tablas):
    # Mismo bloqueo que la reconstrucción general: impide concurrencia entre cargas.
    if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
        raise RuntimeError('Otra carga está en curso')
    con.execute("SET LOCAL lock_timeout='5s'")
    con.execute("SET LOCAL statement_timeout='15min'")
    con.execute(sql_esquema())
    calendario.publicar(con, tablas['dim_fecha'])
    for tabla in reversed(list(ESPEC)):
        if tabla == 'dim_fecha':
            continue
        con.execute(f'DELETE FROM dim_comun.{tabla}')
    for tabla, (columnas, claves) in ESPEC.items():
        if tabla == 'dim_fecha':
            continue
        d = tablas[tabla]
        con.copiar_df(f'dim_comun.{tabla}', d)
    return verificar_lectura(con, tablas)


def principal():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--preparacion', type=Path, required=True)
    modos = p.add_mutually_exclusive_group()
    modos.add_argument('--validar', action='store_true')
    modos.add_argument('--guardar', action='store_true')
    a = p.parse_args()
    tablas, huella = leer_preparacion(a.preparacion)
    carpeta = a.preparacion/'cargas'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    carpeta.mkdir(parents=True, exist_ok=False)
    (carpeta/'esquema.sql').write_text(sql_esquema(), encoding='utf-8')
    informe = dict(version=VERSION, preparacion_sha256=huella, confirmado=False,
                   estado='archivos_verificados', tablas={k: len(v) for k, v in tablas.items()})
    con = None
    try:
        if a.validar or a.guardar:
            con = db.conectar()
            informe['controles_lectura'] = cargar(con, tablas)
            # Verifica de nuevo las huellas antes de confirmar la fotografía.
            _, despues = leer_preparacion(a.preparacion)
            if despues != huella:
                raise RuntimeError('La preparación cambió durante la carga')
            if a.guardar:
                con.commit()
                informe.update(estado='confirmado', confirmado=True)
            else:
                con.conn.rollback()
                informe['estado'] = 'validado_y_revertido'
    except BaseException as e:
        if con is not None:
            con.conn.rollback()
        informe.update(estado='fallido', error=str(e))
        raise
    finally:
        if con is not None:
            con.close()
        (carpeta/'ejecucion.json').write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    print(f'Informe: {carpeta}')


if __name__ == '__main__':
    principal()
