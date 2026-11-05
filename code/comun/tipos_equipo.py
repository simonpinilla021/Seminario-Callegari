"""Clasificación compartida de equipos; no altera la categoría original del ERP.

FURGON está acreditado como equipo secundario en viajes. CHASIS queda en otro
hasta verificar su función: su nombre, por sí solo, no prueba que sea rampla.
"""
import pandas as pd

RAMPLAS = ('THERMO', 'PLANA', 'CAMA BAJA', 'FURGON')
VERSION = '2026-10-30-furgon-rampla-v1'


def clasificar(categorias):
    c = categorias.astype('string').str.strip().str.upper()
    r = pd.Series('otro', index=c.index, dtype='string')
    r.loc[c.str.startswith('TRACTO').fillna(False)] = 'tracto'
    r.loc[c.isin(RAMPLAS)] = 'rampla'
    return r


def expresion_sql(campo):
    """campo es una expresión interna del módulo llamador, nunca entrada del CLI."""
    categorias = ','.join("'"+c+"'" for c in RAMPLAS)
    return (f"CASE WHEN upper(trim({campo})) LIKE 'TRACTO%' THEN 'tracto' "
            f"WHEN upper(trim({campo})) IN ({categorias}) THEN 'rampla' ELSE 'otro' END")


def vistas_comunes_sql():
    sql = [f'CREATE OR REPLACE VIEW dim_comun.v_dim_equipo AS SELECT e.*, '
           f'{expresion_sql("categoria_fuente")} AS tipo_equipo_derivado FROM dim_comun.dim_equipo e;']
    for nombre, categoria in [('tracto','tracto'),('rampla','rampla'),('otros_equipos','otro')]:
        sql.append(f"CREATE OR REPLACE VIEW dim_comun.v_dim_{nombre} AS SELECT * FROM dim_comun.v_dim_equipo WHERE tipo_equipo_derivado='{categoria}';")
    return '\n'.join(sql)
