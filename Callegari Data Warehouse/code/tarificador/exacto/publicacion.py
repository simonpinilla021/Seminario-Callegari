"""Publicación propia y adaptadores compatibles, sin redistribución en 1/2."""
import pandas as pd
from .modelo import preparar,cargar,VERSION,INPUT
from . import fleteros

SQL_VISTAS=fleteros.SQL+'''
CREATE OR REPLACE VIEW ds_gestion.v_tarificador_servicio AS
SELECT q.*,cy.ciclo_comercial AS ciclo_id,cl.razon_social AS cliente,
 'Tarificador'::text AS familia_modelo,
 'Costo del retorno mostrado como cero por decisión de presentación; I17 y margen calculado se conservan'::text AS regla_presentacion
FROM tarificador.fact_cotizacion_servicio q
LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=q.nro_viaje AND cy.es_virtual='N'
LEFT JOIN ventas.dim_cliente cl ON cl.cliente_id=q.cliente_id;

CREATE OR REPLACE VIEW ds_gestion.v_tarificador_ciclo AS
WITH mov AS (
 SELECT cy.ciclo_comercial,cy.nro_viaje,cy.es_virtual,v.tipo_operador,v.ingreso,
 (v.ingreso>0 OR v.tipo_operador IN('fletero','caterina')) AS candidato
 FROM ciclos.fact_viaje_ciclo cy LEFT JOIN ventas.fact_viaje_venta v ON v.nro_viaje=cy.nro_viaje
), alcance AS (
 SELECT d.ciclo_comercial,d.viaje_principal,p.sk_fecha AS fecha_documental_principal_id
 FROM ciclos.dim_ciclo_comercial d JOIN ciclos.fact_viaje_ciclo p ON p.id_viaje=d.viaje_principal
 JOIN ventas.fact_viaje_venta v ON v.nro_viaje=p.nro_viaje WHERE v.tipo_operador='propio'
), suma AS (
 SELECT a.ciclo_comercial AS ciclo_id,s.escenario_id,a.fecha_documental_principal_id,
 count(*) FILTER(WHERE m.es_virtual='N' AND m.candidato)::bigint AS servicios,
 count(*) FILTER(WHERE m.es_virtual='N' AND m.candidato AND q.evaluable)::bigint AS servicios_evaluables,
 count(*) FILTER(WHERE m.es_virtual='N')::bigint AS reales,
 count(*) FILTER(WHERE m.es_virtual='S')::bigint AS virtuales,
 bool_and(m.ingreso IS NOT NULL) FILTER(WHERE m.es_virtual='N') AS ingreso_completo,
 bool_and(m.tipo_operador IN('propio','fletero','caterina')) FILTER(WHERE m.es_virtual='N' AND m.candidato) AS operadores_admitidos,
 sum(m.ingreso) FILTER(WHERE m.es_virtual='N')::float8 AS ingreso_estimado,
 sum(q.costo_presentacion) FILTER(WHERE q.evaluable)::float8 AS subtotal_conocido,  -- retorno = 0 (regla del gerente)
 sum(q.costo_presentacion) FILTER(WHERE q.evaluable)::float8 AS subtotal_presentacion,
 string_agg(DISTINCT coalesce(q.estado,'servicio fuera del alcance o sin clasificación'),'; ')
  FILTER(WHERE m.es_virtual='N' AND m.candidato AND NOT coalesce(q.evaluable,false)) AS motivos
 FROM alcance a CROSS JOIN (VALUES(1),(2)) s(escenario_id)
 JOIN mov m ON m.ciclo_comercial=a.ciclo_comercial
 LEFT JOIN ds_gestion.v_costeo_servicio_1_2 q ON q.nro_viaje=m.nro_viaje AND q.escenario_id=s.escenario_id
 GROUP BY 1,2,3
), evaluado AS (
 SELECT *,coalesce(servicios>0 AND servicios=servicios_evaluables AND ingreso_completo AND operadores_admitidos,false) AS evaluable FROM suma)
SELECT *,CASE WHEN evaluable THEN subtotal_conocido END::float8 AS costo_tarificador,
 CASE WHEN evaluable THEN ingreso_estimado-subtotal_conocido END::float8 AS margen_tarificador,
 CASE WHEN evaluable THEN subtotal_presentacion END::float8 AS costo_presentacion,
 CASE WHEN evaluable THEN 'ok' ELSE coalesce(motivos,'sin servicios cotizables o ingreso incompleto') END AS estado
FROM evaluado;
'''

