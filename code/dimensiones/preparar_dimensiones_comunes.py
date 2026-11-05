"""Prepara y audita maestros offline. No conecta a PostgreSQL ni ejecuta ETL vigente.

Uso: python -B preparar_dimensiones_comunes.py --fuentes CARPETA --extraccion ID
Las salidas son una propuesta de carga en validacion/dimensiones_comunes/.
"""
from comun.rutas import CODE as DW_CODE
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from comun.carga_segura import Entradas
from comun import db, calendario

VERSION = '2026-10-30-dimensiones-tramo-v3'
CONTRATOS = {
    'traveltramo': ('dim_tramo_comercial', 'tramo_id', {
        'sit_stretch_id': 'tramo_id', 'tramo': 'nombre_fuente', 'kilometers': 'km_fuente',
        'isactive': 'activo_fuente', 'updated': 'actualizado_fuente'}),
    'socneg': ('dim_socio_negocio', 'socio_id', {
        'c_bpartner_id': 'socio_id', 'rasonsocial': 'razon_social',
        'iscustomer': 'es_cliente_fuente', 'isvendor': 'es_proveedor_fuente',
        'forhire': 'forhire_fuente', 'ismixedfleet': 'flota_mixta_fuente',
        'isactive': 'activo_fuente', 'updated': 'actualizado_fuente'}),
    'travelveh': ('dim_equipo', 'equipo_id', {
        'a_asset_id': 'equipo_id', 'codigo': 'codigo', 'patente': 'patente',
        'vehiculo': 'nombre_fuente', 'categoryequipment': 'categoria_fuente',
        'marca': 'marca', 'modelo': 'modelo', 'manufacturedyear': 'anio_fabricacion_fuente',
        'isfleet': 'es_flota_fuente', 'isowned': 'es_propio_fuente',
        'actualstate': 'estado_fuente', 'grupoemp': 'grupo_empresa_fuente',
        'c_activity_id': 'centro_costo_id', 'sit_businessunit_id': 'unidad_negocio_id',
        'isactive': 'activo_fuente', 'updated': 'actualizado_fuente'}),
    'traveldriv': ('dim_conductor', 'conductor_id', {
        'sit_driver_v_id': 'conductor_id', 'driver_id': 'conductor_origen_id',
        'name': 'nombre_completo', 'forhire': 'forhire_fuente',
        'isactive': 'activo_fuente', 'status': 'estado_fuente', 'updated': 'actualizado_fuente'}),
    'traveltcarga': ('dim_producto_viaje', 'producto_id', {
        'm_product_id': 'producto_id', 'producto': 'producto',
        'temperaturecontrol': 'control_temperatura_fuente', 'multiload': 'multicarga_fuente',
        'emptytrip': 'viaje_vacio_fuente', 'isactive': 'activo_fuente',
        'updated': 'actualizado_fuente'}),
}


def enteros(serie, obligatorio=False):
    """Rechaza IDs inválidos; no redondea ni convierte errores en desconocidos."""
    n = pd.to_numeric(serie, errors='raise')
    if obligatorio and n.isna().any():
        raise ValueError(f'Clave nula: {serie.name}')
    if (n.dropna() % 1 != 0).any():
        raise ValueError(f'Identificador fraccionario: {serie.name}')
    return n.astype('Int64')


def maestro(df, clave, columnas):
    faltan = set(columnas) - set(df.columns)
    if faltan:
        raise ValueError(f'Faltan columnas obligatorias: {sorted(faltan)}')
    d = df[list(columnas)].rename(columns=columnas).copy()
    for c in d:
        if c.endswith('_id'):
            d[c] = enteros(d[c], obligatorio=c == clave)
    if d[clave].duplicated().any():
        raise ValueError(f'Clave duplicada: {clave}')
    return d.sort_values(clave).reset_index(drop=True)


def referencias(viajes, dimensiones):
    campos = ['c_bpartner_id', 'sit_carrier_id', 'sit_mainteam_id',
              'sit_secondaryteam_id', 'sit_driver_v_id', 'm_product_id']
    v = viajes[['c_orderline_id'] + campos].copy()
    for c in v:
        v[c] = enteros(v[c], obligatorio=c == 'c_orderline_id')
    # Repetición por factura permitida; atributos de identidad contradictorios no.
    if (v.groupby('c_orderline_id')[campos].nunique(dropna=False) > 1).any().any():
        raise ValueError('Identidades contradictorias entre filas de un viaje')
    v = v.drop_duplicates('c_orderline_id')
    reglas = [
        ('cliente', 'c_bpartner_id', 'dim_socio_negocio', 'socio_id'),
        ('transportista', 'sit_carrier_id', 'dim_socio_negocio', 'socio_id'),
        ('tracto', 'sit_mainteam_id', 'dim_equipo', 'equipo_id'),
        ('rampla', 'sit_secondaryteam_id', 'dim_equipo', 'equipo_id'),
        ('conductor', 'sit_driver_v_id', 'dim_conductor', 'conductor_id'),
        ('producto', 'm_product_id', 'dim_producto_viaje', 'producto_id')]
    informe, detalle = {}, []
    for rol, campo, tabla, clave in reglas:
        encontrado = v[campo].isin(dimensiones[tabla][clave])
        sin_id = v[campo].isna()
        informe[rol] = dict(viajes=len(v), encontrados=int(encontrado.sum()),
                            sin_id=int(sin_id.sum()), sin_catalogo=int((~encontrado & ~sin_id).sum()))
        d = v.loc[~encontrado, ['c_orderline_id', campo]].copy()
        d.columns = ['nro_viaje', 'id_fuente']
        d['rol'] = rol
        d['estado'] = d.id_fuente.isna().map({True: 'sin_id', False: 'sin_catalogo'})
        detalle.append(d)
    return informe, pd.concat(detalle, ignore_index=True)


