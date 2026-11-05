"""Referencias contables observadas y cobertura; no reasigna costos ni equipos."""
import pandas as pd
VERSION='2026-10-30-cobertura-referencias-contables-v1'
NOMBRES=['v_cobertura_referencias_mensual','v_referencias_contabilidad',
         'v_ref_centro_contable','v_ref_unidad_negocio','v_reglas_centro_costo']

CONSULTA_CENTROS="""
WITH ids AS (
 SELECT c_activity_id FROM costos.dim_centro_costo
 UNION SELECT c_activity_id FROM costos.fact_contabilidad_mensual WHERE c_activity_id IS NOT NULL
)
SELECT i.c_activity_id,d.nombre,
 CASE WHEN d.c_activity_id IS NULL THEN 'observado_sin_catalogo'
      WHEN d.origen='maestro' THEN 'catalogo_erp'
      WHEN d.origen='manual' THEN 'documentado_manual'
      ELSE 'procedencia_no_identificada' END AS estado_descripcion,
 d.origen AS origen_descripcion
FROM ids i LEFT JOIN costos.dim_centro_costo d USING(c_activity_id)
"""

CONSULTA_UN="""
WITH ids AS (
 SELECT unidad_negocio_id FROM costos.fact_contabilidad_mensual WHERE unidad_negocio_id IS NOT NULL
 UNION SELECT unidad_negocio_id FROM dim_comun.dim_equipo WHERE unidad_negocio_id IS NOT NULL
), equipos AS (
 SELECT unidad_negocio_id,count(*) AS equipos_actuales,
 CASE WHEN count(*)=1 THEN min(equipo_id) END AS equipo_unico_actual
 FROM dim_comun.dim_equipo WHERE unidad_negocio_id IS NOT NULL GROUP BY 1
)
SELECT i.unidad_negocio_id,NULL::text AS nombre,
 'sin_catalogo_empresarial'::text AS estado_descripcion,
 coalesce(e.equipos_actuales,0) AS equipos_actuales,e.equipo_unico_actual,
 CASE WHEN e.equipos_actuales IS NULL THEN 'sin_equipo_en_maestro_actual'
      WHEN e.equipos_actuales=1 THEN 'un_equipo_en_maestro_actual'
      ELSE 'varios_equipos_en_maestro_actual' END AS estado_correspondencia
FROM ids i LEFT JOIN equipos e USING(unidad_negocio_id)
"""


def sql_vistas():
    return f"""
CREATE OR REPLACE VIEW costos.v_ref_centro_contable AS {CONSULTA_CENTROS};
CREATE OR REPLACE VIEW costos.v_ref_unidad_negocio AS {CONSULTA_UN};
CREATE OR REPLACE VIEW costos.v_reglas_centro_costo AS
SELECT c_activity_id,bolsa,rol FROM costos.dim_centro_costo
WHERE nullif(trim(bolsa),'') IS NOT NULL OR nullif(trim(rol),'') IS NOT NULL;
COMMENT ON VIEW costos.v_reglas_centro_costo IS 'Proyección de las reglas existentes. No es un catálogo empresarial ni una nueva fuente de asignación.';
COMMENT ON VIEW costos.v_ref_unidad_negocio IS 'IDs observados y correspondencia con fotografía actual de equipos. No acredita relación histórica ni identifica toda UN como vehículo.';
CREATE OR REPLACE VIEW costos.v_referencias_contabilidad AS
SELECT f.*,coalesce(c.estado_descripcion,'sin_id') AS estado_descripcion_centro,
 coalesce(u.estado_descripcion,'sin_id') AS estado_descripcion_un,
 coalesce(u.estado_correspondencia,'sin_un') AS correspondencia_un_actual,
 u.equipo_unico_actual AS equipo_candidato_actual,
 CASE WHEN f.unidad_negocio_id IS NULL THEN 'sin_un'
      WHEN f.equipo_id IS NOT NULL THEN 'equipo_asignado_en_hecho'
      WHEN coalesce(u.equipos_actuales,0)=0 THEN 'sin_correspondencia_maestro_actual'
      WHEN u.equipos_actuales>1 THEN 'correspondencia_multiple'
      ELSE 'equipo_unico_actual_no_asignado' END AS estado_asignacion_equipo
FROM costos.fact_contabilidad_mensual f
LEFT JOIN costos.v_ref_centro_contable c USING(c_activity_id)
LEFT JOIN costos.v_ref_unidad_negocio u USING(unidad_negocio_id);
CREATE OR REPLACE VIEW costos.v_cobertura_referencias_mensual AS
SELECT anio,mes,estado_descripcion_centro,estado_descripcion_un,estado_asignacion_equipo,
 count(*) AS grupos_contables,sum(n_lineas) AS lineas_fuente,sum(monto) AS monto_neto,
 sum(abs(monto)) AS suma_absoluta_montos_agrupados
FROM costos.v_referencias_contabilidad GROUP BY 1,2,3,4,5;
COMMENT ON VIEW costos.v_cobertura_referencias_mensual IS 'Montos bajo alcance contable del ETL: neto y absoluto de grupos; no equivale a movimientos brutos ni balance completo. Las referencias desconocidas no se eliminan.';
"""