def reemplazar_fact(con,q,escenarios=(1,2)):
    """Solo escribe filas 1/2; nunca ejecuta la redistribución de los otros escenarios."""
    params=pd.read_json(INPUT/'parametros_excel.json',typ='series').to_dict()
    roles=con.df("SELECT nro_viaje,rol_comercial,ciclo_comercial FROM ciclos.fact_viaje_ciclo WHERE es_virtual='N'")
    rmap={int(r.nro_viaje):(r.rol_comercial,r.ciclo_comercial) for r in roles.itertuples()}
    records=[]
    cells={1:'i6',2:'i7',3:'i8',4:'i12',5:'i13',6:'i11'}
    for row in q.to_dict('records'):
        role,cycle=rmap.get(int(row['nro_viaje']),(None,None))
        ok=bool(row.get('evaluable'))
        sin_costo=row.get('clasificacion')=='Retorno'   # regla del gerente: el retorno no tiene costo en 1/2
        for comp,cell in cells.items():
            value=row.get(cell);factor=row.get('factor_retorno')
            # Auditoría Claude 02-11-2026, H-A1: un servicio no cotizable no publica componentes parciales.
            amount=(0. if sin_costo else value*factor) if ok and pd.notna(value) and pd.notna(factor) else None
            tasa={1:row.get('i4'),2:row.get('i4'),3:value,4:params['B24'],5:params['H29'] if row['n5']=='si' else params['H25'],6:params['B22'] if row['n5']=='si' else params['B21']}[comp]
            cantidad=amount/tasa if amount is not None and pd.notna(tasa) and tasa!=0 else 0. if amount==0 else None
            records.append((int(row['nro_viaje']),int(row['escenario_id']),comp,int(row['fecha_documental_id']),cantidad,tasa,amount,amount,role,cycle,'ok' if amount is not None else row['estado'],'ok' if amount is not None else row['estado']))
    if not set(escenarios)<=set((1,2)):raise ValueError('Solo tarificador 1/2')
    q=q[q.escenario_id.isin(escenarios)]
    records=[r for r in records if r[1] in escenarios]
    con.execute('DELETE FROM escenarios.fact_costo_viaje WHERE sk_escenario=ANY(%s)',(list(escenarios),))
    con.copiar('escenarios.fact_costo_viaje',['nro_viaje','sk_escenario','sk_componente','sk_fecha','cantidad','tasa','monto','monto_comercial','rol_comercial','ciclo_comercial','estado','estado_comercial'],records)
    fleteros.publicar_fact(con,escenarios)
    con.execute("UPDATE escenarios.dim_escenario SET nombre=CASE sk_escenario WHEN 1 THEN 'tarificador_exacto' ELSE 'tarificador_exacto_rend_camion' END,descripcion=CASE sk_escenario WHEN 1 THEN 'Cotización exacta Excel por servicio propio; sin reparto por ciclo' ELSE 'Cotización exacta Excel con rendimiento del camión; sin reparto por ciclo' END WHERE sk_escenario=ANY(%s)",(list(escenarios),))
    con.execute('ANALYZE escenarios.fact_costo_viaje')

def construir(con):
    from combustible.rendimiento_tarificador_certificado import publicar as publicar_rendimiento
    publicar_rendimiento(con)
    data=preparar(con);report=cargar(con,data);reemplazar_fact(con,data[0])
    con.execute('CREATE SCHEMA IF NOT EXISTS ds_gestion');con.execute(SQL_VISTAS)
    report['controles']=controles(con)
    return report

