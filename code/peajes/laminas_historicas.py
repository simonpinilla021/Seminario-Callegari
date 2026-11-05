#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Láminas de tarifas de años anteriores (p. ej. 2025): descarga, análisis y carga, en dos pasos.

Paso 1 - examinar (no toca las tablas de peajes_datos/):
  python laminas_historicas.py examinar --anio 2025
    - Toma los PDF de ese año de historico_peajes/enlaces_laminas.csv (salida de buscar_laminas_historicas.py).
    - Los descarga del MOP (si el MOP ya no lo tiene, de la copia del Internet Archive) y los guarda igual que los del
      año actual: peajes_datos/pdf/<año>/<DOCUMENTO>_<huella>.pdf
    - Extrae las tarifas con el mismo lector de peajes_descarga.py.
    - Arma las VERSIONES de cada documento: el MOP volvió a subir varias láminas durante el año. Si la versión nueva
      trae las mismas tarifas es una re-publicación (no se carga); si cambian, es una tarifa nueva con su propia vigencia.
    - Compara el formato con la lámina del año siguiente y las tarifas con las de ese año.
    Todo queda en historico_peajes/<año>/ para revisarlo:
      versiones.csv          una fila por PDF: versión, vigencia asignada y por qué, si se carga
      cambios_tarifa.csv     tarifas de camión que cambiaron entre versiones del mismo año
      formato.csv            por documento: lo leído este año frente a la lámina del año siguiente (¿mismo formato?)
      comparacion_anios.csv  por plaza y condición: tarifa de este año, del siguiente y variación
      documentos.csv         registro de descargas (mismas columnas que peajes_datos/documentos.csv)
      tarifas_detalle.csv    lo que se va a cargar (mismas columnas que peajes_datos/tarifas_detalle.csv)
      integracion/           tabla_integrada.csv, revision.csv... de prueba, con este año + el actual
      equivalencias_documentos.csv (en historico_peajes/) nombre del PDF antiguo -> documento actual. Se puede editar.

Paso 2 - cargar (después de revisar; se pueden borrar o corregir filas de tarifas_detalle.csv antes):
  python laminas_historicas.py cargar --anio 2025
    - Agrega documentos.csv y tarifas_detalle.csv del año a peajes_datos/ (si se repite, reemplaza lo del mismo PDF).
    - Rehace la tabla integrada (peajes_descarga.py --solo-integrar).
    Después: python construir_dw_peajes.py
