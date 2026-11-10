"""Carga del contrato exacto: nunca modifica precios ni parámetros operacionales."""
from pathlib import Path
from datetime import datetime
import hashlib,json,math,unicodedata
import pandas as pd
from .calculo import calcular,buscar_peajes

HERE=Path(__file__).resolve().parent;INPUT=HERE/'insumos';VERSION='2026-11-02-excel-exacto-v2'
ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def insumos():
    m=json.loads((INPUT/'manifiesto.json').read_text(encoding='utf-8'))
    for n,h in m['archivos'].items():
        if sha(INPUT/n)!=h:raise ValueError('Cambió el insumo entregado: '+n)
    p=json.loads((INPUT/'parametros_excel.json').read_text(encoding='utf-8'))
    snapshot=json.loads((INPUT/'entrada_petroleo_version.json').read_text(encoding='utf-8'))
    return m,p,snapshot

def precio_dias(con,snapshot):
    fechas=con.df('SELECT DISTINCT sk_fecha FROM ventas.fact_viaje_venta ORDER BY 1')
    # Solo fecha/vigencia de cartas y sus líneas de origen. No usar tasas contables ni mezcla anual.
    raw=con.df('''SELECT f.sk_fuente,f.vigente_desde::text AS desde,f.vigente_hasta::text AS hasta,
      f.sk_carta,f.sk_precio,p.neto::float8,p.ie::float8,p.neto_deducido,c.fecha_vigencia::text AS fecha_carta,
      f.localidad_region,f.origen_precio,
      c.hash_sha256 FROM precios.fact_costo_combustible_vigente f
      JOIN precios.fact_precio_combustible p USING(sk_precio)
      JOIN precios.dim_carta_precio c ON c.sk_carta=f.sk_carta ORDER BY f.sk_fuente,f.vigente_desde''')
    dates=pd.to_datetime(fechas.sk_fecha.astype(str),format='%Y%m%d')
    result=pd.DataFrame({'fecha':dates.dt.date,'fecha_documental_id':fechas.sk_fecha})
    for supplier,prefix in [(1,'base'),(2,'tct')]:
        fields=['neto','ie','sk_carta','sk_precio','hash_sha256','fecha_carta','neto_deducido','localidad_region','origen_precio']
        for field in fields:result[prefix+'_'+field]=None
        result[prefix+'_origen_entrada']='carta vigente; precisión del dato fuente'
        for r in raw.loc[raw.sk_fuente.eq(supplier)].itertuples():
            mask=dates.ge(pd.Timestamp(r.desde))&dates.lt(pd.Timestamp(min(r.hasta,'2262-04-11')))
            for field in fields:result.loc[mask,prefix+'_'+field]=getattr(r,field)
            if r.fecha_carta==snapshot['fecha_carta']:
                result.loc[mask,prefix+'_neto']=snapshot[prefix+'_neto']
                result.loc[mask,prefix+'_ie']=snapshot[prefix+'_ie']
                result.loc[mask,prefix+'_origen_entrada']='entrada literal petroleo del Excel versión 15.10.2026'
        for field in ['neto','ie']:
            result[prefix+'_'+field]=pd.to_numeric(result[prefix+'_'+field],errors='coerce')
        if supplier==2:
            # La fila histórica 641073 cambió a Coquimbo en diciembre de 2023.
            # R14 no autoriza usar esa ciudad como precio de TCT Antofagasta.
            # Decisión de Cristian 02-11-2026 (auditoría Claude): la fila 641073 con etiqueta COQUIMBO
            # (21-12-2023 a 20-05-2026) se usa como TCT, marcada como SUPUESTO; corregir si aparece la carta faltante.
            lugar=result.tct_localidad_region.fillna('').str.upper()
            historica=result.tct_origen_precio.fillna('').str.contains('641073')
            lugar_ok=lugar.eq('ANTOFAGASTA')
            supuesto=lugar.eq('COQUIMBO') & historica
            result['tct_supuesto_coquimbo']=supuesto
            result.loc[supuesto,'tct_origen_entrada']='SUPUESTO: fila 641073 etiquetada COQUIMBO usada como TCT (decisión 02-11-2026)'
            result.loc[~(lugar_ok|supuesto),['tct_neto','tct_ie']]=float('nan')
            result.loc[~(lugar_ok|supuesto),'tct_origen_entrada']='no disponible: sin TCT Antofagasta ni fila 641073'
        result[prefix+'_operacion']=0.
        result[prefix+'_recuperacion']=result[prefix+'_ie']*snapshot['factor_recuperacion']
        result[prefix+'_costo_excel']=result[prefix+'_neto']+result[prefix+'_operacion']+result[prefix+'_recuperacion']+result[prefix+'_ie']
    result['pct_base']=snapshot['pct_base'];result['pct_tct']=snapshot['pct_tct'];result['colchon']=100.
    result['precio_excel']=result.base_costo_excel*result.pct_base+result.tct_costo_excel*result.pct_tct+result.colchon
    return result

