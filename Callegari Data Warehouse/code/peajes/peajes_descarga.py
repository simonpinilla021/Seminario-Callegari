#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Descarga automática de tarifas de peaje para camiones (Chile).
Seminario BI — Transportes Callegari.

Fuentes públicas (no requieren credenciales):
  1. Dirección General de Concesiones (MOP), página "Peajes y Pórticos": un PDF de tarifas por concesión.
     https://concesiones.mop.gob.cl/peajes-y-porticos/
  2. Dirección de Vialidad (MOP), capa ArcGIS "Plazas de Peaje": nombre, ruta, km, troncal/lateral y coordenadas.

Qué hace cada ejecución (todo queda en la carpeta peajes_datos/):
  - Descarga la capa de plazas del MOP           -> plazas_mop.csv
  - Lee la página de la DGC y descarga los PDF    -> pdf/<año>/<documento>_<huella>.pdf
    y anota cada descarga (con su huella SHA-256) -> documentos.csv   (histórico; detecta cambios)
  - Extrae las tarifas de los PDF nuevos o modificados, usando la posición del texto en la página
                                                  -> tarifas_detalle.csv   (todas las categorías)
  - Une las tarifas de camión con las plazas, buscando solo dentro de la concesión de cada documento
                                                  -> tarifas_camiones.csv  (una fila por tarifa del PDF y plaza a la que se asignó)
                                                  -> tabla_integrada.csv   (RESULTADO: una fila por plaza x categoría x horario x vigencia)
                                                  -> porticos_urbanos.csv  (autopistas urbanas: tarifa por pórtico, horario y TAG)
                                                  -> plazas_candidatas_manual.csv (plazas del PDF que faltan en la capa del MOP)
                                                  -> revision.csv          (lo que hay que mirar a mano)

Archivos que se editan a mano (se crean la primera vez con valores iniciales; después el programa no los toca):
  - concesiones_documentos.csv : a qué concesión de la capa del MOP corresponde cada documento de la DGC
  - alias_plazas.csv           : encabezados del PDF que no nombran la plaza (p. ej. "Norte y Sur")
  - plazas_manual.csv          : plazas que no están en la capa del MOP (la capa se actualizó por última vez en 2016)
  - encabezados_manual.csv     : textos de encabezado de láminas que los dibujan como imagen (Vespucio Sur, Túnel San Cristóbal)

Uso:
  python peajes_descarga.py                  # descarga, extrae e integra
  python peajes_descarga.py --anio 2026      # solo documentos de ese año
  python peajes_descarga.py --solo-extraer   # sin internet: vuelve a extraer desde los PDF ya descargados
  python peajes_descarga.py --reextraer      # vuelve a extraer todos los PDF, aunque no hayan cambiado
  python peajes_descarga.py --solo-integrar  # sin internet ni PDF: rehace la unión con las plazas y la tabla integrada
                                             # (usarlo después de editar los archivos manuales)

Requisitos: pip install requests beautifulsoup4 pdfplumber pandas

Para que siga funcionando sin nadie: programarlo una vez al mes (Programador de tareas de Windows o cron).
Las tarifas cambian normalmente en enero, pero hay concesiones que publican a mitad de año.
El programa termina con código 1 si la página no entrega documentos (por ejemplo, si cambió su diseño),
para que el programador de tareas lo marque como fallido.
"""
import argparse
import hashlib
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

URL_DGC = "https://concesiones.mop.gob.cl/peajes-y-porticos/"
URL_PLAZAS = "https://rest-sit.mop.gob.cl/arcgis/rest/services/VIALIDAD/Infraestructura_Vial/MapServer/1/query"
CABECERAS = {"User-Agent": "Mozilla/5.0 (seminario de titulo UCN; descarga de tarifas publicas de peajes)"}
RE_NO_TARIFAS = re.compile(r"ETICA|PRIVACIDAD|POLITICA DE|REGLAMENTO|INSTRUCTIVO|PROTOCOLO")
MESES = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
         "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12}
VARIACION_ALERTA = 0.15          # cambio anual de tarifa sobre este % se manda a revisión

# Documento de la DGC -> concesión en la capa del MOP. Se compara por palabras completas, sin tildes ni signos:
# ("RUTA 68", "SANTIAGO-VALPARAISO-VINA DEL MAR") une el PDF "RUTA 68" con "Interconexión Vial Santiago-Valparaíso-Viña del Mar".
# Un documento puede tener varias filas (Santiago - Los Vilos incluye el Túnel El Melón).
# Los peajes fiscales solo se unen si el texto de la concesión dice "PEAJE FISCAL".
CONCESIONES_INICIALES = [
    ("SANTIAGO - LOS VILOS", "SANTIAGO - LOS VILOS"), ("SANTIAGO - LOS VILOS", "TUNEL EL MELON"),
    ("LOS VILOS - LA SERENA", "LOS VILOS - LA SERENA"), ("LA SERENA - VALLENAR", "LA SERENA - VALLENAR"),
    ("VALLENAR - CALDERA", "VALLENAR - CALDERA"), ("SANTIAGO - TALCA", "SANTIAGO - TALCA"), ("TALCA - CHILLAN", "TALCA - CHILLAN"),
    ("CHILLAN - COLLIPULLI", "CHILLAN - COLLIPULLI"), ("COLLIPULLI - TEMUCO", "COLLIPULLI - TEMUCO"),
    ("TEMUCO - RIO BUENO", "TEMUCO - RIO BUENO"), ("RIO BUENO - PTO MONTT", "RIO BUENO - PUERTO MONTT"),
    ("PTO MONTT - PARGUA", "PUERTO MONTT - PARGUA"), ("AUTOPISTA DE LA REGION DE ANTOFAGASTA", "REGION DE ANTOFAGASTA"),
    ("ACCESO IQUIQUE", "ACCESO A IQUIQUE"), ("CAMINO NOGALES - PUCHUNCAVI", "NOGALES - PUCHUNCAVI"),
    ("RUTA 60 CH", "CAMINO INTERNACIONAL, RUTA 60 CH"), ("LOS LIBERTADORES", "COLINA - LOS ANDES"),
    ("RUTA 68", "SANTIAGO-VALPARAISO-VINA DEL MAR"), ("RUTA 78", "SANTIAGO - SAN ANTONIO"),
    ("RED VIAL LITORAL CENTRAL", "RED VIAL LITORAL CENTRAL"), ("VARIANTE MELIPILLA", "VARIANTE MELIPILLA"),
    ("RUTA DEL ITATA", "ACCESO NORTE A CONCEPCION"), ("RUTA INTERPORTUARIA", "RUTA INTERPORTUARIA"), ("RUTA 160", "RUTA 160"),
    ("ACCESO NORORIENTE", "NOR-ORIENTE"), ("ACCESO VIAL AEROPUERTO DE SANTIAGO", "AEROPUERTO ARTURO MERINO"),
]

# Alias para encabezados de los PDF que no traen el nombre de la plaza.
# (texto que contiene el nombre del documento, texto que contiene el encabezado, nombre de la plaza en la capa del MOP)
# Si el tercer valor es TRONCAL o LATERAL, la tarifa vale para todas las plazas de ese tipo en la concesión.
ALIAS_INICIALES = [
    ("LOS VILOS - LA SERENA", "NORTE Y SUR", "Angostura de Gálvez"),
    ("LOS VILOS - LA SERENA", "NORTE Y SUR", "Cerrillos Bajos"),
    ("RUTA 68", "LATERAL Y TRONCAL SUR", "Troncal Sur"),
    ("RUTA 68", "LATERAL Y TRONCAL SUR", "LATERAL"),
    # Interportuaria: la lámina cobra por origen-destino ("AEROPUERTO DE PENCO", "TALCAHUANO"...); la plaza Colón de la
    # capa está en Talcahuano y Alessandri junto al aeropuerto. "^" = el encabezado empieza con ese texto.
    ("RUTA INTERPORTUARIA", "^TALCAHUANO", "Colón"),
    ("RUTA INTERPORTUARIA", "^AEROPUERTO", "Alessandri"),
    # encabezados de las láminas 2025 (laminas_historicas.py)
    ("RUTA 68", "ACCESOS Y TRONCAL SUR", "Troncal Sur"),
    ("RUTA 68", "ACCESOS Y TRONCAL SUR", "LATERAL"),
    ("RUTA 78", "=POMAIRE", "Pomaire"),                         # 2025 dice "Pomaire"; 2026 "Pomaire/ Melipilla"
    ("RUTA 78", "=POMAIRE", "Melipilla 1"),
    ("RUTA 78", "=POMAIRE", "Melipilla 2"),
    ("CAMINO NOGALES - PUCHUNCAVI", "=NORMAL", "Troncal Nogales-Puchuncaví"),   # 2025: antes del Bypass
    ("CAMINO NOGALES - PUCHUNCAVI", "=PUNTA", "Troncal Nogales-Puchuncaví"),
    ("TUNEL EL MELON", "TRONCAL", "El Melón"),                  # lámina propia del túnel en 2025
    ("TUNEL EL MELON", "TARIFA UNICA", "El Melón"),
]

# Textos de encabezado fijados a mano, para láminas que dibujan parte del texto como imagen y no se puede leer.
# eje "columna": n-ésima columna de montos de cada tabla (de izquierda a derecha); eje "fila": n-ésima fila de montos.
# Si la lámina cambia y el número de columnas o filas ya no calza, la plantilla no se aplica y la tarifa queda en revisión.
_VSUR = ["P1.3 2ª Transversal – Camino a Melipilla", "P2.2 Gral. Velásquez – Ruta 5", "P3.4 Ruta 5 – Gran Avenida",
         "P3.2 Gran Avenida – Santa Rosa", "P4.3 Coronel – Santa Julia", "P4.2 Gnmo. de Alderete – V. Mackenna",
         "P5.4 Las Torres – Quilín", "P5.2 Quilín – Grecia", "P5.1 Grecia – Quilín", "P5.3 Quilín – Las Torres",
         "P4.1 Gnmo. de Alderete – Santa Julia", "P3.1 Santa Rosa – Gran Avenida", "P3.3 Gran Avenida – Ruta 5",
         "P2.1 Ruta 5 – General Velásquez", "P1.1 General Velásquez – Ruta 78"]
_CATS_URB = ["Categoría 1 y 4: motos, autos y camionetas", "Categoría 2: buses y camiones", "Categoría 3: camiones con remolque"]
PLANTILLAS_INICIALES = (
    [{"documento_contiene": "AUTOPISTA VESPUCIO SUR", "tabla": "", "eje": "fila", "numero": i + 1,
      "texto": f"{t} (sentido {'poniente-oriente' if i < 8 else 'oriente-poniente'})"} for i, t in enumerate(_VSUR)] +
    [{"documento_contiene": "TUNEL SAN CRISTOBAL", "tabla": "", "eje": "columna", "numero": i + 1,
      "texto": f"{c} - {'usuarios con TAG' if i < 3 else 'infractores sin TAG'}"} for i, c in enumerate(_CATS_URB * 2)])

# Plazas que no están en la capa del MOP (la capa se actualizó por última vez en 2016). Completar a mano.
PLAZAS_MANUALES_INICIALES = [
    {"nombre": "Punta Colorada", "concesion": "Ruta 5 Norte La Serena - Vallenar", "ruta": "RUTA 5", "km": 554,
     "posicion": "Troncal", "observacion": "km tomado de una fuente secundaria (Chócale 2026): verificar"},
]

# ----------------------------------------------------------------------------- utilidades

def normalizar(texto):
    """Mayúsculas, sin tildes y con espacios simples."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.upper()).strip()


