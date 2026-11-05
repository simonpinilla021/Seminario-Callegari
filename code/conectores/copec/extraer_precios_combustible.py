"""
Extractor de cartas de precios de combustible Copec — Transportes Callegari
==========================================================================

Lee los PDF de la carpeta docs_combustible/, reconoce cada carta por su
CONTENIDO (granel o TCT, no por el nombre del archivo) y guarda el histórico
en una base SQLite (precios_combustible.db) más exportaciones CSV.

Documentos soportados
---------------------
- GRANEL ("Carta_precio..."): tabla completa por volumen de camión + IE F / IE V.
- TCT    ("Combustible..."):  precio final por región. Solo se guarda
                              Antofagasta (criterio del tarificador; pendiente
                              confirmar con la empresa). Cambiar REGIONES_TCT
                              para guardar más regiones.

Reglas
------
- Cada PDF se carga una sola vez (hash SHA-256).
- Controles de calidad: neto + IVA + IE = final ; IE F + IE V = IE total ;
  IE de granel = IE de TCT para la misma fecha de vigencia.
- Rectificaciones: para la misma fecha y fuente gana la carta emitida más
  tarde; las anteriores quedan con anulada = 1 (no se borran).
- Precio de granel a usar: el MÁS CARO de las filas de volumen (supuesto
  hasta saber qué camión cisterna se usa). Se guardan todas las filas.
- Costo real por litro = neto + IE x (1 - % recuperación IE). IVA fuera.

Uso
---
    pip install pdfplumber
    python extraer_precios_combustible.py            # usa ./docs_combustible
    python extraer_precios_combustible.py otra/carpeta
"""

import csv
import hashlib
import re
import sqlite3
import sys
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

import pdfplumber

# ----------------------------------------------------------------- parámetros
if __package__:
    from .rutas import base_dir, recurso  # noqa: E402
else:
    from rutas import base_dir, recurso  # noqa: E402
BASE_DIR = base_dir()
CARPETA_DOCS = BASE_DIR / "docs_combustible"
RUTA_DB = BASE_DIR / "precios_combustible.db"
CARPETA_CSV = BASE_DIR / "salida_combustible"

REGIONES_TCT = ["Antofagasta"]          # regiones TCT que se guardan
TASA_IVA = Decimal("0.19")              # para despejar el neto de TCT
PCT_RECUPERACION_IE = Decimal("0.31")   # Callegari recupera 31% (confirmar con contador)
TOLERANCIA = Decimal("0.0002")          # diferencia aceptada en los cuadres

NUM = r"-?[\d.]+,\d+-?"                 # número chileno, signo al inicio o al final


# ------------------------------------------------------------------ utilidades
def num_cl(s: str) -> Decimal:
    """'1.371,73138' -> 1371.73138 ; '310,30700-' -> -310.30700"""
    s = s.strip().replace("$", "")
    neg = s.startswith("-") or s.endswith("-")
    v = Decimal(s.strip("-").replace(".", "").replace(",", "."))
    return -v if neg else v


