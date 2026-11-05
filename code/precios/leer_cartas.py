#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lector de cartas de precio de Copec (granel y TCT) para el MVP Data Warehouse.

Lee los PDF de precios/cartas_copec/pdf/ y deja el resultado en precios/cartas_out/ (CSV), que luego carga
construir_dw_precios.py en el esquema `precios` de PostgreSQL. No necesita internet ni la base de datos.

  python leer_cartas.py                                   # lee los PDF que ya están en cartas_copec/pdf
  python leer_cartas.py --importar "..\\..\\Costos - Correo MVP\\CartasCopec"
        # antes de leer, copia los PDF nuevos (por contenido) desde la carpeta del descargador CartasCopec.exe
        # y agrega sus datos de correo (remitente, fecha, asunto) a cartas_copec/correos.csv

Es la lógica de extraer_precios_combustible.py (probada con las cartas del 01.10.2026) más lo aprendido con
las 453 cartas reales 2021–2026:
  - La vigencia se toma de "a partir de las cero horas del día ..." (no de la fecha de emisión).
  - Copec manda la MISMA carta (mismo N°) a varios destinatarios: cada copia es un PDF distinto (hash distinto)
    con los mismos precios. Se guarda una carta por N° y las demás quedan como copias.
  - El IVA se calcula sobre el "monto afecto a IVA", que en 2021 era neto + IE (no el neto).
  - Además de La Serena (base), la carta trae hasta 2026-05-16 una fila sin volumen del solicitante 641073
    ("ANTOFAGASTA" hasta 2023, "COQUIMBO" después, producto ULTRA GB). Es el precio de ruta (TCT) que usaba el
    tarificador ("precio antofagasta"): continúa sin salto en la carta TCT que llega aparte desde 2026-05-21.
    Se marca es_tct_historico = S y sirve de precio TCT antes de que existiera la carta TCT.
  - Horas "0:43AM" (= 00:43).

Salidas (cartas_out/):
  archivos.csv    un PDF: tipo, carta a la que pertenece, estado (cargada, copia, rechazada, no_carta), correo
  cartas.csv      una carta única: fuente, N°, emisión, vigencia, destinatarios, rectificación, anulada, IE
  lineas.csv      una línea de precio: localidad, volumen, neto, IVA, IE, final; es_base y es_elegida
  informe_validacion.txt
