"""Publicación aditiva protegida: --validar revierte; --guardar confirma tras controles."""
from pathlib import Path
from datetime import datetime
import argparse,json,hashlib
from comun import db
from publicacion import contrato_dashboard

def principal():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--guardar',action='store_true');p.add_argument('--validar',action='store_true')
    a=p.parse_args()
    if a.guardar and a.validar:p.error('Elegir un solo modo')
    root=Path(__file__).resolve().parents[2]
    out=root/'validacion/contrato_dashboard'/datetime.now().strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True)
    info={'estado':'iniciado','confirmado':False,'version':contrato_dashboard.VERSION}
    con=db.conectar()
    try:
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='5min'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:raise RuntimeError('Otra carga DW en curso')
        info['movimiento_antes']=con.uno("SELECT pg_get_viewdef('ds_gestion.v_movimiento'::regclass,true)")[0]
        q='SELECT count(*),sum(monto::numeric),sum(monto_comercial::numeric) FROM escenarios.fact_costo_viaje'
        before=con.uno(q)
        info.update(contrato_dashboard.publicar(con))
        if before!=con.uno(q):raise ValueError('Cambió un hecho financiero')
        info['hechos_financieros_sin_cambios']=True
        info['archivo_sha256']=hashlib.sha256(Path(contrato_dashboard.__file__).read_bytes()).hexdigest()
        if a.guardar:
            con.commit();info.update(estado='confirmado',confirmado=True)
        else:con.conn.rollback();info['estado']='validado_y_revertido'
    except BaseException as exc:
        con.conn.rollback();info.update(estado='fallido',error=str(exc));raise
    finally:
        con.close();(out/'ejecucion.json').write_text(json.dumps(info,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(out,flush=True)
    print(json.dumps({k:v for k,v in info.items() if k!='movimiento_antes'},ensure_ascii=False,indent=2))

if __name__=='__main__':principal()
