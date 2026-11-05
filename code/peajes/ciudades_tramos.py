#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lista única de ciudades a partir de la tabla de tramos (sit_stretch).
Seminario BI — Transportes Callegari.

La columna "tramo" viene como  "Ciudad origen/Destino 1/Destino 2/.../Destino n".
Este script la separa por "/" y produce:

  tramos_ciudades/ciudades_unicas.csv       una fila por ciudad: cuántos tramos la usan y en qué papel
                                            (origen, intermedio o destino final), las distintas formas
                                            en que está escrita y un tramo de ejemplo
  tramos_ciudades/tramos_desglosados.csv    una fila por tramo: origen, intermedios, destino final,
                                            cantidad de puntos y km
  tramos_ciudades/tramos_por_tramo.csv      una fila por par de ciudades consecutivas de cada tramo
                                            (Santiago/Copiapó/Antofagasta -> Santiago-Copiapó y Copiapó-Antofagasta)
  tramos_ciudades/revisar.csv               lo que conviene mirar: nombres que podrían ser la misma ciudad,
                                            partes vacías, partes con números, tramos sin "/"

Uso (en la carpeta donde está el parquet):
  python ciudades_tramos.py
  python ciudades_tramos.py --archivo otra_ruta/adempiere_sit_pbix_traveltramo_v.parquet --columna tramo
  python ciudades_tramos.py --solo-activos            # solo filas con isactive = 'Y'

También se puede copiar a una celda de Jupyter: basta cambiar ARCHIVO y COLUMNA abajo y ejecutar principal([]).

Requisitos: pandas y pyarrow (los mismos que ya usas para leer los parquet).
"""
import argparse
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

ARCHIVO = "./parquet_output_prod - 07-10-2026/adempiere_sit_pbix_traveltramo_v.parquet"
COLUMNA = "tramo"
SEPARADOR = "/"
SALIDA = "tramos_ciudades"


def clave(texto):
    """Forma normalizada para comparar: mayúsculas, sin tildes, sin puntos ni dobles espacios. 'Copiapó ' -> 'COPIAPO'."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def leer(archivo):
    ruta = Path(archivo)
    if not ruta.exists():
        sys.exit(f"No encuentro {ruta.resolve()}")
    if ruta.suffix.lower() == ".parquet":
        return pd.read_parquet(ruta)
    return pd.read_csv(ruta, sep=None, engine="python", encoding="utf-8-sig")


def partes_de(tramo):
    """'SANTIAGO / COPIAPÓ/ANTOFAGASTA ' -> ['SANTIAGO', 'COPIAPÓ', 'ANTOFAGASTA'] (texto original, sin espacios sobrantes)."""
    if tramo is None or (isinstance(tramo, float) and pd.isna(tramo)):
        return []
    return [re.sub(r"\s+", " ", p).strip() for p in str(tramo).split(SEPARADOR)]


