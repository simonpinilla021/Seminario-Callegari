"""
Descarga de cartas de precios Copec desde Outlook en el navegador (Microsoft 365)
================================================================================
Transportes Callegari — Seminario BI

Para cuentas de Microsoft 365 que se usan en outlook.office.com (sin Outlook de
escritorio). Usa Microsoft Graph, la API oficial de Microsoft.

Cómo se autentica (sin contraseña en el programa)
-------------------------------------------------
El programa muestra un código y una dirección (microsoft.com/devicelogin). El
gerente la abre en su navegador, escribe el código e inicia sesión como siempre
(con su doble factor si lo tiene). Microsoft le pregunta si autoriza el permiso
"Leer su correo" (Mail.Read). El programa recibe un acceso temporal (~1 hora)
que se guarda SOLO en memoria: al cerrar el programa desaparece.

SOLO LECTURA — garantías
------------------------
1. El permiso pedido es Mail.Read (o Mail.Read.Shared para la casilla grupal):
   Microsoft no permite borrar, mover, enviar ni marcar correos con ese permiso,
   aunque el código lo intentara.
2. Además, el programa solo hace peticiones GET: cualquier otro tipo de petición
   (POST, PATCH, DELETE...) lo detiene.
3. Solo guarda adjuntos PDF que, leídos por dentro, son cartas de granel o TCT.
   Del correo registra fecha, remitente, asunto e identificador; nunca el cuerpo.

Requisitos
----------
    pip install msal requests pdfplumber

Uso
---
    python descargar_outlook_web_copec.py --desde 2022-01-01 --simular
    python descargar_outlook_web_copec.py --desde 2022-01-01
    python descargar_outlook_web_copec.py --buzon buzon.combustible@empresa.cl --desde 2022-01-01
    python descargar_outlook_web_copec.py            # siguientes veces: sigue desde la última carta

Si la empresa bloquea la autorización ("necesita aprobación del administrador"),
TI debe registrar una aplicación en Azure con el permiso delegado Mail.Read y
entregar su "Id. de aplicación (cliente)"; se usa con --client-id.
"""

import argparse
import csv
import hashlib
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__:
    from . import descargar_outlook_copec as base
else:
    import descargar_outlook_copec as base

BASE_DIR = Path(__file__).resolve().parent
RUTA_LOG = BASE_DIR / "registro_descarga_outlook_web.csv"
GRAPH = "https://graph.microsoft.com/v1.0"
# Aplicación pública de Microsoft "Microsoft Graph Command Line Tools" (la misma que usa
# Graph PowerShell). Se puede reemplazar por una propia de la empresa con --client-id.
CLIENT_ID_DEFECTO = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTORIDAD = "https://login.microsoftonline.com/organizations"


# ------------------------------------------------------------------ acceso solo lectura
class GraphSoloLectura:
    """Cliente mínimo de Graph que SOLO permite GET."""

    def __init__(self, token: str):
        import requests
        self._s = requests.Session()
        self._s.headers["Authorization"] = f"Bearer {token}"

    def request(self, metodo, *a, **k):
        sys.exit(f"ABORTADO: el programa intentó una petición {metodo}; solo se permite leer.")

    def get(self, url, params=None, binario=False):
        for intento in range(6):
            r = self._s.get(url, params=params, timeout=60)
            if r.status_code in (429, 503, 504):              # Microsoft pide esperar
                time.sleep(int(r.headers.get("Retry-After", 5 * (intento + 1))))
                continue
            if r.status_code == 403:
                sys.exit("Sin permiso para leer ese buzón (403). Si es la casilla grupal, la cuenta que "
                         "inició sesión debe tener acceso a ella.")
            r.raise_for_status()
            return r.content if binario else r.json()
        r.raise_for_status()


