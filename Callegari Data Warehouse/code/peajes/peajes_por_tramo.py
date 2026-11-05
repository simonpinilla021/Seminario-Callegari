#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Peaje por tramo — Seminario BI Transportes Callegari.

Une la tabla de tramos (sit_stretch) con las plazas de peaje:
  1. ubica cada lugar de los tramos (coordenadas),
  2. traza el recorrido por carretera de cada par de lugares consecutivos,
  3. detecta qué plazas de peaje quedan sobre ese recorrido,
  4. suma sus tarifas (tractocamión con rampla) para obtener el peaje de cada tramo.

Entradas (salidas de los dos programas anteriores):
  tramos_ciudades/tramos_desglosados.csv   (ciudades_tramos.py)
  peajes_datos/tabla_integrada.csv         (peajes_descarga.py)
  peajes_datos/plazas_mop.csv              (todos los puntos de la capa del MOP)
  peajes_datos/plazas_manual.csv           (plazas agregadas a mano; columnas lat y lon opcionales)

Servicios gratuitos que consulta (solo la primera vez: las respuestas quedan en tramos_peajes/cache):
  Nominatim (OpenStreetMap)  nombre de lugar -> coordenadas             1 consulta por segundo
  Overpass  (OpenStreetMap)  casetas y pórticos de peaje de todo Chile   1 consulta
  OSRM                       recorrido por carretera entre dos puntos    1 consulta por segundo
La primera ejecución tarda unos 15 a 20 minutos (unos 160 lugares y unos 550 pares); las siguientes, segundos.

Salidas en tramos_peajes/:
  lugares.csv               cada lugar de los tramos: coordenadas y de dónde salieron
  plazas_ubicacion.csv      cada plaza de tabla_integrada: con qué coordenadas se la detecta
  pares_ruta.csv            cada par de lugares consecutivos: km por carretera y plazas que cruza
  puente_tramo_plaza.csv    tabla puente tramo–plaza: una fila por plaza cruzada en cada tramo, en orden
  peaje_por_tramo.csv       peaje de cada tramo a la fecha de referencia
  plazas_sugeridas_osm.csv  casetas de OpenStreetMap que están en los recorridos pero no en tabla_integrada
  revisar_peajes.csv        lo que conviene mirar
Archivo manual (se crea la primera vez y nunca se sobrescribe):
  lugares_manual.csv        coordenadas puestas a mano; mandan sobre las de Nominatim (p. ej. la base de Coquimbo)

Uso (en la carpeta que contiene tramos_ciudades/ y peajes_datos/):
  python peajes_por_tramo.py
  python peajes_por_tramo.py --tramos otra/tramos_desglosados.csv --peajes otra/peajes_datos
  python peajes_por_tramo.py --fecha 2026-03-15            # tarifas vigentes en esa fecha
  python peajes_por_tramo.py --horario punta               # en plazas con horario, usar la tarifa punta
  python peajes_por_tramo.py --sin-internet                # solo lo que ya está en cache

