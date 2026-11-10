"""Publicación aditiva V34: solo dos vistas de referencia; sin DML de hechos."""
from pathlib import Path
from datetime import datetime
import sys,json,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from comun import db
from publicacion.costos_absorcion import publicar
ROOT=Path(__file__).resolve().parents[2]
def run(guardar=False):
 out=ROOT/'validacion/costos_absorcion'/datetime.now().strftime('%Y%m%d_%H%M%S');out.mkdir(parents=True)
 c=db.conectar();r=dict(confirmado=False)
 try:
  if c.conn.info.host.lower() not in ('localhost','127.0.0.1','::1'):raise RuntimeError('Solo DW local')
  c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
  c.execute("SET LOCAL statement_timeout='5min'");c.execute("SET LOCAL lock_timeout='5s'")
  if not c.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:raise RuntimeError('Constructor activo')
  q='''SELECT sk_escenario,count(*) filas,sum(monto::numeric) importe,count(*) FILTER(WHERE monto IS NULL) faltantes FROM escenarios.fact_costo_viaje GROUP BY 1 ORDER BY 1'''
  before=c.df(q).to_json(orient='records')
  r.update(publicar(c))
  assert before==c.df(q).to_json(orient='records')
  r['hechos_conservados']=True
  for name,view in [('contabilidad','v_rubros_contables_mensual'),('capacidad','v_capacidad_flota_modelada_mensual')]:
   d=c.df('SELECT * FROM ds_gestion.'+view+' ORDER BY mes_id');d.to_csv(out/(name+'.csv'),index=False)
   r[name+'_filas']=len(d)
  if guardar:c.commit();r.update(confirmado=True,estado='confirmado')
  else:c.conn.rollback();r['estado']='validado; revertido'
  print(json.dumps(r,ensure_ascii=False,indent=2))
 except BaseException as e:c.conn.rollback();r.update(estado='fallo; revertido',error=str(e));raise
 finally:(out/'INFORME.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str),encoding='utf-8');c.close();print('Evidencia:',out)
if __name__=='__main__':
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--guardar',action='store_true');g.add_argument('--validar',action='store_true');run(p.parse_args().guardar)
