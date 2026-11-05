"""Diario operacional local independiente de la transacción PostgreSQL.

Un escritor por intento, sin índice compartido. No registra argumentos, mensajes de
excepción, SQL ni credenciales. Las llamadas de biblioteca se auditan si existe un
contexto activo; los CLI operativos crean dicho contexto explícitamente.
"""
import contextvars
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
from comun.rutas import PROYECTO

RAIZ=PROYECTO/'.ingesta/auditoria'
ACTUAL=contextvars.ContextVar('auditoria_operativa_actual',default=None)
VERSION='2026-10-31-auditoria-operativa-v1'
CAMPOS={'etapa','fuente','segundos','modo','informe_id','tipo_error','sqlstate','filas','bytes_dw'}


def ahora():
    return datetime.now(timezone.utc).isoformat()


def escribir_json(ruta,datos):
    temporal=ruta.with_suffix('.tmp')
    with temporal.open('w',encoding='utf-8') as f:
        json.dump(datos,f,ensure_ascii=False,indent=2)
        f.flush();os.fsync(f.fileno())
    temporal.replace(ruta)


class Intento:
    def __init__(self,operacion,raiz=None):
        if not re.fullmatch('[a-z_]+',operacion): raise ValueError('Operación no válida')
        self.padre=ACTUAL.get()
        self.id=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
        self.carpeta=Path(raiz or (self.padre.carpeta.parent if self.padre else RAIZ))/self.id
        self.datos=dict(version=VERSION,id=self.id,padre_id=self.padre.id if self.padre else None,
            operacion=operacion,inicio_utc=ahora(),estado='en_curso',fase='inicio',
            dw_confirmado=False,confirmacion_solicitada=False,paquete_activado=False,pid=os.getpid(),secuencia=0)
        self.reloj=time.perf_counter()
        self.problema=False

    def escribir(self,tipo,**campos):
        if set(campos)-CAMPOS: raise ValueError('Campo de auditoría no permitido')
        for k,v in campos.items():
            if not isinstance(v,(str,int,float,bool,type(None))): raise ValueError('Dato de auditoría no escalar')
            if isinstance(v,str) and (len(v)>200 or any(c in v for c in '\r\n')):
                raise ValueError('Dato de auditoría no permitido')
        self.datos.update(ultimo_evento_utc=ahora(),secuencia=self.datos['secuencia']+1)
        if campos.get('etapa'): self.datos['fase']=campos['etapa']
        if 'bytes_dw' in campos:self.datos['bytes_dw']=campos['bytes_dw']
        registro=dict(self.datos,evento=tipo,**campos)
        with (self.carpeta/'eventos.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(registro,ensure_ascii=False)+'\n')
            f.flush();os.fsync(f.fileno())
        escribir_json(self.carpeta/'resumen.json',self.datos)

    def seguro(self,tipo,**campos):
        try:
            self.escribir(tipo,**campos)
            return True
        except OSError:
            self.problema=True
            print('ADVERTENCIA: auditoría local incompleta; revisar almacenamiento. '
                  'No se cambia el estado de confirmación del DW.',file=sys.stderr)
            return False

    def __enter__(self):
        # Si no se puede abrir el diario inicial, no se comienza la operación.
        self.carpeta.mkdir(parents=True,exist_ok=False)
        self.escribir('inicio')
        self.token=ACTUAL.set(self)
        return self

    def resultado(self,r):
        if isinstance(r,tuple) and len(r)==2 and isinstance(r[1],dict): r=r[1]
        if isinstance(r,dict):
            estado=r.get('estado')
            if isinstance(estado,str) and len(estado)<200:
                self.datos['estado']=estado
            identificador=r.get('id') or r.get('etl_id')
            if isinstance(identificador,str): self.datos['informe_id']=identificador

    def __exit__(self,clase,error,traza):
        try:
            if error is not None:
                if self.datos['paquete_activado']:
                    self.datos['estado']='activado_con_fallo_posterior'
                elif self.datos['dw_confirmado']:
                    self.datos['estado']='dw_confirmado_pendiente_revision'
                elif self.datos['confirmacion_solicitada']:
                    self.datos['estado']='confirmacion_dw_indeterminada'
                elif isinstance(error,SystemExit) and error.code in (None,0):
                    self.datos['estado']='salida_sin_ejecucion'
                else:
                    self.datos['estado']='interrumpida' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'fallida'
                campos=dict(tipo_error=type(error).__name__)
                sqlstate=getattr(error,'sqlstate',None)
                if isinstance(sqlstate,str) and re.fullmatch('[A-Z0-9]{5}',sqlstate): campos['sqlstate']=sqlstate
                self.datos.update(campos)
            else:
                campos={}
                if self.datos['estado']=='en_curso': self.datos['estado']='completada'
            self.datos.update(fin_utc=ahora(),segundos=round(time.perf_counter()-self.reloj,3))
            # Una falla del diario al cerrar no debe ocultar la excepción original
            # ni convertir un commit financiero ya hecho en un rollback ficticio.
            self.seguro('error' if error is not None else 'fin',**campos)
        finally:
            ACTUAL.reset(self.token)
        return False


def evento(tipo,**campos):
    actual=ACTUAL.get()
    if actual is None: return
    if tipo in ('dw_confirmado','paquete_activado','confirmacion_inicio'):
        while actual is not None:
            actual.datos['confirmacion_solicitada' if tipo=='confirmacion_inicio' else tipo]=True
            if tipo=='confirmacion_inicio': actual.escribir(tipo,**campos)
            else: actual.seguro(tipo,**campos)
            actual=actual.padre
    else:
        # Antes del commit, la falta de diario detiene la operación y permite rollback.
        if actual.datos['dw_confirmado']: actual.seguro(tipo,**campos)
        else: actual.escribir(tipo,**campos)


def diagnostico_error(error):
    actual=ACTUAL.get()
    if actual is not None:
        campos=dict(tipo_error=type(error).__name__)
        sqlstate=getattr(error,'sqlstate',None)
        if isinstance(sqlstate,str) and re.fullmatch('[A-Z0-9]{5}',sqlstate): campos['sqlstate']=sqlstate
        actual.seguro('diagnostico_error',**campos)


def operacion(nombre):
    def decorar(funcion):
        @wraps(funcion)
        def ejecutar(*args,**kwargs):
            actual=ACTUAL.get()
            if actual is None: return funcion(*args,**kwargs)
            if actual.datos['operacion']==nombre:
                r=funcion(*args,**kwargs);actual.resultado(r);return r
            with Intento(nombre) as intento:
                r=funcion(*args,**kwargs)
                intento.resultado(r)
            if intento.problema and isinstance(r,dict): r['advertencia_auditoria']=True
            return r
        return ejecutar
    return decorar


def ejecutar_cli(nombre,funcion):
    with Intento(nombre) as intento:
        r=funcion()
        intento.resultado(r)
    return r


def listar(raiz=None,limite=10):
    salida=[]
    for carpeta in sorted(Path(raiz or RAIZ).glob('*'),reverse=True):
        if not carpeta.is_dir(): continue
        try:
            r=json.loads((carpeta/'resumen.json').read_text(encoding='utf-8'))
            if r['padre_id'] is not None: continue
            # No diagnosticar automáticamente que un proceso murió ni que terminó.
            r['requiere_revision']=r['estado'] in ('en_curso','fallida','interrumpida','confirmacion_dw_indeterminada','dw_confirmado_pendiente_revision','activado_con_fallo_posterior')
            if r['estado']=='en_curso': r['observacion']='En curso o interrumpido sin cierre registrado; comprobar proceso e informes.'
            salida.append(r)
        except (OSError,ValueError,KeyError):
            salida.append(dict(id=carpeta.name,estado='registro_no_legible',requiere_revision=True))
        if len(salida)>=limite: break
    return salida


def detalle(identificador,raiz=None):
    if not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}',identificador):
        raise ValueError('Identificador de auditoría no válido')
    raiz=Path(raiz or RAIZ)
    carpeta=raiz/identificador
    r=dict(resumen=json.loads((carpeta/'resumen.json').read_text(encoding='utf-8')),eventos=[],subintentos=[])
    for linea in (carpeta/'eventos.jsonl').read_text(encoding='utf-8').splitlines():
        try:r['eventos'].append(json.loads(linea))
        except ValueError:r['advertencia']='Hay un evento incompleto; revisar cierre del proceso.'
    for p in raiz.glob('*/resumen.json'):
        try:
            hijo=json.loads(p.read_text(encoding='utf-8'))
            if hijo.get('padre_id')==identificador:r['subintentos'].append(hijo)
        except (OSError,ValueError):pass
    return r
