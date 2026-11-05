"""Referencia histórica auditable del escenario 2, exclusivamente desde P7.

La fecha documental se interpreta al inicio del día, como en la regla anterior.
La excepción del primer bloque posterior solo se admite desde 2025 y se marca.
"""
VERSION='2026-11-03-e2-certificado-v1'
SQL='''
CREATE TABLE IF NOT EXISTS combustible.parametro_rendimiento_tarificador (
 parametro text,valor integer NOT NULL CHECK(valor>0),vigente_desde date,vigente_hasta date NOT NULL,
 PRIMARY KEY(parametro,vigente_desde),CHECK(vigente_desde<vigente_hasta));
INSERT INTO combustible.parametro_rendimiento_tarificador
 SELECT 'bloques_recientes',5,'1900-01-01','9999-12-31'
 WHERE NOT EXISTS(SELECT 1 FROM combustible.parametro_rendimiento_tarificador WHERE parametro='bloques_recientes');
CREATE INDEX IF NOT EXISTS ix_certificado_equipo_cierre
 ON combustible.fact_bloque_certificacion(equipo_id,t_fin,bloque) WHERE certificado;
CREATE OR REPLACE VIEW combustible.v_rendimiento_tarificador_certificado AS
WITH v AS (SELECT nro_viaje,equipo_id,sk_fecha,
 to_date(sk_fecha::text,'YYYYMMDD') AS fecha FROM ventas.fact_viaje_venta),
x AS (SELECT v.*,p.valor AS ventana_bloques,
 h.rendimiento AS rendimiento_previo,h.n AS bloques_previos,h.ultimo AS fecha_ultimo_previo,
 h.primero AS fecha_primer_previo,h.ids AS bloques_previos_ids,
 f.bloque AS primer_bloque_certificado,f.t_fin AS fecha_primer_certificado,f.rend_cert AS rendimiento_primer_certificado
 FROM v LEFT JOIN combustible.parametro_rendimiento_tarificador p
 ON p.parametro='bloques_recientes' AND v.fecha>=p.vigente_desde AND v.fecha<p.vigente_hasta
 LEFT JOIN LATERAL (
 SELECT percentile_cont(.5) WITHIN GROUP(ORDER BY b.rend_cert) AS rendimiento,
 count(*)::int AS n,max(b.t_fin) AS ultimo,min(b.t_fin) AS primero,
 array_agg(b.bloque ORDER BY b.t_fin DESC,b.bloque DESC) AS ids
 FROM (SELECT b.bloque,b.t_fin,b.rend_cert FROM combustible.fact_bloque_certificacion b
   WHERE b.equipo_id=v.equipo_id AND b.certificado AND b.t_fin<v.fecha
   AND p.valor IS NOT NULL ORDER BY b.t_fin DESC,b.bloque DESC LIMIT p.valor) b
 ) h ON TRUE
 LEFT JOIN LATERAL (
 SELECT b.bloque,b.t_fin,b.rend_cert FROM combustible.fact_bloque_certificacion b
 WHERE b.equipo_id=v.equipo_id AND b.certificado
 ORDER BY b.t_fin,b.bloque LIMIT 1
 ) f ON h.n=0),
y AS (SELECT *,coalesce(bloques_previos=0 AND fecha>=DATE '2025-01-01'
 AND fecha<fecha_primer_certificado AND ventana_bloques IS NOT NULL,false) AS rendimiento_supuesto FROM x)
SELECT nro_viaje,equipo_id,sk_fecha AS fecha_documental_id,ventana_bloques,
 CASE WHEN bloques_previos>0 THEN rendimiento_previo WHEN rendimiento_supuesto THEN rendimiento_primer_certificado END AS rendimiento_camion,
 CASE WHEN bloques_previos>0 THEN bloques_previos WHEN rendimiento_supuesto THEN 1 ELSE 0 END AS bloques_utilizados,
 CASE WHEN bloques_previos>0 THEN fecha_ultimo_previo WHEN rendimiento_supuesto THEN fecha_primer_certificado END AS fecha_ultimo_bloque,
 CASE WHEN bloques_previos>0 THEN fecha_primer_previo WHEN rendimiento_supuesto THEN fecha_primer_certificado END AS fecha_primer_bloque,
 CASE WHEN bloques_previos>0 THEN bloques_previos_ids WHEN rendimiento_supuesto THEN ARRAY[primer_bloque_certificado] ELSE ARRAY[]::bigint[] END AS bloques_utilizados_ids,
 rendimiento_supuesto,
 CASE WHEN bloques_previos>0 THEN 'mediana de bloques certificados anteriores'
 WHEN rendimiento_supuesto THEN 'primer bloque certificado posterior; supuesto desde 2025'
 ELSE 'sin historia certificada suficiente' END AS origen_rendimiento,
 rendimiento_previo,bloques_previos,fecha_ultimo_previo
FROM y;
'''


def publicar(con):
    if not con.existe_tabla('combustible','fact_bloque_certificacion'):
        raise RuntimeError('Primero publicar combustible certificado P7')
    con.execute(SQL)
    con.execute('CREATE SCHEMA IF NOT EXISTS ds_gestion')
    solapados=con.uno('''SELECT count(*) FROM combustible.parametro_rendimiento_tarificador a
      JOIN combustible.parametro_rendimiento_tarificador b ON a.parametro=b.parametro
      AND a.vigente_desde<b.vigente_desde AND a.vigente_hasta>b.vigente_desde''')[0]
    if solapados:raise ValueError('Ventanas de rendimiento con vigencias solapadas')
    con.execute('''CREATE OR REPLACE VIEW ds_gestion.v_rendimiento_tarificador_certificado AS
      SELECT * FROM combustible.v_rendimiento_tarificador_certificado''')
