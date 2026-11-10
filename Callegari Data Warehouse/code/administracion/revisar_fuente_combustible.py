"""Audita vista ERP, tabla base y cargas históricas ausentes. Solo lectura; no cambia ETL."""
import argparse
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
from comun import db,ingesta


def leer(ruta,anio):
    d=pd.read_parquet(ruta,columns=['sit_petroleum_id','datetrx','liters','oilsupplier'])
    d['datetrx']=pd.to_datetime(d.datetrx,format='ISO8601',errors='raise')
    d['sit_petroleum_id']=pd.to_numeric(d.sit_petroleum_id,errors='raise').astype('Int64')
    d['liters']=pd.to_numeric(d.liters,errors='raise')
    return d[d.datetrx.dt.year.eq(anio)].copy()


def resumen(d):
    return d.assign(mes=d.datetrx.dt.strftime('%Y-%m')).groupby(['mes','oilsupplier'],dropna=False).agg(
        cargas=('sit_petroleum_id','size'),litros=('liters','sum')).reset_index().to_dict('records')


def consultar(con,sql,params=()):
    with con.cursor() as cur:
        cur.execute(sql,params)
        columnas=[d.name for d in cur.description]
        return [dict(zip(columnas,fila)) for fila in cur.fetchall()]


def auditar(nueva,anio=2025):
    _,raiz,_=ingesta.configuracion()
    run=raiz/'auditorias_combustible'/(ingesta.ahora().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    run.mkdir(parents=True)
    historico=next(db.ruta_config('fuentes','combustible').glob('*guiascombust*.parquet'))
    nuevo=leer(nueva,anio);anterior=leer(historico,anio)
    faltantes=anterior[~anterior.sit_petroleum_id.isin(nuevo.sit_petroleum_id)].copy()
    informe=dict(estado='iniciada',anio=anio,solo_lectura=True,
        nueva=str(Path(nueva).resolve()),historico=str(historico),
        nueva_sha256=ingesta.huella(nueva),historico_sha256=ingesta.huella(historico),
        resumen_historico=resumen(anterior),resumen_nuevo=resumen(nuevo),
        cargas_historicas_ausentes=len(faltantes),resumen_ausentes=resumen(faltantes))
    faltantes.to_csv(run/'cargas_historicas_ausentes.csv',index=False)
    ids=[int(i) for i in faltantes.sit_petroleum_id]
    # Identificadores convertidos a int; consulta local para un usuario TI con permisos.
    lista=','.join(map(str,ids))
    consulta_ti="""-- Ejecutar en solo lectura por TI. No modifica datos ni vistas.
WITH historico AS (SELECT unnest(ARRAY["""+lista+"""]::numeric[]) AS sit_petroleum_id)
SELECT h.sit_petroleum_id,a.datetrx,a.liters,a.oilsupplier,a.ad_client_id,a.isactive,
 a.m_product_id,b.m_product_category_id,
 CASE WHEN a.sit_petroleum_id IS NULL THEN 'no_encontrada_en_tabla_base'
      WHEN a.ad_client_id IS DISTINCT FROM 1000007 THEN 'empresa_distinta'
      WHEN a.isactive IS DISTINCT FROM 'Y' THEN 'registro_inactivo'
      WHEN b.m_product_id IS NULL THEN 'producto_no_encontrado'
      WHEN b.m_product_category_id IS DISTINCT FROM 1000008 THEN 'categoria_excluida'
      ELSE 'cumple_filtros_revisar_vista' END AS diagnostico,
 EXISTS(SELECT 1 FROM adempiere.sit_pbix_guiascombust_v v WHERE v.sit_petroleum_id=h.sit_petroleum_id) AS en_vista
FROM historico h LEFT JOIN adempiere.sit_petroleum a USING(sit_petroleum_id)
LEFT JOIN adempiere.m_product b ON b.m_product_id=a.m_product_id
ORDER BY h.sit_petroleum_id;
"""
    (run/'diagnostico_para_ti.sql').write_text(consulta_ti,encoding='utf-8')
    con=None
    try:
        con=ingesta.conexion_erp()
        con.execute("SET LOCAL statement_timeout='60s'")
        if con.execute('SHOW transaction_read_only').fetchone()[0]!='on': raise RuntimeError('Conexión no es de solo lectura')
        definicion=con.execute("SELECT pg_get_viewdef('adempiere.sit_pbix_guiascombust_v'::regclass,true)").fetchone()[0]
        (run/'sit_pbix_guiascombust_v.sql').write_text('-- Definición vigente consultada en solo lectura\n'+definicion+'\n',encoding='utf-8')
        inicio,fin=date(anio,1,1),date(anio+1,1,1)
        informe['vista_actual_mensual']=consultar(con,"""SELECT to_char(datetrx,'YYYY-MM') AS mes,oilsupplier,
            count(*) AS cargas,sum(liters) AS litros FROM adempiere.sit_pbix_guiascombust_v
            WHERE datetrx >= %s AND datetrx < %s GROUP BY 1,2 ORDER BY 1,2""",(inicio,fin))
        con.execute('SAVEPOINT diagnostico_base')
        try:
            informe['tabla_base_mensual_por_filtros']=consultar(con,"""SELECT to_char(a.datetrx,'YYYY-MM') AS mes,
            a.oilsupplier,a.isactive,b.m_product_category_id,(b.m_product_id IS NOT NULL) AS producto_existe,
            count(*) AS cargas,sum(a.liters) AS litros
            FROM adempiere.sit_petroleum a LEFT JOIN adempiere.m_product b ON b.m_product_id=a.m_product_id
            WHERE a.ad_client_id=1000007 AND a.datetrx >= %s AND a.datetrx < %s GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4""",(inicio,fin))
            filas=consultar(con,"""SELECT a.sit_petroleum_id,a.ad_client_id,a.isactive,a.datetrx,a.liters,
            a.oilsupplier,a.m_product_id,b.m_product_category_id,(b.m_product_id IS NOT NULL) AS producto_existe,
            EXISTS(SELECT 1 FROM adempiere.sit_pbix_guiascombust_v v WHERE v.sit_petroleum_id=a.sit_petroleum_id) AS en_vista_actual
            FROM adempiere.sit_petroleum a LEFT JOIN adempiere.m_product b ON b.m_product_id=a.m_product_id
            WHERE a.sit_petroleum_id=ANY(%s)""",(ids,)) if ids else []
            encontrados={int(f['sit_petroleum_id']) for f in filas}
            informe['historicas_ausentes_diagnostico_base']=filas
            informe['ids_no_encontrados_en_tabla_base']=[i for i in ids if i not in encontrados]
            informe['estado']='auditado_sin_cambios'
        except Exception as error:
            if getattr(error,'sqlstate',None)!='42501': raise
            con.execute('ROLLBACK TO SAVEPOINT diagnostico_base')
            informe.update(estado='auditado_vista_sin_permiso_tabla_base',
                limite='Usuario ERP sin permiso SELECT en tabla base; causa de exclusión pendiente de TI',
                sqlstate_tabla_base=error.sqlstate)
        finally:
            con.execute('RELEASE SAVEPOINT diagnostico_base')
        return run,informe
    except BaseException as e:
        informe.update(estado='fallida',tipo_error=type(e).__name__,sqlstate=getattr(e,'sqlstate',None))
        raise
    finally:
        if con is not None: con.rollback();con.close()
        ingesta.escribir_json(run/'informe.json',informe)
        print('Informe: '+str(run/'informe.json'),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nueva',type=Path,required=True)
    p.add_argument('--anio',type=int,default=2025)
    p.add_argument('--ejecutar',action='store_true')
    a=p.parse_args()
    if not a.ejecutar: print('Plan: comparar respaldo/descarga y consultar definición/filtros ERP en solo lectura.')
    else:
        run,r=auditar(a.nueva,a.anio)
        print(json.dumps({k:r[k] for k in ('estado','cargas_historicas_ausentes','resumen_ausentes')},ensure_ascii=False,indent=2,default=str))