"""
import argparse
import csv
import hashlib
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

AQUI = Path(__file__).resolve().parent
CARPETA_PDF = AQUI / "cartas_copec" / "pdf"
CORREOS = AQUI / "cartas_copec" / "correos.csv"
SALIDA = AQUI / "cartas_out"
VERSION = "2026-10-24"

# ----------------------------------------------------------------- reglas de negocio (decididas, ver Precio del combustible.md §4)
SOLICITANTE_BASE = "631216"        # LA SERENA: filas de granel que valen para la base de Coquimbo
REGIONES_TCT = ["Antofagasta"]     # región TCT que se usa como costo (igual que el tarificador)
SOLICITANTE_TCT_HISTORICO = "641073"   # fila "ANTOFAGASTA/COQUIMBO … ULTRA GB" de la carta de granel: precio de ruta
                                       # hasta 2026-05-16; desde 2026-05-21 Copec lo manda en la carta TCT aparte
VOLUMEN_BASE = Decimal("33000")    # granel: la base compra en camión de 33.000 L (así lo usa el tarificador, 2026-10-24);
                                   # si la carta no trae esa fila, se usa la más cara del solicitante base
TASA_IVA = Decimal("0.19")
TOLERANCIA = Decimal("0.0002")     # cuadres en $/L
TOL_IVA = Decimal("0.01")

NUM = r"-?[\d.]+,\d+-?"            # número chileno, signo al inicio o al final
CAMPOS_CORREO = ["hash_archivo", "archivo", "message_id", "remitente", "asunto", "fecha_hora_correo", "origen"]


# ------------------------------------------------------------------ utilidades
def num_cl(s):
    """'1.371,73138' -> 1371.73138 ; '310,30700-' -> -310.30700"""
    s = s.strip().replace("$", "")
    neg = s.startswith("-") or s.endswith("-")
    v = Decimal(s.strip("-").replace(".", "").replace(",", "."))
    return -v if neg else v


def texto_pdf(ruta):
    import pdfplumber
    with pdfplumber.open(ruta) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def hash_archivo(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def clasificar(t):
    if "Camión.Vol" in t or "P. Base" in t:
        return "GRANEL"
    if re.search(r"\bTCT\b", t) and "Precios Vigentes" in t:
        return "TCT"
    return "DESCONOCIDO"


class ErrorCarta(Exception):
    pass


def buscar(patron, texto, nombre, flags=0):
    m = re.search(patron, texto, flags)
    if not m:
        raise ErrorCarta(f"no se encontró '{nombre}'")
    return m


def leer_hora(texto):
    """'5:00 PM' → 17:00; las cartas antiguas escriben '0:43AM' para las 00:43."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AP]M)", texto.strip().upper())
    if not m:
        raise ErrorCarta(f"hora no reconocida: {texto!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 12 or mi > 59:
        raise ErrorCarta(f"hora no válida: {texto!r}")
    return time(h % 12 + (12 if m.group(3) == "PM" else 0), mi)


# -------------------------------------------------------------------- lectura de cada formato
def parse_granel(t):
    emision = datetime.strptime(buscar(r"Santiago,\s*(\d{2}\.\d{2}\.\d{4})", t, "fecha de emisión").group(1), "%d.%m.%Y").date()
    m = re.search(r"cero horas del d[ií]a:?\s*(\d{2}\.\d{2}\.\d{4})", t)
    vigencia = datetime.strptime(m.group(1), "%d.%m.%Y").date() if m else emision
    hora = leer_hora(buscar(r"^\s*(\d{1,2}:\d{2}\s*[AP]M)\s*$", t, "hora", re.M).group(1))
    mail = re.search(r"Mail:\s*(\S+@\S+)", t)
    atencion = re.search(r"At\s*:\s*Sr\(a\)\.\s*(.+)", t)
    ie_f = num_cl(buscar(rf"IE F:\s*\$?({NUM})", t, "IE F").group(1))
    ie_v = num_cl(buscar(rf"IE V:\s*\$?({NUM})", t, "IE V").group(1))

    lineas = []
    # localidad · solicitante (6 dígitos) · producto · [volumen] · neto · FEPP · afecto · IVA · IE · final
    patron = rf"^([A-ZÁÉÍÓÚÑ ]+?)\s+(\d{{6}})\s+(PETROLEO.+?)\s+((?:{NUM}\s+){{5,6}}{NUM})\s*$"
    for m in re.finditer(patron, t, re.M):
        loc, sol, prod, numeros = m.groups()
        n = [num_cl(x) for x in re.findall(NUM, numeros)]
        vol = n.pop(0) if len(n) == 7 else None
        neto, fepp, afecto, iva, ie, final = n
        prod = re.sub(r"\s+N$", "", prod.strip())          # desde 2026 aparece una "N" suelta antes del volumen
        lineas.append(dict(localidad=loc.strip(), solicitante=sol, producto=prod, volumen_camion=vol,
                           neto=neto, neto_deducido="N", fepp=fepp, monto_afecto=afecto, iva=iva, ie=ie,
                           precio_final=final))
    if not lineas:
        raise ErrorCarta("sin filas de precio legibles")
    return dict(fuente="GRANEL", nro_carta=buscar(r"N°\s*(\d+)", t, "N° carta").group(1), fecha_emision=emision,
                hora_emision=hora, fecha_vigencia=vigencia, destinatario=(mail.group(1) if mail else ""),
                atencion=(atencion.group(1).strip() if atencion else ""),
                es_rectificacion="S" if "rectifica" in t.lower() else "N",
                ie_fijo=ie_f, ie_variable=ie_v, ie_total=ie_f + ie_v, lineas=lineas)


def parse_tct(t):
    m = (re.search(r"cero horas del\s*(\d{2}/\d{2}/\d{4})", t)
         or buscar(r"Precios Vigentes\s*(\d{2}/\d{2}/\d{4})", t, "fecha de vigencia"))
    vigencia = datetime.strptime(m.group(1), "%d/%m/%Y").date()
    ie = buscar(rf"Imp\. Fijo PD\s+({NUM})\s+Imp\. Var PD\s+({NUM})\s+Imp\. Específico PD\s+({NUM})", t,
                "impuesto específico")
    ie_f, ie_v, ie_t = (num_cl(ie.group(i)) for i in (1, 2, 3))
    precios = {}
    for reg, p in re.findall(rf"^Región\s+(.+?)\s+TCT\s+({NUM})\s*$", t, re.M):
        v = num_cl(p)
        if reg in precios and precios[reg] != v:
            raise ErrorCarta(f"región {reg} con dos precios distintos ({precios[reg]} y {v})")
        precios[reg] = v
    if not precios:
        raise ErrorCarta("sin regiones legibles")
    lineas = []
    for reg, final in sorted(precios.items()):
        neto = ((final - ie_t) / (1 + TASA_IVA)).quantize(Decimal("0.00001"))   # la carta no trae el neto
        lineas.append(dict(localidad=reg, solicitante="", producto="TCT", volumen_camion=None, neto=neto,
                           neto_deducido="S", fepp=None, monto_afecto=neto,
                           iva=(neto * TASA_IVA).quantize(Decimal("0.00001")), ie=ie_t, precio_final=final))
    return dict(fuente="TCT", nro_carta="", fecha_emision=vigencia, hora_emision=None, fecha_vigencia=vigencia,
                destinatario="", atencion="", es_rectificacion="S" if "rectifica" in t.lower() else "N",
                ie_fijo=ie_f, ie_variable=ie_v, ie_total=ie_t, lineas=lineas)


def validar(c):
    """Controles de calidad de una carta. Lista vacía = OK."""
    obs = []
    if abs(c["ie_fijo"] + c["ie_variable"] - c["ie_total"]) > TOLERANCIA:
        obs.append("IE F + IE V no cuadra con el IE total")
    for f in c["lineas"]:
        etiqueta = f"{f['localidad']} {f['volumen_camion'] or ''}".strip()
        if abs(f["ie"] - c["ie_total"]) > TOLERANCIA:
            obs.append(f"{etiqueta}: IE de la fila ({f['ie']}) distinto del desglose ({c['ie_total']})")
        if c["fuente"] == "GRANEL":
            calc = f["neto"] + f["iva"] + f["ie"]
            if abs(calc - f["precio_final"]) > TOLERANCIA:
                obs.append(f"{etiqueta}: neto+IVA+IE={calc} distinto del final {f['precio_final']}")
            if abs(f["monto_afecto"] * TASA_IVA - f["iva"]) > TOL_IVA:
                obs.append(f"{etiqueta}: IVA no es 19 % del monto afecto")
    if c["fuente"] == "GRANEL" and not any(f["solicitante"] == SOLICITANTE_BASE for f in c["lineas"]):
        obs.append(f"no trae filas del solicitante base {SOLICITANTE_BASE}")
    if c["fuente"] == "TCT" and not all(any(f["localidad"] == r for f in c["lineas"]) for r in REGIONES_TCT):
        obs.append(f"no trae la región {', '.join(REGIONES_TCT)}")
    return obs


def firma_precios(c):
    """Huella del contenido: dos PDF con la misma firma son la misma carta (copias a distintos destinatarios)."""
    partes = [c["fuente"], c["fecha_vigencia"].isoformat(), str(c["ie_total"])]
    partes += [f"{f['localidad']}|{f['solicitante']}|{f['volumen_camion']}|{f['precio_final']}" for f in c["lineas"]]
    return hashlib.sha256("§".join(partes).encode()).hexdigest()[:12]


# -------------------------------------------------------------------- correos (metadatos del descargador)
def leer_correos():
    if not CORREOS.exists():
        return {}
    with open(CORREOS, encoding="utf-8-sig", newline="") as fh:
        return {r["hash_archivo"]: r for r in csv.DictReader(fh)}


def guardar_correos(filas):
    CORREOS.parent.mkdir(parents=True, exist_ok=True)
    with open(CORREOS, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS_CORREO)
        w.writeheader()
        for r in sorted(filas.values(), key=lambda r: (r.get("fecha_hora_correo") or "", r["archivo"])):
            w.writerow({k: r.get(k, "") for k in CAMPOS_CORREO})


def importar(origen):
    """Copia a cartas_copec/pdf los PDF de `origen` (carpeta de CartasCopec.exe o su docs_combustible) que no estén
    ya (comparando contenido), y suma los datos de correo de su precios_combustible.db. No modifica el origen."""
    origen = Path(origen)
    docs = origen / "docs_combustible" if (origen / "docs_combustible").is_dir() else origen
    if not docs.is_dir():
        sys.exit(f"No existe {docs}")
    CARPETA_PDF.mkdir(parents=True, exist_ok=True)
    existentes = {hash_archivo(p) for p in CARPETA_PDF.glob("*.pdf")}
    correos = leer_correos()
    meta = {}
    for db in (origen / "precios_combustible.db", docs.parent / "precios_combustible.db"):
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)               # solo lectura
            for h, mid, rem, asu, fch, arch in con.execute(
                    "SELECT hash_archivo, message_id, remitente, asunto, fecha_hora_correo, archivo_guardado FROM correo_adjunto"):
                meta[h] = dict(hash_archivo=h, message_id=mid, remitente=rem, asunto=asu, fecha_hora_correo=fch,
                               archivo=arch, origen="correo")
            con.close()
            break
    nuevos = 0
    for p in sorted(docs.glob("*.pdf")):
        h = hash_archivo(p)
        if h not in correos:
            correos[h] = meta.get(h) or dict(hash_archivo=h, archivo=p.name, origen="archivo")
            correos[h]["archivo"] = p.name
        if h in existentes:
            continue
        destino = CARPETA_PDF / p.name
        if destino.exists():                                                   # mismo nombre, otro contenido
            destino = CARPETA_PDF / f"{p.stem}_{h[:8]}{p.suffix}"
            correos[h]["archivo"] = destino.name
        shutil.copy2(p, destino)
        existentes.add(h)
        nuevos += 1
    # correos registrados cuyo PDF no llegó (en la descarga de 2026-10-19 el segundo PDF del mismo minuto pisó al
    # primero): se dejan anotados como "sin_archivo" para saber que existen
    for h, r in meta.items():
        if h not in correos:
            correos[h] = dict(r, origen="sin_archivo")
    guardar_correos(correos)
    print(f"Importados {nuevos} PDF nuevos desde {docs} · correos registrados: {len(correos)}")


