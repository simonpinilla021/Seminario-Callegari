"""Contrato de identidad para unir cargas nuevas e históricas sin duplicarlas."""
import pandas as pd


def normalizar_cargas(datos):
    d=datos.copy()
    ids=pd.to_numeric(d['sit_petroleum_id'],errors='raise')
    if ids.isna().any() or (ids%1!=0).any():
        raise ValueError('Carga de combustible con ID nulo o no entero')
    d['sit_petroleum_id']=ids.astype('Int64')
    if d.sit_petroleum_id.duplicated().any():
        raise ValueError('Cargas duplicadas por sit_petroleum_id; revisar unión de fuentes')
    return d
