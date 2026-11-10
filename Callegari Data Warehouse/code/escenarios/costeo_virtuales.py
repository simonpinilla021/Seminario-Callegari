"""Costeo adicional conservador de virtuales; reutiliza las reglas del método propuesto.

Contexto inferido retrospectivamente por vecinos concordantes. No adivina rampla,
no usa el maestro actual como propiedad histórica y no convierte faltantes en cero.
"""
import numpy as np
import pandas as pd

from comun.calidad_tramos_virtuales import estado_tramo_virtual


def _parametro(nombre,metodo,ambito):
    # Si las vigencias se solapan PostgreSQL rechaza la subconsulta: no elegir arbitrariamente.
    return f"""(SELECT valor FROM costos.parametro_costo p WHERE p.parametro='{nombre}'
      AND p.metodo='{metodo}' AND p.ambito={ambito}
      AND to_date(x.sk_fecha::text,'YYYYMMDD')>=p.vigente_desde
      AND to_date(x.sk_fecha::text,'YYYYMMDD')<p.vigente_hasta)"""


AMB="CASE WHEN cp.es_tractoreo='S' THEN 'tractoreo' ELSE 'todos' END"
CONSULTA_CONTEXTO=f"""
WITH cadena AS (
  SELECT id_viaje,equipo_id,orden_procesado,
    max(orden_procesado) FILTER (WHERE es_virtual='N') OVER (
      PARTITION BY equipo_id ORDER BY orden_procesado ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS anterior,
    min(orden_procesado) FILTER (WHERE es_virtual='N') OVER (
      PARTITION BY equipo_id ORDER BY orden_procesado ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS siguiente
  FROM ciclos.fact_viaje_ciclo),
x AS (SELECT cy.*,c.anterior,c.siguiente FROM ciclos.fact_viaje_ciclo cy
      JOIN cadena c USING(id_viaje) WHERE cy.es_virtual='S')
SELECT x.id_viaje,an.nro_viaje AS anterior_nro,si.nro_viaje AS siguiente_nro,
  an.sk_fecha AS anterior_fecha,si.sk_fecha AS siguiente_fecha,
  cp.es_flota_propia AS anterior_propio,cn.es_flota_propia AS siguiente_propio,
  cp.es_tractoreo AS anterior_tractoreo,cn.es_tractoreo AS siguiente_tractoreo,
  cp.rampla_id AS anterior_rampla,cn.rampla_id AS siguiente_rampla,
  cp.usa_rampla_callegari AS anterior_rampla_propia,cn.usa_rampla_callegari AS siguiente_rampla_propia,
  {_parametro('km_por_dia','tarificador',"'todos'")} AS km_dia_virtual,
  {_parametro('cuota_diaria','propuesto',"'flota'")} AS cuota_flota,
  {_parametro('cuota_diaria','propuesto',"'rampla'")} AS cuota_rampla,
  {_parametro('cuota_diaria','propuesto',"'estructura'")} AS cuota_estructura,
  {_parametro('reposicion_diaria','tarificador',AMB)} AS reposicion_diaria,
  {_parametro('costo_neumatico_km','tarificador',AMB)} AS neumatico_km,
  pe.alternativas_peaje,pe.peaje_ref_virtual,pe.estado_peaje_virtual,pe.estado_deteccion_virtual,
  pe.sk_tramo_peaje
FROM x
LEFT JOIN ciclos.fact_viaje_ciclo an ON an.equipo_id=x.equipo_id AND an.orden_procesado=x.anterior
LEFT JOIN ciclos.fact_viaje_ciclo si ON si.equipo_id=x.equipo_id AND si.orden_procesado=x.siguiente
LEFT JOIN costos.fact_viaje_costo_indirecto cp ON cp.nro_viaje=an.nro_viaje AND cp.metodo='propuesto'
LEFT JOIN costos.fact_viaje_costo_indirecto cn ON cn.nro_viaje=si.nro_viaje AND cn.metodo='propuesto'
LEFT JOIN LATERAL (
  SELECT count(*) AS alternativas_peaje,max(t.sk_tramo) AS sk_tramo_peaje,
    max(r.peaje_base) AS peaje_ref_virtual,max(r.estado) AS estado_peaje_virtual,
    max(t.estado_deteccion) AS estado_deteccion_virtual
  FROM peajes.dim_tramo t LEFT JOIN peajes.fact_peaje_referencial_tramo r ON r.sk_tramo=t.sk_tramo
    AND to_date(x.sk_fecha::text,'YYYYMMDD') BETWEEN r.vigente_desde AND r.vigente_hasta
  WHERE t.sit_stretch_id=x.sit_stretch_id
    AND to_date(x.sk_fecha::text,'YYYYMMDD') BETWEEN t.vigente_desde AND t.vigente_hasta
) pe ON TRUE
"""


