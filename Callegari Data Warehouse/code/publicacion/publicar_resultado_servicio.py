"""Publica resultado por servicio/escenario. --validar revierte; --guardar confirma.

Contrasta reglas financieras con la consulta anterior, sin abrir ni modificar Power BI.
"""
from comun.rutas import CODE as DW_CODE
import argparse
import ast
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from comun import db, resultado_servicio
from comun.carga_segura import Entradas


def referencia(ruta):
    arbol=ast.parse(ruta.read_text(encoding='utf-8'))
    for n in arbol.body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SQL_FACT' for t in n.targets):
            return ast.literal_eval(n.value)
    raise ValueError('Falta consulta de referencia financiera')


CONTRASTE = '''SELECT nro_viaje::text AS viaje_id,escenario_id,ingreso_estimado,
 costo_fisico AS costo,margen_fisico AS margen,evaluable_fisico::int AS evaluable,
 subtotal_conocido_fisico AS costo_conocido,componentes_presentes,componentes_esperados AS esperadas,
 motivo_fisico AS estado,costo_comercial,margen_comercial,evaluable_comercial::int AS evaluable_comercial,
 subtotal_conocido_comercial AS costo_comercial_conocido,motivo_comercial AS estado_comercial
 FROM ds_gestion.v_resultado_servicio WHERE fecha_documental_id BETWEEN 20250101 AND 20261026'''


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    modos=p.add_mutually_exclusive_group()
    modos.add_argument('--validar',action='store_true')
    modos.add_argument('--guardar',action='store_true')
    a=p.parse_args()
    code=DW_CODE
    carpeta=code.parent/'validacion/resultado_servicio'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    carpeta.mkdir(parents=True)
    entradas,con=Entradas(),None
    informe=dict(version=resultado_servicio.VERSION,estado='iniciado',confirmado=False)
    try:
        legado=code/'desarrollo/powerbi/servicios_comercial_v9.py'
        for f in [Path(__file__),Path(resultado_servicio.__file__),Path(db.__file__),
                  code/'comun/carga_segura.py',db.CONFIG,legado]:
            entradas.registrar(f)
        consulta_anterior=referencia(legado)
        con=db.conectar()
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='15min'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra carga está en curso')
        informe.update(resultado_servicio.publicar(con))
        print('Contrastando contra las reglas financieras anteriores...',flush=True)
        anterior=con.df(consulta_anterior).sort_values(['viaje_id','escenario_id']).reset_index(drop=True)
        nuevo=con.df(CONTRASTE).sort_values(['viaje_id','escenario_id']).reset_index(drop=True)
        # Desde 02-11-2026: 1/2 pertenecen al tarificador exacto; el contraste legado solo cubre 3/4.
        anterior=anterior[~anterior.escenario_id.isin([1,2])].reset_index(drop=True)
        nuevo=nuevo[~nuevo.escenario_id.isin([1,2])].reset_index(drop=True)
        pd.testing.assert_frame_equal(anterior,nuevo,check_dtype=False,check_exact=False,rtol=0,atol=0.01)
        informe.update(reglas_anteriores_conservadas=True,filas_contrastadas=len(nuevo),
            ventana_contraste='2025-01-01 a 2026-10-26; la vista publicada no tiene este corte')
        informe['resumen']=con.df('''SELECT escenario_id,count(*) AS servicios,
            count(*) FILTER(WHERE evaluable_fisico) AS evaluables_fisicos,
            count(*) FILTER(WHERE evaluable_comercial) AS evaluables_comerciales
            FROM ds_gestion.v_resultado_servicio GROUP BY 1 ORDER BY 1''').to_dict('records')
        entradas.verificar()
        if a.guardar:
            con.commit()
            informe.update(estado='confirmado',confirmado=True)
        else:
            con.conn.rollback()
            informe['estado']='validado_y_revertido'
    except BaseException as e:
        if con is not None:
            con.conn.rollback()
        informe.update(estado='fallido',error=str(e))
        raise
    finally:
        if con is not None:
            con.close()
        informe['entradas']=list(entradas.archivos.values())
        (carpeta/'ejecucion.json').write_text(json.dumps(informe,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Informe: {carpeta}')
    print(json.dumps(informe,ensure_ascii=False,indent=2))


if __name__=='__main__':
    principal()