def iniciar_sesion(client_id: str, compartido: bool) -> str:
    try:
        import msal
    except ImportError:
        sys.exit("Falta msal: pip install msal requests")
    alcances = ["Mail.Read.Shared" if compartido else "Mail.Read"]
    app = msal.PublicClientApplication(client_id, authority=AUTORIDAD)   # sin caché en disco
    flujo = app.initiate_device_flow(scopes=alcances)
    if "user_code" not in flujo:
        sys.exit(f"No se pudo iniciar el inicio de sesión: {flujo.get('error_description', flujo)}")
    print("\n" + flujo["message"] + "\n")                   # "Para iniciar sesión, abra ... e ingrese el código ..."
    res = app.acquire_token_by_device_flow(flujo)
    if "access_token" not in res:
        desc = res.get("error_description", "")
        if "AADSTS65001" in desc or "consent" in desc.lower() or "admin" in desc.lower():
            sys.exit("La empresa exige aprobación de un administrador para este permiso. "
                     "Pide a TI una aplicación con Mail.Read y usa --client-id.\n" + desc)
        sys.exit(f"No se pudo iniciar sesión: {desc}")
    print(f"Sesión iniciada como {res.get('id_token_claims', {}).get('preferred_username', '?')} "
          f"(permiso: {', '.join(alcances)}, solo lectura)\n")
    return res["access_token"]


def mensajes(g: GraphSoloLectura, buzon: str, desde: datetime, hasta):
    """Correos con adjuntos del buzón (todas las carpetas), del más nuevo al más antiguo."""
    raiz = f"{GRAPH}/users/{buzon}" if buzon else f"{GRAPH}/me"
    filtro = f"hasAttachments eq true and receivedDateTime ge {desde:%Y-%m-%dT%H:%M:%SZ}"
    if hasta:
        filtro += f" and receivedDateTime le {hasta:%Y-%m-%dT%H:%M:%SZ}"
    url, params = f"{raiz}/messages", {
        "$filter": filtro, "$orderby": "receivedDateTime desc", "$top": "100",
        "$select": "id,subject,from,receivedDateTime,internetMessageId"}
    while url:
        datos = g.get(url, params=params)
        yield from datos.get("value", [])
        url, params = datos.get("@odata.nextLink"), None
    return raiz