def evaluar_contexto(vt):
    def col(n):
        return vt.get(n,pd.Series('',index=vt.index,dtype='object')).fillna('')
    def num(n):
        return pd.to_numeric(col(n),errors='coerce').astype(float)
    fechas=num('anterior_fecha').le(num('sk_fecha')) & num('siguiente_fecha').ge(num('sk_fecha'))
    propios=col('anterior_propio').eq('S') & col('siguiente_propio').eq('S')
    tractoreo=col('anterior_tractoreo').eq('S') & col('siguiente_tractoreo').eq('S')
    rampla=(col('anterior_tractoreo').eq('N') & col('siguiente_tractoreo').eq('N')
      & num('anterior_rampla').notna() & num('anterior_rampla').eq(num('siguiente_rampla'))
      & col('anterior_rampla_propia').isin(['S','N'])
      & col('anterior_rampla_propia').eq(col('siguiente_rampla_propia')))
    return pd.Series(np.select([
        num('anterior_nro').isna() | num('siguiente_nro').isna(),
        ~fechas.fillna(False),~propios.fillna(False),~(tractoreo|rampla).fillna(False)],
        ['sin ambos viajes reales vecinos','cronologia no concordante','operador propio no concordante',
         'configuracion de rampla o tractoreo no concordante'],default='propio concordante inferido'),index=vt.index)


def componentes_adicionales(vt, *, permitir_parcial=False):
    """Una fila por virtual con montos candidatos admisibles y motivos por componente."""
    x=vt.copy()
    def num(n):
        return pd.to_numeric(x.get(n,pd.Series(np.nan,index=x.index)),errors='coerce').astype(float)
    def col(n):
        return x.get(n,pd.Series('',index=x.index)).fillna('')
    x['estado_contexto']=evaluar_contexto(x)
    valido=(estado_tramo_virtual(x).eq('disponible') & x.estado_contexto.eq('propio concordante inferido'))
    km=num('km')
    dias=km/num('km_dia_virtual').where(num('km_dia_virtual')>0)
    usa_rampla=col('anterior_tractoreo').eq('N') & col('anterior_rampla_propia').eq('S')
    x['dias_modelados']=dias.where(valido)
    for bolsa in ('flota','estructura','rampla'):
        monto=dias*num(f'cuota_{bolsa}')
        if bolsa=='rampla':
            monto=monto.where(usa_rampla,0.)
        x[f'cf_{bolsa}_virtual']=monto.where(valido)
    fijo=x[['cf_flota_virtual','cf_estructura_virtual','cf_rampla_virtual']].sum(axis=1,min_count=3)
    montos={'costo_fijo':fijo,'reposicion':dias*num('reposicion_diaria'),
            'neumaticos':km*num('neumatico_km')}
    tasas={'costo_fijo':fijo/dias.where(dias>0),'reposicion':num('reposicion_diaria'),
           'neumaticos':num('neumatico_km')}
    for n in montos:
        x[f'monto_{n}']=montos[n].where(valido & np.isfinite(montos[n]))
        x[f'cantidad_{n}']=(km if n=='neumaticos' else dias).where(valido)
        x[f'tasa_{n}']=tasas[n].where(valido)
        x[f'estado_{n}']=np.select([
            estado_tramo_virtual(x).ne('disponible'),~valido,x[f'monto_{n}'].isna()],
            ['sin dato: tramo pendiente de revision','sin dato: contexto operativo no concordante',
             'sin dato: parametros vigentes incompletos'],default='ok')
    peaje=num('peaje_ref_virtual')
    peaje_ok=(num('alternativas_peaje').eq(1) & col('estado_deteccion_virtual').eq('completo')
              & col('estado_peaje_virtual').isin(['completo','sin plazas']) & peaje.notna())
    x['monto_peaje']=peaje.where(valido & peaje_ok)
    x['cantidad_peaje']=pd.Series(1.,index=x.index).where(valido & peaje_ok)
    x['tasa_peaje']=x.monto_peaje
    x['estado_peaje']=np.select([estado_tramo_virtual(x).ne('disponible'),~valido,~peaje_ok],
        ['sin dato: tramo pendiente de revision','sin dato: contexto operativo no concordante',
         'sin dato: peaje sin referencia completa a la fecha'],default='ok')
    if permitir_parcial:
        # La categoría coincide entre vecinos propios. La identidad o propiedad
        # de la rampla sigue sin confirmarse y su cuota queda en blanco.
        parcial = (
            estado_tramo_virtual(x).eq('disponible')
            & x.estado_contexto.eq('configuracion de rampla o tractoreo no concordante')
            & col('anterior_tractoreo').eq('N') & col('siguiente_tractoreo').eq('N')
        )
        bolsas = ['cf_flota_virtual', 'cf_estructura_virtual']
        for bolsa in ('flota', 'estructura'):
            monto = dias * num(f'cuota_{bolsa}')
            x.loc[parcial, f'cf_{bolsa}_virtual'] = monto.where(np.isfinite(monto))
        subtotal = x[bolsas].sum(axis=1, min_count=1)
        fijo_parcial = parcial & subtotal.notna()
        x.loc[fijo_parcial, 'monto_costo_fijo'] = subtotal
        x.loc[fijo_parcial, 'cantidad_costo_fijo'] = dias
        x.loc[fijo_parcial, 'tasa_costo_fijo'] = subtotal / dias.where(dias > 0)
        x.loc[fijo_parcial, 'estado_costo_fijo'] = 'parcial: cuota de rampla sin confirmar'
        peaje_parcial = parcial & peaje_ok
        x.loc[peaje_parcial, ['monto_peaje', 'tasa_peaje']] = x.loc[
            peaje_parcial, ['peaje_ref_virtual', 'peaje_ref_virtual']
        ].to_numpy()
        x.loc[peaje_parcial, 'cantidad_peaje'] = 1.
        x.loc[peaje_parcial, 'estado_peaje'] = 'ok'
    return x


