#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Lee el Excel del tarificador del gerente y deja en CSV lo que usa el área escenarios (no modifica el Excel).

  tarificador_out/parametros.csv   parámetro, valor, unidad, celda, fórmula: rendimiento, mezcla base/TCT, recargo del
                                   precio, km por día, L/h del termo, margen, factor de retorno, tasas diarias…
  tarificador_out/peajes.csv       origen, destino, peaje (hoja peajes, solo ida, $ por viaje)

Uso (en la carpeta MVP Data Warehouse):
    python escenarios\leer_tarificador.py  ["ruta\al\TARIFICADOR ... .xlsx"]
Sin ruta usa config.ini [fuentes] tarificador (por defecto la carpeta fuentes\tarificador, el .xlsx más reciente).
Necesita openpyxl (pip install openpyxl). Los CSV ya generados vienen en la carpeta, así que solo hace falta
volver a correrlo cuando llegue un tarificador nuevo.
"""
import csv
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
SALIDA = AQUI / "tarificador_out"

# parámetro: (hoja, celda del valor, unidad, nota)
CELDAS = {
    "rendimiento_kml":        ("planilla", "D27", "km/L", "Economía de combustible (Parametros B5 = planilla D27)"),
    "pct_base":               ("petroleo", "F14", "fracción", "% de base (granel) en el consumo total"),
    "pct_tct":                ("petroleo", "K14", "fracción", "% de tarjeta (TCT) en el consumo total"),
    "precio_base_neto":       ("petroleo", "D6", "$/L", "Neto granel de la carta (33.000 L) usado en el Excel"),
    "precio_tct_neto":        ("petroleo", "I6", "$/L", "Neto TCT usado en el Excel"),
    "costo_base":             ("petroleo", "F11", "$/L", "Costo real granel (neto + IE × (1 − 31 %))"),
    "costo_tct":              ("petroleo", "K11", "$/L", "Costo real TCT"),
    "precio_promedio":        ("petroleo", "H17", "$/L", "pct_base × costo_base + pct_tct × costo_tct"),
    "precio_usado":           ("planilla", "I4", "$/L", "Precio promedio + recargo"),
    "km_hora":                ("Parametros", "B35", "km/h", "Velocidad promedio"),
    "horas_dia":              ("Parametros", "B34", "h/día", "Horas de conducción + extras"),
    "km_dia":                 ("Parametros", "B36", "km/día", "km_hora × horas_dia: duración = km ÷ km_dia"),
    "termo_lh_frio":          ("Parametros", "E10", "L/h", "Termo frío"),
    "termo_lh_congelado":     ("Parametros", "E11", "L/h", "Termo congelado"),
    "costo_fijo_dia":         ("Parametros", "B24", "$/día", "Costo base día (hoja costos)"),
    "reposicion_dia":         ("Parametros", "H25", "$/día", "Pago diario de la reposición del camión"),
    "reposicion_dia_tractoreo": ("Parametros", "H29", "$/día", "Reposición diaria solo tracto"),
    "neumatico_km":           ("Parametros", "B21", "$/km", "18 neumáticos × precio ÷ vida útil"),
    "neumatico_km_tractoreo": ("Parametros", "B22", "$/km", "10 neumáticos (solo tracto)"),
    "margen":                 ("planilla", "J18", "fracción", "Margen sobre el costo total"),
    "factor_retorno":         ("planilla", "C7", "fracción", "Si el viaje es retorno, el costo total × este factor"),
    "dias_mes":               ("Parametros", "B7", "días", "Días trabajados al mes"),
}


def buscar_excel(arg):
    if arg:
        return Path(arg)
    from comun import db
    try:
        base = db.ruta_config("fuentes", "tarificador")
    except KeyError:
        base = (db.MVP / "fuentes" / "tarificador").resolve()
    if base.is_file():
        return base
    xs = sorted(base.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not xs:
        sys.exit(f"No hay .xlsx del tarificador en {base}")
    return xs[-1]


def principal(argv=None):
    import openpyxl
    argv = sys.argv[1:] if argv is None else argv
    ruta = buscar_excel(argv[0] if argv else None)
    wf = openpyxl.load_workbook(ruta, data_only=False)
    wv = openpyxl.load_workbook(ruta, data_only=True)
    SALIDA.mkdir(exist_ok=True)
    filas = []
    for par, (hoja, celda, unidad, nota) in CELDAS.items():
        filas.append([par, wv[hoja][celda].value, unidad, f"{hoja}!{celda}", wf[hoja][celda].value, nota])
    # recargo del precio: planilla I4 = petroleo!H17 + <recargo>
    f = str(wf["planilla"]["I4"].value or "")
    m = re.search(r"H17\s*\+\s*([\d.]+)", f)
    filas.append(["recargo_precio", float(m.group(1)) if m else 0.0, "$/L", "planilla!I4", f,
                  "Se suma al precio promedio (margen de seguridad del precio)"])
    vig = str(wv["petroleo"]["G1"].value or "")
    filas.append(["vigencia_texto", vig, "", "petroleo!G1", "", f"Archivo: {ruta.name}"])
    with open(SALIDA / "parametros.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["parametro", "valor", "unidad", "celda", "formula", "nota"])
        w.writerows(filas)
    # peajes: hoja peajes, nombres de destino en la fila 2 (desde C), orígenes en la columna B (desde la fila 3)
    ws = wv["peajes"]
    destinos = {c: ws.cell(2, c).value for c in range(3, 65) if ws.cell(2, c).value}
    peajes = []
    for r in range(3, 101):
        o = ws.cell(r, 2).value
        if not o or not isinstance(o, str):
            continue
        for c, d in destinos.items():
            v = ws.cell(r, c).value
            if isinstance(v, (int, float)):
                peajes.append([o.strip(), str(d).strip(), v])
    with open(SALIDA / "peajes.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["origen", "destino", "peaje"])
        w.writerows(peajes)
    print(f"Tarificador: {ruta.name}")
    for p in filas:
        print(f"   {p[0]:26s} {p[1]!s:>14}  {p[2]}")
    print(f"   peajes: {len(peajes):,} pares origen-destino → {SALIDA / 'peajes.csv'}")


if __name__ == "__main__":
    principal()
