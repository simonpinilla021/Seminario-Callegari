"""Cotización modelada: conserva las fórmulas acordadas y explicita faltantes.

No altera costos físicos, escenarios ni el Excel fuente. Los componentes están
antes del factor de retorno; subtotal_conocido y costo_total lo incluyen.
"""
import numpy as np
import pandas as pd

COMPONENTES = ('combustible', 'termo', 'peaje', 'costo_fijo',
               'reposicion', 'neumaticos', 'intermedios')
NUEVAS_COLUMNAS = {
    'subtotal_componentes_conocidos': 'DOUBLE PRECISION',
    'subtotal_conocido': 'DOUBLE PRECISION',
    'costo_completo': 'BOOLEAN',
    'cotizacion_completa': 'BOOLEAN',
    'n_componentes_faltantes': 'INTEGER',
    'faltantes': 'TEXT',
}


def factor_termo_excel(clasificacion):
    """planilla!I7: solo ida (retorno) divide el termo por dos."""
    return pd.Series(np.where(clasificacion.eq('Retorno'), .5, 1.), index=clasificacion.index)


def calcular_cotizaciones(v, par, n_int):
    """v ya tiene tipo de carga, precio y peaje a la fecha; no hace consultas.

    Las dependencias no aplicables son cero (seco, tractoreo o intermedios
    desactivados). Las ausentes o inválidas son NaN y quedan en faltantes.
    Aplica la mitad del termo en retornos, conforme a planilla!I7 del Excel.
    """
    f, idx = v['fecha'], v.index
    cl = v['clasificacion_viaje']
    envio, retorno = cl.eq('Envio'), cl.eq('Retorno')
    aplica = envio | retorno
    trac = v['es_tractoreo'].eq('S')
    problemas = pd.DataFrame(index=idx)

    def numero(s, codigo, requerido=aplica, positivo=False):
        s = pd.to_numeric(s, errors='coerce').astype(float)
        bien = np.isfinite(s) & (s.gt(0) if positivo else s.ge(0))
        problemas[codigo] = requerido & ~bien
        return s.where(bien)

    def parametro(codigo, requerido=aplica, positivo=False, ambito='todos'):
        return numero(par.en(codigo, f, ambito), codigo, requerido, positivo)

    km = numero(v['km'], 'km', positivo=True)
    km_dia = parametro('km_dia', positivo=True)
    rend = parametro('rendimiento_kml', positivo=True)
    precio = numero(v['precio_tarif'], 'precio')
    peaje = numero(v['peaje_tarif'], 'peaje')
    fijo = parametro('costo_fijo_diario')
    rep = numero(par.en('reposicion_diaria', f).where(~trac, par.en('reposicion_diaria', f, 'tractoreo')),
                 'reposicion_diaria')
    neu = numero(par.en('costo_neumatico_km', f).where(~trac, par.en('costo_neumatico_km', f, 'tractoreo')),
                 'costo_neumatico_km')
    factor = parametro('factor_retorno', requerido=retorno, positivo=True)
    margen = parametro('margen')
    redondeo = parametro('redondeo', positivo=True)
    problemas['configuracion_tractoreo'] = aplica & ~v['es_tractoreo'].isin(['S', 'N'])
    cargado = pd.to_numeric(v['ingreso'], errors='coerce').gt(0)
    requiere_tipo = aplica & cargado & ~trac
    tipos = v['tipo_carga']
    problemas['tipo_carga'] = requiere_tipo & ~tipos.isin(['seco', 'sin termo', 'frio', 'congelado', 'multi'])
    requiere_termo = requiere_tipo & tipos.isin(['frio', 'congelado', 'multi'])
    lh = pd.Series(0., index=idx)
    for amb in ('frio', 'congelado', 'multi'):
        m = requiere_termo & tipos.eq(amb)
        tasa = pd.to_numeric(par.en('termo_lh', f, amb), errors='coerce').astype(float)
        bien = np.isfinite(tasa) & tasa.ge(0)
        problemas[f'termo_lh_{amb}'] = m & ~bien
        lh.loc[m] = tasa.where(bien).loc[m]

    mult = pd.Series(np.where(envio, 2., np.where(retorno, 1., np.nan)), index=idx)
    kmq, dias = km * mult, km * mult / km_dia
    q = v[['nro_viaje', 'sk_fecha', 'clasificacion_viaje']].copy()
    q['modalidad'] = np.select([envio, retorno], ['ida y vuelta', 'solo ida × factor de retorno'], 'no se cotiza')
    q['tipo_carga'], q['es_tractoreo'], q['n_intermedios'] = tipos, v['es_tractoreo'], n_int
    q['km_cotizados'], q['dias'] = kmq, dias
    q['combustible'] = kmq / rend * precio
    q['termo'] = (km / km_dia * 24 * lh * precio * factor_termo_excel(cl)).where(requiere_termo, 0.)
    q.loc[problemas['tipo_carga'] | problemas['configuracion_tractoreo'], 'termo'] = np.nan
    q['peaje'] = peaje * mult
    q['costo_fijo'] = np.maximum(dias, 1.) * fijo
    q['reposicion'] = np.ceil(dias) * rep
    q['neumaticos'] = kmq * neu
    q.loc[problemas['configuracion_tractoreo'], ['reposicion', 'neumaticos']] = np.nan
    cobra = pd.to_numeric(par.en('cobra_intermedios', f), errors='coerce')
    # Sin destinos intermedios no hace falta tasa ni selector de cobro.
    hay_int = aplica & (n_int.gt(0) | n_int.isna())
    problemas['n_intermedios'] = aplica & (n_int.isna() | n_int.lt(0) | n_int.mod(1).ne(0))
    problemas['cobra_intermedios'] = hay_int & ~cobra.isin([0., 1.])
    cobrado = hay_int & cobra.eq(1.)
    valor = parametro('valor_intermedio', requerido=cobrado)
    q['intermedios'] = (n_int * valor).where(cobrado, 0.)
    q.loc[problemas['n_intermedios'] | problemas['cobra_intermedios'], 'intermedios'] = np.nan

    problemas['componente_no_finito'] = aplica & np.isinf(q[list(COMPONENTES)]).any(axis=1)
    q[list(COMPONENTES)] = q[list(COMPONENTES)].replace([np.inf, -np.inf], np.nan)
    q['n_componentes_faltantes'] = q[list(COMPONENTES)].isna().sum(axis=1).where(aplica, 0).astype(int)
    q['subtotal_componentes_conocidos'] = q[list(COMPONENTES)].sum(axis=1, min_count=1)
    q['subtotal_conocido'] = q['subtotal_componentes_conocidos'].where(~retorno,
                                    q['subtotal_componentes_conocidos'] * factor)
    problemas['subtotal_no_finito'] = aplica & np.isinf(q['subtotal_conocido'])
    problemas = problemas & aplica.to_numpy()[:, None]
    problemas_costo = problemas.drop(columns=['margen', 'redondeo'])
    q['costo_completo'] = aplica & ~problemas_costo.any(axis=1) & q['n_componentes_faltantes'].eq(0)
    q['costo_total'] = q['subtotal_conocido'].where(q['costo_completo'])
    q['margen'] = q['costo_total'] * margen
    q['cotizacion'] = np.ceil((q['costo_total'] + q['margen']) / redondeo) * redondeo
    problemas['resultado_no_finito'] = aplica & ~problemas.any(axis=1) & ~np.isfinite(q['cotizacion'])
    q['cotizacion_completa'] = aplica & ~problemas.any(axis=1) & q['costo_completo']
    q['cotizacion'] = q['cotizacion'].where(q['cotizacion_completa'])
    q['faltantes'] = problemas.apply(lambda r: '; '.join(problemas.columns[r]), axis=1)
    q['estado'] = np.where(~aplica, 'no se cotiza (vacío o sin clasificación)',
                          np.where(q['cotizacion_completa'], 'ok', 'incompleta'))
    numericas = ['km_cotizados', 'dias', *COMPONENTES, 'costo_total', 'margen', 'cotizacion',
                 'subtotal_componentes_conocidos', 'subtotal_conocido']
    q[numericas] = q[numericas].replace([np.inf, -np.inf], np.nan)
    q.loc[~aplica, numericas] = np.nan
    return q