def publicar(con):
    con.execute(sql_vistas())
    consultas={
      'centros_duplicados': 'SELECT count(*) FROM (SELECT c_activity_id FROM costos.v_ref_centro_contable GROUP BY 1 HAVING count(*)<>1 OR c_activity_id IS NULL) x',
      'un_duplicadas': 'SELECT count(*) FROM (SELECT unidad_negocio_id FROM costos.v_ref_unidad_negocio GROUP BY 1 HAVING count(*)<>1 OR unidad_negocio_id IS NULL) x',
      'centros_sin_referencia': 'SELECT count(*) FROM costos.fact_contabilidad_mensual f LEFT JOIN costos.v_ref_centro_contable c USING(c_activity_id) WHERE f.c_activity_id IS NOT NULL AND c.c_activity_id IS NULL',
      'un_sin_referencia': 'SELECT count(*) FROM costos.fact_contabilidad_mensual f LEFT JOIN costos.v_ref_unidad_negocio u USING(unidad_negocio_id) WHERE f.unidad_negocio_id IS NOT NULL AND u.unidad_negocio_id IS NULL',
      'grupos_no_conservados': 'SELECT abs((SELECT count(*) FROM costos.fact_contabilidad_mensual)-(SELECT count(*) FROM costos.v_referencias_contabilidad))',
      'resumen_grupos_no_conservados': 'SELECT abs((SELECT count(*) FROM costos.fact_contabilidad_mensual)-(SELECT coalesce(sum(grupos_contables),0) FROM costos.v_cobertura_referencias_mensual))',
      'resumen_lineas_no_conservadas': 'SELECT abs((SELECT coalesce(sum(n_lineas),0) FROM costos.fact_contabilidad_mensual)-(SELECT coalesce(sum(lineas_fuente),0) FROM costos.v_cobertura_referencias_mensual))',
      'resumen_monto_no_conservado': 'SELECT CASE WHEN abs((SELECT coalesce(sum(monto),0) FROM costos.fact_contabilidad_mensual)-(SELECT coalesce(sum(monto_neto),0) FROM costos.v_cobertura_referencias_mensual))>0.01 THEN 1 ELSE 0 END',
      'desconocidos_con_nombre_inventado': "SELECT count(*) FROM costos.v_ref_centro_contable WHERE estado_descripcion='observado_sin_catalogo' AND nombre IS NOT NULL",
      'un_multiple_con_equipo_elegido': 'SELECT count(*) FROM costos.v_ref_unidad_negocio WHERE equipos_actuales<>1 AND equipo_unico_actual IS NOT NULL',
    }
    controles={n:int(con.uno(q)[0]) for n,q in consultas.items()}
    if any(controles.values()): raise ValueError('Falló cobertura de referencias: '+str(controles))
    centros=int(con.uno('SELECT count(*) FROM costos.v_ref_centro_contable')[0])
    desconocidos=int(con.uno("SELECT count(*) FROM costos.v_ref_centro_contable WHERE estado_descripcion='observado_sin_catalogo'")[0])
    un=int(con.uno('SELECT count(*) FROM costos.v_ref_unidad_negocio')[0])
    return dict(version=VERSION,controles=controles,centros=centros,
                centros_observados_sin_catalogo=desconocidos,unidades_negocio=un)


def probar_semantica(con):
    # No hay INSERT ni tablas de prueba: dos CTE simulan datos de entrada.
    prefijo="""WITH centros_fixture(c_activity_id,nombre,origen) AS
      (VALUES (1,'Documentado','manual'),(2,'ERP','maestro')),
      contabilidad_fixture(c_activity_id) AS (VALUES(1),(3),(3),(NULL::integer)), """
    q=CONSULTA_CENTROS.replace('costos.dim_centro_costo','centros_fixture').replace('costos.fact_contabilidad_mensual','contabilidad_fixture')
    d=con.df(prefijo+q.lstrip()[5:]).set_index('c_activity_id')
    if set(d.index)!={1,2,3} or d.loc[3,'estado_descripcion']!='observado_sin_catalogo' or pd.notna(d.loc[3,'nombre']):
        raise ValueError('Prueba: centro ausente eliminado, duplicado o completado artificialmente')
    prefijo="""WITH contabilidad_fixture(unidad_negocio_id) AS (VALUES(10),(20),(30),(NULL::integer)),
      equipos_fixture(unidad_negocio_id,equipo_id) AS (VALUES(10,1),(20,2),(20,3),(40,4)), """
    q=CONSULTA_UN.replace('costos.fact_contabilidad_mensual','contabilidad_fixture').replace('dim_comun.dim_equipo','equipos_fixture')
    d=con.df(prefijo+q.lstrip()[5:]).set_index('unidad_negocio_id')
    if set(d.index)!={10,20,30,40} or d.loc[10,'equipo_unico_actual']!=1:
        raise ValueError('Prueba: cobertura de UN no conserva las referencias')
    if d.loc[[20,30],'equipo_unico_actual'].notna().any() or d.nombre.notna().any():
        raise ValueError('Prueba: UN múltiple/desconocida con asignación o nombre inventado')
    return dict(casos_centros=3,casos_unidades=4,correctos=True)
