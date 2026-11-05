"""Escenario 4: masa mensual / promedio de días-viaje de meses anteriores.

El universo que paga y el que forma el divisor es el mismo: viajes reales y
virtuales con contexto admisible del escenario 3. Los virtuales sin contexto no
se inventan. Las primeras ventanas incompletas quedan sin cuota, no en cero.
"""
import numpy as np
import pandas as pd

BOLSAS=('flota','rampla','estructura','directo_fleteros')


def cuotas(dias, masas, ventanas):
    """Cálculo puro: incluye meses sin actividad y excluye siempre el mes actual."""
    filas=[]
    meses=pd.period_range(dias.mes.min(),dias.mes.max(),freq='M')
    for bolsa in BOLSAS:
        serie=dias.loc[dias.bolsa.eq(bolsa)].groupby('mes').dias.sum().reindex(meses,fill_value=0.)
        for mes in meses:
            fecha=mes.start_time.date()
            ventana=ventanas[(ventanas.vigente_desde<=fecha)&(fecha<ventanas.vigente_hasta)]
            if len(ventana)!=1:
                raise ValueError('Ventana de absorción ausente o solapada')
            n=int(ventana.iloc[0].valor)
            if n<=0 or n!=float(ventana.iloc[0].valor):
                raise ValueError('Ventana debe ser un número entero positivo de meses')
            prev=serie.reindex(pd.period_range(mes-n,mes-1,freq='M'))
            divisor=round(float(prev.mean()),4) if prev.notna().all() else np.nan
            masa=masas[(masas.ambito==bolsa)&(masas.vigente_desde<=fecha)&(fecha<masas.vigente_hasta)]
            if len(masa)>1:
                raise ValueError('Masa de bolsa solapada')
            mensual=round(float(masa.iloc[0].monto_base)/12,2) if len(masa) else np.nan
            valor=round(mensual/divisor,6) if divisor>0 else (0. if mensual==0 and divisor==0 else np.nan)
            filas.append(dict(mes=str(mes),bolsa=bolsa,masa_mensual=mensual,
                divisor=divisor,cuota=valor,ventana=n))
    return pd.DataFrame(filas)


def construir(con):
    from escenarios.costeo_virtuales import CONSULTA_CONTEXTO, componentes_adicionales
    con.execute('''INSERT INTO costos.parametro_costo
      (parametro,metodo,ambito,valor,unidad,vigente_desde,vigente_hasta,origen,fuente_detalle)
      SELECT 'ventana_meses','absorcion_total','todos',3,'meses','1900-01-01','9999-12-31',
        'decision usuario','Especificacion DW 2026-11-03 seccion 2'
      WHERE NOT EXISTS(SELECT 1 FROM costos.parametro_costo
        WHERE parametro='ventana_meses' AND metodo='absorcion_total')''')
    con.execute('''INSERT INTO costos.parametro_costo
      (parametro,metodo,ambito,valor,unidad,vigente_desde,vigente_hasta,origen,fuente_detalle)
      SELECT 'km_por_dia','absorcion_total','todos',720.5,'km/dia','1900-01-01','9999-12-31',
        'decision usuario','Especificacion DW 2026-11-03 seccion 2.1; escenario 3 legado conserva 720'
      WHERE NOT EXISTS(SELECT 1 FROM costos.parametro_costo
        WHERE parametro='km_por_dia' AND metodo='absorcion_total')''')
    reales=con.df('''SELECT c.nro_viaje::text AS id_viaje,c.nro_viaje,c.sk_fecha_inicio AS sk_fecha,
      c.dias_viaje AS dias,c.km_tramo AS km,c.es_flota_propia,c.usa_rampla_callegari,v.tipo_operador AS operador
      FROM costos.fact_viaje_costo_indirecto c JOIN ventas.fact_viaje_venta v USING(nro_viaje)
      WHERE c.metodo='propuesto' ''')
    reales['es_virtual']=False
    reales['flota']=reales.es_flota_propia.eq('S')
    reales['rampla']=reales.usa_rampla_callegari.eq('S')
    reales['estructura']=reales.operador.isin(['propio','fletero','caterina'])
    reales['directo_fleteros']=reales.operador.eq('fletero')
    vt=con.df('SELECT id_viaje,sk_fecha,km FROM ciclos.fact_viaje_ciclo WHERE es_virtual=\'S\'')
    vt=vt.merge(con.df(CONSULTA_CONTEXTO),on='id_viaje',validate='one_to_one')
    ad=componentes_adicionales(vt)
    vt['dias']=ad.dias_modelados
    vt['nro_viaje']=pd.NA
    vt['es_virtual']=True
    ok=ad.estado_costo_fijo.eq('ok')
    vt['flota']=ok
    vt['estructura']=ok
    vt['rampla']=ok & vt.anterior_rampla_propia.eq('S') & vt.anterior_tractoreo.eq('N')
    vt['directo_fleteros']=False
    universo=pd.concat([reales,vt],ignore_index=True)
    km_dia=con.df("SELECT * FROM costos.parametro_costo WHERE metodo='absorcion_total' AND parametro='km_por_dia' AND ambito='todos'")
    tasa=pd.Series(np.nan,index=universo.index)
    coincidencias=pd.Series(0,index=universo.index)
    for r in km_dia.itertuples():
        desde=int(r.vigente_desde.strftime('%Y%m%d'))
        hasta=int(r.vigente_hasta.strftime('%Y%m%d'))
        mask=universo.sk_fecha.ge(desde)&universo.sk_fecha.lt(hasta)
        if float(r.valor)<=0:raise ValueError('km_por_dia debe ser positivo')
        tasa.loc[mask]=float(r.valor);coincidencias.loc[mask]+=1
    if not coincidencias.eq(1).all():raise ValueError('km_por_dia sin vigencia única')
    universo['dias']=universo.km/tasa
    universo['mes']=pd.to_datetime(universo.sk_fecha.astype(str),format='%Y%m%d',errors='raise').dt.to_period('M')
    dias=pd.concat([universo.loc[universo[b],['id_viaje','nro_viaje','es_virtual','sk_fecha','mes','dias']].assign(bolsa=b)
        for b in BOLSAS],ignore_index=True)
    masas=con.df("SELECT * FROM costos.parametro_costo WHERE parametro='cuota_diaria' AND metodo='propuesto'")
    ventanas=con.df("SELECT * FROM costos.parametro_costo WHERE parametro='ventana_meses' AND metodo='absorcion_total'")
    q=cuotas(dias,masas,ventanas)
    con.execute("DELETE FROM costos.parametro_costo WHERE metodo='absorcion_total' AND parametro='cuota_diaria'")
    filas=[]
    for r in q.itertuples():
        if pd.isna(r.cuota):
            continue
        mes=pd.Period(r.mes,'M')
        filas.append(('cuota_diaria','absorcion_total',r.bolsa,r.cuota,'CLP/dia-viaje',
            mes.start_time.date(),(mes+1).start_time.date(),'calculado',
            'masa vigente escenario 3 / 12 / promedio meses previos; reales y virtuales admisibles',
            r.masa_mensual,r.divisor,'Ventana '+str(r.ventana)+' meses; mes de inicio; sin usar actividad futura'))
    con.copiar('costos.parametro_costo', ['parametro','metodo','ambito','valor','unidad','vigente_desde',
        'vigente_hasta','origen','fuente_detalle','monto_base','cantidad_inductor','nota'],filas)
    dias['mes']=dias.mes.astype(str)
    d=dias.merge(q,on=['mes','bolsa'],validate='many_to_one')
    d['monto']=d.dias*d.cuota
    d['estado']=np.where(d.monto.notna(),'ok','sin dato: ventana o masa incompleta')
    con.execute('''CREATE TABLE IF NOT EXISTS costos.fact_absorcion_viaje (
      id_viaje text,bolsa text,nro_viaje bigint,es_virtual boolean,sk_fecha integer,mes text,
      dias double precision,cuota double precision,monto double precision,estado text,
      PRIMARY KEY(id_viaje,bolsa)); DELETE FROM costos.fact_absorcion_viaje''')
    cols=['id_viaje','bolsa','nro_viaje','es_virtual','sk_fecha','mes','dias','cuota','monto','estado']
    con.copiar_df('costos.fact_absorcion_viaje',d[cols])
    return d,q


