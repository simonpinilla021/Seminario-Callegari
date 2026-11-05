"""Regla única de selección del tarificador, con equivalencia Python/PostgreSQL.

Ámbito específico antes que todos; dentro del ámbito, BBDD antes que manual.
Vigencias [desde, hasta). Un solape del mismo origen no se desempata.
"""
from datetime import date

import numpy as np
import pandas as pd

FIN = '9999-12-31'
CONSULTA = """SELECT d.codigo,v.ambito,v.valor,v.origen,
  v.vigente_desde::text AS vigente_desde,v.vigente_hasta::text AS vigente_hasta,v.nota
  FROM tarificador.fact_parametro_valor v
  JOIN tarificador.dim_parametro d USING(sk_parametro)"""


class Parametros:
    def __init__(self, filas):
        self.t = filas.copy()
        requeridas = {'codigo','ambito','valor','origen','vigente_desde','vigente_hasta'}
        if not requeridas <= set(self.t):
            raise ValueError(f'Faltan columnas de parámetros: {sorted(requeridas-set(self.t))}')
        for c in ('codigo','ambito'):
            if self.t[c].isna().any() or self.t[c].astype(str).str.strip().eq('').any():
                raise ValueError(f'Parámetro sin {c}')
        if not self.t.origen.isin(['manual','BBDD']).all():
            raise ValueError('Origen de parámetro desconocido: solo manual o BBDD')
        self.t['valor'] = pd.to_numeric(self.t.valor, errors='coerce')
        if not np.isfinite(self.t.valor.astype(float)).all():
            raise ValueError('Valor de parámetro ausente o no finito')
        # Comparar fechas ISO completas antes de adaptar el centinela a pandas.
        for c in ('vigente_desde','vigente_hasta'):
            self.t[c] = self.t[c].map(lambda x: date.fromisoformat(str(x)).isoformat())
        if self.t.vigente_desde.ge(self.t.vigente_hasta).any():
            raise ValueError('Vigencia vacía o invertida')
        for clave, g in self.t.groupby(['codigo','ambito','origen']):
            g = g.sort_values('vigente_desde')
            limite = None
            for r in g.itertuples():
                if limite is not None and r.vigente_desde < limite:
                    raise ValueError(f'Vigencias solapadas para {clave}')
                limite = max(limite or r.vigente_hasta, r.vigente_hasta)
        self.t['desde'] = pd.to_datetime(self.t.vigente_desde)
        self.t['hasta'] = pd.to_datetime(self.t.vigente_hasta.replace(FIN,'2262-04-11'))
        self.t['prio'] = self.t.origen.eq('BBDD').astype(int)

    def detalle(self, codigo, fechas, ambito='todos'):
        """Devuelve valor y procedencia efectiva; ausencia conserva NaN/None."""
        out = pd.DataFrame(index=fechas.index, columns=['valor','origen','ambito_efectivo',
                                                      'vigente_desde','vigente_hasta'])
        out['valor'] = np.nan
        # Todos primero, luego el ámbito específico; origen BBDD dentro de cada uno.
        for amb in dict.fromkeys(['todos', ambito]):
            for r in self.t[self.t.codigo.eq(codigo) & self.t.ambito.eq(amb)].sort_values('prio').itertuples():
                m = fechas.ge(r.desde) & fechas.lt(r.hasta)
                out.loc[m, 'valor'] = float(r.valor)
                out.loc[m, 'origen'] = r.origen
                out.loc[m, 'ambito_efectivo'] = r.ambito
                out.loc[m, 'vigente_desde'] = r.vigente_desde
                out.loc[m, 'vigente_hasta'] = r.vigente_hasta
        return out

    def en(self, codigo, fechas, ambito='todos'):
        out = pd.Series(np.nan, index=fechas.index)
        for amb in dict.fromkeys(['todos', ambito]):
            for r in self.t[self.t.codigo.eq(codigo) & self.t.ambito.eq(amb)].sort_values('prio').itertuples():
                out.loc[fechas.ge(r.desde) & fechas.lt(r.hasta)] = float(r.valor)
        return out

    def uno(self, codigo, fecha, ambito='todos'):
        return self.en(codigo, pd.Series([pd.Timestamp(fecha)]), ambito).iloc[0]


