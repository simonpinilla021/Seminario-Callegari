"""Reproduce una ventana de viajes con un paquete real, sin red ni PostgreSQL.

Comprueba que consolidar los mismos registros no altera filas/importes ni el origen.
El resultado es una simulación marcada, no una extracción publicable.
"""
import argparse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from comun import ingesta,incremental


def probar(paquete):
    c,raiz,catalogo=ingesta.configuracion()
    fuentes=ingesta.fuentes_paquete(paquete,catalogo)
    fuente=catalogo['viajes'];origen=Path(fuentes['viajes']['ruta'])
    run=raiz/'pruebas_incrementales'/(ingesta.ahora().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    run.mkdir(parents=True)
    plan=dict(tipo='incremental',desde=incremental.inicio_ventana(ingesta.ahora(),c.getint('ingesta','ventana_meses',fallback=5)).isoformat())
    antes=ingesta.huella(origen)
    tabla=pq.read_table(origen)
    fechas=tabla[fuente['columna_fecha']].to_pandas()
    ids=tabla[fuente['clave']].to_pandas()
    recientes=ids[pd.to_datetime(fechas).ge(pd.Timestamp(plan['desde']))]
    # Grupo completo: conservar todas las facturas de los viajes seleccionados.
    mascara=ids.isin(recientes)
    delta=run/'delta_viajes.parquet';salida=run/'consolidado_viajes.parquet'
    pq.write_table(tabla.filter(pa.array(mascara)),delta,compression='snappy')
    controles=incremental.consolidar(origen,delta,fuente,salida,plan,c.getint('ingesta','lote_filas'))
    # Multiconjunto de filas: conserva también duplicados legítimos y todos los importes.
    def huellas(t):
        d=t.to_pandas()
        return pd.util.hash_pandas_object(d,index=False).sort_values().reset_index(drop=True)
    pd.testing.assert_series_equal(huellas(tabla),huellas(pq.read_table(salida)))
    if antes!=ingesta.huella(origen): raise ValueError('Se alteró el paquete original')
    informe=dict(estado='simulacion_local_correcta_no_activable',red=False,postgres=False,
        origen=str(origen),origen_sha256=antes,ventana=plan,controles=controles,
        todas_las_filas_e_importes_conservados=True)
    ingesta.escribir_json(run/'informe.json',informe)
    return run,informe


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--paquete',type=Path,required=True)
    p.add_argument('--ejecutar',action='store_true')
    a=p.parse_args()
    if not a.ejecutar: print('Plan: simulación local de viajes; no consulta ERP ni DW.')
    else:
        run,r=probar(a.paquete)
        print(r['estado']);print(r['controles']);print('Informe: '+str(run/'informe.json'))
