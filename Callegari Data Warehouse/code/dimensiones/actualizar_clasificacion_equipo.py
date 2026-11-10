"""Actualiza solo vistas de tipo de equipo. --validar revierte; --guardar confirma."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
from comun import db, tipos_equipo
from comun.carga_segura import Entradas
from comun.rutas import PROYECTO, CODE
from taller import equipo_comun


def ejecutar(guardar=False):
    identificador=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    carpeta=PROYECTO/'validacion/clasificacion_equipo'/identificador
    carpeta.mkdir(parents=True)
    r=dict(id=identificador,fecha=datetime.now(timezone.utc).isoformat(),version=tipos_equipo.VERSION,
           estado='iniciada',confirmado=False,alcance='vistas de equipo común y taller')
    entradas=Entradas()
    con=None
    try:
        for p in [__file__,tipos_equipo.__file__,equipo_comun.__file__,db.__file__,db.CONFIG,
                  CODE/'dimensiones/cargar_dimensiones_comunes.py',CODE/'taller/construir_dw_taller.py',
                  CODE/'comun/carga_segura.py']:
            entradas.registrar(p)
        cfg=db.leer_config()['postgres']
        if cfg.get('host','localhost').lower() not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Actualización limitada a PostgreSQL local')
        r['base']=cfg.get('dbname')
        con=db.conectar()
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='60s'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra carga está en curso')
        inventario=con.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')")
        antes=con.df('SELECT * FROM dim_comun.v_dim_equipo ORDER BY equipo_id')
        taller_antes=con.df('SELECT * FROM taller.dim_equipo ORDER BY equipo_id')
        # Solo CREATE OR REPLACE VIEW: sin DROP CASCADE ni reconstrucción de hechos.
        con.execute(tipos_equipo.vistas_comunes_sql())
        con.execute(equipo_comun.vista_sql().replace('CREATE VIEW','CREATE OR REPLACE VIEW',1))
        despues=con.df('SELECT * FROM dim_comun.v_dim_equipo ORDER BY equipo_id')
        esperado=antes.copy()
        esperado['tipo_equipo_derivado']=tipos_equipo.clasificar(esperado.categoria_fuente)
        pd.testing.assert_frame_equal(esperado,despues,check_dtype=False)
        taller_despues=con.df('SELECT * FROM taller.dim_equipo ORDER BY equipo_id')
        esperado_taller=taller_antes.copy()
        mapa=despues.set_index('equipo_id').tipo_equipo_derivado
        compartidos=esperado_taller.equipo_id.isin(mapa.index)
        esperado_taller.loc[compartidos,'tipo_equipo']=esperado_taller.loc[compartidos,'equipo_id'].map(mapa)
        equipo_comun.comparar(esperado_taller,taller_despues)
        nuevo=con.df("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')")
        gestionadas={'v_dim_equipo','v_dim_tracto','v_dim_rampla','v_dim_otros_equipos'}
        def externas(d):
            mascara=(d.schemaname.eq('dim_comun') & d.viewname.isin(gestionadas)) | (d.schemaname.eq('taller') & d.viewname.eq('dim_equipo'))
            return d.loc[~mascara].sort_values(['schemaname','viewname']).reset_index(drop=True)
        pd.testing.assert_frame_equal(externas(inventario),externas(nuevo))
        total=sum(con.uno('SELECT count(*) FROM dim_comun.'+nombre)[0] for nombre in ['v_dim_tracto','v_dim_rampla','v_dim_otros_equipos'])
        if total!=len(antes):
            raise ValueError('La partición de tipos pierde o multiplica equipos')
        cambio=antes.tipo_equipo_derivado.ne(despues.tipo_equipo_derivado)
        detalle=antes.loc[cambio,['equipo_id','categoria_fuente','tipo_equipo_derivado']].copy()
        detalle=detalle.rename(columns={'tipo_equipo_derivado':'tipo_anterior'})
        detalle['tipo_nuevo']=despues.loc[cambio,'tipo_equipo_derivado']
        detalle.to_csv(carpeta/'cambios.csv',index=False)
        if not detalle.categoria_fuente.astype('string').str.strip().str.upper().eq('FURGON').all():
            raise ValueError('Hay cambios fuera de FURGON; revisar el alcance')
        # Verificación real de coherencia Python/PostgreSQL, incluyendo NULL.
        categorias=[' tracto camion ','thermo','PLANA','CAMA BAJA','furgon','CHASIS','CAMION',None,'']
        expresion=tipos_equipo.expresion_sql('c').replace('%','%%')
        leido=con.df('SELECT c,'+expresion+' AS tipo FROM unnest(%s::text[]) WITH ORDINALITY t(c,n) ORDER BY n',(categorias,))
        if leido.tipo.tolist()!=tipos_equipo.clasificar(pd.Series(categorias)).tolist():
            raise ValueError('Python y SQL clasifican de forma diferente')
        r.update(equipos_comunes=len(despues),equipos_taller=len(taller_despues),equipos_reclasificados=len(detalle),
                 tipos=despues.tipo_equipo_derivado.value_counts().to_dict(),
                 otros_atributos_conservados=True,vistas_dependientes_conservadas=True,coherencia_python_sql=True)
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
        print(json.dumps(ejecutar(a.guardar),ensure_ascii=False,indent=2))
    else:
        print('Plan: unificar tipo de equipo, FURGON como rampla; CHASIS permanece otro. Sin conexión ni cambios.')
