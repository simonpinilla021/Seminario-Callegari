"""Contrato de carga P7. Conserva litros/costos P1-P6 y valoriza el ajuste separado.

certificacion.py es una copia íntegra del módulo entregado. Los parámetros públicos
del módulo se registran con vigencia y se inyectan desde PostgreSQL al ejecutarlo.
La vigencia corresponde a la versión de cálculo (no a la fecha del viaje).
"""
import ast
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
ARCHIVOS = ('bloques_certificados.csv', 'rendimiento_camion_mes.csv',
            'ficha_consumo_camion_mes.csv', 'litros_viaje_certificado.csv', 'generacion_camion.csv')


def parametros(con, fecha=None):
    con.execute('''CREATE TABLE IF NOT EXISTS combustible.parametro_certificacion (
      parametro text, vigente_desde date, vigente_hasta date NOT NULL,
      valor jsonb NOT NULL, fuente_sha256 text NOT NULL,
      PRIMARY KEY(parametro,vigente_desde), CHECK(vigente_desde<vigente_hasta))''')
    fuente = AQUI/'certificacion.py'
    huella = hashlib.sha256(fuente.read_bytes()).hexdigest()
    # Solo constantes públicas; no se ejecuta ni modifica el código del proveedor.
    for nodo in ast.parse(fuente.read_text(encoding='utf-8')).body:
        if not isinstance(nodo, ast.Assign):
            continue
        try:
            valor = ast.literal_eval(nodo.value)
        except (ValueError, TypeError):
            continue
        destinos = nodo.targets[0]
        pares = zip([x.id for x in destinos.elts], valor) if isinstance(destinos, ast.Tuple) else [(destinos.id, valor)]
        for nombre, dato in pares:
            if not nombre.isupper():
                continue
            # No sobrescribir versiones administradas; tampoco rellenar huecos históricos.
            con.execute('''INSERT INTO combustible.parametro_certificacion
              SELECT %s,'1900-01-01','9999-12-31',%s::jsonb,%s
              WHERE NOT EXISTS(SELECT 1 FROM combustible.parametro_certificacion WHERE parametro=%s)''',
                        (nombre, json.dumps(dato), huella, nombre))
    fecha = fecha or date.today()
    con.execute('''INSERT INTO combustible.parametro_certificacion
      SELECT 'ALERTA_CERT_MES','1900-01-01','9999-12-31','0.8'::jsonb,%s
      WHERE NOT EXISTS(SELECT 1 FROM combustible.parametro_certificacion WHERE parametro='ALERTA_CERT_MES')''',(huella,))
    p = con.df('''SELECT parametro,valor FROM combustible.parametro_certificacion
      WHERE %s>=vigente_desde AND %s<vigente_hasta''', (fecha, fecha))
    if p.parametro.duplicated().any():
        raise ValueError('Parámetros de certificación con vigencias solapadas')
    return dict(zip(p.parametro, p.valor))


def ejecutar_p7(contexto, con):
    from combustible import certificacion
    for k, v in parametros(con).items():
        setattr(certificacion, k, v)
    # Consumo local calculado por P5: no sustituirlo por la cifra redondeada del informe.
    local = float(100*contexto['beta3'][2])
    return certificacion.ejecutar(**{k: contexto[k] for k in
        ('pa','pegado','ct','b0','bl','pzb','res','eq')}, eq_crudo=contexto['crudo'].get('equipos'),
        salida=contexto['SALIDA'], p=contexto['p'], titulo=contexto['titulo'], l100_local=local)


def _tabla(con, nombre, datos, claves, fechas=()):
    """Tablas explícitas por grano, sin inferir texto por el contenido ni nombres personales."""
    definiciones = []
    for c in datos:
        if c in fechas:
            datos[c] = pd.to_datetime(datos[c], errors='raise')
            tipo = 'date' if c.startswith('vigente_') else 'timestamp'
            if tipo=='date':datos[c]=datos[c].dt.date
        elif c in ('equipo_id','bloque','bloques','bloques_cert','bloques_cert_12m','anio'):
            datos[c] = pd.to_numeric(datos[c], errors='raise').astype('Int64')
            tipo = 'bigint'
        elif pd.api.types.is_bool_dtype(datos[c]):
            tipo = 'boolean'
        elif pd.api.types.is_numeric_dtype(datos[c]):
            tipo = 'double precision'
        else:
            tipo = 'text'
        definiciones.append(f'{c} {tipo}')
    if datos.duplicated(list(claves)).any() or datos[list(claves)].isna().any().any():
        raise ValueError('Claves nulas o repetidas: '+nombre)
    con.execute(f'CREATE TABLE IF NOT EXISTS combustible.{nombre} ('+', '.join(definiciones)+
                ', PRIMARY KEY ('+','.join(claves)+'))')
    con.execute(f'DELETE FROM combustible.{nombre}')
    con.copiar_df('combustible.'+nombre, datos)


