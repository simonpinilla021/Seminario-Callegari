"""Congela la entrega, sin regenerar ni corregir sus kilómetros."""
from pathlib import Path
import json,hashlib,shutil,csv
import openpyxl
ROOT=Path(__file__).resolve().parents[2]
DELIVERY=Path(r'C:\Users\crist\Documents\Proyecto Seminario\Notas\01 Claude - Contexto de diseño\Entrega km tarificador 2026-11-02')
OUT=Path(__file__).resolve().parent/'exacto/insumos'
EXCEL=ROOT/'tarificador/TARIFICADOR STANDAR VIGENTE AL  15.10.2026 PARA SUBASTA DEL DIA 16.10.xlsx'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 files=['distancia_tarificador.csv.gz','tarificador_ciudad.csv','equivalencia_lugar_tarificador.csv','equivalencia_lugar_tarificador_manual.csv','km_tramo_tarificador.csv','resumen_km_tarificador.json']
 for name in files:shutil.copy2(DELIVERY/name,OUT/name)
 summary=json.loads((OUT/'resumen_km_tarificador.json').read_text(encoding='utf-8'))
 assert sha(EXCEL)==summary['sha256']
 w=openpyxl.load_workbook(EXCEL,read_only=True,data_only=True)
 params={a:w['Parametros'][a].value for a in ['B5','B24','H25','H29','B21','B22','B36','E10','E11','B25','C28','B38','F38','I10']}
 for a in ['J18','C7','E22','D22','C22','F22']:params[a]=w['planilla'][a].value
 (OUT/'parametros_excel.json').write_text(json.dumps(params,ensure_ascii=False,indent=2),encoding='utf-8')
 # Primera coincidencia exacta de BUSCARV; conservar coordenadas y nombres crudos.
 ds=list(w['datos2'].iter_rows(min_row=4,max_row=502,min_col=11,max_col=12,values_only=True))
 rows=list(w['peajes'].iter_rows(min_row=3,max_row=101,min_col=2,max_col=64,values_only=True))
 with (OUT/'peajes_excel.csv').open('w',newline='',encoding='utf-8') as f:
  writer=csv.writer(f);writer.writerow(['fila','origen','destino','indice_destino','peaje','estado'])
  for rowno,row in enumerate(rows,3):
   if row[0] is None:continue
   for dest,idx in ds:
    if dest is None or not isinstance(idx,(float,int)):continue
    if not 1<=int(idx)<=len(row):writer.writerow([rowno,row[0],dest,int(idx),'','error']);continue  # #REF! en Excel
    value=row[int(idx)-1]
    # Auditoría Claude 02-11-2026, H-B1: celda vacía -> BUSCARV devuelve 0 ('vacia'); texto -> SUMA(K10:K12) lo ignora
    # ('texto', vale 0); solo un error de Excel (#N/A, #REF!...) deja J8 = SIN PEAJE.
    if isinstance(value,(float,int)):writer.writerow([rowno,row[0],dest,int(idx),value,'ok'])
    elif value is None:writer.writerow([rowno,row[0],dest,int(idx),0,'vacia'])
    elif isinstance(value,str) and not value.startswith('#'):writer.writerow([rowno,row[0],dest,int(idx),0,'texto'])
    else:writer.writerow([rowno,row[0],dest,int(idx),'','error'])
 # Entradas literales de la versión recibida. No extrapolar un redondeo a otras cartas.
 o=w['petroleo']
 snapshot={'fecha_carta':'2026-10-15','base_neto':o['D6'].value,'base_operacion':o['D7'].value,'base_ie':o['D9'].value,'tct_neto':o['I6'].value,'tct_operacion':o['I7'].value,'tct_ie':o['I9'].value,'pct_base':o['F14'].value,'pct_tct':o['K14'].value,'factor_recuperacion':o['E8'].value,'precio_control':w['planilla']['I4'].value}
 (OUT/'entrada_petroleo_version.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
 manifest={'excel':str(EXCEL.relative_to(ROOT)),'excel_sha256':sha(EXCEL),'entrega':str(DELIVERY),'archivos':{p.name:sha(p) for p in OUT.iterdir() if p.is_file() and p.name!='manifiesto.json'},'km_regenerados':False}
 (OUT/'manifiesto.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 print('Insumos congelados:',OUT)
if __name__=='__main__':main()
