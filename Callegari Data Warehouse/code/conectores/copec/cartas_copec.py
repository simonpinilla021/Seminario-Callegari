r"""
CartasCopec — lanzador único (se compila como CartasCopec.exe)
==============================================================
Transportes Callegari — Seminario BI

Al hacer doble clic muestra un menú. También acepta argumentos:
    CartasCopec.exe escritorio --desde 2022-01-01 --simular
    CartasCopec.exe web --desde 2022-01-01
    CartasCopec.exe eml --origen carpeta_con_eml
    CartasCopec.exe listar
    CartasCopec.exe procesar

Todo lo que genera queda JUNTO AL .exe:
    docs_combustible\            cartas PDF descargadas
    precios_combustible.db       base con precios, cartas y correos de origen
    salida_combustible\          CSV de resultados
    registro_descarga_*.csv      qué se revisó y qué se guardó

Los correos se leen en modo SOLO LECTURA (ver cada módulo).
"""

import sys
from datetime import date

# Importaciones explícitas: así PyInstaller incluye todo en el .exe
if __package__:
    from . import descargar_correos_copec as correo_eml
else:
    import descargar_correos_copec as correo_eml
if __package__:
    from . import descargar_outlook_copec as escritorio
else:
    import descargar_outlook_copec as escritorio
if __package__:
    from . import descargar_outlook_web_copec as web
else:
    import descargar_outlook_web_copec as web
if __package__:
    from . import extraer_precios_combustible as extractor
else:
    import extraer_precios_combustible as extractor
if __package__:
    from .rutas import base_dir
else:
    from rutas import base_dir

VERSION = "2026-10-19"

MENU = """
==============================================================
 Cartas de precios Copec — descarga (solo lectura) y extracción
 Versión {v} · Carpeta de trabajo: {d}
==============================================================
 1) Outlook de escritorio (el programa Outlook abierto en este PC)
 2) Outlook en el navegador (Microsoft 365, inicio de sesión con código)
 3) Archivos .eml de una carpeta (correos exportados a mano)
 4) Ver cuentas y carpetas de Outlook de escritorio (no descarga nada)
 5) Solo procesar los PDF que ya están en docs_combustible
 0) Salir
"""


def preguntar(texto, defecto=""):
    r = input(f"{texto}{f' [{defecto}]' if defecto else ''}: ").strip()
    return r or defecto


def pedir_fecha():
    while True:
        r = preguntar("Desde qué fecha (AAAA-MM-DD; Enter = automático, sigue desde la última carta)")
        if not r:
            return []
        try:
            date.fromisoformat(r)
            return ["--desde", r]
        except ValueError:
            print("   Fecha no válida. Ejemplo: 2022-01-01")


def pedir_simular():
    return ["--simular"] if preguntar("¿Solo simular, sin guardar nada? (s/N)", "N").lower().startswith("s") else []


def ejecutar(modulo, argumentos):
    sys.argv = [modulo.__name__] + argumentos
    modulo.main()


def menu():
    print(MENU.format(v=VERSION, d=base_dir()))
    op = preguntar("Opción", "1")
    if op == "1":
        args = pedir_fecha() + pedir_simular()
        cuenta = preguntar("Cuenta/buzón (Enter = la principal; ver opción 4)")
        ejecutar(escritorio, args + (["--cuenta", cuenta] if cuenta else []))
    elif op == "2":
        args = pedir_fecha() + pedir_simular()
        buzon = preguntar("Casilla compartida (Enter = la propia)")
        ejecutar(web, args + (["--buzon", buzon] if buzon else []))
    elif op == "3":
        origen = preguntar("Carpeta con los .eml", "correos")
        ejecutar(correo_eml, ["--modo", "eml", "--origen", origen])
    elif op == "4":
        ejecutar(escritorio, ["--listar"])
    elif op == "5":
        extractor.main(extractor.CARPETA_DOCS)
    else:
        print("Saliendo.")


def con_argumentos(argv):
    modo, resto = argv[0].lower(), argv[1:]
    if modo == "escritorio":
        ejecutar(escritorio, resto)
    elif modo == "web":
        ejecutar(web, resto)
    elif modo == "eml":
        ejecutar(correo_eml, ["--modo", "eml"] + resto)
    elif modo == "listar":
        ejecutar(escritorio, ["--listar"])
    elif modo == "procesar":
        extractor.main(extractor.CARPETA_DOCS)
    else:
        print(__doc__)


def principal():
    interactivo = len(sys.argv) == 1
    try:
        if interactivo:
            menu()
        else:
            con_argumentos(sys.argv[1:])
    except SystemExit as e:                     # los módulos terminan con sys.exit("mensaje")
        if not interactivo:
            raise
        if e.code not in (None, 0):
            print(f"\n{e.code}")
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
    except Exception as e:                      # error inesperado: mostrarlo antes de cerrar la ventana
        if not interactivo:
            raise
        import traceback
        traceback.print_exc()
        print(f"\nError inesperado: {e}")
    finally:
        if interactivo:
            input("\nPresione Enter para cerrar...")


if __name__ == "__main__":
    # Consolas Windows heredadas pueden usar cp1252 y no representar todos los signos.
    for salida in (sys.stdout,sys.stderr):
        if hasattr(salida,'reconfigure'): salida.reconfigure(errors='replace')
    principal()
