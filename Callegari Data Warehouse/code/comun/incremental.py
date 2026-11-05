"""Ventana reciente, correcciones y consolidación inmutable de fuentes Parquet.

No borra ausencias de una vista: conserva el registro anterior y las informa.
Reemplaza grupos completos (por ejemplo, todas las facturas de un viaje).
"""
from datetime import date,datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def inicio_ventana(fecha,meses=5,zona='America/Santiago'):
    if meses<1: raise ValueError('La ventana debe tener al menos un mes')
    if isinstance(fecha,datetime) and fecha.tzinfo is not None: fecha=fecha.astimezone(ZoneInfo(zona))
    indice=fecha.year*12+fecha.month-1-(meses-1)
    return date(indice//12,indice%12+1,1)


def planificar(s,base,fecha,meses=5,solape_horas=48):
    if not s.get('incremental'):
        return dict(tipo='completa',motivo='maestro o grano incremental no habilitado')
    if not base:
        return dict(tipo='completa',motivo='sin fotografía base')
    if not base.get('marca_cambios_local') or not base.get('marca_cambios_utc'):
        return dict(tipo='completa',motivo='base anterior sin marca transaccional; establecer marca segura')
    if solape_horas<0: raise ValueError('Solape de cambios negativo')
    oid=base.get('tipos_postgres',{}).get(s['columna_actualizacion'])
    if oid not in (1114,1184): raise ValueError('Tipo de fecha de actualización no admitido')
    marca=base['marca_cambios_utc' if oid==1184 else 'marca_cambios_local']
    return dict(tipo='incremental',desde=max(date.fromisoformat(s['fecha_desde']),inicio_ventana(fecha,meses)).isoformat(),
        cambios_desde=(datetime.fromisoformat(marca)-timedelta(hours=solape_horas)).isoformat(),
        columna_actualizacion=s['columna_actualizacion'],clave_grupo=s['clave'],
        zona_fuente=base.get('zona_fuente'),meses=meses,solape_horas=solape_horas)


def consulta(s,plan):
    from psycopg import sql
    tabla=sql.SQL('{}.{}').format(*map(sql.Identifier,s['relacion'].split('.')))
    if s['unica']:
        q=sql.SQL('SELECT * FROM {tabla} WHERE {fecha} >= %s OR {actualizacion} >= %s').format(
            tabla=tabla,fecha=sql.Identifier(s['columna_fecha']),actualizacion=sql.Identifier(s['columna_actualizacion']))
        return q,(date.fromisoformat(plan['desde']),datetime.fromisoformat(plan['cambios_desde']))
    # IN vuelve a leer el grupo completo. No pierde una segunda factura sin updated nuevo.
    q=sql.SQL('SELECT * FROM {tabla} WHERE {clave} IN (SELECT {clave} FROM {tabla} '
              'WHERE {fecha} >= %s OR {actualizacion} >= %s)').format(
        tabla=tabla,clave=sql.Identifier(s['clave']),fecha=sql.Identifier(s['columna_fecha']),
        actualizacion=sql.Identifier(s['columna_actualizacion']))
    return q,(date.fromisoformat(plan['desde']),datetime.fromisoformat(plan['cambios_desde']))


def consolidar(base,delta,s,salida,plan,lote=25000):
    base,delta,salida=map(Path,(base,delta,salida))
    viejo,nuevo=pq.ParquetFile(base),pq.ParquetFile(delta)
    if not viejo.schema_arrow.equals(nuevo.schema_arrow,check_metadata=False):
        raise ValueError('Cambió el esquema de la fuente: revisar contrato antes de consolidar')
    clave,fecha=s['clave'],s['columna_fecha']
    ids=pq.read_table(delta,columns=[clave]).column(clave).combine_chunks()
    if ids.null_count: raise ValueError('Delta con clave nula')
    nuevos_ids=pc.unique(ids)
    if s['unica'] and len(nuevos_ids)!=len(ids): raise ValueError('Delta duplica una clave única')
    cutoff=pa.scalar(date.fromisoformat(s['fecha_desde']),type=pa.date32())
    ventana=pa.scalar(date.fromisoformat(plan['desde']),type=pa.date32())
    conservadas=reemplazadas=incorporadas=fuera_alcance=0
    ausentes=[]
    salida.parent.mkdir(parents=True,exist_ok=True)
    temporal=salida.with_suffix('.part')
    with pq.ParquetWriter(temporal,viejo.schema_arrow,compression='snappy') as escritor:
        for b in viejo.iter_batches(batch_size=lote):
            t=pa.Table.from_batches([b]);tocadas=pc.is_in(t[clave],value_set=nuevos_ids)
            reciente=pc.fill_null(pc.greater_equal(pc.cast(t[fecha],pa.date32()),ventana),False)
            ausentes.append(t[clave].filter(pc.and_(reciente,pc.invert(tocadas))))
            guardar=t.filter(pc.invert(tocadas))
            conservadas+=guardar.num_rows;reemplazadas+=t.num_rows-guardar.num_rows
            escritor.write_table(guardar)
        for b in nuevo.iter_batches(batch_size=lote):
            t=pa.Table.from_batches([b])
            guardar=t.filter(pc.fill_null(pc.greater_equal(pc.cast(t[fecha],pa.date32()),cutoff),False))
            incorporadas+=guardar.num_rows;fuera_alcance+=t.num_rows-guardar.num_rows
            escritor.write_table(guardar)
    ausentes=pc.unique(pa.concat_arrays([a.combine_chunks() for a in ausentes])) if ausentes else pa.array([],type=ids.type)
    controles=dict(filas_anteriores=viejo.metadata.num_rows,filas_delta=nuevo.metadata.num_rows,
        filas_anteriores_conservadas=conservadas,filas_anteriores_reemplazadas=reemplazadas,
        filas_delta_incorporadas=incorporadas,filas_delta_fuera_alcance=fuera_alcance,
        grupos_recibidos=len(nuevos_ids),claves_ausentes_ventana=len(ausentes),
        politica_ausencias='conservar_y_advertir',muestra_claves_ausentes=ausentes.slice(0,20).to_pylist())
    if pq.read_metadata(temporal).num_rows!=conservadas+incorporadas:
        raise ValueError('Conteo consolidado no cuadra')
    if s['unica']:
        claves=pq.read_table(temporal,columns=[clave]).column(clave).combine_chunks()
        if claves.null_count or len(pc.unique(claves))!=len(claves): raise ValueError('Clave consolidada inválida')
    temporal.replace(salida)
    return controles