def main():
    ap = argparse.ArgumentParser(description="Descarga (solo lectura) cartas Copec desde Outlook web (Microsoft 365)")
    ap.add_argument("--buzon", help="correo de una casilla compartida (p. ej. grupo.combustible@...); por defecto, el propio")
    ap.add_argument("--desde", type=date.fromisoformat, default=None)
    ap.add_argument("--hasta", type=date.fromisoformat, default=None)
    ap.add_argument("--dominios", default=",".join(base.DOMINIOS_COPEC))
    ap.add_argument("--incluir-reenvios", action="store_true")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--solo-descargar", action="store_true")
    ap.add_argument("--client-id", default=CLIENT_ID_DEFECTO)
    args = ap.parse_args()

    dominios = [d.strip().lower() for d in args.dominios.split(",") if d.strip()]
    if args.desde is None:
        with base.conectar_db() as c0:
            ult = c0.execute("SELECT MAX(fecha_hora_correo) FROM correo_adjunto").fetchone()[0]
        args.desde = (datetime.fromisoformat(ult).date() - timedelta(days=15)) if ult else date(2022, 1, 1)
        print(f"Desde automático: {args.desde} (última carta descargada: {ult or 'ninguna'})")
    desde = datetime.combine(args.desde, datetime.min.time())
    hasta = datetime.combine(args.hasta, datetime.max.time()) if args.hasta else None

    g = GraphSoloLectura(iniciar_sesion(args.client_id, compartido=bool(args.buzon)))
    raiz = f"{GRAPH}/users/{args.buzon}" if args.buzon else f"{GRAPH}/me"

    base.CARPETA_DOCS.mkdir(exist_ok=True)
    con = base.conectar_db()
    tmp = Path(tempfile.mkdtemp(prefix="copec_web_"))
    nuevo_log = not RUTA_LOG.exists()
    log = open(RUTA_LOG, "a", newline="", encoding="utf-8-sig")
    w = csv.writer(log)
    if nuevo_log:
        w.writerow(["fecha_ejecucion", "fecha_correo", "remitente", "asunto", "adjunto", "resultado"])
    ahora = datetime.now().isoformat(timespec="seconds")
    cont = {"revisados": 0, "correos": 0, "guardados": 0, "duplicados": 0, "no_carta": 0}

    try:
        for m in mensajes(g, args.buzon, desde, hasta):
            cont["revisados"] += 1
            remitente = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
            asunto = m.get("subject") or ""
            de_copec = base.dominio_permitido(remitente, dominios)
            reenvio = args.incluir_reenvios and any(p in asunto.lower() for p in base.PALABRAS_ASUNTO)
            if not (de_copec or reenvio):
                continue
            cont["correos"] += 1
            recibido = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            adjuntos = g.get(f"{raiz}/messages/{m['id']}/attachments",
                             params={"$select": "id,name,contentType,size"}).get("value", [])
            for a in adjuntos:
                if a.get("@odata.type") != "#microsoft.graph.fileAttachment" or not str(a.get("name", "")).lower().endswith(".pdf"):
                    continue
                archivo = base.nombre_seguro(a["name"])
                temporal = tmp / archivo
                temporal.write_bytes(g.get(f"{raiz}/messages/{m['id']}/attachments/{a['id']}/$value", binario=True))
                tipo = base.es_carta_copec(temporal)
                if not tipo:
                    temporal.unlink(missing_ok=True)
                    cont["no_carta"] += 1
                    w.writerow([ahora, recibido, remitente, asunto[:80], archivo, "descartado (no es carta Copec)"])
                    continue
                h = hashlib.sha256(temporal.read_bytes()).hexdigest()
                if con.execute("SELECT 1 FROM correo_adjunto WHERE hash_archivo=?", (h,)).fetchone():
                    temporal.unlink(missing_ok=True)
                    cont["duplicados"] += 1
                    w.writerow([ahora, recibido, remitente, asunto[:80], archivo, "duplicado"])
                    continue
                destino = base.CARPETA_DOCS / f"{recibido:%Y%m%d_%H%M}_{tipo}_{archivo}"
                if args.simular:
                    temporal.unlink(missing_ok=True)
                    cont["guardados"] += 1
                    print(f"   [simular] {destino.name}")
                    w.writerow([ahora, recibido, remitente, asunto[:80], archivo, "simulado"])
                    continue
                shutil.move(str(temporal), destino)
                con.execute("INSERT INTO correo_adjunto VALUES (?,?,?,?,?,?,?)",
                            (h, m.get("internetMessageId") or m["id"], remitente, asunto,
                             recibido.isoformat(timespec="seconds"), destino.name, ahora))
                con.commit()
                cont["guardados"] += 1
                w.writerow([ahora, recibido, remitente, asunto[:80], archivo, f"guardado ({tipo})"])
                print(f"   [+] {destino.name}")
            if cont["revisados"] % 500 == 0:
                print(f"   ... {cont['revisados']} correos con adjuntos revisados")
    finally:
        log.close()
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nCorreos con adjuntos en el rango: {cont['revisados']} | de Copec: {cont['correos']} | "
          f"cartas nuevas: {cont['guardados']} | duplicadas: {cont['duplicados']} | PDF descartados: {cont['no_carta']}")
    print(f"Registro: {RUTA_LOG.name}")
    if not args.simular and not args.solo_descargar and cont["guardados"]:
        if __package__:
            from . import extraer_precios_combustible as ext
        else:
            import extraer_precios_combustible as ext
        ext.main(base.CARPETA_DOCS)


if __name__ == "__main__":
    main()