def rendimiento_primer_bloque(con,viajes):
    """Decisión de Cristian 02-11-2026: si el camión aún no tiene bloques cerrados antes del viaje (primeras
    apariciones), el escenario 2 usa el rendimiento de su PRIMER bloque ok posterior. Solo para viajes anteriores a ese
    primer bloque y desde 2025; queda marcado como supuesto. Mismo filtro de bloques que combustible/estimar_costo_tarificador.py."""
    viajes=viajes.copy();viajes['rendimiento_supuesto']=False
    b=con.df('''SELECT equipo_id,t_fin AS termino,rend_cert::float8 AS r FROM combustible.fact_bloque_certificacion
                WHERE certificado AND t_fin IS NOT NULL''')
    if b.empty:return viajes
    b['termino']=pd.to_datetime(b.termino);b=b.sort_values(['equipo_id','termino'])
    primero=b.groupby('equipo_id').first()
    fecha=pd.to_datetime(viajes.fecha_documental_id.astype(str),format='%Y%m%d')
    eq=pd.to_numeric(viajes.equipo_id,errors='coerce')
    p_term=eq.map(primero.termino);p_r=eq.map(primero.r)
    usar=viajes.rendimiento_camion.isna() & p_term.notna() & (fecha<p_term) & viajes.fecha_documental_id.ge(20250101)
    viajes.loc[usar,'rendimiento_camion']=p_r[usar];viajes.loc[usar,'rendimiento_supuesto']=True
    return viajes

TRAMO_EXTENSION = 1000348
ESTADO_EXTENSION = 'ok: extension de seis puntos'


def extension_seis_puntos(km, peajes):
    def clave(texto):
        return ''.join(c for c in unicodedata.normalize('NFD', texto.strip().casefold())
                       if not unicodedata.combining(c))

    ruta = km.loc[km.sit_stretch_id.eq(TRAMO_EXTENSION)].iloc[0]
    ciudades = [c.strip() for c in ruta.texto_tramo.split('/')]
    distancias = pd.read_csv(INPUT/'distancia_tarificador.csv.gz').to_dict('records')
    pares = []
    for origen, destino in zip(ciudades, ciudades[1:]):
        par = clave(origen), clave(destino)
        kms = [float(f['km']) for f in distancias if (clave(f['origen']), clave(f['destino'])) == par]
        tasas = [float(f['peaje']) for f in peajes if f['estado']=='ok'
                 and (clave(f['origen']), clave(f['destino'])) == par]
        if len(kms)!=1 or len(tasas)!=1:
            raise ValueError(f'Extensión sin referencia única para {origen} / {destino}')
        distancia, peaje = kms[0], tasas[0]
        if not (math.isfinite(distancia) and distancia>0 and math.isfinite(peaje) and peaje>=0):
            raise ValueError(f'Referencia inválida para {origen} / {destino}')
        pares.append((distancia, peaje))
    if len(pares)!=5:
        raise ValueError('La extensión autorizada requiere cinco conexiones')
    return ciudades, pares


