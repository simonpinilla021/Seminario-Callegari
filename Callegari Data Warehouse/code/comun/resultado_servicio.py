"""Resultado por servicio y escenario; reglas financieras vigentes trasladadas al DW.

Sin corte temporal implícito. Comercial y físico se evalúan por separado.
"""
VERSION = '2026-11-02-ds-resultado-servicio-v2-tarificador-exacto'
CONSULTA = """WITH v AS MATERIALIZED (
 SELECT vv.nro_viaje,vv.sk_fecha AS fecha_documental_id,vv.ingreso,vv.tipo_operador,cy.ciclo_comercial,cy.rol_comercial,
 coalesce(vp.tipo_operador='propio',false) AS principal_propio
 FROM ventas.fact_viaje_venta vv
 LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=vv.nro_viaje AND cy.es_virtual='N'
 LEFT JOIN ciclos.dim_ciclo_comercial dc ON dc.ciclo_comercial=cy.ciclo_comercial
 LEFT JOIN ciclos.fact_viaje_ciclo pr ON pr.id_viaje=dc.viaje_principal
 LEFT JOIN ventas.fact_viaje_venta vp ON vp.nro_viaje=pr.nro_viaje
),
 c AS (SELECT f.nro_viaje,f.sk_escenario,count(*)::int AS filas,
 sum(f.monto)::float8 AS subtotal,
 sum(f.monto_comercial) FILTER(WHERE f.estado_comercial='ok')::float8 AS subtotal_comercial,
 bool_and(f.estado='ok' AND f.monto IS NOT NULL) AS datos_ok,
 bool_and(f.estado_comercial='ok' AND f.monto_comercial IS NOT NULL) AS comercial_ok,
 bool_and(CASE WHEN v.tipo_operador='propio' AND f.sk_escenario IN(1,2) THEN f.sk_componente IN(1,2,3,4,5,6)
 WHEN v.tipo_operador='propio' THEN f.sk_componente IN(1,2,3,4,5,6,10)
 WHEN f.sk_escenario IN(1,2) THEN f.sk_componente=9 ELSE f.sk_componente IN(4,5,6,9) END) AS componentes_ok
 FROM escenarios.fact_costo_viaje f JOIN v USING(nro_viaje) GROUP BY f.nro_viaje,f.sk_escenario),
 nv AS (SELECT cy.ciclo_comercial,count(*)::int AS n FROM ciclos.fact_viaje_ciclo cy
 WHERE cy.es_virtual='S' GROUP BY cy.ciclo_comercial),
 cv AS (SELECT f.nro_viaje_destino AS nro_viaje,f.sk_escenario,count(DISTINCT f.id_viaje)::int AS n,
 count(*)::int AS filas,sum(f.monto) FILTER(WHERE f.estado='ok')::float8 AS conocido,
 bool_and(f.estado='ok' AND f.monto IS NOT NULL AND f.sk_componente IN(1,2,3,4,5,6)) AS ok
 FROM escenarios.fact_costo_virtual f JOIN v ON v.nro_viaje=f.nro_viaje_destino
 GROUP BY f.nro_viaje_destino,f.sk_escenario),
 x AS (SELECT v.*,s.sk_escenario,c.subtotal,c.subtotal_comercial,c.filas,
 CASE WHEN v.tipo_operador='propio' AND s.sk_escenario IN(1,2) THEN 6
 WHEN v.tipo_operador='propio' THEN 7 WHEN s.sk_escenario IN(1,2) THEN 1 ELSE 4 END AS esperadas,
 coalesce(c.datos_ok AND c.componentes_ok,false) AS datos_ok,
 coalesce(c.comercial_ok,false) AS comercial_ok,
 coalesce(nv.n,0) AS n_virtuales,coalesce(cv.n,0) AS n_virtuales_destino,
 coalesce(cv.ok AND cv.filas=6*cv.n,true) AS virtuales_ok,cv.conocido AS costo_virtual_conocido
 FROM v CROSS JOIN escenarios.dim_escenario s
 LEFT JOIN c ON c.nro_viaje=v.nro_viaje AND c.sk_escenario=s.sk_escenario
 LEFT JOIN nv ON nv.ciclo_comercial=v.ciclo_comercial
 LEFT JOIN cv ON cv.nro_viaje=v.nro_viaje AND cv.sk_escenario=s.sk_escenario
 WHERE s.sk_escenario IN(1,2,3,4)),
 z AS (SELECT *, (ingreso IS NOT NULL AND tipo_operador IN('propio','caterina','fletero')
 AND datos_ok AND filas=esperadas) AS evaluable,
 (tipo_operador IN('caterina','fletero') OR
 (ciclo_comercial IS NOT NULL AND principal_propio AND rol_comercial IN
 ('envío principal','retorno','envío adicional en ruta','envío adicional local','vacío'))) AS contexto_comercial,
 (CASE WHEN subtotal_comercial IS NOT NULL OR costo_virtual_conocido IS NOT NULL
 THEN coalesce(subtotal_comercial,0)+coalesce(costo_virtual_conocido,0) END)::float8 AS conocido_comercial,
 NOT(tipo_operador='propio' AND rol_comercial='envío principal' AND n_virtuales>0 AND sk_escenario IN(1,2)) AS metodo_virtual,
 NOT(tipo_operador='propio' AND rol_comercial='envío principal' AND sk_escenario IN(3,4)
 AND n_virtuales<>n_virtuales_destino) AS asignacion_virtual FROM x),
 q AS (SELECT *,coalesce(evaluable AND contexto_comercial AND comercial_ok AND metodo_virtual
 AND asignacion_virtual AND virtuales_ok,false) AS evaluable_comercial FROM z)
 SELECT nro_viaje,fecha_documental_id,sk_escenario AS escenario_id,ingreso::float8 AS ingreso_estimado,
 CASE WHEN evaluable THEN subtotal END::float8 AS costo_fisico,
 CASE WHEN evaluable THEN ingreso-subtotal END::float8 AS margen_fisico,
 coalesce(evaluable,false) AS evaluable_fisico,subtotal AS subtotal_conocido_fisico,coalesce(filas,0)::int AS componentes_presentes,esperadas AS componentes_esperados,
 CASE WHEN ingreso IS NULL THEN 'Ingreso estimado faltante'
 WHEN tipo_operador NOT IN('propio','caterina','fletero') OR tipo_operador IS NULL THEN 'Operador fuera del alcance definido'
 WHEN filas IS NULL THEN 'Sin componentes de costo' WHEN filas<>esperadas THEN 'Conjunto de componentes incompleto'
 WHEN NOT datos_ok THEN 'Costo físico incompleto' ELSE 'Evaluable físico' END AS motivo_fisico,
 CASE WHEN evaluable_comercial THEN conocido_comercial END AS costo_comercial,
 CASE WHEN evaluable_comercial THEN ingreso-conocido_comercial END::float8 AS margen_comercial,
 evaluable_comercial,conocido_comercial AS subtotal_conocido_comercial,
 CASE WHEN NOT coalesce(evaluable,false) THEN 'Costo físico o ingreso incompleto'
 WHEN NOT coalesce(contexto_comercial,false) THEN 'Sin contexto propio para reparto comercial'
 WHEN NOT coalesce(metodo_virtual,false) THEN 'Principal con virtual sin método en 1/2'
 WHEN NOT coalesce(asignacion_virtual,false) THEN 'Virtual pendiente de asignación'
 WHEN NOT virtuales_ok THEN 'Costo virtual incompleto'
 WHEN NOT comercial_ok THEN 'Reparto comercial incompleto' ELSE 'Evaluable comercial' END AS motivo_comercial
 ,ciclo_comercial AS ciclo_id,rol_comercial,principal_propio,contexto_comercial,
 datos_ok,comercial_ok,metodo_virtual,asignacion_virtual,virtuales_ok,
 n_virtuales AS virtuales_ciclo,n_virtuales_destino AS virtuales_asignados
 FROM q"""

