-- Esquema del MVP del modelo dimensional de peajes (SQLite). Generado por construir_dw_peajes.py

CREATE VIEW v_peaje_viaje AS
SELECT v.nro_viaje, v.nro_hoja_ruta, f.fecha, t.sit_stretch_id, t.texto_tramo, v.peaje_referencial,
       v.peaje_referencial_maximo, v.estado_peaje
FROM fact_viaje v JOIN dim_fecha f ON f.sk_fecha = v.sk_fecha_inicio JOIN dim_tramo t ON t.sk_tramo = v.sk_tramo;

CREATE VIEW v_puente_tramo_plaza AS
SELECT t.sk_tramo, t.sit_stretch_id, t.texto_tramo, ts.orden AS orden_segmento, s.desde_clave, s.hasta_clave,
       sp.orden AS orden_en_segmento, sp.id_plaza, p.nombre_plaza, p.concesion, p.posicion, sp.metodo_deteccion,
       sp.confianza, sp.origen, sp.vigente_desde, sp.vigente_hasta
FROM dim_tramo t JOIN puente_tramo_segmento ts ON ts.sk_tramo = t.sk_tramo
JOIN dim_segmento_ruta s ON s.sk_segmento = ts.sk_segmento
JOIN puente_segmento_plaza sp ON sp.sk_segmento = s.sk_segmento
LEFT JOIN dim_plaza_peaje p ON p.id_plaza = sp.id_plaza AND p.es_actual = 'S';

CREATE VIEW v_tarifa_vigente AS
SELECT p.id_plaza, p.nombre_plaza, p.concesion, p.posicion, c.categoria_vehicular, c.horario, c.modalidad, f.variante,
       f.tarifa_clp, f.vigente_desde, f.vigente_hasta, f.origen_tarifa, d.url AS documento_url
FROM fact_tarifa_peaje f JOIN dim_plaza_peaje p ON p.sk_plaza = f.sk_plaza
JOIN dim_condicion_tarifa c ON c.sk_condicion = f.sk_condicion
LEFT JOIN dim_documento_tarifa d ON d.sk_documento = f.sk_documento
WHERE date('now') BETWEEN f.vigente_desde AND f.vigente_hasta;

CREATE TABLE ajuste_segmento_plaza (
  desde_clave TEXT, hasta_clave TEXT, id_plaza TEXT, accion TEXT, km_desde_inicio REAL, vigente_desde TEXT, motivo TEXT);

CREATE TABLE dim_condicion_tarifa (
  sk_condicion INTEGER PRIMARY KEY, categoria_vehicular TEXT, horario TEXT, modalidad TEXT, aplica_a_flota TEXT,
  UNIQUE (categoria_vehicular, horario, modalidad));

CREATE TABLE dim_documento_tarifa (
  sk_documento INTEGER PRIMARY KEY, url TEXT UNIQUE, documento TEXT, seccion TEXT, anio INTEGER, archivo_local TEXT,
  sha256 TEXT, bytes INTEGER, fecha_descarga TEXT);

CREATE TABLE dim_fecha (
  sk_fecha INTEGER PRIMARY KEY, fecha TEXT UNIQUE, anio INTEGER, mes INTEGER, dia INTEGER, dia_semana INTEGER,
  nombre_dia TEXT, es_fin_de_semana TEXT);

CREATE TABLE dim_lugar (
  sk_lugar INTEGER PRIMARY KEY, clave_lugar TEXT UNIQUE, nombre_erp TEXT, localidad TEXT, sublugar TEXT, region TEXT,
  latitud REAL, longitud REAL, precision TEXT, fuente TEXT, es_base TEXT, es_lugar TEXT);

