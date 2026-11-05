#!/usr/bin/env python3
"""Punto de entrada único del DW Callegari.

Uso (desde la carpeta code, con config.ini creado):

  python -B dw.py crear-base                 Crea la base de datos de config.ini si no existe.
  python -B dw.py crear-esquema              Crea todas las tablas, vistas y relaciones, sin datos
                                             (postgres/esquema_dw.sql). --reemplazar borra los esquemas existentes.
  python -B dw.py demo                       Esquema vacío + carga real del área de peajes con datos públicos del MOP
                                             y el calendario. No necesita datos de la empresa.
  python -B dw.py documentar                 Genera docs/diccionario_datos.md leyendo la base (tablas, columnas,
                                             llaves y comentarios).
  python -B dw.py desde-archivos --validar   Construcción completa con los Parquet de src/ (construir_todo.py).
  python -B dw.py desde-erp --validar        Descarga todas las fuentes del ERP por red y construye (actualizar_dw.py).
  python -B dw.py actualizar --validar       Descarga solo las fuentes vencidas del ERP y reconstruye.

En los tres últimos, --validar construye y revierte; --guardar confirma. Sin ninguno de los dos solo se describe el plan.
desde-erp y actualizar necesitan acceso a la red de la empresa y credenciales del ERP.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE))
ESQUEMA_SQL = CODE / 'postgres' / 'esquema_dw.sql'
ESQUEMAS = ('comun', 'peajes', 'combustible', 'precios', 'costos', 'ventas', 'ciclos', 'tarificador',
            'escenarios', 'taller', 'dim_comun', 'ds_gestion')


def _db():
    from comun import db
    return db


def crear_base():
    _db().crear_base_si_falta()
    print('Base de datos lista.')


def _sentencias_sql(texto):
    """Limpia un volcado de pg_dump para ejecutarlo con psycopg: quita metacomandos de psql (líneas con \\) y
    parámetros de sesión que no existen en versiones anteriores de PostgreSQL."""
    lineas = [l for l in texto.splitlines() if not l.startswith('\\') and not l.startswith('SET transaction_timeout')]
    return '\n'.join(re.sub(r'^CREATE SCHEMA (?!IF NOT EXISTS)', 'CREATE SCHEMA IF NOT EXISTS ', l) for l in lineas)


def esquemas_del_volcado(texto):
    return sorted(set(re.findall(r'^CREATE SCHEMA (?:IF NOT EXISTS )?"?(\w+)"?;', texto, re.M)) - {'public'})


def esquemas_con_tablas(con, esquemas=ESQUEMAS):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname = ANY(%s) AND c.relkind IN ('r','v','m')", (list(esquemas),)).fetchall()]


def crear_esquema(reemplazar=False):
    if not ESQUEMA_SQL.exists():
        raise SystemExit(f'Falta {ESQUEMA_SQL}. Se genera con pg_dump --schema-only (ver README).')
    texto = ESQUEMA_SQL.read_text(encoding='utf-8')
    esquemas = sorted(set(ESQUEMAS) | set(esquemas_del_volcado(texto)))
    db = _db()
    db.crear_base_si_falta()
    con = db.conectar()
    try:
        existentes = esquemas_con_tablas(con, esquemas)
        if existentes and not reemplazar:
            raise SystemExit(f'La base ya tiene objetos en {", ".join(existentes)}. '
                             'Use --reemplazar para borrarlos y crear el esquema vacío.')
        for esquema in esquemas:
            con.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')
        con.execute(_sentencias_sql(texto))
        con.execute("SELECT pg_catalog.set_config('search_path', 'public', false)")
        con.commit()
        resumen = con.df("""SELECT n.nspname AS esquema,
                 count(*) FILTER (WHERE c.relkind='r') AS tablas, count(*) FILTER (WHERE c.relkind='v') AS vistas
                 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname = ANY(%s) GROUP BY 1 ORDER BY 1""", (list(ESQUEMAS),))
        print(resumen.to_string(index=False))
        print('Esquema creado sin datos.')
    except BaseException:
        con.conn.rollback()
        raise
    finally:
        con.close()


def demo(reemplazar=False):
    """Esquema vacío y carga del área peajes con sus CSV públicos. La etapa de peajes es la misma que usa la
    construcción completa: lee láminas MOP ya procesadas, arma plazas, tarifas por fecha y peaje por tramo."""
    crear_esquema(reemplazar=reemplazar)
    from peajes import construir_dw_peajes
    print('\nCargando el área peajes (datos públicos del MOP)...')
    # Los viajes son datos de la empresa: se entrega un archivo vacío con sus columnas, así fact_viaje_peaje
    # queda vacía y el resto del área se carga completo.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        vacio = Path(tmp) / 'viajes_vacio.csv'
        vacio.write_text('c_orderline_id,sit_roadmap_id,sit_stretch_id,datepromised,isreturn\n', encoding='utf-8')
        construir_dw_peajes.principal(['--viajes', str(vacio)])
    print('\nDemo lista. Consultas sugeridas:')
    print('  SELECT * FROM peajes.dim_plaza_peaje LIMIT 20;')
    print('  SELECT * FROM peajes.fact_tarifa_peaje LIMIT 20;')
    print('  SELECT * FROM peajes.fact_peaje_referencial_tramo LIMIT 20;')


def documentar(salida=None):
    """Diccionario de datos desde el catálogo de PostgreSQL: un archivo Markdown con cada tabla y vista."""
    db = _db()
    con = db.conectar()
    try:
        obj = con.df("""SELECT n.nspname AS esquema, c.relname AS objeto,
                 CASE c.relkind WHEN 'r' THEN 'tabla' WHEN 'v' THEN 'vista' WHEN 'm' THEN 'vista materializada' END AS tipo,
                 obj_description(c.oid,'pg_class') AS comentario, c.oid
                 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname = ANY(%s) AND c.relkind IN ('r','v','m') ORDER BY 1,2""", (list(ESQUEMAS),))
        col = con.df("""SELECT a.attrelid AS oid, a.attname AS columna, format_type(a.atttypid,a.atttypmod) AS tipo,
                 NOT a.attnotnull AS nulo, col_description(a.attrelid,a.attnum) AS comentario
                 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname = ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attrelid,a.attnum""",
                     (list(ESQUEMAS),))
        llaves = con.df("""SELECT c.conrelid AS oid, c.contype AS tipo, pg_get_constraintdef(c.oid) AS definicion
                 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
                 WHERE n.nspname = ANY(%s) AND c.contype IN ('p','u','f') ORDER BY 1,2""", (list(ESQUEMAS),))
    finally:
        con.close()
    nombre_llave = {'p': 'Llave primaria', 'u': 'Única', 'f': 'Llave foránea'}
    lineas = ['# Diccionario de datos', '',
              'Generado con `python -B dw.py documentar` a partir del catálogo de PostgreSQL.', '']
    resumen = obj.groupby(['esquema', 'tipo']).size().unstack(fill_value=0)
    lineas += ['## Resumen por esquema', '', '| Esquema | ' + ' | '.join(resumen.columns) + ' |',
               '|---|' + '---:|' * len(resumen.columns)]
    lineas += ['| ' + e + ' | ' + ' | '.join(str(v) for v in fila) + ' |' for e, fila in resumen.iterrows()]
    relaciones = []
    for esquema, grupo in obj.groupby('esquema', sort=True):
        lineas += ['', f'## Esquema `{esquema}`']
        for o in grupo.itertuples():
            lineas += ['', f'### `{esquema}.{o.objeto}` ({o.tipo})']
            if isinstance(o.comentario, str) and o.comentario.strip():
                lineas += ['', str(o.comentario)]
            for k in llaves[llaves.oid == o.oid].itertuples():
                lineas.append(f'- {nombre_llave[k.tipo]}: `{k.definicion}`')
                if k.tipo == 'f':
                    m = re.search(r'REFERENCES ([\w.]+)', k.definicion)
                    if m:
                        relaciones.append((f'{esquema}.{o.objeto}', m.group(1)))
            lineas += ['', '| Columna | Tipo | Nulo | Descripción |', '|---|---|---|---|']
            for c in col[col.oid == o.oid].itertuples():
                desc = (c.comentario if isinstance(c.comentario, str) else '').replace('|', '/').replace('\n', ' ')
                lineas.append(f'| `{c.columna}` | {c.tipo} | {"sí" if c.nulo else "no"} | {desc} |')
    if relaciones:
        lineas += ['', '## Relaciones (llaves foráneas)', '', '```mermaid', 'flowchart LR']
        for a, b in sorted(set(relaciones)):
            lineas.append(f'  {a.replace(".", "_")}["{a}"] --> {b.replace(".", "_")}["{b}"]')
        lineas += ['```']
    destino = Path(salida) if salida else CODE.parent / 'docs' / 'diccionario_datos.md'
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text('\n'.join(lineas) + '\n', encoding='utf-8')
    print(f'{len(obj)} objetos documentados en {destino}')


def delegar(script, argumentos):
    return subprocess.call([sys.executable, '-B', str(CODE / script), *argumentos], cwd=CODE)


def principal(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='comando', required=True)
    sub.add_parser('crear-base', help='crea la base de datos si no existe')
    for nombre, ayuda in [('crear-esquema', 'tablas y vistas vacías'), ('demo', 'esquema + peajes públicos')]:
        p = sub.add_parser(nombre, help=ayuda)
        p.add_argument('--reemplazar', action='store_true', help='borra los esquemas del DW antes de crearlos')
    p = sub.add_parser('documentar', help='genera el diccionario de datos')
    p.add_argument('--salida')
    for nombre, ayuda in [('desde-archivos', 'construcción completa con los Parquet de src/'),
                          ('desde-erp', 'descarga completa del ERP y construcción'),
                          ('actualizar', 'descarga lo vencido del ERP y reconstruye')]:
        p = sub.add_parser(nombre, help=ayuda)
        modo = p.add_mutually_exclusive_group()
        modo.add_argument('--validar', action='store_true')
        modo.add_argument('--guardar', action='store_true')
    a = ap.parse_args(argv)
    if a.comando == 'crear-base':
        return crear_base()
    if a.comando == 'crear-esquema':
        return crear_esquema(a.reemplazar)
    if a.comando == 'demo':
        return demo(a.reemplazar)
    if a.comando == 'documentar':
        return documentar(a.salida)
    modo = ['--validar'] if a.validar else ['--guardar'] if a.guardar else []
    if a.comando == 'desde-archivos':
        return delegar('construir_todo.py', ['--desde-cero', *modo] if modo else ['--plan'])
    if a.comando == 'desde-erp':
        return delegar('actualizar_dw.py', ['--modo', 'inicial', *modo])
    if a.comando == 'actualizar':
        return delegar('actualizar_dw.py', ['--modo', 'actualizar', *modo])


if __name__ == '__main__':
    sys.exit(principal() or 0)
