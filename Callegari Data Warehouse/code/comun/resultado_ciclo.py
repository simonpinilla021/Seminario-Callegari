"""Resultado financiero por ciclo/escenario y admisión por política, sin repetir importes.

Alcance: ciclos con principal propio; Santiago con cortes Coquimbo; sin corte temporal fijo.
"""
VERSION = '2026-11-02-ds-resultado-ciclo-v3-cobertura-e3'
CONSULTA = """WITH
alcance AS MATERIALIZED (
 SELECT d.ciclo_comercial, d.motivo_apertura, d.motivo_cierre,
        p.sk_fecha AS fecha_principal, p.cliente_id AS cliente_principal_id,
        p.sit_stretch_id AS tramo_principal_id,p.ciudad_destino_limpia AS destino_principal,
        eq.codigo AS equipo_codigo,cl.razon_social AS cliente_principal,tb.tramo AS tramo_principal
 FROM ciclos.dim_ciclo_comercial d
 JOIN ciclos.fact_viaje_ciclo p ON p.id_viaje=d.viaje_principal
 JOIN ventas.fact_viaje_venta v ON v.nro_viaje=p.nro_viaje
 LEFT JOIN taller.dim_equipo eq ON eq.equipo_id=p.equipo_id
 LEFT JOIN ventas.dim_cliente cl ON cl.cliente_id=p.cliente_id
 LEFT JOIN ciclos.dim_tramo_base tb ON tb.sit_stretch_id=p.sit_stretch_id
 WHERE v.tipo_operador='propio'
),
mov_base AS MATERIALIZED (
 SELECT cy.*,v.tipo_operador,v.ingreso AS ingreso_estimado,
        lag(cy.ciudad_destino_limpia) OVER(PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS destino_previo,
        lag(cy.base_destino) OVER(PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS base_previa,
        row_number() OVER(PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS posicion
 FROM ciclos.fact_viaje_ciclo cy JOIN alcance a USING(ciclo_comercial)
 LEFT JOIN ventas.fact_viaje_venta v ON v.nro_viaje=cy.nro_viaje
),
mov AS MATERIALIZED (
 SELECT m.*,coalesce(m.posicion=1 AND m.es_virtual='N' AND m.calidad_trayectoria='IG'
   AND previo.es_virtual='S' AND previo.ciclo_comercial<>m.ciclo_comercial
   AND previo.ciudad_destino_limpia=m.ciudad_origen_limpia,false) AS ig_heredada
 FROM mov_base m LEFT JOIN ciclos.fact_viaje_ciclo previo
   ON m.posicion=1 AND m.calidad_trayectoria='IG' AND previo.equipo_id=m.equipo_id
   AND previo.orden_procesado=m.orden_procesado-1
),
estructura AS MATERIALIZED (
 SELECT m.ciclo_comercial,
   count(*) FILTER(WHERE es_virtual='N') AS reales,
   count(*) FILTER(WHERE es_virtual='S') AS virtuales,
   bool_and(coalesce(tipo_operador='propio',false)) FILTER(WHERE es_virtual='N') AS propios,
   bool_and(coalesce(tipo_operador IN('propio','fletero','caterina'),false))
      FILTER(WHERE es_virtual='N') AS operadores_admitidos,
   bool_or(tipo_operador IN('fletero','caterina')) FILTER(WHERE es_virtual='N') AS contiene_terceros,
   bool_and(ingreso_estimado IS NOT NULL) FILTER(WHERE es_virtual='N') AS ingreso_completo,
   sum(ingreso_estimado) FILTER(WHERE es_virtual='N') AS ingreso,
   bool_and(ciudad_origen_limpia IS NOT NULL AND ciudad_destino_limpia IS NOT NULL
       AND (posicion=1 OR ciudad_origen_limpia=destino_previo)
       AND km IS NOT NULL AND km>=0 AND km<'Infinity'::float8
       AND (calidad_trayectoria IS DISTINCT FROM 'IG' OR ig_heredada)
       AND tipo_virtual IS DISTINCT FROM 'Virtual_Sin_Tramo'
       AND NOT(km=0 AND ciudad_origen_limpia IS DISTINCT FROM ciudad_destino_limpia)) AS trayectoria_valida,
   bool_and(ciudad_origen_limpia IS NOT NULL AND ciudad_destino_limpia IS NOT NULL
       AND (posicion=1 OR ciudad_origen_limpia=destino_previo OR (calidad_trayectoria='NB' AND base_origen=base_previa AND base_origen IS NOT NULL AND base_origen NOT IN('','NO APLICA')))
       AND km IS NOT NULL AND km>=0 AND km<'Infinity'::float8
       AND (calidad_trayectoria IS DISTINCT FROM 'IG' OR ig_heredada)
       AND tipo_virtual IS DISTINCT FROM 'Virtual_Sin_Tramo'
       AND NOT(km=0 AND ciudad_origen_limpia IS DISTINCT FROM ciudad_destino_limpia)) AS trayectoria_flexible,
   bool_or(calidad_trayectoria='IG' AND NOT ig_heredada) AS tiene_ig,
   bool_or(ig_heredada) AS ig_heredada,
   bool_or(km=0 AND ciudad_origen_limpia IS DISTINCT FROM ciudad_destino_limpia) AS km_cero,
   bool_or(tipo_virtual='Virtual_Sin_Tramo') AS virtual_sin_tramo,
   count(*) FILTER(WHERE posicion>1 AND ciudad_origen_limpia IS DISTINCT FROM destino_previo) AS saltos,
   string_agg(ciudad_origen_limpia || ' → ' || ciudad_destino_limpia,' | ' ORDER BY orden_ciclo_comercial) AS recorrido_piernas,
   string_agg(CASE WHEN posicion>1 AND ciudad_origen_limpia IS DISTINCT FROM destino_previo THEN destino_previo || ' → ' || ciudad_origen_limpia || CASE WHEN calidad_trayectoria='NB' AND base_origen=base_previa THEN ' (conexión por base NB; distancia local no determinada)' ELSE ' (conexión por revisar)' END END,'; ' ORDER BY orden_ciclo_comercial) AS conexiones,
   (array_agg(ciudad_origen_limpia ORDER BY orden_ciclo_comercial))[1] || ' → ' ||
       string_agg(ciudad_destino_limpia,' → ' ORDER BY orden_ciclo_comercial) AS recorrido_completo,
   json_agg(json_build_array(ciudad_origen_limpia,ciudad_destino_limpia,sit_stretch_id,km,es_virtual)
       ORDER BY orden_ciclo_comercial)::text AS patron_detalle
 FROM mov m GROUP BY m.ciclo_comercial
),
contexto AS MATERIALIZED (
 SELECT a.*,e.reales,e.virtuales,e.propios,e.operadores_admitidos,e.contiene_terceros,e.ig_heredada,e.ingreso_completo,e.ingreso,e.recorrido_completo,
   md5(e.patron_detalle) AS patron_recorrido,
   e.trayectoria_flexible AND ((a.motivo_apertura IN('sale de Santiago','corte en Coquimbo') AND a.motivo_cierre IN('llega a Santiago','corte en Coquimbo'))
      OR (a.motivo_apertura='local en Santiago' AND a.motivo_cierre='local')) AS estructura_flexible,
   ((a.motivo_apertura IN('sale de Santiago','corte en Coquimbo') AND a.motivo_cierre IN('llega a Santiago','corte en Coquimbo'))
      OR (a.motivo_apertura='local en Santiago' AND a.motivo_cierre='local')) AS cierre_valido,
   e.tiene_ig,e.km_cero,e.virtual_sin_tramo,e.saltos,e.recorrido_piernas,e.conexiones,
   e.trayectoria_valida AND
     ((a.motivo_apertura IN('sale de Santiago','corte en Coquimbo') AND a.motivo_cierre IN('llega a Santiago','corte en Coquimbo'))
      OR (a.motivo_apertura='local en Santiago' AND a.motivo_cierre='local')) AS estructura_valida,
   CASE WHEN a.motivo_apertura='local en Santiago' AND a.motivo_cierre='local' THEN 'local Santiago'
        WHEN 'corte en Coquimbo' IN(a.motivo_apertura,a.motivo_cierre) THEN 'con corte Coquimbo'
        WHEN a.motivo_apertura IN('sale de Santiago','corte en Coquimbo') AND a.motivo_cierre IN('llega a Santiago','corte en Coquimbo') THEN 'Santiago-Santiago'
        ELSE 'abierto o inicio incompleto' END AS tipo_segmento
 FROM alcance a JOIN estructura e USING(ciclo_comercial)
),
real_det AS MATERIALIZED (
 SELECT f.*,m.ciclo_comercial AS ciclo_seleccionado
 FROM escenarios.fact_costo_viaje f JOIN mov m ON m.nro_viaje=f.nro_viaje AND m.es_virtual='N'
 WHERE f.sk_escenario IN(1,2,3)
),
virtual_det AS MATERIALIZED (
 SELECT f.* FROM escenarios.fact_costo_virtual f JOIN alcance a USING(ciclo_comercial)
 WHERE f.sk_escenario =3
),
virtual_estado AS MATERIALIZED (
 SELECT v.id_viaje,v.ciclo_comercial,v.sk_escenario,
   count(DISTINCT v.sk_componente)=6
   AND bool_and(coalesce(v.estado='ok',false) AND v.monto IS NOT NULL AND destino.id_viaje IS NOT NULL) AS completo,
   count(DISTINCT v.sk_componente)=6 AND bool_and(coalesce(v.estado='ok',false) AND v.monto IS NOT NULL) AS completo_fisico,
   sum(v.monto) AS costo_fisico
 FROM virtual_det v LEFT JOIN mov destino ON destino.es_virtual='N'
     AND destino.ciclo_comercial=v.ciclo_comercial AND destino.nro_viaje=v.nro_viaje_destino
 GROUP BY 1,2,3
),
costos_comerciales AS MATERIALIZED (
 SELECT nro_viaje,sk_escenario,monto_comercial AS monto,estado_comercial AS estado FROM real_det
 UNION ALL
 SELECT f.nro_viaje_destino,f.sk_escenario,f.monto,f.estado
 FROM escenarios.fact_costo_virtual f
 WHERE f.sk_escenario =3 AND EXISTS(SELECT 1 FROM mov m WHERE m.es_virtual='N' AND m.nro_viaje=f.nro_viaje_destino)
),
costos_real AS MATERIALIZED (
 SELECT nro_viaje,sk_escenario,bool_and(coalesce(estado='ok',false) AND monto IS NOT NULL) AS completo_fisico,
   sum(monto) AS costo_fisico FROM real_det GROUP BY 1,2
),
costos_comercial AS MATERIALIZED (
 SELECT nro_viaje,sk_escenario,bool_and(coalesce(estado='ok',false) AND monto IS NOT NULL) AS completo_comercial,
   sum(monto) AS costo_comercial FROM costos_comerciales GROUP BY 1,2
),
viajes AS MATERIALIZED (
 SELECT m.nro_viaje,m.ciclo_comercial,f.sk_escenario,
   CASE WHEN f.completo_fisico THEN m.ingreso_estimado-f.costo_fisico END AS margen_fisico,
   CASE WHEN c.completo_comercial AND (f.sk_escenario NOT IN(1,2) OR ctx.virtuales=0)
        THEN m.ingreso_estimado-c.costo_comercial END AS margen_comercial,
   c.costo_comercial,c.completo_comercial
 FROM mov m JOIN costos_real f ON f.nro_viaje=m.nro_viaje
 JOIN costos_comercial c ON c.nro_viaje=m.nro_viaje AND c.sk_escenario=f.sk_escenario
 JOIN contexto ctx ON ctx.ciclo_comercial=m.ciclo_comercial
 WHERE m.es_virtual='N'
),
viajes_ciclo AS MATERIALIZED (
 SELECT ciclo_comercial,sk_escenario,count(*) AS reales_con_costo,
   bool_and(coalesce(completo_comercial,false) AND costo_comercial IS NOT NULL) AS costos_completos,
   sum(costo_comercial) AS costo FROM viajes GROUP BY 1,2
),
virtual_ciclo AS MATERIALIZED (
 SELECT ciclo_comercial,sk_escenario,count(*) AS virtuales_con_costo,
   bool_and(completo) AS virtuales_completos FROM virtual_estado GROUP BY 1,2
),
ciclos_evaluados AS MATERIALIZED (
 SELECT ctx.*,s.sk_escenario,
   coalesce(ctx.estructura_valida AND (ctx.propios OR (s.sk_escenario=3 AND ctx.operadores_admitidos)) AND ctx.ingreso_completo AND ctx.reales>0
     AND (s.sk_escenario NOT IN(1,2) OR ctx.virtuales=0)
     AND v.reales_con_costo=ctx.reales AND v.costos_completos
     AND (ctx.virtuales=0 OR (
       ve.virtuales_con_costo=ctx.virtuales AND ve.virtuales_completos)),false) AS completo,
   coalesce(ctx.estructura_flexible AND (ctx.propios OR (s.sk_escenario=3 AND ctx.operadores_admitidos)) AND ctx.ingreso_completo AND ctx.reales>0
     AND (s.sk_escenario NOT IN(1,2) OR ctx.virtuales=0)
     AND v.reales_con_costo=ctx.reales AND v.costos_completos
     AND (ctx.virtuales=0 OR (
       ve.virtuales_con_costo=ctx.virtuales AND ve.virtuales_completos)),false) AS completo_flexible,
   v.costo
 FROM contexto ctx CROSS JOIN escenarios.dim_escenario s
 LEFT JOIN viajes_ciclo v ON v.ciclo_comercial=ctx.ciclo_comercial AND v.sk_escenario=s.sk_escenario
 LEFT JOIN virtual_ciclo ve ON ve.ciclo_comercial=ctx.ciclo_comercial AND ve.sk_escenario=s.sk_escenario
 WHERE s.sk_escenario IN(1,2,3)
)
, fisico_real AS MATERIALIZED (
 SELECT m.ciclo_comercial,r.escenario_id,count(*) AS reales,
 bool_and(r.evaluable_fisico) AS completo,sum(r.subtotal_conocido_fisico) AS conocido
 FROM mov m JOIN ds_gestion.v_resultado_servicio r ON r.nro_viaje=m.nro_viaje
 WHERE m.es_virtual='N' GROUP BY 1,2
), fisico_virtual AS MATERIALIZED (
 SELECT ciclo_comercial,sk_escenario,count(*) AS virtuales,bool_and(completo_fisico) AS completo,
 sum(costo_fisico) AS conocido FROM virtual_estado GROUP BY 1,2
), financiero AS MATERIALIZED (
 SELECT c.*,v.reales_con_costo,v.costos_completos,
 vc.virtuales_con_costo,vc.virtuales_completos,
 coalesce((c.propios OR (c.sk_escenario=3 AND c.operadores_admitidos)) AND c.ingreso_completo AND c.reales>0
   AND (c.sk_escenario NOT IN(1,2) OR c.virtuales=0)
   AND v.reales_con_costo=c.reales AND v.costos_completos
   AND (c.virtuales=0 OR (vc.virtuales_con_costo=c.virtuales AND vc.virtuales_completos)),false) AS financiero_comercial_completo,
 coalesce((c.propios OR (c.sk_escenario=3 AND c.operadores_admitidos)) AND c.ingreso_completo AND c.reales>0
   AND (c.sk_escenario NOT IN(1,2) OR c.virtuales=0)
   AND fr.reales=c.reales AND fr.completo
   AND (c.virtuales=0 OR (fv.virtuales=c.virtuales AND fv.completo)),false) AS financiero_fisico_completo,
 CASE WHEN fr.conocido IS NOT NULL OR fv.conocido IS NOT NULL
   THEN coalesce(fr.conocido,0)+coalesce(fv.conocido,0) END AS conocido_fisico
 FROM ciclos_evaluados c
 LEFT JOIN viajes_ciclo v ON v.ciclo_comercial=c.ciclo_comercial AND v.sk_escenario=c.sk_escenario
 LEFT JOIN virtual_ciclo vc ON vc.ciclo_comercial=c.ciclo_comercial AND vc.sk_escenario=c.sk_escenario
 LEFT JOIN fisico_real fr ON fr.ciclo_comercial=c.ciclo_comercial AND fr.escenario_id=c.sk_escenario
 LEFT JOIN fisico_virtual fv ON fv.ciclo_comercial=c.ciclo_comercial AND fv.sk_escenario=c.sk_escenario
)
SELECT ciclo_comercial AS ciclo_id,sk_escenario AS escenario_id,fecha_principal AS fecha_documental_principal_id,
 CASE WHEN ingreso_completo THEN ingreso END AS ingreso_estimado,
 costo AS subtotal_conocido_comercial,conocido_fisico AS subtotal_conocido_fisico,
 CASE WHEN financiero_comercial_completo THEN costo END AS costo_comercial,
 CASE WHEN financiero_comercial_completo THEN ingreso-costo END AS margen_comercial,
 CASE WHEN financiero_fisico_completo THEN conocido_fisico END AS costo_fisico,
 CASE WHEN financiero_fisico_completo THEN ingreso-conocido_fisico END AS margen_fisico,
 financiero_comercial_completo,financiero_fisico_completo,
 reales,virtuales,propios,ingreso_completo,(estructura_valida AND (sk_escenario=3 OR NOT ig_heredada)) AS estructura_conservadora,
 (estructura_flexible AND (sk_escenario=3 OR NOT ig_heredada)) AS estructura_flexible,
 cierre_valido,(tiene_ig OR (sk_escenario<>3 AND ig_heredada)) AS tiene_ig,km_cero,virtual_sin_tramo,saltos,
 coalesce(reales_con_costo=reales AND costos_completos,false) AS costos_reales_completos,
 coalesce(virtuales=0 OR (virtuales_con_costo=virtuales AND virtuales_completos),false) AS costos_virtuales_completos,
 (sk_escenario NOT IN(1,2) OR virtuales=0) AS metodo_virtual_disponible,
 CASE WHEN estructura_valida AND (sk_escenario=3 OR NOT ig_heredada) THEN 'Sin bloqueo estructural'
 WHEN estructura_flexible AND (sk_escenario=3 OR NOT ig_heredada) THEN 'Solo conexión por base NB'
 WHEN tiene_ig OR (sk_escenario<>3 AND ig_heredada) THEN 'Inconsistencia geográfica IG'
 WHEN sk_escenario=3 AND NOT coalesce(motivo_apertura IN('sale de Santiago','corte en Coquimbo','local en Santiago'),false)
   THEN 'Apertura no admitida'
 WHEN NOT cierre_valido THEN CASE WHEN sk_escenario=3 THEN 'Cierre no admitido' ELSE 'Apertura o cierre no admitidos' END
 ELSE 'Otros problemas de distancia o continuidad' END AS submotivo_estructura,
 concat_ws('; ',CASE WHEN saltos>0 THEN 'Conexión entre ciudades distintas' END,
 CASE WHEN tiene_ig THEN 'Marca IG' END,CASE WHEN NOT cierre_valido THEN 'Apertura/cierre no admitidos' END,
 CASE WHEN km_cero THEN 'Km cero entre ciudades distintas' END,CASE WHEN virtual_sin_tramo THEN 'Virtual sin tramo' END) AS condiciones_estructura,
 recorrido_piernas AS recorrido_movimientos,
 coalesce(conexiones,'Sin salto entre ciudades registrado; establecimientos no identificados') AS conexiones,
 CASE WHEN propios THEN 'Solo TC' WHEN operadores_admitidos AND contiene_terceros THEN 'Mixto'
      ELSE 'Operador sin clasificar' END AS tipo_operacion_ciclo,
 (sk_escenario=3 AND ig_heredada) AS ig_heredada_admitida
FROM financiero"""
SQL_RESULTADO = 'CREATE OR REPLACE VIEW ds_gestion.v_resultado_ciclo AS ' + CONSULTA

