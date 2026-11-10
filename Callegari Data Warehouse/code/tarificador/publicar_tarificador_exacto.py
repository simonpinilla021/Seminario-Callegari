"""Migración protegida de 1/2. --validar revierte; --guardar exige oráculo aceptado."""
from pathlib import Path
from datetime import datetime
import sys,json,hashlib,argparse,gzip
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from comun import db
from tarificador.exacto.modelo import preparar,cargar,INPUT,VERSION
from tarificador.exacto.publicacion import reemplazar_fact,SQL_VISTAS,adaptar_resultados,controles
from tarificador.exacto.puente import construir as puente
ROOT=Path(__file__).resolve().parents[2]
ORACLE=ROOT/'validacion/escenario2_certificado/oraculo_excel_20261103_completo'  # mismos casos, D27 certificado actualizado; oráculos anteriores conservados

def fingerprint(con,sql,output=None):
    h=hashlib.sha256();count=0
    f=gzip.open(output,'wb') if output else None
    try:
        with con.conn.cursor() as cur:
            with cur.copy('COPY ('+sql+') TO STDOUT WITH (FORMAT CSV,HEADER TRUE)') as cp:
                for b in cp:
                    h.update(b);count+=bytes(b).count(b'\n')
                    if f:f.write(b)
    finally:
        if f:f.close()
    return {'sha256':h.hexdigest(),'filas':count-1}

def comparar_derivados(folder,key):
    """SUM(float8) puede variar por orden de plan; ninguna regla/flag puede variar."""
    a=pd.read_csv(folder/(key+'_antes.csv.gz'));b=pd.read_csv(folder/(key+'_despues.csv.gz'))
    assert list(a.columns)==list(b.columns) and len(a)==len(b)
    diferencias=[];materiales=0
    for col in a:
        both=a[col].isna()&b[col].isna();changed=(a[col]!=b[col])&~both
        if not changed.any():continue
        numeric=pd.api.types.is_numeric_dtype(a[col]) and pd.api.types.is_numeric_dtype(b[col])
        tol=.01 if numeric and a[col].dtype.kind=='f' else 0
        delta=(a[col]-b[col]).abs() if numeric else None
        bad=changed & ((delta>tol)|a[col].isna()|b[col].isna()) if numeric else changed
        materiales+=int(bad.sum())
        for i in a.index[changed]:diferencias.append({'ciclo_id':a.loc[i,'ciclo_id'] if 'ciclo_id' in a else None,'escenario_id':int(a.loc[i,'escenario_id']),'columna':col,'antes':a.loc[i,col],'despues':b.loc[i,col],'diferencia':float(delta[i]) if numeric else None,'material':bool(bad[i])})
    pd.DataFrame(diferencias).to_csv(folder/(key+'_diferencias.csv.gz'),index=False)
    assert materiales==0,'Cambios materiales en '+key
    return {'diferencias_materiales':materiales,'valores_con_variacion_binaria_de_sum_float8':len(diferencias),'maxima_variacion':max((x['diferencia'] or 0 for x in diferencias),default=0),'tolerancia_montos':.01}

