"""Secretos locales cifrados con DPAPI (Windows/usuario actual); nunca se registran."""
import ctypes
from ctypes import wintypes
import base64
import json
import os
from pathlib import Path

ARCHIVO=Path(__file__).resolve().parents[2]/'.local/credenciales.json'


class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]


def proteger(datos,descifrar=False):
    if os.name!='nt':
        raise RuntimeError('El almacén cifrado requiere Windows; usar variables de entorno en otro sistema')
    memoria=ctypes.create_string_buffer(datos)
    entrada=Blob(len(datos),ctypes.cast(memoria,ctypes.POINTER(ctypes.c_ubyte)))
    salida=Blob()
    api=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    funcion=api.CryptUnprotectData if descifrar else api.CryptProtectData
    funcion.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    funcion.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if not funcion(ctypes.byref(entrada),None,None,None,None,1,ctypes.byref(salida)):
        raise RuntimeError('Windows no pudo abrir/guardar el secreto para este usuario')
    try:
        return ctypes.string_at(salida.pbData,salida.cbData)
    finally:
        kernel.LocalFree(salida.pbData)


def guardar(perfil,parametros,clave,ruta=ARCHIVO):
    ruta=Path(ruta)
    datos=json.loads(ruta.read_text(encoding='utf-8')) if ruta.exists() else {}
    datos[perfil]=dict(conexion={k:str(parametros[k]) for k in ('host','port','dbname','user')},
        secreto=base64.b64encode(proteger(clave.encode('utf-8'))).decode('ascii'))
    ruta.parent.mkdir(parents=True,exist_ok=True)
    temporal=ruta.with_suffix('.tmp')
    temporal.write_text(json.dumps(datos,indent=2),encoding='utf-8')
    temporal.replace(ruta)


def leer(perfil,parametros=None,ruta=ARCHIVO):
    ruta=Path(ruta)
    if not ruta.exists():
        return None
    registro=json.loads(ruta.read_text(encoding='utf-8')).get(perfil)
    if registro is None:
        return None
    if parametros is not None and any(str(parametros[k])!=registro['conexion'][k] for k in ('host','port','dbname','user')):
        return None
    return dict(registro['conexion'],password=proteger(base64.b64decode(registro['secreto']),True).decode('utf-8'))


def leer_env(ruta):
    d={}
    for linea in Path(ruta).read_text(encoding='utf-8-sig').splitlines():
        linea=linea.strip()
        if linea and not linea.startswith('#') and '=' in linea:
            k,v=linea.split('=',1);d[k.strip()]=v.strip().strip('"').strip("'")
    return d