def texto_clave(texto):
    """Normalizado y solo con letras, números y espacios: 'Ruta 5 Norte, Tramo Vallenar-Caldera' -> 'RUTA 5 NORTE TRAMO VALLENAR CALDERA'."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]", " ", normalizar(texto))).strip()


def alnum(texto):
    return re.sub(r"[^A-Z0-9]", "", normalizar(texto))


def contiene(texto, clave):
    """¿Aparece la clave en el texto como palabras completas? ('RUTA 1' no está en 'RUTA 16')."""
    k = texto_clave(clave)
    return bool(k) and f" {k} " in f" {texto_clave(texto)} "


def sha256(contenido):
    return hashlib.sha256(contenido).hexdigest()


def nueva_sesion():
    s = requests.Session()
    s.headers.update(CABECERAS)
    return s


def pedir(sesion, url, intentos=3, **kw):
    """GET con reintentos y espera creciente."""
    ultimo = None
    for i in range(intentos):
        try:
            r = sesion.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            ultimo = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")


def entero(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def leer_csv(ruta):
    return pd.read_csv(ruta, dtype=str, keep_default_na=False) if Path(ruta).exists() else pd.DataFrame()


def guardar_csv(df, ruta):
    df.to_csv(ruta, index=False, encoding="utf-8-sig")       # utf-8-sig: Excel abre bien las tildes


# ----------------------------------------------------------------------------- 1. plazas del MOP

def descargar_plazas(sesion, carpeta, fecha_descarga):
    filas, inicio = [], 0
    while True:
        r = pedir(sesion, URL_PLAZAS, params={"where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": "4326",
                                              "f": "json", "resultOffset": inicio, "resultRecordCount": 1000})
        datos = r.json()
        if "error" in datos:
            raise RuntimeError(f"El servicio de plazas respondió con error: {datos['error']}")
        feats = datos.get("features", [])
        for f in feats:
            a, g = f.get("attributes", {}), f.get("geometry") or {}
            campo = lambda pref: next((a[k] for k in a if normalizar(k).startswith(pref)), None)
            filas.append({"plaza_id_mop": campo("ID") if campo("ID") is not None else campo("OBJECTID"),
                          "nombre": campo("NOMBRE"), "concesion": campo("CONTRATO"), "tramo_concesion": campo("TRAMO"),
                          "ruta": campo("ROL"), "km": campo("KM"), "posicion": campo("POSICION"),
                          "region": campo("REGION"), "observacion": campo("OBSERV"),
                          "lon": g.get("x"), "lat": g.get("y")})
        if not feats or not datos.get("exceededTransferLimit"):
            break
        inicio += len(feats)
    df = pd.DataFrame(filas)
    df["nombre_norm"] = df["nombre"].map(normalizar)
    df["fecha_descarga"] = fecha_descarga
    guardar_csv(df, carpeta / "plazas_mop.csv")
    return df


# ----------------------------------------------------------------------------- 2. documentos de la DGC

def listar_documentos(html, base=URL_DGC):
    """Devuelve (año de la página, lista de documentos PDF con sección, nombre, mes y año publicados)."""
    sopa = BeautifulSoup(html, "html.parser")
    texto = sopa.get_text(" ", strip=True)
    m = re.search(r"PEAJES Y PORTICOS\s+(20\d{2})", normalizar(texto))
    anio_pagina = int(m.group(1)) if m else None
    docs, vistos = [], set()
    for a in sopa.find_all("a", href=True):
        url = urljoin(base, a["href"].strip())
        if not urlparse(url).path.lower().endswith(".pdf") or url in vistos:
            continue
        if RE_NO_TARIFAS.search(normalizar(a.get_text(" ", strip=True) + " " + url)):
            continue                                             # p. ej. "Código de Ética"
        vistos.add(url)
        fila = a.find_parent("tr")
        celdas = [c.get_text(" ", strip=True) for c in fila.find_all(["td", "th"])] if fila else []
        nombre = a.get_text(" ", strip=True) or (celdas[0] if celdas else "") or Path(urlparse(url).path).stem
        contexto = normalizar(" ".join(celdas) + " " + nombre)
        mes = next((v for k, v in MESES.items() if re.search(rf"\b{k}\b", contexto)), None)
        anios = re.findall(r"\b(20\d{2})\b", contexto)
        subida = re.search(r"/uploads/sites/\d+/(20\d{2})/(\d{2})/", url)
        if mes is None and subida and (int(subida.group(1)) == (int(anios[-1]) if anios else anio_pagina)):
            mes = int(subida.group(2))          # sin mes en la página: el mes en que se subió el PDF (vigencia desde ese mes)
        titulo = a.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        docs.append({"seccion": titulo.get_text(" ", strip=True) if titulo else "", "documento": nombre,
                     "mes_publicacion": mes, "anio": int(anios[-1]) if anios else anio_pagina, "url": url})
    return anio_pagina, docs


def descargar_documentos(sesion, docs, carpeta, fecha_descarga, registro_previo):
    ultimo_sha = {}
    if len(registro_previo):
        ok = registro_previo[registro_previo["sha256"] != ""]
        ultimo_sha = ok.groupby("url")["sha256"].last().to_dict()
    filas = []
    for d in docs:
        fila = dict(d, fecha_descarga=fecha_descarga, archivo="", sha256="", bytes="", estado="")
        try:
            r = pedir(sesion, d["url"])
            contenido = r.content
            if not contenido.startswith(b"%PDF"):
                raise RuntimeError("la respuesta no es un PDF")
            huella = sha256(contenido)
            nombre = re.sub(r"[^A-Z0-9]+", "-", normalizar(d["documento"])).strip("-") or Path(urlparse(d["url"]).path).stem
            destino = carpeta / "pdf" / str(d["anio"] or "sin_anio") / f"{nombre}_{huella[:8]}.pdf"
            destino.parent.mkdir(parents=True, exist_ok=True)
            if not destino.exists():
                destino.write_bytes(contenido)
            previo = ultimo_sha.get(d["url"])
            estado = "nuevo" if previo is None else ("sin cambios" if previo == huella else "actualizado")
            fila.update(archivo=str(destino), sha256=huella, bytes=len(contenido), estado=estado)
        except Exception as e:                                   # un documento con problemas no detiene al resto
            fila["estado"] = f"error: {e}"
        filas.append(fila)
    return pd.DataFrame(filas)


# ----------------------------------------------------------------------------- 3. extracción de tarifas

RE_NUM = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$|^\d{2,6}(?:,\d{1,2})?$")
RE_CATEGORIA = re.compile(r"MOTO|AUTO|BUS|CAMION|VEHICUL|MAQUINARIA|CATEGOR|CASA RODANTE|CAMIONETA|LIVIANO|PESADO")
RE_RUIDO = re.compile(r"PEAJES?\s+TARIFAS|TARIFAS\s+20\d{2}|^\d+$"
                      r"|^/?\s*(RUTA 5 (NORTE|SUR)|RUTAS TRANSVERSALES|(AUTOPISTAS|RUTAS) URBANAS)$")   # nombre de la sección
PALABRAS_HORARIO = r"\b(TARIFAS?|BASE|HORARIOS?|FUERA|DE|PUNTA|NORMAL|SATURACION|TBFP|TBP|TS|LABORAL|SABADOS?|DOMINGOS?|FESTIVOS?|Y)\b"


def a_monto(texto):
    t = texto.replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _unir_fragmentos(ws):
    """Algunas láminas parten un número en dos palabras por un espacio de más: '5' '32,59' -> '532,59'."""
    salida = []                                                  # pdfplumber ya las entrega por línea y de izquierda a derecha
    for w in (dict(x) for x in ws):
        a = salida[-1] if salida else None
        if (a is not None and abs(a["top"] - w["top"]) < 2.5 and -1.5 <= w["x0"] - a["x1"] <= 2.6
                and re.match(r"^\$?[\d.,]+$", a["text"]) and re.match(r"^[\d.,]+$", w["text"])
                and RE_NUM.match((a["text"] + w["text"]).lstrip("$"))
                and not (RE_NUM.match(a["text"].lstrip("$")) and RE_NUM.match(w["text"]) and w["x0"] - a["x1"] > 1.2)):
            a.update(text=a["text"] + w["text"], x1=w["x1"], bottom=max(a["bottom"], w["bottom"]))
        else:
            salida.append(w)
    return salida


def _palabras(pagina):
    ws = _unir_fragmentos(pagina.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False))
    usadas, salida = set(), []
    for i, w in enumerate(ws):                                   # une "$" con el número que lo sigue en la misma línea
        if i in usadas:
            continue
        w = dict(w)
        if w["text"] == "$":
            cand = [(j, n) for j, n in enumerate(ws) if j not in usadas and j != i and abs(n["top"] - w["top"]) < 3
                    and 0 <= n["x0"] - w["x1"] < 15 and RE_NUM.match(n["text"])]
            if cand:
                j, n = min(cand, key=lambda c: c[1]["x0"])
                usadas.add(j)
                w = {"text": "$" + n["text"], "x0": w["x0"], "x1": n["x1"], "top": min(w["top"], n["top"]),
                     "bottom": max(w["bottom"], n["bottom"])}
        salida.append(w)
    return salida


def _es_monto(texto, exigir_peso):
    if texto.startswith("$"):
        return RE_NUM.match(texto[1:].strip()) is not None
    return (not exigir_peso) and re.match(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$|^\d{1,6},\d{1,2}$", texto) is not None


def _segmentos(palabras):
    """Agrupa palabras en trozos de texto por línea, separando cuando hay un espacio horizontal grande."""
    palabras = sorted(palabras, key=lambda w: (w["top"], w["x0"]))
    lineas = []
    for w in palabras:
        if lineas and abs(w["top"] - lineas[-1][0]["top"]) <= 2.5:
            lineas[-1].append(w)
        else:
            lineas.append([w])
    segs = []
    for ln in lineas:
        ln.sort(key=lambda w: w["x0"])
        actual = [ln[0]]
        for w in ln[1:]:
            alto = max(w["bottom"] - w["top"], 6)
            if w["x0"] - actual[-1]["x1"] > 1.8 * alto:
                segs.append(actual)
                actual = [w]
            else:
                actual.append(w)
        segs.append(actual)
    return [{"text": " ".join(w["text"] for w in s), "x0": min(w["x0"] for w in s), "x1": max(w["x1"] for w in s),
             "top": min(w["top"] for w in s), "bottom": max(w["bottom"] for w in s)} for s in segs]


def _agrupar(valores, tol):
    """Agrupa valores numéricos cercanos; devuelve el centro de cada grupo, ordenados."""
    centros = []
    for v in sorted(valores):
        if centros and v - centros[-1][-1] <= tol:
            centros[-1].append(v)
        else:
            centros.append([v])
    return [sum(c) / len(c) for c in centros]


def detectar_horario(texto):
    t = normalizar(texto)
    t = re.sub(r"PUNTA (COLORADA|ARENAS|DE TRALCA|DE LOBOS)", "", t)
    if re.search(r"FUERA DE PUNTA|TBFP|HORARIO NORMAL|TARIFA NORMAL|\bNORMAL\b|BASE FUERA", t):
        return "fuera de punta"
    if re.search(r"SATURACION|\bTS\b", t):
        return "saturacion"
    if re.search(r"\bPUNTA\b|\bTBP\b", t):
        return "punta"
    return "unica"


def solo_horario(texto):
    """True si el texto solo indica un horario ('PUNTA', 'NORMAL', 'TBFP'): es el segundo nivel de un encabezado."""
    t = texto_clave(texto)
    return bool(t) and detectar_horario(t) != "unica" and not re.sub(PALABRAS_HORARIO, " ", t).strip()


def detectar_modalidad(texto):
    """Forma de pago a la que se refiere la tarifa ('' = general)."""
    t = normalizar(texto)
    if "PASE DIARIO" in t:
        return "pase diario"
    if "POR PASADA" in t:
        return "por pasada"
    if re.search(r"SIN TAG|NO HABILITAD", t):
        return "sin tag"
    con, manual = re.search(r"CON TAG|TAG\W*HABILITADO|CON TELEVIA", t), re.search(r"\bMANUAL\b", t)
    if con and not manual:
        return "con tag"
    if manual and not con:
        return "manual"
    return ""


def leyenda_categorias(texto):
    """'Categoría 1 y 4: motos, autos y camionetas  Categoría 3: camiones con remolque' -> {'1': ..., '4': ..., '3': ...}."""
    ley = {}
    for linea in texto.splitlines():
        for trozo in re.split(r"(?=\bCATEGORIAS? \d)", normalizar(linea)):
            m = re.match(r"CATEGORIAS? ([\d ,Y]+?)\s*[:=]\s*(.+)", trozo.strip())
            if m:
                for n in re.findall(r"\d+", m.group(1)):
                    ley.setdefault(n, m.group(2).strip(" .;-"))
    return ley


def detectar_tipo_cobro(texto):
    t = normalizar(texto)
    for clave, tipo in (("LATERAL", "lateral"), ("TRONCAL", "troncal"), ("PORTICO", "portico"), ("ACCESO", "acceso"),
                        ("ENLACE", "portico"), ("PASO SUPERIOR", "portico")):
        if clave in t:
            return tipo
    return ""


def categoria_normalizada(texto):
    t = normalizar(texto)
    mas2 = re.search(r"MAS (DE )?(DOS|2) EJES|> ?2 EJES|(3|TRES) O MAS EJES", t)
    camion = re.search(r"\bCAMION(ES)?\b", t)                   # no confunde con "camionetas"
    if re.search(r"SUPERAN? LAS DIMENSIONES|SOBREDIMENSION", t) and not mas2:
        return "sobredimension"
    if camion:
        if mas2:
            return "camion_mas_2_ejes"
        if "CON REMOLQUE" in t and "SIN REMOLQUE" not in t:
            return "camion_con_remolque"
        if "SIN REMOLQUE" in t:
            return "camion_sin_remolque"
        if re.search(r"(DOS|2) EJES", t):
            return "camion_2_ejes"
        return "camion_otro"
    if re.search(r"\bBUS(ES)?\b", t):
        return "bus"
    if "MOTO" in t:
        return "moto"
    if "AUTO" in t or "CAMIONETA" in t or "LIVIANO" in t:
        return "auto_camioneta"
    return "otra"


def extraer_vigencia(texto, anio, mes):
    """Busca fechas de vigencia en el texto; si no hay, usa el mes publicado (o enero) hasta el 31 de diciembre."""
    t = normalizar(texto)
    fechas = []
    for d, m, a in re.findall(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", t):
        try:
            fechas.append(date(int(a), int(m), int(d)))
        except ValueError:
            pass
    for d, m, a in re.findall(r"\b(\d{1,2}) DE ([A-Z]+) (?:DE|DEL) (20\d{2})\b", t):
        if m in MESES:
            try:
                fechas.append(date(int(a), MESES[m], int(d)))
            except ValueError:
                pass
    # "ENTRE EL 1 DE ENERO Y EL 31 DE DICIEMBRE DE 2026": la primera fecha toma el año de la segunda
    for d1, m1, d2, m2, a in re.findall(r"\b(\d{1,2}) DE ([A-Z]+) (?:Y|AL|HASTA)(?: EL)? (\d{1,2}) DE ([A-Z]+) (?:DE|DEL) (20\d{2})\b", t):
        if m1 in MESES and m2 in MESES:
            try:
                fechas.append(date(int(a), MESES[m1], int(d1)))
            except ValueError:
                pass
    if anio:
        fechas = [f for f in fechas if f.year in (anio, anio + 1)]
    fechas = sorted(set(fechas))
    if len(fechas) >= 2:
        return min(fechas), max(fechas), "texto del documento"
    if len(fechas) == 1 and (fechas[0].month, fechas[0].day) == (12, 31):     # "hasta el 31 de diciembre": es el término
        inicio = date(fechas[0].year, int(mes) if mes and anio == fechas[0].year else 1, 1)
        return inicio, fechas[0], "texto del documento (solo término)"
    if len(fechas) == 1:
        return fechas[0], date(fechas[0].year, 12, 31), "texto del documento (solo inicio)"
    if anio:
        return date(anio, int(mes) if mes else 1, 1), date(anio, 12, 31), "inferida (mes de publicación)"
    return None, None, "sin dato"


TOKENS_HORARIO = {"NORMAL", "PUNTA", "TBFP", "TBP", "TS", "SATURACION"}
# palabras de una frase (título) y no de una lista de nombres de plazas
PALABRAS_FRASE = re.compile(r"\b(TARIFAS?|PARA|USUARIOS?|CON|SIN|HORARIOS?|BASE|POR|ENTRE|APLICABLES?|VALORES?|VIGENTES?)\b")
# clasificación estándar de las autopistas urbanas, para láminas que numeran las categorías sin leyenda
CATEGORIAS_URBANAS = {"1": "motos, autos y camionetas", "2": "buses y camiones", "3": "camiones con remolque", "4": "motos y motonetas"}
RE_COL_LONGITUD = re.compile(r"LONGITUD|^KMS?\.?$|\bEN KM\b")


def es_linea_horario(texto):
    """'NORMAL PUNTA NORMAL PUNTA' o 'TBFP TBP TS': la fila de encabezado que solo indica horarios."""
    palabras = texto_clave(texto).split()
    return bool(palabras) and all(p in TOKENS_HORARIO for p in palabras)


def _mediana(valores, defecto):
    v = sorted(valores)
    return v[len(v) // 2] if v else defecto


def _unir_textos(lst):
    return " ".join(x["text"] for x in sorted(lst, key=lambda x: (x["top"], x["x0"])))


def extraer_tarifas_pdf(ruta_pdf, documento, seccion="", plantillas=()):
    """
    Lee un PDF de tarifas y devuelve una fila por monto encontrado, con la categoría y la plaza que le corresponden
    según su posición: la etiqueta a la izquierda en la misma fila y el encabezado sobre la misma columna.
    Funciona con tablas de categorías en filas o en columnas, con varias tablas por página y con encabezados de
    dos niveles (plaza o categoría arriba, horario NORMAL / PUNTA / TBFP abajo).
    `plantillas`: textos fijados a mano para láminas cuyo encabezado está dibujado y no se puede leer (encabezados_manual.csv).
    """
    filas, texto_total = [], []
    doc_norm = alnum(documento)
    nucleo = alnum(re.sub(r"^(AUTOPISTA|CONCESION|CAMINO|ACCESO)\s+((DE LA|DEL|DE)\s+)?", "", normalizar(documento)))
    nombres_doc = {doc_norm} | ({nucleo} if len(nucleo) >= 6 else set())
    urbana = "URBAN" in normalizar(seccion)
    mis_plantillas = [p for p in plantillas if contiene(documento, p.get("documento_contiene", ""))]
    with pdfplumber.open(ruta_pdf) as pdf:
        for n_pag, pagina in enumerate(pdf.pages, start=1):
            texto_total.append(pagina.extract_text() or "")
            ws = _palabras(pagina)
            if not ws:
                continue
            # ¿los montos llevan "$"? Si la mayoría no lo lleva, se aceptan ambos (y los con "$" son la tarifa por km)
            con_peso = sum(1 for w in ws if re.match(r"^\$\d", w["text"]))
            sin_peso = sum(1 for w in ws if not w["text"].startswith("$") and _es_monto(w["text"], False))
            exigir_peso = con_peso > 0 and con_peso >= sin_peso
            mixto = con_peso > 0 and not exigir_peso
            montos = [w for w in ws if _es_monto(w["text"], exigir_peso)]
            no_montos = [w for w in ws if not _es_monto(w["text"], exigir_peso)]
            etiquetas = [s for s in _segmentos(no_montos)
                         if re.search(r"[A-Za-z0-9]", s["text"]) and not RE_RUIDO.search(normalizar(s["text"]))
                         and not any(alnum(s["text"]).startswith(n) for n in nombres_doc)]   # nombre del documento o subtítulo
            if not montos:
                continue
            for m in montos:
                m["yc"], m["xc"] = (m["top"] + m["bottom"]) / 2, (m["x0"] + m["x1"]) / 2
            # tablas: se separan donde hay un salto vertical grande, una fila de horarios (NORMAL / PUNTA) entre montos,
            # o un cambio completo de columnas (otra tabla debajo, como el pase diario)
            ys = _agrupar([m["yc"] for m in montos], 3.5)
            cols_fila = [set() for _ in ys]
            for m in montos:
                cols_fila[min(range(len(ys)), key=lambda k: abs(ys[k] - m["yc"]))].add(m["xc"])
            paso = _mediana([b - a for a, b in zip(ys, ys[1:])], 20)
            x_min, x_max = min(m["x0"] for m in montos), max(m["x1"] for m in montos)
            lineas_h = [s for s in etiquetas if es_linea_horario(s["text"]) and s["x1"] >= x_min - 10 and s["x0"] <= x_max + 10]
            cortes, acumuladas = [], set(cols_fila[0])
            for k, (a, b) in enumerate(zip(ys, ys[1:])):
                rango = cols_fila[k] | cols_fila[k + 1]
                hay_linea = any(a + 3 < (s["top"] + s["bottom"]) / 2 < b - 3 and s["x1"] >= min(rango) - 15
                                and s["x0"] <= max(rango) + 15 for s in lineas_h)
                siguientes = cols_fila[k + 1] | (cols_fila[k + 2] if k + 2 < len(ys) else set())
                disjuntas = b - a > 20 and not any(abs(x1 - x2) <= 14 for x1 in acumuladas for x2 in siguientes)
                if b - a > max(40, 3 * paso) or hay_linea or disjuntas:
                    cortes.append((a + b) / 2)                   # el corte va entre las dos filas
                    acumuladas = set()
                acumuladas |= cols_fila[k + 1]
            limites = [-1e9] + cortes + [1e9]
            fondo_anterior = 0
            for t_idx in range(len(limites) - 1):
                mt = [m for m in montos if limites[t_idx] <= m["yc"] < limites[t_idx + 1]]
                if not mt:
                    continue
                filas_y = _agrupar([m["yc"] for m in mt], 3.5)
                cols_x = _agrupar([m["xc"] for m in mt], 14)
                y_top, y_bot = min(m["top"] for m in mt), max(m["bottom"] for m in mt)
                x_izq, x_der = min(m["x0"] for m in mt), max(m["x1"] for m in mt)
                sep_col = _mediana([b - a for a, b in zip(cols_x, cols_x[1:])], x_der - x_izq + 60)
                medio_paso = (min(b - a for a, b in zip(filas_y, filas_y[1:])) / 2) if len(filas_y) > 1 else 10
                margen = max(medio_paso, 12)
                en_banda = lambda o: fondo_anterior <= o["top"] and o["bottom"] <= y_top + 1
                banda = [s for s in etiquetas if en_banda(s) and s["x1"] >= x_izq - 25 and s["x0"] <= x_der + 25]
                arriba_izq = [s for s in etiquetas if en_banda(s) and s["x1"] < x_izq - 25 and y_top - s["bottom"] <= 80]
                izq = [s for s in etiquetas if not en_banda(s) and s["x1"] <= x_izq + 2
                       and y_top - margen <= (s["top"] + s["bottom"]) / 2 <= y_bot + margen]

                # 1) fila de horarios del encabezado: cada columna recibe su NORMAL / PUNTA / TBFP...
                segs_hor = [s for s in banda if es_linea_horario(s["text"])]
                hor, top_h, usados = {}, None, []
                if segs_hor:
                    top_h = max(s["top"] for s in segs_hor)              # la fila de horarios más baja del encabezado
                    linea = [w for w in no_montos if abs(w["top"] - top_h) <= 2.5 and any(
                        s["x0"] - 0.5 <= w["x0"] and w["x1"] <= s["x1"] + 0.5 and abs(s["top"] - top_h) <= 2.5 for s in segs_hor)]
                    no_hor = [s for s in banda if not es_linea_horario(s["text"])]
                    for w in linea:
                        if any(s["top"] > top_h + 2.5 and s["x0"] < w["x1"] and w["x0"] < s["x1"] for s in no_hor):
                            continue                             # hay texto debajo ("PUNTA" / "COLORADA"): es parte de un nombre
                        wc = (w["x0"] + w["x1"]) / 2
                        c = min(range(len(cols_x)), key=lambda k: abs(cols_x[k] - wc))
                        if abs(cols_x[c] - wc) <= max(0.6 * sep_col, 12) and (c not in hor or abs(cols_x[c] - wc) < hor[c][1]):
                            hor[c] = (w["text"], abs(cols_x[c] - wc))
                            usados.extend(s for s in segs_hor if s["x0"] - 0.5 <= w["x0"] and w["x1"] <= s["x1"] + 0.5
                                          and abs(s["top"] - w["top"]) <= 2.5)
                # 2) grupos de columnas: columnas seguidas con horarios distintos (NORMAL, PUNTA) son de la misma plaza
                grupos = []
                for c in range(len(cols_x)):
                    t = texto_clave(hor[c][0]) if c in hor else None
                    if grupos and t and grupos[-1]["tokens"] and t not in grupos[-1]["tokens"]:
                        grupos[-1]["cols"].append(c)
                        grupos[-1]["tokens"].add(t)
                    else:
                        grupos.append({"cols": [c], "tokens": {t} if t else set(), "segs": []})
                for g in grupos:
                    xs = [cols_x[c] for c in g["cols"]]
                    g.update(izq=min(xs) - 0.5 * sep_col, der=max(xs) + 0.5 * sep_col, centro=sum(xs) / len(xs))
                dist_grupo = lambda g, x: 0 if g["izq"] <= x <= g["der"] else min(abs(x - g["izq"]), abs(x - g["der"]))
                # 3) cada texto del encabezado va al grupo que tiene debajo; una lista de nombres que cruza varios grupos
                #    ("CHIVILINGO CURANILAHUE") se parte por palabras; una frase que los cruza es título de la tabla
                titulos = []
                for s in banda:
                    if any(s is u for u in usados):
                        continue                                 # ya se usó como horario de su columna
                    cubiertos = [g for g in grupos if s["x0"] - 2 <= g["centro"] <= s["x1"] + 2]
                    if len(cubiertos) >= 2:
                        if not PALABRAS_FRASE.search(normalizar(s["text"])) and not solo_horario(s["text"]):
                            pal = [w for w in no_montos if abs(w["top"] - s["top"]) < 2.5 and w["x0"] >= s["x0"] - 0.5 and w["x1"] <= s["x1"] + 0.5]
                            por_grupo = {}
                            for w in pal:
                                g = min(grupos, key=lambda g: dist_grupo(g, (w["x0"] + w["x1"]) / 2))
                                por_grupo.setdefault(id(g), (g, []))[1].append(w)
                            if len(por_grupo) >= 2:
                                for g, lst in por_grupo.values():
                                    g["segs"].append({"text": " ".join(w["text"] for w in sorted(lst, key=lambda w: w["x0"])),
                                                      "x0": min(w["x0"] for w in lst), "x1": max(w["x1"] for w in lst),
                                                      "top": min(w["top"] for w in lst), "bottom": max(w["bottom"] for w in lst)})
                                continue
                        titulos.append(s)
                    elif len(cubiertos) == 1:
                        cubiertos[0]["segs"].append(s)
                    else:
                        sc = (s["x0"] + s["x1"]) / 2
                        sobre = [g for g in grupos if s["x1"] >= g["izq"] and s["x0"] <= g["der"]]
                        # centrado entre dos grupos vecinos ("PENCO" sobre "DE TALCAHUANO" y "DE AEROPUERTO"): vale para ambos
                        entre = [(a, b) for a, b in zip(grupos, grupos[1:])
                                 if abs(sc - (a["centro"] + b["centro"]) / 2) < 0.25 * (b["centro"] - a["centro"])]
                        if entre:
                            entre[0][0]["segs"].append(s)
                            entre[0][1]["segs"].append(s)
                        elif sobre:
                            min(sobre, key=lambda g: abs(g["centro"] - sc))["segs"].append(s)
                        else:
                            titulos.append(s)
                # en cada grupo, el encabezado son las líneas pegadas a la tabla; lo que queda más arriba es título
                for g in grupos:
                    lst = sorted(g["segs"], key=lambda x: -x["bottom"])
                    pegadas = lst[:1]
                    for x in lst[1:]:
                        alto = max(x["bottom"] - x["top"], 6)
                        if pegadas[-1]["top"] - x["bottom"] <= 1.2 * alto:
                            pegadas.append(x)
                        else:
                            titulos.append(x)
                    g["segs"] = pegadas
                encabezado = {}
                for g in grupos:
                    texto_g = _unir_textos(g["segs"])
                    for c in g["cols"]:
                        encabezado[c] = " ".join(p for p in (texto_g, hor[c][0] if c in hor else "") if p)
                # sin nombre sobre las columnas: la plaza está escrita arriba a la izquierda ("TRONCAL NOGALES")
                if arriba_izq and not any(g["segs"] for g in grupos):
                    sup = max(arriba_izq, key=lambda s: s["bottom"])
                    encabezado = {c: f"{sup['text']} {t}".strip() for c, t in encabezado.items()}
                    titulos = [x for x in titulos if x is not sup]
                titulo_tabla = _unir_textos(titulos)

                # 4) etiquetas de fila: una línea que empieza con minúscula continúa la anterior ("Camiones de más" /
                #    "de dos ejes"); cada categoría queda como un bloque y el monto va al bloque que lo contiene
                bloques = []
                for s in sorted(izq, key=lambda x: (x["top"], x["x0"])):
                    letra = next((ch for ch in s["text"] if ch.isalpha()), "")
                    alto = max(s["bottom"] - s["top"], 6)
                    if bloques and (abs(s["top"] - bloques[-1]["segs"][-1]["top"]) <= 2.5 or
                                    (letra.islower() and s["top"] - bloques[-1]["bottom"] <= 1.5 * alto)):
                        bloques[-1]["segs"].append(s)
                        bloques[-1]["bottom"] = max(bloques[-1]["bottom"], s["bottom"])
                    else:
                        bloques.append({"segs": [s], "top": s["top"], "bottom": s["bottom"]})

                def etiqueta_fila(yc):
                    if not bloques:
                        return []
                    dist = [0 if b["top"] - 1 <= yc <= b["bottom"] + 1 else min(abs(yc - b["top"]), abs(yc - b["bottom"])) for b in bloques]
                    k = min(range(len(bloques)), key=lambda j: (dist[j], abs((bloques[j]["top"] + bloques[j]["bottom"]) / 2 - yc)))
                    return bloques[k]["segs"] if dist[k] <= margen else []

                # 5) textos fijados a mano (encabezados dibujados como imagen)
                pl = lambda eje: {int(float(p["numero"])): p["texto"] for p in mis_plantillas if p.get("eje") == eje
                                  and (not str(p.get("tabla", "")).strip() or int(float(p["tabla"])) == t_idx + 1)}
                pl_cols, pl_filas, aviso = pl("columna"), pl("fila"), ""
                if pl_cols and max(pl_cols) != len(cols_x):
                    aviso, pl_cols = f"baja: la plantilla manual de columnas no calza ({len(cols_x)} columnas en el PDF)", {}
                if pl_filas and max(pl_filas) != len(filas_y):
                    aviso, pl_filas = f"baja: la plantilla manual de filas no calza ({len(filas_y)} filas en el PDF)", {}
                for c, t in pl_cols.items():
                    encabezado[c - 1] = t

                for m in mt:
                    fi = min(range(len(filas_y)), key=lambda k: abs(filas_y[k] - m["yc"]))
                    ci = min(range(len(cols_x)), key=lambda k: abs(cols_x[k] - m["xc"]))
                    et_col = encabezado.get(ci, "")
                    if RE_COL_LONGITUD.search(normalizar(et_col)):
                        continue                                 # columna de longitud del tramo (km), no es tarifa
                    et_fila = pl_filas.get(fi + 1) or _unir_textos(etiqueta_fila(m["yc"]))
                    cat_f, cat_c = bool(RE_CATEGORIA.search(normalizar(et_fila))), bool(RE_CATEGORIA.search(normalizar(et_col)))
                    if cat_f and not cat_c:
                        categoria, plaza, confianza = et_fila, et_col, "alta"
                    elif cat_c and not cat_f:
                        categoria, plaza, confianza = et_col, et_fila, "alta"
                    elif cat_f and cat_c:
                        categoria, plaza, confianza = et_fila, et_col, "baja: ambas etiquetas parecen categoría"
                    else:
                        categoria, plaza, confianza = et_fila, et_col, "baja: no se reconoce la categoría"
                    if not plaza:
                        plaza = titulo_tabla or documento
                        confianza = "media: plaza tomada del título" if confianza == "alta" else confianza
                    if mixto and m["text"].startswith("$"):
                        plaza = f"{plaza} (tarifa base por km)"
                    if aviso:
                        confianza = aviso
                    contexto = f"{plaza} {categoria} {titulo_tabla}"
                    horario = detectar_horario(f"{plaza} {categoria}")
                    horario = horario if horario != "unica" else detectar_horario(titulo_tabla)
                    filas.append({"pagina": n_pag, "tabla": t_idx + 1, "fila": fi + 1, "columna": ci + 1,
                                  "plaza_texto": plaza, "categoria_texto": categoria, "titulo_tabla": titulo_tabla,
                                  "tarifa_clp": a_monto(m["text"]), "texto_monto": m["text"],
                                  "horario": horario, "tipo_cobro": detectar_tipo_cobro(contexto),
                                  "confianza": confianza})
                fondo_anterior = y_bot
    # categorías numeradas ("categoría 3"): se completa la descripción con la leyenda del documento o, en las autopistas
    # urbanas sin leyenda, con su clasificación estándar
    ley = leyenda_categorias("\n".join(texto_total))
    for f in filas:
        t = normalizar(f["categoria_texto"])
        m = re.search(r"\bCATEGORIAS? (\d+)", t)
        if not m or re.search(r"CAMION|BUS|AUTO|MOTO|VEHICUL", t):
            continue
        if m.group(1) in ley:
            f["categoria_texto"] = f"{f['categoria_texto']} ({ley[m.group(1)].lower()})"
        elif urbana and m.group(1) in CATEGORIAS_URBANAS:
            f["categoria_texto"] = f"{f['categoria_texto']} ({CATEGORIAS_URBANAS[m.group(1)]})"
            if f["confianza"] == "alta":
                f["confianza"] = "media: categoría según la clasificación estándar de autopistas urbanas"
    return filas, "\n".join(texto_total)


# ----------------------------------------------------------------------------- 4. unión con las plazas y tabla integrada

# palabras que describen el cobro y no son parte del nombre de la plaza
PALABRAS_NO_NOMBRE = (r"\b(TRONCALES|TRONCAL|LATERALES|LATERAL|ACCESOS?|PLAZAS?|PEAJES?|PORTICOS?|ENLACES?|PASO SUPERIOR|TUNEL|"
                      r"SECTOR|TARIFAS?|BASE|HORARIOS?|FUERA DE PUNTA|NORMAL|SATURACION|TBFP|TBP|TS|LABORAL|CON TAG|SIN TAG|TAG|"
                      r"HABILITADOS?|USUARIOS?|PARA|PASE DIARIO|PAGO MANUAL|MANUAL|COBRO POR PASADA|POR PASADA|EN VIAS DE COBRO ELECTRONICO|"
                      r"RUTAS TRANSVERSALES|AUTOPISTAS URBANAS|RUTAS URBANAS|RUTA 5 NORTE|RUTA 5 SUR)\b")
PUNTA_HORARIO = r"\bPUNTA\b(?! (COLORADA|ARENAS|DE TRALCA|DE LOBOS))"
TIPOS_ALIAS = {"TRONCAL": "troncal", "TRONCALES": "troncal", "LATERAL": "lateral", "LATERALES": "lateral",
               "PORTICO": "portico", "PORTICOS": "portico"}
RE_NOMBRE_GENERICO = re.compile(r"^(RUTA [A-Z]? ?\d+[A-Z]?)?$")       # vacío o "RUTA 5": no nombra una plaza


def limpiar_nombre(texto):
    """'TRONCAL Túnel El Melón' -> 'EL MELON'; 'LATERAL Y TRONCAL SUR' -> 'SUR'; 'PUNTA' -> ''."""
    t = re.sub(r"\b([A-Z])/(\d)", r"\1 \2", normalizar(texto))
    t = re.sub(r"\b(TRONCAL|LATERAL)(ES)? \d{1,2}\b", r"\1", texto_clave(t))      # "TRONCAL 1 HUINANCO" -> "TRONCAL HUINANCO"
    t = re.sub(PUNTA_HORARIO, " ", re.sub(PALABRAS_NO_NOMBRE, " ", t))
    t = re.sub(r"\s+", " ", t).strip()
    anterior = None
    while t != anterior:                                         # conectores sueltos en los bordes
        anterior = t
        t = re.sub(r"^(Y|DE|DEL|A|O)\s+|\s+(Y|DE|DEL|A|O)$|^(Y|DE|DEL|A|O)$", "", t).strip()
    return t


def separar_plazas(texto):
    """'LATERALES Combarbalá / Ovalle' -> ['COMBARBALA', 'OVALLE']; 'RUTA F/90' no se separa; sin nombre -> ['']."""
    t = re.sub(r"\b([A-Z])\s*/\s*(\d)", r"\1-\2", normalizar(texto))
    partes = [limpiar_nombre(p) for p in re.split(r"/|,", t)]
    partes = [p for p in partes if p]
    return partes or [""]


def nombre_legible(texto, nombre, partes):
    """Nombre para mostrar, con tildes: 'TRONCAL LAS ARAÑAS' -> 'Las Arañas'."""
    if partes > 1 or not str(texto).strip():
        return nombre.title()
    t = re.sub(r"(?i)(^|/)\s*(rutas transversales|ruta 5 (norte|sur)|(autopistas|rutas) urbanas)\b", " ", str(texto))
    t = re.sub(r"(?i)\b(troncal(es)?|lateral(es)?)\s+\d{1,2}\b", " ", t)              # "TRONCAL 1 HUINANCO" -> "HUINANCO"
    t = re.sub(r"(?i)\b(troncal(es)?|lateral(es)?|plazas?|peajes?|normal|tbfp|tbp|fuera de punta|saturaci[oó]n)\b", " ", t)
    t = re.sub(r"(?i)\bpunta\b(?! (colorada|arenas|de tralca|de lobos))", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -/")
    return t.title() if t.isupper() else t


def posicion_legible(tipo):
    return {"troncal": "Troncal", "lateral": "Lateral", "acceso": "Lateral", "portico": "Pórtico"}.get(str(tipo).lower(), "")


def parecido(a, b):
    """1 = mismo nombre; 0,95 = uno contiene al otro como palabras; si no, similitud de texto. Los números deben coincidir."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    na, nb = set(re.findall(r"\d+", a)), set(re.findall(r"\d+", b))
    if na and nb and na != nb:                                   # RUTA 1 no es RUTA 16
        return 0.0
    if len(a) >= 4 and len(b) >= 4 and (contiene(a, b) or contiene(b, a)):
        return 0.95                                              # MELIPILLA -> MELIPILLA 1 y MELIPILLA 2
    if na != nb:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def preparar_plazas(plazas_mop, manuales):
    """Universo de plazas: la capa del MOP sin duplicados (trae un punto por caseta o sentido) más las plazas manuales."""
    cols = ["concesion", "nombre", "posicion", "ruta", "km", "region", "lat", "lon", "puntos_mop", "fuente_plaza", "observacion_plaza"]
    partes = []
    if plazas_mop is not None and len(plazas_mop):
        p = plazas_mop.copy()
        for c in ("concesion", "nombre", "posicion"):
            p[c] = p[c].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        p["km"] = pd.to_numeric(p["km"], errors="coerce")
        g = p.groupby(["concesion", "nombre", "posicion"], as_index=False).agg(
            ruta=("ruta", "first"), km=("km", "min"), region=("region", "first"), lat=("lat", "first"), lon=("lon", "first"),
            puntos_mop=("nombre", "size"))
        g["fuente_plaza"], g["observacion_plaza"] = "capa MOP", ""
        partes.append(g)
    if manuales is not None and len(manuales):
        m = manuales.copy()
        for c in cols:
            if c not in m.columns:
                m[c] = ""
        m["observacion_plaza"] = m.get("observacion", "")
        m["km"] = pd.to_numeric(m["km"], errors="coerce")
        m["puntos_mop"], m["fuente_plaza"] = 0, "manual"
        partes.append(m[cols])
    if not partes:
        return pd.DataFrame(columns=cols + ["clave_plaza", "nombre_limpio"])
    u = pd.concat(partes, ignore_index=True)[cols]
    u["posicion"] = u["posicion"].fillna("").astype(str).str.strip().str.capitalize()
    u["clave_plaza"] = u["concesion"] + " | " + u["nombre"] + " | " + u["posicion"]
    u = u.drop_duplicates("clave_plaza")
    u["nombre_limpio"] = u["nombre"].map(limpiar_nombre)
    return u.reset_index(drop=True)


