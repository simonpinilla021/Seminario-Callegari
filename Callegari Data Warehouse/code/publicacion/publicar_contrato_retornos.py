"""Publica ds_gestion.v_retorno y ds_gestion.v_km_corredor.

--validar (por defecto): crea las vistas, corre los controles y revierte (no deja cambios).
--guardar: confirma solo si todos los controles dan cero y ningún hecho financiero cambió.
Uso, desde la carpeta code:  python -B -m publicacion.publicar_contrato_retornos --validar
"""
from pathlib import Path
from datetime import datetime
import argparse, json, hashlib
from comun import db
from publicacion import contrato_retornos


def principal(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    modos = p.add_mutually_exclusive_group()
    modos.add_argument('--validar', action='store_true')
    modos.add_argument('--guardar', action='store_true')
    a = p.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    out = root/'validacion/contrato_retornos'/datetime.now().strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True)
    info = {'estado': 'iniciado', 'confirmado': False, 'modo': 'guardar' if a.guardar else 'validar',
            'version': contrato_retornos.VERSION}
    con = None
    try:
        con = db.conectar()
        con.execute("SET LOCAL lock_timeout='5s'")
        con.execute("SET LOCAL statement_timeout='10min'")
        if not con.uno('SELECT pg_try_advisory_xact_lock(20261026,6)')[0]:
            raise RuntimeError('Otra carga DW en curso')
        q = 'SELECT count(*),sum(monto::numeric),sum(monto_comercial::numeric) FROM escenarios.fact_costo_viaje'
        antes = con.uno(q)
        info.update(contrato_retornos.publicar(con))
        if antes != con.uno(q):
            raise ValueError('Cambió un hecho financiero')
        info['hechos_financieros_sin_cambios'] = True
        info['archivo_sha256'] = hashlib.sha256(Path(contrato_retornos.__file__).read_bytes()).hexdigest()
        if a.guardar:
            con.commit()
            info.update(estado='confirmado', confirmado=True)
        else:
            con.conn.rollback()
            info['estado'] = 'validado_y_revertido'
    except BaseException as exc:
        if con is not None:
            con.conn.rollback()
        info.update(estado='fallido', error=str(exc))
        raise
    finally:
        if con is not None:
            con.close()
        (out/'ejecucion.json').write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f'Informe: {out}', flush=True)
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    principal()
