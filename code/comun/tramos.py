"""Identidad comercial actual y versiones técnicas conservadas en peajes.

Las vigencias de peajes son inclusivas. Sus km se actualizan SCD1: no son historia completa.
"""
import pandas as pd
from comun import db
from dimensiones.preparar_dimensiones_comunes import CONTRATOS, maestro

VERSION = '2026-10-30-tramo-fechas-iso-v2'
VISTA = '''CREATE OR REPLACE VIEW dim_comun.v_tramo_version_peaje AS
 SELECT p.sk_tramo,p.sit_stretch_id AS tramo_id,p.texto_tramo AS nombre_version_peaje,
 p.km_erp AS km_erp_peaje,p.km_ruta_calculada,p.vigente_desde,p.vigente_hasta,p.es_actual,
 m.nombre_fuente AS nombre_catalogo_actual,
 CASE WHEN m.tramo_id IS NULL THEN 'sin_maestro_actual' ELSE 'encontrado' END AS estado_maestro
 FROM peajes.dim_tramo p LEFT JOIN dim_comun.dim_tramo_comercial m ON m.tramo_id=p.sit_stretch_id'''


def preparar(ruta, extraccion, entradas):
    tabla, clave, columnas = CONTRATOS['traveltramo']
    d=maestro(entradas.lector(pd.read_parquet)(ruta),clave,columnas)
    d['km_fuente']=pd.to_numeric(d.km_fuente,errors='raise')
    d['extraccion_id']=extraccion
    d['fuente_sha256']=entradas.archivos[str(ruta.resolve())]['sha256']
    return d


def publicar(con):
    con.execute(VISTA)
    controles={
      'intervalos_invalidos': con.uno("SELECT count(*) FROM peajes.dim_tramo WHERE vigente_desde IS NULL OR vigente_hasta IS NULL OR vigente_hasta<vigente_desde")[0],
      'solapes_versiones': con.uno('''SELECT count(*) FROM peajes.dim_tramo a JOIN peajes.dim_tramo b
        ON a.sit_stretch_id=b.sit_stretch_id AND a.sk_tramo<b.sk_tramo
        AND a.vigente_desde<=b.vigente_hasta AND b.vigente_desde<=a.vigente_hasta''')[0],
      'actuales_duplicadas': con.uno("SELECT count(*) FROM (SELECT sit_stretch_id FROM peajes.dim_tramo WHERE es_actual='S' GROUP BY 1 HAVING count(*)>1) x")[0],
      'filas_multiplicadas': con.uno('SELECT (SELECT count(*) FROM dim_comun.v_tramo_version_peaje)-(SELECT count(*) FROM peajes.dim_tramo)')[0]}
    if any(controles.values()):
        raise RuntimeError(f'Contrato temporal inválido: {controles}')
    return controles


def cargar(con, d):
    con.execute('''CREATE SCHEMA IF NOT EXISTS dim_comun;
      CREATE TABLE IF NOT EXISTS dim_comun.dim_tramo_comercial (
        tramo_id BIGINT PRIMARY KEY,nombre_fuente TEXT,km_fuente DOUBLE PRECISION,
        activo_fuente TEXT,actualizado_fuente TEXT,extraccion_id TEXT,fuente_sha256 TEXT)''')
    con.execute('DELETE FROM dim_comun.dim_tramo_comercial')
    con.copiar_df('dim_comun.dim_tramo_comercial', d)
    leido=con.df('SELECT '+','.join(d.columns)+' FROM dim_comun.dim_tramo_comercial ORDER BY tramo_id')
    esperado=d.sort_values('tramo_id').reset_index(drop=True).copy()
    esperado['actualizado_fuente']=esperado.actualizado_fuente.astype('string')
    # COPY convierte timestamps fuente a texto PostgreSQL; normalizar su representación.
    for t in (esperado,leido):
        t['actualizado_fuente']=pd.to_datetime(t.actualizado_fuente,format='ISO8601',errors='raise')
    pd.testing.assert_frame_equal(esperado,leido,check_dtype=False,check_exact=False,rtol=0,atol=1e-10)
    return publicar(con)
