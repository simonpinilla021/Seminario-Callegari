"""Actualiza exclusivamente el escenario 2 con rendimiento certificado P7.

--validar (defecto) revierte. --guardar valida y confirma en el PostgreSQL local.
No reconstruye combustible ni absorción; preserva escenarios 1,3,4 y Power BI.
"""
import argparse,json,hashlib
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from comun import db
from comun.carga_segura import Entradas
from combustible.rendimiento_tarificador_certificado import VERSION
from tarificador.exacto.modelo import preparar
from tarificador.exacto.publicacion import reemplazar_fact,controles

ROOT=Path(__file__).resolve().parents[2]


def probar_limites(con):
    from combustible.rendimiento_tarificador_certificado import SQL
    con.execute('''CREATE TEMP TABLE prueba_viajes(nro_viaje bigint,equipo_id bigint,sk_fecha integer) ON COMMIT DROP;
      CREATE TEMP TABLE prueba_bloques(equipo_id bigint,bloque bigint,t_fin timestamp,rend_cert double precision,certificado boolean) ON COMMIT DROP;
      CREATE TEMP TABLE prueba_ventana(parametro text,valor integer,vigente_desde date,vigente_hasta date) ON COMMIT DROP;
      INSERT INTO prueba_viajes VALUES(1,1,20250110),(2,1,20250111),(3,1,20250112),
        (4,2,20241231),(5,2,20250101),(6,3,20250101),(7,2,20250102);
      INSERT INTO prueba_ventana VALUES('bloques_recientes',5,'1900-01-01','2025-01-12'),
        ('bloques_recientes',3,'2025-01-12','9999-12-31');
      INSERT INTO prueba_bloques VALUES(1,1,'2025-01-01',2,true),(1,2,'2025-01-02',2.2,true),
        (1,3,'2025-01-03',2.4,true),(1,4,'2025-01-04',2.6,true),(1,5,'2025-01-05',2.8,true),
        (1,6,'2025-01-06',3,true),(1,7,'2025-01-07',99,false),(1,8,'2025-01-10',4,true),
        (2,1,'2025-01-01',99,false),(2,2,'2025-01-02',3.1,true);''')
    consulta=SQL.split('CREATE OR REPLACE VIEW combustible.v_rendimiento_tarificador_certificado AS',1)[1].strip().rstrip(';')
    for origen,destino in [('ventas.fact_viaje_venta','pg_temp.prueba_viajes'),
      ('combustible.fact_bloque_certificacion','pg_temp.prueba_bloques'),
      ('combustible.parametro_rendimiento_tarificador','pg_temp.prueba_ventana')]:consulta=consulta.replace(origen,destino)
    d=con.df(consulta).set_index('nro_viaje')
    esperados={1:2.6,2:2.8,3:3.,4:None,5:3.1,6:None,7:None}
    for n,valor in esperados.items():
        real=d.loc[n,'rendimiento_camion']
        if not ((valor is None and pd.isna(real)) or (valor is not None and pd.notna(real) and abs(real-valor)<1e-12)):
            raise ValueError('Falló límite histórico del caso '+str(n))
    assert d.loc[5,'rendimiento_supuesto'] and not d.loc[4,'rendimiento_supuesto']
    assert d.loc[1,'bloques_utilizados']==5 and d.loc[3,'bloques_utilizados']==3
    return dict(casos=7,diferencias=0,comprueba='certificacion, cinco bloques, vigencia, fecha estricta, sin historia y supuesto desde 2025')


def fingerprint(con,sql):
    h=hashlib.sha256()
    with con.conn.cursor() as cur:
        with cur.copy('COPY ('+sql+') TO STDOUT') as cp:
            for b in cp:h.update(b)
    return h.hexdigest()