def principal(argv=None):
    ap = argparse.ArgumentParser(description="Lista única de ciudades de la tabla de tramos.")
    ap.add_argument("--archivo", default=ARCHIVO)
    ap.add_argument("--columna", default=COLUMNA, help="columna con el texto del tramo")
    ap.add_argument("--solo-activos", action="store_true", help="usar solo filas con isactive = 'Y'")
    ap.add_argument("--salida", default=SALIDA)
    args = ap.parse_args(argv)

    df = leer(args.archivo)
    print(f"Filas leídas: {len(df)} | columnas: {list(df.columns)}")
    if args.columna not in df.columns:
        sys.exit(f"No existe la columna '{args.columna}'. Usa --columna con una de: {list(df.columns)}")
    if args.solo_activos and "isactive" in df.columns:
        df = df[df["isactive"].astype(str).str.upper().isin(["Y", "TRUE", "1", "S"])]
        print(f"Filas activas: {len(df)}")
    id_col = next((c for c in ("sit_stretch_id", "id") if c in df.columns), None)
    km_col = next((c for c in ("kilometers", "km") if c in df.columns), None)
    activo_col = "isactive" if "isactive" in df.columns else None

    # 1) tramos desglosados
    tramos, pares, revisar = [], [], []
    for _, r in df.iterrows():
        texto = r[args.columna]
        partes = partes_de(texto)
        base = {"sit_stretch_id": str(r[id_col]) if id_col else None, "tramo": texto,
                "kilometers": r[km_col] if km_col else None, "isactive": r[activo_col] if activo_col else None}
        if not partes:
            revisar.append(dict(base, motivo="tramo vacío"))
            continue
        vacias = [i for i, p in enumerate(partes) if not p]
        if vacias:
            revisar.append(dict(base, motivo="tiene partes vacías (dos '/' seguidos o '/' al final)"))
        partes = [p for p in partes if p]
        if len(partes) == 1:
            revisar.append(dict(base, motivo="no tiene '/': una sola ciudad"))
        for p in partes:
            if re.search(r"\d", p):
                revisar.append(dict(base, motivo=f"parte con números: '{p}'"))
            if re.search(r"\s-\s|\s-|-\s", p):
                revisar.append(dict(base, motivo=f"parte con guion, podría ser dos ciudades: '{p}'"))
        tramos.append(dict(base, origen=partes[0], destino_final=partes[-1],
                           intermedios=" / ".join(partes[1:-1]), n_puntos=len(partes)))
        for orden, (a, b) in enumerate(zip(partes, partes[1:]), start=1):
            pares.append({"sit_stretch_id": base["sit_stretch_id"], "tramo": texto, "orden": orden,
                          "desde": a, "hasta": b, "desde_clave": clave(a), "hasta_clave": clave(b),
                          "kilometers_tramo_completo": base["kilometers"]})
    tramos = pd.DataFrame(tramos)
    pares = pd.DataFrame(pares)

    # 2) ciudades únicas (se agrupan por la forma normalizada; se guardan las variantes de escritura)
    registros = []
    for _, t in tramos.iterrows():
        partes = [p for p in partes_de(t["tramo"]) if p]
        for i, p in enumerate(partes):
            papel = "origen" if i == 0 else ("destino_final" if i == len(partes) - 1 else "intermedio")
            registros.append({"clave": clave(p), "texto": p, "papel": papel, "tramo": t["tramo"],
                              "activo": t["isactive"]})
    reg = pd.DataFrame(registros)
    ciudades = []
    for k, g in reg.groupby("clave"):
        variantes = Counter(g["texto"])
        ciudades.append({
            "ciudad": variantes.most_common(1)[0][0],
            "clave": k,
            "n_tramos": g["tramo"].nunique(),
            "como_origen": int((g["papel"] == "origen").sum()),
            "como_intermedio": int((g["papel"] == "intermedio").sum()),
            "como_destino_final": int((g["papel"] == "destino_final").sum()),
            "variantes_escritura": " | ".join(f"{v} ({n})" for v, n in variantes.most_common()),
            "ejemplo_tramo": g["tramo"].iloc[0],
            # columnas para completar en el paso siguiente (ubicación en la red vial)
            "ruta": "", "km": "", "lat": "", "lon": "", "observacion": "",
        })
    ciudades = pd.DataFrame(ciudades).sort_values("n_tramos", ascending=False)

    # 3) posibles duplicados: nombres casi iguales ("LA SERENA" / "LASERENA", "COPIAPO" / "COPIAP")
    claves = list(ciudades["clave"])
    for i, a in enumerate(claves):
        for b in claves[i + 1:]:
            sa, sb = a.replace(" ", ""), b.replace(" ", "")
            sim = SequenceMatcher(None, sa, sb).ratio()
            if sa == sb or sim >= 0.85 or (min(len(sa), len(sb)) >= 4 and (sa in sb or sb in sa)):
                revisar.append({"motivo": f"posible misma ciudad: '{a}' y '{b}' (similitud {sim:.2f})"})

    carpeta = Path(args.salida)
    carpeta.mkdir(exist_ok=True)
    ciudades.to_csv(carpeta / "ciudades_unicas.csv", index=False, encoding="utf-8-sig")
    tramos.to_csv(carpeta / "tramos_desglosados.csv", index=False, encoding="utf-8-sig")
    pares.to_csv(carpeta / "tramos_por_tramo.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(revisar).to_csv(carpeta / "revisar.csv", index=False, encoding="utf-8-sig")

    print(f"Tramos: {len(tramos)} | ciudades distintas: {len(ciudades)} | pares de ciudades consecutivas: "
          f"{pares[['desde_clave', 'hasta_clave']].drop_duplicates().shape[0] if len(pares) else 0}")
    print(f"Puntos por tramo: {tramos['n_puntos'].value_counts().sort_index().to_dict()}")
    print("Ciudades más usadas:")
    print(ciudades[["ciudad", "n_tramos", "como_origen", "como_intermedio", "como_destino_final"]].head(15).to_string(index=False))
    print(f"Para revisar: {len(revisar)} filas | archivos en {carpeta.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