def preparar(con, *, solo_extension=False):
    manifest,params,snapshot=insumos()
    km=pd.read_csv(INPUT/'km_tramo_tarificador.csv');eq=pd.read_csv(INPUT/'equivalencia_lugar_tarificador.csv').fillna('')
    peajes=pd.read_csv(INPUT/'peajes_excel.csv').to_dict('records')
    ciudades_extension, pares_extension = extension_seis_puntos(km, peajes)
    peajes_tramo={}
    for t in km.itertuples():
        if t.estado in ('ok','local (0 km)'):peajes_tramo[int(t.sit_stretch_id)]=buscar_peajes([getattr(t,'c'+str(i)) for i in range(9,14)],peajes)
    prices=precio_dias(con,snapshot)
    viajes=con.df(('''SELECT v.nro_viaje,v.sk_fecha AS fecha_documental_id,v.cliente_id,v.equipo_id,
      v.sit_stretch_id AS tramo_id,v.producto_id,v.es_tractoreo,v.ingreso::float8 AS ingreso_estimado,
      cy.clasificacion_viaje AS clasificacion,ct.rendimiento_camion::float8 AS rendimiento_camion,
      ct.rendimiento_supuesto AS rendimiento_supuesto
      FROM ventas.fact_viaje_venta v
      LEFT JOIN ciclos.fact_viaje_ciclo cy ON cy.nro_viaje=v.nro_viaje AND cy.es_virtual='N'
      LEFT JOIN combustible.v_rendimiento_tarificador_certificado ct ON ct.nro_viaje=v.nro_viaje
      WHERE v.tipo_operador='propio' AND v.ingreso>0
      AND cy.clasificacion_viaje IN('Envio','Retorno')'''
      + (f' AND v.sit_stretch_id={TRAMO_EXTENSION}' if solo_extension else '') + ' ORDER BY v.nro_viaje'))
    if viajes.nro_viaje.duplicated().any():raise ValueError('Servicio duplicado en la lectura')
    # La referencia ya aplica la historia estrictamente anterior y la excepción marcada.
    tipos=pd.read_csv(ROOT/'code/costos/tipo_carga_producto_manual.csv')
    tipo=dict(zip(tipos.producto_id,tipos.tipo_carga))
    kmap={int(t['sit_stretch_id']):t for t in km.to_dict('records')}
    pmap={int(t['fecha_documental_id']):t for t in prices.to_dict('records')}
    assumed={x.nombre_erp.upper().strip() for x in eq.itertuples() if x.estado=='supuesto'}
    records=[]
    for v in viajes.to_dict('records'):
        k=kmap.get(int(v['tramo_id'])) if pd.notna(v['tramo_id']) else None
        extension=v['tramo_id']==TRAMO_EXTENSION
        pr=pmap[int(v['fecha_documental_id'])]
        for scenario in [1,2]:
            r={n:v[n] for n in ['nro_viaje','fecha_documental_id','cliente_id','equipo_id','tramo_id','es_tractoreo','ingreso_estimado','clasificacion']}
            r.update(escenario_id=scenario,tipo_carga=tipo.get(v['producto_id'],'sin clasificar'),version=VERSION,fuente_sha256=manifest['excel_sha256'])
            r['estado_km']=k['estado'] if k else 'tramo ausente de la entrega'
            r['supuesto_rm']=bool(k and any(x.strip().upper() in assumed for x in k['texto_tramo'].split('/')))
            r['texto_tramo']=k['texto_tramo'] if k else None
            r['n_puntos']=k['n_puntos'] if k else None
            for i in range(9,14):r['c'+str(i)]=k['c'+str(i)] if k and pd.notna(k.get('c'+str(i))) else None
            if extension:
                r['estado_km']=ESTADO_EXTENSION
                for i,ciudad in enumerate(ciudades_extension[:5],start=9):
                    r[f'c{i}']=ciudad
            frio=r['tipo_carga'] in ['frio','congelado','multi'];cong=r['tipo_carga'] in ['congelado','multi']
            ret=r['clasificacion']=='Retorno'
            r.update(b4='si' if frio else 'no',b5='si' if cong else 'no',b6='si' if ret else 'no',b7='si' if ret else 'no',n5='si' if r['es_tractoreo']=='S' else 'no')
            r['rendimiento']=params['B5'] if scenario==1 else v['rendimiento_camion']
            r['i4']=pr['precio_excel']
            sup=['extension de seis puntos con referencias del tarificador'] if extension else []
            if r['supuesto_rm']:sup.append('comuna RM cotizada como Santiago')
            if bool(pr.get('tct_supuesto_coquimbo')):sup.append('TCT = fila 641073 COQUIMBO')
            if scenario==2 and bool(v.get('rendimiento_supuesto')):sup.append('rendimiento del primer bloque certificado posterior del camión')
            r['supuestos']='; '.join(sup) if sup else None
            reasons=[]
            if r['estado_km'] not in ['ok','local (0 km)',ESTADO_EXTENSION]:reasons.append(r['estado_km'])
            if r['tipo_carga'] not in ['seco','sin termo','frio','congelado','multi']:reasons.append('tipo de carga sin clasificar')
            if r['es_tractoreo'] not in ['S','N']:reasons.append('tractoreo no definido')
            if not pd.notna(r['rendimiento']) or r['rendimiento']<=0:reasons.append('rendimiento del camión no disponible')
            if pd.isna(r['i4']):reasons.append('carta base o TCT Antofagasta vigente no disponible')
            # Regla del gerente (Cristian, 02-11): en 1/2 el retorno se MUESTRA con costo 0 siempre, sea o no cotizable;
            # I17 y el margen calculado se conservan aparte. Los envíos no cotizables quedan sin costo mostrado (NULL).
            r['costo_presentacion']=0. if ret else None
            r['evaluable']=not reasons;r['estado']='ok' if not reasons else '; '.join(reasons)
            if r['estado_km'] in ['ok','local (0 km)',ESTADO_EXTENSION] and r['tipo_carga'] in ['seco','sin termo','frio','congelado','multi'] and r['es_tractoreo'] in ['S','N']:
                entrada={n:r[n] for n in ['c9','c10','c11','c12','c13','b4','b5','b6','b7','n5','i4']}
                entrada['d27']=r['rendimiento']
                for i in range(3):
                    entrada[f'e{10+i}'],entrada[f'k{10+i}']=(pares_extension[i] if extension else
                        (k[f'km_pierna_{i+1}'],peajes_tramo[int(v['tramo_id'])][i]))
                if extension:
                    entrada['tramos_adicionales']=pares_extension[3:]
                r.update(calcular(entrada,params))
                for i in range(3):r['k'+str(i+10)]=entrada['k'+str(i+10)]
                r['j8_estado']='SIN PEAJE' if isinstance(r['j8'],str) else 'ok'
                if isinstance(r['j8'],str):r['j8']=0.
                # Regla del gerente (Cristian, 02-11; corrige R19 de los requerimientos de Claude): en 1/2 el RETORNO NO TIENE
                # COSTO. Su margen es el ingreso completo. I17 se conserva solo como referencia de lo que habría cotizado el Excel.
                r['margen_tarificador']=(r['ingreso_estimado']-(0. if ret else r['i17'])) if r['evaluable'] else None  # no evaluable: sin margen (control costo_incompleto_publicado)
                r['razon_ingreso_cotizacion']=r['ingreso_estimado']/r['i22'] if r['i22'] else None
                # Decisión Cristian 02-11: regla de presentación, no altera I17 ni el margen calculado.
                r['costo_presentacion']=0. if ret else (r['i17'] if r['evaluable'] else None)
            if extension and not r['evaluable']:
                r['i17']=r['i22']=None
            records.append(r)
    q=pd.DataFrame(records)
    return q,prices,km,eq,params,manifest