SQL = 'CREATE OR REPLACE VIEW ds_gestion.v_resultado_servicio AS ' + CONSULTA


def publicar(con, materializar_controles=False):
    con.execute(SQL)
    # Escenarios 1/2 pertenecen al tarificador exacto (auditoría Claude 02-11-2026, H-M1): se adaptan antes de los controles.
    from tarificador.exacto.publicacion import adaptar_resultados
    adaptar_resultados(con,servicio=True,ciclo=False)
    consultas = {
        'claves_duplicadas': 'SELECT count(*) FROM (SELECT nro_viaje,escenario_id FROM ds_gestion.v_resultado_servicio GROUP BY 1,2 HAVING count(*)<>1) x',
        'filas_no_conservadas': 'SELECT abs((SELECT count(*) FROM ds_gestion.v_resultado_servicio)-4*(SELECT count(*) FROM ventas.fact_viaje_venta))',
        'margen_fisico_incompleto': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio WHERE NOT evaluable_fisico AND (costo_fisico IS NOT NULL OR margen_fisico IS NOT NULL)',
        'margen_comercial_incompleto': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio WHERE NOT evaluable_comercial AND (costo_comercial IS NOT NULL OR margen_comercial IS NOT NULL)',
        'comercial_sin_fisico_evaluable': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio WHERE evaluable_comercial AND NOT evaluable_fisico',
        'ingreso_distinto': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio r JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE r.ingreso_estimado IS DISTINCT FROM v.ingreso',
        'margen_fisico_no_cuadra': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio WHERE evaluable_fisico AND (margen_fisico IS NULL OR abs(margen_fisico-(ingreso_estimado-costo_fisico))>0.01)',
        'margen_comercial_no_cuadra': 'SELECT count(*) FROM ds_gestion.v_resultado_servicio WHERE evaluable_comercial AND (margen_comercial IS NULL OR abs(margen_comercial-(ingreso_estimado-costo_comercial))>0.01)'
    }
    if materializar_controles:
        con.execute('CREATE TEMP TABLE control_resultado_servicio ON COMMIT DROP AS SELECT * FROM ds_gestion.v_resultado_servicio')
        consultas={k:q.replace('ds_gestion.v_resultado_servicio','pg_temp.control_resultado_servicio') for k,q in consultas.items()}
    controles = {}
    for nombre,q in consultas.items():
        print(f'Control resultado servicio: {nombre}...',flush=True)
        controles[nombre] = int(con.uno(q)[0])
    from tarificador.exacto import fleteros
    if con.uno("SELECT to_regclass('ds_gestion.v_fletero_comision') IS NOT NULL")[0]:
        # El control de ciclos se ejecuta al publicar ambos resultados, no durante
        # este paso intermedio del constructor donde el resumen puede ser anterior.
        controles.update({'fleteros_'+k:v for k,v in fleteros.controles(con,incluir_ciclos=False).items()})
    if any(controles.values()):
        raise RuntimeError(f'Resultados inconsistentes: {controles}')
    tabla='pg_temp.control_resultado_servicio' if materializar_controles else 'ds_gestion.v_resultado_servicio'
    return dict(controles=controles,filas=int(con.uno('SELECT count(*) FROM '+tabla)[0]))