def preparar(fuentes, extraccion, salida):
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=False)
    entradas = Entradas()
    informe = dict(version=VERSION, extraccion_id=extraccion, estado='iniciado',
                   postgresql_modificado=False)
    try:
        entradas.registrar(__file__)
        entradas.registrar(DW_CODE/'comun/carga_segura.py')
        entradas.registrar(Path(calendario.__file__))
        entradas.registrar(db.CONFIG)
        leer = entradas.lector(pd.read_parquet)
        dimensiones = {}
        for fuente, (tabla, clave, columnas) in CONTRATOS.items():
            ruta = Path(fuentes)/f'adempiere_sit_pbix_{fuente}_v.parquet'
            d = maestro(leer(ruta), clave, columnas)
            d['extraccion_id'] = extraccion
            d['fuente_sha256'] = entradas.archivos[str(ruta.resolve())]['sha256']
            dimensiones[tabla] = d
        v = leer(Path(fuentes)/'adempiere_sit_pbix_travelvta_v.parquet')
        informe['referencias'], faltantes = referencias(v, dimensiones)
        dimensiones['dim_fecha'], informe['calendario'] = calendario.desde_fuentes(
            Path(fuentes), {k: db.ruta_config('fuentes',k) for k in ('combustible','contabilidad','tablas')}, leer)
        s = dimensiones['dim_socio_negocio']
        roles = []
        for rol, campo, marca in [('cliente', 'c_bpartner_id', 'es_cliente_fuente'),
                                  ('transportista', 'sit_carrier_id', 'forhire_fuente')]:
            usado = s.socio_id.isin(enteros(v[campo]).dropna())
            incluir = usado | s[marca].eq('Y') if rol == 'cliente' else usado
            d = s.loc[incluir, ['socio_id']].copy()
            d['rol'] = rol
            d['usado_en_viajes'] = d.socio_id.isin(s.loc[usado, 'socio_id'])
            d['marca_fuente'] = d.socio_id.map(s.set_index('socio_id')[marca])
            roles.append(d)
        d = s.loc[s.es_proveedor_fuente.eq('Y'), ['socio_id']].copy()
        d['rol'], d['usado_en_viajes'], d['marca_fuente'] = 'proveedor', False, 'Y'
        roles.append(d)
        dimensiones['roles_socio'] = pd.concat(roles, ignore_index=True)
        informe['roles_transportista'] = 'uso efectivo sit_carrier_id; forhire conservado sin inferir rol'
        eq = dimensiones['dim_equipo']
        informe['perfil_equipo'] = {}
        for campo in ['codigo', 'patente', 'nombre_fuente']:
            texto = eq[campo].astype('string').str.strip()
            presentes = texto.notna() & texto.ne('')
            informe['perfil_equipo'][campo] = dict(
                vacios=int((~presentes).sum()),
                filas_con_valor_repetido=int(texto[presentes].duplicated(keep=False).sum()))
        informe['tablas'] = {k: len(d) for k, d in dimensiones.items()}
        for nombre, d in dimensiones.items():
            d.to_csv(salida/f'{nombre}.csv', index=False)
        faltantes.to_csv(salida/'referencias_no_resueltas.csv', index=False)
        entradas.verificar()
        informe['salidas'] = [dict(archivo=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                              for p in sorted(salida.glob('*.csv'))]
        informe['estado'] = 'preparado_offline'
        return informe
    except Exception as e:
        informe.update(estado='fallido', error=str(e))
        raise
    finally:
        informe['entradas'] = list(entradas.archivos.values())
        (salida/'manifiesto.json').write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fuentes', type=Path, required=True)
    p.add_argument('--extraccion', required=True)
    p.add_argument('--salida', type=Path, default=DW_CODE.parent/'validacion/dimensiones_comunes'/datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    a = p.parse_args()
    r = preparar(a.fuentes, a.extraccion, a.salida)
    print(json.dumps(dict(estado=r['estado'], tablas=r['tablas'], referencias=r['referencias'], salida=str(a.salida.resolve())), ensure_ascii=False, indent=2))
