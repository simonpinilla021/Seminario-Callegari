"""
Descarga de cartas de precios Copec desde Outlook de escritorio (Windows)
=========================================================================
Transportes Callegari — Seminario BI

Lee los correos del Outlook que ya está ABIERTO en el computador (usa la sesión
del usuario: no pide ni guarda contraseña), guarda los PDF de las cartas de
Copec en docs_combustible/ y luego ejecuta extraer_precios_combustible.py.

SOLO LECTURA — garantías
------------------------
- El programa nunca llama a Delete, Move, Copy, Save, Send, Forward, Reply ni
  modifica propiedades (UnRead, FlagStatus, Categories...). Al iniciar se revisa
  a sí mismo y se detiene si encuentra alguna de esas llamadas en su código.
- No abre los correos en pantalla (no se marcan como leídos).
- Solo guarda adjuntos PDF que, leídos por dentro, son cartas de granel o TCT
  de Copec. Cualquier otro PDF se descarta sin quedar en disco.
- Del correo solo registra: fecha, remitente, asunto, carpeta e identificador.
  No guarda el cuerpo.

Requisitos
----------
    pip install pywin32 pdfplumber
    Outlook de escritorio abierto con la cuenta del gerente (o la casilla grupal).

Uso
---
    python descargar_outlook_copec.py --listar                 # cuentas y carpetas (no descarga nada)
    python descargar_outlook_copec.py --desde 2022-01-01 --simular   # muestra qué descargaría
    python descargar_outlook_copec.py --desde 2022-01-01       # descarga y extrae (primera vez: histórico)
    python descargar_outlook_copec.py                          # siguientes veces: sigue desde la última carta
    python descargar_outlook_copec.py --desde 2022-01-01 --cuenta "buzon.combustible@empresa.cl"
    python descargar_outlook_copec.py --desde 2022-01-01 --carpeta "Bandeja de entrada"
    python descargar_outlook_copec.py --desde 2022-01-01 --incluir-reenvios   # acepta reenvíos internos

Por defecto revisa TODAS las carpetas de correo de la cuenta predeterminada
(incluye subcarpetas y archivo), porque las cartas antiguas pueden estar movidas.
"""

import argparse
import csv
import hashlib
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CARPETA_DOCS = BASE_DIR / "docs_combustible"
RUTA_DB = BASE_DIR / "precios_combustible.db"
RUTA_LOG = BASE_DIR / "registro_descarga_outlook.csv"

DOMINIOS_COPEC = ["copec.cl"]
# Reenvíos internos (opción --incluir-reenvios): el asunto debe contener alguna de estas palabras
PALABRAS_ASUNTO = ["precio", "carta", "combustible", "copec", "vigente"]

OL_MAIL_ITEM = 43          # clase COM de un correo
OL_ATTACH_BY_VALUE = 1     # adjunto real (no vínculo)
OL_FOLDER_MAIL = 0         # carpeta de correo (no calendario, contactos...)
PR_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
PR_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"


# --------------------------------------------------------------- autoverificación
PROHIBIDO = [r"\.Delete\s*\(", r"\.Move\s*\(", r"\.Copy\s*\(", r"\.Save\s*\(", r"\.Send\s*\(",
             r"\.Forward\s*\(", r"\.Reply", r"\.UnRead\s*=", r"\.FlagStatus\s*=", r"\.Categories\s*=",
             r"\.Display\s*\(", r"\.PermanentDelete", r"\.MarkAsTask", r"\.ClearTaskFlag"]


def verificar_solo_lectura():
    """Se detiene si el propio código contiene una llamada que modifique el buzón."""
    codigo = Path(__file__).read_text(encoding="utf-8")
    codigo = codigo.rsplit("# --------------------------------------------------------------- fin autoverificación", 1)[1]
    lineas = [l.split("#")[0] for l in codigo.splitlines()]          # sin comentarios
    for patron in PROHIBIDO:
        for n, l in enumerate(lineas, 1):
            if re.search(patron, l):
                sys.exit(f"ABORTADO: se encontró una operación de escritura sobre el correo ({patron}) en la línea {n}.")
# --------------------------------------------------------------- fin autoverificación


def conectar_outlook():
    try:
        import win32com.client
    except ImportError:
        sys.exit("Falta pywin32: pip install pywin32  (solo funciona en Windows con Outlook de escritorio)")
    try:
        return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    except Exception as e:
        sys.exit(f"No se pudo conectar con Outlook ({e}). ¿Está instalado y abierto?")


def remitente_smtp(item) -> str:
    try:
        if item.SenderEmailType == "EX":                 # usuario de Exchange interno
            u = item.Sender.GetExchangeUser()
            if u is not None:
                return (u.PrimarySmtpAddress or "").lower()
            return (item.Sender.PropertyAccessor.GetProperty(PR_SMTP) or "").lower()
        return (item.SenderEmailAddress or "").lower()
    except Exception:
        return ""