# Misma SELECT para auditoría de lectura y función publicada. "pedidos" es una
# relación con codigo, fecha y ambito. LEFT JOIN conserva solicitudes sin dato.
SQL_ELEGIR = """
WITH candidatos AS (
  SELECT p.codigo,p.fecha,p.ambito AS ambito_solicitado,
    v.ambito AS ambito_efectivo,v.valor,v.origen,v.vigente_desde,v.vigente_hasta,v.nota,
    RANK() OVER(PARTITION BY p.codigo,p.fecha,p.ambito
      ORDER BY (v.ambito=p.ambito) DESC,(v.origen='BBDD') DESC) AS prioridad
  FROM pedidos p
  LEFT JOIN tarificador.dim_parametro d ON d.codigo=p.codigo
  LEFT JOIN tarificador.fact_parametro_valor v ON v.sk_parametro=d.sk_parametro
    AND v.ambito IN (p.ambito,'todos')
    AND p.fecha>=v.vigente_desde AND p.fecha<v.vigente_hasta)
SELECT c.codigo,c.fecha,c.ambito_solicitado,c.ambito_efectivo,c.valor,c.origen,c.vigente_desde,c.vigente_hasta,c.nota
FROM candidatos c WHERE c.prioridad=1
"""

# STABLE y SECURITY INVOKER: consulta con los permisos del llamador. RANK
# conserva empates para rechazarlos, sin LIMIT 1 ni orden físico arbitrario.
SQL_RESOLVER = """
CREATE OR REPLACE FUNCTION tarificador.resolver_parametro(
  p_codigo text, p_fecha date, p_ambito text DEFAULT 'todos')
RETURNS TABLE(ambito_efectivo text, valor double precision, origen text,
              vigente_desde date, vigente_hasta date, nota text)
LANGUAGE plpgsql STABLE AS $resolver$
DECLARE cantidad bigint;
BEGIN
  RETURN QUERY
  WITH pedidos AS (SELECT p_codigo AS codigo,p_fecha AS fecha,p_ambito AS ambito),
  seleccion AS (""" + SQL_ELEGIR + """)
  SELECT c.ambito_efectivo,c.valor,c.origen,c.vigente_desde,c.vigente_hasta,c.nota
  FROM seleccion c WHERE c.ambito_efectivo IS NOT NULL;
  GET DIAGNOSTICS cantidad = ROW_COUNT;
  IF cantidad>1 THEN
    RAISE EXCEPTION 'Parametros ambiguos: codigo=%, fecha=%, ambito=%',p_codigo,p_fecha,p_ambito;
  END IF;
END;
$resolver$;
"""

SQL_VISTAS = """
CREATE OR REPLACE VIEW tarificador.v_parametro_vigente AS
WITH pedidos AS (
  SELECT DISTINCT sk_parametro,ambito FROM tarificador.fact_parametro_valor
  WHERE CURRENT_DATE>=vigente_desde AND CURRENT_DATE<vigente_hasta)
SELECT d.codigo,d.nombre,d.categoria,d.unidad,p.ambito,r.valor,r.origen,r.vigente_desde,r.nota
FROM pedidos p JOIN tarificador.dim_parametro d USING(sk_parametro)
CROSS JOIN LATERAL tarificador.resolver_parametro(d.codigo,CURRENT_DATE,p.ambito) r;

CREATE OR REPLACE VIEW tarificador.v_parametro_vigente_candidatos AS
SELECT d.codigo,d.nombre,v.ambito,v.valor,v.origen,v.vigente_desde,v.vigente_hasta,v.nota
FROM tarificador.fact_parametro_valor v JOIN tarificador.dim_parametro d USING(sk_parametro)
WHERE CURRENT_DATE>=v.vigente_desde AND CURRENT_DATE<v.vigente_hasta;
"""
