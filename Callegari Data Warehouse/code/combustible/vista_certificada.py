"""Vista de consumo del combustible certificado P7."""

SQL = """
CREATE OR REPLACE VIEW ds_gestion.v_combustible_certificado AS
SELECT id_viaje_fuente AS movimiento_cadena_id,nro_viaje,equipo_id,sk_fecha_inicio AS fecha_documental_id,
 litros_asignados,litros_margen,ajuste_litros,estado_litros,fraccion_certificada,
 fuente_ficha,motivo_bloque,precio_litro_margen,costo_combustible AS costo_original,
 costo_combustible_margen,costo_ajuste
FROM combustible.fact_viaje_combustible;
"""

def publicar(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS ds_gestion")
    con.execute(SQL)