def concesiones_de(documento, mapa, concesiones):
    """Concesiones de la capa (o de plazas_manual) que corresponden a un documento de la DGC."""
    claves = [c for d, c in mapa if contiene(documento, d)]
    salida = []
    for conc in concesiones:
        ck = texto_clave(conc)
        fiscal_ok = lambda c: not ck.startswith("PEAJE FISCAL") or texto_clave(c).startswith("PEAJE FISCAL")
        if any(contiene(conc, c) and fiscal_ok(c) for c in claves) or ck == texto_clave(documento):
            salida.append(conc)
    return salida


def corregir_horario(det):
    """
    El horario se toma del encabezado de la plaza o de la categoría. Si solo lo indica el título de la tabla
    (p. ej. "Tarifa Base Fuera de Punta" sobre una lámina que también tiene plazas sin horario), se acepta únicamente
    cuando la misma plaza aparece en el documento con otro horario; si no, la tarifa es única.
    """
    explicito = (det["plaza_texto"].fillna("") + " " + det["categoria_texto"].fillna("")).map(detectar_horario)
    titulo = det["titulo_tabla"].fillna("").map(detectar_horario)
    h = explicito.where(explicito != "unica", titulo)
    llave = det["url"].astype(str) + "|" + det["sha256"].astype(str) + "|" + det["plaza_texto"].map(limpiar_nombre)
    con_horario = h != "unica"
    n_horarios = h[con_horario].groupby(llave[con_horario]).nunique()
    tiene_otro = llave.map(n_horarios).fillna(0) >= 2
    solo_titulo = (explicito == "unica") & (titulo != "unica")
    det["horario"] = h.where(~solo_titulo | tiene_otro, "unica")
    det["horario_fuente"] = "encabezado"
    det.loc[explicito == "unica", "horario_fuente"] = "sin indicación"
    det.loc[solo_titulo & tiene_otro, "horario_fuente"] = "título de la tabla"
    det.loc[solo_titulo & ~tiene_otro, "horario_fuente"] = "título sin contraparte: se asume única"
    return det