VISTAS_COSTEO="""
CREATE OR REPLACE VIEW escenarios.v_costo_virtual_resumen AS
SELECT id_viaje,sk_escenario,sk_fecha,ciclo_comercial,nro_viaje_destino,
       max(estado_asignacion) AS estado_asignacion,max(estado_tramo) AS estado_tramo,
       max(estado_contexto) AS estado_contexto,
       sum(monto) AS subtotal_conocido,
       bool_and(estado='ok' AND monto IS NOT NULL) AND count(*)=6 AS completo,
       CASE WHEN bool_and(estado='ok' AND monto IS NOT NULL) AND count(*)=6 THEN sum(monto) END AS costo_completo,
       CASE WHEN nro_viaje_destino IS NULL THEN 'conservado sin imputacion comercial'
            ELSE 'imputado al principal propio' END AS destino_del_costo
FROM escenarios.fact_costo_virtual GROUP BY 1,2,3,4,5;

CREATE OR REPLACE VIEW escenarios.v_capacidad_con_virtuales AS
WITH bolsas AS (
  SELECT sk_fecha/10000 AS anio,'flota' AS bolsa,cf_flota_virtual AS monto
  FROM escenarios.fact_costo_virtual WHERE sk_escenario=3 AND sk_componente=4 AND estado='ok'
  UNION ALL
  SELECT sk_fecha/10000,'rampla',cf_rampla_virtual FROM escenarios.fact_costo_virtual
  WHERE sk_escenario=3 AND sk_componente=4 AND estado='ok'
  UNION ALL
  SELECT sk_fecha/10000,'estructura',cf_estructura_virtual FROM escenarios.fact_costo_virtual
  WHERE sk_escenario=3 AND sk_componente=4 AND estado='ok'),
v AS (SELECT anio,bolsa,sum(monto) AS absorbido_virtual_conocido FROM bolsas GROUP BY 1,2)
SELECT c.*,coalesce(v.absorbido_virtual_conocido,0) AS absorbido_virtual_conocido,
       c.absorbido+coalesce(v.absorbido_virtual_conocido,0) AS absorbido_real_y_virtual_conocido,
       c.capacidad_ociosa-coalesce(v.absorbido_virtual_conocido,0) AS residual_tras_virtuales_conocidos,
       'solo virtuales con fijo completo; no equivale a capacidad ociosa validada' AS alcance_ajuste
FROM costos.fact_capacidad_bolsa c LEFT JOIN v ON v.anio=c.anio AND v.bolsa=c.bolsa;
"""
