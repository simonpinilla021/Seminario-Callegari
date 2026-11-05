"""Extensión 1/2: fletero y Caterina = comisión observada, sin cotizarlos en Excel.

Regla de Cristian 04-11-2026, ampliada explícitamente a Caterina el mismo día.
No cambia camiones propios ni los escenarios 3/4.
NULL sigue siendo desconocido; un cero registrado es un valor presente.
"""
SQL = '''
CREATE OR REPLACE VIEW ds_gestion.v_fletero_comision AS
SELECT v.nro_viaje,v.sk_fecha AS fecha_documental_id,s.escenario_id,
 v.ingreso::float8 AS ingreso_estimado,v.pago_tercero_observado::float8 AS comision,
 v.pct_pago_tercero::float8 AS porcentaje_comision_origen,
 (v.ingreso IS NOT NULL AND v.pago_tercero_observado IS NOT NULL) AS evaluable,
 CASE WHEN v.ingreso IS NOT NULL AND v.pago_tercero_observado IS NOT NULL
      THEN v.pago_tercero_observado END::float8 AS costo,
 CASE WHEN v.ingreso IS NOT NULL AND v.pago_tercero_observado IS NOT NULL
      THEN v.ingreso-v.pago_tercero_observado END::float8 AS margen,
 CASE WHEN v.ingreso IS NULL THEN 'Ingreso estimado faltante'
      WHEN v.pago_tercero_observado IS NULL THEN CASE WHEN v.tipo_operador='caterina' THEN 'Comisión de Caterina faltante' ELSE 'Comisión del fletero faltante' END
      WHEN v.tipo_operador='caterina' THEN 'Evaluable Caterina; solo comisión registrada, sin costos fijos'
      ELSE 'Evaluable fletero; solo comisión registrada, sin costos fijos' END::text AS estado
FROM ventas.fact_viaje_venta v CROSS JOIN (VALUES(1),(2)) s(escenario_id)
WHERE v.tipo_operador IN('fletero','caterina');

CREATE OR REPLACE VIEW ds_gestion.v_costeo_servicio_1_2 AS
SELECT q.nro_viaje,q.escenario_id,q.evaluable,q.costo_presentacion,q.margen_tarificador,
 q.estado,6::integer AS componentes_esperados
FROM tarificador.fact_cotizacion_servicio q
UNION ALL
SELECT f.nro_viaje,f.escenario_id,f.evaluable,f.costo,f.margen,f.estado,1::integer
FROM ds_gestion.v_fletero_comision f;
'''

def publicar_fact(con,escenarios=(1,2)):
    if not set(escenarios)<= {1,2}:raise ValueError('Solo escenarios 1/2')
    con.execute('''DELETE FROM escenarios.fact_costo_viaje f USING ventas.fact_viaje_venta v
      WHERE f.nro_viaje=v.nro_viaje AND v.tipo_operador IN('fletero','caterina') AND f.sk_escenario=ANY(%s)''',(list(escenarios),))
    con.execute('''INSERT INTO escenarios.fact_costo_viaje
      (nro_viaje,sk_escenario,sk_componente,sk_fecha,cantidad,tasa,monto,monto_comercial,
       rol_comercial,ciclo_comercial,estado,estado_comercial)
      SELECT v.nro_viaje,s.id,9,v.sk_fecha,1.,v.pago_tercero_observado,v.pago_tercero_observado,
       v.pago_tercero_observado,cy.rol_comercial,cy.ciclo_comercial,
       CASE WHEN v.pago_tercero_observado IS NULL THEN 'sin dato: comisión fletero' ELSE 'ok' END,
       CASE WHEN v.pago_tercero_observado IS NULL THEN 'sin dato: comisión fletero' ELSE 'ok' END
      FROM ventas.fact_viaje_venta v CROSS JOIN unnest(%s::integer[]) s(id)
      LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=v.nro_viaje AND cy.es_virtual='N'
      WHERE v.tipo_operador IN('fletero','caterina') ''',(list(escenarios),))

def controles(con,incluir_ciclos=True):
    sql={
      'costo_o_margen_distinto_regla':'''SELECT count(*) FROM ds_gestion.v_resultado_servicio r
        JOIN ds_gestion.v_fletero_comision f USING(nro_viaje,escenario_id)
        WHERE r.evaluable_comercial IS DISTINCT FROM f.evaluable
        OR r.costo_comercial IS DISTINCT FROM f.costo OR r.margen_comercial IS DISTINCT FROM f.margen''',
      'componentes_fletero_distintos_de_comision':'''SELECT count(*) FROM escenarios.fact_costo_viaje f
        JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE v.tipo_operador IN('fletero','caterina')
        AND f.sk_escenario IN(1,2) AND (f.sk_componente<>9
        OR f.monto IS DISTINCT FROM v.pago_tercero_observado
        OR f.monto_comercial IS DISTINCT FROM v.pago_tercero_observado)''',
      'fleteros_sin_unico_componente':'''SELECT count(*) FROM ventas.fact_viaje_venta v
        CROSS JOIN (VALUES(1),(2)) s(id) LEFT JOIN escenarios.fact_costo_viaje f
        ON f.nro_viaje=v.nro_viaje AND f.sk_escenario=s.id WHERE v.tipo_operador IN('fletero','caterina')
        GROUP BY v.nro_viaje,s.id HAVING count(f.sk_componente)<>1''',
      'ciclos_evaluables_sin_conciliar':'''WITH f AS (
        SELECT cy.ciclo_comercial AS ciclo_id,f.sk_escenario AS escenario_id,sum(f.monto) AS costo
        FROM escenarios.fact_costo_viaje f JOIN ciclos.fact_viaje_ciclo cy
        ON cy.nro_viaje=f.nro_viaje AND cy.es_virtual='N'
        WHERE f.sk_escenario IN(1,2) GROUP BY 1,2)
        SELECT count(*) FROM ds_gestion.v_tarificador_ciclo c LEFT JOIN f USING(ciclo_id,escenario_id)
        WHERE c.evaluable AND (f.costo IS NULL OR abs(c.costo_tarificador-f.costo)>.01
        OR abs(c.margen_tarificador-(c.ingreso_estimado-f.costo))>.01)''',
    }
    if not incluir_ciclos:sql.pop('ciclos_evaluables_sin_conciliar')
    # La consulta de unicidad enumera grupos inválidos, las demás devuelven un contador.
    out={n:(len(con.df(q)) if n=='fleteros_sin_unico_componente' else int(con.uno(q)[0])) for n,q in sql.items()}
    if any(out.values()):raise RuntimeError('Fleteros 1/2 inconsistentes: '+str(out))
    return out