SQL_POLITICA = """
CREATE OR REPLACE VIEW ds_gestion.v_politica_cobertura AS
SELECT 'flexible_base'::text AS politica_id,'Flexible por base'::text AS nombre,1 AS version_regla;
CREATE OR REPLACE VIEW ds_gestion.v_evaluacion_ciclo AS
SELECT r.ciclo_id,r.escenario_id,p.politica_id,p.nombre AS politica,p.version_regla,
 coalesce(r.financiero_comercial_completo AND
 CASE WHEN p.politica_id='conservador' THEN r.estructura_conservadora ELSE r.estructura_flexible END,false) AS evaluable,
 CASE WHEN coalesce(r.financiero_comercial_completo AND
 CASE WHEN p.politica_id='conservador' THEN r.estructura_conservadora ELSE r.estructura_flexible END,false) THEN 'Evaluable'
 WHEN NOT coalesce(CASE WHEN p.politica_id='conservador' THEN r.estructura_conservadora ELSE r.estructura_flexible END,false) THEN 'Estructura o trayectoria no evaluable'
 WHEN NOT coalesce(r.propios OR (r.escenario_id=3 AND r.tipo_operacion_ciclo='Mixto'),false) THEN 'Alcance de operadores no equivalente'
 WHEN NOT coalesce(r.ingreso_completo,false) THEN 'Ingreso estimado incompleto'
 WHEN NOT r.metodo_virtual_disponible THEN 'Virtual sin método equivalente en 1/2'
 WHEN r.virtuales>0 AND NOT r.costos_virtuales_completos THEN 'Costos virtuales incompletos'
 WHEN NOT r.costos_reales_completos THEN 'Costos de viajes reales incompletos'
 ELSE 'Costeo o asignación virtual incompletos' END AS motivo
FROM ds_gestion.v_resultado_ciclo r CROSS JOIN ds_gestion.v_politica_cobertura p;
"""


