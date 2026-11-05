"""Fórmulas de planilla. Entradas y resultados conservan su coordenada Excel."""
import math
from decimal import Decimal, ROUND_HALF_UP

def si(v):return str(v).casefold()=='si'
def redondear_excel(v):return float(Decimal(str(v)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def calcular(entrada,parametros):
    e={str(k).lower():v for k,v in entrada.items()};p=parametros
    defaults={'b4':'no','b5':'no','b6':'no','b7':'no','n5':'no','g4':'si','n4':'no','g5':'no','e7':'no','c21':'no','e8':'no','e5':0.,'e6':0.,'g7':0.,'i19':0.,'i23':0.}
    defaults.update({a.lower():p[a] for a in ['E22','D22','C22','F22']})
    for k,v in defaults.items():e.setdefault(k,v)
    # No buscar otra distancia: e10..12 proceden exclusivamente de la entrega.
    r={k:float(e[k]) for k in ('e10','e11','e12')};r['e13']=0.;r['e14']=0.
    r.update({k:e[k] for k in ['g4','n4','g5','e7','c21','e8','e5','e6','g7','e22','d22','c22','f22']})
    r['e15']=r['e10']+r['e11']+r['e12']
    r['f15']=r['e15']*(1 if si(e['b6']) else 2)
    r['g6']=sum(str(e['c'+str(i)]).casefold()!=str(e['c'+str(i-1)]).casefold() for i in (11,12,13))
    r['g8']=r['g6']+float(e['g7'])
    r['i4']=float(e['i4']) if e['i4'] is not None else float('nan')
    value=e.get('d27',p['B5']);r['d27']=float(value) if value is not None else float('nan')
    r['i5']=r['f15']/p['B36']+(r['g8']*(p.get('F38') or 0) if si(e['e8']) else 0.)
    r['k5']=float(math.ceil(r['i5']))
    r['i6']=r['f15']*r['i4']/r['d27'] if math.isfinite(r['d27']) and r['d27']>0 else float('nan')
    lh=p['E11'] if si(e['b5']) else p['E10']
    r['i7']=(r['e15']/p['B36']*24*lh*r['i4'] if si(e['b4']) else 0.)/(2 if si(e['b6']) else 1)
    tolls=[e.get('k'+str(i)) for i in (10,11,12)]
    r['k15']=sum(tolls) if all(isinstance(x,(int,float)) and math.isfinite(x) for x in tolls) else None
    r['j8']=sum(tolls) if all(isinstance(x,(int,float)) and math.isfinite(x) for x in tolls) else 'SIN PEAJE'
    r['i8']=(r['j8']*(1 if si(e['b6']) else 2)) if isinstance(r['j8'],(int,float)) else 0.
    r['i9']=float(e['e6'])*p['B24'] if float(e['e6'])>0 else float(e['e5'])*p['B25']
    r['i10']=redondear_excel(r['f15']/p['B36'])*p['C28'] if si(e['g5']) else 0.
    r['i11']=r['f15']*(p['B22'] if si(e['n5']) else p['B21'])
    r['i12']=0. if si(e['n4']) else max(p['B24'],r['f15']/p['B36']*p['B24'])
    r['i13']=r['k5']*(p['H29'] if si(e['n5']) else p['H25']) if si(e['g4']) and not si(e['n4']) else 0.
    r['i14']=float(e['e22'])*float(e['d22'])/12/float(e['c22'])*float(e['f22']) if si(e['c21']) else 0.
    r['i15']=(r['g6']+float(e['g7']))*p['B38'] if si(e['e8']) else 0.
    r['i16']=r['i5']*p['I10'] if si(e['e7']) else 0.
    r['factor_retorno']=p['C7'] if si(e['b7']) else 1.
    r['subtotal']=sum(r['i'+str(i)] for i in range(6,17))
    r['i17']=r['subtotal']*r['factor_retorno']
    r['i18']=r['i17']*p['J18'];r['i19']=float(e['i19']);r['i20']=r['i17']+r['i18']+r['i19'];r['i21']=0.
    r['i22']=float(math.ceil(r['i20']/500)*500) if math.isfinite(r['i20']) else None
    return r

def buscar_peajes(cities,filas):
    """BUSCARV exacto sin tildes/alias inferidos; primera coincidencia en cada rango."""
    resultado=[]
    for i in range(3):
        origen,destino=str(cities[i]).casefold(),str(cities[i+1]).casefold()
        match=next((x for x in filas if str(x['origen']).casefold()==origen and str(x['destino']).casefold()==destino and int(x['fila'])<=(101 if i==1 else 100)),None)
        # Auditoría Claude 02-11-2026, H-B1: celda vacía (BUSCARV=0) o texto (SUMA lo ignora) valen 0, no error.
        resultado.append(float(match['peaje']) if match and match['estado'] in ('ok','vacia','texto') else None)
    return resultado
