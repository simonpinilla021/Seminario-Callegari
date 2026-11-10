"""Puente secuencial en servicios equivalentes; separa cambios de cobertura."""
import numpy as np
import pandas as pd

def construir(con,anterior,q,folder):
    q=q[q.escenario_id.eq(1)].set_index('nro_viaje')
    propios=con.df("SELECT nro_viaje FROM ventas.fact_viaje_venta WHERE tipo_operador='propio'").nro_viaje
    old=anterior[anterior.sk_escenario.eq(1)&anterior.nro_viaje.isin(propios)&anterior.sk_componente.isin([1,2,3,4,5,6])]
    p=old.pivot(index='nro_viaje',columns='sk_componente',values='monto').reindex(columns=range(1,7))
    comercial=old.pivot(index='nro_viaje',columns='sk_componente',values='monto_comercial').reindex(columns=range(1,7))
    tasas=old.pivot(index='nro_viaje',columns='sk_componente',values='tasa').reindex(columns=range(1,7))
    kmold=old[old.sk_componente.eq(6)].set_index('nro_viaje').cantidad
    ids=p.index.intersection(q[q.evaluable].index)
    ids=ids[p.loc[ids].notna().all(axis=1)&comercial.loc[ids].notna().all(axis=1)]
    d=q.loc[ids];cur=comercial.loc[ids].copy();start=cur.copy();steps=[]
    def step(name,new):
        nonlocal cur
        for col in cur:
            steps.append({'causa':name,'componente_id':col,'servicios_comparables':len(ids),'antes_clp':float(cur[col].sum()),'despues_clp':float(new[col].sum()),'diferencia_clp':float((new[col]-cur[col]).sum())})
        cur=new.copy()
    step('Deshacer redistribución comercial anterior',p.loc[ids])
    ratio=d.e15.div(kmold.reindex(ids).replace(0,np.nan)).fillna(1.)
    n=cur.copy()
    for col in [1,2,4,5,6]:n[col]*=ratio
    step('Km entregados frente a km ERP',n)
    mult=np.where(d.clasificacion.eq('Envio'),2.,1.)
    n=cur.copy()
    for col in [1,3,4,5,6]:n[col]*=mult
    step('Modalidad Excel: ida y vuelta en envíos',n)
    n=cur.copy();n[3]=d.i8;step('Peajes exactos: tres búsquedas o SIN PEAJE completo',n)
    days=d.f15/720
    n=cur.copy();n[4]=n[4].where(days.ge(1),tasas.loc[ids,4]);step('Mínimo de un día de fijo',n)
    n=cur.copy();n[5]=np.ceil(days)*tasas.loc[ids,5];step('ROUNDUP de días en reposición',n)
    n=cur.copy();n[4]=d.i12;step('Tasa fija Excel en vez de tasa contable',n)
    n=cur.copy();n[5]=d.i13;n[6]=d.i11;step('Precisión exacta de reposición y neumáticos',n)
    prices=con.df('SELECT sk_fecha,costo_base,costo_tct,precio_tarificador FROM tarificador.fact_precio_combustible_tarificador').set_index('sk_fecha')
    oldprice=d.fecha_documental_id.map(prices.precio_tarificador)
    p68=d.fecha_documental_id.map(prices.costo_base*.68+prices.costo_tct*.32+100)
    n=cur.copy()
    for col in [1,2]:n[col]=n[col]*p68/oldprice
    step('Mezcla de precio: 68% fijo en vez del año anterior',n)
    n=cur.copy()
    for col in [1,2]:n[col]=n[col]*d.i4/p68
    step('Entradas y signo del precio Excel; sin reinterpretación contable',n)
    n=cur.copy();n[1]=d.i6;step('Rendimiento estándar exacto de la versión Excel',n)
    n=cur.copy();n[2]=d.i7;step('Activación de termo conforme a B4/B5 del Excel',n)
    step('Factor de retorno 0,8 sobre todo el servicio',cur.mul(d.factor_retorno,axis=0))
    # Regla del gerente (02-11): en 1/2 el retorno no tiene costo.
    con_costo=d.clasificacion.ne('Retorno').astype(float)
    step('Retorno sin costo (regla del gerente)',cur.mul(con_costo,axis=0))
    target=pd.DataFrame({col:d[cell]*d.factor_retorno*con_costo for col,cell in {1:'i6',2:'i7',3:'i8',4:'i12',5:'i13',6:'i11'}.items()})
    assert np.allclose(cur,target,atol=.01,rtol=0),'Puente no conciliado'
    for col in range(1,7):steps.append({'causa':'Virtuales: sin costo en 1/2 antes y después; cambia admisión, no importe','componente_id':col,'servicios_comparables':len(ids),'antes_clp':float(cur[col].sum()),'despues_clp':float(cur[col].sum()),'diferencia_clp':0.})
    pd.DataFrame(steps).to_csv(folder/'puente_causas_componentes.csv',index=False)
    detalle=pd.DataFrame({'nro_viaje':ids,'anterior_comercial':start.sum(axis=1),'nuevo_tarificador':target.sum(axis=1),'diferencia':target.sum(axis=1)-start.sum(axis=1)})
    detalle.to_csv(folder/'puente_servicios_equivalentes.csv.gz',index=False)
    tot=old.groupby('sk_componente')[['monto','monto_comercial']].sum(min_count=1)
    tot.to_csv(folder/'anterior_1_total_conocido_por_componente.csv')
    valid=q[q.evaluable];newtotal=valid.i17.where(valid.clasificacion.ne('Retorno'),0.).sum()  # retorno sin costo
    return {'servicios_equivalentes':len(ids),'anterior_comercial_equivalente':float(start.to_numpy().sum()),'nuevo_equivalente':float(target.to_numpy().sum()),'diferencia_equivalente':float((target-start).to_numpy().sum()),'residuo_puente':float((cur-target).abs().to_numpy().max()),'total_anterior_propios_conocido_incluye_vacios':float(old.monto.sum()),'total_nuevo_cotizaciones_evaluables':float(newtotal),'advertencia':'Los dos totales generales tienen poblaciones y unidades de costeo distintas; no son ahorro. El puente causal numérico usa únicamente servicios completos equivalentes. Vacíos se excluyen del nuevo cotizador; virtuales no se cotizan.'}