def coincide_alias(patron, enc):
    """Encabezado del alias: "NORTE Y SUR" = contiene el texto; "^TALCAHUANO" = empieza con él; "=X" = es exactamente X."""
    p = str(patron).strip()
    if p.startswith("^"):
        return bool(alnum(p[1:])) and enc.startswith(alnum(p[1:]))
    if p.startswith("="):
        return bool(alnum(p[1:])) and enc == alnum(p[1:])
    return bool(alnum(p)) and alnum(p) in enc


def asignar_plazas(cam, univ, mapa, alias):
    """
    Une cada tarifa con la plaza a la que corresponde, buscando SOLO dentro de la concesión de su documento:
      1. alias_plazas.csv            -> metodo_union "alias" (o "alias genérico" si el alias dice TRONCAL/LATERAL)
      2. nombre igual o contenido     -> "por nombre" (puede asignar varias plazas: "Pomaire/Melipilla", "Melipilla 1 y 2")
      3. sin nombre ("TRONCAL", "LATERAL", "PUNTA", "RUTA 5") -> "genérica": vale para las plazas de ese tipo
      4. con nombre que no está en la capa -> "sin coincidencia" (candidata a plazas_manual.csv)
    Cuando el PDF no tenía encabezado y la plaza se tomó del título de la tabla, solo se acepta la unión por nombre
    ("por nombre (título)", confianza baja) y nunca se trata como genérica.
    Devuelve una fila por tarifa y plaza asignada.
    """
    concesiones = sorted(univ["concesion"].unique()) if len(univ) else []
    cache, filas = {}, []
    vacio = {"plaza_mop": "", "clave_plaza": "", "concesion_mop": "", "ruta": "", "km": "", "posicion_mop": "", "puntaje": ""}

    def con_plaza(base, p, metodo, puntaje):
        return dict(base, plaza_mop=p["nombre"], clave_plaza=p["clave_plaza"], concesion_mop=p["concesion"], ruta=p["ruta"],
                    km=p["km"], posicion_mop=p["posicion"], metodo_union=metodo, puntaje=puntaje, generica=False)

    for _, r in cam.iterrows():
        doc = r["documento"]
        if doc not in cache:
            cache[doc] = concesiones_de(doc, mapa, concesiones)
        concs = cache[doc]
        cand = univ[univ["concesion"].isin(concs)]
        base = dict(r.to_dict(), concesiones_doc=" ; ".join(concs), plaza_pdf=str(r["plaza_texto"]), **vacio)
        enc = alnum(r["plaza_texto"])
        objetivos = [a[2] for a in alias if contiene(doc, a[0]) and coincide_alias(a[1], enc)]
        if objetivos:
            for obj in objetivos:
                tipo = TIPOS_ALIAS.get(texto_clave(obj))
                if tipo:
                    filas.append(dict(base, plaza=texto_clave(obj), tipo_cobro=tipo, metodo_union="alias genérico", generica=True))
                    continue
                hit = cand[cand["nombre"].map(normalizar) == normalizar(obj)]
                if not len(hit):
                    hit = univ[univ["nombre"].map(normalizar) == normalizar(obj)]
                if len(hit):
                    filas += [con_plaza(dict(base, plaza=normalizar(obj)), p, "alias", 1.0) for _, p in hit.iterrows()]
                else:
                    filas.append(dict(base, plaza=normalizar(obj), metodo_union="alias sin plaza en la capa", generica=False))
            continue
        desde_titulo = str(r.get("confianza", "")).startswith("media: plaza tomada del título")
        nombres = separar_plazas(r["plaza_texto"])
        for nombre in nombres:
            if texto_clave(nombre) == texto_clave(doc):          # el encabezado repite el nombre del documento
                nombre = ""
            fila = dict(base, plaza=nombre, plaza_pdf=nombre_legible(r["plaza_texto"], nombre, len(nombres)))
            if not concs:
                filas.append(dict(fila, metodo_union="documento sin concesión en la capa", generica=False))
                continue
            puntajes = cand["nombre_limpio"].map(lambda b: parecido(nombre, b)) if len(cand) else pd.Series(dtype=float)
            mejor = puntajes.max() if len(puntajes) else 0
            if mejor >= 0.85:
                elegidas = cand[puntajes == mejor] if mejor >= 0.95 else cand[puntajes == mejor].head(1)
                tipo_pdf = {"acceso": "lateral"}.get(str(r.get("tipo_cobro", "")), str(r.get("tipo_cobro", "")))
                if elegidas["posicion"].str.lower().nunique() > 1 and (elegidas["posicion"].str.lower() == tipo_pdf).any():
                    elegidas = elegidas[elegidas["posicion"].str.lower() == tipo_pdf]
                metodo = "por nombre (título)" if desde_titulo else "por nombre"
                filas += [con_plaza(fila, p, metodo, round(mejor, 2)) for _, p in elegidas.iterrows()]
            elif RE_NOMBRE_GENERICO.match(nombre) and not desde_titulo:
                filas.append(dict(fila, metodo_union="genérica", generica=True, puntaje=round(mejor, 2)))
            else:
                filas.append(dict(fila, metodo_union="sin coincidencia", generica=False, puntaje=round(mejor, 2)))
    out = pd.DataFrame(filas)
    # tipo de cobro: el que dice el PDF; si no dice, el de la plaza encontrada
    out["tipo_cobro"] = out["tipo_cobro"].fillna("").astype(str).replace("nan", "")
    sin_tipo = out["tipo_cobro"] == ""
    out.loc[sin_tipo, "tipo_cobro"] = out.loc[sin_tipo, "posicion_mop"].fillna("").astype(str).str.lower()
    return out