def publicar(con, materializar_controles=False):
    con.execute(SQL_RESULTADO)
    # Escenarios 1/2 pertenecen al tarificador exacto (auditoría Claude 02-11-2026, H-M1): se adaptan antes de los controles.
    from tarificador.exacto.publicacion import adaptar_resultados
    adaptar_resultados(con,servicio=False,ciclo=True)
    # Decisión de Cristian 02-11-2026: se elimina la política Conservador; queda solo Flexible por base.
    # (estructura_conservadora se conserva como dato descriptivo del ciclo.)
    con.execute(SQL_POLITICA)
    consultas={
      'claves_resultado_duplicadas': 'SELECT count(*) FROM (SELECT ciclo_id,escenario_id FROM ds_gestion.v_resultado_ciclo GROUP BY 1,2 HAVING count(*)<>1) x',
      'claves_evaluacion_duplicadas': 'SELECT count(*) FROM (SELECT ciclo_id,escenario_id,politica_id FROM ds_gestion.v_evaluacion_ciclo GROUP BY 1,2,3 HAVING count(*)<>1) x',
      'politicas_incompletas': 'SELECT count(*) FROM (SELECT ciclo_id,escenario_id FROM ds_gestion.v_evaluacion_ciclo GROUP BY 1,2 HAVING count(*)<>1) x',
      'politica_distinta_de_flexible': "SELECT count(*) FROM ds_gestion.v_politica_cobertura WHERE politica_id<>'flexible_base'",
      'escenarios_incompletos': '''SELECT count(*) FROM (
        SELECT ciclo_id FROM ds_gestion.v_resultado_ciclo GROUP BY 1
        HAVING count(*)<>(SELECT count(*) FROM escenarios.dim_escenario)) x''',
      'margenes_comerciales_incompletos': 'SELECT count(*) FROM ds_gestion.v_resultado_ciclo WHERE NOT financiero_comercial_completo AND (costo_comercial IS NOT NULL OR margen_comercial IS NOT NULL)',
      'margenes_fisicos_incompletos': 'SELECT count(*) FROM ds_gestion.v_resultado_ciclo WHERE NOT financiero_fisico_completo AND (costo_fisico IS NOT NULL OR margen_fisico IS NOT NULL)',
      'fisico_no_cuadra': 'SELECT count(*) FROM ds_gestion.v_resultado_ciclo WHERE financiero_fisico_completo AND (margen_fisico IS NULL OR costo_fisico IS NULL OR abs(margen_fisico-(ingreso_estimado-costo_fisico))>0.01)',
      'comercial_no_cuadra': 'SELECT count(*) FROM ds_gestion.v_resultado_ciclo WHERE financiero_comercial_completo AND (margen_comercial IS NULL OR costo_comercial IS NULL OR abs(margen_comercial-(ingreso_estimado-costo_comercial))>0.01)',
      'fisico_comercial_no_conservado': 'SELECT count(*) FROM ds_gestion.v_resultado_ciclo WHERE financiero_fisico_completo AND financiero_comercial_completo AND abs(costo_fisico-costo_comercial)>0.01',
      'evaluables_sin_resultado': 'SELECT count(*) FROM ds_gestion.v_evaluacion_ciclo e JOIN ds_gestion.v_resultado_ciclo r USING(ciclo_id,escenario_id) WHERE e.evaluable AND NOT r.financiero_comercial_completo'
    }
    if materializar_controles:
        # Una misma fotografía transaccional para todos los controles; las vistas
        # públicas y sus fórmulas permanecen iguales. Evita recalcular 11 veces.
        con.execute('CREATE TEMP TABLE control_resultado_ciclo ON COMMIT DROP AS SELECT * FROM ds_gestion.v_resultado_ciclo')
        evaluacion=SQL_POLITICA.split('CREATE OR REPLACE VIEW ds_gestion.v_evaluacion_ciclo AS',1)[1].strip().rstrip(';')
        evaluacion=evaluacion.replace('ds_gestion.v_resultado_ciclo','pg_temp.control_resultado_ciclo')
        con.execute('CREATE TEMP TABLE control_evaluacion_ciclo ON COMMIT DROP AS '+evaluacion)
        consultas={k:q.replace('ds_gestion.v_evaluacion_ciclo','pg_temp.control_evaluacion_ciclo')
          .replace('ds_gestion.v_resultado_ciclo','pg_temp.control_resultado_ciclo') for k,q in consultas.items()}
    controles={}
    for nombre,q in consultas.items():
        print(f'Control resultado ciclo: {nombre}...',flush=True)
        controles[nombre]=int(con.uno(q)[0])
    if any(controles.values()):
        raise RuntimeError(f'Ciclos inconsistentes: {controles}')
    r='pg_temp.control_resultado_ciclo' if materializar_controles else 'ds_gestion.v_resultado_ciclo'
    e='pg_temp.control_evaluacion_ciclo' if materializar_controles else 'ds_gestion.v_evaluacion_ciclo'
    return dict(controles=controles,filas_resultado=int(con.uno('SELECT count(*) FROM '+r)[0]),
      filas_evaluacion=int(con.uno('SELECT count(*) FROM '+e)[0]))
