"""Valida contrato y versiones administrativas offline. No modifica CSV ni PostgreSQL."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from comun.contrato_parametros import validar
from comun.carga_segura import Entradas
from comun.rutas import PROYECTO, CODE


def principal():
    carpeta=PROYECTO/'validacion/contrato_parametros'/(
        datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    carpeta.mkdir(parents=True)
    r=dict(estado='iniciada',modifica_dw=False)
    entradas=Entradas()
    try:
        for p in [__file__,CODE/'comun/contrato_parametros.py',CODE/'comun/carga_segura.py',
                  CODE/'parametros/contrato.json',CODE/'parametros/registro_versiones.json',
                  CODE/'costos/parametros_costo_manual.csv',CODE/'tarificador/parametros_valor_manual.csv',
                  CODE/'tarificador/dim_parametro.csv']:
            entradas.registrar(p)
        r.update(validar())
        entradas.verificar()
        r['estado']='contrato_valido_sin_cambios_financieros'
        print(json.dumps(r,ensure_ascii=False,indent=2))
        return r
    except BaseException as e:
        r.update(estado='fallido',error=str(e))
        raise
    finally:
        r['entradas']=list(entradas.archivos.values())
        (carpeta/'informe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Informe:',carpeta/'informe.json')


if __name__=='__main__':
    principal()
