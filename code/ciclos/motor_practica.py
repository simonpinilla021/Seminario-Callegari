# -*- coding: utf-8 -*-
"""
Motor de trayectorias y ciclos de la PRÁCTICA (Ciclos Practica - Codigo/modules), copiado SIN CAMBIOS DE LÓGICA:
  - normalizar_texto           (paso1)
  - motor V3 de trayectorias   (paso3: orden de los viajes de cada camión, viajes virtuales "Creado",
                                calidad N / NB / IG / C)
Lo usa ciclos/construir_dw_ciclos.py. Si se corrige el algoritmo, corregirlo aquí y anotarlo en la documentación.
"""
from itertools import permutations
from typing import Optional

import pandas as pd
import unicodedata


def normalizar_texto(texto):
    if texto is None or (isinstance(texto, float)):
        return None
    texto_normalizado = unicodedata.normalize('NFKD', str(texto))
    texto_limpio = texto_normalizado.encode('ascii', 'ignore').decode('ascii')
    return texto_limpio.upper().strip()



# =========================================================
# FUNCIONES DE MATCH
# =========================================================

def match_origen(viaje: dict, punto: dict, modo: str) -> bool:
    ciudad_ok = viaje["Ciudad_Origen_Limpia"] == punto["ciudad_limpia"]
    if modo == "exacto":
        return ciudad_ok
    if modo == "base":
        if punto["base"] is None or punto["base"] == "NO APLICA":
            return False
        base_ok = viaje["Bas_O"] is not None and viaje["Bas_O"] == punto["base"]
        return base_ok and not ciudad_ok
    return False


def match_destino(viaje: dict, punto: dict, modo: str) -> bool:
    ciudad_ok = viaje["Ciudad_Destino_Limpia"] == punto["ciudad_limpia"]
    if modo == "exacto":
        return ciudad_ok
    if modo == "base":
        if punto["base"] is None or punto["base"] == "NO APLICA":
            return False
        base_ok = viaje["Bas_D"] is not None and viaje["Bas_D"] == punto["base"]
        return base_ok and not ciudad_ok
    return False


# =========================================================
# FUNCIONES DE APOYO
# =========================================================

def obtener_km_virtual(origen_limpio: str, destino_limpio: str, tramos_lookup: dict) -> dict:
    key       = (origen_limpio, destino_limpio)
    candidato = tramos_lookup.get(key)
    if candidato is None:
        return {"stretch_id": "-999", "km": 0.0}
    if candidato["km"] > 0:
        return {"stretch_id": str(candidato["stretch_id"]), "km": candidato["km"]}
    return {"stretch_id": "-999", "km": 0.0}


def hacer_punto(viaje: dict, lado: str) -> dict:
    if lado == "origen":
        return {
            "ciudad_limpia"  : viaje["Ciudad_Origen_Limpia"],
            "ciudad_original": viaje["Ciudad_Origen_Limpia"],
            "base"           : viaje["Bas_O"]
        }
    return {
        "ciudad_limpia"  : viaje["Ciudad_Destino_Limpia"],
        "ciudad_original": viaje["Ciudad_Destino_Limpia"],
        "base"           : viaje["Bas_D"]
    }


def clasificar_movimiento(viaje: dict) -> str:
    estimado = viaje.get("estimado") or 0.0
    if estimado == 0:
        return "Vacio"
    if viaje.get("Bas_O") not in (None, "NO APLICA"):
        return "Envio"
    return "Retorno"


def procesar_viaje_real(
    viaje        : dict,
    resultado    : list,
    orden_global : list,
    hubo_inc     : list,
    calidad      : Optional[str] = None
) -> None:
    viaje["Orden_Procesado"] = orden_global[0]

    if calidad == "NB":
        viaje["Calidad_Trayectoria"] = "NB"
    else:
        viaje["Calidad_Trayectoria"] = "IG" if hubo_inc[0] else "N"

    viaje["Tipo_Movimiento"] = clasificar_movimiento(viaje)
    resultado.append(viaje)
    orden_global[0] += 1
    hubo_inc[0]      = False