PROTECTED={
 'fact_costo_viaje_3_4_5': 'SELECT * FROM escenarios.fact_costo_viaje WHERE sk_escenario NOT IN(1,2) ORDER BY nro_viaje,sk_escenario,sk_componente',
 'fact_costo_virtual': 'SELECT * FROM escenarios.fact_costo_virtual ORDER BY id_viaje,sk_escenario,sk_componente',
 'dim_escenario_3_4_5': 'SELECT * FROM escenarios.dim_escenario WHERE sk_escenario NOT IN(1,2) ORDER BY sk_escenario',
 'resultado_servicio_3_4_5': 'SELECT * FROM ds_gestion.v_resultado_servicio WHERE escenario_id NOT IN(1,2) ORDER BY nro_viaje,escenario_id',
 'resultado_ciclo_3_4_5': 'SELECT * FROM ds_gestion.v_resultado_ciclo WHERE escenario_id NOT IN(1,2) ORDER BY ciclo_id,escenario_id',
}
def wr(folder,name,x):
    (folder/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def validar_excel_dw(con,folder):
    differences=[];checks=0;errores_peaje_esperados=0
    for oracle in [ORACLE,ORACLE/'cinco_puntos']:
        cases=json.loads((oracle/'casos_entradas.json').read_text(encoding='utf-8'))
        results={r['caso_id']:r for r in json.loads((oracle/'excel_resultados.json').read_text(encoding='utf-8'))}
        if oracle!=ORACLE:continue # Límites independientes comprobados por comparador contra la misma función.
        df=con.df('SELECT * FROM tarificador.fact_cotizacion_servicio WHERE nro_viaje=ANY(%s)',([int(x['nro_viaje']) for x in cases if 'nro_viaje' in x],)).set_index(['nro_viaje','escenario_id'])
        for case in cases:
            if 'nro_viaje' not in case:continue
            row=df.loc[(case['nro_viaje'],case['escenario_id'])]
            for cell,actual in results[case['caso_id']]['celdas'].items():
                key=cell.lower()
                if key not in row:continue
                tol=1e-6 if cell in ['E10','E11','E12','E13','E15','F15','G6','I5','K5'] else .01
                if cell in ['K10','K11','K12','K15'] and actual['error']!=0:
                    good=pd.isna(row[key]);errores_peaje_esperados+=1
                else:good=pd.notna(row[key]) and actual['error']==0 and abs(float(row[key])-actual['valor'])<=tol
                if cell=='J8':good=good and ((actual['texto']=='SIN PEAJE')==(row.j8_estado=='SIN PEAJE'))
                checks+=1
                if not good:differences.append({'caso_id':case['caso_id'],'celda':cell,'dw':row[key],'excel':actual})
    wr(folder,'comparacion_excel_dw.json',{'comparaciones':checks,'diferencias':differences,'errores_peaje_excel_representados_como_null':errores_peaje_esperados})
    assert not differences,'DW difiere del Excel recalculado'
    return {'comparaciones':checks,'diferencias':0,'errores_peaje_excel_representados_como_null':errores_peaje_esperados}

def main():
    ap=argparse.ArgumentParser(description=__doc__);g=ap.add_mutually_exclusive_group();g.add_argument('--guardar',action='store_true');g.add_argument('--validar',action='store_true');ap.add_argument('--solo-hechos',action='store_true',help='Validación reversible rápida: no repite las huellas extra de vistas agregadas');args=ap.parse_args()
    if args.guardar and args.solo_hechos:ap.error('--guardar requiere también validación de vistas derivadas')
    for path in [ORACLE,ORACLE/'cinco_puntos']:
        accepted=json.loads((path/'comparacion_excel.json').read_text(encoding='utf-8'))
        if not accepted['aceptado'] or accepted['diferencias']:raise RuntimeError('Oráculo no aceptado: '+str(path))
        for n in ['casos_entradas.json','casos_esperados.json','excel_resultados.json']:
            if accepted.get('archivos_sha256',{}).get(n)!=hashlib.sha256((path/n).read_bytes()).hexdigest():raise RuntimeError('Oráculo desactualizado: '+str(path/n))
    cfg=db.leer_config()['postgres']
    if cfg.get('host','localhost').lower() not in ['localhost','127.0.0.1','::1']:raise RuntimeError('Migración autorizada solo al DW local')
    folder=ROOT/'validacion/tarificador_exacto'/datetime.now().strftime('%Y%m%d_%H%M%S');folder.mkdir(parents=True)
    report={'version':VERSION,'confirmado':False,'carpeta':str(folder)};con=db.conectar()
    try:
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        con.execute("SET LOCAL statement_timeout='15min'");con.execute("SET LOCAL lock_timeout='5s'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:raise RuntimeError('Existe otra construcción del DW')
        before={};protegidos={k:v for k,v in PROTECTED.items() if not(args.solo_hechos and k.startswith('resultado_'))}
        for k,sql in protegidos.items():
            print('Huella anterior:',k,flush=True);before[k]=fingerprint(con,sql,folder/(k+'_antes.csv.gz') if k.startswith('resultado_') else None)
        wr(folder,'huellas_anteriores.json',before)
        columns=con.df("SELECT table_name,column_name,data_type,ordinal_position FROM information_schema.columns WHERE table_schema='ds_gestion' ORDER BY 1,4")
        columns.to_csv(folder/'columnas_ds_antes.csv',index=False)
        definitions=con.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname IN('ds_gestion','escenarios') ORDER BY 1,2")
        wr(folder,'vistas_antes.json',definitions.to_dict('records'))
        print('Respaldando filas anteriores 1/2...',flush=True)
        anterior=con.df('SELECT * FROM escenarios.fact_costo_viaje WHERE sk_escenario IN(1,2) ORDER BY nro_viaje,sk_escenario,sk_componente')
        anterior.to_csv(folder/'fact_costo_viaje_1_2_antes.csv.gz',index=False)
        print('Preparando contrato exacto...',flush=True);data=preparar(con)
        print('Cargando tablas propias...',flush=True);report['carga']=cargar(con,data)
        report['puente']=puente(con,anterior,data[0],folder)
        print('Sustituyendo solo filas 1/2...',flush=True);reemplazar_fact(con,data[0]);con.execute(SQL_VISTAS)
        report['contrato']=adaptar_resultados(con)
        # Decisión de Cristian 02-11-2026: eliminar la política Conservador (solo Flexible por base). Se aplica aquí la
        # misma definición que comun/resultado_ciclo.SQL_POLITICA usa en cada reconstrucción.
        from comun import resultado_ciclo as _rc
        antes_flex=con.df("SELECT escenario_id,count(*) FILTER(WHERE evaluable) AS evaluables,count(*) AS ciclos FROM ds_gestion.v_evaluacion_ciclo WHERE politica_id='flexible_base' GROUP BY 1 ORDER BY 1")
        con.execute(_rc.SQL_POLITICA)
        despues_flex=con.df("SELECT escenario_id,count(*) FILTER(WHERE evaluable) AS evaluables,count(*) AS ciclos FROM ds_gestion.v_evaluacion_ciclo GROUP BY 1 ORDER BY 1")
        assert con.uno("SELECT count(*) FROM ds_gestion.v_politica_cobertura WHERE politica_id<>'flexible_base'")[0]==0
        assert con.uno("SELECT count(*) FROM (SELECT ciclo_id,escenario_id FROM ds_gestion.v_ciclo_politica GROUP BY 1,2 HAVING count(*)<>1) x")[0]==0
        assert antes_flex.set_index('escenario_id').loc[[3,4]].equals(despues_flex.set_index('escenario_id').loc[[3,4]]),'Cambió la evaluación flexible de 3/4'
        report['politica']={'eliminada':'conservador','flexible_antes':antes_flex.to_dict('records'),'despues':despues_flex.to_dict('records')}
        report['controles_exactos']=controles(con)
        after={};report['derivados']={}
        for k,sql in protegidos.items():
            print('Huella posterior:',k,flush=True);after[k]=fingerprint(con,sql,folder/(k+'_despues.csv.gz') if k.startswith('resultado_') else None)
            if k.startswith('resultado_'):report['derivados'][k]=comparar_derivados(folder,k)
            else:assert before[k]==after[k],f'Se alteró {k}'
        wr(folder,'huellas_posteriores.json',after);report['no_regresion']=all(before[k]==after[k] for k in before if not k.startswith('resultado_')) and all(v['diferencias_materiales']==0 for v in report['derivados'].values())
        updated=con.df("SELECT table_name,column_name,data_type,ordinal_position FROM information_schema.columns WHERE table_schema='ds_gestion' ORDER BY 1,4")
        oldnames=set(columns.table_name);assert columns.equals(updated[updated.table_name.isin(oldnames)].reset_index(drop=True)),'Cambió el contrato de columnas existente'
        report['columnas_existentes_conservadas']=True
        report['excel_dw']=validar_excel_dw(con,folder)
        from tarificador.exacto.calculo import calcular
        params=con.uno('SELECT parametros FROM tarificador.version_excel_exacto WHERE version=%s',(VERSION,))[0]
        gold=calcular(dict(c9='Santiago',c10='Arica',c11='Arica',c12='Arica',c13='Arica',e10=2059.04491164,e11=0,e12=0,k10=136010,k11=0,k12=0,b4='si',b5='si',i4=1341.65539,d27=2.65),params)
        assert abs(gold['i17']-4129944.55)<.01 and gold['i22']==5369000
        report['caso_oro']={k:gold[k] for k in ['e15','i17','i22']}
        source=pd.read_csv(INPUT/'km_tramo_tarificador.csv').set_index('sit_stretch_id')
        loaded=con.df('SELECT * FROM tarificador.km_tramo_tarificador').set_index('sit_stretch_id').reindex(source.index)
        assert len(source)==len(loaded) and source.estado.equals(loaded.estado)
        differences=source.km_ida.sub(loaded.km_ida).abs();assert differences.fillna(0).le(1e-6).all()
        assert (source.km_ida.isna()==loaded.km_ida.isna()).all()
        report['km']={'tramos':len(source),'ok':int(source.estado.eq('ok').sum()),'locales':int(source.estado.eq('local (0 km)').sum()),'diferencias':0,'base_entregada_regenerada':False}
        report['precio_control']=con.uno('SELECT precio_excel FROM tarificador.fact_precio_excel_dia WHERE fecha_documental_id=20261015')[0]
        assert abs(report['precio_control']-1341.65539)<1e-8
        coverage=con.df('''SELECT (fecha_documental_id/10000)::int AS anio,cliente_id,escenario_id,estado,supuesto_rm,count(*) AS servicios FROM tarificador.fact_cotizacion_servicio GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5''')
        coverage.to_csv(folder/'cobertura_anio_cliente_motivo.csv',index=False)
        con.df('SELECT * FROM ds_gestion.v_tarificador_ciclo').to_csv(folder/'ciclos_tarificador.csv.gz',index=False)
        con.df('SELECT * FROM tarificador.fact_precio_excel_dia').to_csv(folder/'precio_con_formacion.csv.gz',index=False)
        report['escenario_5']= 'No existe en esta base; ausencia preservada'
        # Auditoría Claude 02-11-2026: supuestos, conciliación ciclo-servicio (H-M2) y pendiente del tramo de 6 puntos.
        report['supuestos']=con.df('''SELECT q.escenario_id,s.supuestos,count(*) AS servicios FROM tarificador.fact_cotizacion_supuesto s
          JOIN tarificador.fact_cotizacion_servicio q USING(nro_viaje,escenario_id) WHERE q.evaluable GROUP BY 1,2 ORDER BY 1,2''').to_dict('records')
        report['conciliacion_ciclo_servicio']=con.df('''WITH s AS (SELECT q.escenario_id,count(*) AS evaluables,sum(q.i17) AS i17 FROM tarificador.fact_cotizacion_servicio q WHERE q.evaluable GROUP BY 1),
          c AS (SELECT escenario_id,sum(servicios_evaluables) AS evaluables_en_ciclos,sum(subtotal_conocido) AS i17_en_ciclos,
                sum(servicios) AS candidatos_en_ciclos FROM ds_gestion.v_tarificador_ciclo GROUP BY 1)
          SELECT s.*,c.evaluables_en_ciclos,c.i17_en_ciclos,s.i17-c.i17_en_ciclos AS i17_fuera_de_ciclos,c.candidatos_en_ciclos FROM s JOIN c USING(escenario_id) ORDER BY 1''').to_dict('records')
        report['servicios_evaluables_fuera_de_ciclos']=con.df('''SELECT q.nro_viaje,q.escenario_id,q.fecha_documental_id,q.clasificacion,q.i17,cy.ciclo_comercial
          FROM tarificador.fact_cotizacion_servicio q LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=q.nro_viaje AND cy.es_virtual='N'
          LEFT JOIN ds_gestion.v_tarificador_ciclo t ON t.ciclo_id=cy.ciclo_comercial AND t.escenario_id=q.escenario_id
          WHERE q.evaluable AND t.ciclo_id IS NULL ORDER BY 1,2''').to_dict('records')
        report['pendiente_tramo_6_puntos']=con.df('''SELECT nro_viaje,fecha_documental_id,texto_tramo FROM tarificador.fact_cotizacion_servicio
          WHERE escenario_id=1 AND n_puntos>5 AND estado_km='excede la planilla' AND fecha_documental_id>=20250101 ORDER BY 2''').to_dict('records')
        report['cobertura_desde_2025']=con.df('''SELECT escenario_id,count(*) AS candidatos,count(*) FILTER(WHERE evaluable) AS evaluables
          FROM tarificador.fact_cotizacion_servicio WHERE fecha_documental_id>=20250101 GROUP BY 1 ORDER BY 1''').to_dict('records')
        if args.guardar:con.commit();report.update(confirmado=True,estado='confirmado')
        else:con.conn.rollback();report['estado']='validado; revertido'
        print(json.dumps(report,ensure_ascii=False,indent=2,default=str),flush=True)
    except BaseException as e:
        con.conn.rollback();report.update(estado='fallo; revertido',error=str(e));raise
    finally:
        wr(folder,'INFORME.json',report);con.close();print('Evidencia:',folder,flush=True)
if __name__=='__main__':main()
