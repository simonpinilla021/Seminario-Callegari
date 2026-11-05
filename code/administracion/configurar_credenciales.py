"""Guarda contraseña una vez con cifrado Windows. No la pases en argumentos."""
import argparse
import getpass
from comun import db,credenciales


def principal():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--perfil',choices=['dw','erp'],required=True)
    p.add_argument('--importar-env',help='Solo ERP: .env anterior, sin ejecutar scripts antiguos')
    a=p.parse_args()
    if a.perfil=='dw':
        pg=db.leer_config()['postgres']
        destino={k:pg.get(k,v) for k,v in dict(host='localhost',port='5432',dbname='dw_callegari',user='postgres').items()}
        clave=getpass.getpass('Contraseña DW (se guarda cifrada para este usuario Windows): ')
    else:
        if a.importar_env:
            env=credenciales.leer_env(a.importar_env)
            destino=dict(host=env.get('DB_HOST','localhost'),port=env.get('DB_PORT','5432'),
                dbname=env.get('DB_NAME','idempiere'),user=env.get('DB_USER','consulta'))
            clave=env.get('DB_PASS','')
        else:
            destino=dict(host=input('Servidor ERP: ').strip(),port=input('Puerto [5432]: ').strip() or '5432',
                dbname=input('Base ERP [idempiere]: ').strip() or 'idempiere',user=input('Usuario ERP [consulta]: ').strip() or 'consulta')
            clave=getpass.getpass('Contraseña ERP: ')
    if not clave:
        raise SystemExit('No se guardó: contraseña vacía')
    credenciales.guardar(a.perfil,destino,clave)
    print(f'Perfil {a.perfil} guardado cifrado. No se abrió ninguna conexión.')


if __name__=='__main__':
    principal()
