"""Conservación global con sumas decimales y diagnóstico de la suma flotante."""
from decimal import Decimal

TOLERANCIA = Decimal('0.01')


def diferencia(fisico, virtual, comercial, sin_imputar):
    return (fisico or Decimal(0)) + (virtual or Decimal(0)) - (comercial or Decimal(0)) - (sin_imputar or Decimal(0))


def concilia(fisico, virtual, comercial, sin_imputar):
    return abs(diferencia(fisico,virtual,comercial,sin_imputar)) <= TOLERANCIA


def consulta(escenario):
    if escenario not in (3,4):
        raise ValueError('Conciliación global disponible para escenarios 3 y 4')
    # Convertir ANTES de SUM. Convertir a decimal la suma float ya calculada
    # conservaría su error acumulado. ::text mantiene la representación del
    # importe almacenado; no redondear cada componente a pesos/centavos.
    return f"""
      WITH f AS (
        SELECT sum(monto::text::numeric) AS decimal,sum(monto) AS flotante
        FROM escenarios.fact_costo_viaje WHERE sk_escenario={escenario}),
      v AS (
        SELECT sum(monto::text::numeric) AS decimal,sum(monto) AS flotante,
          sum(monto::text::numeric) FILTER(WHERE nro_viaje_destino IS NULL) AS sin_imputar,
          sum(monto) FILTER(WHERE nro_viaje_destino IS NULL) AS sin_imputar_float
        FROM escenarios.fact_costo_virtual WHERE sk_escenario={escenario}),
      c AS (
        SELECT sum(subtotal_comercial_conocido_{escenario}::text::numeric) AS decimal,
          sum(subtotal_comercial_conocido_{escenario}) AS flotante
        FROM escenarios.v_costo_viaje_resumen)
      SELECT f.decimal,v.decimal,c.decimal,v.sin_imputar,
        coalesce(f.flotante,0)+coalesce(v.flotante,0)-coalesce(c.flotante,0)-coalesce(v.sin_imputar_float,0)
      FROM f CROSS JOIN v CROSS JOIN c
    """


def comprobar(conn):
    controles, diagnostico = {}, {}
    for escenario in (3,4):
        fila = conn.execute(consulta(escenario)).fetchone()
        delta = diferencia(*fila[:4])
        controles[f'conservacion_con_virtuales_{escenario}'] = int(not concilia(*fila[:4]))
        diagnostico[str(escenario)] = dict(
            costo_fisico=str(fila[0]),costo_virtual=str(fila[1]),
            costo_comercial=str(fila[2]),virtual_sin_imputar=str(fila[3]),
            diferencia_decimal=str(delta),diferencia_float=str(fila[4]),
            tolerancia_clp=str(TOLERANCIA))
        print(f'   Conservación {escenario}: diferencia decimal={delta} CLP; '
              f'float={fila[4]} CLP; tolerancia={TOLERANCIA} CLP',flush=True)
    return controles,diagnostico
