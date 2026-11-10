"""Lectura de la única definición de ciclo: Santiago con cortes en Coquimbo."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from comun import db


def principal(argv=None, _conn=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--desde',default='2018-01-01')
    a=p.parse_args(argv)
    from datetime import date
    desde=date.fromisoformat(a.desde)
    con=db.conectar(_conn=_conn)
    try:
        d=con.df("""SELECT motivo_apertura,motivo_cierre,estado,count(*) AS ciclos,
          sum(viajes) AS movimientos,sum(viajes_virtuales) AS virtuales
          FROM ciclos.dim_ciclo_comercial WHERE fecha_inicio>=?
          GROUP BY 1,2,3 ORDER BY 4 DESC""",(desde,))
        print('Ciclos Santiago con cortes en Coquimbo; fechas documentales.')
        print(d.to_string(index=False))
    finally:
        con.close()


if __name__=='__main__':
    principal()
