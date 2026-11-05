"""Conciliaciones de las vistas comunes con los componentes y escenarios canónicos."""


def comprobar_margenes(con):
    checks={}
    def control(nombre,sql):
        print(f'   Control de margenes: {nombre}...',flush=True)
        checks[nombre]=int(con.uno(sql)[0])

    control('viajes_duplicados', '''SELECT count(*) FROM (
      SELECT nro_viaje FROM comun.v_margen_viaje GROUP BY nro_viaje HAVING count(*)<>1) x''')
    control('operacional_distinto_de_escenario_3', '''SELECT count(*) FROM comun.v_margen_viaje m
      LEFT JOIN escenarios.v_costo_viaje_resumen e USING(nro_viaje)
      WHERE (m.margen_operacional IS NULL) <> (e.margen_3 IS NULL)
         OR abs(m.margen_operacional-e.margen_3)>0.01
         OR (m.margen_operacional_comercial IS NULL) <> (e.margen_comercial_3 IS NULL)
         OR abs(m.margen_operacional_comercial-e.margen_comercial_3)>0.01''')
    control('operacional_distinto_de_componentes', '''WITH costos AS (
      SELECT nro_viaje,sum(monto) AS costo,bool_and(estado='ok' AND monto IS NOT NULL) AS completo,
             count(*) AS componentes
      FROM escenarios.fact_costo_viaje WHERE sk_escenario=3 GROUP BY nro_viaje),
    esperado AS (
      SELECT m.nro_viaje,m.margen_operacional,
        CASE WHEN c.completo AND ((m.tipo_operador='propio' AND c.componentes=7)
           OR (m.tipo_operador IN ('fletero','caterina') AND c.componentes=4))
          THEN m.ingreso-c.costo END AS margen_esperado
      FROM comun.v_margen_viaje m LEFT JOIN costos c USING(nro_viaje))
    SELECT count(*) FROM esperado
    WHERE (margen_operacional IS NULL) <> (margen_esperado IS NULL)
       OR abs(margen_operacional-margen_esperado)>0.01''')
    control('parcial_con_observacion_faltante', '''SELECT count(*) FROM comun.v_margen_viaje
      WHERE (tipo_operador='propio' AND comision_conductor_observada IS NULL AND margen_contribucion IS NOT NULL)
         OR (tipo_operador IN ('fletero','caterina') AND pago_tercero_observado IS NULL AND margen_contribucion IS NOT NULL)''')
    control('cobertura_operacional_incorrecta', '''SELECT count(*) FROM comun.v_margen_viaje
      WHERE (cobertura_margen IN ('completo','completo (tercero)') AND margen_operacional IS NULL)
         OR (cobertura_margen NOT IN ('completo','completo (tercero)') AND margen_operacional IS NOT NULL)''')
    control('comercial_publicado_incompleto', '''SELECT count(*) FROM comun.v_margen_viaje
      WHERE NOT costos_comerciales_completos AND margen_operacional_comercial IS NOT NULL''')
    return checks