def cargar(con, carpeta):
    carpeta = Path(carpeta)
    parametros(con)
    for archivo in ARCHIVOS:
        if not (carpeta/archivo).is_file():
            raise FileNotFoundError('P7 incompleto: '+str(carpeta/archivo))
    b = pd.read_csv(carpeta/ARCHIVOS[0]).rename(columns={'equipo':'equipo_id','bloque_u':'bloque'})
    m = pd.read_csv(carpeta/ARCHIVOS[1]).rename(columns={'equipo':'equipo_id'})
    f = pd.read_csv(carpeta/ARCHIVOS[2]).rename(columns={'camion':'equipo_id', 'L100_cargado':'l100_cargado',
        'L100_vacio':'l100_vacio','kmL_cargado':'kml_cargado','kmL_vacio':'kml_vacio'})
    periodos=pd.PeriodIndex(f.mes,freq='M')
    f['vigente_desde']=periodos.to_timestamp()
    f['vigente_hasta']=(periodos+1).to_timestamp()
    g = pd.read_csv(carpeta/ARCHIVOS[4]).rename(columns={'equipo':'equipo_id'})
    v = pd.read_csv(carpeta/ARCHIVOS[3], dtype={'id_orig':str})
    v['id_orig'] = v.id_orig.str.replace(r'\.0$', '', regex=True)
    actual = con.df('SELECT id_calculo,id_viaje_fuente,litros_asignados FROM combustible.fact_viaje_combustible')
    z = v.merge(actual, left_on=['id','id_orig'], right_on=['id_calculo','id_viaje_fuente'],
                how='outer', indicator=True, validate='one_to_one')
    if not z['_merge'].eq('both').all():
        raise ValueError('P7 no corresponde a la misma fotografía de viajes: regenerar P1-P7')
    if (z.litros_asignados_x-z.litros_asignados_y).abs().gt(0.00011).any():
        raise ValueError('Los litros originales de P7 no coinciden con P1-P6')
    _tabla(con,'fact_bloque_certificacion',b,('equipo_id','bloque'),('t_ini','t_fin'))
    _tabla(con,'fact_rendimiento_camion_mes',m,('equipo_id','mes'))
    _tabla(con,'param_ficha_consumo',f,('equipo_id','mes'),('vigente_desde','vigente_hasta'))
    _tabla(con,'dim_generacion_camion',g,('equipo_id',))
    nombres = {'fraccion_certificada':'fraccion_certificada','fuente_ficha':'fuente_ficha',
        'l100_teo':'l100_teorico','litros_teo':'litros_teoricos','razon':'razon_real_teorico',
        'kmL':'rendimiento_viaje','kmL_max':'rendimiento_max','motivo_bloque':'motivo_bloque',
        'estado_litros':'estado_litros','litros_margen':'litros_margen','ajuste_litros':'ajuste_litros'}
    datos=v[['id']+list(nombres)].rename(columns={'id':'id_calculo',**nombres})
    for c in nombres.values():
        tipo='text' if c in ('fuente_ficha','motivo_bloque','estado_litros') else 'double precision'
        con.execute(f'ALTER TABLE combustible.fact_viaje_combustible ADD COLUMN IF NOT EXISTS {c} {tipo}')
    con.execute('''ALTER TABLE combustible.fact_viaje_combustible
      ADD COLUMN IF NOT EXISTS costo_combustible_margen double precision,
      ADD COLUMN IF NOT EXISTS costo_ajuste double precision,
      ADD COLUMN IF NOT EXISTS precio_litro_margen double precision''')
    con.execute('CREATE TEMP TABLE carga_certificada ON COMMIT DROP AS SELECT * FROM combustible.fact_viaje_combustible WITH NO DATA')
    con.copiar_df('carga_certificada',datos)
    con.execute('UPDATE combustible.fact_viaje_combustible v SET '+
        ','.join(f'{c}=s.{c}' for c in nombres.values())+' FROM carga_certificada s WHERE s.id_calculo=v.id_calculo')
    # P7 exporta a cuatro decimales. Restaurar la precisión de los litros reales
    # que ya conserva el DW y derivar su diferencia, sin recalcular certificación.
    con.execute('''UPDATE combustible.fact_viaje_combustible SET litros_margen=litros_asignados
      WHERE estado_litros IN('real certificado','real con alerta');
      UPDATE combustible.fact_viaje_combustible SET ajuste_litros=litros_margen-litros_asignados''')
    # Sin litros originales: precio ATP de la fecha, no mezcla ni promedio mensual.
    con.execute('''UPDATE combustible.fact_viaje_combustible v SET precio_litro_margen =
       CASE WHEN litros_asignados>0 THEN costo_combustible/litros_asignados ELSE p.costo_real END
       FROM comun.dim_fecha f LEFT JOIN precios.v_costo_combustible_dia p
         ON p.fecha=f.fecha AND p.oilsupplier='ATP' WHERE f.sk_fecha=v.sk_fecha_inicio;
       UPDATE combustible.fact_viaje_combustible SET
         costo_combustible_margen=CASE WHEN estado_litros IN('real certificado','real con alerta')
           THEN costo_combustible ELSE litros_margen*precio_litro_margen END,
         costo_ajuste=ajuste_litros*precio_litro_margen;
       CREATE OR REPLACE VIEW combustible.v_litros_no_asignables AS
       SELECT sk_fecha_inicio/10000 AS anio,motivo_bloque,count(*) AS viajes,
         sum(litros_asignados) AS litros_asignados,sum(litros_margen) AS litros_margen,
         sum(ajuste_litros) AS ajuste_litros,sum(costo_ajuste) AS costo_ajuste
       FROM combustible.fact_viaje_combustible GROUP BY 1,2;
       CREATE OR REPLACE VIEW combustible.v_alerta_certificacion_mensual AS
       SELECT date_trunc('month',t_fin)::date AS mes,
         sum(litros) FILTER(WHERE certificado)/nullif(sum(litros),0) AS fraccion_certificada
       FROM combustible.fact_bloque_certificacion GROUP BY 1''')
    return {x:len(y) for x,y in [('bloques',b),('notas',m),('fichas',f),('generaciones',g),('viajes',v)]}
