"""
Descarga de cartas de precios Copec desde el correo — Transportes Callegari
===========================================================================

Busca en el buzón los correos de Copec, guarda sus PDF adjuntos en
docs_combustible/ y luego ejecuta extraer_precios_combustible.py.

Modos
-----
  eml   Lee archivos .eml de una carpeta (correos exportados desde Outlook,
        Gmail, etc.). Sirve para probar SIN acceso al buzón.
  imap  Se conecta al buzón por IMAP en modo SOLO LECTURA.

Seguridad
---------
- La contraseña NUNCA va en el código ni en config_correo.ini: se lee de la
  variable de entorno CORREO_PASSWORD o se pide por teclado (no se muestra).
- El buzón se abre en solo lectura (EXAMINE) y los mensajes se leen con
  BODY.PEEK: no se marcan como leídos, no se mueven, no se borran.
- Solo se procesan correos cuyo remitente termina en un dominio permitido
  (por defecto copec.cl) y solo se guardan adjuntos .pdf que empiecen con
  la firma real de un PDF (%PDF). Nombres de archivo saneados.
- Lo único que se guarda del correo es: message-id, remitente, asunto y
  fecha. No se guarda el cuerpo.

Uso
---
    python descargar_correos_copec.py --modo eml --origen correos_prueba
    python descargar_correos_copec.py --modo imap
    python descargar_correos_copec.py --modo imap --desde 2026-01-01 --solo-descargar
"""

import argparse
import configparser
import email
import getpass
import hashlib
import imaplib
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUTA_CONFIG = BASE_DIR / "config_correo.ini"
CARPETA_DOCS = BASE_DIR / "docs_combustible"
RUTA_DB = BASE_DIR / "precios_combustible.db"

CONFIG_EJEMPLO = """\
; Configuración del lector de correo. NO poner la contraseña aquí.
[imap]
servidor = outlook.office365.com   ; Gmail/Workspace: imap.gmail.com
puerto = 993
usuario = usuario@empresa.cl
carpeta = INBOX

[filtro]
dominios_remitente = copec.cl
; días hacia atrás cuando no se indica --desde
dias_atras = 30
"""


# ------------------------------------------------------------------ utilidades
def decodificar(valor) -> str:
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:
        return str(valor)


def nombre_seguro(nombre: str) -> str:
    nombre = Path(nombre.replace("\\", "/")).name          # sin rutas
    nombre = re.sub(r"[^\w.\- ]", "_", nombre).strip(" .")
    return nombre[:120] or "adjunto.pdf"


def remitente_permitido(remitente: str, dominios: list[str]) -> bool:
    dom = parseaddr(remitente)[1].lower().rsplit("@", 1)[-1]
    return any(dom == d or dom.endswith("." + d) for d in dominios)


def cargar_config() -> configparser.ConfigParser:
    if not RUTA_CONFIG.exists():
        RUTA_CONFIG.write_text(CONFIG_EJEMPLO, encoding="utf-8")
        print(f"Se creó {RUTA_CONFIG.name}. Revísalo (servidor y usuario) y vuelve a ejecutar.")
        sys.exit(0)
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";",))
    cfg.read(RUTA_CONFIG, encoding="utf-8")
    return cfg


def conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS correo_adjunto (
        hash_archivo TEXT PRIMARY KEY, message_id TEXT, remitente TEXT, asunto TEXT,
        fecha_hora_correo TEXT, archivo_guardado TEXT, fecha_descarga TEXT)""")
    return con


# ------------------------------------------------------- procesar un mensaje
def procesar_mensaje(msg, con, dominios) -> int:
    """Guarda los PDF de un mensaje. Devuelve cuántos adjuntos nuevos guardó."""
    remitente = decodificar(msg.get("From"))
    if not remitente_permitido(remitente, dominios):
        return 0
    asunto = decodificar(msg.get("Subject"))
    message_id = (msg.get("Message-ID") or "").strip()
    try:
        fecha = parsedate_to_datetime(msg.get("Date"))
        fecha_txt = fecha.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        prefijo = fecha.astimezone().strftime("%Y%m%d_%H%M")
    except Exception:
        fecha_txt, prefijo = None, "sinfecha"

    guardados = 0
    for parte in msg.walk():
        nombre = decodificar(parte.get_filename())
        if not nombre.lower().endswith(".pdf"):
            continue
        datos = parte.get_payload(decode=True) or b""
        if not datos.startswith(b"%PDF"):
            print(f"   [!] '{nombre}' dice ser PDF pero no lo es; se ignora")
            continue
        h = hashlib.sha256(datos).hexdigest()
        if con.execute("SELECT 1 FROM correo_adjunto WHERE hash_archivo=?", (h,)).fetchone():
            continue
        destino = CARPETA_DOCS / f"{prefijo}_{nombre_seguro(nombre)}"
        destino.write_bytes(datos)
        con.execute("INSERT INTO correo_adjunto VALUES (?,?,?,?,?,?,?)",
                    (h, message_id, parseaddr(remitente)[1], asunto, fecha_txt,
                     destino.name, datetime.now().isoformat(timespec="seconds")))
        guardados += 1
        print(f"   [+] {destino.name}  (correo {fecha_txt}, '{asunto[:50]}')")
    return guardados


# ----------------------------------------------------------------- fuentes
def desde_eml(carpeta: Path, con, dominios) -> int:
    archivos = sorted(carpeta.glob("*.eml"))
    if not archivos:
        sys.exit(f"No hay archivos .eml en {carpeta}")
    total = 0
    for ruta in archivos:
        print(f"[eml] {ruta.name}")
        with open(ruta, "rb") as fh:
            total += procesar_mensaje(email.message_from_binary_file(fh), con, dominios)
    return total


def desde_imap(cfg, desde: date, con, dominios) -> int:
    servidor = cfg.get("imap", "servidor")
    usuario = cfg.get("imap", "usuario")
    password = os.environ.get("CORREO_PASSWORD") or getpass.getpass(
        f"Contraseña (o contraseña de aplicación) de {usuario}: ")

    print(f"Conectando a {servidor} como {usuario} (solo lectura)...")
    imap = imaplib.IMAP4_SSL(servidor, cfg.getint("imap", "puerto", fallback=993))
    try:
        imap.login(usuario, password)
        estado, _ = imap.select(cfg.get("imap", "carpeta", fallback="INBOX"), readonly=True)
        if estado != "OK":
            sys.exit("No se pudo abrir la carpeta del buzón")

        criterio_fecha = desde.strftime("%d-%b-%Y")      # IMAP exige meses en inglés
        uids = set()
        for d in dominios:
            estado, datos = imap.uid("SEARCH", None, "SINCE", criterio_fecha, "FROM", f'"{d}"')
            if estado == "OK" and datos[0]:
                uids.update(datos[0].split())
        print(f"{len(uids)} correo(s) de {', '.join(dominios)} desde {desde}")

        total = 0
        for uid in sorted(uids, key=int):
            estado, datos = imap.uid("FETCH", uid, "(BODY.PEEK[])")   # PEEK = no marca leído
            if estado != "OK" or not datos or not isinstance(datos[0], tuple):
                continue
            total += procesar_mensaje(email.message_from_bytes(datos[0][1]), con, dominios)
        return total
    except imaplib.IMAP4.error as e:
        sys.exit(f"Error IMAP: {e}\n"
                 "Si es Microsoft 365 o Google, probablemente la cuenta exige OAuth o una "
                 "contraseña de aplicación; ver notas en el README.")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Descarga cartas Copec desde el correo")
    ap.add_argument("--modo", choices=["eml", "imap"], required=True)
    ap.add_argument("--origen", type=Path, help="carpeta con .eml (modo eml)")
    ap.add_argument("--desde", type=date.fromisoformat, help="AAAA-MM-DD (modo imap)")
    ap.add_argument("--solo-descargar", action="store_true",
                    help="no ejecutar el extractor después")
    args = ap.parse_args()

    cfg = cargar_config()
    dominios = [d.strip().lower() for d in
                cfg.get("filtro", "dominios_remitente", fallback="copec.cl").split(",")]
    CARPETA_DOCS.mkdir(exist_ok=True)
    con = conectar_db()

    if args.modo == "eml":
        if not args.origen:
            sys.exit("En modo eml indica --origen <carpeta>")
        nuevos = desde_eml(args.origen, con, dominios)
    else:
        desde = args.desde or date.today() - timedelta(days=cfg.getint("filtro", "dias_atras", fallback=30))
        nuevos = desde_imap(cfg, desde, con, dominios)
    con.commit()
    con.close()
    print(f"\n{nuevos} PDF nuevo(s) guardado(s) en {CARPETA_DOCS.name}/\n")

    if not args.solo_descargar:
        if __package__:
            from . import extraer_precios_combustible as ext
        else:
            import extraer_precios_combustible as ext
        ext.main(CARPETA_DOCS)


if __name__ == "__main__":
    main()
