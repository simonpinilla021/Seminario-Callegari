"""Contrato administrativo de parámetros; los CSV siguen siendo entrada del cálculo.

El registro es evidencia de versiones, no una segunda fuente de valores efectivos.
No confirma económicamente valores heredados ni resuelve diferencias entre políticas.
"""
import csv
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from comun.rutas import CODE

CONTRATO=CODE/'parametros/contrato.json'
REGISTRO=CODE/'parametros/registro_versiones.json'


def leer_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def huella(datos):
    return hashlib.sha256(json.dumps(datos,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def filas_csv(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def normalizar(fila,familia,unidad,contrato):
    codigo=fila['parametro'].strip()
    metodo=fila.get('metodo','cotizacion').strip() or 'tarificador'
    ambito=fila['ambito'].strip() or 'todos'
    if metodo not in contrato['metodos'][familia]:
        raise ValueError('Método fuera del contrato: '+metodo)
    valor=Decimal(fila['valor'])
    if not valor.is_finite(): raise ValueError('Valor no finito: '+codigo)
    if codigo in contrato['positivos'] and valor<=0:
        raise ValueError('Debe ser positivo: '+codigo)
    if (codigo.startswith('pct_') and not 0<=valor<=1) or (codigo=='cobra_intermedios' and valor not in (0,1)):
        raise ValueError('Valor fuera del dominio: '+codigo)
    desde=date.fromisoformat(fila['vigente_desde'].strip() or '1900-01-01')
    hasta=date.fromisoformat(fila['vigente_hasta'].strip() or '9999-12-31')
    if desde>=hasta: raise ValueError('Intervalo vacío o invertido: '+codigo)
    if unidad not in contrato['unidades']:
        raise ValueError('Unidad fuera del contrato: '+unidad)
    canon=contrato['unidades'][unidad]
    return dict(familia=familia,politica=familia+'.'+metodo,codigo=codigo,metodo=metodo,ambito=ambito,
        valor=str(valor.normalize()),unidad=canon['unidad'],moneda=canon['moneda'],
        vigente_desde=desde.isoformat(),vigente_hasta=hasta.isoformat())


def actuales(code=CODE,contrato=None):
    code=Path(code)
    contrato=contrato or leer_json(code/'parametros/contrato.json')
    catalogo=filas_csv(code/'tarificador/dim_parametro.csv')
    codigos=[r['codigo'] for r in catalogo]
    if len(codigos)!=len(set(codigos)): raise ValueError('Catálogo de parámetros duplicado')
    unidades={r['codigo']:r['unidad'] for r in catalogo}
    filas=[]
    for familia,ruta in contrato['fuentes'].items():
        for i,f in enumerate(filas_csv(code/ruta),2):
            unidad=f['unidad'] if familia=='costos' else unidades[f['parametro']]
            dato=normalizar(f,familia,unidad,contrato)
            dato.update(archivo=ruta,fila_csv=i,
                fuente_texto=f.get('fuente_detalle','') or next((r['referencia_tarificador'] for r in catalogo if r['codigo']==f['parametro']),''),
                nota_heredada=f.get('nota',''))
            dato['version_id']=huella({k:v for k,v in dato.items() if k!='fila_csv'})
            filas.append(dato)
    comprobar_intervalos(filas)
    return filas


def comprobar_intervalos(filas):
    grupos={}
    for f in filas:
        k=(f['politica'],f['codigo'],f['ambito'])
        grupos.setdefault(k,[]).append(f)
    for clave,g in grupos.items():
        limite=None
        for f in sorted(g,key=lambda r:r['vigente_desde']):
            if limite is not None and f['vigente_desde']<limite:
                raise ValueError('Versiones solapadas: '+str(clave))
            limite=max(limite or f['vigente_hasta'],f['vigente_hasta'])


def validar_registro(filas,registro):
    por_id={}
    for v in registro['versiones']:
        if v['version_id'] in por_id: raise ValueError('Versión repetida en registro')
        datos={k:v[k] for k in ['familia','politica','codigo','metodo','ambito','valor','unidad','moneda','vigente_desde','vigente_hasta','archivo','fuente_texto','nota_heredada']}
        if huella(datos)!=v['version_id']: raise ValueError('Contenido de versión alterado sin nueva identidad')
        if date.fromisoformat(v['vigente_desde'])>=date.fromisoformat(v['vigente_hasta']):
            raise ValueError('Versión histórica con intervalo vacío o invertido')
        if not Decimal(v['valor']).is_finite(): raise ValueError('Versión histórica con valor no finito')
        if v['estado_revision'] not in ('sin_revision','supuesto','confirmado'):
            raise ValueError('Estado administrativo desconocido')
        date.fromisoformat(v['registrado_el'])
        if v.get('fecha_valoracion'): date.fromisoformat(v['fecha_valoracion'])
        if v['estado_revision']=='confirmado' and not all(v.get(k) for k in ['fecha_valoracion','responsable_confirmacion','evidencia_documental']):
            raise ValueError('Confirmación sin responsable, fecha o evidencia')
        if not v.get('motivo_registro'): raise ValueError('Versión sin motivo de registro')
        por_id[v['version_id']]=v
    for v in por_id.values():
        anterior=v.get('reemplaza_version_id')
        if anterior:
            if anterior not in por_id or anterior==v['version_id']:
                raise ValueError('Versión reemplazada inexistente o autorreferencia')
            if any(v[k]!=por_id[anterior][k] for k in ('politica','codigo','ambito')):
                raise ValueError('Reemplazo entre identidades diferentes')
        visitados=set()
        actual=v
        while actual.get('reemplaza_version_id'):
            if actual['version_id'] in visitados: raise ValueError('Ciclo de reemplazos administrativos')
            visitados.add(actual['version_id'])
            anterior=actual['reemplaza_version_id']
            if anterior not in por_id: raise ValueError('Reemplazo inexistente')
            actual=por_id[anterior]
    for f in filas:
        if f['version_id'] not in por_id:
            raise ValueError('Parámetro sin versión administrativa registrada: '+str((f['politica'],f['codigo'],f['ambito'])))
        for k in ('archivo','fuente_texto','nota_heredada'):
            if f[k]!=por_id[f['version_id']][k]:
                raise ValueError('Cambió la documentación de origen; actualizar trazabilidad: '+f['codigo'])
    return por_id


def validar(code=CODE):
    code=Path(code)
    contrato=leer_json(code/'parametros/contrato.json')
    if contrato.get('version')!='2026-10-30-contrato-parametros-v1':
        raise ValueError('Versión de contrato no admitida')
    filas=actuales(code,contrato)
    registro=leer_json(code/'parametros/registro_versiones.json')
    if registro.get('contrato_version')!=contrato['version']:
        raise ValueError('Registro de otra versión de contrato')
    versiones=validar_registro(filas,registro)
    estados={estado:sum(versiones[f['version_id']]['estado_revision']==estado for f in filas)
             for estado in ('sin_revision','supuesto','confirmado')}
    return dict(version=contrato['version'],versiones_actuales=len(filas),estados=estados,
        decisiones_abiertas=contrato['decisiones_abiertas'],
        contrato_sha256=hashlib.sha256((code/'parametros/contrato.json').read_bytes()).hexdigest(),
        registro_sha256=hashlib.sha256((code/'parametros/registro_versiones.json').read_bytes()).hexdigest())