def crear_virtual(punto_a, candidato, contador_v, orden_global, tramos_lookup):
    tramo = obtener_km_virtual(
        punto_a["ciudad_limpia"],
        candidato["Ciudad_Origen_Limpia"],
        tramos_lookup
    )
    contador_v[0] += 1
    v = candidato.copy()
    v.update({
        "c_orderline_id"       : "V_{}".format(contador_v[0]),
        # FIX: sit_stretch_id siempre como string para evitar mezcla de tipos
        "sit_stretch_id"       : tramo["stretch_id"],
        "kilometers"           : tramo["km"],
        "Ciudad_Origen_Limpia" : punto_a["ciudad_limpia"],
        "Ciudad_Destino_Limpia": candidato["Ciudad_Origen_Limpia"],
        "Bas_O"                : punto_a["base"],
        "Bas_D"                : candidato["Bas_O"],
        "Tipo_Movimiento"      : "Vacio",
        "estimado"             : 0.0,
        "Calidad_Trayectoria"  : "C",
        "Orden_Procesado"      : orden_global[0],
    })
    orden_global[0] += 1
    return v


def buscar_ancla_b(pendientes: list) -> Optional[dict]:
    fechas       = sorted(set(v["datepromised"] for v in pendientes))
    fecha_actual = fechas[0] if fechas else None

    for fecha in fechas:
        if fecha == fecha_actual:
            continue
        dia = [v for v in pendientes if v["datepromised"] == fecha]
        if len(dia) == 1:
            return dict(hacer_punto(dia[0], "origen"), viaje=dia[0])
    return None


# =========================================================
# HELPER: verificar conexión y crear virtual si hace falta
# =========================================================

def verificar_y_crear_virtual_si_necesario(
    primer_viaje : dict,
    punto_a      : dict,
    resultado    : list,
    orden_global : list,
    contador_v   : list,
    hubo_inc     : list,
    tramos_lookup: dict
) -> Optional[str]:
    conecta_exacto = match_origen(primer_viaje, punto_a, "exacto")
    conecta_base   = match_origen(primer_viaje, punto_a, "base")

    if not conecta_exacto and not conecta_base:
        virtual = crear_virtual(punto_a, primer_viaje, contador_v, orden_global, tramos_lookup)
        resultado.append(virtual)
        hubo_inc[0] = True
        return None

    if not conecta_exacto and conecta_base:
        return "NB"

    return None


# =========================================================
# SUB-VENTANA: CADENAS INTERNAS
# =========================================================

def buscar_cadenas_internas(viajes: list, modo: str) -> list:
    if len(viajes) == 1:
        return [[(viajes[0], "N")]]

    cadenas_validas = []

    for perm in permutations(viajes):
        valida     = True
        cadena_cal = [(perm[0], "N")]

        for i in range(1, len(perm)):
            anterior = perm[i - 1]
            actual   = perm[i]

            ciudad_conecta = (
                anterior["Ciudad_Destino_Limpia"] == actual["Ciudad_Origen_Limpia"]
            )

            if modo == "exacto":
                if not ciudad_conecta:
                    valida = False
                    break
                cadena_cal.append((actual, "N"))

            elif modo == "mixto":
                if ciudad_conecta:
                    cadena_cal.append((actual, "N"))
                else:
                    bas_ant      = anterior["Bas_D"]
                    bas_act      = actual["Bas_O"]
                    base_conecta = (
                        bas_ant is not None
                        and bas_act is not None
                        and bas_ant == bas_act
                        and bas_ant != "NO APLICA"
                    )
                    if base_conecta:
                        cadena_cal.append((actual, "NB"))
                    else:
                        valida = False
                        break

        if valida:
            cadenas_validas.append(cadena_cal)

    return cadenas_validas


# =========================================================
# RESOLVER VENTANA V3
# =========================================================