# -------------------------------------------------------------------- proceso principal
def leer_todo():
    archivos = sorted(CARPETA_PDF.glob("*.pdf"))
    if not archivos:
        sys.exit(f"No hay PDF en {CARPETA_PDF}")
    correos = leer_correos()
    reg_archivos, cartas = [], {}
    for p in archivos:
        h = hash_archivo(p)
        co = correos.get(h, {})
        fila = dict(archivo=p.name, hash_archivo=h, tipo="", id_carta="", estado="", motivo="",
                    remitente=co.get("remitente", ""), fecha_hora_correo=co.get("fecha_hora_correo", ""),
                    asunto=co.get("asunto", ""), message_id=co.get("message_id", ""))
        if not fila["fecha_hora_correo"]:                                    # respaldo: el nombre AAAAMMDD_HHMM
            m = re.match(r"(\d{8})_(\d{4})", p.name)
            if m:
                fila["fecha_hora_correo"] = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").isoformat()
        reg_archivos.append(fila)
        try:
            t = texto_pdf(p)
            tipo = clasificar(t)
            fila["tipo"] = tipo
            if tipo == "DESCONOCIDO":
                fila.update(estado="no_carta", motivo="no es carta de granel ni TCT")
                continue
            c = parse_granel(t) if tipo == "GRANEL" else parse_tct(t)
            obs = validar(c)
            if obs:
                fila.update(estado="rechazada", motivo="; ".join(obs))
                continue
        except ErrorCarta as e:
            fila.update(estado="rechazada", motivo=str(e))
            continue
        except Exception as e:                                                 # PDF dañado u otro formato
            fila.update(estado="rechazada", motivo=f"{type(e).__name__}: {e}")
            continue
        firma = firma_precios(c)
        clave = f"GRANEL-{c['nro_carta']}" if c["fuente"] == "GRANEL" else f"TCT-{c['fecha_vigencia']:%Y%m%d}-{firma}"
        if clave in cartas:
            previa = cartas[clave]
            if previa["firma"] != firma:
                fila.update(estado="rechazada", motivo=f"mismo N° {c['nro_carta']} que otra carta pero con precios distintos")
                continue
            previa["copias"] += 1
            previa["destinatarios"].add(c["destinatario"] or c["atencion"])
            if fila["fecha_hora_correo"] and fila["fecha_hora_correo"] < previa["fecha_hora_correo"]:
                previa["fecha_hora_correo"] = fila["fecha_hora_correo"]
            fila.update(id_carta=clave, estado="copia")
            continue
        c.update(id_carta=clave, firma=firma, copias=1, archivo=p.name, hash_archivo=h,
                 fecha_hora_correo=fila["fecha_hora_correo"], destinatarios={c["destinatario"] or c["atencion"]})
        cartas[clave] = c
        fila.update(id_carta=clave, estado="cargada")

    # rectificaciones: misma fuente y fecha de vigencia → gana la emitida más tarde; las demás quedan anuladas
    grupos = defaultdict(list)
    for c in cartas.values():
        grupos[(c["fuente"], c["fecha_vigencia"])].append(c)
    for lista in grupos.values():
        lista.sort(key=lambda c: (c["fecha_emision"], c["hora_emision"] or time(0), c["nro_carta"], c["fecha_hora_correo"]))
        for c in lista[:-1]:
            c.update(anulada="S", reemplazada_por=lista[-1]["id_carta"])
        lista[-1].update(anulada="N", reemplazada_por="")

    # línea elegida para el costo: granel = la de 33.000 L del solicitante base (antes: la más cara = 15.000 L, decisión 4
    # corregida el 2026-10-24); si no viene, la más cara. TCT = región elegida
    for c in cartas.values():
        base = [f for f in c["lineas"] if (f["solicitante"] == SOLICITANTE_BASE if c["fuente"] == "GRANEL"
                                          else f["localidad"] in REGIONES_TCT)]
        vol = [f for f in base if f["volumen_camion"] is not None and Decimal(str(f["volumen_camion"])) == VOLUMEN_BASE]
        elegida = vol[0] if (c["fuente"] == "GRANEL" and vol) else (max(base, key=lambda f: f["neto"]) if base else None)
        for f in c["lineas"]:
            f["es_base"] = "S" if f in base else "N"
            f["es_elegida"] = "S" if f is elegida else "N"
            f["es_tct_historico"] = "S" if c["fuente"] == "GRANEL" and f["solicitante"] == SOLICITANTE_TCT_HISTORICO else "N"
    return reg_archivos, cartas


