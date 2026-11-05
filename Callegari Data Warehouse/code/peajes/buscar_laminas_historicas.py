#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evaluación: ¿existen las láminas de tarifas de peajes de años anteriores (2022–2025)?

La página de Peajes y Pórticos del MOP hoy solo muestra el año actual. Este script busca en el Internet Archive
(web.archive.org, que guarda copias antiguas de páginas web) las copias de esa página de años anteriores, anota los
enlaces a PDF que tenía cada copia y revisa si cada PDF todavía se puede bajar, ya sea del MOP o del mismo archivo.
No descarga los PDF: solo arma la lista para decidir si vale la pena seguir.

Salidas en historico_peajes/:
  copias_pagina.csv      una fila por copia guardada de la página: fecha, año que muestra y cuántos PDF enlaza
  enlaces_laminas.csv    una fila por PDF distinto: año de la lámina, nombre, enlace y dónde está disponible
  pdfs_archivados.csv    PDF del sitio del MOP guardados en el archivo cuyo nombre tiene un año 2021–2025 (referencia)

Uso (en la carpeta EDA):
  python buscar_laminas_historicas.py
Tarda unos 5 a 10 minutos: el Internet Archive pide ir despacio. Solo usa librerías que vienen con Python.
"""
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

PAGINAS = ["concesiones.mop.gob.cl/peajes-y-porticos/"]
DESDE, HASTA = 2021, 2026
CDX = "https://web.archive.org/cdx/search/cdx"
AGENTE = "seminario-ucn-peajes/1.0 (proyecto academico, consultas ocasionales)"
PAUSA_S = 1.5
SALIDA = Path("historico_peajes")
VERSION = "2026-10-12"


# ------------------------------------------------------------------------------------------------ red
_ultimo = [0.0]


def pedir(url, metodo="GET", encabezados=None, intentos=4, timeout=60, leer=True):
    """(código HTTP, contenido) con pausa entre consultas y reintentos; (None, b'') si no hubo respuesta."""
    espera = PAUSA_S - (time.time() - _ultimo[0])
    if espera > 0:
        time.sleep(espera)
    for intento in range(intentos):
        try:
            req = urllib.request.Request(url, method=metodo, headers=dict({"User-Agent": AGENTE}, **(encabezados or {})))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                datos = r.read() if leer else r.read(1)
                _ultimo[0] = time.time()
                return r.status, datos
        except urllib.error.HTTPError as e:
            _ultimo[0] = time.time()
            if e.code in (429, 500, 502, 503, 504) and intento < intentos - 1:
                time.sleep(5 * 2 ** intento)
                continue
            return e.code, b""
        except Exception:                                          # sin conexión, tiempo agotado, etc.
            _ultimo[0] = time.time()
            if intento < intentos - 1:
                time.sleep(5 * 2 ** intento)
                continue
            return None, b""
    return None, b""


def cdx(params):
    """Consulta el índice del Internet Archive; devuelve una lista de diccionarios."""
    codigo, datos = pedir(CDX + "?" + urllib.parse.urlencode(params, doseq=True), timeout=120)
    if codigo != 200 or not datos.strip():
        return [] if codigo == 200 else None
    filas = json.loads(datos.decode("utf-8"))
    if not filas:
        return []
    cab = filas[0]
    return [dict(zip(cab, f)) for f in filas[1:]]


# ------------------------------------------------------------------------------------------------ lectura de la página
class Enlaces(HTMLParser):
    """Junta los enlaces (href + texto) y el texto visible de una página."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.enlaces, self.texto, self._a = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._a = [dict(attrs).get("href") or "", []]

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.enlaces.append((self._a[0], " ".join(" ".join(self._a[1]).split())))
            self._a = None

    def handle_data(self, data):
        self.texto.append(data)
        if self._a is not None:
            self._a[1].append(data)


def sin_archivo(url):
    """Quita el prefijo del Internet Archive si el enlace quedó reescrito: /web/2024.../https://... -> https://..."""
    m = re.search(r"/web/\d+(?:[a-z_]+)?/(https?://.+)$", url)
    return m.group(1) if m else url


def leer_pagina(html, url_original):
    p = Enlaces()
    try:
        p.feed(html)
    except Exception:
        pass
    texto = " ".join(" ".join(p.texto).split())
    anios = re.findall(r"(?i)(?:peajes\s+y\s+p[oó]rticos|tarifas)\D{0,25}(20\d{2})", texto)
    anio = Counter(anios).most_common(1)[0][0] if anios else ""
    pdfs = []
    for href, txt in p.enlaces:
        if not href:
            continue
        absoluto = sin_archivo(urllib.parse.urljoin(url_original, href.strip()))
        if re.search(r"\.pdf(?:$|[?#])", absoluto, re.I):
            pdfs.append((txt, absoluto.split("#")[0]))
    return anio, pdfs


def anio_en_url(url):
    m = re.search(r"/uploads/sites/\d+/(20\d{2})/", url)
    return m.group(1) if m else ""


# ------------------------------------------------------------------------------------------------ disponibilidad
def estado_mop(url):
    """Código HTTP del PDF en el servidor del MOP (200 = se puede bajar)."""
    codigo, _ = pedir(url, metodo="HEAD", intentos=2, timeout=30, leer=False)
    if codigo in (None, 403, 405):
        codigo, _ = pedir(url, encabezados={"Range": "bytes=0-0"}, intentos=2, timeout=30, leer=False)
    return codigo