def resolver_ventana_v3(ventana, cola_b, punto_a, punto_b,
                        pendientes, resultado, orden_global,
                        contador_v, hubo_inc, tramos_lookup):

    fechas_ventana = sorted(set(v["datepromised"] for v in ventana))
    idx_frente     = 0
    idx_cierre     = len(fechas_ventana) - 1
    punto_b_actual = dict(punto_b) if punto_b else None

    while idx_frente <= idx_cierre and ventana:

        viajes_frente = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_frente]]
        viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
        hizo_algo     = False

        # PASO 1 — Pinza adelante, ciudad exacta, menor ID
        avanzando = True
        while avanzando and viajes_frente:
            avanzando  = False
            candidatos = sorted(
                [v for v in viajes_frente if match_origen(v, punto_a, "exacto")],
                key=lambda x: x["c_orderline_id"]
            )
            if candidatos:
                match = candidatos[0]
                procesar_viaje_real(match, resultado, orden_global, hubo_inc)
                punto_a = hacer_punto(match, "destino")
                viajes_frente.remove(match)
                ventana.remove(match)
                pendientes.remove(match)
                if not viajes_frente:
                    idx_frente += 1
                hizo_algo = True
                avanzando = True

        # PASO 2 — Pinza adelante, ciudad exacta, cualquier ID
        avanzando = True
        while avanzando and viajes_frente:
            avanzando  = False
            candidatos = sorted(
                [v for v in viajes_frente if match_origen(v, punto_a, "exacto")],
                key=lambda x: x["c_orderline_id"]
            )
            if candidatos:
                match = candidatos[0]
                procesar_viaje_real(match, resultado, orden_global, hubo_inc)
                punto_a = hacer_punto(match, "destino")
                viajes_frente.remove(match)
                ventana.remove(match)
                pendientes.remove(match)
                if not viajes_frente:
                    idx_frente += 1
                hizo_algo = True
                avanzando = True

        # PASO 3 — Pinza adelante, base, menor ID
        avanzando = True
        while avanzando and viajes_frente:
            avanzando  = False
            candidatos = sorted(
                [v for v in viajes_frente if match_origen(v, punto_a, "base")],
                key=lambda x: x["c_orderline_id"]
            )
            if candidatos:
                match = candidatos[0]
                procesar_viaje_real(match, resultado, orden_global, hubo_inc, calidad="NB")
                punto_a = hacer_punto(match, "destino")
                viajes_frente.remove(match)
                ventana.remove(match)
                pendientes.remove(match)
                if not viajes_frente:
                    idx_frente += 1
                hizo_algo = True
                avanzando = True

        # PASO 4 — Pinza adelante, base, cualquier ID
        avanzando = True
        while avanzando and viajes_frente:
            avanzando  = False
            candidatos = sorted(
                [v for v in viajes_frente if match_origen(v, punto_a, "base")],
                key=lambda x: x["c_orderline_id"]
            )
            if candidatos:
                match = candidatos[0]
                procesar_viaje_real(match, resultado, orden_global, hubo_inc, calidad="NB")
                punto_a = hacer_punto(match, "destino")
                viajes_frente.remove(match)
                ventana.remove(match)
                pendientes.remove(match)
                if not viajes_frente:
                    idx_frente += 1
                hizo_algo = True
                avanzando = True

        # PASOS 5-8 — Pinza atrás
        if punto_b_actual and idx_cierre != idx_frente:

            # PASO 5 — ciudad exacta, mayor ID
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            avanzando = True
            while avanzando and viajes_cierre:
                avanzando  = False
                candidatos = sorted(
                    [v for v in viajes_cierre if match_destino(v, punto_b_actual, "exacto")],
                    key=lambda x: x["c_orderline_id"],
                    reverse=True
                )
                if candidatos:
                    match = candidatos[0]
                    cola_b.append((match, "N"))
                    punto_b_actual = hacer_punto(match, "origen")
                    viajes_cierre.remove(match)
                    ventana.remove(match)
                    pendientes.remove(match)
                    if not viajes_cierre:
                        idx_cierre -= 1
                    hizo_algo = True
                    avanzando = True

            # PASO 6 — ciudad exacta, cualquier ID
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            avanzando = True
            while avanzando and viajes_cierre:
                avanzando  = False
                candidatos = sorted(
                    [v for v in viajes_cierre if match_destino(v, punto_b_actual, "exacto")],
                    key=lambda x: x["c_orderline_id"],
                    reverse=True
                )
                if candidatos:
                    match = candidatos[0]
                    cola_b.append((match, "N"))
                    punto_b_actual = hacer_punto(match, "origen")
                    viajes_cierre.remove(match)
                    ventana.remove(match)
                    pendientes.remove(match)
                    if not viajes_cierre:
                        idx_cierre -= 1
                    hizo_algo = True
                    avanzando = True

            # PASO 7 — base, mayor ID
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            avanzando = True
            while avanzando and viajes_cierre:
                avanzando  = False
                candidatos = sorted(
                    [v for v in viajes_cierre if match_destino(v, punto_b_actual, "base")],
                    key=lambda x: x["c_orderline_id"],
                    reverse=True
                )
                if candidatos:
                    match = candidatos[0]
                    cola_b.append((match, "NB"))
                    punto_b_actual = hacer_punto(match, "origen")
                    viajes_cierre.remove(match)
                    ventana.remove(match)
                    pendientes.remove(match)
                    if not viajes_cierre:
                        idx_cierre -= 1
                    hizo_algo = True
                    avanzando = True

            # PASO 8 — base, cualquier ID
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            avanzando = True
            while avanzando and viajes_cierre:
                avanzando  = False
                candidatos = sorted(
                    [v for v in viajes_cierre if match_destino(v, punto_b_actual, "base")],
                    key=lambda x: x["c_orderline_id"],
                    reverse=True
                )
                if candidatos:
                    match = candidatos[0]
                    cola_b.append((match, "NB"))
                    punto_b_actual = hacer_punto(match, "origen")
                    viajes_cierre.remove(match)
                    ventana.remove(match)
                    pendientes.remove(match)
                    if not viajes_cierre:
                        idx_cierre -= 1
                    hizo_algo = True
                    avanzando = True

        # PASO 9 — Sub-ventana frente, ciudad exacta
        if ventana and not hizo_algo:
            viajes_frente = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_frente]]
            if viajes_frente:
                cadenas = buscar_cadenas_internas(viajes_frente, modo="exacto")
                if len(cadenas) == 1:
                    primer_viaje    = cadenas[0][0][0]
                    calidad_externa = verificar_y_crear_virtual_si_necesario(
                        primer_viaje, punto_a, resultado, orden_global,
                        contador_v, hubo_inc, tramos_lookup
                    )
                    for i, (viaje, _) in enumerate(cadenas[0]):
                        cal = calidad_externa if i == 0 else None
                        procesar_viaje_real(viaje, resultado, orden_global, hubo_inc, calidad=cal)
                        punto_a = hacer_punto(viaje, "destino")
                        ventana.remove(viaje)
                        pendientes.remove(viaje)
                    idx_frente += 1
                    hizo_algo   = True

        # PASO 10 — Sub-ventana frente, ciudad + base mixto
        if ventana and not hizo_algo:
            viajes_frente = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_frente]]
            if viajes_frente:
                cadenas = buscar_cadenas_internas(viajes_frente, modo="mixto")
                if len(cadenas) == 1:
                    primer_viaje    = cadenas[0][0][0]
                    calidad_externa = verificar_y_crear_virtual_si_necesario(
                        primer_viaje, punto_a, resultado, orden_global,
                        contador_v, hubo_inc, tramos_lookup
                    )
                    for i, (viaje, cal_interna) in enumerate(cadenas[0]):
                        if i == 0 and calidad_externa == "NB":
                            cal_final = "NB"
                        else:
                            cal_final = "NB" if cal_interna == "NB" else None
                        procesar_viaje_real(
                            viaje, resultado, orden_global, hubo_inc,
                            calidad=cal_final
                        )
                        punto_a = hacer_punto(viaje, "destino")
                        ventana.remove(viaje)
                        pendientes.remove(viaje)
                    idx_frente += 1
                    hizo_algo   = True

        # PASO 11 — Sub-ventana cierre, ciudad exacta
        if ventana and not hizo_algo and punto_b_actual and idx_cierre != idx_frente:
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            if viajes_cierre:
                cadenas = buscar_cadenas_internas(viajes_cierre, modo="exacto")
                if len(cadenas) == 1:
                    for viaje, _ in reversed(cadenas[0]):
                        cola_b.append((viaje, "N"))
                        punto_b_actual = hacer_punto(viaje, "origen")
                        ventana.remove(viaje)
                        pendientes.remove(viaje)
                    idx_cierre -= 1
                    hizo_algo   = True

        # PASO 12 — Sub-ventana cierre, ciudad + base mixto
        if ventana and not hizo_algo and punto_b_actual and idx_cierre != idx_frente:
            viajes_cierre = [v for v in ventana if v["datepromised"] == fechas_ventana[idx_cierre]]
            if viajes_cierre:
                cadenas = buscar_cadenas_internas(viajes_cierre, modo="mixto")
                if len(cadenas) == 1:
                    for viaje, cal in reversed(cadenas[0]):
                        cola_b.append((viaje, cal))
                        punto_b_actual = hacer_punto(viaje, "origen")
                        ventana.remove(viaje)
                        pendientes.remove(viaje)
                    idx_cierre -= 1
                    hizo_algo   = True

        # PASO 13 — Virtual (último recurso)
        if ventana and not hizo_algo:
            viajes_frente_actual = [
                v for v in ventana
                if v["datepromised"] == fechas_ventana[idx_frente]
            ]
            if viajes_frente_actual:
                candidato = sorted(
                    viajes_frente_actual,
                    key=lambda x: x["c_orderline_id"]
                )[0]
                virtual = crear_virtual(punto_a, candidato, contador_v, orden_global, tramos_lookup)
                resultado.append(virtual)
                punto_a     = hacer_punto(candidato, "origen")
                hubo_inc[0] = True
                hizo_algo   = True
            else:
                idx_frente += 1

    return punto_a