TEXT={'clasificacion','es_tractoreo','texto_tramo','tipo_carga','version','fuente_sha256','estado_km','estado','c9','c10','c11','c12','c13','b4','b5','b6','b7','n5','j8_estado','g4','n4','g5','e7','c21','e8'}
INT={'nro_viaje','escenario_id','fecha_documental_id','cliente_id','equipo_id','tramo_id','n_puntos'}
BOOL={'supuesto_rm','evaluable'}
def tabla_df(con,name,df,types,pk):
    """Tipos explícitos; carga a tablas propias y conserva vistas dependientes al repetir."""
    df=df.copy()
    for c,t in types.items():
        if t in ['integer','bigint']:df[c]=pd.to_numeric(df[c],errors='raise').astype('Int64')
    con.execute('CREATE TABLE IF NOT EXISTS '+name+' ('+', '.join('"'+c+'" '+types[c] for c in df.columns)+', PRIMARY KEY ('+','.join(pk)+'))')
    con.execute('TRUNCATE '+name)
    con.copiar_df(name,df)
    con.execute('ANALYZE '+name)

def cargar(con,prepared):
    q,prices,km,eq,params,manifest=prepared
    con.execute('CREATE SCHEMA IF NOT EXISTS tarificador')
    con.execute('''CREATE TABLE IF NOT EXISTS tarificador.version_excel_exacto (
     version text PRIMARY KEY,fuente_sha256 text, vigente_desde date,vigente_hasta date,parametros jsonb,manifiesto jsonb)''')
    con.execute('''INSERT INTO tarificador.version_excel_exacto VALUES(%s,%s,'1900-01-01','9999-12-31',%s::jsonb,%s::jsonb)
     ON CONFLICT(version) DO UPDATE SET parametros=excluded.parametros,manifiesto=excluded.manifiesto''',(VERSION,manifest['excel_sha256'],json.dumps(params),json.dumps(manifest)))
    distances=pd.read_csv(INPUT/'distancia_tarificador.csv.gz')
    distances['fuente_sha256']=manifest['excel_sha256']
    tabla_df(con,'tarificador.fact_distancia_tarificador',distances,{c:('double precision' if c=='km' else 'date' if c.startswith('vigente_') else 'text') for c in distances},['origen','destino','vigente_desde'])
    cities=pd.read_csv(INPUT/'tarificador_ciudad.csv')
    tabla_df(con,'tarificador.ciudad_excel_exacto',cities,{c:('boolean' if c=='puede_ser_origen' else 'integer' if c in ['indice_columna','fila_distancia'] else 'text') for c in cities},['ciudad'])
    tabla_df(con,'tarificador.equivalencia_lugar_tarificador',eq,{c:'text' for c in eq},['clave_lugar'])
    numeric_km={'sit_stretch_id','n_puntos','km_erp','km_pierna_1','km_pierna_2','km_pierna_3','intermedios','km_ida','km_ida_vuelta'}
    tabla_df(con,'tarificador.km_tramo_tarificador',km,{c:('boolean' if c=='destino_4_sin_km' else 'double precision' if c in numeric_km else 'date' if c.startswith('vigente_') else 'text') for c in km},['sit_stretch_id','vigente_desde'])
    # La matriz cruda permite auditar el efecto de un único #N/A.
    pj=pd.read_csv(INPUT/'peajes_excel.csv')
    tabla_df(con,'tarificador.peaje_excel_exacto',pj,{c:('integer' if c in ['fila','indice_destino'] else 'double precision' if c=='peaje' else 'text') for c in pj},['fila','destino'])
    prices=prices.drop(columns=['tct_supuesto_coquimbo'],errors='ignore')  # el supuesto queda en tct_origen_entrada
    types={c:('date' if c=='fecha' else 'bigint' if c.endswith(('_sk_carta','_sk_precio')) or c=='fecha_documental_id' else 'text' if c.endswith(('_hash_sha256','_fecha_carta','_neto_deducido','_origen_entrada','_localidad_region','_origen_precio')) else 'double precision') for c in prices}
    tabla_df(con,'tarificador.fact_precio_excel_dia',prices,types,['fecha_documental_id'])
    # Supuestos en tabla propia: fact_cotizacion_servicio conserva exactamente sus columnas (vistas dependientes).
    sup=q.loc[q.supuestos.notna(),['nro_viaje','escenario_id','supuestos']].copy()
    con.execute('''CREATE TABLE IF NOT EXISTS tarificador.fact_cotizacion_supuesto (nro_viaje bigint,escenario_id bigint,
      supuestos text,PRIMARY KEY(nro_viaje,escenario_id))''')
    con.execute('TRUNCATE tarificador.fact_cotizacion_supuesto');con.copiar_df('tarificador.fact_cotizacion_supuesto',sup)
    q=q.drop(columns=['supuestos'])
    types={c:('text' if c in TEXT else 'bigint' if c in INT else 'boolean' if c in BOOL else 'double precision') for c in q}
    tabla_df(con,'tarificador.fact_cotizacion_servicio',q,types,['nro_viaje','escenario_id'])
    return {'servicios':len(q)//2,'filas':len(q),'evaluables':q.groupby('escenario_id').evaluable.sum().to_dict(),'km_cargados_sin_recalculo':len(distances),'tramos':len(km)}