CREATE TABLE dim_plaza_peaje (
  sk_plaza INTEGER PRIMARY KEY, id_plaza TEXT, clave_plaza_script TEXT, plaza_id_mop TEXT, nombre_plaza TEXT, codigo_portico TEXT,
  concesion TEXT, documento_tarifa TEXT, tipo_red TEXT, cobro_por_recorrido TEXT, ruta TEXT, km_ruta REAL, region TEXT,
  latitud REAL, longitud REAL, fuente_coordenadas TEXT, posicion TEXT, tipo_cobro TEXT, fuente_registro TEXT,
  estado TEXT, vigente_desde TEXT, vigente_hasta TEXT, es_actual TEXT);

CREATE TABLE dim_segmento_ruta (
  sk_segmento INTEGER PRIMARY KEY, desde_clave TEXT, hasta_clave TEXT, sk_lugar_desde INTEGER REFERENCES dim_lugar,
  sk_lugar_hasta INTEGER REFERENCES dim_lugar, km_ruta REAL, horas_ruta REAL, estado TEXT, version_ruta TEXT,
  UNIQUE (desde_clave, hasta_clave));

CREATE TABLE dim_tramo (
  sk_tramo INTEGER PRIMARY KEY, sit_stretch_id INTEGER, texto_tramo TEXT, sk_lugar_origen INTEGER REFERENCES dim_lugar,
  sk_lugar_destino INTEGER REFERENCES dim_lugar, n_puntos INTEGER, km_erp REAL, km_ruta_calculada REAL, es_recorrido TEXT,
  isactive TEXT, estado_deteccion TEXT, vigente_desde TEXT, vigente_hasta TEXT, es_actual TEXT);

CREATE TABLE fact_peaje_referencial_tramo (
  sk_tramo INTEGER REFERENCES dim_tramo, vigente_desde TEXT, vigente_hasta TEXT, peaje_base REAL, peaje_maximo REAL,
  cantidad_plazas INTEGER, plazas_sin_tarifa INTEGER, estado TEXT, PRIMARY KEY (sk_tramo, vigente_desde));

CREATE TABLE fact_tarifa_peaje (
  sk_tarifa INTEGER PRIMARY KEY, sk_plaza INTEGER REFERENCES dim_plaza_peaje, id_plaza TEXT,
  sk_condicion INTEGER REFERENCES dim_condicion_tarifa, variante INTEGER, vigente_desde TEXT, vigente_hasta TEXT,
  tarifa_clp REAL, origen_tarifa TEXT, metodo_asignacion TEXT, confianza TEXT, categoria_texto_lamina TEXT,
  sk_documento INTEGER REFERENCES dim_documento_tarifa, fecha_carga TEXT, observacion TEXT);

CREATE TABLE fact_viaje (
  sk_viaje INTEGER PRIMARY KEY, nro_viaje INTEGER UNIQUE, nro_hoja_ruta INTEGER, sk_tramo INTEGER REFERENCES dim_tramo,
  sk_fecha_inicio INTEGER REFERENCES dim_fecha, es_retorno TEXT, peaje_referencial REAL, peaje_referencial_maximo REAL,
  estado_peaje TEXT);

CREATE TABLE parametro_peaje (parametro TEXT PRIMARY KEY, valor TEXT, descripcion TEXT);

CREATE TABLE puente_segmento_plaza (
  sk_segmento INTEGER REFERENCES dim_segmento_ruta, id_plaza TEXT, orden INTEGER, km_desde_inicio REAL,
  metodo_deteccion TEXT, confianza TEXT, origen TEXT, vigente_desde TEXT, vigente_hasta TEXT);

CREATE TABLE puente_segmento_plaza_deteccion (
  sk_segmento INTEGER, id_plaza TEXT, orden INTEGER, km_desde_inicio REAL, metodo_deteccion TEXT, confianza TEXT,
  vigente_desde TEXT, vigente_hasta TEXT);

CREATE TABLE puente_tramo_segmento (
  sk_tramo INTEGER REFERENCES dim_tramo, sk_segmento INTEGER REFERENCES dim_segmento_ruta, orden INTEGER,
  PRIMARY KEY (sk_tramo, orden));

CREATE TABLE registro_carga (
  fecha_carga TEXT, version_script TEXT, fecha_cambio TEXT, detalle TEXT);