def copia_en_archivo(url):
    """Fecha de la primera copia del PDF guardada en el Internet Archive ('' si no hay)."""
    filas = cdx({"url": url, "output": "json", "fl": "timestamp,statuscode", "filter": "statuscode:200", "limit": 1})
    return filas[0]["timestamp"] if filas else ""


# ------------------------------------------------------------------------------------------------ programa
def guardar(ruta, filas, columnas):
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)


def principal():
    print(f"buscar_laminas_historicas.py versión {VERSION}")
    SALIDA.mkdir(exist_ok=True)

    # 1. copias guardadas de la página (una por mes)
    copias = []
    for pagina in PAGINAS:
        filas = cdx({"url": pagina, "output": "json", "fl": "timestamp,original,statuscode", "filter": "statuscode:200",
                     "from": str(DESDE), "to": str(HASTA), "collapse": "timestamp:6"})
        if filas is None:
            sys.exit("No hubo respuesta del Internet Archive (web.archive.org). Revisa la conexión y vuelve a intentar.")
        copias += filas
    print(f"1) Copias de la página guardadas en el Internet Archive ({DESDE}-{HASTA}, una por mes): {len(copias)}")
    if not copias:
        print("   No hay copias: por este camino no se pueden obtener las láminas antiguas.")

    # 2. enlaces a PDF de cada copia
    resumen, por_pdf = [], defaultdict(lambda: {"anios": Counter(), "textos": Counter(), "copias": []})
    for i, c in enumerate(copias, start=1):
        url = f"https://web.archive.org/web/{c['timestamp']}id_/{c['original']}"
        codigo, datos = pedir(url, timeout=90)
        if codigo != 200:
            resumen.append({"fecha_copia": c["timestamp"][:8], "anio_que_muestra": "", "pdfs": "", "estado": f"no se pudo leer ({codigo})"})
            continue
        html = datos.decode("utf-8", "ignore")
        anio, pdfs = leer_pagina(html, c["original"])
        resumen.append({"fecha_copia": c["timestamp"][:8], "anio_que_muestra": anio, "pdfs": len(pdfs), "estado": "ok",
                        "copia": url})
        for txt, u in pdfs:
            d = por_pdf[u]
            d["anios"][anio or c["timestamp"][:4]] += 1
            d["textos"][txt] += 1
            d["copias"].append(c["timestamp"][:8])
        print(f"   {i}/{len(copias)}  copia {c['timestamp'][:4]}-{c['timestamp'][4:6]}: muestra el año {anio or '?'}, {len(pdfs)} PDF")
    guardar(SALIDA / "copias_pagina.csv", resumen, ["fecha_copia", "anio_que_muestra", "pdfs", "estado", "copia"])

    # 3. ¿se puede bajar cada PDF?
    print(f"2) PDF distintos enlazados: {len(por_pdf)}. Revisando si siguen disponibles...")
    enlaces = []
    for i, (u, d) in enumerate(sorted(por_pdf.items()), start=1):
        mop = estado_mop(u)
        archivo = "" if mop == 200 else copia_en_archivo(u)
        enlaces.append({"anio_lamina": d["anios"].most_common(1)[0][0], "texto_enlace": d["textos"].most_common(1)[0][0],
                        "nombre_archivo": urllib.parse.unquote(u.rsplit("/", 1)[-1]), "anio_subida": anio_en_url(u),
                        "url_pdf": u, "primera_copia": min(d["copias"]), "ultima_copia": max(d["copias"]),
                        "estado_mop": mop if mop is not None else "sin respuesta",
                        "copia_en_archivo": archivo,
                        "disponible": "MOP" if mop == 200 else ("Internet Archive" if archivo else "no")})
        if i % 20 == 0:
            print(f"   {i}/{len(por_pdf)}")
    guardar(SALIDA / "enlaces_laminas.csv", enlaces, ["anio_lamina", "texto_enlace", "nombre_archivo", "anio_subida", "url_pdf",
                                                      "primera_copia", "ultima_copia", "estado_mop", "copia_en_archivo", "disponible"])

    # 4. referencia: PDF del sitio del MOP guardados en el archivo con un año en el nombre
    filas = cdx({"url": "concesiones.mop.gob.cl/uploads/sites/4/*", "output": "json", "fl": "original,timestamp",
                 "filter": ["statuscode:200", "mimetype:application/pdf"], "collapse": "urlkey", "limit": 20000}) or []
    ref = [{"nombre_archivo": urllib.parse.unquote(f["original"].rsplit("/", 1)[-1]), "url_pdf": f["original"],
            "fecha_copia": f["timestamp"][:8]} for f in filas
           if re.search(r"20(2[1-5])", urllib.parse.unquote(f["original"].rsplit("/", 1)[-1]))]
    guardar(SALIDA / "pdfs_archivados.csv", ref, ["nombre_archivo", "url_pdf", "fecha_copia"])

    # resumen
    print("\nResumen por año de lámina (según el año que mostraba la página):")
    por_anio = defaultdict(Counter)
    for e in enlaces:
        por_anio[e["anio_lamina"]][e["disponible"]] += 1
    for anio in sorted(por_anio):
        c = por_anio[anio]
        print(f"   {anio}: {sum(c.values()):3d} PDF | en el MOP {c['MOP']:3d} | solo en el Internet Archive {c['Internet Archive']:3d} "
              f"| no disponibles {c['no']:3d}")
    print(f"PDF del MOP archivados con un año 2021-2025 en el nombre: {len(ref)}")
    print(f"Archivos en {SALIDA.resolve()}")


if __name__ == "__main__":
    principal()