Requisitos: pandas y numpy (los mismos de siempre). No necesita librerías extra.
"""
import argparse
import gzip
import hashlib
import http.client
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

TRAMOS = "tramos_ciudades/tramos_desglosados.csv"
PEAJES = "peajes_datos"
SALIDA = "tramos_peajes"

AGENTE = "seminario-ucn-peajes/1.0 (proyecto academico, consultas ocasionales)"
URL_NOMINATIM = "https://nominatim.openstreetmap.org/search"
URL_OVERPASS = "https://overpass-api.de/api/interpreter"
URL_OSRM = "https://router.project-osrm.org/route/v1/driving"
PAUSA_S = 1.1                      # los servicios públicos piden como máximo una consulta por segundo

UMBRAL_OSM_M = 3                   # caseta o pórtico de OSM dibujado sobre la calzada: la ruta pasa "por encima" (el
                                   # nodo es un vértice del recorrido; a 10-20 m ya es la otra calzada o una rampa)
UMBRAL_OSM_AREA_M = 25             # caseta dibujada como área (se usa su centro)
UMBRAL_MOP_M = {"TRONCAL": 150, "LATERAL": 60, "PORTICO": 60, "": 100}   # plazas sin caseta en OSM
RADIO_ASIGNACION_M = 1500          # una caseta de OSM se asigna a la plaza más cercana dentro de este radio
RADIO_NOMBRE_M = 12000             # ... o a una plaza de igual nombre dentro de este radio (p. ej. "Peaje Angostura")
TOL_LOCALIDAD_KM = 35              # Nominatim debe devolver la localidad cerca de su ubicación esperada (REFERENCIA)
DIST_MAX_SUBLUGAR_KM = 80          # un sublugar ("TIERRA AMARILLA (LOS LOROS)") debe quedar cerca de su localidad
MARGEN_LATERAL_KM = 10             # una lateral sin caseta OSM solo se cobra si está al inicio o al final del recorrido
RADIO_FREE_FLOW_KM = 5             # pórtico OSM sin nombre junto a una troncal detectada por el punto MOP = la misma plaza
DIF_KM_ALERTA = 0.25               # diferencia entre km del ERP y km por carretera que se marca para revisar
# Concesiones que cobran por recorrido (origen-destino): si la ruta cruza varias de sus plazas se cobra una sola vez.
COBRO_POR_RECORRIDO = ("INTERPORTUARIA",)
VERSION = "2026-10-18"

REGIONES = {  # código: (texto para buscar, palabra que debe aparecer en la región que devuelve Nominatim)
    "AP": ("Arica y Parinacota", "ARICA"), "TA": ("Tarapacá", "TARAPACA"), "AN": ("Antofagasta", "ANTOFAGASTA"),
    "AT": ("Atacama", "ATACAMA"), "CO": ("Coquimbo", "COQUIMBO"), "VA": ("Valparaíso", "VALPARAISO"),
    "RM": ("Región Metropolitana", "METROPOLITANA"), "LI": ("O'Higgins", "HIGGINS"), "ML": ("Maule", "MAULE"),
    "NB": ("Ñuble", "NUBLE"), "BI": ("Biobío", "BIOBIO"), "AR": ("Araucanía", "ARAUCANIA"),
    "LR": ("Los Ríos", "LOS RIOS"), "LL": ("Los Lagos", "LOS LAGOS"),
}

# Localidad (como viene en los tramos, normalizada) -> (nombre oficial para buscar | alternativas, región).
LOCALIDADES = {
    "SANTIAGO": ("Santiago", "RM"), "COQUIMBO": ("Coquimbo", "CO"), "ANTOFAGASTA": ("Antofagasta", "AN"),
    "COPIAPO": ("Copiapó", "AT"), "LA SERENA": ("La Serena", "CO"), "CALAMA": ("Calama", "AN"),
    "OVALLE": ("Ovalle", "CO"), "ARICA": ("Arica", "AP"), "VALLENAR": ("Vallenar", "AT"),
    "VALPARAISO": ("Valparaíso", "VA"), "CALDERA": ("Caldera", "AT"), "IQUIQUE": ("Iquique", "TA"),
    "SAN ANTONIO": ("San Antonio", "VA"), "SAN FELIPE": ("San Felipe", "VA"), "HUASCO": ("Huasco", "AT"),
    "TIERRA AMARILLA": ("Tierra Amarilla", "AT"), "PUCHUNCAVI": ("Puchuncaví", "VA"), "RANCAGUA": ("Rancagua", "LI"),
    "TALCA": ("Talca", "ML"), "CHILLAN": ("Chillán", "NB"), "LOS ANDES": ("Los Andes", "VA"),
    "TOCOPILLA": ("Tocopilla", "AN"), "ALTO DEL CARMEN": ("Alto del Carmen", "AT"), "TEMUCO": ("Temuco", "AR"),
    "VICUNA": ("Vicuña", "CO"), "MONTE PATRIA": ("Monte Patria", "CO"), "TILTIL": ("Tiltil|Til Til", "RM"),
    "LA HIGUERA": ("La Higuera", "CO"), "IGLESIA COLORADA": ("Iglesia Colorada", "AT"),
    "CONCEPCION": ("Concepción", "BI"), "CODEGUA": ("Codegua", "LI"), "COMBARBALA": ("Combarbalá", "CO"),
    "SALAMANCA": ("Salamanca", "CO"), "ILLAPEL": ("Illapel", "CO"), "LOS ANGELES": ("Los Ángeles", "BI"),
    "TALCAHUANO": ("Talcahuano", "BI"), "VINA DEL MAR": ("Viña del Mar", "VA"), "CHANARAL": ("Chañaral", "AT"),
    "CURICO": ("Curicó", "ML"), "PUDAHUEL": ("Pudahuel", "RM"), "ALTO HOSPICIO": ("Alto Hospicio", "TA"),
    "BUIN": ("Buin", "RM"), "HIJUELAS": ("Hijuelas", "VA"), "RENGO": ("Rengo", "LI"),
    "TALAGANTE": ("Talagante", "RM"), "CALERA": ("La Calera", "VA"), "TALTAL": ("Taltal", "AN"),
    "LOS VILOS": ("Los Vilos", "CO"), "PUERTO MONTT": ("Puerto Montt", "LL"), "MACHALI": ("Machalí", "LI"),
    "REQUINOA": ("Requínoa", "LI"), "QUILICURA": ("Quilicura", "RM"), "QUILLOTA": ("Quillota", "VA"),
    "PAINE": ("Paine", "RM"), "CABILDO": ("Cabildo", "VA"), "SAN PEDRO DE": ("San Pedro de Atacama", "AN"),
    "SAN PEDRO DE ATACAMA": ("San Pedro de Atacama", "AN"), "MALLOA": ("Malloa", "LI"), "HUARA": ("Huara", "TA"),
    "HUECHURABA": ("Huechuraba", "RM"), "SAN VICENTE": ("San Vicente de Tagua Tagua|San Vicente", "LI"),
    "OSORNO": ("Osorno", "LL"), "MELIPILLA": ("Melipilla", "RM"), "NOGALES": ("Nogales", "VA"),
    "LLAILLAY": ("Llaillay|Llay-Llay", "VA"), "LINARES": ("Linares", "ML"), "CATAPILCO": ("Catapilco", "VA"),
    "CAMARONES": ("Camarones", "AP"), "LAUTARO": ("Lautaro", "AR"), "LAS CONDES": ("Las Condes", "RM"),
    "LIMACHE": ("Limache", "VA"), "DIEGO DE ALMAGRO": ("Diego de Almagro", "AT"), "CURACAVI": ("Curacaví", "RM"),
    "CORONEL": ("Coronel", "BI"), "ISLA DE MAIPO": ("Isla de Maipo", "RM"), "MAIPU": ("Maipú", "RM"),
    "MEJILLONES": ("Mejillones", "AN"), "LO BARNECHEA": ("Lo Barnechea", "RM"), "PAIHUANO": ("Paihuano|Paiguano", "CO"),
    "PENAFLOR": ("Peñaflor", "RM"), "NUNOA": ("Ñuñoa", "RM"), "PUENTE ALTO": ("Puente Alto", "RM"),
    "QUINTERO": ("Quintero", "VA"), "PEDRO AGUIRRE": ("Pedro Aguirre Cerda", "RM"),
    "PEDRO AGUIRRE CERDA": ("Pedro Aguirre Cerda", "RM"), "PARRAL": ("Parral", "ML"), "TENO": ("Teno", "ML"),
    "VALDIVIA": ("Valdivia", "LR"), "CALERA DE TANGO": ("Calera de Tango", "RM"),
}
# Ubicación aproximada esperada de cada localidad (lat, lon). Sirve para descartar homónimos de Nominatim
# (hay varios "San Antonio" y "La Higuera" en Chile) y como respaldo si Nominatim no la encuentra.
REFERENCIA = {
    "SANTIAGO": (-33.44, -70.65), "COQUIMBO": (-29.95, -71.34), "ANTOFAGASTA": (-23.65, -70.40), "COPIAPO": (-27.37, -70.33),
    "LA SERENA": (-29.90, -71.25), "CALAMA": (-22.46, -68.93), "OVALLE": (-30.60, -71.20), "ARICA": (-18.48, -70.31),
    "VALLENAR": (-28.58, -70.76), "VALPARAISO": (-33.05, -71.62), "CALDERA": (-27.07, -70.82), "IQUIQUE": (-20.21, -70.15),
    "SAN ANTONIO": (-33.59, -71.61), "SAN FELIPE": (-32.75, -70.73), "HUASCO": (-28.47, -71.22),
    "TIERRA AMARILLA": (-27.48, -70.26), "PUCHUNCAVI": (-32.72, -71.41), "RANCAGUA": (-34.17, -70.74),
    "TALCA": (-35.43, -71.66), "CHILLAN": (-36.61, -72.10), "LOS ANDES": (-32.83, -70.60), "TOCOPILLA": (-22.09, -70.20),
    "ALTO DEL CARMEN": (-28.76, -70.49), "TEMUCO": (-38.74, -72.60), "VICUNA": (-30.03, -70.71),
    "MONTE PATRIA": (-30.69, -70.96), "TILTIL": (-33.08, -70.93), "LA HIGUERA": (-29.51, -71.26),
    "IGLESIA COLORADA": (-28.16, -69.88), "CONCEPCION": (-36.83, -73.05), "CODEGUA": (-34.03, -70.67),
    "COMBARBALA": (-31.18, -71.00), "SALAMANCA": (-31.78, -70.97), "ILLAPEL": (-31.63, -71.17),
    "LOS ANGELES": (-37.47, -72.35), "TALCAHUANO": (-36.72, -73.12), "VINA DEL MAR": (-33.02, -71.55),
    "CHANARAL": (-26.35, -70.62), "CURICO": (-34.98, -71.24), "PUDAHUEL": (-33.44, -70.76), "ALTO HOSPICIO": (-20.27, -70.10),
    "BUIN": (-33.73, -70.74), "HIJUELAS": (-32.80, -71.15), "RENGO": (-34.41, -70.86), "TALAGANTE": (-33.66, -70.93),
    "CALERA": (-32.79, -71.20), "TALTAL": (-25.40, -70.48), "LOS VILOS": (-31.91, -71.51), "PUERTO MONTT": (-41.47, -72.94),
    "MACHALI": (-34.18, -70.65), "REQUINOA": (-34.28, -70.82), "QUILICURA": (-33.36, -70.73), "QUILLOTA": (-32.88, -71.25),
    "PAINE": (-33.81, -70.74), "CABILDO": (-32.43, -71.07), "SAN PEDRO DE": (-22.91, -68.20),
    "SAN PEDRO DE ATACAMA": (-22.91, -68.20), "MALLOA": (-34.45, -70.95), "HUARA": (-19.99, -69.77),
    "HUECHURABA": (-33.37, -70.64), "SAN VICENTE": (-34.44, -71.08), "OSORNO": (-40.57, -73.13), "MELIPILLA": (-33.69, -71.21),
    "NOGALES": (-32.73, -71.20), "LLAILLAY": (-32.84, -70.96), "LINARES": (-35.85, -71.59), "CATAPILCO": (-32.57, -71.29),
    "CAMARONES": (-19.10, -70.00), "LAUTARO": (-38.53, -72.43), "LAS CONDES": (-33.41, -70.57), "LIMACHE": (-33.00, -71.27),
    "DIEGO DE ALMAGRO": (-26.39, -70.05), "CURACAVI": (-33.40, -71.13), "CORONEL": (-37.03, -73.15),
    "ISLA DE MAIPO": (-33.75, -70.90), "MAIPU": (-33.51, -70.76), "MEJILLONES": (-23.10, -70.45),
    "LO BARNECHEA": (-33.35, -70.52), "PAIHUANO": (-30.03, -70.52), "PENAFLOR": (-33.61, -70.88), "NUNOA": (-33.46, -70.60),
    "PUENTE ALTO": (-33.61, -70.58), "QUINTERO": (-32.78, -71.53), "PEDRO AGUIRRE": (-33.49, -70.67),
    "PEDRO AGUIRRE CERDA": (-33.49, -70.67), "PARRAL": (-36.14, -71.83), "TENO": (-34.87, -71.16),
    "VALDIVIA": (-39.81, -73.25), "CALERA DE TANGO": (-33.63, -70.78),
}
# Código de un pórtico urbano al inicio o al final del nombre: "PA15 14 de la Fama...", "Ruta 68 - Los Mares P2".
RE_CODIGO_PORTICO = re.compile(r"^\s*(P[A-Z]?\d+(?:[.,]\d+)?)\b|\b(P[A-Z]?\d+(?:[.,]\d+)?)\s*$")
# Cómo buscar algunos sublugares (lo que va entre paréntesis).
SUBLUGAR_BUSQUEDA = {
    "PTO VENTANAS": "Ventanas", "CABO LEON DOMEYCO": "Domeyko", "FARFANA": "La Farfana", "PLAZA EGANA": "Plaza Egaña",
    "CHANARAL ALTO": "Chañaral Alto", "SOTAQUI": "Sotaquí", "PAN DE AZUCAR": "Pan de Azúcar", "EL PENON": "El Peñón",
    "EL TRANSITO": "El Tránsito", "PELEQUEN": "Pelequén", "EL MELON": "El Melón", "LO VALLEDOR NORTE": "Lo Valledor",
    "AV EL SANTO": "Avenida El Santo", "VILLA MARIA": "Villa María", "INCAGUASI": "Incahuasi|Incaguasi",
    "PUERTO COQUIMBO": "Puerto de Coquimbo",
}
# Sublugares propios de la empresa o de un cliente: no se buscan (se usa la localidad); si importan, van a lugares_manual.csv.
SUBLUGAR_EMPRESA = {"BASE", "BASE TCALLEGARI", "RET VEGETALES", "RET CELTA", "KIMUN", "CENTRO EMPRESARIAL", "CUGAT",
                    "KENNEDY II", "CANTERA SUBSOLE", "MAITENES"}
# Lo que va entre paréntesis y no es un lugar sino un concepto de cobro.
CONCEPTOS = {"TRONCAL", "EXTRA", "CARGA RECHAZADA", "SOBRESTADIA DIARIA", "PUNTO ADICIONAL", "LOCAL"}
# Partes de tramo que no son lugares.
NO_LUGARES = {"SOLO", "DINERO", "DIFERENCIA", "SANTA CATERINA"}

TIPOS_LUGAR = {"city", "town", "village", "hamlet", "suburb", "neighbourhood", "quarter", "locality",
               "isolated_dwelling", "borough", "municipality", "farm", "square"}
TIPOS_RECHAZADOS = {"state", "region", "province", "country", "county", "state_district", "continent"}

CONSULTA_OVERPASS = """[out:json][timeout:180];
area["ISO3166-1"="CL"][admin_level=2]->.cl;
(
  node["barrier"="toll_booth"](area.cl);
  node["highway"="toll_gantry"](area.cl);
  way["barrier"="toll_booth"](area.cl);
);
out center tags;"""

MANUAL_INICIAL = [
    ("COQUIMBO BASE", "COQUIMBO (BASE)", "coordenadas de la base de Coquimbo (Google Maps: clic derecho sobre el lugar y "
                                         "copiar las coordenadas; lat es el primer número, lon el segundo)"),
    ("COQUIMBO BASE TCALLEGARI", "COQUIMBO (BASE TCALLEGARI)", "la misma base de Coquimbo"),
    ("LA SERENA BASE", "LA SERENA (BASE)", "¿existe una base en La Serena o es la misma de Coquimbo?"),
    ("SANTIAGO", "SANTIAGO", "si en vacío se deja el centro de Santiago; si la mayoría de los viajes a Santiago van a una "
                             "misma zona (p. ej. Quilicura, Pudahuel, Lo Espejo), poner aquí ese punto"),
]


# --------------------------------------------------------------------------------------------- utilidades de texto
def sin_tildes(texto):
    return unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()


def clave(texto):
    """Forma normalizada (la misma de ciudades_tramos.py): 'Copiapó (Los Loros)' -> 'COPIAPO LOS LOROS'."""
    t = re.sub(r"[^A-Z0-9 ]", " ", sin_tildes(texto).upper())
    return re.sub(r"\s+", " ", t).strip()


def contiene_frase(frase, texto):
    """¿La frase aparece en el texto como palabras completas? (ambos ya normalizados con clave())."""
    return bool(frase) and f" {frase} " in f" {texto} "


def frase_plaza(nombre):
    """Parte distintiva del nombre de una plaza: 'Paso superior Lo Marcoleta' -> 'LO MARCOLETA'."""
    k = clave(nombre)
    k = re.sub(r"^(PLAZA DE PEAJE|PEAJE|PLAZA|PORTICO|PASO SUPERIOR|ENLACE|CASETA)\s+", "", k)
    k = re.sub(r"\s+(TRONCAL|LATERAL)$", "", k)
    return "" if k in ("SIN NOMBRE", "") else k


def codigo_portico(nombre):
    """'P05 Vivaceta - Carrascal' -> ('P5', 'VIVACETA CARRASCAL'); sin código al inicio o al final -> ('', '')."""
    t = sin_tildes(nombre).upper()
    t = re.sub(r"\(.*?\)", " ", t).strip()                       # "(sentido poniente-oriente)", "(Longitud 5,46 Kms)"
    t = re.sub(r"\b(P[A-Z]?)\s+(\d)", r"\1\2", t)                 # "PA 32" -> "PA32"
    m = RE_CODIGO_PORTICO.search(t)
    if not m:
        return "", ""
    cod = (m.group(1) or m.group(2)).replace(",", ".")
    cod = re.sub(r"^(P[A-Z]?)0+(\d)", r"\1\2", cod)              # P05 -> P5, P00 -> P0
    return cod, clave(t[:m.start()] + " " + t[m.end():])


def similitud(a, b):
    """Parecido entre dos nombres: el mayor entre la comparación de texto y la de conjuntos de palabras."""
    pa, pb = set(a.split()), set(b.split())
    jac = len(pa & pb) / len(pa | pb) if pa | pb else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), jac)


def nucleo_osm(nombre):
    """'Peaje Chillán' -> 'CHILLAN' (nombre de una caseta OSM sin las palabras genéricas)."""
    k = clave(nombre)
    k = re.sub(r"^(PLAZA DE PEAJE|PEAJE|PLAZA|PORTICO|CASETA)\s+", "", k)
    return re.sub(r"\s+(TRONCAL|LATERAL)$", "", k)


def pesos(valor):
    return "$" + f"{int(round(valor)):,}".replace(",", ".")


def limpiar_id(valor):
    s = str(valor).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def a_numero(valor):
    try:
        v = float(str(valor).replace(",", ".")) if str(valor).strip() not in ("", "nan", "None") else math.nan
    except ValueError:
        v = math.nan
    return v


# --------------------------------------------------------------------------------------------- distancias
R_TIERRA_M = 6371008.8


def dist_m(lat1, lon1, lat2, lon2):
    """Distancia en metros sobre la esfera (acepta arreglos)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2
    return 2 * R_TIERRA_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def decodificar_polilinea(texto, precision=6):
    """Formato 'polyline' de Google/OSRM -> lista de (lat, lon)."""
    coords, i, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    n = len(texto)
    while i < n:
        valores = []
        for _ in range(2):
            resultado, corrimiento = 0, 0
            while True:
                b = ord(texto[i]) - 63
                i += 1
                resultado |= (b & 0x1F) << corrimiento
                corrimiento += 5
                if b < 0x20:
                    break
            valores.append(~(resultado >> 1) if resultado & 1 else resultado >> 1)
        lat += valores[0]
        lon += valores[1]
        coords.append((lat / factor, lon / factor))
    return coords


