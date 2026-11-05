"""Maestro común y soporte propio de taller, sin perder equipos fuera del catálogo."""
import pandas as pd
from comun.tipos_equipo import expresion_sql

ACTIVIDAD = ['equipo_id','primera_actividad','ultima_actividad','fecha_alta','fecha_baja']
COMPARTIDOS = {
    'codigo': 'm.codigo', 'patente': 'm.patente', 'nombre': 'm.nombre_fuente',
    'categoria': 'upper(trim(m.categoria_fuente))',
    'tipo_equipo': expresion_sql('m.categoria_fuente'),
    'grupo_empresa': 'm.grupo_empresa_fuente',
    'es_propio': "CASE WHEN upper(trim(m.es_propio_fuente)) IN ('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END",
    'es_flota': "CASE WHEN upper(trim(m.es_flota_fuente)) IN ('Y','S','SI','TRUE','1') THEN 'S' ELSE 'N' END",
    'estado_actual': 'm.estado_fuente', 'marca': 'm.marca', 'modelo': 'm.modelo',
    'anio_fabricacion': 'm.anio_fabricacion_fuente::numeric::integer',
    'centro_costo_id': 'm.centro_costo_id', 'unidad_negocio_id': 'm.unidad_negocio_id'}
TIPOS = dict(equipo_id='BIGINT PRIMARY KEY', codigo='TEXT', patente='TEXT', nombre='TEXT',
             categoria='TEXT', tipo_equipo='TEXT', grupo_empresa='TEXT', es_propio='TEXT',
             es_flota='TEXT', es_camion='TEXT', estado_actual='TEXT', marca='TEXT', modelo='TEXT',
             anio_fabricacion='INTEGER', rendimiento_ficha='DOUBLE PRECISION', centro_costo_id='BIGINT',
             unidad_negocio_id='BIGINT', fuente='TEXT', fecha_carga='TIMESTAMP')


def separar(d, ids_maestro):
    if d.equipo_id.isna().any() or d.equipo_id.duplicated().any():
        raise ValueError('Equipo nulo o duplicado en soporte taller')
    actividad = d[ACTIVIDAD].copy()
    soporte = d[list(TIPOS)].copy()
    # No guardar otra copia de los atributos cuya autoridad es dim_comun.
    for c in COMPARTIDOS:
        soporte.loc[soporte.equipo_id.isin(ids_maestro), c] = None
    return actividad, soporte


def vista_sql():
    cols = ['r.equipo_id']
    for c in list(TIPOS)[1:]:
        if c in COMPARTIDOS:
            cols.append(f'CASE WHEN m.equipo_id IS NOT NULL THEN {COMPARTIDOS[c]} ELSE r.{c} END AS {c}')
        else:
            cols.append('r.' + c)
    # Orden de columnas del contrato histórico.
    cols = cols[:-2] + ['a.' + c for c in ACTIVIDAD[1:]] + cols[-2:]
    return ('CREATE VIEW taller.dim_equipo AS SELECT ' + ',\n'.join(cols) +
            ' FROM taller.ref_equipo_taller r LEFT JOIN dim_comun.dim_equipo m USING(equipo_id) '
            'LEFT JOIN taller.resumen_actividad_equipo a USING(equipo_id)')


def comparar(dimension,leido):
    """Comparar valores tras normalizar la representación de tipos y nulos SQL."""
    columnas=list(dimension.columns)
    izquierda,derecha=dimension[columnas].copy(),leido[columnas].copy()
    for d in (izquierda,derecha):
        for c in columnas:
            tipo=TIPOS.get(c,'DATE' if c in ACTIVIDAD[1:] else '')
            if tipo.startswith(('BIGINT','INTEGER')):
                d[c]=pd.to_numeric(d[c],errors='raise').astype('Int64')
            elif tipo.startswith('DOUBLE'):
                d[c]=pd.to_numeric(d[c],errors='raise').astype('float64')
            elif tipo.startswith('TEXT'):
                d[c]=d[c].astype('string')
            elif tipo in ('DATE','TIMESTAMP'):
                d[c]=pd.to_datetime(d[c],errors='raise').astype('datetime64[ns]')
    pd.testing.assert_frame_equal(izquierda.sort_values('equipo_id').reset_index(drop=True),
        derecha.sort_values('equipo_id').reset_index(drop=True),
        check_dtype=False,check_exact=False,rtol=0,atol=1e-10)


def cargar(con, dimension):
    if not con.existe_tabla('dim_comun','dim_equipo'):
        raise RuntimeError('Falta maestro dim_comun.dim_equipo')
    ids = con.df('SELECT equipo_id FROM dim_comun.dim_equipo').equipo_id
    actividad, soporte = separar(dimension, ids)
    con.execute('''DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='taller' AND c.relname='dim_equipo' AND c.relkind='r') THEN
        DROP TABLE taller.dim_equipo CASCADE;
      ELSE
        DROP VIEW IF EXISTS taller.dim_equipo CASCADE;
      END IF;
    END $$;''')
    campos = ','.join(c + ' ' + t for c,t in TIPOS.items())
    con.execute(f'CREATE TABLE IF NOT EXISTS taller.ref_equipo_taller ({campos})')
    con.execute('''CREATE TABLE IF NOT EXISTS taller.resumen_actividad_equipo (
      equipo_id BIGINT PRIMARY KEY, primera_actividad DATE,ultima_actividad DATE,
      fecha_alta DATE,fecha_baja DATE)''')
    con.execute("COMMENT ON TABLE taller.resumen_actividad_equipo IS 'Actividad y periodo inferidos para estado diario; no son alta/baja administrativa observada'")
    con.execute('DELETE FROM taller.ref_equipo_taller')
    con.execute('DELETE FROM taller.resumen_actividad_equipo')
    con.copiar_df('taller.ref_equipo_taller', soporte)
    con.copiar_df('taller.resumen_actividad_equipo', actividad)
    con.execute(vista_sql())
    leido = con.df('SELECT * FROM taller.dim_equipo ORDER BY equipo_id')
    comparar(dimension,leido)
    return dict(equipos=len(dimension), compartidos=int(dimension.equipo_id.isin(ids).sum()),
                referencias_fuera_maestro=int((~dimension.equipo_id.isin(ids)).sum()))
