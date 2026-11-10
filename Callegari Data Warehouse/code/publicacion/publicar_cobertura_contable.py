"""Agrega vistas de cobertura contable. --validar revierte; --guardar confirma."""
import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4
import pandas as pd
from comun import db
from comun.carga_segura import Entradas
from comun.rutas import PROYECTO, CODE
from costos import cobertura_referencias as cobertura


def huellas(con):
    claves={'fact_contabilidad_mensual':'anio,mes,sk_cuenta,unidad_negocio_id,c_activity_id',
            'fact_viaje_costo_indirecto':'nro_viaje,metodo',
            'parametro_costo':'parametro,metodo,ambito,vigente_desde',
            'fact_capacidad_bolsa':'anio,bolsa','dim_centro_costo':'c_activity_id'}
    return {t:list(con.uno(f"SELECT count(*),md5(string_agg(md5(row_to_json(x)::text),'' ORDER BY {k})) FROM costos.{t} x"))
            for t,k in claves.items()}


def ejecutar(guardar=False):
    identificador=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    carpeta=PROYECTO/'validacion/cobertura_contable'/identificador
    carpeta.mkdir(parents=True)
    r=dict(id=identificador,fecha=datetime.now(timezone.utc).isoformat(),estado='iniciada',confirmado=False)
    entradas,con=Entradas(),None
    try:
        for p in [__file__,cobertura.__file__,db.__file__,db.CONFIG,CODE/'comun/carga_segura.py',CODE/'costos/construir_dw_costos.py']:
            entradas.registrar(p)
        cfg=db.leer_config()['postgres']
        if cfg.get('host','localhost').lower() not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Publicación limitada a PostgreSQL local')
        r['base']=cfg.get('dbname')
        con=db.conectar()
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='90s'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra carga está en curso')
        antes=huellas(con)
        vistas_antes=con.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')")
        r['pruebas_semantica']=cobertura.probar_semantica(con)
        r.update(cobertura.publicar(con))
        for v in ['v_ref_centro_contable','v_ref_unidad_negocio','v_cobertura_referencias_mensual']:
            con.df('SELECT * FROM costos.'+v).to_csv(carpeta/(v+'.csv'),index=False)
        if antes!=huellas(con):
            raise ValueError('Cambió el contenido de tablas financieras o maestro existente')
        vistas_despues=con.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')")
        def externas(d):
            return d[~(d.schemaname.eq('costos') & d.viewname.isin(cobertura.NOMBRES))].sort_values(['schemaname','viewname']).reset_index(drop=True)
        pd.testing.assert_frame_equal(externas(vistas_antes),externas(vistas_despues))
        r['tablas_conservadas']=antes
        r['vistas_anteriores_conservadas']=True
        r['correspondencia_un']=con.df('SELECT estado_correspondencia,count(*) AS unidades FROM costos.v_ref_unidad_negocio GROUP BY 1').to_dict('records')
        entradas.verificar()
        if guardar:
            r.update(estado='confirmado',confirmado=True)
            con.execute('INSERT INTO comun.registro_ejecucion(id,fecha,manifiesto) VALUES(%s,%s,%s::jsonb)',
                        (identificador,r['fecha'],json.dumps(r,ensure_ascii=False)))
            con.commit()
        else:
            con.conn.rollback()
            r['estado']='validado_y_revertido'
        return r
    except BaseException as e:
        if con is not None: con.conn.rollback()
        r.update(estado='fallido',confirmado=False,error=str(e))
        raise
    finally:
        if con is not None: con.close()
        r['entradas']=list(entradas.archivos.values())
        (carpeta/'informe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Informe:',carpeta/'informe.json',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group()
    g.add_argument('--validar',action='store_true')
    g.add_argument('--guardar',action='store_true')
    a=p.parse_args()
    if a.validar or a.guardar:
        print(json.dumps(ejecutar(a.guardar),ensure_ascii=False,indent=2,default=str))
    else:
        print('Plan: vistas aditivas de referencias y cobertura. Sin conexión, reasignaciones ni cambios financieros.')
