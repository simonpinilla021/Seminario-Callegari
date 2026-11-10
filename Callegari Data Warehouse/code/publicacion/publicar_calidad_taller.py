"""Publicación aditiva de calidad de taller. --validar revierte; --guardar confirma."""
import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from comun import db
from comun.carga_segura import Entradas
from comun.rutas import PROYECTO, CODE
from taller import calidad


def huellas_hechos(con):
    claves={'fact_orden_taller':'sit_workorder_id','fact_consumo_repuesto':'consumo_id',
            'fact_estado_diario_equipo':'equipo_id,sk_fecha'}
    return {tabla:list(con.uno(f"SELECT count(*),md5(string_agg(md5(row_to_json(x)::text),'' ORDER BY {pk})) FROM taller.{tabla} x"))
            for tabla,pk in claves.items()}


def ejecutar(guardar=False):
    identificador=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    carpeta=PROYECTO/'validacion/calidad_taller'/identificador
    carpeta.mkdir(parents=True)
    r=dict(id=identificador,fecha=datetime.now(timezone.utc).isoformat(),estado='iniciada',confirmado=False)
    entradas,con=Entradas(),None
    try:
        for p in [__file__,calidad.__file__,db.__file__,db.CONFIG,CODE/'comun/carga_segura.py',CODE/'taller/construir_dw_taller.py']:
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
        antes=huellas_hechos(con)
        r['pruebas_semantica']=calidad.probar_semantica(con)
        r.update(calidad.publicar(con))
        for nombre in ['v_calidad_ot_mensual','v_cobertura_preventiva_mensual','v_cobertura_imputacion_mensual']:
            con.df('SELECT * FROM taller.'+nombre).to_csv(carpeta/(nombre+'.csv'),index=False)
        if antes!=huellas_hechos(con):
            raise ValueError('Cambió el contenido de hechos de taller')
        r['hechos_conservados']=antes
        r['preventivo']=con.df('SELECT motivo_cobertura,count(*) AS ordenes FROM taller.v_cobertura_preventiva GROUP BY 1').to_dict('records')
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
        if con is not None:
            con.conn.rollback()
        r.update(estado='fallido',confirmado=False,error=str(e))
        raise
    finally:
        if con is not None:
            con.close()
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
        print('Plan: cinco vistas de calidad/cobertura en taller; no modifica hechos. Sin conexión.')
