"""Calendario único; comun conserva la tabla por sus FK y dim_comun la publica."""
from datetime import date
import pandas as pd

COLUMNAS = ['sk_fecha','fecha','anio','mes','dia','dia_semana']
TODAS = COLUMNAS + ['nombre_dia','es_fin_de_semana']
NOMBRES = ['lunes','martes','miércoles','jueves','viernes','sábado','domingo']
VERSION = '2026-10-30-calendario-alcance-2018-v1'


def generar(desde, hasta):
    dias = pd.date_range(desde, hasta)
    return pd.DataFrame(dict(sk_fecha=dias.strftime('%Y%m%d').astype(int), fecha=dias.date,
        anio=dias.year, mes=dias.month, dia=dias.day, dia_semana=dias.dayofweek+1,
        nombre_dia=[NOMBRES[d] for d in dias.dayofweek],
        es_fin_de_semana=['S' if d>=5 else 'N' for d in dias.dayofweek]))


def desde_fuentes(erp, rutas, leer):
    fuentes = [
        (erp/'adempiere_sit_pbix_travelvta_v.parquet',['datepromised','enddatetravel']),
        (erp/'adempiere_sit_pbix_guiascombust_v.parquet',['datetrx']),
        (erp/'adempiere_sit_pbix_prodconsumo_v.parquet',['movementdate']),
        (rutas['combustible']/'sit_pbix_guiascombust_v.parquet',['datetrx']),
        (rutas['contabilidad']/'rv_fact_acct.parquet',['dateacct']),
        (rutas['tablas']/'sit_workorder.parquet',['created','enddate','updated'])]
    inicio_analitico = date(2018,1,1)
    perfiles, limites = [], [inicio_analitico,date(2030,12,31)]
    for archivo, columnas in fuentes:
        d = leer(archivo, columns=columnas)
        for c in columnas:
            valores = pd.to_datetime(d[c], errors='raise').dropna()
            if valores.empty:
                perfiles.append(dict(archivo=str(archivo),campo=c,nulos=len(d),desde=None,hasta=None))
                continue
            desde, hasta = valores.min().date(), valores.max().date()
            if desde.year < 1900 or hasta.year > 2100:
                raise ValueError(f'Fecha fuera del intervalo admitido 1900-2100: {archivo.name}.{c}')
            dentro = valores[valores.dt.date >= inicio_analitico]
            perfiles.append(dict(archivo=str(archivo),campo=c,nulos=int(d[c].isna().sum()),
                                  desde=str(desde),hasta=str(hasta),
                                  anteriores_a_2018=int((valores.dt.date < inicio_analitico).sum())))
            if not dentro.empty:
                limites.append(dentro.max().date())
    inicio, fin = date(min(limites).year,1,1), date(max(limites).year,12,31)
    return generar(inicio,fin)[COLUMNAS], dict(inicio=str(inicio),fin=str(fin),
        alcance='histórico analítico desde 2018; rentabilidad desde 2025; horizonte mínimo hasta 2030; no filtra hechos', fuentes=perfiles)


def verificar_existentes(actual, esperado):
    if actual.empty:
        return
    if actual.sk_fecha.isna().any() or actual.sk_fecha.duplicated().any():
        raise ValueError('Calendario existente con claves nulas o duplicadas')
    e = esperado.set_index('sk_fecha')
    for c in actual.columns:
        if c not in e.columns:
            continue
        izquierda = actual.set_index('sk_fecha')[c].sort_index()
        derecha = e.loc[izquierda.index,c]
        # Preparación usa Int64 anulable; PostgreSQL/generación pueden usar int64.
        # La búsqueda anterior exige las mismas claves; normalizar su representación
        # evita falsos desacuerdos de pandas entre índices anulables y NumPy.
        izquierda.index=izquierda.index.astype('int64')
        derecha.index=derecha.index.astype('int64')
        pd.testing.assert_series_equal(izquierda,derecha,check_dtype=False,check_index_type=False,check_names=False)


def publicar(con, preparado):
    con.execute('''CREATE SCHEMA IF NOT EXISTS comun;
      CREATE SCHEMA IF NOT EXISTS dim_comun;
      CREATE TABLE IF NOT EXISTS comun.dim_fecha(
        sk_fecha INTEGER PRIMARY KEY,fecha DATE UNIQUE,anio INTEGER,mes INTEGER,dia INTEGER,
        dia_semana INTEGER,nombre_dia TEXT,es_fin_de_semana TEXT)''')
    actual = con.df('SELECT '+','.join(TODAS)+' FROM comun.dim_fecha')
    anterior_dim = con.df('SELECT '+','.join(COLUMNAS)+' FROM dim_comun.dim_fecha') if con.existe_tabla('dim_comun','dim_fecha') else pd.DataFrame()
    fechas = list(preparado.fecha) + list(actual.fecha) + (list(anterior_dim.fecha) if not anterior_dim.empty else [])
    calendario = generar(date(min(fechas).year,1,1),date(max(fechas).year,12,31))
    verificar_existentes(actual,calendario)
    verificar_existentes(preparado,calendario)
    verificar_existentes(anterior_dim,calendario)
    faltan = calendario[~calendario.sk_fecha.isin(actual.sk_fecha)]
    con.copiar_df('comun.dim_fecha',faltan)
    # Sin CASCADE: una dependencia no prevista debe bloquear la transición.
    con.execute('''DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='dim_comun' AND c.relname='dim_fecha' AND c.relkind='r') THEN
        DROP TABLE dim_comun.dim_fecha;
      END IF;
    END $$;''')
    con.execute('CREATE OR REPLACE VIEW dim_comun.dim_fecha AS SELECT '+','.join(COLUMNAS)+' FROM comun.dim_fecha')
    leido = con.df('SELECT '+','.join(TODAS)+' FROM comun.dim_fecha')
    verificar_existentes(leido,calendario)
    if len(leido) != len(calendario):
        raise RuntimeError('Calendario incompleto después de carga')
    return dict(fechas=len(calendario),agregadas=len(faltan),inicio=str(calendario.fecha.min()),fin=str(calendario.fecha.max()))


def verificar_referencias(con):
    from psycopg import sql
    columnas = con.df("""SELECT c.table_schema,c.table_name,c.column_name
      FROM information_schema.columns c JOIN information_schema.tables t
      ON t.table_schema=c.table_schema AND t.table_name=c.table_name
      WHERE t.table_type='BASE TABLE' AND c.table_schema IN
      ('ventas','ciclos','peajes','combustible','costos','tarificador','escenarios','taller')
      AND (c.column_name='sk_fecha' OR c.column_name LIKE 'sk_fecha_%')""")
    controles = {}
    for esquema,tabla,columna in columnas.itertuples(index=False,name=None):
        q=sql.SQL('SELECT count(*) FROM {}.{} f LEFT JOIN comun.dim_fecha d ON d.sk_fecha=f.{} WHERE f.{} IS NOT NULL AND d.sk_fecha IS NULL').format(
            sql.Identifier(esquema),sql.Identifier(tabla),sql.Identifier(columna),sql.Identifier(columna))
        controles[f'{esquema}.{tabla}.{columna}'] = int(con.uno(q)[0])
    if any(controles.values()):
        raise RuntimeError(f'Fechas de hechos fuera del calendario: {controles}')
    return controles
