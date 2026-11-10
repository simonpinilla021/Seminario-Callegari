"""Entrada para revisión académica y construcción autorizada del DW."""
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


def crear_esquema():
    if not ESQUEMA_SQL.exists():
        raise SystemExit(f'Falta {ESQUEMA_SQL}. Se genera con pg_dump --schema-only (ver README).')
    texto = ESQUEMA_SQL.read_text(encoding='utf-8')
    esquemas = sorted(set(ESQUEMAS) | set(esquemas_del_volcado(texto)))
    db = _db()
    pg = db.leer_config()["postgres"]
    if pg.get("dbname") != "dw_callegari_esquema" or pg.get("host","localhost").lower() not in ("localhost","127.0.0.1","::1"):
        raise SystemExit("crear-esquema requiere la base local dw_callegari_esquema")
    db.crear_base_si_falta()
    con = db.conectar()
    try:
        existentes = esquemas_con_tablas(con, esquemas)
        if existentes:
            raise SystemExit('La base de revisión ya tiene objetos. Use una base vacía.')
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
    sub.add_parser('crear-esquema', help='crea tablas y vistas vacías')
    sub.add_parser('demo', help='carga plazas y tarifas públicas MOP')
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
        return crear_esquema()
    if a.comando == 'demo':
        from peajes import construir_dw_peajes
        return construir_dw_peajes.principal(["--solo-mop"])
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