def aplicar(con,fc,virtuales):
    d,q=construir(con)
    # Las bolsas no aplicables no generan filas; cualquier bolsa pendiente mantiene NULL el total.
    total=d.groupby('id_viaje').monto.agg(lambda s:s.sum() if s.notna().all() else np.nan)
    fc=fc.copy(); virtuales=virtuales.copy()
    m=fc.sk_escenario.eq(4)&fc.sk_componente.eq(4)
    dias=d.groupby('id_viaje').dias.first()
    fc.loc[m,'cantidad']=fc.loc[m,'nro_viaje'].astype(str).map(dias)
    fc.loc[m,'monto']=fc.loc[m,'nro_viaje'].astype(str).map(total)
    fc.loc[m,'tasa']=fc.loc[m,'monto']/fc.loc[m,'cantidad'].where(fc.loc[m,'cantidad']>0)
    fc.loc[m,'estado']=np.where(fc.loc[m,'monto'].notna(),'ok','sin dato: absorcion total')
    m=virtuales.sk_escenario.eq(4)&virtuales.sk_componente.eq(4)
    # Mantener bloqueos de trayectoria/contexto originales.
    admisible=m & virtuales.estado.eq('ok')
    virtuales.loc[admisible,'cantidad']=virtuales.loc[admisible,'id_viaje'].map(dias)
    virtuales.loc[admisible,'monto']=virtuales.loc[admisible,'id_viaje'].map(total)
    virtuales.loc[admisible,'tasa']=virtuales.loc[admisible,'monto']/virtuales.loc[admisible,'cantidad'].where(virtuales.loc[admisible,'cantidad']>0)
    virtuales.loc[admisible,'estado']=np.where(virtuales.loc[admisible,'monto'].notna(),'ok','sin dato: absorcion total')
    virtuales.loc[m,'origen_calculo']='cuota mensual absorcion total; dias segun parametro propio; contexto admisible'
    for b in ('flota','rampla','estructura'):
        montos=d.loc[d.bolsa.eq(b)].set_index('id_viaje').monto
        ids=virtuales.loc[admisible,'id_viaje']
        virtuales.loc[admisible,'cf_'+b+'_virtual']=ids.map(montos).where(ids.isin(montos.index),0.)
    return fc,virtuales