"""
import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import peajes_descarga as pdes

VERSION = "2026-10-18c"
CARPETA_PEAJES = Path("peajes_datos")
CARPETA_HIST = Path("historico_peajes")

# nombre del PDF antiguo (sin año ni ".pdf") -> nombre del documento del año actual (el que usan los archivos manuales)
EQUIVALENCIAS_INICIALES = [
    ("ACCEO-VIAL", "ACCESO VIAL AEROPUERTO DE SANTIAGO"), ("ACCESO-NORORIENTE", "ACCESO NORORIENTE"),
    ("ANTOFAGASTA", "AUTOPISTA DE LA REGIÓN DE ANTOFAGASTA"), ("AUTOPISTA-CENTRAL", "AUTOPISTA CENTRAL"),
    ("AVOI", "AVO I – TRAMO EL SALTO – PRÍNCIPE DE GALES"), ("CHILLAN-COLLIPULLI", "CHILLÁN – COLLIPULLI"),
    ("COLLIPULI-TEMUCO", "COLLIPULLI – TEMUCO"), ("CONCE-CABRERO", "AUTOPISTA CONCEPCIÓN – CABRERO"),
    ("COSTANERA-NORTE", "COSTANERA NORTE"), ("INTERPORTUARIA", "RUTA INTERPORTUARIA"), ("IQUIQUE", "ACCESO IQUIQUE"),
    ("ITATA", "RUTA DEL ITATA"), ("LA-SERENA-VALLENAR", "LA SERENA – VALLENAR"), ("LOS-LIBERTADORES", "LOS LIBERTADORES"),
    ("LOS-VILOS-LA-SERENA", "LOS VILOS – LA SERENA"), ("NAHUELBUTA", "RUTA NAHUELBUTA"),
    ("NOGALES-PUCHUNCAVI", "CAMINO NOGALES – PUCHUNCAVÍ"), ("PTO-MONTT-PARGUA", "PTO MONTT – PARGUA"),
    ("RED-VIAL", "RED VIAL LITORAL CENTRAL"), ("RIO-BUENO-PTO-MONTT", "RÍO BUENO – PTO MONTT"), ("RUTA-160", "RUTA 160"),
    ("RUTA-43", "RUTA 43"), ("RUTA-60CH", "RUTA 60 CH"), ("RUTA-66", "RUTA 66 CAMINO DE LA FRUTA"), ("RUTA-68", "RUTA 68"),
    ("RUTA-78", "RUTA 78"), ("RUTAS-DEL-LOA", "RUTAS DEL LOA"), ("STGO-LOS-VILOS", "SANTIAGO – LOS VILOS"),
    ("STGO-LAMPA", "TRAMO URBANO SANTIAGO – LAMPA"), ("STGO-TALCA", "SANTIAGO – TALCA"), ("TALCA-CHILLAN", "TALCA – CHILLÁN"),
    ("TEMUCO-RIO-BUENO", "TEMUCO – RÍO BUENO"), ("TUNEL-EL-MELON", "TÚNEL EL MELÓN"), ("TUNEL-SC", "TÚNEL SAN CRISTOBAL"),
    ("VALLENAR-CALDERA", "VALLENAR – CALDERA"), ("VARIANTE-MELIPILLA", "VARIANTE MELIPILLA"),
    ("VESPUCIO-NORTE", "VESPUCIO NORTE"), ("VESPUCIO-SUR", "AUTOPISTA VESPUCIO SUR"),
]
# documentos que en 2026 no existen como lámina propia: a qué concesión de la capa corresponden y en qué sección van
CONCESIONES_EXTRA = [("TUNEL EL MELON", "TUNEL EL MELON"), ("TRAMO URBANO SANTIAGO - LAMPA", "SANTIAGO - LOS VILOS")]
SECCION_EXTRA = {"TÚNEL EL MELÓN": "Tarifas Ruta 5 Norte", "TRAMO URBANO SANTIAGO – LAMPA": "Tarifas Ruta 5 Norte"}
CATEGORIAS_CAMION = ("camion_mas_2_ejes", "camion_con_remolque")


def stem_sin_anio(nombre):
    s = re.sub(r"\.pdf$", "", str(nombre), flags=re.I)
    return re.sub(r"-?(19|20)\d{2}$", "", s).strip("-")


def subida(url):
    m = re.search(r"/uploads/sites/\d+/(20\d{2})/(\d{2})/", str(url))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def fecha_txt(d):
    return d.isoformat() if isinstance(d, date) else (str(d) if d else "")


def leer_equivalencias():
    ruta = CARPETA_HIST / "equivalencias_documentos.csv"
    eq = pdes.archivo_manual(ruta, EQUIVALENCIAS_INICIALES, ["archivo_sin_anio", "documento"], completar=True)
    return {pdes.texto_clave(a): d for a, d in eq[["archivo_sin_anio", "documento"]].values}


def mapa_concesiones():
    mapa = pdes.archivo_manual(CARPETA_PEAJES / "concesiones_documentos.csv", pdes.CONCESIONES_INICIALES,
                               ["documento_contiene", "concesion_mop_contiene"])
    mapa = [tuple(x) for x in mapa[["documento_contiene", "concesion_mop_contiene"]].values]
    ya = {(pdes.texto_clave(a), pdes.texto_clave(b)) for a, b in mapa}
    return mapa + [x for x in CONCESIONES_EXTRA if (pdes.texto_clave(x[0]), pdes.texto_clave(x[1])) not in ya]


def archivos_manuales():
    alias = pdes.archivo_manual(CARPETA_PEAJES / "alias_plazas.csv", pdes.ALIAS_INICIALES,
                                ["documento_contiene", "encabezado_contiene", "plaza_mop"], completar=True)
    alias = [tuple(x) for x in alias[["documento_contiene", "encabezado_contiene", "plaza_mop"]].values]
    manuales = pdes.archivo_manual(CARPETA_PEAJES / "plazas_manual.csv", pdes.PLAZAS_MANUALES_INICIALES,
                                   ["nombre", "concesion", "ruta", "km", "posicion", "observacion"])
    plantillas = pdes.archivo_manual(CARPETA_PEAJES / "encabezados_manual.csv", pdes.PLANTILLAS_INICIALES,
                                     ["documento_contiene", "tabla", "eje", "numero", "texto"]).to_dict("records")
    return alias, manuales, plantillas


# ------------------------------------------------------------------------------------------------ paso 1: examinar
def lista_pdf(anio, equiv, docs_actuales):
    """PDF del año: los de las copias del Internet Archive + (si es el año en curso) los que hoy publica la página."""
    enl = pd.read_csv(CARPETA_HIST / "enlaces_laminas.csv", dtype=str, encoding="utf-8-sig").fillna("")
    enl = enl[enl["anio_lamina"] == str(anio)].drop_duplicates("url_pdf")
    act = docs_actuales.copy() if len(docs_actuales) else pd.DataFrame(columns=["documento", "seccion", "url", "estado", "anio"])
    act = act[(act["anio"].astype(str) == str(anio)) & ~act["estado"].astype(str).str.startswith("histórico")
              & (act["sha256"].astype(str) != "")].drop_duplicates("url", keep="last")
    extra = act[~act["url"].isin(set(enl["url_pdf"]))]
    if len(extra):                                                    # publicados después de la última copia del archivo
        enl = pd.concat([enl, pd.DataFrame({"anio_lamina": str(anio), "texto_enlace": extra["documento"],
                                            "nombre_archivo": extra["url"].str.rsplit("/", n=1).str[-1], "url_pdf": extra["url"],
                                            "primera_copia": "", "ultima_copia": "página actual"})], ignore_index=True)
    if not len(enl):
        sys.exit(f"No hay PDF del año {anio} en {CARPETA_HIST / 'enlaces_laminas.csv'} (ejecutar buscar_laminas_historicas.py)")
    seccion_de = {pdes.texto_clave(d): s for d, s in docs_actuales[["documento", "seccion"]].values} if len(docs_actuales) else {}
    por_url = {r["url"]: r for _, r in act.iterrows()}
    # nombre del archivo actual (sin año ni sufijos) -> documento, para las versiones anteriores del mismo PDF
    por_stem = {pdes.texto_clave(stem_sin_anio(u.rsplit("/", 1)[-1])): d for u, d in zip(act["url"], act["documento"])}
    filas = []
    for _, r in enl.iterrows():
        a, m = subida(r["url_pdf"])
        stem = pdes.texto_clave(stem_sin_anio(r["nombre_archivo"]))
        actual = por_url.get(r["url_pdf"])
        documento = actual["documento"] if actual is not None else (equiv.get(stem) or por_stem.get(stem))
        filas.append({"seccion": actual["seccion"] if actual is not None else
                      seccion_de.get(pdes.texto_clave(documento or ""), SECCION_EXTRA.get(documento or "", "")),
                      "documento": documento or r["texto_enlace"], "mes_publicacion": m or "", "anio": anio,
                      "url": r["url_pdf"], "archivo_origen": r["nombre_archivo"], "subida": f"{a}-{m:02d}" if a else "",
                      "primera_copia": r["primera_copia"], "ultima_copia": r["ultima_copia"],
                      "equivalencia": "ok" if documento else "SIN EQUIVALENCIA: agregar a equivalencias_documentos.csv"})
    return pd.DataFrame(filas)


def descargar(lista, anio, previo):
    """Descarga cada PDF (MOP o Internet Archive) a peajes_datos/pdf/<año>/. Reusa lo ya bajado en una ejecución anterior."""
    sesion = pdes.nueva_sesion()
    ya = {}
    if len(previo):
        for _, p in previo.iterrows():
            if p.get("archivo") and Path(p["archivo"]).exists():
                ya[p["url"]] = p
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    salida = []
    for i, d in enumerate(lista.to_dict("records"), start=1):
        if d["url"] in ya:
            p = ya[d["url"]]
            d.update(fecha_descarga=p["fecha_descarga"], archivo=p["archivo"], sha256=p["sha256"], bytes=p["bytes"],
                     fuente_descarga=p.get("fuente_descarga", ""), estado="histórico: ya descargado")
            salida.append(d)
            continue
        contenido, fuente, error = None, "", ""
        for fuente, url in (("MOP", d["url"]), ("Internet Archive", f"https://web.archive.org/web/{d['primera_copia'] or anio}id_/{d['url']}")):
            try:
                r = pdes.pedir(sesion, url, intentos=2)
                if r.content.startswith(b"%PDF"):
                    contenido = r.content
                    break
                error = "la respuesta no es un PDF"
            except Exception as e:                               # noqa: BLE001
                error = str(e)
        if contenido is None:
            d.update(fecha_descarga=fecha, archivo="", sha256="", bytes="", fuente_descarga="", estado=f"error: {error}")
        else:
            huella = pdes.sha256(contenido)
            nombre = re.sub(r"[^A-Z0-9]+", "-", pdes.normalizar(d["documento"])).strip("-")
            destino = CARPETA_PEAJES / "pdf" / str(anio) / f"{nombre}_{huella[:8]}.pdf"
            destino.parent.mkdir(parents=True, exist_ok=True)
            if not destino.exists():
                destino.write_bytes(contenido)
            d.update(fecha_descarga=fecha, archivo=str(destino), sha256=huella, bytes=len(contenido), fuente_descarga=fuente,
                     estado="histórico: descargado")
        salida.append(d)
        print(f"   {i}/{len(lista)} {d['archivo_origen']} ({d['subida']}): {d['estado']}")
    return pd.DataFrame(salida)


def firma(filas):
    """Conjunto de tarifas de un PDF (todas las categorías) para saber si dos versiones traen lo mismo."""
    return frozenset((pdes.texto_clave(f["plaza_texto"]), pdes.texto_clave(f["categoria_texto"]), f["horario"],
                      round(float(f["tarifa_clp"]), 2)) for f in filas if f.get("tarifa_clp") not in (None, ""))


def versionar(docs, extraido, anio):
    """Ordena las versiones de cada documento por mes de subida y asigna la vigencia de cada una."""
    inicio_anio, fin_anio = date(anio, 1, 1), date(anio, 12, 31)
    versiones, cambios = [], []
    for documento, g in docs[docs["sha256"] != ""].sort_values(["subida", "url"]).groupby("documento", sort=False):
        previa = None                                                 # última versión que se carga
        for _, d in g.iterrows():
            filas, texto = extraido.get(d["sha256"], ([], ""))
            desde_t, hasta_t, fuente_t = pdes.extraer_vigencia(texto, anio, None)
            fuente_t = fuente_t if fuente_t.startswith("texto") else ""
            n_cam = sum(1 for f in filas if pdes.categoria_normalizada(f["categoria_texto"]) in CATEGORIAS_CAMION)
            v = {"documento": documento, "archivo_origen": d["archivo_origen"], "subida": d["subida"], "url": d["url"],
                 "sha256": d["sha256"][:8], "primera_copia": d["primera_copia"], "ultima_copia": d["ultima_copia"],
                 "tarifas_leidas": len(filas), "tarifas_camion": n_cam, "vigencia_texto": f"{fecha_txt(desde_t)} a {fecha_txt(hasta_t)}"
                 if fuente_t else "el PDF no dice fechas", "_firma": firma(filas), "_filas": filas}
            if not filas:
                v.update(estado_version="sin tarifas leídas: revisar el PDF a mano", cargar="no")
            elif previa is None:
                desde = desde_t if fuente_t and desde_t and desde_t.year == anio else inicio_anio
                v.update(estado_version="primera versión del año", cargar="si", vigencia_desde=desde,
                         fuente_vigencia="texto del documento" if fuente_t else f"inferida: lámina del año {anio} desde el 1 de enero")
            elif v["_firma"] == previa["_firma"] or d["sha256"][:8] == previa["sha256"]:
                v.update(estado_version=f"re-publicación: mismas tarifas que la versión {previa['subida']}", cargar="no")
            else:
                a, m = map(int, d["subida"].split("-"))
                desde_sub = date(a, m, 1)
                if fuente_t and desde_t and desde_t > previa["vigencia_desde"]:
                    desde, fuente = desde_t, "texto del documento"
                else:
                    desde, fuente = desde_sub, (f"inferida: mes en que se subió el PDF ({d['subida']}); la página la muestra "
                                                f"entre las copias {d['primera_copia']} y {d['ultima_copia']}")
                previa["vigencia_hasta"] = desde - timedelta(days=1)
                v.update(estado_version=f"tarifas distintas a la versión {previa['subida']}: vigencia nueva", cargar="si",
                         vigencia_desde=desde, fuente_vigencia=fuente)
                antes = {(pdes.texto_clave(f["plaza_texto"]), pdes.texto_clave(f["categoria_texto"]), f["horario"]):
                         float(f["tarifa_clp"]) for f in previa["_filas"]}
                for f in filas:
                    cat = pdes.categoria_normalizada(f["categoria_texto"])
                    k = (pdes.texto_clave(f["plaza_texto"]), pdes.texto_clave(f["categoria_texto"]), f["horario"])
                    if cat in CATEGORIAS_CAMION and k in antes and abs(antes[k] - float(f["tarifa_clp"])) > 0.5:
                        cambios.append({"documento": documento, "plaza_texto": f["plaza_texto"], "categoria": cat,
                                        "categoria_texto": f["categoria_texto"], "horario": f["horario"],
                                        "version_anterior": previa["subida"], "tarifa_anterior": antes[k], "version_nueva": d["subida"],
                                        "tarifa_nueva": float(f["tarifa_clp"]), "desde": fecha_txt(desde),
                                        "variacion_pct": round(100 * (float(f["tarifa_clp"]) / antes[k] - 1), 1) if antes[k] else None})
            if v["cargar"] == "si":
                hasta = hasta_t if fuente_t and hasta_t and hasta_t.year == anio and hasta_t > v["vigencia_desde"] else fin_anio
                v["vigencia_hasta"] = hasta
                previa = v
            versiones.append(v)
    return versiones, cambios


def comparar_formato(versiones, detalle_actual, anio):
    """Por documento: la última versión cargada de este año frente a la lámina vigente del año siguiente."""
    act = detalle_actual.copy()
    otro = anio + 1
    if len(act):
        if not (act["vigencia_desde"].astype(str).str[:4] == str(anio + 1)).any():
            otro = anio - 1                                           # año en curso: se compara con el anterior
        act = act[act["vigencia_desde"].astype(str).str[:4] == str(otro)]
    filas = []
    for doc, vs in pd.DataFrame(versiones).groupby("documento"):
        vs = vs[vs["cargar"] == "si"]
        ult = vs.iloc[-1] if len(vs) else None
        f25 = ult["_filas"] if ult is not None else []
        a = act[act["documento"].map(pdes.texto_clave) == pdes.texto_clave(doc)] if len(act) else act
        cam25 = [f for f in f25 if pdes.categoria_normalizada(f["categoria_texto"]) in CATEGORIAS_CAMION]
        cam26 = a[a["categoria"].isin(CATEGORIAS_CAMION)] if len(a) else a
        p25 = {pdes.texto_clave(f["plaza_texto"]) for f in cam25}
        p26 = set(cam26["plaza_texto"].map(pdes.texto_clave)) if len(cam26) else set()
        h25 = {f["horario"] for f in cam25}
        h26 = set(cam26["horario"]) if len(cam26) else set()
        obs = []
        if not len(a):
            obs.append(f"sin lámina {otro} para comparar")
        if not cam25:
            obs.append("no se leyó tarifa de camión")
        if p25 != p26 and len(a):
            obs.append("cambian los encabezados de plaza")
        if h25 != h26 and len(a):
            obs.append("cambian los horarios")
        if len(a) and cam25 and abs(len(cam25) - len(cam26)) > max(2, 0.2 * len(cam26)):
            obs.append("cantidad de tarifas de camión muy distinta")
        filas.append({"documento": doc, f"tarifas_{anio}": len(f25), f"tarifas_{otro}": len(a),
                      f"camion_{anio}": len(cam25), f"camion_{otro}": len(cam26),
                      f"horarios_{anio}": " | ".join(sorted(h25)), f"horarios_{otro}": " | ".join(sorted(h26)),
                      f"plazas_solo_{anio}": " | ".join(sorted(p25 - p26))[:300], f"plazas_solo_{otro}": " | ".join(sorted(p26 - p25))[:300],
                      "veredicto": "mismo formato" if not obs else "revisar: " + "; ".join(obs)})
    return pd.DataFrame(filas)


def comparar_anios(integ, anio):
    """Tarifa de camión por plaza y condición en cada vigencia del año y en el año siguiente."""
    t = integ[integ["tarifa_clp"].astype(str) != ""].copy()
    t = t[t["categoria"].isin(CATEGORIAS_CAMION)]
    t["tarifa_clp"] = pd.to_numeric(t["tarifa_clp"], errors="coerce")
    t["plaza"] = t["plaza_mop"].where(t["plaza_mop"].astype(str) != "", t["plaza_pdf"])
    llave = ["concesion", "plaza", "posicion", "categoria", "horario", "modalidad"]
    t["periodo"] = t["vigencia_desde"].astype(str)
    piv = t.pivot_table(index=llave, columns="periodo", values="tarifa_clp", aggfunc="max").reset_index()
    cols = sorted(c for c in piv.columns if c not in llave)
    este = [c for c in cols if c.startswith(str(anio))]
    sig = [c for c in cols if c.startswith(str(anio + 1))]
    if este and not sig:                                              # año en curso: frente al año anterior
        ant = [c for c in cols if c.startswith(str(anio - 1))]
        if ant:
            previa = piv[ant].ffill(axis=1).iloc[:, -1]
            piv[f"ultima_{anio - 1}"] = previa
            piv["variacion_pct_primera_vs_anterior"] = (100 * (piv[este].bfill(axis=1).iloc[:, 0] / previa - 1)).round(1)
            piv["alerta"] = ""
            if len(este) > 1:
                piv.loc[piv[este].nunique(axis=1) > 1, "alerta"] = f"cambió durante {anio}"
            piv.loc[piv[este].isna().all(axis=1), "alerta"] = f"sin tarifa {anio}"
            piv.loc[piv["variacion_pct_primera_vs_anterior"].abs() > 15, "alerta"] = "variación mayor a 15%"
        return piv
    if este and sig:
        ultima = piv[este].ffill(axis=1).iloc[:, -1]                  # la última tarifa del año (de la versión más reciente)
        piv[f"ultima_{anio}"] = ultima
        piv["variacion_pct_ultima_a_siguiente"] = (100 * (piv[sig[0]] / ultima - 1)).round(1)
        piv["alerta"] = ""
        if len(este) > 1:
            cambio = piv[este].nunique(axis=1) > 1
            piv.loc[cambio, "alerta"] = f"cambió durante {anio}"
        piv.loc[ultima.isna(), "alerta"] = f"sin tarifa {anio}"
        piv.loc[piv[sig[0]].isna(), "alerta"] = f"sin tarifa {anio + 1}"
        piv.loc[piv["variacion_pct_ultima_a_siguiente"].abs() > 15, "alerta"] = "variación mayor a 15%"
        piv.loc[piv["variacion_pct_ultima_a_siguiente"] < -0.5, "alerta"] = f"baja de {anio} a {anio + 1}: revisar"
    return piv


def examinar(anio):
    salida = CARPETA_HIST / str(anio)
    (salida / "integracion").mkdir(parents=True, exist_ok=True)
    equiv = leer_equivalencias()
    docs_actuales = pdes.leer_csv(CARPETA_PEAJES / "documentos.csv")
    lista = lista_pdf(anio, equiv, docs_actuales)
    sin_eq = lista[lista["equivalencia"] != "ok"]
    print(f"1) PDF del año {anio}: {len(lista)} ({lista['documento'].nunique()} documentos)"
          + (f" | sin equivalencia: {', '.join(sin_eq['archivo_origen'])}" if len(sin_eq) else ""))
    previo = pd.concat([docs_actuales, pdes.leer_csv(salida / "documentos.csv")], ignore_index=True).fillna("")
    docs = descargar(lista, anio, previo)
    print(f"   descargados: {(docs['sha256'] != '').sum()} | PDF distintos (huella): {docs.loc[docs['sha256'] != '', 'sha256'].nunique()}"
          f" | errores: {(docs['sha256'] == '').sum()}")

    print("2) Leyendo las tarifas de cada PDF...")
    alias, manuales, plantillas = archivos_manuales()
    extraido, problemas = {}, []
    for _, d in docs[docs["sha256"] != ""].drop_duplicates("sha256").iterrows():
        try:
            extraido[d["sha256"]] = pdes.extraer_tarifas_pdf(d["archivo"], d["documento"], d["seccion"], plantillas)
        except Exception as e:                                   # noqa: BLE001
            extraido[d["sha256"]] = ([], "")
            problemas.append({"documento": d["documento"], "url": d["url"], "motivo": f"no se pudo leer el PDF: {e}"})
        if not extraido[d["sha256"]][1].strip():
            problemas.append({"documento": d["documento"], "url": d["url"], "motivo": "el PDF no tiene texto (imagen): cargar a mano"})

    print("3) Versiones y vigencias...")
    versiones, cambios = versionar(docs, extraido, anio)
    vdf = pd.DataFrame(versiones)
    cols_v = ["documento", "archivo_origen", "subida", "sha256", "primera_copia", "ultima_copia", "tarifas_leidas", "tarifas_camion",
              "vigencia_texto", "estado_version", "cargar", "vigencia_desde", "vigencia_hasta", "fuente_vigencia", "url"]
    vcsv = vdf.reindex(columns=cols_v)
    for c in ("vigencia_desde", "vigencia_hasta"):
        vcsv[c] = vcsv[c].map(lambda x: fecha_txt(x) if isinstance(x, date) else "")
    pdes.guardar_csv(vcsv, salida / "versiones.csv")
    pdes.guardar_csv(pd.DataFrame(cambios, columns=["documento", "plaza_texto", "categoria", "categoria_texto", "horario", "version_anterior",
                                                    "tarifa_anterior", "version_nueva", "tarifa_nueva", "desde", "variacion_pct"]),
                     salida / "cambios_tarifa.csv")

    # detalle a cargar: mismas columnas que peajes_datos/tarifas_detalle.csv, con la vigencia resuelta
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    por_url = docs.set_index("url")
    nuevas = []
    for v in versiones:
        if v["cargar"] != "si":
            continue
        d = por_url.loc[v["url"]]
        for f in v["_filas"]:
            nuevas.append(dict(f, documento=v["documento"], seccion=d["seccion"], anio=anio,
                               vigencia_desde=fecha_txt(v["vigencia_desde"]), vigencia_hasta=fecha_txt(v["vigencia_hasta"]),
                               fuente_vigencia=v["fuente_vigencia"], url=v["url"], archivo=d["archivo"], sha256=d["sha256"],
                               fecha_descarga=d["fecha_descarga"], fecha_extraccion=fecha,
                               categoria=pdes.categoria_normalizada(f["categoria_texto"])))
    detalle_actual = pdes.leer_csv(CARPETA_PEAJES / "tarifas_detalle.csv")
    nuevas = pd.DataFrame(nuevas).astype(str)
    columnas = list(detalle_actual.columns) if len(detalle_actual) else list(nuevas.columns)
    nuevas = nuevas.reindex(columns=columnas + [c for c in nuevas.columns if c not in columnas])
    pdes.guardar_csv(nuevas, salida / "tarifas_detalle.csv")
    reg = docs.copy()
    reg["estado"] = reg["url"].map({v["url"]: ("histórico: cargar" if v["cargar"] == "si" else "histórico: " + v["estado_version"])
                                    for v in versiones}).fillna(reg["estado"])
    pdes.guardar_csv(reg, salida / "documentos.csv")

    print("4) Formato frente a la lámina del año siguiente y unión de prueba con las plazas...")
    formato = comparar_formato(versiones, detalle_actual, anio)
    pdes.guardar_csv(formato, salida / "formato.csv")
    plazas = pdes.leer_csv(CARPETA_PEAJES / "plazas_mop.csv")
    detalle = pd.concat([detalle_actual, nuevas], ignore_index=True).fillna("")
    _, integ = pdes.integrar(salida / "integracion", detalle, plazas, mapa_concesiones(), alias, manuales, problemas)
    integ = integ.fillna("")
    comp = comparar_anios(integ, anio)
    pdes.guardar_csv(comp, salida / "comparacion_anios.csv")

    # resumen
    print("\nResumen")
    print(vdf["estado_version"].str.split(":").str[0].value_counts().to_string())
    print(f"Documentos con tarifa nueva a mitad de año: {sorted({c['documento'] for c in cambios}) or 'ninguno'}")
    print(f"Formato: {(formato['veredicto'] == 'mismo formato').sum()} documentos iguales al del año de comparación; "
          f"{(formato['veredicto'] != 'mismo formato').sum()} para revisar (formato.csv)")
    if "alerta" in comp:
        print("Comparación por plaza (comparacion_anios.csv): " + ", ".join(f"{k or 'sin alerta'}: {n}" for k, n in
                                                                           comp["alerta"].value_counts().items()))
    print(f"Filas listas para cargar: {len(nuevas)} (tarifas_detalle.csv)")
    print(f"Archivos en {salida.resolve()}  |  PDF en {(CARPETA_PEAJES / 'pdf' / str(anio)).resolve()}")
    print(f"Cuando esté revisado: python laminas_historicas.py cargar --anio {anio}")


# ------------------------------------------------------------------------------------------------ paso 2: cargar
def cargar(anio):
    salida = CARPETA_HIST / str(anio)
    nuevas = pdes.leer_csv(salida / "tarifas_detalle.csv")
    docs = pdes.leer_csv(salida / "documentos.csv")
    if not len(nuevas):
        sys.exit(f"No hay {salida / 'tarifas_detalle.csv'}: ejecutar antes 'examinar --anio {anio}'")
    clave = lambda df: df["url"].astype(str) + "|" + df["sha256"].astype(str)
    det = pdes.leer_csv(CARPETA_PEAJES / "tarifas_detalle.csv")
    reg = pdes.leer_csv(CARPETA_PEAJES / "documentos.csv")
    # se reemplaza lo de los mismos PDF (así se puede volver a cargar después de corregir)
    det = det[~clave(det).isin(set(clave(nuevas)))] if len(det) else det
    if len(reg):
        vigentes = set(reg.loc[~reg["estado"].astype(str).str.startswith("histórico"), "url"])
        docs = docs[~docs["url"].isin(vigentes)]                     # los de la página actual quedan como estaban
        reg = reg[~reg["url"].isin(set(docs["url"]))]
    cols_reg = list(reg.columns) if len(reg) else list(docs.columns)
    pdes.guardar_csv(pd.concat([det, nuevas], ignore_index=True).fillna(""), CARPETA_PEAJES / "tarifas_detalle.csv")
    pdes.guardar_csv(pd.concat([reg, docs.reindex(columns=cols_reg)], ignore_index=True).fillna(""), CARPETA_PEAJES / "documentos.csv")
    # concesiones de los documentos que no existen en el año actual
    ruta = CARPETA_PEAJES / "concesiones_documentos.csv"
    mapa = pdes.leer_csv(ruta)
    ya = {(pdes.texto_clave(a), pdes.texto_clave(b)) for a, b in mapa[["documento_contiene", "concesion_mop_contiene"]].values}
    extra = [x for x in CONCESIONES_EXTRA if (pdes.texto_clave(x[0]), pdes.texto_clave(x[1])) not in ya]
    if extra:
        pdes.guardar_csv(pd.concat([mapa, pd.DataFrame(extra, columns=mapa.columns)], ignore_index=True), ruta)
    print(f"Cargadas {len(nuevas)} tarifas de {nuevas['url'].nunique()} PDF del año {anio} en {CARPETA_PEAJES.resolve()}")
    print("Rehaciendo la tabla integrada...")
    codigo = pdes.principal(["--carpeta", str(CARPETA_PEAJES), "--solo-integrar"])
    print("Siguiente paso: python construir_dw_peajes.py")
    return codigo


def principal(argv=None):
    print(f"laminas_historicas.py versión {VERSION}")
    ap = argparse.ArgumentParser(description="Descarga, examina y carga láminas de tarifas de años anteriores.")
    ap.add_argument("accion", choices=["examinar", "cargar"])
    ap.add_argument("--anio", type=int, required=True)
    a = ap.parse_args(argv)
    return examinar(a.anio) if a.accion == "examinar" else cargar(a.anio)


if __name__ == "__main__":
    sys.exit(principal() or 0)
