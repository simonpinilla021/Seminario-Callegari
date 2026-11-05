"""Descarga ERP por frecuencia; sin opciones muestra plan y no conecta.

--probar verifica permisos/columnas sin descargar filas.
--descargar conserva originales; --muestra nunca puede alimentar ETL.
"""
import argparse
import json
from pathlib import Path
from comun import ingesta
from comun import auditoria_operativa as auditoria


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    modos=p.add_mutually_exclusive_group()
    modos.add_argument('--plan',action='store_true')
    modos.add_argument('--probar',action='store_true')
    modos.add_argument('--descargar',action='store_true')
    p.add_argument('--modo',choices=['inicial','actualizar'],default='actualizar')
    p.add_argument('--solo',nargs='+')
    p.add_argument('--muestra',type=int)
    p.add_argument('--empaquetar',action='store_true')
    p.add_argument('--base-paquete',type=Path,help='Fotografía preparada como base; no activa datos en DW')
    a=p.parse_args()
    if a.muestra and not a.descargar: p.error('--muestra requiere --descargar')
    if a.empaquetar and (not a.descargar or a.muestra): p.error('--empaquetar requiere descarga completa')
    c,raiz,fuentes=ingesta.configuracion()
    estado=json.loads((raiz/'estado.json').read_text(encoding='utf-8')) if (raiz/'estado.json').exists() else {}
    if a.base_paquete:
        if a.modo=='inicial': p.error('--base-paquete requiere --modo actualizar')
        estado=ingesta.fuentes_paquete(a.base_paquete,fuentes)
    if not (a.probar or a.descargar):
        pendientes=ingesta.pendientes(fuentes,estado,a.modo)
        print(json.dumps(dict(modo=a.modo,fuentes=[dict(nombre=n,relacion=s['relacion'],cada_horas=s['cada_horas'],filtro_fecha=ingesta.filtro_fecha(s),
            actualizacion=ingesta.incremental.planificar(s,estado.get(n) if a.modo=='actualizar' else None,ingesta.ahora(),c.getint('ingesta','ventana_meses',fallback=5),c.getint('ingesta','solape_cambios_horas',fallback=48)),
            pendiente=n in pendientes) for n,s in fuentes.items()],
            revisiones_externas={s.split(':',1)[1]:dict(c[s]) for s in c.sections() if s.startswith('revision:')}),ensure_ascii=False,indent=2))
        return dict(estado='plan_sin_ejecucion')
    try:
        run,informe=ingesta.ejecutar(a.modo,a.solo,a.muestra,a.probar,base_paquete=a.base_paquete)
    except RuntimeError as e:
        raise SystemExit(str(e)) from None
    print(json.dumps(informe,ensure_ascii=False,indent=2))
    print(f'Manifiesto: {run/"manifiesto.json"}')
    if a.empaquetar:
        print('Paquete: '+str(ingesta.empaquetar(run)))
    if a.probar and any(v['estado']=='fallido' for v in informe.get('pruebas',{}).values()):
        raise SystemExit(1)
    return informe


if __name__=='__main__':
    auditoria.ejecutar_cli('descarga_erp',principal)
