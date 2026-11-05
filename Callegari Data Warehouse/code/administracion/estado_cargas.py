"""Consulta el diario local de intentos. No conecta ni modifica el DW."""
import argparse
import json
from comun.auditoria_operativa import listar,detalle


def fuentes_activas():
    from comun import ingesta
    c,raiz,catalogo=ingesta.configuracion()
    ruta=raiz/'estado.json'
    estado=json.loads(ruta.read_text(encoding='utf-8')) if ruta.exists() else {}
    ruta_actual=raiz/'actual.json'
    actual=json.loads(ruta_actual.read_text(encoding='utf-8')) if ruta_actual.exists() else {}
    vencidas=ingesta.pendientes(catalogo,estado,'actualizar')
    return dict(paquete_activo=actual.get('paquete'),activado_utc=actual.get('activado_utc'),
        aclaracion='La extracción incorporada no es la fecha máxima de negocio. Vigencia indica frecuencia de revisión, no completitud de datos.',
        fuentes=[dict(nombre=n,publicada=n in estado,ultima_extraccion_utc=estado.get(n,{}).get('extraida_utc'),
            filas=estado.get(n,{}).get('filas'),marca_cambios_utc=estado.get(n,{}).get('marca_cambios_utc'),
            cada_horas=s['cada_horas'],requiere_revision=n in vencidas) for n,s in catalogo.items()])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ultimas',type=int,default=10)
    modo=p.add_mutually_exclusive_group()
    modo.add_argument('--id',help='Detalle y subintentos de un identificador de auditoría')
    modo.add_argument('--fuentes',action='store_true',help='Estado ERP incorporado en la última publicación')
    a=p.parse_args()
    if a.ultimas<1:p.error('--ultimas debe ser positivo')
    r=fuentes_activas() if a.fuentes else detalle(a.id) if a.id else listar(limite=a.ultimas)
    print(json.dumps(r,ensure_ascii=False,indent=2))