def tabla_integrada(cam, univ):
    """
    RESULTADO: una fila por plaza (capa del MOP + manuales) x categoría x horario x modalidad x vigencia, con su tarifa.
    Prioridad para cada plaza: tarifa con su nombre (o alias) > tarifa genérica de su tipo en la concesión >
    tarifa genérica sin tipo del documento. Las tarifas del PDF que no se asignaron a ninguna plaza se agregan al final.
    """
    cam = cam.copy()
    lista_concs = cam["concesiones_doc"].map(lambda s: [c for c in str(s).split(" ; ") if c])
    concs_con_doc = {c for cs in lista_concs for c in cs}
    # tipo para repartir las genéricas: si la concesión no tiene plazas de ese tipo (p. ej. "pórtico"), se trata como sin tipo
    tipos_conc = univ.assign(t=univ["posicion"].map(normalizar).str.lower()).groupby("concesion")["t"].agg(set).to_dict()
    tipo = cam["tipo_cobro"].replace({"acceso": "lateral"})
    existe = [t in set().union(*[tipos_conc.get(c, set()) for c in cs]) if cs else False for t, cs in zip(tipo, lista_concs)]
    cam["tipo_union"] = tipo.where(existe, "")
    directas = cam[cam["clave_plaza"] != ""]
    genericas = cam[cam["generica"] == True]                      # noqa: E712 (columna con objetos)
    usadas = set()
    claves_grupo = ["categoria", "horario", "modalidad", "vigencia_desde"]
    salida = []
    for _, p in univ.iterrows():
        info = {"concesion": p["concesion"], "plaza_mop": p["nombre"], "posicion": p["posicion"], "ruta": p["ruta"], "km": p["km"],
                "region": p["region"], "lat": p["lat"], "lon": p["lon"], "puntos_mop": p["puntos_mop"], "fuente_plaza": p["fuente_plaza"]}
        d = directas[directas["clave_plaza"] == p["clave_plaza"]]
        if (d["metodo_union"] != "por nombre (título)").any():    # lo leído en el encabezado manda sobre lo tomado del título
            d = d[d["metodo_union"] != "por nombre (título)"]
        if len(d):
            usar, metodo, confianza = d.assign(_nivel="directa"), None, "alta"
        else:
            g = genericas[genericas["concesiones_doc"].map(lambda s: p["concesion"] in str(s).split(" ; "))]
            g_tipo, g_sin = g[g["tipo_union"] == normalizar(p["posicion"]).lower()], g[g["tipo_union"] == ""]
            if len(g_tipo):
                # las genéricas sin tipo completan horarios o modalidades que la del tipo no trae
                # (p. ej. un encabezado "TRONCAL" sobre NORMAL y PUNTA que solo quedó pegado a NORMAL)
                cubiertas = set(zip(g_tipo["categoria"], g_tipo["horario"], g_tipo["modalidad"]))
                nuevas = pd.Series([c not in cubiertas for c in zip(g_sin["categoria"], g_sin["horario"], g_sin["modalidad"])],
                                   index=g_sin.index, dtype=bool)
                extra = g_sin[nuevas]
                if (g_tipo["horario"] == "unica").all():           # solo se completan horarios de una tarifa que ya es por horario
                    extra = extra.iloc[0:0]
                extra = extra[extra["horario"] != "unica"]
                usar = pd.concat([g_tipo.assign(_nivel="tipo"), extra.assign(_nivel="sin tipo")])
                metodo, confianza = "por tipo dentro de la concesión", "media"
            elif len(g_sin):
                usar, metodo, confianza = g_sin.assign(_nivel="sin tipo"), "genérica del documento (sin tipo)", "baja"
            else:
                motivo = "sin tarifa encontrada" if p["concesion"] in concs_con_doc else "concesión sin documento de tarifas"
                salida.append(dict(info, metodo_asignacion=motivo, confianza=""))
                continue
        usadas |= set(usar.index)
        mezcla = set()
        for (cat, mod), gg in usar.groupby(["categoria", "modalidad"]):
            hs = set(gg["horario"])
            if "unica" in hs and len(hs) > 1:
                mezcla.add((cat, mod))
        for (cat, hor, mod, vig), gg in usar.groupby(claves_grupo, dropna=False):
            valores = sorted(set(gg["tarifa_clp"].dropna()))
            obs = []
            if len(valores) > 1:
                obs.append("valores distintos en el documento: " + " / ".join(f"{v:,.0f}".replace(",", ".") for v in valores))
            if (cat, mod) in mezcla:
                obs.append("la plaza tiene tarifa única y por horario: revisar")
            if metodo is None:
                met = "alias" if (gg["metodo_union"] == "alias").all() else (
                    "por nombre (título de la tabla)" if (gg["metodo_union"] == "por nombre (título)").any() else "por nombre")
            else:
                met = metodo if (gg["_nivel"] == "tipo").any() else "genérica del documento (sin tipo)"
            conf = "baja" if obs or "título" in met or "sin tipo" in met else confianza
            salida.append(dict(info, categoria=cat, horario=hor, modalidad=mod, tarifa_clp=max(valores) if valores else None,
                               vigencia_desde=vig, vigencia_hasta=gg["vigencia_hasta"].iloc[0], metodo_asignacion=met,
                               confianza=conf, observacion="; ".join(obs), documento=gg["documento"].iloc[0],
                               encabezado_pdf=" | ".join(dict.fromkeys(gg["plaza_texto"].astype(str)))[:200],
                               url=gg["url"].iloc[0], fecha_descarga=gg["fecha_descarga"].iloc[0]))
    # tarifas del PDF que no quedaron en ninguna plaza
    resto = cam[~cam.index.isin(usadas)]
    motivos = {"sin coincidencia": "solo en el PDF: plaza que falta en la capa del MOP (agregar a plazas_manual.csv)",
               "documento sin concesión en la capa": "solo en el PDF: concesión que no está en la capa del MOP (agregar sus plazas a plazas_manual.csv)",
               "alias sin plaza en la capa": "solo en el PDF: el alias apunta a una plaza que no existe",
               "urbana": "solo en el PDF: autopista urbana (sus pórticos no están en la capa del MOP)",
               "por nombre (título)": "solo en el PDF: lectura dudosa (plaza tomada del título) y la plaza ya tiene tarifa del encabezado",
               "genérica": "solo en el PDF: tarifa genérica sin plazas de ese tipo en la concesión",
               "alias genérico": "solo en el PDF: tarifa genérica sin plazas de ese tipo en la concesión"}
    resto = resto.drop_duplicates(["documento", "plaza_texto", "plaza", "categoria", "horario", "modalidad", "tarifa_clp", "vigencia_desde"])
    for _, r in resto.iterrows():
        urbana = "URBAN" in normalizar(r.get("seccion", ""))
        if urbana and r["metodo_union"] == "documento sin concesión en la capa":
            r["metodo_union"] = "urbana"
        salida.append({"concesion": r["concesiones_doc"] or r["documento"], "plaza_mop": "", "posicion": posicion_legible(r["tipo_cobro"]),
                       "fuente_plaza": "solo PDF", "plaza_pdf": r["plaza_pdf"] if r["plaza"] else "", "categoria": r["categoria"],
                       "horario": r["horario"], "modalidad": r["modalidad"], "tarifa_clp": r["tarifa_clp"],
                       "vigencia_desde": r["vigencia_desde"], "vigencia_hasta": r["vigencia_hasta"],
                       "metodo_asignacion": motivos.get(r["metodo_union"], "solo en el PDF")
                       if r["plaza"] or r["generica"] == True or urbana  # noqa: E712
                       else "solo en el PDF: no se pudo leer a qué plaza corresponde (revisar la lámina)", "confianza": "",
                       "observacion": "", "documento": r["documento"], "encabezado_pdf": r["plaza_texto"], "url": r["url"],
                       "fecha_descarga": r["fecha_descarga"]})
    columnas = ["concesion", "plaza_mop", "plaza_pdf", "posicion", "ruta", "km", "region", "lat", "lon", "puntos_mop", "fuente_plaza",
                "categoria", "horario", "modalidad", "tarifa_clp", "vigencia_desde", "vigencia_hasta", "metodo_asignacion", "confianza",
                "observacion", "documento", "encabezado_pdf", "url", "fecha_descarga"]
    integ = pd.DataFrame(salida).reindex(columns=columnas)
    integ["_orden"] = integ["fuente_plaza"].eq("solo PDF")
    integ["_km"] = pd.to_numeric(integ["km"], errors="coerce")
    integ = integ.sort_values(["_orden", "concesion", "_km", "plaza_mop", "plaza_pdf", "categoria", "horario"]).drop(columns=["_orden", "_km"])
    return integ.reset_index(drop=True)