def controles(con):
    checks={
     'costo_incompleto_publicado':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio WHERE NOT evaluable AND (i17 IS NOT NULL OR margen_tarificador IS NOT NULL OR i22 IS NOT NULL)",
     'costo_sin_margen_o_precio':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio WHERE evaluable AND (i17 IS NULL OR i22 IS NULL OR margen_tarificador IS NULL OR abs(margen_tarificador-(ingreso_estimado-CASE WHEN clasificacion='Retorno' THEN 0 ELSE i17 END))>.01)",
     'costo_retorno_distinto_de_cero':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio WHERE evaluable AND clasificacion='Retorno' AND abs(margen_tarificador-ingreso_estimado)>.01",
     'fact_retorno_con_costo':'''SELECT count(*) FROM escenarios.fact_costo_viaje f JOIN tarificador.fact_cotizacion_servicio q
       ON q.nro_viaje=f.nro_viaje AND q.escenario_id=f.sk_escenario WHERE f.sk_escenario IN(1,2) AND q.clasificacion='Retorno' AND coalesce(f.monto,0)<>0''',
     'vacios_o_terceros_cotizados':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio q JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE v.tipo_operador<>'propio' OR v.ingreso<=0 OR q.clasificacion NOT IN('Envio','Retorno')",
     'reparto_1_2':"SELECT count(*) FROM escenarios.fact_costo_viaje WHERE sk_escenario IN(1,2) AND monto IS DISTINCT FROM monto_comercial",
     'retorno_mostrado_distinto_de_cero':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio WHERE clasificacion='Retorno' AND costo_presentacion IS DISTINCT FROM 0::float8",
     'presentacion_en_no_evaluable':"SELECT count(*) FROM tarificador.fact_cotizacion_servicio WHERE NOT evaluable AND clasificacion<>'Retorno' AND costo_presentacion IS NOT NULL",
     'fact_no_evaluable_con_monto':'''SELECT count(*) FROM escenarios.fact_costo_viaje f JOIN tarificador.fact_cotizacion_servicio q
       ON q.nro_viaje=f.nro_viaje AND q.escenario_id=f.sk_escenario WHERE f.sk_escenario IN(1,2) AND NOT q.evaluable AND (f.monto IS NOT NULL OR f.estado='ok')''',
     'fact_evaluable_incompleto':'''SELECT count(*) FROM escenarios.fact_costo_viaje f JOIN tarificador.fact_cotizacion_servicio q
       ON q.nro_viaje=f.nro_viaje AND q.escenario_id=f.sk_escenario WHERE f.sk_escenario IN(1,2) AND q.evaluable AND (f.monto IS NULL OR f.estado<>'ok')''',
     'suma_fact_distinta_cotizador':'''SELECT count(*) FROM tarificador.fact_cotizacion_servicio q JOIN (SELECT nro_viaje,sk_escenario,sum(monto) AS costo FROM escenarios.fact_costo_viaje WHERE sk_escenario IN(1,2) GROUP BY 1,2) f ON f.nro_viaje=q.nro_viaje AND f.sk_escenario=q.escenario_id WHERE q.evaluable AND (f.costo IS NULL OR abs(f.costo-CASE WHEN q.clasificacion='Retorno' THEN 0 ELSE q.i17 END)>.01)''',
     # Decisión 02-11-2026: se admite además la fila 641073 COQUIMBO, siempre marcada como SUPUESTO.
     'tct_fuera_antofagasta_con_precio':"SELECT count(*) FROM tarificador.fact_precio_excel_dia WHERE upper(coalesce(tct_localidad_region,''))<>'ANTOFAGASTA' AND precio_excel IS NOT NULL AND coalesce(tct_origen_entrada,'') NOT LIKE 'SUPUESTO: fila 641073%'",
     'supuesto_coquimbo_sin_marca':'''SELECT count(*) FROM tarificador.fact_cotizacion_servicio q JOIN tarificador.fact_precio_excel_dia p USING(fecha_documental_id)
       LEFT JOIN tarificador.fact_cotizacion_supuesto s ON s.nro_viaje=q.nro_viaje AND s.escenario_id=q.escenario_id
       WHERE q.evaluable AND p.tct_origen_entrada LIKE 'SUPUESTO%' AND coalesce(s.supuestos,'') NOT LIKE '%641073%' ''',
    }
    result={n:int(con.uno(q)[0]) for n,q in checks.items()}
    if any(result.values()):raise RuntimeError('Contrato exacto inconsistente: '+str(result))
    return result

