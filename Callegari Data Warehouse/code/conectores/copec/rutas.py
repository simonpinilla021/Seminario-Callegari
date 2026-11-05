"""Rutas que funcionan igual ejecutando el .py o el .exe (PyInstaller)."""
import sys
from pathlib import Path


def base_dir() -> Path:
    """Carpeta de trabajo: junto al .exe, o junto a los .py. Ahí quedan los PDF, la base y los registros."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def recurso(nombre: str) -> Path:
    """Archivo empaquetado dentro del .exe (o junto a los .py)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / nombre
    return Path(__file__).resolve().parent / nombre