def candidatas_manual(cam):
    """Plazas con nombre en el PDF que no están en la capa del MOP, con las columnas de plazas_manual.csv para copiar y completar."""
    urbana = cam.get("seccion", pd.Series("", index=cam.index)).fillna("").map(normalizar).str.contains("URBAN")
    c = cam[cam["metodo_union"].isin(["sin coincidencia", "documento sin concesión en la capa"]) & ~urbana].copy()
    sin_nombre = c["plaza"] == ""
    c.loc[sin_nombre, "plaza"] = c.loc[sin_nombre, "documento"].map(normalizar)
    c.loc[sin_nombre, "plaza_pdf"] = c.loc[sin_nombre, "documento"].str.title() + " (plaza única de la lámina: confirmar nombre)"
    c = c[~sin_nombre | (c["metodo_union"] == "documento sin concesión en la capa")]
    if not len(c):
        return pd.DataFrame(columns=["nombre", "concesion", "ruta", "km", "posicion", "observacion"])
    c = c.assign(concesion=c["concesiones_doc"].str.split(" ; ").str[0].where(c["concesiones_doc"] != "", c["documento"]))
    out = c.groupby(["plaza", "concesion"], as_index=False).agg(nombre=("plaza_pdf", "first"), posicion=("tipo_cobro", "first"),
                                                                documento=("documento", "first"))
    out = out.assign(ruta="", km="", posicion=out["posicion"].map(posicion_legible),
                     observacion="aparece en el PDF " + out["documento"] + ": completar ruta y km")
    return out[["nombre", "concesion", "ruta", "km", "posicion", "observacion"]]