class Ruta:
    """Recorrido por carretera; sabe medir la distancia de un punto al recorrido y en qué km del recorrido queda."""
    CELDA = 0.01  # grados (~1 km)

    def __init__(self, coords):
        c = np.asarray(coords, dtype=float).reshape(-1, 2)
        self.lat, self.lon = c[:, 0], c[:, 1]
        self.n = len(c)
        if self.n < 2:
            self.acum = np.zeros(self.n)
            self.celdas = set()
            return
        la, lb, oa, ob = self.lat[:-1], self.lat[1:], self.lon[:-1], self.lon[1:]
        largo = dist_m(la, oa, lb, ob)
        self.acum = np.concatenate([[0.0], np.cumsum(largo)])
        self.la, self.lb, self.oa, self.ob = la, lb, oa, ob
        self.min_lat, self.max_lat = np.minimum(la, lb), np.maximum(la, lb)
        self.min_lon, self.max_lon = np.minimum(oa, ob), np.maximum(oa, ob)
        # celdas de ~1 km que toca el recorrido (con puntos intermedios cada 400 m en tramos largos)
        partes = np.maximum(1, np.ceil(largo / 400.0)).astype(int)
        seg = np.repeat(np.arange(len(partes)), partes)
        k = np.arange(partes.sum()) - np.repeat(np.cumsum(partes) - partes, partes)
        f = k / partes[seg]
        plat = np.concatenate([la[seg] + (lb - la)[seg] * f, [lb[-1]]])
        plon = np.concatenate([oa[seg] + (ob - oa)[seg] * f, [ob[-1]]])
        self.celdas = set(zip(np.floor(plat / self.CELDA).astype(int).tolist(), np.floor(plon / self.CELDA).astype(int).tolist()))

    @property
    def largo_m(self):
        return float(self.acum[-1]) if self.n else 0.0

    def cerca(self, lat, lon):
        """Filtro rápido: ¿el punto está a menos de ~1 km del recorrido?"""
        cy, cx = math.floor(lat / self.CELDA), math.floor(lon / self.CELDA)
        return any((cy + dy, cx + dx) in self.celdas for dy in (-1, 0, 1) for dx in (-1, 0, 1))

    def distancia(self, lat, lon, margen=0.006):
        """(distancia en m del punto al recorrido, m recorridos desde el inicio hasta la proyección del punto)."""
        if self.n < 2:
            return math.inf, None
        ml = margen / max(0.2, math.cos(math.radians(lat)))
        m = (self.min_lat <= lat + margen) & (self.max_lat >= lat - margen) & (self.min_lon <= lon + ml) & (self.max_lon >= lon - ml)
        idx = np.nonzero(m)[0]
        if not len(idx):
            return math.inf, None
        kx, ky = math.cos(math.radians(lat)) * 111320.0, 110574.0
        ax, ay = (self.oa[idx] - lon) * kx, (self.la[idx] - lat) * ky
        dx, dy = (self.ob[idx] - lon) * kx - ax, (self.lb[idx] - lat) * ky - ay
        l2 = dx * dx + dy * dy
        t = np.clip(np.where(l2 > 0, -(ax * dx + ay * dy) / np.where(l2 > 0, l2, 1.0), 0.0), 0.0, 1.0)
        d = np.hypot(ax + t * dx, ay + t * dy)
        j = int(np.argmin(d))
        i = idx[j]
        return float(d[j]), float(self.acum[i] + t[j] * (self.acum[i + 1] - self.acum[i]))


