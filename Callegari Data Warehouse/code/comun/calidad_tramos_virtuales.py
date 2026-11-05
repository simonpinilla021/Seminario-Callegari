"""Política: tramo pendiente visible para TI; estimaciones auxiliares fuera del costo oficial."""
import numpy as np
import pandas as pd


def estado_tramo_virtual(viajes):
    km = pd.to_numeric(viajes['km'],errors='coerce')
    def col(nombre):
        return viajes.get(nombre,pd.Series('',index=viajes.index)).fillna('')
    distintas = (col('ciudad_origen_limpia').ne('') & col('ciudad_destino_limpia').ne('')
                 & col('ciudad_origen_limpia').ne(col('ciudad_destino_limpia')))
    return pd.Series(np.select([
        col('tipo_virtual').eq('Virtual_Sin_Tramo'),km.isna(),km.lt(0),km.eq(0)&distintas],
        ['sin_tramo','distancia_ausente','distancia_invalida','distancia_no_resuelta'],
        default='disponible'),index=viajes.index)


ESTADO_SQL = """CASE
  WHEN cy.tipo_virtual = 'Virtual_Sin_Tramo' THEN 'sin_tramo'
  WHEN cy.km IS NULL THEN 'distancia_ausente'
  WHEN cy.km < 0 THEN 'distancia_invalida'
  WHEN cy.km = 0 AND coalesce(cy.ciudad_origen_limpia,'') <> ''
    AND coalesce(cy.ciudad_destino_limpia,'') <> ''
    AND cy.ciudad_origen_limpia <> cy.ciudad_destino_limpia THEN 'distancia_no_resuelta'
  ELSE 'disponible' END"""


VISTAS_CALIDAD = f"""
CREATE OR REPLACE VIEW escenarios.v_tramos_virtuales_pendientes AS
SELECT cy.id_viaje, cy.sk_fecha, cy.equipo_id, cy.ciclo_comercial,
       cy.ciudad_origen_limpia AS origen, cy.ciudad_destino_limpia AS destino,
       cy.tipo_virtual, cy.km AS km_registrados,
       {ESTADO_SQL} AS estado_tramo,
       TRUE AS requiere_revision_ti,
       CAST(NULL AS DOUBLE PRECISION) AS km_para_costeo,
       'pendiente_revision' AS estado_revision,
       'TI / maestro de tramos' AS responsable_sugerido,
       'Verificar extremos y crear o corregir tramo; recargar despues de validar' AS accion_requerida,
       'Propuestas calculadas fuera del costo oficial' AS politica_estimacion,
       dc.viaje_principal, vp.tipo_operador AS operador_principal,
       dc.estado AS estado_geografico_ciclo,
       dc.ingreso AS ingreso_ciclo_referencia
FROM ciclos.fact_viaje_ciclo cy
LEFT JOIN ciclos.dim_ciclo_comercial dc ON dc.ciclo_comercial=cy.ciclo_comercial
LEFT JOIN ciclos.fact_viaje_ciclo cp ON cp.id_viaje=dc.viaje_principal
LEFT JOIN ventas.fact_viaje_venta vp ON vp.nro_viaje=cp.nro_viaje
WHERE cy.es_virtual='S' AND ({ESTADO_SQL}) <> 'disponible';

CREATE OR REPLACE VIEW escenarios.v_tramos_virtuales_pendientes_resumen AS
SELECT origen,destino,estado_tramo,
       count(*) AS virtuales_afectados,
       count(DISTINCT equipo_id) AS equipos_afectados,
       count(DISTINCT ciclo_comercial) AS ciclos_afectados,
       min(sk_fecha) AS primera_fecha, max(sk_fecha) AS ultima_fecha
FROM escenarios.v_tramos_virtuales_pendientes
GROUP BY origen,destino,estado_tramo;
"""