def integrar(carpeta, detalle, plazas_mop, mapa, alias, manuales, problemas):
    """Paso 4 y 5: tarifas de camión unidas a las plazas, tabla integrada y archivo de revisión."""
    det = detalle[~detalle["documento"].map(lambda x: bool(RE_NO_TARIFAS.search(normalizar(x))))].copy()
    for c in ("plaza_texto", "categoria_texto", "titulo_tabla", "tipo_cobro", "url", "sha256", "fecha_descarga", "vigencia_desde"):
        if c not in det.columns:
            det[c] = ""
    det["categoria"] = det["categoria_texto"].map(categoria_normalizada)
    det = corregir_horario(det)
    det["modalidad"] = det["plaza_texto"].map(detectar_modalidad)
    for col in ("categoria_texto", "titulo_tabla"):
        sin_mod = det["modalidad"] == ""
        det.loc[sin_mod, "modalidad"] = det.loc[sin_mod, col].map(detectar_modalidad)
    det["tipo_cobro"] = (det["plaza_texto"].fillna("") + " " + det["titulo_tabla"].fillna("")).map(detectar_tipo_cobro)
    # si un mismo documento (url) se descargó en varias versiones con la misma vigencia, vale la última
    det["_ultima"] = det.groupby(["url", "vigencia_desde"], dropna=False)["fecha_descarga"].transform("max")
    det = det[det["fecha_descarga"] == det["_ultima"]].drop(columns="_ultima")

    univ = preparar_plazas(plazas_mop, manuales)
    cam = det[det["categoria"].isin(["camion_mas_2_ejes", "camion_con_remolque"])].reset_index(drop=True)
    if not len(cam):
        print("No se encontraron tarifas de camión en los documentos extraídos.")
        guardar_csv(pd.DataFrame(problemas), carpeta / "revision.csv")
        return cam, pd.DataFrame()
    cam = asignar_plazas(cam, univ, mapa, alias)
    cam["tarifa_clp"] = pd.to_numeric(cam["tarifa_clp"], errors="coerce")
    # variación frente a la vigencia anterior de la misma tarifa
    grupo = cam["concesiones_doc"].where(cam["concesiones_doc"] != "", cam["documento"])
    clave = [grupo, cam["plaza"], cam["clave_plaza"], cam["categoria"], cam["horario"], cam["modalidad"], cam["tipo_cobro"]]
    cam = cam.assign(_g=pd.Series(list(zip(*clave)), index=cam.index).astype(str))
    # valor de cada grupo en cada vigencia (solo si es uno) y el de la vigencia anterior
    por_vig = cam.groupby(["_g", "vigencia_desde"])["tarifa_clp"].agg(lambda x: x.iloc[0] if x.nunique() == 1 else float("nan"))
    por_vig = por_vig.reset_index().sort_values(["_g", "vigencia_desde"])
    por_vig["tarifa_anterior"] = por_vig.groupby("_g")["tarifa_clp"].shift()
    cam = cam.merge(por_vig[["_g", "vigencia_desde", "tarifa_anterior"]], on=["_g", "vigencia_desde"], how="left").drop(columns="_g")
    cam["variacion_pct"] = (100 * (cam["tarifa_clp"] / cam["tarifa_anterior"] - 1)).round(1)
    cam["revisado"] = "no"
    # autopistas urbanas que no están en la capa: se cobran por pórtico y van en su propio archivo
    urb = cam["seccion"].fillna("").map(normalizar).str.contains("URBAN") & (cam["metodo_union"] == "documento sin concesión en la capa")
    porticos = cam[urb].rename(columns={"plaza_texto": "portico_o_tramo"})
    guardar_csv(porticos[[c for c in ["documento", "portico_o_tramo", "categoria", "categoria_texto", "horario", "modalidad", "tarifa_clp",
                                      "vigencia_desde", "vigencia_hasta", "confianza", "titulo_tabla", "pagina", "url", "fecha_descarga"]
                          if c in porticos.columns]].drop_duplicates(), carpeta / "porticos_urbanos.csv")
    integ = tabla_integrada(cam[~urb], univ)
    columnas = ["plaza", "plaza_mop", "metodo_union", "puntaje", "concesion_mop", "ruta", "km", "posicion_mop", "tipo_cobro",
                "categoria", "categoria_texto", "horario", "horario_fuente", "modalidad", "tarifa_clp", "vigencia_desde",
                "vigencia_hasta", "fuente_vigencia", "tarifa_anterior", "variacion_pct", "documento", "seccion", "concesiones_doc",
                "titulo_tabla", "plaza_texto", "pagina", "confianza", "url", "archivo", "sha256", "fecha_descarga",
                "fecha_extraccion", "clave_plaza", "revisado"]
    guardar_csv(cam[[c for c in columnas if c in cam.columns]], carpeta / "tarifas_camiones.csv")
    guardar_csv(integ, carpeta / "tabla_integrada.csv")
    cands = candidatas_manual(cam)
    guardar_csv(cands, carpeta / "plazas_candidatas_manual.csv")

    # revisión: una fila por documento y motivo, con ejemplos (el detalle está en tarifas_camiones y tabla_integrada)
    pesos = lambda v: "" if pd.isna(v) else f"{v:,.0f}".replace(",", ".")
    rev = []
    for _, r in cam[cam["confianza"].astype(str).str.split(":").str[0] != "alta"].iterrows():
        rev.append({"documento": r["documento"], "motivo": f"lectura del PDF con confianza {r['confianza']}",
                    "ejemplo": f"{str(r['plaza_texto'])[:40]} {pesos(r['tarifa_clp'])}", "url": r["url"]})
    for _, r in cam[cam["variacion_pct"].abs() > 100 * VARIACION_ALERTA].iterrows():
        rev.append({"documento": r["documento"], "motivo": f"variación mayor a {VARIACION_ALERTA:.0%} frente a la vigencia anterior",
                    "ejemplo": f"{r['plaza_mop'] or r['plaza']} {pesos(r['tarifa_anterior'])} -> {pesos(r['tarifa_clp'])} ({r['variacion_pct']:+}%)",
                    "url": r["url"]})
    for _, r in integ[integ["fuente_plaza"] == "solo PDF"].iterrows():
        rev.append({"documento": r["documento"], "motivo": r["metodo_asignacion"],
                    "ejemplo": f"{r['plaza_pdf'] or str(r['encabezado_pdf'])[:40]} {pesos(r['tarifa_clp'])}", "url": r["url"]})
    for _, r in integ[integ["observacion"].fillna("") != ""].iterrows():
        rev.append({"documento": r["documento"], "motivo": "plaza con valores que no calzan: " + r["observacion"].split(":")[0],
                    "ejemplo": f"{r['plaza_mop']}: {r['observacion'].split(': ', 1)[-1]}", "url": r["url"]})
    for _, r in integ[integ["metodo_asignacion"] == "sin tarifa encontrada"].iterrows():
        rev.append({"documento": r["concesion"], "motivo": "plaza de la capa sin tarifa (revisar la lámina o agregar un alias)",
                    "ejemplo": r["plaza_mop"], "url": ""})
    sin_camion = sorted(set(det["documento"]) - set(cam["documento"]))
    problemas = problemas + [{"documento": d, "url": "", "motivo": "no se detectó tarifa de camión de más de dos ejes"} for d in sin_camion]
    rev = pd.DataFrame(rev, columns=["documento", "motivo", "ejemplo", "url"])
    resumen_rev = (rev.groupby(["documento", "motivo"], as_index=False, sort=False)
                   .agg(filas=("ejemplo", "size"), ejemplos=("ejemplo", lambda x: " | ".join(list(dict.fromkeys(x))[:6])), url=("url", "first")))
    probs = pd.DataFrame(problemas, columns=["documento", "url", "motivo"]).assign(filas=1, ejemplos="")
    guardar_csv(pd.concat([probs, resumen_rev], ignore_index=True)[["documento", "motivo", "filas", "ejemplos", "url"]], carpeta / "revision.csv")
    rev_total = len(probs) + len(resumen_rev)

    resumen = integ[integ["fuente_plaza"] != "solo PDF"].groupby(["concesion", "plaza_mop", "posicion"])["metodo_asignacion"].first()
    print(f"Tarifas de camión: {len(cam)} filas en {cam['documento'].nunique()} documentos")
    print(f"Tabla integrada: {resumen.size} plazas")
    print(resumen.value_counts().to_string())
    print(f"Tarifas de pórticos urbanos (porticos_urbanos.csv): {int(urb.sum())} en {cam.loc[urb, 'documento'].nunique()} autopistas")
    print(f"Tarifas que solo están en el PDF: {(integ['fuente_plaza'] == 'solo PDF').sum()} | candidatas a plazas_manual: {len(cands)}"
          f" | puntos para revisar: {rev_total}")
    return cam, integ


