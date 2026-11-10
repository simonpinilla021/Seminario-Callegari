# Data Warehouse de Transportes Callegari

Este proyecto integra viajes, ciclos, combustible, precios, peajes, contabilidad y taller en PostgreSQL. Conserva el detalle de cada proceso y expone vistas en `ds_gestion` para consultar costos, márgenes y cobertura por servicio y por ciclo.

| Método | Cálculo |
| --- | --- |
| E1 · Tarificador | Réplica del tarificador de cotización para servicios propios. |
| E2 · Tarificador camión | Misma lógica con el rendimiento certificado del camión cuando está disponible. |
| E3 · Costeo estimado | Estimación ajustada con combustible asignado, peajes referenciales y costo fijo a capacidad práctica. |

El ingreso usado en los márgenes es el estimado del servicio. La cobertura indica qué registros se pueden evaluar bajo cada método.

## Revisión académica

Requisitos. Python 3.12 o superior y PostgreSQL local. Los comandos se ejecutan desde la raíz.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item code/config.ejemplo.ini code/config.ini
.\.venv\Scripts\python.exe -B code/dw.py demo
```

Configure el puerto y el usuario si corresponde. Ingrese la contraseña cuando el programa la solicite. La demo usa la base local `dw_callegari_demo` y ejecuta el cargador de peajes con fuentes públicas del MOP. Carga documentos, plazas y tarifas por vigencia. Los tramos y viajes del ERP quedan vacíos.

```sql
SELECT count(*) FROM peajes.dim_documento_tarifa; -- 121
SELECT count(*) FROM peajes.dim_plaza_peaje;       -- 268
SELECT count(*) FROM peajes.fact_tarifa_peaje;     -- 1079
```

Para inspeccionar el modelo completo, cambie `dbname` en `code/config.ini` a `dw_callegari_esquema` y ejecute:

```powershell
.\.venv\Scripts\python.exe -B code/dw.py crear-esquema
.\.venv\Scripts\python.exe -B code/dw.py documentar
```

El primer comando restaura 81 tablas y 73 vistas vacías. El segundo genera `docs/diccionario_datos.md` a partir del catálogo de PostgreSQL. El [diccionario generado](docs/diccionario_datos.md) también se puede leer directamente en el repositorio. Los comandos usan bases distintas para que el profesor pueda inspeccionar la demo y el modelo en paralelo.

## Construcción con fuentes autorizadas

El código del constructor se incluye para revisión. Antes de usarlo, configure en `code/config.ini` la base PostgreSQL local autorizada para la carga completa. La reconstrucción real requiere acceso autorizado a las extracciones del ERP, contabilidad, documentos de precios, catálogos de tramos e insumos internos del tarificador. Se deben incorporar localmente en las rutas del código y de `code/config.ini`, junto con el registro autorizado de versiones de parámetros (`code/parametros/registro_versiones.json`). La copia académica trae únicamente los CSV públicos que consume la demo.

```powershell
.\.venv\Scripts\python.exe -B code/dw.py desde-archivos --validar
```

También existen `desde-erp --validar` para extraer todas las fuentes autorizadas y `actualizar --validar` para extraer las que requieren actualización. El modo de validación ejecuta los controles y revierte la transacción. Use `--guardar` para confirmar en la base autorizada después de revisar la validación. El plan revisa las entradas antes de conectarse y requiere que estén configuradas y disponibles.

El constructor carga primero las dimensiones comunes, luego los módulos de peajes, ventas, ciclos, precios, combustible, costos, tarificador, escenarios y taller. Al final concilia los resultados y crea las vistas de consulta de `ds_gestion`.

## Auditoría

`code/comun/` conserva los controles que verifican la consistencia de las cargas y el código que registra su ejecución. Los informes generados quedan en el entorno local. El repositorio académico excluye informes privados, credenciales, respaldos y extracciones de la empresa.
