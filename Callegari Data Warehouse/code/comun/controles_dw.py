"""Controles permanentes antes de confirmar una construcción del DW."""
import time
from comun import db
from comun.validacion_margenes import comprobar_margenes

def controles(conn, habia_escenarios):
    n = 0

    def uno(sql):
        nonlocal n
        n += 1
        inicio = time.perf_counter()
        print(f"   Comprobación SQL {n}: en curso...", flush=True)
        valor = conn.execute(sql).fetchone()[0]
        print(f"   Comprobación SQL {n}: {valor} ({time.perf_counter() - inicio:.1f} s)", flush=True)
        return valor

    checks = {}
    checks['cotizaciones_incompletas_publicadas'] = uno("""
      SELECT count(*) FROM escenarios.fact_cotizacion_viaje
      WHERE (NOT coalesce(cotizacion_completa,false) AND cotizacion IS NOT NULL)
         OR (NOT coalesce(costo_completo,false) AND costo_total IS NOT NULL)
         OR (cotizacion_completa AND (cotizacion IS NULL OR estado IS DISTINCT FROM 'ok'
            OR n_componentes_faltantes<>0 OR faltantes IS DISTINCT FROM ''))
    """)
    if habia_escenarios:
        checks["diferencias_escenarios_1_2"] = uno("""
          SELECT count(*) FROM validacion_antes a FULL JOIN (
            SELECT nro_viaje, sk_escenario, sk_componente, monto, monto_comercial
            FROM escenarios.fact_costo_viaje WHERE sk_escenario IN (1,2)
          ) b USING (nro_viaje, sk_escenario, sk_componente)
          WHERE a.nro_viaje IS NULL OR b.nro_viaje IS NULL
             OR (a.monto IS NULL) <> (b.monto IS NULL)
             OR abs(a.monto - b.monto) > 0.01
             OR (a.monto_comercial IS NULL) <> (b.monto_comercial IS NULL)
             OR abs(a.monto_comercial - b.monto_comercial) > 0.01
        """)
    checks["diferencias_redistribucion"] = uno("""
      SELECT count(*) FROM (
        SELECT ciclo_comercial, sk_escenario, sk_componente
        FROM escenarios.fact_costo_viaje GROUP BY 1,2,3
        HAVING abs(coalesce(sum(monto),0)-coalesce(sum(monto_comercial),0)) > 0.01
      ) t
    """)
    checks["comision_incorrecta"] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_viaje f JOIN ventas.fact_viaje_venta v USING(nro_viaje)
      WHERE f.sk_componente = 10 AND (f.sk_escenario NOT IN (3,4) OR v.tipo_operador <> 'propio'
        OR f.monto IS DISTINCT FROM v.comision_conductor_observada)
    """)
    checks["pago_tercero_incorrecto"] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_viaje f JOIN ventas.fact_viaje_venta v USING(nro_viaje)
      WHERE f.sk_componente = 9 AND f.sk_escenario IN (3,4)
        AND f.monto IS DISTINCT FROM v.pago_tercero_observado
    """)
    checks["margenes_incompletos_publicados"] = uno("""
      SELECT count(*) FROM escenarios.v_costo_viaje_resumen
      WHERE (NOT coalesce(completo_3,false) AND margen_3 IS NOT NULL)
         OR (NOT coalesce(completo_4,false) AND margen_4 IS NOT NULL)
         OR (NOT coalesce(completo_comercial_3,false) AND margen_comercial_3 IS NOT NULL)
         OR (NOT coalesce(completo_comercial_4,false) AND margen_comercial_4 IS NOT NULL)
    """)
    checks["virtuales_con_destino_incorrecto"] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_virtual x
      LEFT JOIN ventas.fact_viaje_venta v ON v.nro_viaje = x.nro_viaje_destino
      WHERE x.nro_viaje_destino IS NOT NULL AND v.tipo_operador IS DISTINCT FROM 'propio'
    """)
    checks["virtuales_sin_tramo_con_costo_publicado"] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_virtual
      WHERE estado_tramo <> 'disponible' AND sk_componente=1
        AND (monto IS NOT NULL OR estado='ok')
    """)
    checks['virtuales_adicionales_sin_contexto_propio'] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_virtual
      WHERE sk_componente IN (3,4,5,6) AND monto IS NOT NULL
        AND estado_contexto IS DISTINCT FROM 'propio concordante inferido'
    """)
    checks['virtuales_fijo_distinto_de_bolsas'] = uno("""
      SELECT count(*) FROM escenarios.fact_costo_virtual
      WHERE sk_componente=4 AND estado='ok' AND (
        cf_flota_virtual IS NULL OR cf_rampla_virtual IS NULL OR cf_estructura_virtual IS NULL
        OR abs(monto-cf_flota_virtual-cf_rampla_virtual-cf_estructura_virtual)>0.01)
    """)
    checks['virtuales_totales_incompletos_publicados'] = uno("""
      SELECT count(*) FROM escenarios.v_costo_virtual_resumen
      WHERE NOT completo AND costo_completo IS NOT NULL
    """)
    from comun.conciliacion_costos import comprobar as comprobar_conservacion
    comprobaciones_conservacion, diagnostico_conservacion = comprobar_conservacion(conn)
    checks.update(comprobaciones_conservacion)
    resumen = {'conciliacion_con_virtuales':diagnostico_conservacion}
    for tabla in ("ventas.fact_viaje_venta", "costos.fact_viaje_costo_indirecto",
                  "escenarios.fact_costo_viaje", "escenarios.fact_costo_virtual"):
        resumen[tabla] = uno(f"SELECT count(*) FROM {tabla}")
    resumen["virtuales_sin_destino"] = uno("""SELECT count(DISTINCT id_viaje)
      FROM escenarios.fact_costo_virtual WHERE nro_viaje_destino IS NULL""")
    resumen["viajes_con_comercial_3_completo"] = uno("""SELECT count(*)
      FROM escenarios.v_costo_viaje_resumen WHERE completo_comercial_3""")
    resumen["virtuales_con_tramo_pendiente"] = uno("""SELECT count(*)
      FROM escenarios.v_tramos_virtuales_pendientes""")
    resumen['virtuales_con_costos_completos_3'] = uno("""SELECT count(*)
      FROM escenarios.v_costo_virtual_resumen WHERE sk_escenario=3 AND completo""")
    resumen['virtuales_conocidos_sin_imputacion_3'] = uno("""SELECT count(*)
      FROM escenarios.v_costo_virtual_resumen WHERE sk_escenario=3 AND nro_viaje_destino IS NULL
        AND subtotal_conocido IS NOT NULL""")
    checks.update({f'margenes_{k}':v for k,v in comprobar_margenes(db.Base(conn)).items()})
    return checks, resumen