def dominio_permitido(correo: str, dominios) -> bool:
    dom = correo.rsplit("@", 1)[-1]
    return any(dom == d or dom.endswith("." + d) for d in dominios)


def fecha_py(rt) -> datetime:
    return datetime(rt.year, rt.month, rt.day, rt.hour, rt.minute, rt.second)


def nombre_seguro(nombre: str) -> str:
    nombre = Path(str(nombre).replace("\\", "/")).name
    nombre = re.sub(r"[^\w.\- ]", "_", nombre).strip(" .")
    return nombre[:120] or "adjunto.pdf"


def carpetas_correo(carpeta, ruta=""):
    """Recorre la carpeta y sus subcarpetas de correo."""
    nombre = f"{ruta}/{carpeta.Name}" if ruta else carpeta.Name
    try:
        if carpeta.DefaultItemType == OL_FOLDER_MAIL:
            yield nombre, carpeta
    except Exception:
        pass
    try:
        for sub in carpeta.Folders:
            yield from carpetas_correo(sub, nombre)
    except Exception:
        pass


def elegir_cuenta(ns, cuenta):
    tiendas = list(ns.Folders)
    if not cuenta:
        return ns.GetDefaultFolder(6).Parent                 # 6 = Bandeja de entrada → su cuenta
    for t in tiendas:
        if t.Name.lower() == cuenta.lower():
            return t
    sys.exit(f"No encontré la cuenta '{cuenta}'. Disponibles: {[t.Name for t in tiendas]}")


def listar(ns):
    for t in ns.Folders:
        print(f"\nCUENTA: {t.Name}")
        for nombre, c in carpetas_correo(t):
            try:
                n = c.Items.Count
            except Exception:
                n = "?"
            print(f"   {nombre}  ({n} elementos)")


def es_carta_copec(ruta_pdf: Path) -> str:
    """Devuelve GRANEL, TCT o '' leyendo el contenido del PDF. Exige las marcas propias de cada carta."""
    if ruta_pdf.read_bytes()[:4] != b"%PDF":
        return ""
    try:
        if __package__:
            from . import extraer_precios_combustible as ext
        else:
            import extraer_precios_combustible as ext
        t = ext.texto_pdf(ruta_pdf)
    except Exception:
        return ""
    tipo = ext.clasificar(t)
    if tipo == "GRANEL" and "IE F:" in t and "Precio Final" in t:
        return "GRANEL"
    if tipo == "TCT" and "Precios Vigentes" in t and "Imp. Fijo PD" in t:
        return "TCT"
    return ""