def escribir(reg_archivos, cartas):
    SALIDA.mkdir(exist_ok=True)

    def w(nombre, filas, campos):
        with open(SALIDA / nombre, "w", encoding="utf-8", newline="") as fh:
            dw = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
            dw.writeheader()
            for f in filas:
                dw.writerow({k: ("" if f.get(k) is None else f.get(k)) for k in campos})

    w("archivos.csv", reg_archivos, ["archivo", "hash_archivo", "tipo", "id_carta", "estado", "motivo", "remitente",
                                     "fecha_hora_correo", "asunto", "message_id"])
    filas_c, filas_l = [], []
    for c in sorted(cartas.values(), key=lambda c: (c["fuente"], c["fecha_vigencia"], c["fecha_emision"], c["nro_carta"])):
        filas_c.append(dict(c, destinatarios="; ".join(sorted(d for d in c["destinatarios"] if d)),
                            hora_emision=c["hora_emision"].isoformat() if c["hora_emision"] else ""))
        for f in c["lineas"]:
            filas_l.append(dict(f, id_carta=c["id_carta"], fuente=c["fuente"]))
    w("cartas.csv", filas_c, ["id_carta", "fuente", "nro_carta", "fecha_emision", "hora_emision", "fecha_vigencia",
                              "fecha_hora_correo", "destinatarios", "copias", "es_rectificacion", "anulada",
                              "reemplazada_por", "ie_fijo", "ie_variable", "ie_total", "archivo", "hash_archivo", "firma"])
    w("lineas.csv", filas_l, ["id_carta", "fuente", "localidad", "solicitante", "producto", "volumen_camion", "neto",
                              "neto_deducido", "fepp", "monto_afecto", "iva", "ie", "precio_final", "es_base", "es_elegida",
                              "es_tct_historico"])
    return filas_c, filas_l