def protegidos(con):
    return {k:fingerprint(con,q) for k,q in {
      'cotizaciones_1':'SELECT * FROM tarificador.fact_cotizacion_servicio WHERE escenario_id=1 ORDER BY nro_viaje',
      'supuestos_1':'SELECT * FROM tarificador.fact_cotizacion_supuesto WHERE escenario_id=1 ORDER BY nro_viaje',
      'costos_1_3_4':'SELECT * FROM escenarios.fact_costo_viaje WHERE sk_escenario<>2 ORDER BY nro_viaje,sk_escenario,sk_componente',
      'virtuales':'SELECT * FROM escenarios.fact_costo_virtual ORDER BY id_viaje,sk_escenario,sk_componente',
      'combustible_certificado':'SELECT * FROM combustible.fact_viaje_combustible ORDER BY id_calculo',
      'absorcion':'SELECT * FROM costos.fact_absorcion_viaje ORDER BY id_viaje,bolsa',
      'parametros_costos':'SELECT * FROM costos.parametro_costo ORDER BY parametro,metodo,ambito,vigente_desde',
      'tarificador_estandar_referencia':'SELECT nro_viaje,km_tramo,precio_base,precio_ruta,pct_base,recargo_precio,precio_ponderado,rendimiento_tarificador,litros_tarificador,costo_tarificador FROM combustible.fact_viaje_combustible_tarificador ORDER BY nro_viaje',
    }.items()}


def verificar_referencia(con,q,folder):
    """Referencia independiente en NumPy: búsqueda por fecha y mediana de IDs certificados."""
    ref=con.df('SELECT * FROM combustible.v_rendimiento_tarificador_certificado WHERE nro_viaje=ANY(%s)',
        (q.nro_viaje.astype(int).tolist(),))
    b=con.df('SELECT equipo_id,bloque,t_fin,rend_cert FROM combustible.fact_bloque_certificacion WHERE certificado ORDER BY equipo_id,t_fin,bloque')
    historiales={int(k):v for k,v in b.groupby('equipo_id')}
    errores=[]
    for r in ref.itertuples():
        h=historiales.get(int(r.equipo_id)) if pd.notna(r.equipo_id) else None
        rendimiento=np.nan;ids=[];supuesto=False
        if h is not None:
            fechas=h.t_fin.to_numpy(dtype='datetime64[ns]')
            fecha=np.datetime64(pd.to_datetime(str(r.fecha_documental_id),format='%Y%m%d'),'ns')
            corte=int(np.searchsorted(fechas,fecha,side='left'))
            if corte>0:
                elegidos=h.iloc[max(0,corte-int(r.ventana_bloques)):corte]
                rendimiento=float(np.median(elegidos.rend_cert.to_numpy()))
                ids=elegidos.bloque.astype(int).tolist()[::-1]
            elif int(r.fecha_documental_id)>=20250101 and fecha<fechas[0]:
                rendimiento=float(h.iloc[0].rend_cert);ids=[int(h.iloc[0].bloque)];supuesto=True
        coincide=(pd.isna(rendimiento) and pd.isna(r.rendimiento_camion)) or (
            pd.notna(rendimiento) and pd.notna(r.rendimiento_camion) and abs(rendimiento-r.rendimiento_camion)<1e-12)
        if not coincide or ids!=list(r.bloques_utilizados_ids or []) or supuesto!=r.rendimiento_supuesto:
            errores.append(dict(nro_viaje=r.nro_viaje,esperado=rendimiento,obtenido=r.rendimiento_camion,ids_esperados=ids))
    if len(ref)!=len(q):raise ValueError('Faltan referencias por servicio')
    ref.to_csv(folder/'rendimiento_por_servicio.csv.gz',index=False)
    (folder/'contraste_numpy.json').write_text(json.dumps(dict(servicios=len(ref),diferencias=errores),indent=2,default=str),encoding='utf-8')
    if errores:raise ValueError('Difiere la selección certificada de la referencia NumPy')
    return ref