# --------------------------------------------------------------------------------------------- red
class Red:
    """Consultas HTTP con pausa entre llamadas, reintentos y corte si un servicio no responde."""

    def __init__(self, internet=True):
        self.internet = internet
        self.ultimo = {}
        self.fallas = {}
        self.caidos = set()

    def pedir(self, url, datos=None, timeout=60):
        host = urllib.parse.urlparse(url).netloc
        if not self.internet or host in self.caidos:
            return None
        espera = PAUSA_S - (time.time() - self.ultimo.get(host, 0))
        if espera > 0:
            time.sleep(espera)
        for intento in range(4):
            try:
                req = urllib.request.Request(url, data=datos, headers={"User-Agent": AGENTE, "Accept": "application/json",
                                                                       "Accept-Language": "es"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    texto = r.read().decode("utf-8")
                self.ultimo[host] = time.time()
                self.fallas[host] = 0
                return json.loads(texto)
            except urllib.error.HTTPError as e:
                self.ultimo[host] = time.time()
                cuerpo = e.read().decode("utf-8", "ignore")
                if e.code == 400:                       # OSRM responde 400 cuando no hay ruta: es una respuesta válida
                    try:
                        return json.loads(cuerpo)
                    except ValueError:
                        return {"code": "Error400", "message": cuerpo[:200]}
                if e.code in (429, 500, 502, 503, 504) and intento < 3:
                    time.sleep(10 * (intento + 1))
                    continue
                print(f"  ! {host} respondió {e.code}: {cuerpo[:150]}")
                break
            except (OSError, ValueError, http.client.HTTPException) as e:   # sin conexión, tiempo agotado o respuesta no JSON
                self.ultimo[host] = time.time()
                if intento < 3:
                    time.sleep(5 * (intento + 1))
                    continue
                print(f"  ! sin respuesta válida de {host}: {e}")
        self.fallas[host] = self.fallas.get(host, 0) + 1
        if self.fallas[host] >= 3:
            print(f"  ! {host} falló 3 veces seguidas: no se le consulta más en esta ejecución")
            self.caidos.add(host)
        return None


# --------------------------------------------------------------------------------------------- 1. lugares
def interpretar_lugar(texto):
    """'TIERRA AMARILLA (LOS LOROS)' -> localidad TIERRA AMARILLA, sublugar LOS LOROS; 'CALAMA (EXTRA)' -> concepto EXTRA."""
    t = sin_tildes(texto).upper().strip()
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", t)
    base, dentro = (m.group(1), m.group(2)) if m else (t, "")
    base, dentro = clave(base), clave(dentro)
    notas = []
    sin_numero = re.sub(r"(\s+\d+)+$", "", base)
    if sin_numero != base:
        notas.append(f"se quitó el número '{base[len(sin_numero):].strip()}' del nombre")
        base = sin_numero
    concepto, sublugar = "", dentro
    if dentro in CONCEPTOS:
        concepto, sublugar = dentro, ""
    elif dentro.startswith("LOCAL "):
        concepto, sublugar = "LOCAL", dentro[6:]
    es_lugar = base not in NO_LUGARES and clave(texto) not in NO_LUGARES and bool(base)
    nombre, region = LOCALIDADES.get(base, ("", ""))
    if es_lugar and not nombre:
        notas.append("localidad no reconocida: se busca tal cual en todo Chile")
        nombre = base.title()
    if concepto:
        notas.append(f"'{concepto}' es un concepto de cobro, no un lugar: se usa la localidad")
    return {"clave": clave(texto), "texto": texto, "localidad": nombre.split("|")[0] if nombre else "", "region": region,
            "sublugar": sublugar, "concepto": concepto, "es_lugar": es_lugar, "_nombres": nombre, "_notas": notas,
            "_base": base}


def candidatos_nominatim(consulta, cache, red):
    if consulta in cache:
        return cache[consulta]
    params = {"q": consulta, "format": "jsonv2", "limit": 6, "countrycodes": "cl", "addressdetails": 1}
    res = red.pedir(URL_NOMINATIM + "?" + urllib.parse.urlencode(params))
    if res is None:
        return None                                     # sin respuesta: no se guarda, se reintenta la próxima vez
    cands = [{"lat": float(r["lat"]), "lon": float(r["lon"]), "nombre": r.get("display_name", ""),
              "clase": r.get("category", r.get("class", "")), "tipo": r.get("type", ""),
              "tipo_direccion": r.get("addresstype", ""), "region": (r.get("address") or {}).get("state", "")}
             for r in res if isinstance(r, dict) and "lat" in r]
    cache[consulta] = cands
    return cands


def elegir_candidato(cands, region, ref=None, max_km=None):
    palabra = REGIONES.get(region, ("", ""))[1]
    buenos = []
    for orden, c in enumerate(cands or []):
        if c["tipo_direccion"] in TIPOS_RECHAZADOS:
            continue
        if palabra and c["region"] and palabra not in clave(c["region"]):
            continue
        if ref is not None and max_km and float(dist_m(ref[0], ref[1], c["lat"], c["lon"])) / 1000 > max_km:
            continue
        rango = 0 if (c["clase"] == "place" and c["tipo"] in TIPOS_LUGAR) else (1 if c["clase"] == "boundary" else 2)
        buenos.append((rango, orden, c))
    return min(buenos, key=lambda x: (x[0], x[1]))[2] if buenos else None


def buscar(nombres, contexto, region, cache, red, ref=None, max_km=None):
    """Prueba las consultas en orden; devuelve (candidato, consulta, hubo_falla_de_red)."""
    falla = False
    reg_txt = REGIONES.get(region, ("", ""))[0]
    consultas = []
    for n in nombres:
        consultas += [", ".join(x for x in (n, contexto, reg_txt, "Chile") if x), ", ".join(x for x in (n, reg_txt, "Chile") if x)]
    for q in dict.fromkeys(consultas):
        cands = candidatos_nominatim(q, cache, red)
        if cands is None:
            falla = True
            continue
        c = elegir_candidato(cands, region, ref, max_km)
        if c:
            return c, q, falla
    return None, "", falla


def leer_manual_lugares(ruta):
    if not ruta.exists():
        pd.DataFrame([{"clave": k, "texto": t, "lat": "", "lon": "", "ignorar": "", "nota": n} for k, t, n in MANUAL_INICIAL]
                     ).to_csv(ruta, index=False, encoding="utf-8-sig")
        print(f"Creado {ruta.name}: complétalo con las coordenadas de la base (y de Santiago si corresponde)")
    m = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig").fillna("")
    for c in ("clave", "lat", "lon", "ignorar"):
        if c not in m.columns:
            m[c] = ""
    m["clave"] = m["clave"].map(clave)
    return {r["clave"]: r for _, r in m.iterrows() if r["clave"]}


def ubicar_lugares(textos, carpeta_cache, red, manual):
    archivo_cache = carpeta_cache / "geocodificacion.json"
    cache = json.loads(archivo_cache.read_text(encoding="utf-8")) if archivo_cache.exists() else {}
    filas, n_consultas = [], len(cache)
    for i, texto in enumerate(textos, start=1):
        info = interpretar_lugar(texto)
        notas = info.pop("_notas")
        nombres = [n for n in info.pop("_nombres").split("|") if n]
        ref = REFERENCIA.get(info.pop("_base"))
        fila = dict(info, lat=math.nan, lon=math.nan, precision="", fuente="", consulta="", nombre_encontrado="", observacion="")
        man = manual.get(info["clave"])
        if man is not None and str(man.get("ignorar", "")).strip().lower() in ("si", "sí", "s", "x", "1"):
            fila["es_lugar"] = False
            notas.append("marcado como 'ignorar' en lugares_manual.csv")
        if not fila["es_lugar"]:
            fila["precision"] = "no es un lugar"
        elif man is not None and not math.isnan(a_numero(man["lat"])) and not math.isnan(a_numero(man["lon"])):
            fila.update(lat=a_numero(man["lat"]), lon=a_numero(man["lon"]), precision="manual", fuente="lugares_manual.csv")
            if str(man.get("nota", "")).strip():
                notas.append(str(man["nota"]).strip())
        else:
            loc, q, falla = buscar(nombres, "", info["region"], cache, red, ref=ref, max_km=TOL_LOCALIDAD_KM if ref else None)
            if loc is None and ref is not None:
                loc = {"lat": ref[0], "lon": ref[1], "nombre": "(ubicación aproximada del programa)"}
                fila.update(lat=ref[0], lon=ref[1], precision="localidad (aprox.)", fuente="referencia del programa")
                notas.append("sin respuesta del servicio de mapas" if falla else
                             f"Nominatim no devolvió esta localidad a menos de {TOL_LOCALIDAD_KM} km de donde debería estar")
                notas.append("se usó la ubicación aproximada del programa (precisión de unos km)")
            elif loc is None:
                fila["precision"] = "sin coordenadas"
                notas.append("sin respuesta del servicio de mapas: volver a ejecutar" if falla else "Nominatim no encontró la localidad")
            else:
                fila.update(lat=loc["lat"], lon=loc["lon"], precision="localidad", fuente="Nominatim", consulta=q,
                            nombre_encontrado=loc["nombre"])
            if loc is not None:
                sub = info["sublugar"]
                if sub and sub in SUBLUGAR_EMPRESA:
                    notas.append(f"'{sub}' es un punto propio de la empresa o un cliente: se usó el centro de {fila['localidad']} "
                                 f"(si importa, poner sus coordenadas en lugares_manual.csv)")
                elif sub:
                    subs = SUBLUGAR_BUSQUEDA.get(sub, sub.title()).split("|")
                    c, q2, falla2 = buscar(subs, nombres[0], info["region"], cache, red, ref=(loc["lat"], loc["lon"]),
                                           max_km=DIST_MAX_SUBLUGAR_KM)
                    if c is not None:
                        fila.update(lat=c["lat"], lon=c["lon"], precision="sublugar", consulta=q2, nombre_encontrado=c["nombre"])
                    else:
                        notas.append(f"no se encontró '{sub}' cerca de {fila['localidad']}"
                                     + (" (falló el servicio: volver a ejecutar)" if falla2 else "") + ": se usó la localidad")
        fila["observacion"] = "; ".join(notas)
        filas.append(fila)
        if len(cache) > n_consultas:                     # guardar avance (por si se corta)
            archivo_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
            n_consultas = len(cache)
        if i % 20 == 0:
            print(f"  lugares: {i}/{len(textos)}")
    return pd.DataFrame(filas)


# --------------------------------------------------------------------------------------------- 2. plazas
def clave_plaza(concesion, nombre, posicion):
    return f"{clave(concesion)} | {clave(nombre)} | {clave(posicion)}"


def cargar_plazas(carpeta):
    """Plazas de tabla_integrada (las que tienen o deberían tener tarifa) y sus puntos con coordenadas."""
    archivo = carpeta / "tabla_integrada.csv"
    if not archivo.exists():
        sys.exit(f"No encuentro {archivo.resolve()} (sale de peajes_descarga.py). Usa --peajes con la carpeta correcta.")
    ti = pd.read_csv(archivo, encoding="utf-8-sig")
    urb = carpeta / "porticos_urbanos.csv"
    if urb.exists():                    # pórticos de autopistas urbanas con código (PA15, P1.3...): se ubican por nombre en OSM
        pu = pd.read_csv(urb, encoding="utf-8-sig")
        pu = pu[pu["portico_o_tramo"].fillna("").map(lambda t: bool(codigo_portico(t)[0]))]
        # solo la lámina más reciente de cada autopista: los nombres de los pórticos cambian entre años y las láminas
        # antiguas (laminas_historicas.py) duplicarían pórticos con el mismo código al ubicarlos en OpenStreetMap
        anio = pu["vigencia_desde"].astype(str).str[:4]
        pu = pu[anio == anio.groupby(pu["documento"]).transform("max")]
        ti = pd.concat([ti, pd.DataFrame({
            "concesion": pu["documento"], "plaza_mop": pu["portico_o_tramo"], "plaza_pdf": None, "posicion": "Pórtico",
            "ruta": "", "km": np.nan, "lat": np.nan, "lon": np.nan, "fuente_plaza": "porticos_urbanos",
            "categoria": pu["categoria"], "horario": pu["horario"], "modalidad": pu["modalidad"], "tarifa_clp": pu["tarifa_clp"],
            "vigencia_desde": pu["vigencia_desde"], "vigencia_hasta": pu["vigencia_hasta"]})], ignore_index=True)
    ti["nombre_plaza"] = ti["plaza_mop"].fillna(ti["plaza_pdf"]).fillna("(sin nombre)").astype(str)
    ti["posicion"] = ti["posicion"].fillna("")
    ti["clave_plaza"] = [clave_plaza(c, n, p) for c, n, p in zip(ti["concesion"].fillna(""), ti["nombre_plaza"], ti["posicion"])]
    plazas = ti.groupby("clave_plaza", as_index=False).agg(
        concesion=("concesion", "first"), nombre=("nombre_plaza", "first"), posicion=("posicion", "first"),
        ruta=("ruta", "first"), km=("km", "first"), lat=("lat", "first"), lon=("lon", "first"),
        fuente_plaza=("fuente_plaza", "first"), tiene_tarifa=("tarifa_clp", lambda s: bool(s.notna().any())))
    puntos = []
    mop = carpeta / "plazas_mop.csv"
    if mop.exists():
        pm = pd.read_csv(mop, encoding="utf-8-sig")
        for _, r in pm.iterrows():
            k = clave_plaza(r.get("concesion", ""), r.get("nombre", ""), str(r.get("posicion", "") or "").strip().capitalize())
            if not (pd.isna(r.get("lat")) or pd.isna(r.get("lon"))):
                puntos.append({"clave_plaza": k, "lat": float(r["lat"]), "lon": float(r["lon"]), "origen": "capa MOP"})
    man = carpeta / "plazas_manual.csv"
    if man.exists():
        pmn = pd.read_csv(man, encoding="utf-8-sig", dtype=str).fillna("")
        for _, r in pmn.iterrows():
            lat, lon = a_numero(r.get("lat", "")), a_numero(r.get("lon", ""))
            if not (math.isnan(lat) or math.isnan(lon)):
                k = clave_plaza(r.get("concesion", ""), r.get("nombre", ""), r.get("posicion", ""))
                puntos.append({"clave_plaza": k, "lat": lat, "lon": lon, "origen": "plazas_manual"})
    puntos = pd.DataFrame(puntos, columns=["clave_plaza", "lat", "lon", "origen"])
    puntos = puntos[puntos["clave_plaza"].isin(plazas["clave_plaza"])]
    # plazas con coordenadas en tabla_integrada pero sin punto (por si cambió algún nombre en la capa)
    faltan = plazas[~plazas["clave_plaza"].isin(puntos["clave_plaza"]) & plazas["lat"].notna() & plazas["lon"].notna()]
    if len(faltan):
        puntos = pd.concat([puntos, pd.DataFrame({"clave_plaza": faltan["clave_plaza"], "lat": faltan["lat"].astype(float),
                                                  "lon": faltan["lon"].astype(float), "origen": "tabla_integrada"})])
    return ti, plazas, puntos.drop_duplicates().reset_index(drop=True)


def peajes_osm(carpeta_cache, red, actualizar=False):
    archivo = carpeta_cache / "osm_peajes.json"
    datos = None
    if archivo.exists() and not actualizar:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    else:
        print("Descargando casetas y pórticos de peaje de OpenStreetMap (Overpass, puede tardar 1-2 minutos)...")
        res = red.pedir(URL_OVERPASS, datos=urllib.parse.urlencode({"data": CONSULTA_OVERPASS}).encode(), timeout=240)
        if res is not None and "elements" in res:
            datos = res
            archivo.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        elif archivo.exists():
            datos = json.loads(archivo.read_text(encoding="utf-8"))
    filas = []
    for el in (datos or {}).get("elements", []):
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        es_portico = tags.get("highway") == "toll_gantry"
        tipo = "pórtico" if es_portico else ("caseta (área)" if el.get("type") == "way" else "caseta")
        nombre = tags.get("name") or tags.get("description") or ""
        filas.append({"osm_id": f"{el.get('type', 'node')[0]}{el.get('id')}", "tipo": tipo, "nombre_osm": nombre,
                      "operador_osm": tags.get("operator", ""), "ref_osm": tags.get("ref", ""), "lat": float(lat), "lon": float(lon),
                      "umbral_m": UMBRAL_OSM_AREA_M if el.get("type") == "way" else UMBRAL_OSM_M})
    if datos is None:
        print("  ! no hay datos de OpenStreetMap: las plazas se detectarán solo con los puntos del MOP/manuales")
    return pd.DataFrame(filas, columns=["osm_id", "tipo", "nombre_osm", "operador_osm", "ref_osm", "lat", "lon", "umbral_m"])


def asignar_osm(osm, plazas, puntos):
    """
    Cada caseta/pórtico de OpenStreetMap se asigna a una plaza de tabla_integrada, en este orden:
      1. pórticos urbanos, por su código y nombre ("PA15 - 14 de la Fama - Río Mapocho");
      2. plazas sin coordenadas, por nombre ("Pórtico 8 - Enlace Lo Pinto" -> Enlace Lo Pinto);
      3. plazas con coordenadas: la más cercana a menos de 1,5 km, o una de igual nombre a menos de 12 km;
      4. pórticos sin nombre que quedaron libres: la troncal más cercana a menos de 5 km que no tenga pórticos
         (troncal que pasó a free-flow en un lugar distinto al de la capa del MOP, como Las Vegas).
    """
    osm = osm.copy()
    osm["clave_plaza"], osm["asignacion"], osm["dist_plaza_m"] = None, "", np.nan
    if not len(osm):
        return osm
    info = plazas.set_index("clave_plaza")
    frases = {k: frase_plaza(r["nombre"]) for k, r in info.iterrows()}
    urbana = {k: r["fuente_plaza"] == "porticos_urbanos" for k, r in info.iterrows()}
    posicion = {k: clave(r["posicion"]) for k, r in info.iterrows()}
    nombres_osm = osm["nombre_osm"].map(clave)
    codigos_osm = osm["nombre_osm"].map(lambda n: codigo_portico(n)[0] if n else "")

    # 1. pórticos urbanos
    por_codigo = {}
    for k, r in info.iterrows():
        if urbana[k]:
            cod, resto = codigo_portico(r["nombre"])
            if cod:
                por_codigo.setdefault(cod, []).append((k, resto))
    for i in osm.index[(osm["tipo"] == "pórtico") & (codigos_osm != "")]:
        _, resto_osm = codigo_portico(osm.at[i, "nombre_osm"])
        cands = [(similitud(resto_osm, resto), k) for k, resto in por_codigo.get(codigos_osm[i], [])]
        if cands:
            sim, k = max(cands)
            if sim >= 0.55:
                osm.at[i, "clave_plaza"], osm.at[i, "asignacion"] = k, f"pórtico urbano por código y nombre (similitud {sim:.2f})"

    # 2. plazas sin coordenadas, por nombre
    con_punto = set(puntos["clave_plaza"])
    for k, r in info.iterrows():
        if k in con_punto or urbana[k] or len(frases[k].replace(" ", "")) < 4:
            continue
        libres = osm["clave_plaza"].isna()
        coinc = osm[libres & nombres_osm.map(lambda n: contiene_frase(frases[k], n))]
        if posicion[k] == "PORTICO" and (coinc["tipo"] == "pórtico").any():
            coinc = coinc[coinc["tipo"] == "pórtico"]
        if not len(coinc):
            continue
        dispersion = float(np.max(dist_m(coinc["lat"].mean(), coinc["lon"].mean(), coinc["lat"].to_numpy(), coinc["lon"].to_numpy())))
        if dispersion > 5000:
            osm.loc[coinc.index, "asignacion"] = f"nombre ambiguo con '{r['nombre']}' (casetas a más de 5 km entre sí)"
            continue
        osm.loc[coinc.index, "clave_plaza"] = k
        osm.loc[coinc.index, "asignacion"] = "por nombre (plaza sin coordenadas: verificar)"

    # 3. plazas con coordenadas
    if len(puntos):
        plat, plon, pk = puntos["lat"].to_numpy(), puntos["lon"].to_numpy(), puntos["clave_plaza"].to_numpy()
        for i in osm.index[osm["clave_plaza"].isna()]:
            d = dist_m(osm.at[i, "lat"], osm.at[i, "lon"], plat, plon)
            nom = nombres_osm[i]
            es_portico = osm.at[i, "tipo"] == "pórtico"
            pos_osm = "LATERAL" if contiene_frase("LATERAL", nom) else ("TRONCAL" if contiene_frase("TRONCAL", nom) else "")
            mejor = None
            nucleo = nucleo_osm(osm.at[i, "nombre_osm"])
            for j in np.nonzero(d <= RADIO_NOMBRE_M)[0]:
                k = pk[j]
                coincide = contiene_frase(frases[k], nom)
                debil = (not coincide and len(nucleo.replace(" ", "")) >= 5 and contiene_frase(nucleo, frases[k])
                         and d[j] <= RADIO_NOMBRE_M / 2)
                pos = posicion[k]
                if d[j] > RADIO_ASIGNACION_M and not (coincide or debil):
                    continue                                 # lejos: solo si el nombre coincide ("Peaje Angostura")
                coincide = coincide or debil
                # un pórtico va con plazas tipo pórtico; con una troncal solo si no tiene nombre (troncal que pasó a
                # free-flow) o si el nombre coincide; nunca "Pórtico 8 - Enlace Lo Pinto" con la troncal San José
                permitido = coincide or es_portico == (pos == "PORTICO") or (es_portico and pos == "TRONCAL" and not nom)
                if not permitido:
                    continue
                puntaje = d[j] - (500 if coincide else 0)
                if pos_osm and pos:
                    puntaje += -150 if pos_osm == pos else 300
                if mejor is None or puntaje < mejor[0]:
                    mejor = (puntaje, k, d[j], coincide)
            if mejor:
                osm.at[i, "clave_plaza"], osm.at[i, "dist_plaza_m"] = mejor[1], round(float(mejor[2]), 1)
                osm.at[i, "asignacion"] = "cercanía y nombre" if mejor[3] else "cercanía"

        # 4. pórticos sin nombre todavía libres -> troncal cercana sin pórticos
        con_portico = set(osm.loc[(osm["tipo"] == "pórtico") & osm["clave_plaza"].notna(), "clave_plaza"])
        es_troncal = np.array([posicion.get(k) == "TRONCAL" and k not in con_portico for k in pk])
        for i in osm.index[osm["clave_plaza"].isna() & (osm["tipo"] == "pórtico") & (nombres_osm == "")]:
            if not es_troncal.any():
                break
            d = np.where(es_troncal, dist_m(osm.at[i, "lat"], osm.at[i, "lon"], plat, plon), np.inf)
            j = int(np.argmin(d))
            if d[j] <= RADIO_FREE_FLOW_KM * 1000:
                osm.at[i, "clave_plaza"], osm.at[i, "dist_plaza_m"] = pk[j], round(float(d[j]), 1)
                osm.at[i, "asignacion"] = f"pórtico sin nombre a {d[j] / 1000:.1f} km de la troncal (pasó a free-flow): verificar"
    return osm


def puntos_deteccion(osm, plazas, puntos):
    """Puntos con que se detecta cada plaza: sus casetas OSM si tiene; si no, sus puntos MOP/manuales con margen mayor."""
    filas = []
    for _, r in osm.iterrows():
        filas.append({"clave_plaza": r["clave_plaza"], "origen": "OSM", "osm_id": r["osm_id"], "tipo": r["tipo"],
                      "nombre_osm": r["nombre_osm"], "lat": r["lat"], "lon": r["lon"], "umbral_m": r["umbral_m"]})
    con_osm = set(osm["clave_plaza"].dropna())
    pos = plazas.set_index("clave_plaza")["posicion"].map(clave)
    for _, r in puntos[~puntos["clave_plaza"].isin(con_osm)].iterrows():
        filas.append({"clave_plaza": r["clave_plaza"], "origen": r["origen"], "osm_id": "", "tipo": "", "nombre_osm": "",
                      "lat": r["lat"], "lon": r["lon"], "umbral_m": UMBRAL_MOP_M.get(pos.get(r["clave_plaza"], ""), 100)})
    return pd.DataFrame(filas, columns=["clave_plaza", "origen", "osm_id", "tipo", "nombre_osm", "lat", "lon", "umbral_m"])


def resumen_plazas(plazas, puntos, osm):
    filas = []
    for _, p in plazas.iterrows():
        k = p["clave_plaza"]
        o = osm[osm["clave_plaza"] == k]
        pts = puntos[puntos["clave_plaza"] == k]
        if len(o):
            fuente = f"OpenStreetMap ({len(o)} caseta/pórtico)" + (" por nombre: verificar" if o["asignacion"].str.startswith("por nombre").any() else "")
            lat, lon = o["lat"].mean(), o["lon"].mean()
        elif len(pts):
            fuente, lat, lon = ", ".join(sorted(set(pts["origen"]))), pts["lat"].mean(), pts["lon"].mean()
        else:
            fuente, lat, lon = "sin coordenadas: no se puede detectar", math.nan, math.nan
        d_mop_osm = math.nan
        if len(o) and len(pts):
            d_mop_osm = round(float(min(dist_m(a, b, o["lat"].to_numpy(), o["lon"].to_numpy()).min() for a, b in zip(pts["lat"], pts["lon"]))), 1)
        filas.append({"clave_plaza": k, "concesion": p["concesion"], "plaza": p["nombre"], "posicion": p["posicion"],
                      "ruta": p["ruta"], "km": p["km"], "tiene_tarifa": p["tiene_tarifa"], "coordenadas_de": fuente,
                      "lat": lat, "lon": lon, "puntos_mop_o_manual": len(pts), "casetas_osm": len(o),
                      "osm_ids": " ".join(o["osm_id"]), "nombres_osm": " | ".join(sorted(set(o["nombre_osm"]) - {""})),
                      "dist_punto_mop_a_osm_m": d_mop_osm})
    return pd.DataFrame(filas)


# --------------------------------------------------------------------------------------------- 3. rutas
def ruta_osrm(a, b, carpeta, red):
    k = f"{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"
    archivo = carpeta / (hashlib.sha1(k.encode()).hexdigest()[:20] + ".json.gz")
    if archivo.exists():
        with gzip.open(archivo, "rt", encoding="utf-8") as f:
            return json.load(f)
    url = f"{URL_OSRM}/{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}?overview=full&geometries=polyline6&alternatives=false&steps=false"
    res = red.pedir(url)
    if res is None:
        return None
    if res.get("code") == "Ok" and res.get("routes"):
        r = res["routes"][0]
        dato = {"codigo": "Ok", "distancia_m": r["distance"], "duracion_s": r["duration"], "geometria": r["geometry"], "par": k}
    else:
        dato = {"codigo": res.get("code", "?"), "mensaje": res.get("message", ""), "par": k}
    with gzip.open(archivo, "wt", encoding="utf-8") as f:
        json.dump(dato, f)
    return dato


def detectar(ruta, det):
    """Plazas y casetas OSM cruzadas por la ruta; también plazas MOP cercanas que no alcanzaron el umbral."""
    if ruta.n < 2 or not len(det):
        return [], []
    cruces, cercanas = [], []
    for i in range(len(det)):
        lat, lon = det["lat"].iat[i], det["lon"].iat[i]
        if not ruta.cerca(lat, lon):
            continue
        d, pos = ruta.distancia(lat, lon)
        umbral = det["umbral_m"].iat[i]
        if d <= umbral:
            cruces.append((i, d, pos))
        elif det["origen"].iat[i] != "OSM" and d <= 3 * umbral:
            cercanas.append((i, d, pos))
    return cruces, cercanas


def analizar_par(ruta, det, posiciones):
    """Lista ordenada de lo que cruza el par: plazas de tabla_integrada y casetas OSM sin plaza asignada."""
    cruces, cercanas = detectar(ruta, det)
    largo_km = ruta.largo_m / 1000
    por_plaza, sin_plaza = {}, []
    for i, d, pos in cruces:
        k = det["clave_plaza"].iat[i]
        if isinstance(k, str) and k:
            if k not in por_plaza or d < por_plaza[k]["distancia_m"]:
                origen = det["origen"].iat[i]
                metodo = (f"caseta OSM {det['osm_id'].iat[i]} sobre la ruta" if origen == "OSM"
                          else f"punto {origen} a {d:.0f} m de la ruta")
                por_plaza[k] = {"clave_plaza": k, "km_en_par": pos / 1000, "distancia_m": round(d, 1), "metodo": metodo,
                                "origen": origen, "posicion": posiciones.get(k, ""),
                                "confianza": "alta" if origen == "OSM" else ("media" if d <= 60 else "baja")}
        else:
            sin_plaza.append({"osm_id": det["osm_id"].iat[i], "tipo": det["tipo"].iat[i], "nombre_osm": det["nombre_osm"].iat[i],
                              "lat": det["lat"].iat[i], "lon": det["lon"].iat[i], "km_en_par": pos / 1000, "distancia_m": round(d, 1)})
    # Laterales sin caseta OSM: el punto MOP está sobre el eje de la carretera, así que una ruta que solo pasa de largo
    # también queda cerca. Una lateral se paga al entrar o salir de la autopista: se deja solo la más cercana al
    # inicio y la más cercana al final del recorrido (dentro de MARGEN_LATERAL_KM).
    lat_mop = [x for x in por_plaza.values() if x["origen"] != "OSM" and x["posicion"] == "LATERAL"]
    quedan = set()
    al_inicio = [x for x in lat_mop if x["km_en_par"] <= MARGEN_LATERAL_KM]
    al_final = [x for x in lat_mop if largo_km - x["km_en_par"] <= MARGEN_LATERAL_KM]
    if al_inicio:
        quedan.add(min(al_inicio, key=lambda x: x["km_en_par"])["clave_plaza"])
    if al_final:
        quedan.add(max(al_final, key=lambda x: x["km_en_par"])["clave_plaza"])
    for x in lat_mop:
        if x["clave_plaza"] in quedan:
            x["metodo"] += " (lateral al entrar o salir de la carretera: verificar)"
            x["confianza"] = "baja"
        else:
            del por_plaza[x["clave_plaza"]]
    # Pórticos free-flow sin nombre a pocos km de una troncal detectada por su punto MOP: es la misma plaza
    # (varias troncales se convirtieron en pórticos y la capa del MOP aún tiene la ubicación antigua).
    troncales_mop = [x for x in por_plaza.values() if x["origen"] != "OSM" and x["posicion"] == "TRONCAL"]
    restantes = []
    for s in sin_plaza:
        cercana = None
        if s["tipo"] == "pórtico" and not codigo_portico(s["nombre_osm"])[0]:
            cercana = next((x for x in troncales_mop if abs(x["km_en_par"] - s["km_en_par"]) <= RADIO_FREE_FLOW_KM), None)
        if cercana is not None:
            if "free-flow" not in cercana["metodo"]:
                cercana["metodo"] += f" (+ pórtico free-flow OSM {s['osm_id']} a {abs(cercana['km_en_par'] - s['km_en_par']):.1f} km)"
        else:
            restantes.append(s)
    # casetas OSM sin plaza: varias casetas de un mismo lugar (una por pista o sentido) cuentan una vez
    restantes.sort(key=lambda x: x["km_en_par"])
    agrupadas = []
    for s in restantes:
        if agrupadas and agrupadas[-1]["tipo"] == s["tipo"] and s["km_en_par"] - agrupadas[-1]["km_en_par"] < 0.3:
            continue
        agrupadas.append(s)
    cerca = {}
    for i, d, pos in cercanas:
        k = det["clave_plaza"].iat[i]
        if k not in por_plaza and posiciones.get(k, "") != "LATERAL" and (k not in cerca or d < cerca[k]):
            cerca[k] = round(d, 1)
    return sorted(por_plaza.values(), key=lambda x: x["km_en_par"]), agrupadas, cerca


# --------------------------------------------------------------------------------------------- 4. tarifas
def tarifas_vigentes(ti, fecha, horario):
    """Una tarifa por plaza para la fecha: horario preferido (o única), con TAG si hay modalidad, la vigencia más reciente."""
    t = ti[ti["tarifa_clp"].notna()].copy()
    t["desde"] = pd.to_datetime(t["vigencia_desde"], errors="coerce")
    t["hasta"] = pd.to_datetime(t["vigencia_hasta"], errors="coerce")
    f = pd.Timestamp(fecha)
    t = t[(t["desde"].isna() | (t["desde"] <= f)) & (t["hasta"].isna() | (f <= t["hasta"]))]
    salida = {}
    for k, g in t.groupby("clave_plaza"):
        if g["desde"].notna().any():
            g = g[g["desde"] == g["desde"].max()]
        for col, preferencias in (("horario", (horario, "unica")), ("modalidad", ("con tag", ""))):
            valores = g[col].fillna("").astype(str).str.lower()
            for pref in preferencias:
                if (valores == pref).any():
                    g = g[valores == pref]
                    break
        cat = g["categoria"].fillna("")
        if (cat == "camion_con_remolque").any():
            g = g[cat == "camion_con_remolque"]
        montos = sorted(set(g["tarifa_clp"].astype(float)))
        primera = g.iloc[0]
        salida[k] = {"tarifa_clp": max(montos), "categoria": primera["categoria"], "horario": primera["horario"],
                     "modalidad": "" if pd.isna(primera["modalidad"]) else primera["modalidad"],
                     "vigencia_desde": primera["vigencia_desde"],
                     "nota_tarifa": (f"la lámina trae {len(montos)} valores ({', '.join(pesos(m) for m in montos)}, p. ej. por sentido): "
                                     f"se usó el mayor") if len(montos) > 1 else ""}
    return salida


# --------------------------------------------------------------------------------------------- programa principal
def principal(argv=None, red=None):
    print(f"peajes_por_tramo.py versión {VERSION}")
    ap = argparse.ArgumentParser(description="Peaje de cada tramo a partir de las plazas que cruza su recorrido.")
    ap.add_argument("--tramos", default=TRAMOS, help="tramos_desglosados.csv de ciudades_tramos.py")
    ap.add_argument("--peajes", default=PEAJES, help="carpeta de salida de peajes_descarga.py")
    ap.add_argument("--salida", default=SALIDA)
    ap.add_argument("--fecha", default=date.today().isoformat(), help="fecha de las tarifas (AAAA-MM-DD)")
    ap.add_argument("--horario", default="fuera de punta", choices=["fuera de punta", "punta"],
                    help="tarifa a usar en plazas que distinguen horario")
    ap.add_argument("--solo-activos", action="store_true", help="solo tramos con isactive = 'Y'")
    ap.add_argument("--sin-internet", action="store_true", help="usar solo lo guardado en cache")
    ap.add_argument("--actualizar-osm", action="store_true", help="volver a descargar las casetas de OpenStreetMap")
    args = ap.parse_args(argv)

    salida = Path(args.salida)
    cache = salida / "cache"
    (cache / "rutas").mkdir(parents=True, exist_ok=True)
    red = red or Red(internet=not args.sin_internet)

    # tramos
    archivo_tramos = Path(args.tramos)
    if not archivo_tramos.exists():
        sys.exit(f"No encuentro {archivo_tramos.resolve()} (sale de ciudades_tramos.py). Usa --tramos con la ruta correcta.")
    tramos = pd.read_csv(archivo_tramos, dtype=str, encoding="utf-8-sig").fillna("")
    if args.solo_activos and "isactive" in tramos.columns:
        tramos = tramos[tramos["isactive"].str.upper().isin(["Y", "TRUE", "1", "S"])]
    tramos["sit_stretch_id"] = tramos["sit_stretch_id"].map(limpiar_id)
    tramos["partes"] = tramos["tramo"].map(lambda t: [re.sub(r"\s+", " ", p).strip() for p in str(t).split("/") if p.strip()])
    textos = list({clave(p): p for ps in tramos["partes"][::-1] for p in ps[::-1]}.values())[::-1]  # uno por forma normalizada
    print(f"Tramos: {len(tramos)} | lugares distintos: {len(textos)}")

    # 1. lugares
    print("1) Ubicando lugares...")
    manual = leer_manual_lugares(salida / "lugares_manual.csv")
    lugares = ubicar_lugares(textos, cache, red, manual)

    # 2. plazas
    print("2) Plazas de peaje...")
    ti, plazas, puntos = cargar_plazas(Path(args.peajes))
    osm = asignar_osm(peajes_osm(cache, red, args.actualizar_osm), plazas, puntos)
    det = puntos_deteccion(osm, plazas, puntos)
    ubic = resumen_plazas(plazas, puntos, osm)
    print(f"   plazas en tabla_integrada: {len(plazas)} | con coordenadas: {int(ubic['lat'].notna().sum())} | "
          f"casetas/pórticos OSM: {len(osm)} (asignados a una plaza: {int(osm['clave_plaza'].notna().sum())})")

    posiciones = {k: clave(v) for k, v in plazas.set_index("clave_plaza")["posicion"].items()}
    urbanas = set(plazas.loc[plazas["fuente_plaza"] == "porticos_urbanos", "clave_plaza"])
    # lugares muy cerca de una plaza troncal: según el lado en que quede el lugar real, se paga o no
    troncales = det[det["clave_plaza"].map(lambda k: isinstance(k, str) and posiciones.get(k) in ("TRONCAL", "PORTICO")
                                            and k not in urbanas)].reset_index(drop=True)
    nombre_plaza = plazas.set_index("clave_plaza")["nombre"]
    cercania = []
    for _, l in lugares.iterrows():
        if pd.isna(l["lat"]) or not len(troncales):
            cercania.append("")
            continue
        d = dist_m(l["lat"], l["lon"], troncales["lat"].to_numpy(), troncales["lon"].to_numpy())
        j = int(np.argmin(d))
        cercania.append(f"a {d[j] / 1000:.1f} km del peaje {nombre_plaza.get(troncales['clave_plaza'].iat[j])}" if d[j] < 3000 else "")
    lugares["peaje_cercano"] = cercania
    coord = {r["clave"]: (r["lat"], r["lon"]) for _, r in lugares.iterrows() if not pd.isna(r["lat"])}
    es_lugar = dict(zip(lugares["clave"], lugares["es_lugar"]))

    # 3. pares y rutas
    tramos["claves"] = tramos["partes"].map(lambda ps: [clave(p) for p in ps])
    pares = {}
    for cs in tramos["claves"]:
        for a, b in zip(cs, cs[1:]):
            if a in coord and b in coord:
                pares.setdefault((a, b), 0)
                pares[(a, b)] += 1
    print(f"3) Recorridos por carretera: {len(pares)} pares de lugares consecutivos")
    resultado_par, inicio, nuevos = {}, time.time(), 0
    for n, (a, b) in enumerate(pares, start=1):
        pa, pb = coord[a], coord[b]
        if float(dist_m(pa[0], pa[1], pb[0], pb[1])) < 50:
            resultado_par[(a, b)] = {"estado": "mismo punto", "km": 0.0, "horas": 0.0, "plazas": [], "osm": [], "cerca": {}}
            continue
        ya = (cache / "rutas" / (hashlib.sha1(f"{pa[0]:.5f},{pa[1]:.5f};{pb[0]:.5f},{pb[1]:.5f}".encode()).hexdigest()[:20] + ".json.gz")).exists()
        dato = ruta_osrm(pa, pb, cache / "rutas", red)
        nuevos += 0 if ya else 1
        if dato is None:
            resultado_par[(a, b)] = {"estado": "sin respuesta del servicio de rutas (volver a ejecutar)", "km": math.nan, "horas": math.nan,
                                     "plazas": [], "osm": [], "cerca": {}}
            continue
        if dato.get("codigo") != "Ok":
            resultado_par[(a, b)] = {"estado": f"sin ruta ({dato.get('codigo')}: {dato.get('mensaje', '')})", "km": math.nan,
                                     "horas": math.nan, "plazas": [], "osm": [], "cerca": {}}
            continue
        ruta = Ruta(decodificar_polilinea(dato["geometria"]))
        pl, so, ce = analizar_par(ruta, det, posiciones)
        resultado_par[(a, b)] = {"estado": "ok", "km": dato["distancia_m"] / 1000, "horas": dato["duracion_s"] / 3600,
                                 "plazas": pl, "osm": so, "cerca": ce}
        if n % 25 == 0 or n == len(pares):
            print(f"   {n}/{len(pares)} pares ({nuevos} consultados ahora, {time.time() - inicio:.0f} s)")

    # 4. tarifas y armado de salidas
    tarifas = tarifas_vigentes(ti, args.fecha, args.horario)
    info_plaza = plazas.set_index("clave_plaza")

    def etiqueta(k):
        p = info_plaza.loc[k]
        return f"{p['nombre']} ({p['posicion']})" if p["posicion"] else str(p["nombre"])

    def tarifas_par(r):
        """[(plaza cruzada, tarifa o None)] del par. En concesiones que cobran por recorrido se cobra una vez (la mayor) y
        las demás plazas de esa concesión quedan en $0 como incluidas."""
        filas, cobradas = [], {}
        for x in r["plazas"]:
            tar = tarifas.get(x["clave_plaza"])
            conc = clave(info_plaza.at[x["clave_plaza"], "concesion"])
            g = next((c for c in COBRO_POR_RECORRIDO if c in conc), None)
            filas.append([x, dict(tar) if tar else None, g])
            if g and tar:
                cobradas.setdefault(g, []).append(len(filas) - 1)
        for g, idx in cobradas.items():
            mayor = max(idx, key=lambda i: filas[i][1]["tarifa_clp"])
            for i, f in enumerate(filas):
                if f[2] == g and i != mayor:
                    base_t = f[1] or {"categoria": "", "horario": "", "modalidad": "", "vigencia_desde": ""}
                    f[1] = dict(base_t, tarifa_clp=0.0,
                                nota_tarifa="incluida: esta concesión cobra una vez por recorrido (origen-destino)")
        return [(x, tar) for x, tar, _ in filas]

    filas_pares = []
    for (a, b), r in resultado_par.items():
        monto = sum(tar["tarifa_clp"] for _, tar in tarifas_par(r) if tar)
        filas_pares.append({"desde_clave": a, "hasta_clave": b, "tramos_que_lo_usan": pares[(a, b)], "estado": r["estado"],
                            "km_ruta": round(r["km"], 1) if not pd.isna(r["km"]) else math.nan,
                            "horas_ruta": round(r["horas"], 2) if not pd.isna(r["horas"]) else math.nan,
                            "n_plazas": len(r["plazas"]), "peaje_clp": monto,
                            "plazas": " → ".join(etiqueta(x["clave_plaza"]) for x in r["plazas"]),
                            "casetas_osm_sin_plaza": " | ".join(f"{s['nombre_osm'] or s['osm_id']} ({s['tipo']})" for s in r["osm"])})
    filas_pares = pd.DataFrame(filas_pares)

    puente, resumen = [], []
    for _, t in tramos.iterrows():
        cs = t["claves"]
        base = {"sit_stretch_id": t["sit_stretch_id"], "tramo": t["tramo"], "isactive": t.get("isactive", ""),
                "kilometers": a_numero(t.get("kilometers", ""))}
        problema = ""
        if any(not es_lugar.get(c, True) for c in cs):
            problema = "no calculado: el tramo contiene partes que no son lugares (" + ", ".join(c for c in cs if not es_lugar.get(c, True)) + ")"
        elif len(cs) < 2:
            problema = "no calculado: el tramo tiene un solo lugar (falta el origen o el destino)"
        elif any(c not in coord for c in cs):
            problema = "no calculado: lugar sin coordenadas (" + ", ".join(c for c in cs if c not in coord) + ")"
        elif any(resultado_par[(a, b)]["estado"] not in ("ok", "mismo punto") for a, b in zip(cs, cs[1:])):
            problema = "no calculado: " + "; ".join(resultado_par[(a, b)]["estado"] for a, b in zip(cs, cs[1:])
                                                    if resultado_par[(a, b)]["estado"] not in ("ok", "mismo punto"))
        if problema:
            resumen.append(dict(base, km_ruta=math.nan, dif_km_pct=math.nan, n_plazas=0, peaje_clp=math.nan, plazas_sin_tarifa=0,
                                casetas_osm_sin_plaza=0, porticos_osm_sin_plaza=0, estado=problema, detalle=""))
            continue
        km_acum, orden, total, sin_tarifa, detalle, n_casetas, n_porticos = 0.0, 0, 0.0, 0, [], 0, 0
        for np_, (a, b) in enumerate(zip(cs, cs[1:]), start=1):
            r = resultado_par[(a, b)]
            for x, tar in tarifas_par(r):
                orden += 1
                p = info_plaza.loc[x["clave_plaza"]]
                puente.append(dict(base, orden=orden, par=np_, desde=a, hasta=b, clave_plaza=x["clave_plaza"], concesion=p["concesion"],
                                   plaza=p["nombre"], posicion=p["posicion"], ruta=p["ruta"], km_plaza=p["km"],
                                   km_desde_inicio_tramo=round(km_acum + x["km_en_par"], 1), distancia_a_ruta_m=x["distancia_m"],
                                   deteccion=x["metodo"], confianza=x["confianza"],
                                   tarifa_referencia_clp=tar["tarifa_clp"] if tar else math.nan,
                                   horario=tar["horario"] if tar else "", modalidad=tar["modalidad"] if tar else "",
                                   vigencia_desde=tar["vigencia_desde"] if tar else "", nota_tarifa=tar["nota_tarifa"] if tar else
                                   "sin tarifa vigente en tabla_integrada para la fecha"))
                if tar:
                    total += tar["tarifa_clp"]
                    detalle.append(f"{etiqueta(x['clave_plaza'])} {pesos(tar['tarifa_clp'])}")
                else:
                    sin_tarifa += 1
                    detalle.append(f"{etiqueta(x['clave_plaza'])} ¿sin tarifa?")
            n_casetas += sum(1 for s in r["osm"] if s["tipo"] != "pórtico")
            n_porticos += sum(1 for s in r["osm"] if s["tipo"] == "pórtico")
            km_acum += r["km"]
        estado = []
        if sin_tarifa:
            estado.append(f"{sin_tarifa} plaza(s) sin tarifa")
        if n_casetas:
            estado.append(f"{n_casetas} caseta(s) de OSM sin plaza en tabla_integrada")
        if n_porticos:
            estado.append(f"{n_porticos} pórtico(s) free-flow sin tarifa (autopistas urbanas u otros)")
        dif = (km_acum - base["kilometers"]) / base["kilometers"] if base["kilometers"] and not pd.isna(base["kilometers"]) else math.nan
        resumen.append(dict(base, km_ruta=round(km_acum, 1), dif_km_pct=round(100 * dif, 1) if not pd.isna(dif) else math.nan,
                            n_plazas=orden, peaje_clp=total, plazas_sin_tarifa=sin_tarifa, casetas_osm_sin_plaza=n_casetas,
                            porticos_osm_sin_plaza=n_porticos, estado="completo" if not estado else "parcial: " + "; ".join(estado),
                            detalle=" → ".join(detalle)))
    resumen = pd.DataFrame(resumen)
    resumen.insert(resumen.columns.get_loc("peaje_clp") + 1, "fecha_tarifas", args.fecha)
    puente = pd.DataFrame(puente)

    # uso de cada plaza (para ordenar lo que hay que revisar)
    uso = puente.groupby("clave_plaza")["sit_stretch_id"].nunique() if len(puente) else pd.Series(dtype=int)
    ubic["tramos_que_la_cruzan"] = ubic["clave_plaza"].map(uso).fillna(0).astype(int)

    # casetas OSM sin plaza que aparecen en los recorridos -> sugerencias para plazas_manual.csv
    sug = {}
    for (a, b), r in resultado_par.items():
        for s in r["osm"]:
            g = sug.setdefault(s["osm_id"], dict(s, pares=0, tramos=0, ejemplo=f"{a} → {b}"))
            g["pares"] += 1
            g["tramos"] += pares[(a, b)]
    sugeridas = pd.DataFrame(list(sug.values()))
    if len(sugeridas):
        sugeridas = sugeridas.sort_values("tramos", ascending=False)
        sugeridas = pd.DataFrame({"nombre": sugeridas["nombre_osm"].replace("", "(sin nombre en OSM)"), "concesion": "", "ruta": "",
                                  "km": "", "posicion": np.where(sugeridas["tipo"] == "pórtico", "Pórtico", "Troncal"),
                                  "lat": sugeridas["lat"].round(6), "lon": sugeridas["lon"].round(6), "tipo_osm": sugeridas["tipo"],
                                  "osm_id": sugeridas["osm_id"], "tramos_que_la_cruzan": sugeridas["tramos"],
                                  "ejemplo": sugeridas["ejemplo"]})

    # revisar
    rev = []
    for _, l in lugares.iterrows():
        if l["precision"] in ("sin coordenadas",) or (l["es_lugar"] and "no se encontró" in l["observacion"]) \
                or "no reconocida" in l["observacion"] or "propio de la empresa" in l["observacion"]:
            rev.append({"tipo": "lugar", "elemento": l["texto"], "detalle": l["observacion"] or l["precision"],
                        "tramos_afectados": int(sum(l["clave"] in cs for cs in tramos["claves"]))})
        if l.get("peaje_cercano"):
            rev.append({"tipo": "lugar junto a un peaje", "elemento": l["texto"],
                        "detalle": f"{l['peaje_cercano']}: si el lugar real queda al otro lado, cambia el peaje (confirmar coordenadas)",
                        "tramos_afectados": int(sum(l["clave"] in cs for cs in tramos["claves"]))})
    if urbanas:
        u = ubic[ubic["clave_plaza"].isin(urbanas)]
        rev.append({"tipo": "pórticos urbanos", "elemento": f"{len(u)} pórticos con código en porticos_urbanos.csv",
                    "detalle": f"{int(u['lat'].notna().sum())} ubicados en OpenStreetMap por su código; los demás no se pueden "
                               "detectar (y los pórticos OSM sin código quedan sin tarifa)",
                    "tramos_afectados": int(u["tramos_que_la_cruzan"].gt(0).sum())})
    for _, p in ubic[ubic["tiene_tarifa"] & ubic["lat"].isna() & ~ubic["clave_plaza"].isin(urbanas)].iterrows():
        rev.append({"tipo": "plaza con tarifa sin coordenadas", "elemento": f"{p['plaza']} — {p['concesion']}",
                    "detalle": "no se puede detectar en los recorridos: agregar lat y lon en plazas_manual.csv "
                               "(o ver plazas_sugeridas_osm.csv)", "tramos_afectados": math.nan})
    for _, p in ubic[(~ubic["tiene_tarifa"]) & (ubic["tramos_que_la_cruzan"] > 0)].iterrows():
        rev.append({"tipo": "plaza cruzada sin tarifa", "elemento": f"{p['plaza']} — {p['concesion']}",
                    "detalle": "aparece en los recorridos pero tabla_integrada no tiene su tarifa",
                    "tramos_afectados": p["tramos_que_la_cruzan"]})
    for _, s in (sugeridas.iterrows() if len(sugeridas) else []):
        rev.append({"tipo": "caseta OSM sin plaza", "elemento": f"{s['nombre']} ({s['tipo_osm']}, {s['lat']}, {s['lon']})",
                    "detalle": f"está en el recorrido (ej. {s['ejemplo']}) pero no corresponde a ninguna plaza de tabla_integrada",
                    "tramos_afectados": s["tramos_que_la_cruzan"]})
    cerca_tot = {}
    for (a, b), r in resultado_par.items():
        for k, d in r["cerca"].items():
            c = cerca_tot.setdefault(k, [math.inf, 0, f"{a} → {b}"])
            c[0], c[1] = min(c[0], d), c[1] + pares[(a, b)]
    for k, (d, n, ej) in cerca_tot.items():
        rev.append({"tipo": "plaza cerca de la ruta (no contada)", "elemento": etiqueta(k) + " — " + str(info_plaza.loc[k, "concesion"]),
                    "detalle": f"el punto MOP queda a {d:.0f} m del recorrido (ej. {ej}); si la ruta realmente pasa por ella, "
                               f"agregar sus coordenadas exactas", "tramos_afectados": n})
    for _, t in resumen.iterrows():
        if not pd.isna(t["dif_km_pct"]) and abs(t["dif_km_pct"]) > 100 * DIF_KM_ALERTA:
            rev.append({"tipo": "km distintos", "elemento": f"{t['sit_stretch_id']} {t['tramo']}",
                        "detalle": f"ERP {t['kilometers']:.0f} km vs carretera {t['km_ruta']:.0f} km ({t['dif_km_pct']:+.0f}%)",
                        "tramos_afectados": 1})
        elif str(t["estado"]).startswith("no calculado"):
            rev.append({"tipo": "tramo no calculado", "elemento": f"{t['sit_stretch_id']} {t['tramo']}", "detalle": t["estado"],
                        "tramos_afectados": 1})
    rev = pd.DataFrame(rev, columns=["tipo", "elemento", "detalle", "tramos_afectados"])
    rev = rev.sort_values(["tipo", "tramos_afectados"], ascending=[True, False], na_position="last")

    # guardar
    lugares.drop(columns=[c for c in lugares.columns if c.startswith("_")]).to_csv(salida / "lugares.csv", index=False, encoding="utf-8-sig")
    ubic.to_csv(salida / "plazas_ubicacion.csv", index=False, encoding="utf-8-sig")
    filas_pares.to_csv(salida / "pares_ruta.csv", index=False, encoding="utf-8-sig")
    puente.to_csv(salida / "puente_tramo_plaza.csv", index=False, encoding="utf-8-sig")
    resumen.to_csv(salida / "peaje_por_tramo.csv", index=False, encoding="utf-8-sig")
    (sugeridas if len(sugeridas) else pd.DataFrame(columns=["nombre", "concesion", "ruta", "km", "posicion", "lat", "lon"])
     ).to_csv(salida / "plazas_sugeridas_osm.csv", index=False, encoding="utf-8-sig")
    rev.to_csv(salida / "revisar_peajes.csv", index=False, encoding="utf-8-sig")

    # resumen en pantalla
    print(f"\nLugares: {lugares['precision'].value_counts().to_dict()}")
    estados = resumen["estado"].str.split(":").str[0].value_counts().to_dict()
    print(f"Tramos: {estados}")
    calc = resumen[resumen["peaje_clp"].notna()]
    if len(calc):
        print(f"Peaje por tramo (tarifas al {args.fecha}): mediana {pesos(calc['peaje_clp'].median())}, "
              f"máximo {pesos(calc['peaje_clp'].max())} | tramos sin ningún peaje: {int((calc['n_plazas'] == 0).sum())}")
    top = ubic.sort_values("tramos_que_la_cruzan", ascending=False).head(10)
    print("Plazas más cruzadas:")
    for _, p in top[top["tramos_que_la_cruzan"] > 0].iterrows():
        print(f"   {p['tramos_que_la_cruzan']:4d} tramos  {p['plaza']} ({p['posicion']}) — {p['concesion']}")
    print(f"Para revisar: {len(rev)} filas | archivos en {salida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
