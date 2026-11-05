"""Inventario de tamaños sin borrado. --dw agrega PostgreSQL local de solo lectura."""
import argparse
import json
from comun.almacenamiento import medir


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dw',action='store_true')
    a=p.parse_args()
    _,r=medir(a.dw)
    print(json.dumps(dict(estado=r['estado'],archivos_MB=r['archivos']['MB'],
        archivos_GiB=r['archivos']['GiB'],categorias=r['archivos']['categorias'],
        dw=r['dw'],comparacion=r['comparacion'],borra_archivos=False),ensure_ascii=False,indent=2))