def actualizar(con,q):
    sup=q.loc[q.supuestos.notna(),['nro_viaje','escenario_id','supuestos']]
    con.execute('DELETE FROM tarificador.fact_cotizacion_supuesto WHERE escenario_id=2')
    con.copiar_df('tarificador.fact_cotizacion_supuesto',sup)
    campos=con.df("SELECT column_name FROM information_schema.columns WHERE table_schema='tarificador' AND table_name='fact_cotizacion_servicio' ORDER BY ordinal_position").column_name.tolist()
    con.execute('DELETE FROM tarificador.fact_cotizacion_servicio WHERE escenario_id=2')
    con.copiar_df('tarificador.fact_cotizacion_servicio',q[campos])
    reemplazar_fact(con,q,escenarios=(2,))
    # Mantener coherente la referencia legada que también recalcula el constructor.
    # Allí se conserva el historial previo; el supuesto posterior pertenece al exacto E2.
    con.execute('''UPDATE combustible.fact_viaje_combustible_tarificador c SET
      rendimiento_camion=r.rendimiento_previo,bloques_rendimiento=r.bloques_previos,
      fecha_ultimo_bloque=r.fecha_ultimo_previo,
      litros_rend_camion=CASE WHEN c.km_tramo>0 THEN c.km_tramo/r.rendimiento_previo END,
      costo_rend_camion=CASE WHEN c.km_tramo>0 THEN c.km_tramo/r.rendimiento_previo*c.precio_ponderado END,
      estado=CASE WHEN c.km_tramo IS NULL OR c.km_tramo<=0 THEN 'sin km'
        WHEN c.precio_ponderado IS NULL THEN 'sin precio'
        WHEN r.rendimiento_previo IS NULL THEN 'sin rendimiento del camión' ELSE 'ok' END
      FROM combustible.v_rendimiento_tarificador_certificado r WHERE r.nro_viaje=c.nro_viaje''')


def comparacion(antes,nuevo):
    if not antes.nro_viaje.equals(nuevo.nro_viaje):raise ValueError('Cambió el conjunto de servicios')
    a=antes.set_index('nro_viaje');b=nuevo.set_index('nro_viaje')
    # Únicamente el combustible puede cambiar entre los componentes calculados.
    columnas=['ingreso_estimado','i4','f15','i7','i8','i9','i10','i11','i12','i13','i14','i15','i16','factor_retorno']
    errores={}
    for col in columnas:
        x=pd.to_numeric(a[col],errors='coerce');y=pd.to_numeric(b[col],errors='coerce')
        n=int((~((x.isna()&y.isna())|((x-y).abs()<1e-7))).sum())
        if n:errores[col]=n
    if errores:raise ValueError('Cambió otro insumo o componente: '+str(errores))
    comunes=a.evaluable&b.evaluable
    delta=(b.i17-a.i17)-(b.i6-a.i6)*b.factor_retorno
    if delta[comunes].abs().gt(.00001).any():raise ValueError('Cambio de I17 no explicado solo por combustible')
    return dict(servicios=len(b),evaluables_antes=int(a.evaluable.sum()),evaluables_despues=int(b.evaluable.sum()),
      rendimiento_cambiado=int((~(a.rendimiento.eq(b.rendimiento)|(a.rendimiento.isna()&b.rendimiento.isna()))).sum()),
      gana_cobertura=int((~a.evaluable&b.evaluable).sum()),pierde_cobertura=int((a.evaluable&~b.evaluable).sum()),
      otros_componentes_modificados=0,max_residuo_cambio_i17=float(delta[comunes].abs().max()))


