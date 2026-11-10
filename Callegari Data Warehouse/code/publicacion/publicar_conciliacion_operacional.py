"""Publicación aditiva de conciliación EERR; --validar revierte; --guardar confirma."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pandas as pd
from comun import db
from publicacion import conciliacion_operacional as modulo

TABLAS_PROTEGIDAS = ('escenarios.fact_costo_viaje', 'escenarios.fact_costo_virtual',
                     'ventas.fact_viaje_venta', 'costos.fact_contabilidad_mensual',
                     'costos.parametro_costo')
VISTAS = ('v_referencia_contable_mensual', 'v_fijos_escenario_mensual',
          'v_conciliacion_operacional_mensual', 'v_puente_operacional_mensual',
          'v_fijo_no_absorbido_mensual')


def huellas(con):
    return {t: con.uno(f"SELECT count(*),md5(string_agg(h,'' ORDER BY h)) FROM "
                       f"(SELECT md5(row_to_json(f)::text) AS h FROM {t} f) x")
            for t in TABLAS_PROTEGIDAS}


def comprobar_fuentes(con):
    """Recalcula cada mes con pandas y clasificación original, fuera de las vistas."""
    c = con.df('''SELECT f.anio*100+f.mes AS mes_id,f.monto,d.codigo,d.linea_eerr,
        d.clase_tarificador FROM costos.fact_contabilidad_mensual f
        JOIN costos.dim_cuenta_contable d USING(sk_cuenta)''')
    v = con.df('SELECT * FROM ds_gestion.v_referencia_contable_mensual')
    esperado = []
    for mes, d in c.groupby('mes_id'):
        def suma(mask):
            return float(d.loc[mask, 'monto'].sum())
        ingreso = -suma(d.linea_eerr.eq('001'))
        costo = suma(d.linea_eerr.isin(['002','003','004','005','006','007','008']))
        admin = suma(d.linea_eerr.eq('009'))
        esperado.append(dict(mes_id=mes, ingreso_explotacion=ingreso,
            costo_explotacion=costo, administracion_ventas=admin,
            ingreso_total=-suma(d.codigo.str.startswith('4')),
            gasto_total=suma(d.codigo.str.startswith('3')),
            fijo_operacional=suma(d.clase_tarificador.eq('fijo') & d.codigo.ne('310020104') &
                                  d.linea_eerr.isin(['002','003','004','005','006','007','008','009'])),
            resultado_operacional_antes_depreciacion=ingreso-costo-admin))
    e = pd.DataFrame(esperado).set_index('mes_id').sort_index()
    a = v.set_index('mes_id').sort_index()
    if not a.index.equals(e.index):
        raise ValueError('Meses contables distintos de la fuente')
    max_error = max(float((a[k].astype(float)-e[k]).abs().max()) for k in e.columns)
    if max_error > .01:
        raise ValueError(f'Diferencia contabilidad contra pandas: {max_error}')
    for fuente, consulta, columnas, clave in [
        ('fijos', '''WITH x AS (SELECT sk_fecha,sk_escenario,monto FROM escenarios.fact_costo_viaje WHERE sk_componente=4
            UNION ALL SELECT sk_fecha,sk_escenario,monto FROM escenarios.fact_costo_virtual WHERE sk_componente=4)
            SELECT sk_fecha/100 AS mes_id,sk_escenario,sum(monto::numeric) AS fijo_conocido
            FROM x WHERE sk_escenario IN(1,2,3) GROUP BY 1,2''', ['fijo_conocido'], ['mes_id','sk_escenario']),
        ('modelo', '''WITH x AS (SELECT sk_fecha,sk_escenario,monto FROM escenarios.fact_costo_viaje
            UNION ALL SELECT sk_fecha,sk_escenario,monto FROM escenarios.fact_costo_virtual)
            SELECT sk_fecha/100 AS mes_id,sk_escenario,sum(monto::numeric) AS costo_modelado_conocido,
            count(*) FILTER(WHERE monto IS NULL) AS montos_faltantes FROM x
            WHERE sk_escenario =3 GROUP BY 1,2''', ['costo_modelado_conocido','montos_faltantes'], ['mes_id','sk_escenario']),
        ('ingreso', '''SELECT sk_fecha/100 AS mes_id,sum(ingreso::numeric) AS ingreso_estimado_conocido
            FROM ventas.fact_viaje_venta GROUP BY 1''', ['ingreso_estimado_conocido'], ['mes_id']),
    ]:
        e2 = con.df(consulta).set_index(clave).sort_index()
        vista = 'v_fijos_escenario_mensual' if fuente=='fijos' else 'v_conciliacion_operacional_mensual'
        a2 = con.df(f'SELECT * FROM ds_gestion.{vista}')
        if fuente=='ingreso':
            a2 = a2[a2.sk_escenario.eq(4) & a2.hay_ventas]
        elif fuente=='modelo':
            a2 = a2[a2.hay_costos]
        a2 = a2.set_index(clave).sort_index()
        if not a2.index.equals(e2.index):
            raise ValueError(f'Claves fuente {fuente} distintas')
        for k in columnas:
            if not a2[k].isna().equals(e2[k].isna()):
                raise ValueError(f'Nulos fuente {fuente}/{k} distintos')
            error = float((a2[k].astype(float)-e2[k].astype(float)).abs().max())
            if error > .01:
                raise ValueError(f'Diferencia fuente {fuente}/{k}: {error}')
    return dict(meses_contables=len(e), max_error_contable=max_error,
                fuentes_verificadas=['contabilidad_pandas','fijos','costos_reales_y_virtuales','ingresos'])


def principal(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument('--validar', action='store_true')
    modo.add_argument('--guardar', action='store_true')
    args = ap.parse_args(argv)
    out = Path(__file__).resolve().parents[2]/'validacion/conciliacion_operacional_publicada'/datetime.now().strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True)
    info = dict(version=modulo.VERSION, confirmado=False, estado='iniciado')
    con = None
    try:
        con = db.conectar()
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='10min'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra publicación en curso')
        print('Registrando huellas de tablas protegidas...', flush=True)
        antes = huellas(con)
        respaldo = con.df("SELECT viewname,definition FROM pg_views WHERE schemaname='ds_gestion' AND viewname=ANY(%s)", (list(VISTAS),))
        respaldo.to_json(out/'definiciones_anteriores.json', orient='records', force_ascii=False, indent=2)
        print('Creando vistas y verificando controles...', flush=True)
        info.update(modulo.publicar(con))
        pd.DataFrame([dict(control=n, esperado=0, obtenido=v) for n,v in info['controles'].items()]).to_csv(
            out/'controles.csv', index=False)
        print('Comparando contra las fuentes independientes...', flush=True)
        info['verificacion_independiente'] = comprobar_fuentes(con)
        if huellas(con) != antes:
            raise ValueError('Cambió una tabla protegida')
        info['huellas_protegidas'] = antes
        info['tablas_protegidas_sin_cambios'] = True
        print('Exportando evidencia...', flush=True)
        for vista in VISTAS:
            con.df(f'SELECT * FROM ds_gestion.{vista}').to_csv(out/f'{vista}.csv', index=False)
        (out/'definiciones_publicadas.sql').write_text(modulo.SQL, encoding='utf-8')
        info['codigo_sha256'] = hashlib.sha256(Path(modulo.__file__).read_bytes()).hexdigest()
        if args.guardar:
            con.commit()
            info.update(estado='confirmado', confirmado=True)
            # Lectura posterior al commit: las vistas existen y siguen conciliadas.
            info['lectura_posterior_controles'] = {n: int(con.uno(s)[0]) for n,s in modulo.CONTROLES.items()}
            if any(info['lectura_posterior_controles'].values()):
                raise RuntimeError('Falló lectura posterior; publicación ya confirmada')
        else:
            con.conn.rollback()
            info['estado'] = 'validado_y_revertido'
    except BaseException as exc:
        if con is not None:
            con.conn.rollback()
        info.update(estado='error_posterior' if info['confirmado'] else 'fallido', error=str(exc))
        raise
    finally:
        if con is not None:
            con.close()
        (out/'INFORME.json').write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(out, flush=True)
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    principal()
