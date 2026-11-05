"""Transacción compartida y huellas de entradas; no almacena contraseñas."""
import hashlib
import json
from pathlib import Path


def sha256(ruta):
    h = hashlib.sha256()
    with Path(ruta).open('rb') as f:
        for bloque in iter(lambda: f.read(1024*1024), b''):
            h.update(bloque)
    return h.hexdigest()


class Entradas:
    def __init__(self):
        self.archivos = {}

    def registrar(self, ruta):
        if not isinstance(ruta,(str,Path)):
            raise ValueError('La carga reproducible requiere archivos locales explícitos')
        p = Path(ruta).resolve()
        if not p.is_file():
            raise FileNotFoundError(f'Entrada no es un archivo local: {p}')
        actual = {'archivo': str(p), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
        anterior = self.archivos.get(str(p))
        if anterior is not None and anterior != actual:
            raise RuntimeError(f'Una entrada cambió durante la carga: {p}')
        self.archivos[str(p)] = actual
        return actual

    def lector(self, funcion):
        def leer(ruta,*args,**kwargs):
            self.registrar(ruta)
            resultado = funcion(ruta,*args,**kwargs)
            self.registrar(ruta)
            return resultado
        return leer

    def verificar(self):
        for registro in list(self.archivos.values()):
            self.registrar(registro['archivo'])


class Compartida:
    """Las áreas no pueden confirmar/cerrar la conexión del orquestador."""
    def __init__(self,conn):
        self.conn, self.abortada = conn, False

    def cursor(self,*args,**kwargs):
        return self.conn.cursor(*args,**kwargs)

    def commit(self):
        pass

    def close(self):
        pass

    def rollback(self):
        self.abortada = True
        self.conn.rollback()


def huella_ciclos(conn):
    """COPY en orden estable: incluye la cadena completa que usa combustible."""
    h = hashlib.sha256()
    with conn.cursor() as cur:
        with cur.copy('COPY (SELECT * FROM ciclos.fact_viaje_ciclo ORDER BY id_viaje) TO STDOUT') as copia:
            for bloque in copia:
                h.update(bloque)
    return h.hexdigest()


def comprobar_bloques(carpeta, huella):
    ruta = Path(carpeta)/'bloques_procedencia.json'
    if not ruta.is_file():
        raise ValueError('Bloques sin procedencia verificable: regenerarlos sin --sin-litros')
    datos = json.loads(ruta.read_text(encoding='utf-8'))
    if datos['ciclos_sha256'] != huella:
        raise ValueError('Los bloques corresponden a otra cadena de ciclos')
    for entrada in datos['entradas']:
        if sha256(entrada['archivo']) != entrada['sha256']:
            raise ValueError(f"Cambió una fuente o regla de los bloques: {entrada['archivo']}")
    for nombre, digest in datos['salidas'].items():
        if sha256(Path(carpeta)/nombre) != digest:
            raise ValueError(f'Bloque modificado: {nombre}')
    return datos
