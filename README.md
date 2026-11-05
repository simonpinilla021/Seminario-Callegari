# Data Warehouse Transportes Callegari

Data warehouse en PostgreSQL para calcular el **costo y el margen por viaje y por ciclo de viaje** de una empresa de transporte de carga. Integra viajes, combustible, peajes, contabilidad y taller, y calcula el costo bajo cuatro escenarios. Es parte del seminario de título de Ingeniería en Información y Control de Gestión (Universidad Católica del Norte).

> Este repositorio contiene **solo el código y los datos públicos o de parámetros**. Los datos de la empresa (extracciones del ERP, contabilidad y cartas de precio de combustible) no se publican. Sin ellos el código se puede revisar, pero no se puede construir el DW con datos reales. Ver [Datos que no se incluyen](#datos-que-no-se-incluyen).

## Arquitectura

```
Fuentes (ERP, contabilidad, cartas Copec, láminas MOP, parámetros)
        │  Parquet / CSV
        ▼
Esquemas de cálculo: dim_comun · ventas · ciclos · combustible · peajes · precios
                     costos · tarificador · escenarios · taller · comun
        │
        ▼
ds_gestion (vistas de consumo)  ──►  Power BI
```

- **Dimensiones comunes** (`dim_comun`): calendario, equipo, conductor, socio de negocio, producto, tramo.
- **Hechos por proceso**: un esquema por proceso. Por ejemplo, `ventas` tiene un viaje por fila y `combustible` tiene las cargas y los litros asignados a cada viaje.
- **Escenarios de costo** (`escenarios`):

  | Escenario | Qué calcula |
  |---|---|
  | 1 | Tarificador de la empresa replicado exactamente |
  | 2 | Igual al 1, pero con el rendimiento real de cada camión |
  | 3 | Costo estimado: combustible por bloques de carga, peaje referencial MOP y costo fijo a capacidad práctica |
  | 4 | Igual al 3, pero con absorción total mensual del costo fijo |

- **Publicación** (`ds_gestion`): vistas estables que lee el dashboard, más controles de conciliación.

## Construcción

`code/construir_todo.py` construye todo el DW en **una sola transacción** y aplica controles de integridad antes de confirmar. Si una etapa o un control falla, no se guarda nada. Las etapas son:

1. Dimensiones comunes
2. Peajes
3. Ventas
4. Ciclos
5. Precios de combustible
6. Asignación de litros (bloques)
7. Combustible
8. Costo del tarificador
9. Costos
10. Tarificador
11. Escenarios
12. Taller
13. Conciliaciones y publicación en `ds_gestion`

## Requisitos

- Python 3.12 o superior.
- PostgreSQL local. Por seguridad, la carga solo se ejecuta contra `localhost`.
- Dependencias: `pip install -r code/requirements.txt`. El conector de correo Copec necesita además `code/requirements-copec.txt`.

## Instalación

Todos los comandos se ejecutan desde la carpeta `code`:

```powershell
python -m venv ..\.venv
..\.venv\Scripts\activate
pip install -r requirements.txt
python -B -m administracion.inicializar_entorno --crear-config
```

El último comando crea `config.ini` a partir de `config.ejemplo.ini`. Ahí se escriben el servidor, el puerto, la base y el usuario de PostgreSQL. La contraseña puede ir en la línea `password` de `config.ini`, que no se sube a Git. También puede ir en la variable `PGPASSWORD` o cifrada para el usuario de Windows con `python -B -m administracion.configurar_credenciales --perfil dw`. Si no hay ninguna, se pregunta al ejecutar.

## Uso: un solo comando, `dw.py`

| Comando | Qué hace | Necesita datos de la empresa |
|---|---|---|
| `python -B dw.py crear-base` | Crea la base de datos indicada en `config.ini` | No |
| `python -B dw.py crear-esquema` | Crea **todas las tablas, vistas y llaves del DW, sin datos**, desde `postgres/esquema_dw.sql` | No |
| `python -B dw.py demo` | Crea el esquema y **ejecuta la carga real del área de peajes** con datos públicos del MOP, más el calendario | No |
| `python -B dw.py documentar` | Genera `docs/diccionario_datos.md` desde la base: tablas, columnas, tipos, llaves y relaciones | No |
| `python -B dw.py desde-archivos --validar` | Construcción completa con las extracciones guardadas en `src/` | Sí |
| `python -B dw.py desde-erp --validar` | Descarga todas las fuentes del ERP por la red de la empresa y construye | Sí, más acceso a la red |
| `python -B dw.py actualizar --validar` | Descarga solo las fuentes vencidas del ERP y reconstruye | Sí, más acceso a la red |

En los tres últimos, `--validar` construye todo en una transacción, aplica los controles y **revierte**; `--guardar` confirma. Sin ninguno de los dos, el comando solo describe el plan. Cada ejecución deja un manifiesto con la huella SHA-256 de cada archivo de entrada. `crear-esquema` y `demo` se niegan a borrar un DW existente salvo que se agregue `--reemplazar`.

### Por qué el ERP se lee a archivos y no directo a la base

`desde-erp` descarga las vistas del ERP en modo de solo lectura y las guarda como Parquet en un paquete con fecha (*staging*); recién después construye. Esto permite:

- **reproducir**: la misma foto de datos genera el mismo DW;
- **no cargar el ERP**: cada fuente se lee una vez;
- **reintentar** una construcción fallida sin volver a descargar.

Las fuentes, sus columnas y sus frecuencias están en `code/ingesta.ini`.

## Revisión académica

Sin acceso a los datos de la empresa se puede:

1. Crear la base y el esquema completo vacío con `dw.py crear-esquema`, y revisar las tablas y sus relaciones con cualquier cliente de PostgreSQL (pgAdmin, DBeaver).
2. Ejecutar `dw.py demo` para ver una carga real de punta a punta. Lee las láminas de tarifas del MOP ya procesadas, normaliza las plazas, versiona las tarifas por fecha y calcula el peaje referencial de cada tramo. Consultas de ejemplo:
   ```sql
   SELECT * FROM peajes.dim_plaza_peaje LIMIT 20;
   SELECT * FROM peajes.fact_tarifa_peaje LIMIT 20;
   SELECT * FROM peajes.fact_peaje_referencial_tramo LIMIT 20;
   ```
3. Leer `docs/diccionario_datos.md`, que describe cada tabla y vista.
4. Revisar en el código cómo se construye cada área: `code/<área>/construir_dw_<área>.py`.

## Estructura

| Carpeta | Contenido |
|---|---|
| `code/dw.py` | Punto de entrada: crear esquema, demo, documentar, construir y actualizar |
| `code/construir_todo.py` | Orquestador de la construcción completa |
| `code/actualizar_dw.py` | Descarga las fuentes del ERP y reconstruye el DW |
| `code/dimensiones/` | Dimensiones comunes |
| `code/ventas/`, `ciclos/`, `combustible/`, `peajes/`, `precios/`, `costos/`, `tarificador/`, `escenarios/`, `taller/` | Una etapa de construcción por proceso |
| `code/publicacion/` | Vistas de consumo (`ds_gestion`) y contratos con el dashboard |
| `code/comun/` | Conexión, credenciales, calendario, controles y reglas compartidas |
| `code/conectores/` | Extracción del ERP y lector de las cartas de precio Copec |
| `code/administracion/` | Instalación: configuración, credenciales y fuentes externas |
| `code/parametros/` | Contrato y registro de versiones de los parámetros |
| `code/postgres/esquema_dw.sql` | Esquema completo del DW (tablas, vistas y llaves) |
| `docs/` | Diccionario de datos |
| `code/**/*_manual.csv` | Parámetros definidos manualmente (costos, taller, tarificador, precios) |
| `code/peajes/` | Tarifas MOP ya procesadas (CSV) y asignación de plazas a tramos |
| `code/tarificador/exacto/insumos/` | Kilómetros, peajes y parámetros congelados del tarificador |
| `src/` | Fuentes de la empresa (vacía en el repositorio) |

## Datos que no se incluyen

Estos datos son privados de la empresa, así que deben ubicarse localmente para construir el DW. `.gitignore` los excluye.

| Ruta | Contenido |
|---|---|
| `src/erp_actual/` | Vistas del ERP en Parquet: `sit_pbix_travelvta_v`, `traveltramo_v`, `travelveh_v`, `socneg_v`, `traveltcarga_v`, `traveldriv_v`, `city_v`, `travelreglas_v`, `prodconsumo_v`, `prodmaster_v` y `guiascombust_v` |
| `src/combustible_historico/` | Respaldo histórico de las cargas de combustible (`guiascombust_v`, `travelveh_v`) |
| `src/contabilidad/` | `rv_fact_acct.parquet`, los asientos contables |
| `src/tablas/` | `sit_workorder.parquet`, las órdenes de trabajo de taller |
| `code/precios/cartas_out/` | `cartas.csv`, `lineas.csv` y `archivos.csv`, que genera `precios/leer_cartas.py` a partir de los PDF de cartas Copec |
| `code/config.ini` y `.local/` | Configuración local y credenciales cifradas |

Las rutas de `src/` se definen en `code/config.ini`. Tampoco se incluyen los PDF de las láminas de peajes del MOP, que son públicos: se vuelven a descargar con `peajes/peajes_descarga.py`, y el DW usa los CSV ya extraídos que sí están incluidos.
