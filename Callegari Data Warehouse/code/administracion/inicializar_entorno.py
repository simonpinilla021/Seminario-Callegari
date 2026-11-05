"""Prepara config.ini en un equipo nuevo sin reemplazar configuración ni descargar datos."""
from comun.rutas import CODE as DW_CODE
import argparse
import json
from pathlib import Path

CODE=DW_CODE


def inicializar(code=CODE,crear=False):
    code=Path(code)
    config=code/'config.ini'
    creado=False
    if crear and not config.exists():
        with config.open('xb') as salida:
            salida.write((code/'config.ejemplo.ini').read_bytes())
        creado=True
    return dict(configuracion_existe=config.exists(),configuracion_creada=creado,
        pdf_peajes=len(list((code/'peajes/peajes_datos').rglob('*.pdf'))),
        pdf_copec=len(list((code/'precios/cartas_copec').rglob('*.pdf'))),
        parametros_manuales=len(list(code.rglob('*manual*.csv'))),
        siguiente='Configurar PostgreSQL/ERP y credenciales por usuario; luego descargar lote inicial. '
                  'PostgreSQL y Python deben estar instalados. Conservar los archivos base y el histórico necesarios.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--crear-config',action='store_true')
    print(json.dumps(inicializar(crear=p.parse_args().crear_config),ensure_ascii=False,indent=2))