def conectar_db():
    con = sqlite3.connect(RUTA_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS correo_adjunto (
        hash_archivo TEXT PRIMARY KEY, message_id TEXT, remitente TEXT, asunto TEXT,
        fecha_hora_correo TEXT, archivo_guardado TEXT, fecha_descarga TEXT)""")
    return con


# --------------------------------------------------------------------- principal
def main():
    verificar_solo_lectura()
    ap = argparse.ArgumentParser(description="Descarga (solo lectura) cartas Copec desde Outlook")
    ap.add_argument("--listar", action="store_true", help="muestra cuentas y carpetas y termina")
    ap.add_argument("--desde", type=date.fromisoformat, default=None,
                    help="AAAA-MM-DD. Si se omite: última carta ya descargada − 15 días (o 2022-01-01 la primera vez)")
    ap.add_argument("--dominios", default=",".join(DOMINIOS_COPEC),
                    help="dominios de remitente aceptados, separados por coma (por defecto copec.cl)")
    ap.add_argument("--hasta", type=date.fromisoformat, default=None)
    ap.add_argument("--cuenta", help="nombre de la cuenta/buzón tal como aparece en --listar")
    ap.add_argument("--carpeta", help="solo carpetas cuyo nombre contenga este texto")
    ap.add_argument("--incluir-reenvios", action="store_true",
                    help="acepta también correos internos reenviados si el asunto lo sugiere (el PDF igual se valida por contenido)")
    ap.add_argument("--simular", action="store_true", help="no guarda nada: solo informa qué descargaría")
    ap.add_argument("--solo-descargar", action="store_true", help="no ejecutar el extractor al final")
    args = ap.parse_args()

    ns = conectar_outlook()
    if args.listar:
        listar(ns)
        return

    raiz = elegir_cuenta(ns, args.cuenta)
    dominios = [d.strip().lower() for d in args.dominios.split(",") if d.strip()]
    if args.desde is None:
        with conectar_db() as c0:
            ult = c0.execute("SELECT MAX(fecha_hora_correo) FROM correo_adjunto").fetchone()[0]
        args.desde = (datetime.fromisoformat(ult).date() - timedelta(days=15)) if ult else date(2022, 1, 1)
        print(f"Desde automático: {args.desde} (última carta descargada: {ult or 'ninguna'})")
    desde = datetime.combine(args.desde, datetime.min.time())
    hasta = datetime.combine(args.hasta, datetime.max.time()) if args.hasta else None
    CARPETA_DOCS.mkdir(exist_ok=True)
    con = conectar_db()
    tmp = Path(tempfile.mkdtemp(prefix="copec_"))
    nuevo_log = not RUTA_LOG.exists()
    log = open(RUTA_LOG, "a", newline="", encoding="utf-8-sig")
    w = csv.writer(log)
    if nuevo_log:
        w.writerow(["fecha_ejecucion", "carpeta", "fecha_correo", "remitente", "asunto", "adjunto", "resultado"])
    ahora = datetime.now().isoformat(timespec="seconds")
    cont = {"correos": 0, "guardados": 0, "duplicados": 0, "no_carta": 0}

    print(f"Cuenta: {raiz.Name} | desde {desde.date()}" + (f" hasta {hasta.date()}" if hasta else "")
          + (" | SIMULACIÓN" if args.simular else ""))
    try:
        for nombre, carpeta in carpetas_correo(raiz):
            if args.carpeta and args.carpeta.lower() not in nombre.lower():
                continue
            try:
                items = carpeta.Items
                items.Sort("[ReceivedTime]", True)               # más nuevos primero (solo ordena la vista en memoria)
            except Exception:
                continue
            revisados = 0
            for item in items:
                try:
                    if item.Class != OL_MAIL_ITEM:
                        continue
                    recibido = fecha_py(item.ReceivedTime)
                except Exception:
                    continue
                if recibido < desde:
                    break                                        # el resto es más antiguo
                if hasta and recibido > hasta:
                    continue
                revisados += 1
                remitente = remitente_smtp(item)
                asunto = item.Subject or ""
                de_copec = dominio_permitido(remitente, dominios)
                reenvio = args.incluir_reenvios and any(p in asunto.lower() for p in PALABRAS_ASUNTO)
                if not (de_copec or reenvio):
                    continue
                try:
                    if item.Attachments.Count == 0:
                        continue
                except Exception:
                    continue
                cont["correos"] += 1
                try:
                    msg_id = item.PropertyAccessor.GetProperty(PR_MESSAGE_ID)
                except Exception:
                    msg_id = item.EntryID
                for i in range(1, item.Attachments.Count + 1):
                    adj = item.Attachments.Item(i)
                    try:
                        if adj.Type != OL_ATTACH_BY_VALUE or not adj.FileName.lower().endswith(".pdf"):
                            continue
                    except Exception:
                        continue
                    archivo = nombre_seguro(adj.FileName)
                    temporal = tmp / f"{i}_{archivo}"
                    adj.SaveAsFile(str(temporal))               # copia el adjunto a disco; el correo no cambia
                    tipo = es_carta_copec(temporal)
                    if not tipo:
                        temporal.unlink(missing_ok=True)         # PDF ajeno: no queda en disco
                        cont["no_carta"] += 1
                        w.writerow([ahora, nombre, recibido, remitente, asunto[:80], archivo, "descartado (no es carta Copec)"])
                        continue
                    h = hashlib.sha256(temporal.read_bytes()).hexdigest()
                    if con.execute("SELECT 1 FROM correo_adjunto WHERE hash_archivo=?", (h,)).fetchone():
                        temporal.unlink(missing_ok=True)
                        cont["duplicados"] += 1
                        w.writerow([ahora, nombre, recibido, remitente, asunto[:80], archivo, "duplicado"])
                        continue
                    destino = CARPETA_DOCS / f"{recibido:%Y%m%d_%H%M}_{tipo}_{archivo}"
                    if args.simular:
                        temporal.unlink(missing_ok=True)
                        print(f"   [simular] {destino.name}  ({nombre})")
                        w.writerow([ahora, nombre, recibido, remitente, asunto[:80], archivo, "simulado"])
                        cont["guardados"] += 1
                        continue
                    shutil.move(str(temporal), destino)
                    con.execute("INSERT INTO correo_adjunto VALUES (?,?,?,?,?,?,?)",
                                (h, msg_id, remitente, asunto, recibido.isoformat(timespec="seconds"),
                                 destino.name, ahora))
                    con.commit()
                    cont["guardados"] += 1
                    w.writerow([ahora, nombre, recibido, remitente, asunto[:80], archivo, f"guardado ({tipo})"])
                    print(f"   [+] {destino.name}")
            if revisados:
                print(f"{nombre}: {revisados} correo(s) en el rango")
    finally:
        log.close()
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nCorreos con adjuntos revisados: {cont['correos']} | cartas nuevas: {cont['guardados']} | "
          f"duplicadas: {cont['duplicados']} | PDF descartados: {cont['no_carta']}")
    print(f"Registro: {RUTA_LOG.name}")
    if not args.simular and not args.solo_descargar and cont["guardados"]:
        if __package__:
            from . import extraer_precios_combustible as ext
        else:
            import extraer_precios_combustible as ext
        ext.main(CARPETA_DOCS)


if __name__ == "__main__":
    main()
