"""Regla autorizada 04-11: fleteros y Caterina 1/2 = comisión, sin otros costos.

--validar revierte; --guardar publica. No recalcula cotizaciones, km ni 3/4.
"""
from pathlib import Path
from datetime import datetime
import sys,json,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from comun import db
from tarificador.exacto import fleteros
from tarificador.exacto.publicacion import SQL_VISTAS,adaptar_resultados,controles
from tarificador.publicar_tarificador_exacto import fingerprint,comparar_derivados,PROTECTED
ROOT=Path(__file__).resolve().parents[2]

def ejecutar(guardar=False):
    out=ROOT/'validacion/caterina_comision_1_2'/datetime.now().strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True)
    c=db.conectar();r=dict(version='2026-11-04-fleteros-caterina-comision',confirmado=False)
    try:
        if c.conn.info.host.lower() not in ('localhost','127.0.0.1','::1'):raise RuntimeError('Solo DW local')
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        c.execute("SET LOCAL statement_timeout='15min'")
        c.execute("SET LOCAL lock_timeout='5s'")
        if not c.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:raise RuntimeError('Constructor activo')
        protected=dict(PROTECTED)
        protected.update(
          cotizaciones_excel='SELECT * FROM tarificador.fact_cotizacion_servicio ORDER BY nro_viaje,escenario_id',
          hechos_propios_1_2="SELECT f.* FROM escenarios.fact_costo_viaje f JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE v.tipo_operador='propio' AND f.sk_escenario IN(1,2) ORDER BY f.nro_viaje,f.sk_escenario,f.sk_componente",
          porcentajes_fuente='SELECT nro_viaje,pct_pago_tercero,pago_tercero_observado FROM ventas.fact_viaje_venta ORDER BY nro_viaje',
          hechos_fleteros_1_2="SELECT f.* FROM escenarios.fact_costo_viaje f JOIN ventas.fact_viaje_venta v USING(nro_viaje) WHERE v.tipo_operador='fletero' AND f.sk_escenario IN(1,2) ORDER BY f.nro_viaje,f.sk_escenario,f.sk_componente")
        before={}
        for n,q in protected.items():
            print('Huella anterior:',n,flush=True)
            before[n]=fingerprint(c,q,out/(n+'_antes.csv.gz') if n.startswith('resultado_') else None)
        c.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname='ds_gestion' ORDER BY 2").to_json(out/'vistas_antes.json',orient='records',force_ascii=False)
        c.df('SELECT * FROM escenarios.fact_costo_viaje WHERE sk_escenario IN(1,2) ORDER BY nro_viaje,sk_escenario,sk_componente').to_csv(out/'hechos_1_2_antes.csv.gz',index=False)
        print('Publicando solo comisión de fleteros y Caterina 1/2...',flush=True)
        fleteros.publicar_fact(c)
        c.execute(SQL_VISTAS)
        adaptar_resultados(c)
        r['controles_tarificador']=controles(c)
        r['controles_fleteros']=fleteros.controles(c)
        r['no_regresion']={}
        for n,q in protected.items():
            print('Huella posterior:',n,flush=True)
            after=fingerprint(c,q,out/(n+'_despues.csv.gz') if n.startswith('resultado_') else None)
            if n.startswith('resultado_'):
                r['no_regresion'][n]=comparar_derivados(out,n)
            else:
                assert before[n]==after,'Cambio protegido: '+n
                r['no_regresion'][n]=dict(identico=True,**after)
        r['resumen']=c.df('''SELECT (fecha_documental_id/10000)::int anio,escenario_id,count(*) viajes,
          count(*) FILTER(WHERE evaluable) evaluables,sum(ingreso_estimado) ingreso,
          sum(costo) comision,sum(margen) margen,count(*) FILTER(WHERE porcentaje_comision_origen IS NOT NULL) con_porcentaje
          FROM ds_gestion.v_fletero_comision WHERE fecha_documental_id>=20250101 GROUP BY 1,2 ORDER BY 1,2''').to_dict('records')
        c.df('SELECT * FROM ds_gestion.v_fletero_comision WHERE fecha_documental_id>=20250101 ORDER BY nro_viaje,escenario_id').to_csv(out/'fleteros_1_2.csv.gz',index=False)
        c.df('''SELECT ciclo_id,escenario_id,evaluable,ingreso_estimado,costo_tarificador,margen_tarificador
          FROM ds_gestion.v_tarificador_ciclo WHERE fecha_documental_principal_id>=20250101 ORDER BY 1,2''').to_csv(out/'ciclos_1_2.csv.gz',index=False)
        # Las mismas comisiones deben reflejarse en los resultados y en los hechos, incluidos retornos.
        r['ejemplos']=c.df('''SELECT f.*,cy.clasificacion_viaje FROM ds_gestion.v_fletero_comision f
          LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=f.nro_viaje AND cy.es_virtual='N'
          WHERE f.nro_viaje IN(1610041,1609992,1525796) ORDER BY 1,3''').to_dict('records')
        if guardar:c.commit();r.update(confirmado=True,estado='confirmado')
        else:c.conn.rollback();r['estado']='validado; revertido'
        print(json.dumps(r,ensure_ascii=False,indent=2,default=str),flush=True)
    except BaseException as e:
        c.conn.rollback();r.update(estado='fallo; revertido',error=str(e));raise
    finally:
        (out/'INFORME.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        c.close();print('Evidencia:',out,flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--guardar',action='store_true');g.add_argument('--validar',action='store_true')
    ejecutar(p.parse_args().guardar)