# =========================================================
# RESOLVER CAMIÓN V3
# =========================================================

def resolver_camion_v3(viajes: list, tramos_lookup: dict) -> list:
    if not viajes:
        return []

    pendientes   = sorted(viajes, key=lambda x: (x["datepromised"], x["c_orderline_id"]))
    resultado    = []
    orden_global = [1]
    contador_v   = [1000]
    hubo_inc     = [False]

    punto_a = hacer_punto(pendientes[0], "origen")

    while pendientes:
        fecha_hoy  = pendientes[0]["datepromised"]
        bloque_hoy = [v for v in pendientes if v["datepromised"] == fecha_hoy]

        if len(bloque_hoy) == 1:
            viaje          = bloque_hoy[0]
            conecta_exacto = match_origen(viaje, punto_a, "exacto")
            conecta_base   = match_origen(viaje, punto_a, "base")

            if not conecta_exacto and not conecta_base:
                virtual = crear_virtual(punto_a, viaje, contador_v, orden_global, tramos_lookup)
                resultado.append(virtual)
                hubo_inc[0] = True
                calidad = None
            elif not conecta_exacto and conecta_base:
                calidad = "NB"
            else:
                calidad = None

            procesar_viaje_real(viaje, resultado, orden_global, hubo_inc, calidad=calidad)
            punto_a = hacer_punto(viaje, "destino")
            pendientes.remove(viaje)
            continue

        ancla_b = buscar_ancla_b(pendientes)
        if ancla_b:
            ventana = [v for v in pendientes if v["datepromised"] < ancla_b["viaje"]["datepromised"]]
            punto_b = {k: v for k, v in ancla_b.items() if k != "viaje"}
        else:
            ventana = list(pendientes)
            punto_b = None

        cola_b = []

        punto_a = resolver_ventana_v3(
            ventana       = ventana,
            cola_b        = cola_b,
            punto_a       = punto_a,
            punto_b       = punto_b,
            pendientes    = pendientes,
            resultado     = resultado,
            orden_global  = orden_global,
            contador_v    = contador_v,
            hubo_inc      = hubo_inc,
            tramos_lookup = tramos_lookup
        )

        for viaje, cal in reversed(cola_b):
            conecta_exacto = match_origen(viaje, punto_a, "exacto")
            conecta_base   = match_origen(viaje, punto_a, "base")

            if not conecta_exacto and not conecta_base:
                virtual = crear_virtual(punto_a, viaje, contador_v, orden_global, tramos_lookup)
                resultado.append(virtual)
                hubo_inc[0]   = True
                calidad_final = "NB" if cal == "NB" else None
            elif not conecta_exacto and conecta_base:
                calidad_final = "NB"
            else:
                calidad_final = "NB" if cal == "NB" else None

            procesar_viaje_real(
                viaje, resultado, orden_global, hubo_inc,
                calidad=calidad_final
            )
            punto_a = hacer_punto(viaje, "destino")

    return resultado


# =========================================================
# EJECUTAR
# =========================================================

def _normalizar_stretch_id(val):
    """
    Convierte sit_stretch_id a string limpio.
    Viajes reales vienen como float (1000252.0) → "1000252"
    Viajes virtuales ya vienen como string ("-999") → "-999"
    None/NaN → None
    """
    if val is None:
        return None
    if isinstance(val, float):
        if pd.isna(val):
            return None
        return str(int(val))
    return str(val)



# =========================================================
# Fin de utilidades de reconstrucción de trayectorias.
# =========================================================

