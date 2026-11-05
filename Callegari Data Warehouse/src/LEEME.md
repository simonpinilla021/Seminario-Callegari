# Fuentes de datos (privadas)

Esta carpeta se publica vacía. Para construir el DW se deben copiar aquí las extracciones de la empresa:

- `erp_actual/`: vistas del ERP en Parquet (viajes, tramos, vehículos, socios, tipo de carga, conductores, ciudades, reglas de retorno, consumos, productos, cargas de combustible).
- `combustible_historico/`: respaldo histórico de las cargas de combustible.
- `contabilidad/`: `rv_fact_acct.parquet`.
- `tablas/`: `sit_workorder.parquet`.

Las rutas se configuran en `code/config.ini`, sección `[fuentes]`. El detalle está en el README principal.