def principal(argv=None):
    p=argparse.ArgumentParser(description=__doc__);m=p.add_mutually_exclusive_group()
    m.add_argument('--guardar',action='store_true');m.add_argument('--validar',action='store_true');a=p.parse_args(argv)
    out=ROOT/'validacion/escenario2_certificado'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    info=dict(version=VERSION,confirmado=False,estado='iniciado',powerbi_modificado=False)
    ent=Entradas();con=None
    try:
        for n in ['tarificador/publicar_rendimiento_certificado.py','tarificador/exacto/modelo.py',
          'tarificador/exacto/publicacion.py','tarificador/exacto/calculo.py','combustible/rendimiento_tarificador_certificado.py']:
            ent.registrar(ROOT/'code'/n)
        cfg=db.leer_config()['postgres']
        if cfg.get('host','localhost').lower() not in ('localhost','127.0.0.1','::1'):raise RuntimeError('Solo PostgreSQL local')
        con=db.conectar();con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        con.execute("SET LOCAL statement_timeout='10min'; SET LOCAL lock_timeout='5s'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:raise RuntimeError('Otra carga en curso')
        print('Respaldando y protegiendo escenarios 1/3/4...',flush=True)
        info['huellas_antes']=protegidos(con)
        anterior=con.df('SELECT * FROM tarificador.fact_cotizacion_servicio WHERE escenario_id=2 ORDER BY nro_viaje')
        anterior.to_csv(out/'escenario2_antes.csv.gz',index=False)
        print('Seleccionando últimos bloques certificados y recalculando E2...',flush=True)
        from combustible.rendimiento_tarificador_certificado import publicar as publicar_referencia
        publicar_referencia(con)
        info['limites']=probar_limites(con)
        data=preparar(con);q=data[0].query('escenario_id==2').sort_values('nro_viaje').reset_index(drop=True)
        ref=verificar_referencia(con,q,out)
        info['comparacion']=comparacion(anterior.reset_index(drop=True),q)
        actualizar(con,q)
        info['controles_tarificador']=controles(con)
        from tarificador.publicar_tarificador_exacto import ORACLE,validar_excel_dw
        for carpeta in [ORACLE,ORACLE/'cinco_puntos']:
            aceptacion=json.loads((carpeta/'comparacion_excel.json').read_text(encoding='utf-8'))
            if not aceptacion['aceptado'] or aceptacion['diferencias']:raise ValueError('Oráculo Excel no aceptado')
            for nombre in ['casos_entradas.json','casos_esperados.json','excel_resultados.json']:
                ent.registrar(carpeta/nombre)
                if hashlib.sha256((carpeta/nombre).read_bytes()).hexdigest()!=aceptacion['archivos_sha256'][nombre]:
                    raise ValueError('Oráculo Excel modificado: '+nombre)
        info['excel_dw']=validar_excel_dw(con,out)
        info['referencia']=dict(servicios=len(ref),diferencias_numpy=0,
          con_historia=int(ref.rendimiento_camion.notna().sum()),
          supuestos_posteriores=int(ref.rendimiento_supuesto.sum()),sin_historia=int(ref.rendimiento_camion.isna().sum()))
        q.to_csv(out/'escenario2_despues.csv.gz',index=False)
        info['cobertura_desde_2025']=con.df('''SELECT escenario_id,count(*) AS candidatos,count(*) FILTER(WHERE evaluable) AS evaluables
          FROM tarificador.fact_cotizacion_servicio WHERE fecha_documental_id>=20250101 GROUP BY 1 ORDER BY 1''').to_dict('records')
        info['huellas_despues']=protegidos(con)
        if info['huellas_antes']!=info['huellas_despues']:raise ValueError('Cambió un hecho protegido fuera del escenario 2')
        ent.verificar()
        if a.guardar:
            con.execute('INSERT INTO comun.registro_carga(area,fecha_carga,version_script,detalle) VALUES(%s,CURRENT_TIMESTAMP,%s,%s)',
              ('tarificador_rendimiento_certificado',VERSION,str(out)))
            con.commit();info.update(confirmado=True,estado='confirmado')
        else:con.conn.rollback();info['estado']='validado_y_revertido'
    except BaseException as e:
        if con:con.conn.rollback()
        info.update(estado='fallido',error=str(e));raise
    finally:
        if con:con.close()
        info['entradas']=list(ent.archivos.values())
        (out/'INFORME.json').write_text(json.dumps(info,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Evidencia: '+str(out),flush=True)
    print(json.dumps({k:info[k] for k in ['estado','comparacion','referencia','cobertura_desde_2025']},ensure_ascii=False,indent=2),flush=True)
    return info


if __name__=='__main__':principal()