# ----------------------------------------------------------------------------- principal

def archivo_manual(ruta, filas_iniciales, columnas, completar=False):
    """Crea el archivo con los valores iniciales si no existe y lo lee. Con completar=True agrega a un archivo existente
    las filas iniciales que le falten (filas nuevas de una versión posterior del programa)."""
    if not ruta.exists():
        guardar_csv(pd.DataFrame(filas_iniciales, columns=columnas), ruta)
    df = leer_csv(ruta)
    if completar and len(filas_iniciales):
        ini = pd.DataFrame(filas_iniciales, columns=columnas)
        llave = lambda d: d[columnas].astype(str).apply(lambda r: "|".join(texto_clave(x) for x in r), axis=1)
        faltan = ini[~llave(ini).isin(set(llave(df))) if len(df) else slice(None)]
        if len(faltan):
            df = pd.concat([df, faltan], ignore_index=True)
            guardar_csv(df, ruta)
            print(f"Se agregaron {len(faltan)} fila(s) nuevas a {ruta.name}")
    return df


VERSION = "2026-10-18"


def principal(argv=None):
    print(f"peajes_descarga.py versión {VERSION}")
    ap = argparse.ArgumentParser(description="Descarga tarifas de peaje para camiones (MOP / DGC).")
    ap.add_argument("--carpeta", default="peajes_datos", help="carpeta de salida")
    ap.add_argument("--anio", type=int, default=None, help="solo documentos de este año (por defecto, el de la página)")
    ap.add_argument("--solo-extraer", action="store_true", help="no descarga nada; extrae desde los PDF ya descargados")
    ap.add_argument("--reextraer", action="store_true", help="extrae todos los PDF, aunque no hayan cambiado")
    ap.add_argument("--solo-integrar", action="store_true", help="no descarga ni lee PDF; rehace la unión con las plazas")
    ap.add_argument("--sin-plazas", action="store_true", help="no descarga la capa de plazas del MOP")
    args = ap.parse_args(argv)

    carpeta = Path(args.carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    registro_previo = leer_csv(carpeta / "documentos.csv")
    mapa = archivo_manual(carpeta / "concesiones_documentos.csv", CONCESIONES_INICIALES, ["documento_contiene", "concesion_mop_contiene"])
    mapa = [tuple(x) for x in mapa[["documento_contiene", "concesion_mop_contiene"]].values]
    alias = archivo_manual(carpeta / "alias_plazas.csv", ALIAS_INICIALES, ["documento_contiene", "encabezado_contiene", "plaza_mop"],
                           completar=True)
    alias = [tuple(x) for x in alias[["documento_contiene", "encabezado_contiene", "plaza_mop"]].values]
    manuales = archivo_manual(carpeta / "plazas_manual.csv", PLAZAS_MANUALES_INICIALES,
                              ["nombre", "concesion", "ruta", "km", "posicion", "observacion"])
    plantillas = archivo_manual(carpeta / "encabezados_manual.csv", PLANTILLAS_INICIALES,
                                ["documento_contiene", "tabla", "eje", "numero", "texto"]).to_dict("records")
    problemas = []

    if args.solo_integrar:
        detalle, plazas = leer_csv(carpeta / "tarifas_detalle.csv"), leer_csv(carpeta / "plazas_mop.csv")
        if not len(detalle):
            print("No hay tarifas extraídas todavía (falta tarifas_detalle.csv).")
            return 1
        integrar(carpeta, detalle, plazas, mapa, alias, manuales, problemas)
        print(f"Archivos en {carpeta.resolve()}")
        return 0

    # 1-2. descarga
    if args.solo_extraer:
        if not len(registro_previo):
            print("No hay documentos descargados todavía.")
            return 1
        docs = registro_previo[registro_previo["sha256"] != ""].groupby("url").tail(1).copy()
        # las láminas de años anteriores las carga laminas_historicas.py con su vigencia ya resuelta: no se re-extraen aquí
        docs = docs[~docs["estado"].astype(str).str.startswith("histórico")]
        docs["estado"] = "sin cambios"
        plazas = leer_csv(carpeta / "plazas_mop.csv")
    else:
        sesion = nueva_sesion()
        plazas = None
        if not args.sin_plazas:
            try:
                plazas = descargar_plazas(sesion, carpeta, fecha)
                print(f"Plazas del MOP: {len(plazas)}")
            except Exception as e:
                print(f"AVISO: no se pudo descargar la capa de plazas ({e}); se usa la última guardada.")
        if plazas is None:
            plazas = leer_csv(carpeta / "plazas_mop.csv")
        anio_pagina, lista = listar_documentos(pedir(sesion, URL_DGC).text)
        anio = args.anio or anio_pagina
        if anio:
            lista = [d for d in lista if d["anio"] in (anio, None)]
        print(f"Página de la DGC: año {anio_pagina}, {len(lista)} documentos PDF" + (f" del año {anio}" if anio else ""))
        if not lista:
            print("ERROR: la página no entregó documentos. Puede haber cambiado su diseño: revisar a mano.")
            return 1
        docs = descargar_documentos(sesion, lista, carpeta, fecha, registro_previo)
        guardar_csv(pd.concat([registro_previo, docs.astype(str)], ignore_index=True), carpeta / "documentos.csv")
        print(docs["estado"].str.split(":").str[0].value_counts().to_string())

    # 3. extracción
    docs = docs[~docs["documento"].map(lambda x: bool(RE_NO_TARIFAS.search(normalizar(x))))]
    detalle_previo = leer_csv(carpeta / "tarifas_detalle.csv")
    por_extraer = docs if (args.reextraer or args.solo_extraer) else docs[docs["estado"].isin(["nuevo", "actualizado"])]
    clave_doc = lambda df: df["url"].astype(str) + "|" + df["sha256"].astype(str)      # mismo PDF en la misma dirección
    if not args.reextraer and not args.solo_extraer and len(detalle_previo):
        ya = set(clave_doc(detalle_previo))                      # extraídos en ejecuciones anteriores
        por_extraer = pd.concat([por_extraer, docs[(docs["estado"] == "sin cambios") & ~clave_doc(docs).isin(ya)]])
    nuevas = []
    for _, d in por_extraer.iterrows():
        if not d.get("archivo") or not Path(d["archivo"]).exists():
            continue
        try:
            filas, texto = extraer_tarifas_pdf(d["archivo"], d["documento"], d.get("seccion", ""), plantillas)
        except Exception as e:
            problemas.append({"documento": d["documento"], "url": d["url"], "motivo": f"no se pudo leer el PDF: {e}"})
            continue
        anio_doc, mes_doc = entero(d.get("anio")), entero(d.get("mes_publicacion"))
        desde, hasta, fuente_vig = extraer_vigencia(texto, anio_doc, mes_doc)
        if not texto.strip():
            problemas.append({"documento": d["documento"], "url": d["url"], "motivo": "el PDF no tiene texto (es una imagen): cargar a mano"})
        elif not filas:
            problemas.append({"documento": d["documento"], "url": d["url"], "motivo": "no se encontraron montos"})
        for f in filas:
            f.update(documento=d["documento"], seccion=d.get("seccion", ""), anio=anio_doc, vigencia_desde=desde,
                     vigencia_hasta=hasta, fuente_vigencia=fuente_vig, url=d["url"], archivo=d["archivo"], sha256=d["sha256"],
                     fecha_descarga=d["fecha_descarga"], fecha_extraccion=fecha)
            f["categoria"] = categoria_normalizada(f["categoria_texto"])
            nuevas.append(f)
    nuevas = pd.DataFrame(nuevas)
    if len(nuevas):
        detalle = pd.concat([detalle_previo[~clave_doc(detalle_previo).isin(set(clave_doc(nuevas)))] if len(detalle_previo) else detalle_previo,
                             nuevas.astype(str)], ignore_index=True)
    else:
        detalle = detalle_previo
    if not len(detalle):
        print("No hay tarifas extraídas.")
        guardar_csv(pd.DataFrame(problemas), carpeta / "revision.csv")
        return 0
    guardar_csv(detalle, carpeta / "tarifas_detalle.csv")

    # 4-5. unión con las plazas, tabla integrada y revisión
    integrar(carpeta, detalle, plazas, mapa, alias, manuales, problemas)
    print(f"Archivos en {carpeta.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