def informe(reg_archivos, cartas, pct=Decimal("0.31")):
    """Validación del conjunto: devuelve el texto del informe."""
    L = [f"Informe de validación de cartas Copec — leer_cartas.py {VERSION} — {datetime.now():%Y-%m-%d %H:%M}", ""]
    est = defaultdict(int)
    for a in reg_archivos:
        est[a["estado"]] += 1
    L.append(f"PDF leídos: {len(reg_archivos)} → " + ", ".join(f"{k} {v}" for k, v in sorted(est.items())))
    for a in reg_archivos:
        if a["estado"] in ("rechazada", "no_carta"):
            L.append(f"   [!] {a['archivo']}: {a['motivo']}")
    vig = [c for c in cartas.values() if c["anulada"] == "N"]
    granel_por_fecha = {c["fecha_vigencia"]: c for c in vig if c["fuente"] == "GRANEL"}

    def cartas_de(fecha):
        return granel_por_fecha[fecha]["lineas"]
    for fuente in ("GRANEL", "TCT"):
        cs = sorted((c for c in vig if c["fuente"] == fuente), key=lambda c: c["fecha_vigencia"])
        todas = [c for c in cartas.values() if c["fuente"] == fuente]
        if not cs:
            L.append(f"\n{fuente}: sin cartas")
            continue
        L.append(f"\n{fuente}: {len(todas)} cartas únicas ({sum(c['copias'] for c in todas)} PDF), "
                 f"{len(cs)} vigentes, {len(todas) - len(cs)} anuladas por rectificación; "
                 f"vigencias {cs[0]['fecha_vigencia']} → {cs[-1]['fecha_vigencia']}")
        # continuidad: huecos entre vigencias consecutivas
        huecos = [(a["fecha_vigencia"], b["fecha_vigencia"]) for a, b in zip(cs, cs[1:])
                  if (b["fecha_vigencia"] - a["fecha_vigencia"]).days > 9]
        L.append(f"   huecos de más de 9 días entre cartas: {len(huecos)}"
                 + "".join(f"\n      {a} → {b} ({(b - a).days} días)" for a, b in huecos))
        # saltos de precio semana a semana (costo real de la línea elegida)
        def costo(c):
            f = next(f for f in c["lineas"] if f["es_elegida"] == "S")
            return f["neto"] + f["ie"] * (1 - pct)
        saltos = [(a["fecha_vigencia"], b["fecha_vigencia"], costo(a), costo(b)) for a, b in zip(cs, cs[1:])
                  if abs(costo(b) / costo(a) - 1) > Decimal("0.08")]
        L.append(f"   saltos de costo real > 8 % entre cartas consecutivas: {len(saltos)}"
                 + "".join(f"\n      {a} → {b}: {x:,.2f} → {y:,.2f} ({(y / x - 1) * 100:+.1f} %)" for a, b, x, y in saltos))
        anuladas = [c for c in todas if c["anulada"] == "S"]
        for c in sorted(anuladas, key=lambda c: c["fecha_vigencia"]):
            g = cartas[c["reemplazada_por"]]
            L.append(f"   rectificación {c['fecha_vigencia']}: {c['id_carta']} ({c['hora_emision']}) "
                     f"→ {g['id_carta']} ({g['hora_emision']}), costo {costo(c):,.2f} → {costo(g):,.2f}")
        fuera = [c for c in todas if c["fecha_vigencia"] < c["fecha_emision"]]
        if fuera:
            L.append(f"   [!] {len(fuera)} cartas con vigencia anterior a su emisión")
    # TCT histórico: fila 641073 de las cartas de granel (antes de la carta TCT)
    hist = [(c["fecha_vigencia"], f) for c in vig if c["fuente"] == "GRANEL" for f in c["lineas"] if f["es_tct_historico"] == "S"]
    if hist:
        dif = [f["precio_final"] / next(g for g in cartas_de(c_f) if g["es_elegida"] == "S")["precio_final"] - 1
               for c_f, f in hist]
        L.append(f"\nTCT histórico (fila {SOLICITANTE_TCT_HISTORICO} de la carta de granel): {len(hist)} cartas, "
                 f"{min(d for d, _ in hist)} → {max(d for d, _ in hist)}; sobre granel en promedio "
                 f"{sum(dif) / len(dif) * 100:+.2f} % (mín {min(dif) * 100:+.2f} %, máx {max(dif) * 100:+.2f} %)")
    # IE: debe ser el mismo en granel y TCT de la misma fecha
    por_fecha = defaultdict(dict)
    for c in vig:
        por_fecha[c["fecha_vigencia"]][c["fuente"]] = c
    ambos = [(d, x) for d, x in sorted(por_fecha.items()) if len(x) == 2]
    distintos = [(d, x) for d, x in ambos if abs(x["GRANEL"]["ie_total"] - x["TCT"]["ie_total"]) > TOLERANCIA]
    L.append(f"\nIE granel vs TCT en la misma fecha: {len(ambos)} fechas comparables, {len(distintos)} con diferencia"
             + "".join(f"\n   {d}: granel {x['GRANEL']['ie_total']} · TCT {x['TCT']['ie_total']}" for d, x in distintos))
    # contraste con el valor conocido del tarificador (hoja petroleo del tarificador vigente al 15.10.2026: base 33.000 L)
    for fuente, esperado in (("GRANEL", Decimal("1230.43")), ("TCT", Decimal("1265.50"))):
        c = por_fecha.get(date(2026, 10, 15), {}).get(fuente)
        if c:
            f = next(f for f in c["lineas"] if f["es_elegida"] == "S")
            v = (f["neto"] + f["ie"] * (1 - pct)).quantize(Decimal("0.01"))
            L.append(f"Control 15.10.2026 {fuente}: costo real {v} (esperado {esperado}) "
                     + ("✔" if abs(v - esperado) <= Decimal("0.01") else "✘"))
    return "\n".join(L)


def principal(argv=None):
    ap = argparse.ArgumentParser(description="Lee las cartas Copec (PDF) y deja CSV para el DW.")
    ap.add_argument("--importar", help="carpeta de CartasCopec.exe (o su docs_combustible) de donde copiar PDF nuevos")
    a = ap.parse_args(argv)
    print(f"leer_cartas.py versión {VERSION}")
    if a.importar:
        importar(a.importar)
    reg, cartas = leer_todo()
    filas_c, filas_l = escribir(reg, cartas)
    texto = informe(reg, cartas)
    (SALIDA / "informe_validacion.txt").write_text(texto, encoding="utf-8")
    print(texto)
    print(f"\nSalida: {SALIDA} (cartas {len(filas_c)}, líneas {len(filas_l)})")


if __name__ == "__main__":
    principal()