def texto_pdf(ruta: Path) -> str:
    with pdfplumber.open(ruta) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def hash_archivo(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def clasificar(texto: str) -> str:
    if "Camión.Vol" in texto or "P. Base" in texto:
        return "GRANEL"
    if re.search(r"\bTCT\b", texto):
        return "TCT"
    return "DESCONOCIDO"


class ErrorCarta(Exception):
    pass


def buscar(patron, texto, nombre, flags=0):
    m = re.search(patron, texto, flags)
    if not m:
        raise ErrorCarta(f"No se encontró '{nombre}' en el documento")
    return m


def leer_hora(texto: str):
    """'5:00 PM' → 17:00. Cartas antiguas usan '0:43AM' para las 00:43 (fuera del rango 1–12)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AP]M)", texto.strip().upper())
    if not m:
        raise ErrorCarta(f"Hora no reconocida: {texto!r}")
    h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if h > 12 or mi > 59:
        raise ErrorCarta(f"Hora no válida: {texto!r}")
    h = h % 12 + (12 if ampm == "PM" else 0)     # 12AM→0, 0AM→0, 12PM→12, 5PM→17
    return time(h, mi)


# -------------------------------------------------------------------- parsers
def parse_granel(t: str) -> dict:
    fecha = datetime.strptime(
        buscar(r"Santiago,\s*(\d{2}\.\d{2}\.\d{4})", t, "fecha").group(1), "%d.%m.%Y"
    ).date()
    hora = leer_hora(buscar(r"^\s*(\d{1,2}:\d{2}\s*[AP]M)\s*$", t, "hora", re.M).group(1))
    mail = re.search(r"Mail:\s*(\S+@\S+)", t)
    ie_f = num_cl(buscar(rf"IE F:\s*\$?({NUM})", t, "IE F").group(1))
    ie_v = num_cl(buscar(rf"IE V:\s*\$?({NUM})", t, "IE V").group(1))

    filas = []
    patron = (rf"^(.+?)\s+(\d{{6}})\s+(PETROLEO.+?)\s+"
              rf"({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
    for m in re.finditer(patron, t, re.M):
        loc, sol, prod, *n = m.groups()
        vol, neto, fepp, afecto, iva, ie, final = map(num_cl, n)
        filas.append(dict(localidad=loc.strip(), solicitante=sol, producto=prod.strip(),
                          volumen_camion_l=vol, precio_neto=neto, fepp=fepp,
                          monto_afecto_iva=afecto, iva=iva, ie_total=ie,
                          precio_final=final, ie_fijo=ie_f, ie_variable=ie_v))
    if not filas:
        raise ErrorCarta("La carta de granel no tiene filas de precios legibles")

    return dict(
        fuente="GRANEL",
        fecha_vigencia=fecha,
        n_carta=buscar(r"N°\s*(\d+)", t, "N° carta").group(1),
        hora_emision=hora,
        destinatario=mail.group(1) if mail else None,
        es_rectificacion="rectifica" in t.lower(),
        ie_fijo=ie_f, ie_variable=ie_v, ie_total=ie_f + ie_v,
        filas=filas,
    )


def parse_tct(t: str) -> dict:
    fecha = datetime.strptime(
        buscar(r"Precios Vigentes\s*(\d{2}/\d{2}/\d{4})", t, "fecha").group(1), "%d/%m/%Y"
    ).date()
    ie = buscar(rf"Imp\. Fijo PD\s+({NUM})\s+Imp\. Var PD\s+({NUM})\s+"
                rf"Imp\. Específico PD\s+({NUM})", t, "impuesto específico")
    ie_f, ie_v, ie_t = (num_cl(ie.group(i)) for i in (1, 2, 3))

    precios = {}
    for reg, p in re.findall(rf"^Región\s+(.+?)\s+TCT\s+({NUM})\s*$", t, re.M):
        v = num_cl(p)
        if reg in precios and precios[reg] != v:
            raise ErrorCarta(f"Región {reg} con dos precios distintos: {precios[reg]} / {v}")
        precios[reg] = v

    filas = []
    for reg in REGIONES_TCT:
        if reg not in precios:
            raise ErrorCarta(f"La carta TCT no trae la región {reg}")
        final = precios[reg]
        neto = (final - ie_t) / (1 + TASA_IVA)       # deducido: la carta no lo trae
        filas.append(dict(localidad=reg, solicitante=None, producto="TCT",
                          volumen_camion_l=None, precio_neto=round(neto, 5), fepp=None,
                          monto_afecto_iva=round(neto, 5), iva=round(neto * TASA_IVA, 5),
                          ie_total=ie_t, precio_final=final, ie_fijo=ie_f, ie_variable=ie_v))

    return dict(fuente="TCT", fecha_vigencia=fecha, n_carta=None, hora_emision=None,
                destinatario=None, es_rectificacion="rectifica" in t.lower(),
                ie_fijo=ie_f, ie_variable=ie_v, ie_total=ie_t, filas=filas,
                regiones_en_documento=len(precios))


# -------------------------------------------------------- controles de calidad
def validar(carta: dict) -> list[str]:
    obs = []
    if abs(carta["ie_fijo"] + carta["ie_variable"] - carta["ie_total"]) > TOLERANCIA:
        obs.append("IE F + IE V no cuadra con IE total")
    for f in carta["filas"]:
        if abs(f["ie_total"] - carta["ie_total"]) > TOLERANCIA:
            obs.append(f"{f['localidad']}: IE de la fila distinto al desglose")
        if carta["fuente"] == "GRANEL":
            calc = f["precio_neto"] + f["iva"] + f["ie_total"]
            if abs(calc - f["precio_final"]) > TOLERANCIA:
                obs.append(f"{f['volumen_camion_l']} L: neto+IVA+IE={calc} ≠ final {f['precio_final']}")
            if abs(f["precio_neto"] * TASA_IVA - f["iva"]) > Decimal("0.01"):
                obs.append(f"{f['volumen_camion_l']} L: IVA no es 19% del neto")
    return obs


# --------------------------------------------------------------------- base
ESQUEMA = """
CREATE TABLE IF NOT EXISTS carta (
    id_carta            INTEGER PRIMARY KEY,
    fuente              TEXT NOT NULL,          -- GRANEL | TCT
    fecha_vigencia      TEXT NOT NULL,
    n_carta             TEXT,
    hora_emision        TEXT,
    fecha_hora_correo   TEXT,                   -- se llenará al leer el correo
    destinatario        TEXT,
    es_rectificacion    INTEGER NOT NULL,
    anulada             INTEGER NOT NULL DEFAULT 0,
    ie_fijo             REAL, ie_variable REAL, ie_total REAL,
    archivo_origen      TEXT NOT NULL,
    hash_archivo        TEXT NOT NULL UNIQUE,
    observaciones       TEXT,
    fecha_carga         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS precio_combustible (
    id_precio           INTEGER PRIMARY KEY,
    id_carta            INTEGER NOT NULL REFERENCES carta(id_carta),
    fuente              TEXT NOT NULL,
    fecha_vigencia      TEXT NOT NULL,
    localidad           TEXT,                   -- localidad granel o región TCT
    solicitante         TEXT,
    producto            TEXT,
    volumen_camion_l    REAL,                   -- NULL en TCT
    precio_neto         REAL,                   -- en TCT es deducido
    neto_deducido       INTEGER NOT NULL,
    fepp                REAL,
    monto_afecto_iva    REAL,
    iva                 REAL,
    ie_total            REAL,
    ie_fijo             REAL,
    ie_variable         REAL,
    precio_final        REAL
);
-- La llena descargar_correos_copec.py: de qué correo salió cada PDF.
CREATE TABLE IF NOT EXISTS correo_adjunto (
    hash_archivo        TEXT PRIMARY KEY,
    message_id          TEXT,
    remitente           TEXT,
    asunto              TEXT,
    fecha_hora_correo   TEXT,
    archivo_guardado    TEXT,
    fecha_descarga      TEXT
);
CREATE TABLE IF NOT EXISTS parametro (
    nombre TEXT, valor REAL, vigente_desde TEXT, vigente_hasta TEXT, nota TEXT
);

-- Precio vigente por fuente y fecha: carta no anulada; en granel la fila más cara.
DROP VIEW IF EXISTS v_costo_combustible;
CREATE VIEW v_costo_combustible AS
WITH vigentes AS (
    SELECT p.*, ROW_NUMBER() OVER (
             PARTITION BY p.fuente, p.fecha_vigencia
             ORDER BY p.precio_final DESC) AS rk
    FROM precio_combustible p JOIN carta c USING (id_carta)
    WHERE c.anulada = 0
),
pct AS (SELECT valor FROM parametro WHERE nombre = 'pct_recuperacion_ie'
        ORDER BY vigente_desde DESC LIMIT 1)
SELECT v.fuente,
       v.fecha_vigencia                                         AS vigente_desde,
       (SELECT MIN(c2.fecha_vigencia) FROM carta c2
         WHERE c2.fuente = v.fuente AND c2.anulada = 0
           AND c2.fecha_vigencia > v.fecha_vigencia)            AS vigente_hasta,
       v.localidad, v.volumen_camion_l,
       v.precio_final                                           AS precio_facturado_l,
       ROUND(v.precio_neto + v.ie_total, 5)                     AS desembolso_neto_iva_l,
       ROUND(v.precio_neto + v.ie_total * (1 - pct.valor), 5)   AS costo_real_l,
       v.precio_neto, v.iva, v.ie_total
FROM vigentes v, pct
WHERE v.rk = 1;
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.executescript(ESQUEMA)
    if not con.execute("SELECT 1 FROM parametro WHERE nombre='pct_recuperacion_ie'").fetchone():
        con.execute("INSERT INTO parametro VALUES ('pct_recuperacion_ie', ?, '2000-01-01', NULL,"
                    " 'Recuperación IE diésel transporte de carga, tramo empresa grande. Confirmar con contador')",
                    (float(PCT_RECUPERACION_IE),))
    return con


def guardar(con, carta, ruta: Path, h: str, obs: list[str]) -> int:
    cur = con.execute(
        """INSERT INTO carta (fuente, fecha_vigencia, n_carta, hora_emision, destinatario,
               es_rectificacion, ie_fijo, ie_variable, ie_total, archivo_origen,
               hash_archivo, observaciones, fecha_carga)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (carta["fuente"], carta["fecha_vigencia"].isoformat(), carta["n_carta"],
         carta["hora_emision"].isoformat() if carta["hora_emision"] else None,
         carta["destinatario"], int(carta["es_rectificacion"]),
         float(carta["ie_fijo"]), float(carta["ie_variable"]), float(carta["ie_total"]),
         ruta.name, h, "; ".join(obs) or None, datetime.now().isoformat(timespec="seconds")))
    id_carta = cur.lastrowid
    for f in carta["filas"]:
        con.execute(
            """INSERT INTO precio_combustible (id_carta, fuente, fecha_vigencia, localidad,
                   solicitante, producto, volumen_camion_l, precio_neto, neto_deducido, fepp,
                   monto_afecto_iva, iva, ie_total, ie_fijo, ie_variable, precio_final)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (id_carta, carta["fuente"], carta["fecha_vigencia"].isoformat(), f["localidad"],
             f["solicitante"], f["producto"],
             *(float(f[k]) if f[k] is not None else None for k in ("volumen_camion_l", "precio_neto")),
             int(carta["fuente"] == "TCT"),
             *(float(f[k]) if f[k] is not None else None
               for k in ("fepp", "monto_afecto_iva", "iva", "ie_total", "ie_fijo",
                         "ie_variable", "precio_final"))))
    return id_carta


def resolver_rectificaciones(con):
    """Para cada fuente+fecha deja vigente solo la carta emitida más tarde."""
    con.execute("UPDATE carta SET anulada = 0")
    con.execute("""
        UPDATE carta SET anulada = 1 WHERE id_carta IN (
            SELECT id_carta FROM (
                SELECT id_carta, ROW_NUMBER() OVER (
                    PARTITION BY fuente, fecha_vigencia
                    ORDER BY COALESCE(fecha_hora_correo, '') DESC,
                             COALESCE(hora_emision, '') DESC,
                             CAST(COALESCE(n_carta, '0') AS INTEGER) DESC,
                             id_carta DESC) AS rk
                FROM carta) WHERE rk > 1)""")


def comparar_ie(con) -> list[str]:
    """IE F e IE V deben coincidir entre granel y TCT de la misma fecha."""
    alertas = []
    for fecha, n, f_min, f_max, v_min, v_max in con.execute("""
        SELECT fecha_vigencia, COUNT(DISTINCT fuente),
               MIN(ie_fijo), MAX(ie_fijo), MIN(ie_variable), MAX(ie_variable)
        FROM carta WHERE anulada = 0 GROUP BY fecha_vigencia"""):
        if n < 2:
            alertas.append(f"{fecha}: falta la carta {'TCT' if n else ''} o de granel para comparar IE")
        elif f_max - f_min > float(TOLERANCIA) or v_max - v_min > float(TOLERANCIA):
            alertas.append(f"{fecha}: IE F/IE V distintos entre granel y TCT")
    return alertas


def exportar_csv(con):
    CARPETA_CSV.mkdir(exist_ok=True)
    for nombre in ("carta", "precio_combustible", "v_costo_combustible"):
        cur = con.execute(f"SELECT * FROM {nombre}")
        with open(CARPETA_CSV / f"{nombre}.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=",")
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur)


# --------------------------------------------------------------------- main
def main(carpeta: Path):
    if not carpeta.is_dir():
        sys.exit(f"No existe la carpeta {carpeta}")
    con = conectar()
    nuevos = 0
    for ruta in sorted(carpeta.glob("*.pdf")):
        h = hash_archivo(ruta)
        if con.execute("SELECT 1 FROM carta WHERE hash_archivo = ?", (h,)).fetchone():
            print(f"[=] {ruta.name}: ya cargado")
            continue
        try:
            t = texto_pdf(ruta)
            tipo = clasificar(t)
            if tipo == "DESCONOCIDO":
                print(f"[?] {ruta.name}: no es carta de granel ni TCT, se omite")
                continue
            carta = parse_granel(t) if tipo == "GRANEL" else parse_tct(t)
            obs = validar(carta)
            if obs:
                print(f"[!] {ruta.name}: NO cargado, falla control de calidad:")
                for o in obs:
                    print("      -", o)
                continue
            guardar(con, carta, ruta, h, obs)
            nuevos += 1
            rect = " (RECTIFICACIÓN)" if carta["es_rectificacion"] else ""
            print(f"[+] {ruta.name}: {tipo} {carta['fecha_vigencia']}{rect}, "
                  f"{len(carta['filas'])} fila(s)")
        except ErrorCarta as e:
            print(f"[!] {ruta.name}: {e}")
        except Exception as e:                      # un PDF raro no debe detener el resto
            print(f"[!] {ruta.name}: error inesperado, se omite ({type(e).__name__}: {e})")

    # fecha/hora del correo (si el PDF vino por correo): desempata rectificaciones TCT
    con.execute("""UPDATE carta SET fecha_hora_correo = (
                       SELECT fecha_hora_correo FROM correo_adjunto a
                       WHERE a.hash_archivo = carta.hash_archivo)
                   WHERE fecha_hora_correo IS NULL""")
    resolver_rectificaciones(con)
    for a in comparar_ie(con):
        print("[!] IE:", a)
    con.commit()
    exportar_csv(con)

    print(f"\n{nuevos} carta(s) nuevas. Costo vigente por fuente:")
    for r in con.execute("""SELECT fuente, vigente_desde, localidad, volumen_camion_l,
                                   precio_facturado_l, costo_real_l
                            FROM v_costo_combustible ORDER BY vigente_desde, fuente"""):
        vol = f"{r[3]:,.0f} L" if r[3] else "-"
        print(f"  {r[1]}  {r[0]:<6} {r[2]:<12} {vol:>9}  facturado {r[4]:>10.2f}  costo real {r[5]:>10.2f}")
    print(f"\nBase: {RUTA_DB}\nCSV:  {CARPETA_CSV}")
    con.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else CARPETA_DOCS)