def adaptar_resultados(con,servicio=True,ciclo=True):
    """Mismos nombres, orden y tipos públicos. El resto de escenarios usa su SQL anterior.
    Auditoría Claude 02-11-2026, H-M1: comun.resultado_servicio/resultado_ciclo.publicar llaman a esta función
    (servicio=... / ciclo=...) inmediatamente después de crear su vista, así que ninguna ruta deja 1/2 en la lógica legada."""
    from comun import resultado_servicio as srv,resultado_ciclo as cyc
    if con.uno("SELECT to_regclass('tarificador.fact_cotizacion_servicio') IS NOT NULL")[0] is not True:
        return {'version':VERSION,'adaptado':False,'motivo':'tarificador.fact_cotizacion_servicio aún no existe'}
    con.execute(fleteros.SQL)
    def cols(view):return con.df("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='ds_gestion' AND table_name=%s ORDER BY ordinal_position",(view,)).to_dict('records')
    def select(mapping,columns):
        missing=[x['column_name'] for x in columns if x['column_name'] not in mapping]
        if missing:raise ValueError('Contrato ampliado sin adaptación: '+str(missing))
        return ','.join('('+mapping[x['column_name']]+')::'+x['data_type']+' AS '+x['column_name'] for x in columns)
    ev='coalesce(t.evaluable,false)'
    sm={
     'nro_viaje':'v.nro_viaje','fecha_documental_id':'v.sk_fecha','escenario_id':'s.id','ingreso_estimado':'v.ingreso',
     'costo_fisico':'CASE WHEN t.evaluable THEN t.costo_presentacion END','margen_fisico':'t.margen_tarificador','evaluable_fisico':ev,'subtotal_conocido_fisico':'CASE WHEN t.evaluable THEN t.costo_presentacion END',
     'componentes_presentes':'CASE WHEN t.nro_viaje IS NOT NULL THEN t.componentes_esperados ELSE 0 END','componentes_esperados':'coalesce(t.componentes_esperados,6)',
     'motivo_fisico':"CASE WHEN v.tipo_operador IN('fletero','caterina') THEN t.estado WHEN t.evaluable THEN 'Evaluable tarificador (no costo físico)' ELSE coalesce(t.estado,'No cotizable: vacío, sin ingreso o fuera del alcance propio') END",
     'costo_comercial':'CASE WHEN t.evaluable THEN t.costo_presentacion END','margen_comercial':'t.margen_tarificador','evaluable_comercial':ev,'subtotal_conocido_comercial':'CASE WHEN t.evaluable THEN t.costo_presentacion END',
     'motivo_comercial':"CASE WHEN v.tipo_operador IN('fletero','caterina') THEN t.estado WHEN t.evaluable THEN 'Evaluable tarificador; sin redistribución' ELSE coalesce(t.estado,'No cotizable: vacío, sin ingreso o fuera del alcance propio') END",
     'ciclo_id':'cy.ciclo_comercial','rol_comercial':'cy.rol_comercial','principal_propio':"v.tipo_operador='propio'",'contexto_comercial':ev,
     'datos_ok':ev,'comercial_ok':ev,'metodo_virtual':'true','asignacion_virtual':'true','virtuales_ok':'true','virtuales_ciclo':'0','virtuales_asignados':'0'}
    if servicio:
     columns=cols('v_resultado_servicio')
     sql='CREATE OR REPLACE VIEW ds_gestion.v_resultado_servicio AS WITH anterior AS ('+srv.CONSULTA+') SELECT * FROM anterior WHERE escenario_id NOT IN(1,2) UNION ALL SELECT '+select(sm,columns)+''' FROM ventas.fact_viaje_venta v CROSS JOIN (VALUES(1),(2)) s(id)
     LEFT JOIN ds_gestion.v_costeo_servicio_1_2 t ON t.nro_viaje=v.nro_viaje AND t.escenario_id=s.id
     LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=v.nro_viaje AND cy.es_virtual='N' '''
     con.execute(sql)
    if not ciclo:return {'version':VERSION,'adaptado':'servicio','columnas_conservadas':True}
    columns=cols('v_resultado_ciclo');cm={x['column_name']:'a.'+x['column_name'] for x in columns}
    cm.update(ingreso_estimado='t.ingreso_estimado',subtotal_conocido_comercial='t.subtotal_conocido',subtotal_conocido_fisico='t.subtotal_conocido',costo_comercial='t.costo_tarificador',margen_comercial='t.margen_tarificador',costo_fisico='t.costo_tarificador',margen_fisico='t.margen_tarificador',financiero_comercial_completo=ev,financiero_fisico_completo=ev,propios='coalesce(t.operadores_admitidos,false)',ingreso_completo='coalesce(t.ingreso_completo,false)',estructura_conservadora='true',estructura_flexible='true',costos_reales_completos=ev,costos_virtuales_completos='true',metodo_virtual_disponible='true',submotivo_estructura="'No aplica: cotización independiente por servicio'",condiciones_estructura="'No se costean vacíos ni virtuales; se suman los servicios cotizables'")
    sql='CREATE OR REPLACE VIEW ds_gestion.v_resultado_ciclo AS WITH anterior AS ('+cyc.CONSULTA+') SELECT * FROM anterior WHERE escenario_id NOT IN(1,2) UNION ALL SELECT '+select(cm,columns)+''' FROM anterior a LEFT JOIN ds_gestion.v_tarificador_ciclo t ON t.ciclo_id=a.ciclo_id AND t.escenario_id=a.escenario_id WHERE a.escenario_id IN(1,2)'''
    con.execute(sql)
    return {'version':VERSION,'columnas_conservadas':True,'retorno_sin_costo_en_todas_las_vistas_1_2':True}
