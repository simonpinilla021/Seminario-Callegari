--
-- PostgreSQL database dump
--

\restrict T47P2Zr5mTht7TShVVc8PbMvVvYe6783N3eMNC28zRbMBNon4opiyyvVGyKhRqI

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: ciclos; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ciclos;


--
-- Name: combustible; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA combustible;


--
-- Name: comun; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA comun;


--
-- Name: costos; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA costos;


--
-- Name: dim_comun; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA dim_comun;


--
-- Name: ds_gestion; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ds_gestion;


--
-- Name: escenarios; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA escenarios;


--
-- Name: peajes; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA peajes;


--
-- Name: precios; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA precios;


--
-- Name: taller; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA taller;


--
-- Name: tarificador; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tarificador;


--
-- Name: ventas; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ventas;


--
-- Name: par(text); Type: FUNCTION; Schema: taller; Owner: -
--

CREATE FUNCTION taller.par(p text) RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT valor FROM taller.parametro_taller WHERE parametro = p $$;


--
-- Name: resolver_parametro(text, date, text); Type: FUNCTION; Schema: tarificador; Owner: -
--

CREATE FUNCTION tarificador.resolver_parametro(p_codigo text, p_fecha date, p_ambito text DEFAULT 'todos'::text) RETURNS TABLE(ambito_efectivo text, valor double precision, origen text, vigente_desde date, vigente_hasta date, nota text)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE cantidad bigint;
BEGIN
  RETURN QUERY
  WITH pedidos AS (SELECT p_codigo AS codigo,p_fecha AS fecha,p_ambito AS ambito),
  seleccion AS (
WITH candidatos AS (
  SELECT p.codigo,p.fecha,p.ambito AS ambito_solicitado,
    v.ambito AS ambito_efectivo,v.valor,v.origen,v.vigente_desde,v.vigente_hasta,v.nota,
    RANK() OVER(PARTITION BY p.codigo,p.fecha,p.ambito
      ORDER BY (v.ambito=p.ambito) DESC,(v.origen='BBDD') DESC) AS prioridad
  FROM pedidos p
  LEFT JOIN tarificador.dim_parametro d ON d.codigo=p.codigo
  LEFT JOIN tarificador.fact_parametro_valor v ON v.sk_parametro=d.sk_parametro
    AND v.ambito IN (p.ambito,'todos')
    AND p.fecha>=v.vigente_desde AND p.fecha<v.vigente_hasta)
SELECT c.codigo,c.fecha,c.ambito_solicitado,c.ambito_efectivo,c.valor,c.origen,c.vigente_desde,c.vigente_hasta,c.nota
FROM candidatos c WHERE c.prioridad=1
)
  SELECT c.ambito_efectivo,c.valor,c.origen,c.vigente_desde,c.vigente_hasta,c.nota
  FROM seleccion c WHERE c.ambito_efectivo IS NOT NULL;
  GET DIAGNOSTICS cantidad = ROW_COUNT;
  IF cantidad>1 THEN
    RAISE EXCEPTION 'Parametros ambiguos: codigo=%, fecha=%, ambito=%',p_codigo,p_fecha,p_ambito;
  END IF;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: dim_ciclo_comercial; Type: TABLE; Schema: ciclos; Owner: -
--

CREATE TABLE ciclos.dim_ciclo_comercial (
    ciclo_comercial text NOT NULL,
    equipo_id bigint,
    fecha_inicio date,
    fecha_fin date,
    dias integer,
    motivo_apertura text,
    motivo_cierre text,
    estado text,
    viajes integer,
    viajes_virtuales integer,
    envios integer,
    retornos integer,
    vacios integer,
    km double precision,
    km_cargado double precision,
    ingreso double precision,
    patron text,
    recorrido text,
    viaje_principal text
);


--
-- Name: dim_tramo_base; Type: TABLE; Schema: ciclos; Owner: -
--

CREATE TABLE ciclos.dim_tramo_base (
    sit_stretch_id bigint NOT NULL,
    tramo text,
    km double precision,
    ciudad_origen text,
    ciudad_origen_limpia text,
    ciudad_destino text,
    ciudad_destino_limpia text,
    base_origen text,
    base_destino text,
    tipo_movimiento_base text
);


--
-- Name: fact_viaje_ciclo; Type: TABLE; Schema: ciclos; Owner: -
--

CREATE TABLE ciclos.fact_viaje_ciclo (
    id_viaje text NOT NULL,
    nro_viaje bigint,
    equipo_id bigint,
    orden_procesado integer,
    sk_fecha integer,
    sit_stretch_id bigint,
    km double precision,
    ciudad_origen_limpia text,
    ciudad_destino_limpia text,
    base_origen text,
    base_destino text,
    cliente_id bigint,
    producto_id bigint,
    ingreso double precision,
    ingreso_por_km double precision,
    tipo_movimiento text,
    clasificacion_viaje text,
    regla_clasificacion text,
    tipo_carga_final text,
    calidad_trayectoria text,
    es_virtual text,
    tipo_virtual text,
    ciclo_comercial text,
    orden_ciclo_comercial integer,
    rol_comercial text
);


--
-- Name: regla_retorno; Type: TABLE; Schema: ciclos; Owner: -
--

CREATE TABLE ciclos.regla_retorno (
    orden integer,
    rompe_regla text,
    cliente_id bigint,
    razon_social text,
    carga_estricta text,
    producto_id bigint,
    tipo_carga text,
    nombre_carga text
);


--
-- Name: dim_generacion_camion; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.dim_generacion_camion (
    equipo_id bigint NOT NULL,
    codigo text,
    modelo text,
    anio bigint,
    generacion text,
    max_cargado double precision,
    max_vacio double precision
);


--
-- Name: fact_bloque_certificacion; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_bloque_certificacion (
    equipo_id bigint NOT NULL,
    bloque bigint NOT NULL,
    t_ini timestamp without time zone,
    t_fin timestamp without time zone,
    litros double precision,
    km_viajes double precision,
    km_c double precision,
    km_v double precision,
    centro double precision,
    km_odo double precision,
    calidad text,
    sis_ini double precision,
    sis_fin double precision,
    gps_ini double precision,
    gps_fin double precision,
    km_sis double precision,
    km_gps double precision,
    grado_lectura text,
    fuente_km_cert text,
    km_cert double precision,
    razon_cert double precision,
    rend_cert double precision,
    rend_max double precision,
    motivo text,
    certificado boolean
);


--
-- Name: fact_bloque_combustible; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_bloque_combustible (
    equipo_id bigint NOT NULL,
    bloque integer NOT NULL,
    litros double precision,
    paradas integer,
    cargas_ruta integer,
    inicio timestamp without time zone,
    termino timestamp without time zone,
    odometro_ini double precision,
    odometro_fin double precision,
    razon_habitual_camion double precision,
    bloques_unidos integer,
    viajes integer,
    km_viajes double precision,
    km_cargados double precision,
    km_vacios double precision,
    km_local double precision,
    dias double precision,
    km_odometro double precision,
    razon_odo_viajes double precision,
    razon_relativa double precision,
    rendimiento_odo double precision,
    rendimiento_viajes double precision,
    calidad text,
    anio integer,
    litros_atp double precision,
    litros_cop double precision,
    litros_otro double precision,
    litros_sin_precio double precision,
    precio_medio_litro double precision,
    costo_combustible double precision
);


--
-- Name: fact_carga_combustible; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_carga_combustible (
    id_carga bigint NOT NULL,
    equipo_id bigint,
    fecha timestamp without time zone,
    sk_fecha integer,
    litros double precision,
    oilsupplier text,
    producto_id bigint,
    parada bigint,
    bloque integer,
    bloque_original integer,
    es_llenado_corte text,
    precio_litro_aplicado double precision,
    monto double precision,
    sk_carta_precio integer
);


--
-- Name: fact_rendimiento_camion_mes; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_rendimiento_camion_mes (
    equipo_id bigint NOT NULL,
    mes text NOT NULL,
    litros_mes double precision,
    bloques bigint,
    litros_cert double precision,
    km_cert double precision,
    bloques_cert bigint,
    pct_litros_cert double precision,
    rendimiento double precision,
    publicado boolean,
    mediana_12m double precision,
    desvio double precision,
    alerta boolean
);


--
-- Name: fact_viaje_combustible; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_viaje_combustible (
    sk_viaje_comb bigint NOT NULL,
    nro_viaje bigint,
    id_viaje_fuente text,
    equipo_id bigint,
    orden_cadena double precision,
    sk_fecha_inicio integer,
    tramo_texto text,
    km_tramo double precision,
    tipo_movimiento text,
    vacio_fisico text,
    grupo_carga text,
    es_virtual text,
    relacion_base text,
    litros_bloque double precision,
    fraccion_cubierta double precision,
    fraccion_cubierta_ok double precision,
    litros_estimado double precision,
    litros_asignados double precision,
    metodo_asignacion text,
    id_calculo bigint,
    costo_bloque double precision,
    costo_estimado double precision,
    costo_combustible double precision,
    precio_litro_aplicado double precision,
    fraccion_certificada double precision,
    fuente_ficha text,
    l100_teorico double precision,
    litros_teoricos double precision,
    razon_real_teorico double precision,
    rendimiento_viaje double precision,
    rendimiento_max double precision,
    motivo_bloque text,
    estado_litros text,
    litros_margen double precision,
    ajuste_litros double precision,
    costo_combustible_margen double precision,
    costo_ajuste double precision,
    precio_litro_margen double precision
);


--
-- Name: fact_viaje_combustible_sk_viaje_comb_seq; Type: SEQUENCE; Schema: combustible; Owner: -
--

ALTER TABLE combustible.fact_viaje_combustible ALTER COLUMN sk_viaje_comb ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME combustible.fact_viaje_combustible_sk_viaje_comb_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fact_viaje_combustible_tarificador; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.fact_viaje_combustible_tarificador (
    nro_viaje bigint NOT NULL,
    fecha date,
    equipo_id bigint,
    codigo_camion text,
    grupo_equipo text,
    sk_tramo bigint,
    km_tramo double precision,
    precio_base double precision,
    precio_ruta double precision,
    pct_base double precision,
    recargo_precio double precision,
    precio_ponderado double precision,
    rendimiento_camion double precision,
    bloques_rendimiento integer,
    fecha_ultimo_bloque timestamp without time zone,
    litros_rend_camion double precision,
    costo_rend_camion double precision,
    rendimiento_tarificador double precision,
    litros_tarificador double precision,
    costo_tarificador double precision,
    estado text
);


--
-- Name: param_ficha_consumo; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.param_ficha_consumo (
    equipo_id bigint NOT NULL,
    mes text NOT NULL,
    generacion text,
    bloques_cert_12m bigint,
    fuente text,
    l100_cargado double precision,
    l100_vacio double precision,
    kml_cargado double precision,
    kml_vacio double precision,
    vigente_desde date,
    vigente_hasta date
);


--
-- Name: parametro_certificacion; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.parametro_certificacion (
    parametro text NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date NOT NULL,
    valor jsonb NOT NULL,
    fuente_sha256 text NOT NULL,
    CONSTRAINT parametro_certificacion_check CHECK ((vigente_desde < vigente_hasta))
);


--
-- Name: parametro_rendimiento_tarificador; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.parametro_rendimiento_tarificador (
    parametro text NOT NULL,
    valor integer NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date NOT NULL,
    CONSTRAINT parametro_rendimiento_tarificador_check CHECK ((vigente_desde < vigente_hasta)),
    CONSTRAINT parametro_rendimiento_tarificador_valor_check CHECK ((valor > 0))
);


--
-- Name: puente_viaje_bloque; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.puente_viaje_bloque (
    equipo_id bigint,
    bloque integer,
    id_calculo bigint,
    id_viaje_fuente text,
    nro_viaje bigint,
    es_virtual text,
    fraccion_viaje double precision,
    km_tramo_parte double precision,
    km_ponderados double precision,
    km_ponderados_bloque double precision,
    litros_asignados double precision,
    costo_asignado double precision,
    calidad_bloque text
);


--
-- Name: ref_regresion_camion; Type: TABLE; Schema: combustible; Owner: -
--

CREATE TABLE combustible.ref_regresion_camion (
    equipo_id bigint NOT NULL,
    codigo text,
    modelo text,
    bloques integer,
    l100_cargado double precision,
    error_cargado double precision,
    l100_vacio double precision,
    error_vacio double precision,
    peso_cargado_vs_vacio double precision,
    km_cargado_pct double precision
);


--
-- Name: v_alerta_certificacion_mensual; Type: VIEW; Schema: combustible; Owner: -
--

CREATE VIEW combustible.v_alerta_certificacion_mensual AS
 SELECT (date_trunc('month'::text, t_fin))::date AS mes,
    (sum(litros) FILTER (WHERE certificado) / NULLIF(sum(litros), (0)::double precision)) AS fraccion_certificada
   FROM combustible.fact_bloque_certificacion
  GROUP BY ((date_trunc('month'::text, t_fin))::date);


--
-- Name: v_litros_no_asignables; Type: VIEW; Schema: combustible; Owner: -
--

CREATE VIEW combustible.v_litros_no_asignables AS
 SELECT (sk_fecha_inicio / 10000) AS anio,
    motivo_bloque,
    count(*) AS viajes,
    sum(litros_asignados) AS litros_asignados,
    sum(litros_margen) AS litros_margen,
    sum(ajuste_litros) AS ajuste_litros,
    sum(costo_ajuste) AS costo_ajuste
   FROM combustible.fact_viaje_combustible
  GROUP BY (sk_fecha_inicio / 10000), motivo_bloque;


--
-- Name: fact_viaje_venta; Type: TABLE; Schema: ventas; Owner: -
--

CREATE TABLE ventas.fact_viaje_venta (
    nro_viaje bigint NOT NULL,
    nro_orden text,
    sk_fecha integer,
    cliente_id bigint,
    producto_id bigint,
    sit_stretch_id bigint,
    equipo_id bigint,
    rampla_id bigint,
    operador_grupo text,
    tipo_operador text,
    transportista_id bigint,
    conductor_id bigint,
    conductor_interno text,
    es_retorno text,
    es_tractoreo text,
    estado_viaje text,
    cantidad double precision,
    ingreso double precision,
    valor_orden_venta double precision,
    precio_cotizado double precision,
    valor_facturado double precision,
    facturas integer,
    comision_conductor double precision,
    pago_tercero double precision,
    pct_pago_tercero double precision,
    comision_conductor_observada double precision,
    pago_tercero_observado double precision
);


--
-- Name: v_rendimiento_tarificador_certificado; Type: VIEW; Schema: combustible; Owner: -
--

CREATE VIEW combustible.v_rendimiento_tarificador_certificado AS
 WITH v AS (
         SELECT fact_viaje_venta.nro_viaje,
            fact_viaje_venta.equipo_id,
            fact_viaje_venta.sk_fecha,
            to_date((fact_viaje_venta.sk_fecha)::text, 'YYYYMMDD'::text) AS fecha
           FROM ventas.fact_viaje_venta
        ), x AS (
         SELECT v.nro_viaje,
            v.equipo_id,
            v.sk_fecha,
            v.fecha,
            p.valor AS ventana_bloques,
            h.rendimiento AS rendimiento_previo,
            h.n AS bloques_previos,
            h.ultimo AS fecha_ultimo_previo,
            h.primero AS fecha_primer_previo,
            h.ids AS bloques_previos_ids,
            f.bloque AS primer_bloque_certificado,
            f.t_fin AS fecha_primer_certificado,
            f.rend_cert AS rendimiento_primer_certificado
           FROM (((v
             LEFT JOIN combustible.parametro_rendimiento_tarificador p ON (((p.parametro = 'bloques_recientes'::text) AND (v.fecha >= p.vigente_desde) AND (v.fecha < p.vigente_hasta))))
             LEFT JOIN LATERAL ( SELECT percentile_cont((0.5)::double precision) WITHIN GROUP (ORDER BY b.rend_cert) AS rendimiento,
                    (count(*))::integer AS n,
                    max(b.t_fin) AS ultimo,
                    min(b.t_fin) AS primero,
                    array_agg(b.bloque ORDER BY b.t_fin DESC, b.bloque DESC) AS ids
                   FROM ( SELECT b_1.bloque,
                            b_1.t_fin,
                            b_1.rend_cert
                           FROM combustible.fact_bloque_certificacion b_1
                          WHERE ((b_1.equipo_id = v.equipo_id) AND b_1.certificado AND (b_1.t_fin < v.fecha) AND (p.valor IS NOT NULL))
                          ORDER BY b_1.t_fin DESC, b_1.bloque DESC
                         LIMIT p.valor) b) h ON (true))
             LEFT JOIN LATERAL ( SELECT b.bloque,
                    b.t_fin,
                    b.rend_cert
                   FROM combustible.fact_bloque_certificacion b
                  WHERE ((b.equipo_id = v.equipo_id) AND b.certificado)
                  ORDER BY b.t_fin, b.bloque
                 LIMIT 1) f ON ((h.n = 0)))
        ), y AS (
         SELECT x.nro_viaje,
            x.equipo_id,
            x.sk_fecha,
            x.fecha,
            x.ventana_bloques,
            x.rendimiento_previo,
            x.bloques_previos,
            x.fecha_ultimo_previo,
            x.fecha_primer_previo,
            x.bloques_previos_ids,
            x.primer_bloque_certificado,
            x.fecha_primer_certificado,
            x.rendimiento_primer_certificado,
            COALESCE(((x.bloques_previos = 0) AND (x.fecha >= '2025-01-01'::date) AND (x.fecha < x.fecha_primer_certificado) AND (x.ventana_bloques IS NOT NULL)), false) AS rendimiento_supuesto
           FROM x
        )
 SELECT nro_viaje,
    equipo_id,
    sk_fecha AS fecha_documental_id,
    ventana_bloques,
        CASE
            WHEN (bloques_previos > 0) THEN rendimiento_previo
            WHEN rendimiento_supuesto THEN rendimiento_primer_certificado
            ELSE NULL::double precision
        END AS rendimiento_camion,
        CASE
            WHEN (bloques_previos > 0) THEN bloques_previos
            WHEN rendimiento_supuesto THEN 1
            ELSE 0
        END AS bloques_utilizados,
        CASE
            WHEN (bloques_previos > 0) THEN fecha_ultimo_previo
            WHEN rendimiento_supuesto THEN fecha_primer_certificado
            ELSE NULL::timestamp without time zone
        END AS fecha_ultimo_bloque,
        CASE
            WHEN (bloques_previos > 0) THEN fecha_primer_previo
            WHEN rendimiento_supuesto THEN fecha_primer_certificado
            ELSE NULL::timestamp without time zone
        END AS fecha_primer_bloque,
        CASE
            WHEN (bloques_previos > 0) THEN bloques_previos_ids
            WHEN rendimiento_supuesto THEN ARRAY[primer_bloque_certificado]
            ELSE ARRAY[]::bigint[]
        END AS bloques_utilizados_ids,
    rendimiento_supuesto,
        CASE
            WHEN (bloques_previos > 0) THEN 'mediana de bloques certificados anteriores'::text
            WHEN rendimiento_supuesto THEN 'primer bloque certificado posterior; supuesto desde 2025'::text
            ELSE 'sin historia certificada suficiente'::text
        END AS origen_rendimiento,
    rendimiento_previo,
    bloques_previos,
    fecha_ultimo_previo
   FROM y;


--
-- Name: dim_fecha; Type: TABLE; Schema: comun; Owner: -
--

CREATE TABLE comun.dim_fecha (
    sk_fecha integer NOT NULL,
    fecha date,
    anio integer,
    mes integer,
    dia integer,
    dia_semana integer,
    nombre_dia text,
    es_fin_de_semana text
);


--
-- Name: registro_carga; Type: TABLE; Schema: comun; Owner: -
--

CREATE TABLE comun.registro_carga (
    id bigint NOT NULL,
    area text,
    fecha_carga timestamp without time zone,
    version_script text,
    fecha_cambio text,
    detalle text
);


--
-- Name: registro_carga_id_seq; Type: SEQUENCE; Schema: comun; Owner: -
--

ALTER TABLE comun.registro_carga ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME comun.registro_carga_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: registro_ejecucion; Type: TABLE; Schema: comun; Owner: -
--

CREATE TABLE comun.registro_ejecucion (
    id text NOT NULL,
    fecha timestamp with time zone,
    manifiesto jsonb
);


--
-- Name: fact_viaje_costo_indirecto; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.fact_viaje_costo_indirecto (
    nro_viaje bigint NOT NULL,
    metodo text NOT NULL,
    sk_fecha_inicio integer,
    sit_stretch_id bigint,
    equipo_id bigint,
    rampla_id bigint,
    operador text,
    es_flota_propia text,
    es_tractoreo text,
    usa_rampla_callegari text,
    km_tramo double precision,
    dias_viaje double precision,
    costo_fijo_asignado double precision,
    cf_flota double precision,
    cf_rampla double precision,
    cf_estructura double precision,
    cf_directo_fleteros double precision,
    reposicion_asignada double precision,
    costo_neumatico_asignado double precision,
    costo_indirecto_total double precision,
    tasa_costo_fijo_aplicada double precision,
    tasa_reposicion_aplicada double precision,
    tasa_neumatico_aplicada double precision,
    km_por_dia_aplicado double precision,
    estado_costo_indirecto text
);


--
-- Name: dim_producto_viaje; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.dim_producto_viaje (
    producto_id bigint NOT NULL,
    producto text,
    control_temperatura_fuente text,
    multicarga_fuente text,
    viaje_vacio_fuente text,
    activo_fuente text,
    actualizado_fuente text,
    extraccion_id text,
    fuente_sha256 text
);


--
-- Name: dim_socio_negocio; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.dim_socio_negocio (
    socio_id bigint NOT NULL,
    razon_social text,
    es_cliente_fuente text,
    es_proveedor_fuente text,
    forhire_fuente text,
    flota_mixta_fuente text,
    activo_fuente text,
    actualizado_fuente text,
    extraccion_id text,
    fuente_sha256 text
);


--
-- Name: roles_socio; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.roles_socio (
    socio_id bigint NOT NULL,
    rol text NOT NULL,
    usado_en_viajes boolean,
    marca_fuente text
);


--
-- Name: dim_tramo; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_tramo (
    sk_tramo bigint NOT NULL,
    sit_stretch_id bigint,
    texto_tramo text,
    sk_lugar_origen bigint,
    sk_lugar_destino bigint,
    n_puntos integer,
    km_erp double precision,
    km_ruta_calculada double precision,
    es_recorrido text,
    isactive text,
    estado_deteccion text,
    vigente_desde date,
    vigente_hasta date,
    es_actual text
);


--
-- Name: fact_viaje_peaje; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.fact_viaje_peaje (
    sk_viaje bigint NOT NULL,
    nro_viaje bigint,
    nro_hoja_ruta bigint,
    sk_tramo bigint,
    sk_fecha_inicio integer,
    es_retorno text,
    peaje_referencial double precision,
    peaje_referencial_maximo double precision,
    estado_peaje text
);


--
-- Name: dim_cliente; Type: VIEW; Schema: ventas; Owner: -
--

CREATE VIEW ventas.dim_cliente AS
 SELECT s.socio_id AS cliente_id,
    s.razon_social,
    s.activo_fuente AS activo
   FROM dim_comun.dim_socio_negocio s
  WHERE ((EXISTS ( SELECT 1
           FROM dim_comun.roles_socio r
          WHERE ((r.socio_id = s.socio_id) AND (r.rol = 'cliente'::text)))) OR (EXISTS ( SELECT 1
           FROM ventas.fact_viaje_venta v
          WHERE (v.cliente_id = s.socio_id))))
UNION ALL
 SELECT DISTINCT v.cliente_id,
    NULL::text AS razon_social,
    NULL::text AS activo
   FROM ventas.fact_viaje_venta v
  WHERE ((v.cliente_id IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
           FROM dim_comun.dim_socio_negocio s
          WHERE (s.socio_id = v.cliente_id)))));


--
-- Name: dim_producto_viaje; Type: VIEW; Schema: ventas; Owner: -
--

CREATE VIEW ventas.dim_producto_viaje AS
 SELECT dim_producto_viaje.producto_id,
    dim_producto_viaje.producto,
        CASE
            WHEN (dim_producto_viaje.control_temperatura_fuente IS NULL) THEN NULL::text
            WHEN (upper(TRIM(BOTH FROM dim_producto_viaje.control_temperatura_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
            ELSE 'N'::text
        END AS control_temperatura,
        CASE
            WHEN (dim_producto_viaje.multicarga_fuente IS NULL) THEN NULL::text
            WHEN (upper(TRIM(BOTH FROM dim_producto_viaje.multicarga_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
            ELSE 'N'::text
        END AS multicarga,
        CASE
            WHEN (dim_producto_viaje.viaje_vacio_fuente IS NULL) THEN NULL::text
            WHEN (upper(TRIM(BOTH FROM dim_producto_viaje.viaje_vacio_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
            ELSE 'N'::text
        END AS viaje_vacio
   FROM dim_comun.dim_producto_viaje
UNION ALL
 SELECT DISTINCT v.producto_id,
    NULL::text AS producto,
    NULL::text AS control_temperatura,
    NULL::text AS multicarga,
    NULL::text AS viaje_vacio
   FROM ventas.fact_viaje_venta v
  WHERE ((v.producto_id IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
           FROM dim_comun.dim_producto_viaje p
          WHERE (p.producto_id = v.producto_id)))));


--
-- Name: v_fact_viaje; Type: VIEW; Schema: comun; Owner: -
--

CREATE VIEW comun.v_fact_viaje AS
 SELECT p.nro_viaje,
    p.nro_hoja_ruta,
    f.fecha,
    p.sk_tramo,
    t.texto_tramo,
    p.es_retorno,
    p.peaje_referencial,
    p.peaje_referencial_maximo,
    p.estado_peaje,
    c.equipo_id,
    c.orden_cadena,
    c.km_tramo,
    c.tipo_movimiento,
    c.vacio_fisico,
    c.grupo_carga,
    c.litros_asignados,
    c.metodo_asignacion AS metodo_asignacion_comb,
    c.costo_combustible,
    c.precio_litro_aplicado AS precio_litro_comb,
    vv.cliente_id,
    cl.razon_social AS cliente,
    vv.producto_id,
    pr.producto AS producto_viaje,
    vv.tipo_operador,
    vv.equipo_id AS tracto_id,
    vv.rampla_id,
    vv.conductor_interno,
    vv.estado_viaje,
    vv.ingreso,
    vv.valor_facturado,
    vv.comision_conductor,
    vv.pago_tercero,
    vv.pct_pago_tercero,
    vv.comision_conductor_observada,
    vv.pago_tercero_observado,
    cy.orden_procesado,
    cy.clasificacion_viaje,
    cy.regla_clasificacion,
    cy.tipo_carga_final,
    cy.calidad_trayectoria,
    cy.ciclo_comercial,
    cy.rol_comercial,
    ct.precio_ponderado AS precio_litro_tarificador,
    ct.rendimiento_camion,
    ct.costo_rend_camion AS costo_comb_rend_camion,
    ct.costo_tarificador AS costo_comb_tarificador,
    ct.estado AS estado_comb_tarificador,
    ci.es_flota_propia,
    ci.es_tractoreo,
    ci.km_tramo AS km_tramo_costos,
    cp.dias_viaje,
    cp.usa_rampla_callegari,
    ci.costo_fijo_asignado,
    ci.reposicion_asignada,
    ci.costo_neumatico_asignado,
    ci.costo_indirecto_total,
    ci.estado_costo_indirecto,
    cp.costo_fijo_asignado AS costo_fijo_propuesto,
    cp.cf_flota,
    cp.cf_rampla,
    cp.cf_estructura,
    cp.cf_directo_fleteros,
    cp.reposicion_asignada AS reposicion_propuesta,
    cp.costo_neumatico_asignado AS neumatico_propuesto,
    cp.costo_indirecto_total AS costo_indirecto_propuesto,
    cp.estado_costo_indirecto AS estado_costo_propuesto
   FROM ((((((((((peajes.fact_viaje_peaje p
     JOIN comun.dim_fecha f ON ((f.sk_fecha = p.sk_fecha_inicio)))
     LEFT JOIN peajes.dim_tramo t ON ((t.sk_tramo = p.sk_tramo)))
     LEFT JOIN combustible.fact_viaje_combustible c ON ((c.nro_viaje = p.nro_viaje)))
     LEFT JOIN ventas.fact_viaje_venta vv ON ((vv.nro_viaje = p.nro_viaje)))
     LEFT JOIN ventas.dim_cliente cl ON ((cl.cliente_id = vv.cliente_id)))
     LEFT JOIN ventas.dim_producto_viaje pr ON ((pr.producto_id = vv.producto_id)))
     LEFT JOIN ciclos.fact_viaje_ciclo cy ON ((cy.nro_viaje = p.nro_viaje)))
     LEFT JOIN combustible.fact_viaje_combustible_tarificador ct ON ((ct.nro_viaje = p.nro_viaje)))
     LEFT JOIN costos.fact_viaje_costo_indirecto ci ON (((ci.nro_viaje = p.nro_viaje) AND (ci.metodo = 'tarificador'::text))))
     LEFT JOIN costos.fact_viaje_costo_indirecto cp ON (((cp.nro_viaje = p.nro_viaje) AND (cp.metodo = 'propuesto'::text))));


--
-- Name: fact_costo_viaje; Type: TABLE; Schema: escenarios; Owner: -
--

CREATE TABLE escenarios.fact_costo_viaje (
    nro_viaje bigint NOT NULL,
    sk_escenario integer NOT NULL,
    sk_componente integer NOT NULL,
    sk_fecha integer,
    cantidad double precision,
    tasa double precision,
    monto double precision,
    monto_comercial double precision,
    rol_comercial text,
    ciclo_comercial text,
    estado text,
    estado_comercial text
);


--
-- Name: fact_costo_virtual; Type: TABLE; Schema: escenarios; Owner: -
--

CREATE TABLE escenarios.fact_costo_virtual (
    id_viaje text NOT NULL,
    ciclo_comercial text,
    nro_viaje_destino bigint,
    sk_fecha integer,
    sk_escenario integer NOT NULL,
    sk_componente integer NOT NULL,
    cantidad double precision,
    tasa double precision,
    monto double precision,
    estado text,
    estado_asignacion text,
    estado_tramo text,
    estado_contexto text,
    origen_calculo text,
    cf_flota_virtual double precision,
    cf_rampla_virtual double precision,
    cf_estructura_virtual double precision
);


--
-- Name: fact_cotizacion_viaje; Type: TABLE; Schema: escenarios; Owner: -
--

CREATE TABLE escenarios.fact_cotizacion_viaje (
    nro_viaje bigint NOT NULL,
    sk_fecha integer,
    clasificacion_viaje text,
    modalidad text,
    tipo_carga text,
    es_tractoreo text,
    n_intermedios integer,
    km_cotizados double precision,
    dias double precision,
    combustible double precision,
    termo double precision,
    peaje double precision,
    costo_fijo double precision,
    reposicion double precision,
    neumaticos double precision,
    intermedios double precision,
    costo_total double precision,
    margen double precision,
    cotizacion double precision,
    subtotal_componentes_conocidos double precision,
    subtotal_conocido double precision,
    costo_completo boolean,
    cotizacion_completa boolean,
    n_componentes_faltantes integer,
    faltantes text,
    estado text
);


--
-- Name: v_costo_viaje_resumen; Type: VIEW; Schema: escenarios; Owner: -
--

CREATE VIEW escenarios.v_costo_viaje_resumen AS
 WITH detalle AS (
         SELECT fact_costo_viaje.nro_viaje,
            fact_costo_viaje.sk_escenario,
            fact_costo_viaje.monto,
            fact_costo_viaje.monto_comercial,
            fact_costo_viaje.estado,
            fact_costo_viaje.estado_comercial,
            fact_costo_viaje.rol_comercial,
            fact_costo_viaje.ciclo_comercial,
            false AS es_virtual
           FROM escenarios.fact_costo_viaje
        UNION ALL
         SELECT fact_costo_virtual.nro_viaje_destino,
            fact_costo_virtual.sk_escenario,
            NULL::double precision,
            fact_costo_virtual.monto,
            fact_costo_virtual.estado,
            fact_costo_virtual.estado,
            NULL::text,
            NULL::text,
            true
           FROM escenarios.fact_costo_virtual
          WHERE (fact_costo_virtual.nro_viaje_destino IS NOT NULL)
        ), c AS (
         SELECT detalle.nro_viaje,
            sum(detalle.monto) FILTER (WHERE (detalle.sk_escenario = 1)) AS costo_1_tarificador,
            sum(detalle.monto) FILTER (WHERE (detalle.sk_escenario = 2)) AS costo_2_tarif_rend_camion,
                CASE
                    WHEN bool_and(((detalle.estado = 'ok'::text) AND (detalle.monto IS NOT NULL))) FILTER (WHERE ((detalle.sk_escenario = 3) AND (NOT detalle.es_virtual))) THEN sum(detalle.monto) FILTER (WHERE (detalle.sk_escenario = 3))
                    ELSE NULL::double precision
                END AS costo_3_real,
            sum(detalle.monto_comercial) FILTER (WHERE (detalle.sk_escenario = 1)) AS costo_comercial_1,
            sum(detalle.monto_comercial) FILTER (WHERE (detalle.sk_escenario = 2)) AS costo_comercial_2,
            sum(detalle.monto) FILTER (WHERE (detalle.sk_escenario = 3)) AS subtotal_conocido_3,
            sum(detalle.monto_comercial) FILTER (WHERE (detalle.sk_escenario = 3)) AS subtotal_comercial_conocido_3,
            bool_and(((detalle.estado = 'ok'::text) AND (detalle.monto IS NOT NULL))) FILTER (WHERE ((detalle.sk_escenario = 3) AND (NOT detalle.es_virtual))) AS completo_3,
            bool_and(((detalle.estado_comercial = 'ok'::text) AND (detalle.monto_comercial IS NOT NULL))) FILTER (WHERE (detalle.sk_escenario = 3)) AS completo_comercial_3,
            max(detalle.rol_comercial) AS rol_comercial,
            max(detalle.ciclo_comercial) AS ciclo_comercial,
            bool_and((detalle.estado = 'ok'::text)) FILTER (WHERE (NOT detalle.es_virtual)) AS completo
           FROM detalle
          GROUP BY detalle.nro_viaje
        )
 SELECT vv.nro_viaje,
    f.fecha,
    vv.equipo_id,
    vv.tipo_operador,
    cy.clasificacion_viaje,
    vv.cliente_id,
    cl.razon_social AS cliente,
    tb.tramo AS texto_tramo,
    vv.ingreso,
    q.cotizacion AS cotizacion_tarificador,
    c.costo_1_tarificador,
    c.costo_2_tarif_rend_camion,
    c.costo_3_real,
    (vv.ingreso - c.costo_1_tarificador) AS margen_1,
    (vv.ingreso - c.costo_2_tarif_rend_camion) AS margen_2,
    (vv.ingreso - c.costo_3_real) AS margen_3,
    c.ciclo_comercial,
    c.rol_comercial,
    c.costo_comercial_1,
    c.costo_comercial_2,
        CASE
            WHEN c.completo_comercial_3 THEN c.subtotal_comercial_conocido_3
            ELSE NULL::double precision
        END AS costo_comercial_3,
    (vv.ingreso - c.costo_comercial_1) AS margen_comercial_1,
    (vv.ingreso - c.costo_comercial_2) AS margen_comercial_2,
        CASE
            WHEN c.completo_comercial_3 THEN (vv.ingreso - c.subtotal_comercial_conocido_3)
            ELSE NULL::double precision
        END AS margen_comercial_3,
    (c.completo AND COALESCE(c.completo_comercial_3, false)) AS costos_completos,
    c.subtotal_conocido_3,
    c.subtotal_comercial_conocido_3,
    c.completo_3,
    c.completo_comercial_3
   FROM ((((((c
     JOIN ventas.fact_viaje_venta vv ON ((vv.nro_viaje = c.nro_viaje)))
     LEFT JOIN escenarios.fact_cotizacion_viaje q ON ((q.nro_viaje = c.nro_viaje)))
     LEFT JOIN comun.dim_fecha f ON ((f.sk_fecha = vv.sk_fecha)))
     LEFT JOIN ventas.dim_cliente cl ON ((cl.cliente_id = vv.cliente_id)))
     LEFT JOIN ciclos.fact_viaje_ciclo cy ON ((cy.nro_viaje = c.nro_viaje)))
     LEFT JOIN ciclos.dim_tramo_base tb ON ((tb.sit_stretch_id = COALESCE(cy.sit_stretch_id, vv.sit_stretch_id))));


--
-- Name: v_margen_viaje; Type: VIEW; Schema: comun; Owner: -
--

CREATE VIEW comun.v_margen_viaje AS
 SELECT v.nro_viaje,
    v.nro_hoja_ruta,
    v.fecha,
    v.sk_tramo,
    v.texto_tramo,
    v.es_retorno,
    v.peaje_referencial,
    v.peaje_referencial_maximo,
    v.estado_peaje,
    v.equipo_id,
    v.orden_cadena,
    v.km_tramo,
    v.tipo_movimiento,
    v.vacio_fisico,
    v.grupo_carga,
    v.litros_asignados,
    v.metodo_asignacion_comb,
    v.costo_combustible,
    v.precio_litro_comb,
    v.cliente_id,
    v.cliente,
    v.producto_id,
    v.producto_viaje,
    v.tipo_operador,
    v.tracto_id,
    v.rampla_id,
    v.conductor_interno,
    v.estado_viaje,
    v.ingreso,
    v.valor_facturado,
    v.comision_conductor,
    v.pago_tercero,
    v.pct_pago_tercero,
    v.comision_conductor_observada,
    v.pago_tercero_observado,
    v.orden_procesado,
    v.clasificacion_viaje,
    v.regla_clasificacion,
    v.tipo_carga_final,
    v.calidad_trayectoria,
    v.ciclo_comercial,
    v.rol_comercial,
    v.precio_litro_tarificador,
    v.rendimiento_camion,
    v.costo_comb_rend_camion,
    v.costo_comb_tarificador,
    v.estado_comb_tarificador,
    v.es_flota_propia,
    v.es_tractoreo,
    v.km_tramo_costos,
    v.dias_viaje,
    v.usa_rampla_callegari,
    v.costo_fijo_asignado,
    v.reposicion_asignada,
    v.costo_neumatico_asignado,
    v.costo_indirecto_total,
    v.estado_costo_indirecto,
    v.costo_fijo_propuesto,
    v.cf_flota,
    v.cf_rampla,
    v.cf_estructura,
    v.cf_directo_fleteros,
    v.reposicion_propuesta,
    v.neumatico_propuesto,
    v.costo_indirecto_propuesto,
    v.estado_costo_propuesto,
        CASE
            WHEN (v.tipo_operador = 'propio'::text) THEN (((v.ingreso - v.costo_combustible) - v.peaje_referencial) - v.comision_conductor_observada)
            WHEN (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) THEN (v.ingreso - v.pago_tercero_observado)
            ELSE NULL::double precision
        END AS margen_contribucion,
        CASE
            WHEN (v.tipo_operador = 'propio'::text) THEN (((v.ingreso - v.costo_comb_rend_camion) - v.peaje_referencial) - v.comision_conductor_observada)
            ELSE NULL::double precision
        END AS margen_contribucion_comb_estimado,
    e.margen_3 AS margen_operacional,
        CASE
            WHEN (v.ingreso IS NULL) THEN 'sin ingreso'::text
            WHEN ((v.tipo_operador IS NULL) OR (v.tipo_operador <> ALL (ARRAY['propio'::text, 'fletero'::text, 'caterina'::text]))) THEN 'operador sin clasificar'::text
            WHEN (e.nro_viaje IS NULL) THEN 'sin escenario 3; recargar escenarios'::text
            WHEN (NOT COALESCE(e.completo_3, false)) THEN 'costos fisicos incompletos'::text
            WHEN (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) THEN 'completo (tercero)'::text
            ELSE 'completo'::text
        END AS cobertura_margen,
    e.margen_comercial_3 AS margen_operacional_comercial,
    COALESCE(e.completo_3, false) AS costos_fisicos_completos,
    COALESCE(e.completo_comercial_3, false) AS costos_comerciales_completos,
    e.costo_3_real AS costo_fisico_escenario_3,
    e.costo_comercial_3 AS costo_comercial_escenario_3,
    'escenario 3; ingreso registrado; costo fisico modelado'::text AS definicion_margen_operacional,
    'escenario 3; ingreso registrado; costo redistribuido por ciclo'::text AS definicion_margen_comercial,
    'parcial: combustible y peaje referencial mas comision; terceros: pago observado'::text AS alcance_contribucion,
        CASE
            WHEN (v.ingreso IS NULL) THEN 'sin ingreso'::text
            WHEN ((v.tipo_operador = 'propio'::text) AND (v.comision_conductor_observada IS NULL)) THEN 'sin comision observada'::text
            WHEN ((v.tipo_operador = 'propio'::text) AND (v.costo_combustible IS NULL)) THEN 'sin combustible asignado'::text
            WHEN ((v.tipo_operador = 'propio'::text) AND (v.peaje_referencial IS NULL)) THEN 'sin peaje referencial'::text
            WHEN (v.tipo_operador = 'propio'::text) THEN 'disponible dentro del alcance parcial'::text
            WHEN ((v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) AND (v.pago_tercero_observado IS NULL)) THEN 'sin pago observado'::text
            WHEN (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) THEN 'disponible dentro del alcance parcial'::text
            ELSE 'operador sin clasificar'::text
        END AS cobertura_contribucion,
        CASE
            WHEN (v.ingreso IS NULL) THEN 'sin ingreso'::text
            WHEN ((v.tipo_operador IS NULL) OR (v.tipo_operador <> 'propio'::text)) THEN 'no aplica'::text
            WHEN (v.comision_conductor_observada IS NULL) THEN 'sin comision observada'::text
            WHEN (v.costo_comb_rend_camion IS NULL) THEN 'sin combustible estimado'::text
            WHEN (v.peaje_referencial IS NULL) THEN 'sin peaje referencial'::text
            ELSE 'disponible dentro del alcance parcial'::text
        END AS cobertura_contribucion_comb_estimado
   FROM (comun.v_fact_viaje v
     LEFT JOIN escenarios.v_costo_viaje_resumen e ON ((e.nro_viaje = v.nro_viaje)));


--
-- Name: dim_centro_costo; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.dim_centro_costo (
    c_activity_id bigint NOT NULL,
    nombre text,
    bolsa text,
    rol text,
    origen text
);


--
-- Name: dim_cuenta_contable; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.dim_cuenta_contable (
    sk_cuenta integer NOT NULL,
    codigo text,
    nombre text,
    tipo text,
    linea_eerr text,
    nombre_linea_eerr text,
    clase_tarificador text,
    clase_real text,
    inductor text,
    origen_clasificacion text,
    nota text
);


--
-- Name: fact_capacidad_bolsa; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.fact_capacidad_bolsa (
    anio integer NOT NULL,
    bolsa text NOT NULL,
    monto_bolsa numeric(18,2),
    capacidad_dias numeric(18,2),
    cuota_media numeric(18,2),
    dias_usados numeric(18,2),
    absorbido numeric(18,2),
    capacidad_ociosa numeric(18,2),
    pct_absorbido numeric(8,4)
);


--
-- Name: fact_contabilidad_mensual; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.fact_contabilidad_mensual (
    sk_fecha_mes integer,
    anio integer,
    mes integer,
    sk_cuenta integer,
    unidad_negocio_id bigint,
    equipo_id bigint,
    es_un_fleteros text,
    c_activity_id bigint,
    monto numeric(16,2),
    n_lineas integer,
    bolsa text
);


--
-- Name: parametro_costo; Type: TABLE; Schema: costos; Owner: -
--

CREATE TABLE costos.parametro_costo (
    parametro text NOT NULL,
    metodo text NOT NULL,
    ambito text NOT NULL,
    valor numeric(18,6),
    unidad text,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    parametro_padre text,
    origen text,
    fuente_detalle text,
    monto_base numeric(18,2),
    cantidad_inductor numeric(18,4),
    nota text
);


--
-- Name: v_ref_centro_contable; Type: VIEW; Schema: costos; Owner: -
--

CREATE VIEW costos.v_ref_centro_contable AS
 WITH ids AS (
         SELECT dim_centro_costo.c_activity_id
           FROM costos.dim_centro_costo
        UNION
         SELECT fact_contabilidad_mensual.c_activity_id
           FROM costos.fact_contabilidad_mensual
          WHERE (fact_contabilidad_mensual.c_activity_id IS NOT NULL)
        )
 SELECT i.c_activity_id,
    d.nombre,
        CASE
            WHEN (d.c_activity_id IS NULL) THEN 'observado_sin_catalogo'::text
            WHEN (d.origen = 'maestro'::text) THEN 'catalogo_erp'::text
            WHEN (d.origen = 'manual'::text) THEN 'documentado_manual'::text
            ELSE 'procedencia_no_identificada'::text
        END AS estado_descripcion,
    d.origen AS origen_descripcion
   FROM (ids i
     LEFT JOIN costos.dim_centro_costo d USING (c_activity_id));


--
-- Name: dim_equipo; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.dim_equipo (
    equipo_id bigint NOT NULL,
    codigo text,
    patente text,
    nombre_fuente text,
    categoria_fuente text,
    marca text,
    modelo text,
    anio_fabricacion_fuente text,
    es_flota_fuente text,
    es_propio_fuente text,
    estado_fuente text,
    grupo_empresa_fuente text,
    centro_costo_id bigint,
    unidad_negocio_id bigint,
    activo_fuente text,
    actualizado_fuente text,
    extraccion_id text,
    fuente_sha256 text
);


--
-- Name: v_ref_unidad_negocio; Type: VIEW; Schema: costos; Owner: -
--

CREATE VIEW costos.v_ref_unidad_negocio AS
 WITH ids AS (
         SELECT fact_contabilidad_mensual.unidad_negocio_id
           FROM costos.fact_contabilidad_mensual
          WHERE (fact_contabilidad_mensual.unidad_negocio_id IS NOT NULL)
        UNION
         SELECT dim_equipo.unidad_negocio_id
           FROM dim_comun.dim_equipo
          WHERE (dim_equipo.unidad_negocio_id IS NOT NULL)
        ), equipos AS (
         SELECT dim_equipo.unidad_negocio_id,
            count(*) AS equipos_actuales,
                CASE
                    WHEN (count(*) = 1) THEN min(dim_equipo.equipo_id)
                    ELSE NULL::bigint
                END AS equipo_unico_actual
           FROM dim_comun.dim_equipo
          WHERE (dim_equipo.unidad_negocio_id IS NOT NULL)
          GROUP BY dim_equipo.unidad_negocio_id
        )
 SELECT i.unidad_negocio_id,
    NULL::text AS nombre,
    'sin_catalogo_empresarial'::text AS estado_descripcion,
    COALESCE(e.equipos_actuales, (0)::bigint) AS equipos_actuales,
    e.equipo_unico_actual,
        CASE
            WHEN (e.equipos_actuales IS NULL) THEN 'sin_equipo_en_maestro_actual'::text
            WHEN (e.equipos_actuales = 1) THEN 'un_equipo_en_maestro_actual'::text
            ELSE 'varios_equipos_en_maestro_actual'::text
        END AS estado_correspondencia
   FROM (ids i
     LEFT JOIN equipos e USING (unidad_negocio_id));


--
-- Name: VIEW v_ref_unidad_negocio; Type: COMMENT; Schema: costos; Owner: -
--

COMMENT ON VIEW costos.v_ref_unidad_negocio IS 'IDs observados y correspondencia con fotografía actual de equipos. No acredita relación histórica ni identifica toda UN como vehículo.';


--
-- Name: v_referencias_contabilidad; Type: VIEW; Schema: costos; Owner: -
--

CREATE VIEW costos.v_referencias_contabilidad AS
 SELECT f.sk_fecha_mes,
    f.anio,
    f.mes,
    f.sk_cuenta,
    f.unidad_negocio_id,
    f.equipo_id,
    f.es_un_fleteros,
    f.c_activity_id,
    f.monto,
    f.n_lineas,
    f.bolsa,
    COALESCE(c.estado_descripcion, 'sin_id'::text) AS estado_descripcion_centro,
    COALESCE(u.estado_descripcion, 'sin_id'::text) AS estado_descripcion_un,
    COALESCE(u.estado_correspondencia, 'sin_un'::text) AS correspondencia_un_actual,
    u.equipo_unico_actual AS equipo_candidato_actual,
        CASE
            WHEN (f.unidad_negocio_id IS NULL) THEN 'sin_un'::text
            WHEN (f.equipo_id IS NOT NULL) THEN 'equipo_asignado_en_hecho'::text
            WHEN (COALESCE(u.equipos_actuales, (0)::bigint) = 0) THEN 'sin_correspondencia_maestro_actual'::text
            WHEN (u.equipos_actuales > 1) THEN 'correspondencia_multiple'::text
            ELSE 'equipo_unico_actual_no_asignado'::text
        END AS estado_asignacion_equipo
   FROM ((costos.fact_contabilidad_mensual f
     LEFT JOIN costos.v_ref_centro_contable c USING (c_activity_id))
     LEFT JOIN costos.v_ref_unidad_negocio u USING (unidad_negocio_id));


--
-- Name: v_cobertura_referencias_mensual; Type: VIEW; Schema: costos; Owner: -
--

CREATE VIEW costos.v_cobertura_referencias_mensual AS
 SELECT anio,
    mes,
    estado_descripcion_centro,
    estado_descripcion_un,
    estado_asignacion_equipo,
    count(*) AS grupos_contables,
    sum(n_lineas) AS lineas_fuente,
    sum(monto) AS monto_neto,
    sum(abs(monto)) AS suma_absoluta_montos_agrupados
   FROM costos.v_referencias_contabilidad
  GROUP BY anio, mes, estado_descripcion_centro, estado_descripcion_un, estado_asignacion_equipo;


--
-- Name: VIEW v_cobertura_referencias_mensual; Type: COMMENT; Schema: costos; Owner: -
--

COMMENT ON VIEW costos.v_cobertura_referencias_mensual IS 'Montos bajo alcance contable del ETL: neto y absoluto de grupos; no equivale a movimientos brutos ni balance completo. Las referencias desconocidas no se eliminan.';


--
-- Name: v_reglas_centro_costo; Type: VIEW; Schema: costos; Owner: -
--

CREATE VIEW costos.v_reglas_centro_costo AS
 SELECT c_activity_id,
    bolsa,
    rol
   FROM costos.dim_centro_costo
  WHERE ((NULLIF(TRIM(BOTH FROM bolsa), ''::text) IS NOT NULL) OR (NULLIF(TRIM(BOTH FROM rol), ''::text) IS NOT NULL));


--
-- Name: VIEW v_reglas_centro_costo; Type: COMMENT; Schema: costos; Owner: -
--

COMMENT ON VIEW costos.v_reglas_centro_costo IS 'Proyección de las reglas existentes. No es un catálogo empresarial ni una nueva fuente de asignación.';


--
-- Name: dim_conductor; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.dim_conductor (
    conductor_id bigint NOT NULL,
    conductor_origen_id bigint,
    nombre_completo text,
    forhire_fuente text,
    activo_fuente text,
    estado_fuente text,
    actualizado_fuente text,
    extraccion_id text,
    fuente_sha256 text
);


--
-- Name: dim_fecha; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.dim_fecha AS
 SELECT sk_fecha,
    fecha,
    anio,
    mes,
    dia,
    dia_semana
   FROM comun.dim_fecha;


--
-- Name: dim_tramo_comercial; Type: TABLE; Schema: dim_comun; Owner: -
--

CREATE TABLE dim_comun.dim_tramo_comercial (
    tramo_id bigint NOT NULL,
    nombre_fuente text,
    km_fuente double precision,
    activo_fuente text,
    actualizado_fuente text,
    extraccion_id text,
    fuente_sha256 text
);


--
-- Name: v_dim_equipo; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.v_dim_equipo AS
 SELECT equipo_id,
    codigo,
    patente,
    nombre_fuente,
    categoria_fuente,
    marca,
    modelo,
    anio_fabricacion_fuente,
    es_flota_fuente,
    es_propio_fuente,
    estado_fuente,
    grupo_empresa_fuente,
    centro_costo_id,
    unidad_negocio_id,
    activo_fuente,
    actualizado_fuente,
    extraccion_id,
    fuente_sha256,
        CASE
            WHEN (upper(TRIM(BOTH FROM categoria_fuente)) ~~ 'TRACTO%'::text) THEN 'tracto'::text
            WHEN (upper(TRIM(BOTH FROM categoria_fuente)) = ANY (ARRAY['THERMO'::text, 'PLANA'::text, 'CAMA BAJA'::text, 'FURGON'::text])) THEN 'rampla'::text
            ELSE 'otro'::text
        END AS tipo_equipo_derivado
   FROM dim_comun.dim_equipo e;


--
-- Name: v_dim_otros_equipos; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.v_dim_otros_equipos AS
 SELECT equipo_id,
    codigo,
    patente,
    nombre_fuente,
    categoria_fuente,
    marca,
    modelo,
    anio_fabricacion_fuente,
    es_flota_fuente,
    es_propio_fuente,
    estado_fuente,
    grupo_empresa_fuente,
    centro_costo_id,
    unidad_negocio_id,
    activo_fuente,
    actualizado_fuente,
    extraccion_id,
    fuente_sha256,
    tipo_equipo_derivado
   FROM dim_comun.v_dim_equipo
  WHERE (tipo_equipo_derivado = 'otro'::text);


--
-- Name: v_dim_rampla; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.v_dim_rampla AS
 SELECT equipo_id,
    codigo,
    patente,
    nombre_fuente,
    categoria_fuente,
    marca,
    modelo,
    anio_fabricacion_fuente,
    es_flota_fuente,
    es_propio_fuente,
    estado_fuente,
    grupo_empresa_fuente,
    centro_costo_id,
    unidad_negocio_id,
    activo_fuente,
    actualizado_fuente,
    extraccion_id,
    fuente_sha256,
    tipo_equipo_derivado
   FROM dim_comun.v_dim_equipo
  WHERE (tipo_equipo_derivado = 'rampla'::text);


--
-- Name: v_dim_tracto; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.v_dim_tracto AS
 SELECT equipo_id,
    codigo,
    patente,
    nombre_fuente,
    categoria_fuente,
    marca,
    modelo,
    anio_fabricacion_fuente,
    es_flota_fuente,
    es_propio_fuente,
    estado_fuente,
    grupo_empresa_fuente,
    centro_costo_id,
    unidad_negocio_id,
    activo_fuente,
    actualizado_fuente,
    extraccion_id,
    fuente_sha256,
    tipo_equipo_derivado
   FROM dim_comun.v_dim_equipo
  WHERE (tipo_equipo_derivado = 'tracto'::text);


--
-- Name: v_tramo_version_peaje; Type: VIEW; Schema: dim_comun; Owner: -
--

CREATE VIEW dim_comun.v_tramo_version_peaje AS
 SELECT p.sk_tramo,
    p.sit_stretch_id AS tramo_id,
    p.texto_tramo AS nombre_version_peaje,
    p.km_erp AS km_erp_peaje,
    p.km_ruta_calculada,
    p.vigente_desde,
    p.vigente_hasta,
    p.es_actual,
    m.nombre_fuente AS nombre_catalogo_actual,
        CASE
            WHEN (m.tramo_id IS NULL) THEN 'sin_maestro_actual'::text
            ELSE 'encontrado'::text
        END AS estado_maestro
   FROM (peajes.dim_tramo p
     LEFT JOIN dim_comun.dim_tramo_comercial m ON ((m.tramo_id = p.sit_stretch_id)));


--
-- Name: v_capacidad_periodica; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_capacidad_periodica AS
 WITH r AS MATERIALIZED (
         SELECT (fact_viaje_costo_indirecto.sk_fecha_inicio / 100) AS mes_id,
            count(*) AS viajes,
            count(*) FILTER (WHERE (fact_viaje_costo_indirecto.estado_costo_indirecto IS DISTINCT FROM 'ok'::text)) AS viajes_pendientes,
            sum(fact_viaje_costo_indirecto.cf_flota) FILTER (WHERE (fact_viaje_costo_indirecto.estado_costo_indirecto = 'ok'::text)) AS flota,
            sum(fact_viaje_costo_indirecto.cf_rampla) FILTER (WHERE (fact_viaje_costo_indirecto.estado_costo_indirecto = 'ok'::text)) AS rampla,
            sum(fact_viaje_costo_indirecto.cf_estructura) FILTER (WHERE (fact_viaje_costo_indirecto.estado_costo_indirecto = 'ok'::text)) AS estructura,
            sum(fact_viaje_costo_indirecto.cf_directo_fleteros) FILTER (WHERE (fact_viaje_costo_indirecto.estado_costo_indirecto = 'ok'::text)) AS directo_fleteros,
            max(fact_viaje_costo_indirecto.sk_fecha_inicio) AS ultima_fecha
           FROM costos.fact_viaje_costo_indirecto
          WHERE (fact_viaje_costo_indirecto.metodo = 'propuesto'::text)
          GROUP BY (fact_viaje_costo_indirecto.sk_fecha_inicio / 100)
        ), v AS MATERIALIZED (
         SELECT (fact_costo_virtual.sk_fecha / 100) AS mes_id,
            count(*) AS virtuales,
            count(*) FILTER (WHERE ((fact_costo_virtual.estado IS DISTINCT FROM 'ok'::text) OR (fact_costo_virtual.monto IS NULL))) AS virtuales_pendientes,
            sum(fact_costo_virtual.cf_flota_virtual) FILTER (WHERE (fact_costo_virtual.estado = ANY (ARRAY['ok'::text, 'parcial: cuota de rampla sin confirmar'::text]))) AS flota,
            sum(fact_costo_virtual.cf_rampla_virtual) FILTER (WHERE (fact_costo_virtual.estado = 'ok'::text)) AS rampla,
            sum(fact_costo_virtual.cf_estructura_virtual) FILTER (WHERE (fact_costo_virtual.estado = ANY (ARRAY['ok'::text, 'parcial: cuota de rampla sin confirmar'::text]))) AS estructura
           FROM escenarios.fact_costo_virtual
          WHERE ((fact_costo_virtual.sk_escenario = 3) AND (fact_costo_virtual.sk_componente = 4))
          GROUP BY (fact_costo_virtual.sk_fecha / 100)
        ), cortes AS (
         SELECT (r.mes_id / 100) AS anio,
            to_date((max(r.ultima_fecha))::text, 'YYYYMMDD'::text) AS hasta
           FROM r
          GROUP BY (r.mes_id / 100)
        ), meses AS (
         SELECT c.anio,
            c.hasta,
            (g.g)::date AS desde,
            LEAST(((g.g + '1 mon'::interval))::date, (c.hasta + 1)) AS fin,
            ((g.g + '1 mon'::interval))::date AS fin_mes
           FROM (cortes c
             CROSS JOIN LATERAL generate_series((make_date(c.anio, 1, 1))::timestamp with time zone, date_trunc('month'::text, (c.hasta)::timestamp with time zone), '1 mon'::interval) g(g))
        ), base AS (
         SELECT m.anio,
            m.hasta,
            m.desde,
            m.fin,
            m.fin_mes,
            b_1.bolsa,
            to_char((m.desde)::timestamp with time zone, 'YYYY-MM'::text) AS mes,
            COALESCE(r.viajes, (0)::bigint) AS viajes,
            COALESCE(r.viajes_pendientes, (0)::bigint) AS viajes_pendientes,
            COALESCE(v.virtuales, (0)::bigint) AS virtuales,
            COALESCE(v.virtuales_pendientes, (0)::bigint) AS virtuales_pendientes,
            COALESCE(
                CASE b_1.bolsa
                    WHEN 'flota'::text THEN r.flota
                    WHEN 'rampla'::text THEN r.rampla
                    WHEN 'estructura'::text THEN r.estructura
                    ELSE r.directo_fleteros
                END, (0)::double precision) AS absorcion_real,
            COALESCE(
                CASE b_1.bolsa
                    WHEN 'flota'::text THEN v.flota
                    WHEN 'rampla'::text THEN v.rampla
                    WHEN 'estructura'::text THEN v.estructura
                    ELSE (0)::double precision
                END, (0)::double precision) AS absorcion_virtual_conocida
           FROM (((meses m
             CROSS JOIN ( VALUES ('flota'::text), ('rampla'::text), ('estructura'::text), ('directo_fleteros'::text)) b_1(bolsa))
             LEFT JOIN r ON ((r.mes_id = ((m.anio * 100) + (EXTRACT(month FROM m.desde))::integer))))
             LEFT JOIN v ON ((v.mes_id = ((m.anio * 100) + (EXTRACT(month FROM m.desde))::integer))))
        ), pres AS (
         SELECT b_1.mes,
            b_1.bolsa,
            bool_and(((p_1.n = 1) AND (p_1.monto IS NOT NULL))) AS presupuesto_completo,
            sum((p_1.monto / (EXTRACT(doy FROM (make_date((b_1.anio + 1), 1, 1) - 1)))::double precision)) AS presupuesto
           FROM ((base b_1
             CROSS JOIN LATERAL generate_series((b_1.desde)::timestamp with time zone, ((b_1.fin - 1))::timestamp with time zone, '1 day'::interval) dia(dia))
             CROSS JOIN LATERAL ( SELECT count(*) AS n,
                    (max(parametro_costo.monto_base))::double precision AS monto
                   FROM costos.parametro_costo
                  WHERE ((parametro_costo.parametro = 'cuota_diaria'::text) AND (parametro_costo.metodo = 'propuesto'::text) AND (parametro_costo.ambito = b_1.bolsa) AND ((dia.dia)::date >= parametro_costo.vigente_desde) AND ((dia.dia)::date < parametro_costo.vigente_hasta))) p_1)
          GROUP BY b_1.mes, b_1.bolsa
        )
 SELECT b.mes,
    b.anio,
    b.bolsa,
    b.desde,
    (b.fin - 1) AS hasta,
    (b.fin < b.fin_mes) AS parcial,
        CASE
            WHEN p.presupuesto_completo THEN p.presupuesto
            ELSE NULL::double precision
        END AS presupuesto,
    b.absorcion_real,
    b.absorcion_virtual_conocida,
    (b.absorcion_real + b.absorcion_virtual_conocida) AS absorcion_conocida,
        CASE
            WHEN p.presupuesto_completo THEN ((p.presupuesto - b.absorcion_real) - b.absorcion_virtual_conocida)
            ELSE NULL::double precision
        END AS residual_provisional,
        CASE
            WHEN (p.presupuesto_completo AND (p.presupuesto <> (0)::double precision)) THEN (((100)::double precision * (b.absorcion_real + b.absorcion_virtual_conocida)) / p.presupuesto)
            ELSE NULL::double precision
        END AS porcentaje_absorbido,
    p.presupuesto_completo,
    b.viajes,
    b.viajes_pendientes,
    b.virtuales,
    b.virtuales_pendientes,
    'prorrateo calendario; residual tras absorcion conocida, no disponibilidad fisica'::text AS alcance
   FROM (base b
     JOIN pres p USING (mes, bolsa));


--
-- Name: v_referencia_contable_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_referencia_contable_mensual AS
 WITH c AS (
         SELECT ((f.anio * 100) + f.mes) AS mes_id,
            count(*) AS registros_contables,
            (- sum((f.monto)::numeric) FILTER (WHERE (d.linea_eerr = '001'::text))) AS ingreso_explotacion,
            (- sum((f.monto)::numeric) FILTER (WHERE (d.codigo ~~ '4%'::text))) AS ingreso_total,
            sum((f.monto)::numeric) FILTER (WHERE (d.codigo ~~ '3%'::text)) AS gasto_total,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE (d.linea_eerr = ANY (ARRAY['002'::text, '003'::text, '004'::text, '005'::text, '006'::text, '007'::text, '008'::text]))), (0)::numeric) AS costo_explotacion,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE (d.linea_eerr = '009'::text)), (0)::numeric) AS administracion_ventas,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE (d.linea_eerr = '014'::text)), (0)::numeric) AS depreciacion_registrada,
            count(*) FILTER (WHERE (d.linea_eerr = '014'::text)) AS registros_depreciacion,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE ((d.clase_tarificador = 'fijo'::text) AND (d.codigo <> '310020104'::text))), (0)::numeric) AS fijo_comparable,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE ((d.clase_tarificador = 'fijo'::text) AND (d.codigo <> '310020104'::text) AND (d.linea_eerr = ANY (ARRAY['002'::text, '003'::text, '004'::text, '005'::text, '006'::text, '007'::text, '008'::text, '009'::text])))), (0)::numeric) AS fijo_operacional,
            COALESCE(sum((f.monto)::numeric) FILTER (WHERE (d.linea_eerr = '000'::text)), (0)::numeric) AS monto_sin_linea_eerr,
            count(*) FILTER (WHERE ((d.linea_eerr = '000'::text) OR (d.linea_eerr IS NULL))) AS registros_sin_linea_eerr
           FROM (costos.fact_contabilidad_mensual f
             JOIN costos.dim_cuenta_contable d USING (sk_cuenta))
          GROUP BY ((f.anio * 100) + f.mes)
        ), r AS (
         SELECT c.mes_id,
            c.registros_contables,
            c.ingreso_explotacion,
            c.ingreso_total,
            c.gasto_total,
            c.costo_explotacion,
            c.administracion_ventas,
            c.depreciacion_registrada,
            c.registros_depreciacion,
            c.fijo_comparable,
            c.fijo_operacional,
            c.monto_sin_linea_eerr,
            c.registros_sin_linea_eerr,
            (c.ingreso_total - c.ingreso_explotacion) AS otros_ingresos,
            ((c.ingreso_explotacion - c.costo_explotacion) - c.administracion_ventas) AS resultado_operacional_antes_depreciacion,
            (c.ingreso_total - c.gasto_total) AS resultado_total
           FROM c
        )
 SELECT mes_id,
    registros_contables,
    ingreso_explotacion,
    ingreso_total,
    gasto_total,
    costo_explotacion,
    administracion_ventas,
    depreciacion_registrada,
    registros_depreciacion,
    fijo_comparable,
    fijo_operacional,
    monto_sin_linea_eerr,
    registros_sin_linea_eerr,
    otros_ingresos,
    resultado_operacional_antes_depreciacion,
    resultado_total,
    (resultado_operacional_antes_depreciacion - depreciacion_registrada) AS resultado_operacional_despues_depreciacion,
    (resultado_operacional_antes_depreciacion / NULLIF(ingreso_explotacion, (0)::numeric)) AS margen_operacional_antes_depreciacion_pct,
    (resultado_total / NULLIF(ingreso_total, (0)::numeric)) AS margen_total_pct,
    (mes_id = max(mes_id) OVER ()) AS ultimo_mes_observado,
    'EERR por periodo contable; ultimo mes observado no acredita cierre; porcentajes en fraccion; depreciacion sin registros no implica costo economico cero'::text AS alcance
   FROM r;


--
-- Name: v_conciliacion_operacional_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_conciliacion_operacional_mensual AS
 WITH ventas AS (
         SELECT (fact_viaje_venta.sk_fecha / 100) AS mes_id,
            count(*) AS viajes_reales,
            count(*) FILTER (WHERE (fact_viaje_venta.ingreso IS NULL)) AS ingresos_faltantes,
            sum((fact_viaje_venta.ingreso)::numeric) AS ingreso_estimado_conocido,
            sum((fact_viaje_venta.valor_facturado)::numeric) AS facturado_por_fecha_viaje,
            max(fact_viaje_venta.sk_fecha) AS ultima_fecha_viaje
           FROM ventas.fact_viaje_venta
          GROUP BY (fact_viaje_venta.sk_fecha / 100)
        ), fuente AS (
         SELECT (fact_costo_viaje.sk_fecha / 100) AS mes_id,
            fact_costo_viaje.sk_escenario,
            fact_costo_viaje.sk_componente,
            fact_costo_viaje.monto,
            fact_costo_viaje.estado,
            false AS virtual
           FROM escenarios.fact_costo_viaje
          WHERE (fact_costo_viaje.sk_escenario = 3)
        UNION ALL
         SELECT (fact_costo_virtual.sk_fecha / 100),
            fact_costo_virtual.sk_escenario,
            fact_costo_virtual.sk_componente,
            fact_costo_virtual.monto,
            fact_costo_virtual.estado,
            true
           FROM escenarios.fact_costo_virtual
          WHERE (fact_costo_virtual.sk_escenario = 3)
        ), costos AS (
         SELECT fuente.mes_id,
            fuente.sk_escenario,
            count(*) AS registros_componentes,
            count(*) FILTER (WHERE (fuente.monto IS NULL)) AS montos_faltantes,
            count(*) FILTER (WHERE ((fuente.estado IS DISTINCT FROM 'ok'::text) OR (fuente.monto IS NULL))) AS componentes_no_validos,
            count(*) FILTER (WHERE fuente.virtual) AS componentes_virtuales,
            sum((fuente.monto)::numeric) AS costo_modelado_conocido,
            COALESCE(sum((fuente.monto)::numeric) FILTER (WHERE (fuente.sk_componente = 4)), (0)::numeric) AS fijo_modelado_conocido,
            COALESCE(sum((fuente.monto)::numeric) FILTER (WHERE (fuente.sk_componente = 5)), (0)::numeric) AS reposicion_modelada_conocida
           FROM fuente
          GROUP BY fuente.mes_id, fuente.sk_escenario
        ), meses AS (
         SELECT ventas.mes_id
           FROM ventas
        UNION
         SELECT costos.mes_id
           FROM costos
        UNION
         SELECT v_referencia_contable_mensual.mes_id
           FROM ds_gestion.v_referencia_contable_mensual
        ), base AS (
         SELECT m.mes_id,
            e.sk_escenario,
            v.viajes_reales,
            v.ingresos_faltantes,
            v.ingreso_estimado_conocido,
            v.facturado_por_fecha_viaje,
            v.ultima_fecha_viaje,
            k.registros_componentes,
            k.montos_faltantes,
            k.componentes_no_validos,
            k.componentes_virtuales,
            k.costo_modelado_conocido,
            k.fijo_modelado_conocido,
            k.reposicion_modelada_conocida,
            c.ingreso_explotacion,
            c.ingreso_total,
            c.otros_ingresos,
            c.gasto_total,
            c.costo_explotacion,
            c.administracion_ventas,
            c.depreciacion_registrada,
            c.registros_depreciacion,
            c.fijo_comparable AS fijo_contable_comparable,
            c.fijo_operacional AS fijo_contable_operacional,
            c.resultado_operacional_antes_depreciacion,
            c.resultado_operacional_despues_depreciacion,
            c.resultado_total,
            c.registros_sin_linea_eerr,
            c.monto_sin_linea_eerr,
            (v.mes_id IS NOT NULL) AS hay_ventas,
            (k.mes_id IS NOT NULL) AS hay_costos,
            (c.mes_id IS NOT NULL) AS hay_contabilidad,
            (v.ingreso_estimado_conocido - k.costo_modelado_conocido) AS margen_modelado_subtotal
           FROM ((((meses m
             CROSS JOIN ( VALUES (3)) e(sk_escenario))
             LEFT JOIN ventas v USING (mes_id))
             LEFT JOIN costos k USING (mes_id, sk_escenario))
             LEFT JOIN ds_gestion.v_referencia_contable_mensual c USING (mes_id))
        ), pasos AS (
         SELECT base.mes_id,
            base.sk_escenario,
            base.viajes_reales,
            base.ingresos_faltantes,
            base.ingreso_estimado_conocido,
            base.facturado_por_fecha_viaje,
            base.ultima_fecha_viaje,
            base.registros_componentes,
            base.montos_faltantes,
            base.componentes_no_validos,
            base.componentes_virtuales,
            base.costo_modelado_conocido,
            base.fijo_modelado_conocido,
            base.reposicion_modelada_conocida,
            base.ingreso_explotacion,
            base.ingreso_total,
            base.otros_ingresos,
            base.gasto_total,
            base.costo_explotacion,
            base.administracion_ventas,
            base.depreciacion_registrada,
            base.registros_depreciacion,
            base.fijo_contable_comparable,
            base.fijo_contable_operacional,
            base.resultado_operacional_antes_depreciacion,
            base.resultado_operacional_despues_depreciacion,
            base.resultado_total,
            base.registros_sin_linea_eerr,
            base.monto_sin_linea_eerr,
            base.hay_ventas,
            base.hay_costos,
            base.hay_contabilidad,
            base.margen_modelado_subtotal,
            (base.ingreso_explotacion - base.ingreso_estimado_conocido) AS diferencia_ingresos,
            (base.fijo_modelado_conocido - base.fijo_contable_operacional) AS diferencia_fijos,
            (((base.costo_modelado_conocido - base.fijo_modelado_conocido) - base.reposicion_modelada_conocida) - ((base.costo_explotacion + base.administracion_ventas) - base.fijo_contable_operacional)) AS diferencia_otros_costos,
            (base.margen_modelado_subtotal + base.reposicion_modelada_conocida) AS margen_sin_reposicion_subtotal,
            (base.hay_ventas AND base.hay_costos AND base.hay_contabilidad) AS periodo_con_fuentes_comunes,
            (base.hay_ventas AND base.hay_costos AND (base.ingresos_faltantes = 0) AND (base.componentes_no_validos = 0)) AS componentes_modelados_completos
           FROM base
        )
 SELECT mes_id,
    sk_escenario,
    viajes_reales,
    ingresos_faltantes,
    ingreso_estimado_conocido,
    facturado_por_fecha_viaje,
    ultima_fecha_viaje,
    registros_componentes,
    montos_faltantes,
    componentes_no_validos,
    componentes_virtuales,
    costo_modelado_conocido,
    fijo_modelado_conocido,
    reposicion_modelada_conocida,
    ingreso_explotacion,
    ingreso_total,
    otros_ingresos,
    gasto_total,
    costo_explotacion,
    administracion_ventas,
    depreciacion_registrada,
    registros_depreciacion,
    fijo_contable_comparable,
    fijo_contable_operacional,
    resultado_operacional_antes_depreciacion,
    resultado_operacional_despues_depreciacion,
    resultado_total,
    registros_sin_linea_eerr,
    monto_sin_linea_eerr,
    hay_ventas,
    hay_costos,
    hay_contabilidad,
    margen_modelado_subtotal,
    diferencia_ingresos,
    diferencia_fijos,
    diferencia_otros_costos,
    margen_sin_reposicion_subtotal,
    periodo_con_fuentes_comunes,
    componentes_modelados_completos,
    ((((margen_modelado_subtotal + diferencia_ingresos) + diferencia_fijos) + reposicion_modelada_conocida) + diferencia_otros_costos) AS resultado_conciliado_antes_depreciacion,
    (resultado_operacional_antes_depreciacion - ((((margen_modelado_subtotal + diferencia_ingresos) + diferencia_fijos) + reposicion_modelada_conocida) + diferencia_otros_costos)) AS residuo_aritmetico,
        CASE
            WHEN periodo_con_fuentes_comunes THEN
            CASE
                WHEN componentes_modelados_completos THEN 'Puente agregado; componentes presentes'::text
                ELSE 'Puente agregado de subtotales; hay faltantes o estados no validos'::text
            END
            ELSE 'Sin todas las fuentes del periodo; no conciliable'::text
        END AS estado_conciliacion,
    (mes_id = max(mes_id) OVER ()) AS ultimo_mes_observado,
    'Toda la empresa por fecha de movimiento; sin filtro de ciclos; E3; conciliacion de agregados, no documental ni causal; no altera reposicion ni margenes fuente; ultimo mes puede estar incompleto'::text AS alcance
   FROM pasos;


--
-- Name: v_fijos_escenario_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_fijos_escenario_mensual AS
 WITH f AS (
         SELECT (fact_costo_viaje.sk_fecha / 100) AS mes_id,
            fact_costo_viaje.sk_escenario,
            fact_costo_viaje.monto,
            fact_costo_viaje.estado,
            false AS virtual
           FROM escenarios.fact_costo_viaje
          WHERE (fact_costo_viaje.sk_componente = 4)
        UNION ALL
         SELECT (fact_costo_virtual.sk_fecha / 100),
            fact_costo_virtual.sk_escenario,
            fact_costo_virtual.monto,
            fact_costo_virtual.estado,
            true
           FROM escenarios.fact_costo_virtual
          WHERE (fact_costo_virtual.sk_componente = 4)
        ), m AS (
         SELECT f.mes_id,
            f.sk_escenario,
            count(*) AS registros,
            count(*) FILTER (WHERE (f.monto IS NULL)) AS montos_faltantes,
            count(*) FILTER (WHERE ((f.estado IS DISTINCT FROM 'ok'::text) OR (f.monto IS NULL))) AS componentes_no_validos,
            sum((f.monto)::numeric) AS fijo_conocido,
            sum((f.monto)::numeric) FILTER (WHERE (NOT f.virtual)) AS fijo_real_conocido,
            sum((f.monto)::numeric) FILTER (WHERE f.virtual) AS fijo_virtual_conocido
           FROM f
          WHERE (f.sk_escenario = ANY (ARRAY[1, 2, 3]))
          GROUP BY f.mes_id, f.sk_escenario
        )
 SELECT m.mes_id,
    m.sk_escenario,
    m.registros,
    m.montos_faltantes,
    m.componentes_no_validos,
    m.fijo_conocido,
    m.fijo_real_conocido,
    m.fijo_virtual_conocido,
    c.fijo_comparable AS fijo_contable_comparable,
    (c.registros_contables IS NOT NULL) AS hay_contabilidad,
    'Subtotales conocidos; 1/2 cotizaciones con otra cobertura y retornos sin costo; 3 reales y virtuales; totales entre escenarios no implican poblacion equivalente'::text AS alcance
   FROM (m
     LEFT JOIN ds_gestion.v_referencia_contable_mensual c USING (mes_id));


--
-- Name: v_fijo_no_absorbido_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_fijo_no_absorbido_mensual AS
 WITH presupuesto AS (
         SELECT v_capacidad_periodica.anio,
            (replace(v_capacidad_periodica.mes, '-'::text, ''::text))::integer AS mes_id,
            min(v_capacidad_periodica.desde) AS desde,
            max(v_capacidad_periodica.hasta) AS hasta,
            bool_or(v_capacidad_periodica.parcial) AS parcial,
            (bool_and(v_capacidad_periodica.presupuesto_completo) AND (count(*) = 4)) AS presupuesto_completo,
                CASE
                    WHEN (bool_and(v_capacidad_periodica.presupuesto_completo) AND (count(*) = 4)) THEN sum((v_capacidad_periodica.presupuesto)::numeric)
                    ELSE NULL::numeric
                END AS presupuesto,
            sum((v_capacidad_periodica.absorcion_conocida)::numeric) AS asignado_control_bolsas,
            max(v_capacidad_periodica.viajes_pendientes) AS viajes_indirectos_pendientes,
            max(v_capacidad_periodica.virtuales_pendientes) AS virtuales_pendientes
           FROM ds_gestion.v_capacidad_periodica
          GROUP BY v_capacidad_periodica.anio, (replace(v_capacidad_periodica.mes, '-'::text, ''::text))::integer
        )
 SELECT c.mes_id,
    (c.mes_id / 100) AS anio,
    to_char((to_date((c.mes_id)::text, 'YYYYMM'::text))::timestamp with time zone, 'YYYY-MM'::text) AS mes,
    p.desde,
    p.hasta,
    p.parcial,
    p.presupuesto_completo,
    p.presupuesto,
    c.fijo_modelado_conocido AS asignado_conocido,
    f.fijo_real_conocido AS asignado_real_conocido,
    f.fijo_virtual_conocido AS asignado_virtual_conocido,
    (p.presupuesto - c.fijo_modelado_conocido) AS saldo_presupuesto_no_asignado,
    c.fijo_contable_comparable AS gasto_fijo_contable,
    (c.fijo_contable_comparable - c.fijo_modelado_conocido) AS diferencia_contable_no_asignada,
    (c.fijo_modelado_conocido / NULLIF(p.presupuesto, (0)::numeric)) AS absorcion_presupuesto_pct,
    (c.fijo_modelado_conocido / NULLIF(c.fijo_contable_comparable, (0)::numeric)) AS asignado_sobre_gasto_pct,
    p.asignado_control_bolsas,
    f.montos_faltantes AS fijos_faltantes,
    f.componentes_no_validos AS fijos_no_validos,
    p.viajes_indirectos_pendientes,
    p.virtuales_pendientes,
    c.hay_ventas,
    c.hay_costos,
    c.hay_contabilidad,
    c.ultimo_mes_observado,
    'Escenario 3; toda la empresa por mes de movimiento, sin filtro de ciclos/clientes. Presupuesto prorrateado por dias calendario con vigencias. Saldos con signo y tras asignacion conocida: no son ahorro evitable ni indisponibilidad fisica; faltantes pueden explicar parte del saldo. Fijo contable incluye financieros de la regla vigente y excluye comision ya modelada aparte.'::text AS alcance
   FROM ((ds_gestion.v_conciliacion_operacional_mensual c
     LEFT JOIN presupuesto p USING (mes_id))
     LEFT JOIN ds_gestion.v_fijos_escenario_mensual f ON (((f.mes_id = c.mes_id) AND (f.sk_escenario = 3))))
  WHERE (c.sk_escenario = 3);


--
-- Name: v_capacidad_flota_modelada_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_capacidad_flota_modelada_mensual AS
 WITH m AS (
         SELECT v_fijo_no_absorbido_mensual.mes_id,
            v_fijo_no_absorbido_mensual.mes,
            v_fijo_no_absorbido_mensual.anio,
            v_fijo_no_absorbido_mensual.desde,
            v_fijo_no_absorbido_mensual.hasta,
            v_fijo_no_absorbido_mensual.parcial
           FROM ds_gestion.v_fijo_no_absorbido_mensual
          WHERE (v_fijo_no_absorbido_mensual.anio >= 2025)
        ), practica AS (
         SELECT m_1.mes_id,
            count(*) AS dias_calendario,
            bool_and(((p_1.n = 1) AND (p_1.capacidad IS NOT NULL))) AS capacidad_completa,
            (
                CASE
                    WHEN bool_and(((p_1.n = 1) AND (p_1.capacidad IS NOT NULL))) THEN sum((p_1.capacidad / EXTRACT(doy FROM (make_date((m_1.anio + 1), 1, 1) - 1))))
                    ELSE NULL::numeric
                END)::double precision AS dias_capacidad
           FROM ((m m_1
             CROSS JOIN LATERAL generate_series((m_1.desde)::timestamp with time zone, (m_1.hasta)::timestamp with time zone, '1 day'::interval) dia(dia))
             CROSS JOIN LATERAL ( SELECT count(*) AS n,
                    max(parametro_costo.cantidad_inductor) AS capacidad
                   FROM costos.parametro_costo
                  WHERE ((parametro_costo.metodo = 'propuesto'::text) AND (parametro_costo.parametro = 'cuota_diaria'::text) AND (parametro_costo.ambito = 'flota'::text) AND ((dia.dia)::date >= parametro_costo.vigente_desde) AND ((dia.dia)::date < parametro_costo.vigente_hasta))) p_1)
          GROUP BY m_1.mes_id
        ), reales AS (
         SELECT (fact_viaje_costo_indirecto.sk_fecha_inicio / 100) AS mes_id,
            sum(fact_viaje_costo_indirecto.dias_viaje) AS dias_reales,
            count(*) AS viajes,
            count(*) FILTER (WHERE (fact_viaje_costo_indirecto.dias_viaje IS NULL)) AS dias_reales_faltantes
           FROM costos.fact_viaje_costo_indirecto
          WHERE ((fact_viaje_costo_indirecto.metodo = 'propuesto'::text) AND (fact_viaje_costo_indirecto.es_flota_propia = 'S'::text))
          GROUP BY (fact_viaje_costo_indirecto.sk_fecha_inicio / 100)
        ), virtuales AS (
         SELECT (fact_costo_virtual.sk_fecha / 100) AS mes_id,
            sum(fact_costo_virtual.cantidad) FILTER (WHERE (fact_costo_virtual.estado = 'ok'::text)) AS dias_virtuales,
            count(*) AS virtuales,
            count(*) FILTER (WHERE ((fact_costo_virtual.estado IS DISTINCT FROM 'ok'::text) OR (fact_costo_virtual.cantidad IS NULL))) AS dias_virtuales_faltantes
           FROM escenarios.fact_costo_virtual
          WHERE ((fact_costo_virtual.sk_escenario = 3) AND (fact_costo_virtual.sk_componente = 4))
          GROUP BY (fact_costo_virtual.sk_fecha / 100)
        )
 SELECT m.mes_id,
    m.mes,
    m.anio,
    (m.desde)::text AS desde,
    (m.hasta)::text AS hasta,
    m.parcial,
    p.dias_calendario,
    p.capacidad_completa,
    p.dias_capacidad,
    COALESCE(r.dias_reales, (0)::double precision) AS dias_reales_conocidos,
    COALESCE(v.dias_virtuales, (0)::double precision) AS dias_virtuales_conocidos,
    (COALESCE(r.dias_reales, (0)::double precision) + COALESCE(v.dias_virtuales, (0)::double precision)) AS dias_actividad_conocida,
    ((p.dias_capacidad - COALESCE(r.dias_reales, (0)::double precision)) - COALESCE(v.dias_virtuales, (0)::double precision)) AS brecha_modelada,
    COALESCE(r.viajes, (0)::bigint) AS viajes,
    COALESCE(v.virtuales, (0)::bigint) AS virtuales,
    (COALESCE(r.dias_reales_faltantes, (0)::bigint) + COALESCE(v.dias_virtuales_faltantes, (0)::bigint)) AS referencias_dias_faltantes,
    'Solo flota propia. Capacidad anual del parametro vigente prorrateada por calendario; actividad conocida en dias-viaje reales y virtuales E3. No son dias observados de trabajo, taller, descanso o ahorro evitable.'::text AS alcance
   FROM (((m
     LEFT JOIN practica p USING (mes_id))
     LEFT JOIN reales r USING (mes_id))
     LEFT JOIN virtuales v USING (mes_id));


--
-- Name: v_movimiento; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_movimiento AS
 WITH m AS (
         SELECT ('R:'::text || (v.nro_viaje)::text) AS movimiento_id,
            v.nro_viaje,
            false AS es_virtual,
            cy.id_viaje AS id_cadena,
            v.sk_fecha AS fecha_documental_id,
            'documental_erp'::text AS origen_fecha,
            v.equipo_id,
            v.rampla_id,
            v.conductor_id,
            v.cliente_id,
            v.producto_id,
            v.sit_stretch_id AS tramo_id,
            v.tipo_operador,
            cy.ciclo_comercial AS ciclo_id,
            cy.orden_ciclo_comercial,
            cy.rol_comercial,
            cy.clasificacion_viaje,
            cy.calidad_trayectoria,
            cy.km,
                CASE
                    WHEN (cy.id_viaje IS NULL) THEN 'sin_cadena'::text
                    ELSE 'en_cadena'::text
                END AS estado_cadena
           FROM (ventas.fact_viaje_venta v
             LEFT JOIN ciclos.fact_viaje_ciclo cy ON (((cy.nro_viaje = v.nro_viaje) AND (cy.es_virtual = 'N'::text))))
        UNION ALL
         SELECT ('V:'::text || cy.id_viaje),
            NULL::bigint AS int8,
            true,
            cy.id_viaje,
            cy.sk_fecha,
            'heredada_reconstruccion'::text,
            cy.equipo_id,
            NULL::bigint AS int8,
            NULL::bigint AS int8,
            cy.cliente_id,
            cy.producto_id,
            cy.sit_stretch_id,
            NULL::text AS text,
            cy.ciclo_comercial,
            cy.orden_ciclo_comercial,
            cy.rol_comercial,
            cy.clasificacion_viaje,
            cy.calidad_trayectoria,
            cy.km,
            'virtual_reconstruido'::text
           FROM ciclos.fact_viaje_ciclo cy
          WHERE (cy.es_virtual = 'S'::text)
        )
 SELECT m.movimiento_id,
    m.nro_viaje,
    m.es_virtual,
    m.id_cadena,
    m.fecha_documental_id,
    m.origen_fecha,
    m.equipo_id,
    m.rampla_id,
    m.conductor_id,
    m.cliente_id,
    m.producto_id,
    m.tramo_id,
    m.tipo_operador,
    m.ciclo_id,
    m.orden_ciclo_comercial,
    m.rol_comercial,
    m.clasificacion_viaje,
    m.calidad_trayectoria,
    m.km,
    m.estado_cadena,
    c.nombre_completo AS conductor_nombre,
        CASE
            WHEN m.es_virtual THEN 'no_observado_virtual'::text
            WHEN (m.conductor_id IS NULL) THEN 'sin_id'::text
            WHEN (c.conductor_id IS NULL) THEN 'sin_catalogo'::text
            ELSE 'encontrado'::text
        END AS estado_conductor,
    e.nombre_fuente AS equipo_nombre,
    r.nombre_fuente AS rampla_nombre,
    s.razon_social AS cliente_nombre,
    p.producto AS producto_nombre,
    t.nombre_fuente AS tramo_nombre_actual,
    z.ciudad_origen_limpia AS origen,
    z.ciudad_destino_limpia AS destino,
    z.base_origen,
    z.base_destino,
    z.tipo_virtual,
    z.tipo_carga_final AS carga_descriptiva,
    q.tipo_carga AS carga_termica_costeo,
        CASE
            WHEN (q.tipo_carga IS NOT NULL) THEN 'escenarios.fact_cotizacion_viaje.tipo_carga'::text
            ELSE NULL::text
        END AS origen_carga_termica,
    COALESCE(tb.tramo, ((z.ciudad_origen_limpia || ' / '::text) || z.ciudad_destino_limpia)) AS tramo_nombre_costeo,
    COALESCE(m.km, tb.km) AS km_analisis,
    e.codigo AS equipo_codigo,
        CASE
            WHEN m.es_virtual THEN 'S'::text
            WHEN (p.viaje_vacio_fuente IS NULL) THEN NULL::text
            WHEN (upper(TRIM(BOTH FROM p.viaje_vacio_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
            ELSE 'N'::text
        END AS vacio_fisico,
    tb.tramo AS tramo_nombre_base
   FROM (((((((((m
     LEFT JOIN dim_comun.dim_conductor c ON ((c.conductor_id = m.conductor_id)))
     LEFT JOIN dim_comun.dim_equipo e ON ((e.equipo_id = m.equipo_id)))
     LEFT JOIN dim_comun.dim_equipo r ON ((r.equipo_id = m.rampla_id)))
     LEFT JOIN dim_comun.dim_socio_negocio s ON ((s.socio_id = m.cliente_id)))
     LEFT JOIN dim_comun.dim_producto_viaje p ON ((p.producto_id = m.producto_id)))
     LEFT JOIN dim_comun.dim_tramo_comercial t ON ((t.tramo_id = m.tramo_id)))
     LEFT JOIN ciclos.fact_viaje_ciclo z ON ((z.id_viaje = m.id_cadena)))
     LEFT JOIN escenarios.fact_cotizacion_viaje q ON ((q.nro_viaje = m.nro_viaje)))
     LEFT JOIN ciclos.dim_tramo_base tb ON ((tb.sit_stretch_id = m.tramo_id)));


--
-- Name: v_ciclo; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_ciclo AS
 WITH a AS (
         SELECT v_movimiento.ciclo_id,
            (((array_agg(v_movimiento.origen ORDER BY v_movimiento.orden_ciclo_comercial))[1] || ' → '::text) || string_agg(v_movimiento.destino, ' → '::text ORDER BY v_movimiento.orden_ciclo_comercial)) AS recorrido_completo,
            md5((json_agg(json_build_array(v_movimiento.origen, v_movimiento.destino, v_movimiento.tramo_id, v_movimiento.km,
                CASE
                    WHEN v_movimiento.es_virtual THEN 'S'::text
                    ELSE 'N'::text
                END) ORDER BY v_movimiento.orden_ciclo_comercial))::text) AS patron_recorrido,
            count(*) FILTER (WHERE (NOT v_movimiento.es_virtual)) AS reales,
            count(*) FILTER (WHERE v_movimiento.es_virtual) AS virtuales,
            count(*) FILTER (WHERE ((NOT v_movimiento.es_virtual) AND (v_movimiento.clasificacion_viaje = 'Retorno'::text))) AS retornos_reales,
            count(*) FILTER (WHERE ((NOT v_movimiento.es_virtual) AND (v_movimiento.clasificacion_viaje = 'Retorno'::text) AND (v_movimiento.tipo_operador = 'propio'::text))) AS retornos_propios,
            count(*) FILTER (WHERE ((NOT v_movimiento.es_virtual) AND (v_movimiento.clasificacion_viaje = 'Retorno'::text) AND (v_movimiento.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])))) AS retornos_terceros,
            count(*) FILTER (WHERE ((NOT v_movimiento.es_virtual) AND (v_movimiento.rol_comercial = ANY (ARRAY['envío adicional en ruta'::text, 'envío adicional local'::text])))) AS envios_adicionales
           FROM ds_gestion.v_movimiento
          WHERE (v_movimiento.ciclo_id IS NOT NULL)
          GROUP BY v_movimiento.ciclo_id
        )
 SELECT d.ciclo_comercial AS ciclo_id,
    p.movimiento_id AS movimiento_principal_id,
    p.nro_viaje AS nro_viaje_principal,
    p.fecha_documental_id AS fecha_documental_principal_id,
    p.cliente_id AS cliente_principal_id,
    p.cliente_nombre AS cliente_principal,
    p.producto_id AS producto_principal_id,
    p.producto_nombre AS producto_principal,
    p.tramo_id AS tramo_principal_id,
    p.tramo_nombre_costeo AS tramo_principal,
    p.origen AS origen_principal,
    p.destino AS destino_principal,
    p.equipo_id,
    p.equipo_codigo,
    p.equipo_nombre,
    p.conductor_nombre,
    p.tipo_operador AS operador_principal,
    COALESCE((p.tipo_operador = 'propio'::text), false) AS en_alcance_rentabilidad,
    d.fecha_inicio AS inicio_documental,
    d.fecha_fin AS fin_documental,
    d.motivo_apertura,
    d.motivo_cierre,
        CASE
            WHEN ((d.motivo_apertura = 'local en Santiago'::text) AND (d.motivo_cierre = 'local'::text)) THEN 'local Santiago'::text
            WHEN (('corte en Coquimbo'::text = d.motivo_apertura) OR ('corte en Coquimbo'::text = d.motivo_cierre)) THEN 'con corte Coquimbo'::text
            WHEN ((d.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text])) AND (d.motivo_cierre = ANY (ARRAY['llega a Santiago'::text, 'corte en Coquimbo'::text]))) THEN 'Santiago-Santiago'::text
            ELSE 'abierto o inicio incompleto'::text
        END AS tipo_segmento,
    a.recorrido_completo,
    a.patron_recorrido,
    COALESCE((d.retornos > 0), false) AS contiene_retorno,
    a.reales,
    a.virtuales,
    a.retornos_reales,
    a.retornos_propios,
    a.retornos_terceros,
    a.envios_adicionales
   FROM ((ciclos.dim_ciclo_comercial d
     LEFT JOIN ds_gestion.v_movimiento p ON ((p.id_cadena = d.viaje_principal)))
     LEFT JOIN a ON ((a.ciclo_id = d.ciclo_comercial)));


--
-- Name: v_fletero_comision; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_fletero_comision AS
 SELECT v.nro_viaje,
    v.sk_fecha AS fecha_documental_id,
    s.escenario_id,
    v.ingreso AS ingreso_estimado,
    v.pago_tercero_observado AS comision,
    v.pct_pago_tercero AS porcentaje_comision_origen,
    ((v.ingreso IS NOT NULL) AND (v.pago_tercero_observado IS NOT NULL)) AS evaluable,
        CASE
            WHEN ((v.ingreso IS NOT NULL) AND (v.pago_tercero_observado IS NOT NULL)) THEN v.pago_tercero_observado
            ELSE NULL::double precision
        END AS costo,
        CASE
            WHEN ((v.ingreso IS NOT NULL) AND (v.pago_tercero_observado IS NOT NULL)) THEN (v.ingreso - v.pago_tercero_observado)
            ELSE NULL::double precision
        END AS margen,
        CASE
            WHEN (v.ingreso IS NULL) THEN 'Ingreso estimado faltante'::text
            WHEN (v.pago_tercero_observado IS NULL) THEN
            CASE
                WHEN (v.tipo_operador = 'caterina'::text) THEN 'Comisión de Caterina faltante'::text
                ELSE 'Comisión del fletero faltante'::text
            END
            WHEN (v.tipo_operador = 'caterina'::text) THEN 'Evaluable Caterina; solo comisión registrada, sin costos fijos'::text
            ELSE 'Evaluable fletero; solo comisión registrada, sin costos fijos'::text
        END AS estado
   FROM (ventas.fact_viaje_venta v
     CROSS JOIN ( VALUES (1), (2)) s(escenario_id))
  WHERE (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text]));


--
-- Name: fact_cotizacion_servicio; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_cotizacion_servicio (
    nro_viaje bigint NOT NULL,
    fecha_documental_id bigint,
    cliente_id bigint,
    equipo_id bigint,
    tramo_id bigint,
    es_tractoreo text,
    ingreso_estimado double precision,
    clasificacion text,
    escenario_id bigint NOT NULL,
    tipo_carga text,
    version text,
    fuente_sha256 text,
    estado_km text,
    supuesto_rm boolean,
    texto_tramo text,
    n_puntos bigint,
    c9 text,
    c10 text,
    c11 text,
    c12 text,
    c13 text,
    b4 text,
    b5 text,
    b6 text,
    b7 text,
    n5 text,
    rendimiento double precision,
    i4 double precision,
    costo_presentacion double precision,
    evaluable boolean,
    estado text,
    e10 double precision,
    e11 double precision,
    e12 double precision,
    e13 double precision,
    e14 double precision,
    g4 text,
    n4 text,
    g5 text,
    e7 text,
    c21 text,
    e8 text,
    e5 double precision,
    e6 double precision,
    g7 double precision,
    e22 double precision,
    d22 double precision,
    c22 double precision,
    f22 double precision,
    e15 double precision,
    f15 double precision,
    g6 double precision,
    g8 double precision,
    d27 double precision,
    i5 double precision,
    k5 double precision,
    i6 double precision,
    i7 double precision,
    k15 double precision,
    j8 double precision,
    i8 double precision,
    i9 double precision,
    i10 double precision,
    i11 double precision,
    i12 double precision,
    i13 double precision,
    i14 double precision,
    i15 double precision,
    i16 double precision,
    factor_retorno double precision,
    subtotal double precision,
    i17 double precision,
    i18 double precision,
    i19 double precision,
    i20 double precision,
    i21 double precision,
    i22 double precision,
    k10 double precision,
    k11 double precision,
    k12 double precision,
    j8_estado text,
    margen_tarificador double precision,
    razon_ingreso_cotizacion double precision
);


--
-- Name: v_costeo_servicio_1_2; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_costeo_servicio_1_2 AS
 SELECT q.nro_viaje,
    q.escenario_id,
    q.evaluable,
    q.costo_presentacion,
    q.margen_tarificador,
    q.estado,
    6 AS componentes_esperados
   FROM tarificador.fact_cotizacion_servicio q
UNION ALL
 SELECT f.nro_viaje,
    f.escenario_id,
    f.evaluable,
    f.costo AS costo_presentacion,
    f.margen AS margen_tarificador,
    f.estado,
    1 AS componentes_esperados
   FROM ds_gestion.v_fletero_comision f;


--
-- Name: v_politica_cobertura; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_politica_cobertura AS
 SELECT 'flexible_base'::text AS politica_id,
    'Flexible por base'::text AS nombre,
    1 AS version_regla;


--
-- Name: dim_escenario; Type: TABLE; Schema: escenarios; Owner: -
--

CREATE TABLE escenarios.dim_escenario (
    sk_escenario integer NOT NULL,
    nombre text,
    descripcion text
);


--
-- Name: v_resultado_servicio; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_resultado_servicio AS
 WITH anterior AS (
         WITH v AS MATERIALIZED (
                 SELECT vv.nro_viaje,
                    vv.sk_fecha AS fecha_documental_id,
                    vv.ingreso,
                    vv.tipo_operador,
                    cy.ciclo_comercial,
                    cy.rol_comercial,
                    COALESCE((vp.tipo_operador = 'propio'::text), false) AS principal_propio
                   FROM ((((ventas.fact_viaje_venta vv
                     LEFT JOIN ciclos.fact_viaje_ciclo cy ON (((cy.nro_viaje = vv.nro_viaje) AND (cy.es_virtual = 'N'::text))))
                     LEFT JOIN ciclos.dim_ciclo_comercial dc ON ((dc.ciclo_comercial = cy.ciclo_comercial)))
                     LEFT JOIN ciclos.fact_viaje_ciclo pr ON ((pr.id_viaje = dc.viaje_principal)))
                     LEFT JOIN ventas.fact_viaje_venta vp ON ((vp.nro_viaje = pr.nro_viaje)))
                ), c AS (
                 SELECT f.nro_viaje,
                    f.sk_escenario,
                    (count(*))::integer AS filas,
                    sum(f.monto) AS subtotal,
                    sum(f.monto_comercial) FILTER (WHERE (f.estado_comercial = 'ok'::text)) AS subtotal_comercial,
                    bool_and(((f.estado = 'ok'::text) AND (f.monto IS NOT NULL))) AS datos_ok,
                    bool_and(((f.estado_comercial = 'ok'::text) AND (f.monto_comercial IS NOT NULL))) AS comercial_ok,
                    bool_and(
                        CASE
                            WHEN ((v.tipo_operador = 'propio'::text) AND (f.sk_escenario = ANY (ARRAY[1, 2]))) THEN (f.sk_componente = ANY (ARRAY[1, 2, 3, 4, 5, 6]))
                            WHEN (v.tipo_operador = 'propio'::text) THEN (f.sk_componente = ANY (ARRAY[1, 2, 3, 4, 5, 6, 10]))
                            WHEN (f.sk_escenario = ANY (ARRAY[1, 2])) THEN (f.sk_componente = 9)
                            ELSE (f.sk_componente = ANY (ARRAY[4, 5, 6, 9]))
                        END) AS componentes_ok
                   FROM (escenarios.fact_costo_viaje f
                     JOIN v USING (nro_viaje))
                  GROUP BY f.nro_viaje, f.sk_escenario
                ), nv AS (
                 SELECT cy.ciclo_comercial,
                    (count(*))::integer AS n
                   FROM ciclos.fact_viaje_ciclo cy
                  WHERE (cy.es_virtual = 'S'::text)
                  GROUP BY cy.ciclo_comercial
                ), cv AS (
                 SELECT f.nro_viaje_destino AS nro_viaje,
                    f.sk_escenario,
                    (count(DISTINCT f.id_viaje))::integer AS n,
                    (count(*))::integer AS filas,
                    sum(f.monto) FILTER (WHERE (f.estado = 'ok'::text)) AS conocido,
                    bool_and(((f.estado = 'ok'::text) AND (f.monto IS NOT NULL) AND (f.sk_componente = ANY (ARRAY[1, 2, 3, 4, 5, 6])))) AS ok
                   FROM (escenarios.fact_costo_virtual f
                     JOIN v ON ((v.nro_viaje = f.nro_viaje_destino)))
                  GROUP BY f.nro_viaje_destino, f.sk_escenario
                ), x AS (
                 SELECT v.nro_viaje,
                    v.fecha_documental_id,
                    v.ingreso,
                    v.tipo_operador,
                    v.ciclo_comercial,
                    v.rol_comercial,
                    v.principal_propio,
                    s.sk_escenario,
                    c.subtotal,
                    c.subtotal_comercial,
                    c.filas,
                        CASE
                            WHEN ((v.tipo_operador = 'propio'::text) AND (s.sk_escenario = ANY (ARRAY[1, 2]))) THEN 6
                            WHEN (v.tipo_operador = 'propio'::text) THEN 7
                            WHEN (s.sk_escenario = ANY (ARRAY[1, 2])) THEN 1
                            ELSE 4
                        END AS esperadas,
                    COALESCE((c.datos_ok AND c.componentes_ok), false) AS datos_ok,
                    COALESCE(c.comercial_ok, false) AS comercial_ok,
                    COALESCE(nv.n, 0) AS n_virtuales,
                    COALESCE(cv.n, 0) AS n_virtuales_destino,
                    COALESCE((cv.ok AND (cv.filas = (6 * cv.n))), true) AS virtuales_ok,
                    cv.conocido AS costo_virtual_conocido
                   FROM ((((v
                     CROSS JOIN escenarios.dim_escenario s)
                     LEFT JOIN c ON (((c.nro_viaje = v.nro_viaje) AND (c.sk_escenario = s.sk_escenario))))
                     LEFT JOIN nv ON ((nv.ciclo_comercial = v.ciclo_comercial)))
                     LEFT JOIN cv ON (((cv.nro_viaje = v.nro_viaje) AND (cv.sk_escenario = s.sk_escenario))))
                  WHERE (s.sk_escenario = ANY (ARRAY[1, 2, 3]))
                ), z AS (
                 SELECT x.nro_viaje,
                    x.fecha_documental_id,
                    x.ingreso,
                    x.tipo_operador,
                    x.ciclo_comercial,
                    x.rol_comercial,
                    x.principal_propio,
                    x.sk_escenario,
                    x.subtotal,
                    x.subtotal_comercial,
                    x.filas,
                    x.esperadas,
                    x.datos_ok,
                    x.comercial_ok,
                    x.n_virtuales,
                    x.n_virtuales_destino,
                    x.virtuales_ok,
                    x.costo_virtual_conocido,
                    ((x.ingreso IS NOT NULL) AND (x.tipo_operador = ANY (ARRAY['propio'::text, 'caterina'::text, 'fletero'::text])) AND x.datos_ok AND (x.filas = x.esperadas)) AS evaluable,
                    ((x.tipo_operador = ANY (ARRAY['caterina'::text, 'fletero'::text])) OR ((x.ciclo_comercial IS NOT NULL) AND x.principal_propio AND (x.rol_comercial = ANY (ARRAY['envío principal'::text, 'retorno'::text, 'envío adicional en ruta'::text, 'envío adicional local'::text, 'vacío'::text])))) AS contexto_comercial,
                        CASE
                            WHEN ((x.subtotal_comercial IS NOT NULL) OR (x.costo_virtual_conocido IS NOT NULL)) THEN (COALESCE(x.subtotal_comercial, (0)::double precision) + COALESCE(x.costo_virtual_conocido, (0)::double precision))
                            ELSE NULL::double precision
                        END AS conocido_comercial,
                    (NOT ((x.tipo_operador = 'propio'::text) AND (x.rol_comercial = 'envío principal'::text) AND (x.n_virtuales > 0) AND (x.sk_escenario = ANY (ARRAY[1, 2])))) AS metodo_virtual,
                    (NOT ((x.tipo_operador = 'propio'::text) AND (x.rol_comercial = 'envío principal'::text) AND (x.sk_escenario = 3) AND (x.n_virtuales <> x.n_virtuales_destino))) AS asignacion_virtual
                   FROM x
                ), q AS (
                 SELECT z.nro_viaje,
                    z.fecha_documental_id,
                    z.ingreso,
                    z.tipo_operador,
                    z.ciclo_comercial,
                    z.rol_comercial,
                    z.principal_propio,
                    z.sk_escenario,
                    z.subtotal,
                    z.subtotal_comercial,
                    z.filas,
                    z.esperadas,
                    z.datos_ok,
                    z.comercial_ok,
                    z.n_virtuales,
                    z.n_virtuales_destino,
                    z.virtuales_ok,
                    z.costo_virtual_conocido,
                    z.evaluable,
                    z.contexto_comercial,
                    z.conocido_comercial,
                    z.metodo_virtual,
                    z.asignacion_virtual,
                    COALESCE((z.evaluable AND z.contexto_comercial AND z.comercial_ok AND z.metodo_virtual AND z.asignacion_virtual AND z.virtuales_ok), false) AS evaluable_comercial
                   FROM z
                )
         SELECT q.nro_viaje,
            q.fecha_documental_id,
            q.sk_escenario AS escenario_id,
            q.ingreso AS ingreso_estimado,
                CASE
                    WHEN q.evaluable THEN q.subtotal
                    ELSE NULL::double precision
                END AS costo_fisico,
                CASE
                    WHEN q.evaluable THEN (q.ingreso - q.subtotal)
                    ELSE NULL::double precision
                END AS margen_fisico,
            COALESCE(q.evaluable, false) AS evaluable_fisico,
            q.subtotal AS subtotal_conocido_fisico,
            COALESCE(q.filas, 0) AS componentes_presentes,
            q.esperadas AS componentes_esperados,
                CASE
                    WHEN (q.ingreso IS NULL) THEN 'Ingreso estimado faltante'::text
                    WHEN ((q.tipo_operador <> ALL (ARRAY['propio'::text, 'caterina'::text, 'fletero'::text])) OR (q.tipo_operador IS NULL)) THEN 'Operador fuera del alcance definido'::text
                    WHEN (q.filas IS NULL) THEN 'Sin componentes de costo'::text
                    WHEN (q.filas <> q.esperadas) THEN 'Conjunto de componentes incompleto'::text
                    WHEN (NOT q.datos_ok) THEN 'Costo físico incompleto'::text
                    ELSE 'Evaluable físico'::text
                END AS motivo_fisico,
                CASE
                    WHEN q.evaluable_comercial THEN q.conocido_comercial
                    ELSE NULL::double precision
                END AS costo_comercial,
                CASE
                    WHEN q.evaluable_comercial THEN (q.ingreso - q.conocido_comercial)
                    ELSE NULL::double precision
                END AS margen_comercial,
            q.evaluable_comercial,
            q.conocido_comercial AS subtotal_conocido_comercial,
                CASE
                    WHEN (NOT COALESCE(q.evaluable, false)) THEN 'Costo físico o ingreso incompleto'::text
                    WHEN (NOT COALESCE(q.contexto_comercial, false)) THEN 'Sin contexto propio para reparto comercial'::text
                    WHEN (NOT COALESCE(q.metodo_virtual, false)) THEN 'Principal con virtual sin método en 1/2'::text
                    WHEN (NOT COALESCE(q.asignacion_virtual, false)) THEN 'Virtual pendiente de asignación'::text
                    WHEN (NOT q.virtuales_ok) THEN 'Costo virtual incompleto'::text
                    WHEN (NOT q.comercial_ok) THEN 'Reparto comercial incompleto'::text
                    ELSE 'Evaluable comercial'::text
                END AS motivo_comercial,
            q.ciclo_comercial AS ciclo_id,
            q.rol_comercial,
            q.principal_propio,
            q.contexto_comercial,
            q.datos_ok,
            q.comercial_ok,
            q.metodo_virtual,
            q.asignacion_virtual,
            q.virtuales_ok,
            q.n_virtuales AS virtuales_ciclo,
            q.n_virtuales_destino AS virtuales_asignados
           FROM q
        )
 SELECT anterior.nro_viaje,
    anterior.fecha_documental_id,
    anterior.escenario_id,
    anterior.ingreso_estimado,
    anterior.costo_fisico,
    anterior.margen_fisico,
    anterior.evaluable_fisico,
    anterior.subtotal_conocido_fisico,
    anterior.componentes_presentes,
    anterior.componentes_esperados,
    anterior.motivo_fisico,
    anterior.costo_comercial,
    anterior.margen_comercial,
    anterior.evaluable_comercial,
    anterior.subtotal_conocido_comercial,
    anterior.motivo_comercial,
    anterior.ciclo_id,
    anterior.rol_comercial,
    anterior.principal_propio,
    anterior.contexto_comercial,
    anterior.datos_ok,
    anterior.comercial_ok,
    anterior.metodo_virtual,
    anterior.asignacion_virtual,
    anterior.virtuales_ok,
    anterior.virtuales_ciclo,
    anterior.virtuales_asignados
   FROM anterior
  WHERE (anterior.escenario_id <> ALL (ARRAY[1, 2]))
UNION ALL
 SELECT v.nro_viaje,
    v.sk_fecha AS fecha_documental_id,
    s.id AS escenario_id,
    v.ingreso AS ingreso_estimado,
        CASE
            WHEN t.evaluable THEN t.costo_presentacion
            ELSE NULL::double precision
        END AS costo_fisico,
    t.margen_tarificador AS margen_fisico,
    COALESCE(t.evaluable, false) AS evaluable_fisico,
        CASE
            WHEN t.evaluable THEN t.costo_presentacion
            ELSE NULL::double precision
        END AS subtotal_conocido_fisico,
        CASE
            WHEN (t.nro_viaje IS NOT NULL) THEN t.componentes_esperados
            ELSE 0
        END AS componentes_presentes,
    COALESCE(t.componentes_esperados, 6) AS componentes_esperados,
        CASE
            WHEN (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) THEN t.estado
            WHEN t.evaluable THEN 'Evaluable tarificador (no costo físico)'::text
            ELSE COALESCE(t.estado, 'No cotizable: vacío, sin ingreso o fuera del alcance propio'::text)
        END AS motivo_fisico,
        CASE
            WHEN t.evaluable THEN t.costo_presentacion
            ELSE NULL::double precision
        END AS costo_comercial,
    t.margen_tarificador AS margen_comercial,
    COALESCE(t.evaluable, false) AS evaluable_comercial,
        CASE
            WHEN t.evaluable THEN t.costo_presentacion
            ELSE NULL::double precision
        END AS subtotal_conocido_comercial,
        CASE
            WHEN (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text])) THEN t.estado
            WHEN t.evaluable THEN 'Evaluable tarificador; sin redistribución'::text
            ELSE COALESCE(t.estado, 'No cotizable: vacío, sin ingreso o fuera del alcance propio'::text)
        END AS motivo_comercial,
    cy.ciclo_comercial AS ciclo_id,
    cy.rol_comercial,
    (v.tipo_operador = 'propio'::text) AS principal_propio,
    COALESCE(t.evaluable, false) AS contexto_comercial,
    COALESCE(t.evaluable, false) AS datos_ok,
    COALESCE(t.evaluable, false) AS comercial_ok,
    true AS metodo_virtual,
    true AS asignacion_virtual,
    true AS virtuales_ok,
    0 AS virtuales_ciclo,
    0 AS virtuales_asignados
   FROM (((ventas.fact_viaje_venta v
     CROSS JOIN ( VALUES (1), (2)) s(id))
     LEFT JOIN ds_gestion.v_costeo_servicio_1_2 t ON (((t.nro_viaje = v.nro_viaje) AND (t.escenario_id = s.id))))
     LEFT JOIN ciclos.fact_viaje_ciclo cy ON (((cy.nro_viaje = v.nro_viaje) AND (cy.es_virtual = 'N'::text))));


--
-- Name: v_tarificador_ciclo; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_tarificador_ciclo AS
 WITH mov AS (
         SELECT cy.ciclo_comercial,
            cy.nro_viaje,
            cy.es_virtual,
            v.tipo_operador,
            v.ingreso,
            ((v.ingreso > (0)::double precision) OR (v.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text]))) AS candidato
           FROM (ciclos.fact_viaje_ciclo cy
             LEFT JOIN ventas.fact_viaje_venta v ON ((v.nro_viaje = cy.nro_viaje)))
        ), alcance AS (
         SELECT d.ciclo_comercial,
            d.viaje_principal,
            p.sk_fecha AS fecha_documental_principal_id
           FROM ((ciclos.dim_ciclo_comercial d
             JOIN ciclos.fact_viaje_ciclo p ON ((p.id_viaje = d.viaje_principal)))
             JOIN ventas.fact_viaje_venta v ON ((v.nro_viaje = p.nro_viaje)))
          WHERE (v.tipo_operador = 'propio'::text)
        ), suma AS (
         SELECT a.ciclo_comercial AS ciclo_id,
            s.escenario_id,
            a.fecha_documental_principal_id,
            count(*) FILTER (WHERE ((m.es_virtual = 'N'::text) AND m.candidato)) AS servicios,
            count(*) FILTER (WHERE ((m.es_virtual = 'N'::text) AND m.candidato AND q.evaluable)) AS servicios_evaluables,
            count(*) FILTER (WHERE (m.es_virtual = 'N'::text)) AS reales,
            count(*) FILTER (WHERE (m.es_virtual = 'S'::text)) AS virtuales,
            bool_and((m.ingreso IS NOT NULL)) FILTER (WHERE (m.es_virtual = 'N'::text)) AS ingreso_completo,
            bool_and((m.tipo_operador = ANY (ARRAY['propio'::text, 'fletero'::text, 'caterina'::text]))) FILTER (WHERE ((m.es_virtual = 'N'::text) AND m.candidato)) AS operadores_admitidos,
            sum(m.ingreso) FILTER (WHERE (m.es_virtual = 'N'::text)) AS ingreso_estimado,
            sum(q.costo_presentacion) FILTER (WHERE q.evaluable) AS subtotal_conocido,
            sum(q.costo_presentacion) FILTER (WHERE q.evaluable) AS subtotal_presentacion,
            string_agg(DISTINCT COALESCE(q.estado, 'servicio fuera del alcance o sin clasificación'::text), '; '::text) FILTER (WHERE ((m.es_virtual = 'N'::text) AND m.candidato AND (NOT COALESCE(q.evaluable, false)))) AS motivos
           FROM (((alcance a
             CROSS JOIN ( VALUES (1), (2)) s(escenario_id))
             JOIN mov m ON ((m.ciclo_comercial = a.ciclo_comercial)))
             LEFT JOIN ds_gestion.v_costeo_servicio_1_2 q ON (((q.nro_viaje = m.nro_viaje) AND (q.escenario_id = s.escenario_id))))
          GROUP BY a.ciclo_comercial, s.escenario_id, a.fecha_documental_principal_id
        ), evaluado AS (
         SELECT suma.ciclo_id,
            suma.escenario_id,
            suma.fecha_documental_principal_id,
            suma.servicios,
            suma.servicios_evaluables,
            suma.reales,
            suma.virtuales,
            suma.ingreso_completo,
            suma.operadores_admitidos,
            suma.ingreso_estimado,
            suma.subtotal_conocido,
            suma.subtotal_presentacion,
            suma.motivos,
            COALESCE(((suma.servicios > 0) AND (suma.servicios = suma.servicios_evaluables) AND suma.ingreso_completo AND suma.operadores_admitidos), false) AS evaluable
           FROM suma
        )
 SELECT ciclo_id,
    escenario_id,
    fecha_documental_principal_id,
    servicios,
    servicios_evaluables,
    reales,
    virtuales,
    ingreso_completo,
    operadores_admitidos,
    ingreso_estimado,
    subtotal_conocido,
    subtotal_presentacion,
    motivos,
    evaluable,
        CASE
            WHEN evaluable THEN subtotal_conocido
            ELSE NULL::double precision
        END AS costo_tarificador,
        CASE
            WHEN evaluable THEN (ingreso_estimado - subtotal_conocido)
            ELSE NULL::double precision
        END AS margen_tarificador,
        CASE
            WHEN evaluable THEN subtotal_presentacion
            ELSE NULL::double precision
        END AS costo_presentacion,
        CASE
            WHEN evaluable THEN 'ok'::text
            ELSE COALESCE(motivos, 'sin servicios cotizables o ingreso incompleto'::text)
        END AS estado
   FROM evaluado;


--
-- Name: ref_equipo_taller; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.ref_equipo_taller (
    equipo_id bigint NOT NULL,
    codigo text,
    patente text,
    nombre text,
    categoria text,
    tipo_equipo text,
    grupo_empresa text,
    es_propio text,
    es_flota text,
    es_camion text,
    estado_actual text,
    marca text,
    modelo text,
    anio_fabricacion integer,
    rendimiento_ficha double precision,
    centro_costo_id bigint,
    unidad_negocio_id bigint,
    fuente text,
    fecha_carga timestamp without time zone
);


--
-- Name: resumen_actividad_equipo; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.resumen_actividad_equipo (
    equipo_id bigint NOT NULL,
    primera_actividad date,
    ultima_actividad date,
    fecha_alta date,
    fecha_baja date
);


--
-- Name: TABLE resumen_actividad_equipo; Type: COMMENT; Schema: taller; Owner: -
--

COMMENT ON TABLE taller.resumen_actividad_equipo IS 'Actividad y periodo inferidos para estado diario; no son alta/baja administrativa observada';


--
-- Name: dim_equipo; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.dim_equipo AS
 SELECT r.equipo_id,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.codigo
            ELSE r.codigo
        END AS codigo,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.patente
            ELSE r.patente
        END AS patente,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.nombre_fuente
            ELSE r.nombre
        END AS nombre,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN upper(TRIM(BOTH FROM m.categoria_fuente))
            ELSE r.categoria
        END AS categoria,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN
            CASE
                WHEN (upper(TRIM(BOTH FROM m.categoria_fuente)) ~~ 'TRACTO%'::text) THEN 'tracto'::text
                WHEN (upper(TRIM(BOTH FROM m.categoria_fuente)) = ANY (ARRAY['THERMO'::text, 'PLANA'::text, 'CAMA BAJA'::text, 'FURGON'::text])) THEN 'rampla'::text
                ELSE 'otro'::text
            END
            ELSE r.tipo_equipo
        END AS tipo_equipo,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.grupo_empresa_fuente
            ELSE r.grupo_empresa
        END AS grupo_empresa,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN
            CASE
                WHEN (upper(TRIM(BOTH FROM m.es_propio_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
                ELSE 'N'::text
            END
            ELSE r.es_propio
        END AS es_propio,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN
            CASE
                WHEN (upper(TRIM(BOTH FROM m.es_flota_fuente)) = ANY (ARRAY['Y'::text, 'S'::text, 'SI'::text, 'TRUE'::text, '1'::text])) THEN 'S'::text
                ELSE 'N'::text
            END
            ELSE r.es_flota
        END AS es_flota,
    r.es_camion,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.estado_fuente
            ELSE r.estado_actual
        END AS estado_actual,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.marca
            ELSE r.marca
        END AS marca,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.modelo
            ELSE r.modelo
        END AS modelo,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN ((m.anio_fabricacion_fuente)::numeric)::integer
            ELSE r.anio_fabricacion
        END AS anio_fabricacion,
    r.rendimiento_ficha,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.centro_costo_id
            ELSE r.centro_costo_id
        END AS centro_costo_id,
        CASE
            WHEN (m.equipo_id IS NOT NULL) THEN m.unidad_negocio_id
            ELSE r.unidad_negocio_id
        END AS unidad_negocio_id,
    a.primera_actividad,
    a.ultima_actividad,
    a.fecha_alta,
    a.fecha_baja,
    r.fuente,
    r.fecha_carga
   FROM ((taller.ref_equipo_taller r
     LEFT JOIN dim_comun.dim_equipo m USING (equipo_id))
     LEFT JOIN taller.resumen_actividad_equipo a USING (equipo_id));


--
-- Name: v_resultado_ciclo; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_resultado_ciclo AS
 WITH anterior AS (
         WITH alcance AS MATERIALIZED (
                 SELECT d.ciclo_comercial,
                    d.motivo_apertura,
                    d.motivo_cierre,
                    p.sk_fecha AS fecha_principal,
                    p.cliente_id AS cliente_principal_id,
                    p.sit_stretch_id AS tramo_principal_id,
                    p.ciudad_destino_limpia AS destino_principal,
                    eq.codigo AS equipo_codigo,
                    cl.razon_social AS cliente_principal,
                    tb.tramo AS tramo_principal
                   FROM (((((ciclos.dim_ciclo_comercial d
                     JOIN ciclos.fact_viaje_ciclo p ON ((p.id_viaje = d.viaje_principal)))
                     JOIN ventas.fact_viaje_venta v ON ((v.nro_viaje = p.nro_viaje)))
                     LEFT JOIN taller.dim_equipo eq ON ((eq.equipo_id = p.equipo_id)))
                     LEFT JOIN ventas.dim_cliente cl ON ((cl.cliente_id = p.cliente_id)))
                     LEFT JOIN ciclos.dim_tramo_base tb ON ((tb.sit_stretch_id = p.sit_stretch_id)))
                  WHERE (v.tipo_operador = 'propio'::text)
                ), mov_base AS MATERIALIZED (
                 SELECT cy.id_viaje,
                    cy.nro_viaje,
                    cy.equipo_id,
                    cy.orden_procesado,
                    cy.sk_fecha,
                    cy.sit_stretch_id,
                    cy.km,
                    cy.ciudad_origen_limpia,
                    cy.ciudad_destino_limpia,
                    cy.base_origen,
                    cy.base_destino,
                    cy.cliente_id,
                    cy.producto_id,
                    cy.ingreso,
                    cy.ingreso_por_km,
                    cy.tipo_movimiento,
                    cy.clasificacion_viaje,
                    cy.regla_clasificacion,
                    cy.tipo_carga_final,
                    cy.calidad_trayectoria,
                    cy.es_virtual,
                    cy.tipo_virtual,
                    cy.ciclo_comercial,
                    cy.orden_ciclo_comercial,
                    cy.rol_comercial,
                    v.tipo_operador,
                    v.ingreso AS ingreso_estimado,
                    lag(cy.ciudad_destino_limpia) OVER (PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS destino_previo,
                    lag(cy.base_destino) OVER (PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS base_previa,
                    row_number() OVER (PARTITION BY cy.ciclo_comercial ORDER BY cy.orden_ciclo_comercial) AS posicion
                   FROM ((ciclos.fact_viaje_ciclo cy
                     JOIN alcance a USING (ciclo_comercial))
                     LEFT JOIN ventas.fact_viaje_venta v ON ((v.nro_viaje = cy.nro_viaje)))
                ), mov AS MATERIALIZED (
                 SELECT m.id_viaje,
                    m.nro_viaje,
                    m.equipo_id,
                    m.orden_procesado,
                    m.sk_fecha,
                    m.sit_stretch_id,
                    m.km,
                    m.ciudad_origen_limpia,
                    m.ciudad_destino_limpia,
                    m.base_origen,
                    m.base_destino,
                    m.cliente_id,
                    m.producto_id,
                    m.ingreso,
                    m.ingreso_por_km,
                    m.tipo_movimiento,
                    m.clasificacion_viaje,
                    m.regla_clasificacion,
                    m.tipo_carga_final,
                    m.calidad_trayectoria,
                    m.es_virtual,
                    m.tipo_virtual,
                    m.ciclo_comercial,
                    m.orden_ciclo_comercial,
                    m.rol_comercial,
                    m.tipo_operador,
                    m.ingreso_estimado,
                    m.destino_previo,
                    m.base_previa,
                    m.posicion,
                    COALESCE(((m.posicion = 1) AND (m.es_virtual = 'N'::text) AND (m.calidad_trayectoria = 'IG'::text) AND (previo.es_virtual = 'S'::text) AND (previo.ciclo_comercial <> m.ciclo_comercial) AND (previo.ciudad_destino_limpia = m.ciudad_origen_limpia)), false) AS ig_heredada
                   FROM (mov_base m
                     LEFT JOIN ciclos.fact_viaje_ciclo previo ON (((m.posicion = 1) AND (m.calidad_trayectoria = 'IG'::text) AND (previo.equipo_id = m.equipo_id) AND (previo.orden_procesado = (m.orden_procesado - 1)))))
                ), estructura AS MATERIALIZED (
                 SELECT m.ciclo_comercial,
                    count(*) FILTER (WHERE (m.es_virtual = 'N'::text)) AS reales,
                    count(*) FILTER (WHERE (m.es_virtual = 'S'::text)) AS virtuales,
                    bool_and(COALESCE((m.tipo_operador = 'propio'::text), false)) FILTER (WHERE (m.es_virtual = 'N'::text)) AS propios,
                    bool_and(COALESCE((m.tipo_operador = ANY (ARRAY['propio'::text, 'fletero'::text, 'caterina'::text])), false)) FILTER (WHERE (m.es_virtual = 'N'::text)) AS operadores_admitidos,
                    bool_or((m.tipo_operador = ANY (ARRAY['fletero'::text, 'caterina'::text]))) FILTER (WHERE (m.es_virtual = 'N'::text)) AS contiene_terceros,
                    bool_and((m.ingreso_estimado IS NOT NULL)) FILTER (WHERE (m.es_virtual = 'N'::text)) AS ingreso_completo,
                    sum(m.ingreso_estimado) FILTER (WHERE (m.es_virtual = 'N'::text)) AS ingreso,
                    bool_and(((m.ciudad_origen_limpia IS NOT NULL) AND (m.ciudad_destino_limpia IS NOT NULL) AND ((m.posicion = 1) OR (m.ciudad_origen_limpia = m.destino_previo)) AND (m.km IS NOT NULL) AND (m.km >= (0)::double precision) AND (m.km < 'Infinity'::double precision) AND ((m.calidad_trayectoria IS DISTINCT FROM 'IG'::text) OR m.ig_heredada) AND (m.tipo_virtual IS DISTINCT FROM 'Virtual_Sin_Tramo'::text) AND (NOT ((m.km = (0)::double precision) AND (m.ciudad_origen_limpia IS DISTINCT FROM m.ciudad_destino_limpia))))) AS trayectoria_valida,
                    bool_and(((m.ciudad_origen_limpia IS NOT NULL) AND (m.ciudad_destino_limpia IS NOT NULL) AND ((m.posicion = 1) OR (m.ciudad_origen_limpia = m.destino_previo) OR ((m.calidad_trayectoria = 'NB'::text) AND (m.base_origen = m.base_previa) AND (m.base_origen IS NOT NULL) AND (m.base_origen <> ALL (ARRAY[''::text, 'NO APLICA'::text])))) AND (m.km IS NOT NULL) AND (m.km >= (0)::double precision) AND (m.km < 'Infinity'::double precision) AND ((m.calidad_trayectoria IS DISTINCT FROM 'IG'::text) OR m.ig_heredada) AND (m.tipo_virtual IS DISTINCT FROM 'Virtual_Sin_Tramo'::text) AND (NOT ((m.km = (0)::double precision) AND (m.ciudad_origen_limpia IS DISTINCT FROM m.ciudad_destino_limpia))))) AS trayectoria_flexible,
                    bool_or(((m.calidad_trayectoria = 'IG'::text) AND (NOT m.ig_heredada))) AS tiene_ig,
                    bool_or(m.ig_heredada) AS ig_heredada,
                    bool_or(((m.km = (0)::double precision) AND (m.ciudad_origen_limpia IS DISTINCT FROM m.ciudad_destino_limpia))) AS km_cero,
                    bool_or((m.tipo_virtual = 'Virtual_Sin_Tramo'::text)) AS virtual_sin_tramo,
                    count(*) FILTER (WHERE ((m.posicion > 1) AND (m.ciudad_origen_limpia IS DISTINCT FROM m.destino_previo))) AS saltos,
                    string_agg(((m.ciudad_origen_limpia || ' → '::text) || m.ciudad_destino_limpia), ' | '::text ORDER BY m.orden_ciclo_comercial) AS recorrido_piernas,
                    string_agg(
                        CASE
                            WHEN ((m.posicion > 1) AND (m.ciudad_origen_limpia IS DISTINCT FROM m.destino_previo)) THEN (((m.destino_previo || ' → '::text) || m.ciudad_origen_limpia) ||
                            CASE
                                WHEN ((m.calidad_trayectoria = 'NB'::text) AND (m.base_origen = m.base_previa)) THEN ' (conexión por base NB; distancia local no determinada)'::text
                                ELSE ' (conexión por revisar)'::text
                            END)
                            ELSE NULL::text
                        END, '; '::text ORDER BY m.orden_ciclo_comercial) AS conexiones,
                    (((array_agg(m.ciudad_origen_limpia ORDER BY m.orden_ciclo_comercial))[1] || ' → '::text) || string_agg(m.ciudad_destino_limpia, ' → '::text ORDER BY m.orden_ciclo_comercial)) AS recorrido_completo,
                    (json_agg(json_build_array(m.ciudad_origen_limpia, m.ciudad_destino_limpia, m.sit_stretch_id, m.km, m.es_virtual) ORDER BY m.orden_ciclo_comercial))::text AS patron_detalle
                   FROM mov m
                  GROUP BY m.ciclo_comercial
                ), contexto AS MATERIALIZED (
                 SELECT a.ciclo_comercial,
                    a.motivo_apertura,
                    a.motivo_cierre,
                    a.fecha_principal,
                    a.cliente_principal_id,
                    a.tramo_principal_id,
                    a.destino_principal,
                    a.equipo_codigo,
                    a.cliente_principal,
                    a.tramo_principal,
                    e.reales,
                    e.virtuales,
                    e.propios,
                    e.operadores_admitidos,
                    e.contiene_terceros,
                    e.ig_heredada,
                    e.ingreso_completo,
                    e.ingreso,
                    e.recorrido_completo,
                    md5(e.patron_detalle) AS patron_recorrido,
                    (e.trayectoria_flexible AND (((a.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text])) AND (a.motivo_cierre = ANY (ARRAY['llega a Santiago'::text, 'corte en Coquimbo'::text]))) OR ((a.motivo_apertura = 'local en Santiago'::text) AND (a.motivo_cierre = 'local'::text)))) AS estructura_flexible,
                    (((a.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text])) AND (a.motivo_cierre = ANY (ARRAY['llega a Santiago'::text, 'corte en Coquimbo'::text]))) OR ((a.motivo_apertura = 'local en Santiago'::text) AND (a.motivo_cierre = 'local'::text))) AS cierre_valido,
                    e.tiene_ig,
                    e.km_cero,
                    e.virtual_sin_tramo,
                    e.saltos,
                    e.recorrido_piernas,
                    e.conexiones,
                    (e.trayectoria_valida AND (((a.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text])) AND (a.motivo_cierre = ANY (ARRAY['llega a Santiago'::text, 'corte en Coquimbo'::text]))) OR ((a.motivo_apertura = 'local en Santiago'::text) AND (a.motivo_cierre = 'local'::text)))) AS estructura_valida,
                        CASE
                            WHEN ((a.motivo_apertura = 'local en Santiago'::text) AND (a.motivo_cierre = 'local'::text)) THEN 'local Santiago'::text
                            WHEN (('corte en Coquimbo'::text = a.motivo_apertura) OR ('corte en Coquimbo'::text = a.motivo_cierre)) THEN 'con corte Coquimbo'::text
                            WHEN ((a.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text])) AND (a.motivo_cierre = ANY (ARRAY['llega a Santiago'::text, 'corte en Coquimbo'::text]))) THEN 'Santiago-Santiago'::text
                            ELSE 'abierto o inicio incompleto'::text
                        END AS tipo_segmento
                   FROM (alcance a
                     JOIN estructura e USING (ciclo_comercial))
                ), real_det AS MATERIALIZED (
                 SELECT f.nro_viaje,
                    f.sk_escenario,
                    f.sk_componente,
                    f.sk_fecha,
                    f.cantidad,
                    f.tasa,
                    f.monto,
                    f.monto_comercial,
                    f.rol_comercial,
                    f.ciclo_comercial,
                    f.estado,
                    f.estado_comercial,
                    m.ciclo_comercial AS ciclo_seleccionado
                   FROM (escenarios.fact_costo_viaje f
                     JOIN mov m ON (((m.nro_viaje = f.nro_viaje) AND (m.es_virtual = 'N'::text))))
                  WHERE (f.sk_escenario = ANY (ARRAY[1, 2, 3]))
                ), virtual_det AS MATERIALIZED (
                 SELECT f.id_viaje,
                    f.ciclo_comercial,
                    f.nro_viaje_destino,
                    f.sk_fecha,
                    f.sk_escenario,
                    f.sk_componente,
                    f.cantidad,
                    f.tasa,
                    f.monto,
                    f.estado,
                    f.estado_asignacion,
                    f.estado_tramo,
                    f.estado_contexto,
                    f.origen_calculo,
                    f.cf_flota_virtual,
                    f.cf_rampla_virtual,
                    f.cf_estructura_virtual
                   FROM (escenarios.fact_costo_virtual f
                     JOIN alcance a USING (ciclo_comercial))
                  WHERE (f.sk_escenario = 3)
                ), virtual_estado AS MATERIALIZED (
                 SELECT v.id_viaje,
                    v.ciclo_comercial,
                    v.sk_escenario,
                    ((count(DISTINCT v.sk_componente) = 6) AND bool_and((COALESCE((v.estado = 'ok'::text), false) AND (v.monto IS NOT NULL) AND (destino.id_viaje IS NOT NULL)))) AS completo,
                    ((count(DISTINCT v.sk_componente) = 6) AND bool_and((COALESCE((v.estado = 'ok'::text), false) AND (v.monto IS NOT NULL)))) AS completo_fisico,
                    sum(v.monto) AS costo_fisico
                   FROM (virtual_det v
                     LEFT JOIN mov destino ON (((destino.es_virtual = 'N'::text) AND (destino.ciclo_comercial = v.ciclo_comercial) AND (destino.nro_viaje = v.nro_viaje_destino))))
                  GROUP BY v.id_viaje, v.ciclo_comercial, v.sk_escenario
                ), costos_comerciales AS MATERIALIZED (
                 SELECT real_det.nro_viaje,
                    real_det.sk_escenario,
                    real_det.monto_comercial AS monto,
                    real_det.estado_comercial AS estado
                   FROM real_det
                UNION ALL
                 SELECT f.nro_viaje_destino,
                    f.sk_escenario,
                    f.monto,
                    f.estado
                   FROM escenarios.fact_costo_virtual f
                  WHERE ((f.sk_escenario = 3) AND (EXISTS ( SELECT 1
                           FROM mov m
                          WHERE ((m.es_virtual = 'N'::text) AND (m.nro_viaje = f.nro_viaje_destino)))))
                ), costos_real AS MATERIALIZED (
                 SELECT real_det.nro_viaje,
                    real_det.sk_escenario,
                    bool_and((COALESCE((real_det.estado = 'ok'::text), false) AND (real_det.monto IS NOT NULL))) AS completo_fisico,
                    sum(real_det.monto) AS costo_fisico
                   FROM real_det
                  GROUP BY real_det.nro_viaje, real_det.sk_escenario
                ), costos_comercial AS MATERIALIZED (
                 SELECT costos_comerciales.nro_viaje,
                    costos_comerciales.sk_escenario,
                    bool_and((COALESCE((costos_comerciales.estado = 'ok'::text), false) AND (costos_comerciales.monto IS NOT NULL))) AS completo_comercial,
                    sum(costos_comerciales.monto) AS costo_comercial
                   FROM costos_comerciales
                  GROUP BY costos_comerciales.nro_viaje, costos_comerciales.sk_escenario
                ), viajes AS MATERIALIZED (
                 SELECT m.nro_viaje,
                    m.ciclo_comercial,
                    f.sk_escenario,
                        CASE
                            WHEN f.completo_fisico THEN (m.ingreso_estimado - f.costo_fisico)
                            ELSE NULL::double precision
                        END AS margen_fisico,
                        CASE
                            WHEN (c.completo_comercial AND ((f.sk_escenario <> ALL (ARRAY[1, 2])) OR (ctx.virtuales = 0))) THEN (m.ingreso_estimado - c.costo_comercial)
                            ELSE NULL::double precision
                        END AS margen_comercial,
                    c.costo_comercial,
                    c.completo_comercial
                   FROM (((mov m
                     JOIN costos_real f ON ((f.nro_viaje = m.nro_viaje)))
                     JOIN costos_comercial c ON (((c.nro_viaje = m.nro_viaje) AND (c.sk_escenario = f.sk_escenario))))
                     JOIN contexto ctx ON ((ctx.ciclo_comercial = m.ciclo_comercial)))
                  WHERE (m.es_virtual = 'N'::text)
                ), viajes_ciclo AS MATERIALIZED (
                 SELECT viajes.ciclo_comercial,
                    viajes.sk_escenario,
                    count(*) AS reales_con_costo,
                    bool_and((COALESCE(viajes.completo_comercial, false) AND (viajes.costo_comercial IS NOT NULL))) AS costos_completos,
                    sum(viajes.costo_comercial) AS costo
                   FROM viajes
                  GROUP BY viajes.ciclo_comercial, viajes.sk_escenario
                ), virtual_ciclo AS MATERIALIZED (
                 SELECT virtual_estado.ciclo_comercial,
                    virtual_estado.sk_escenario,
                    count(*) AS virtuales_con_costo,
                    bool_and(virtual_estado.completo) AS virtuales_completos
                   FROM virtual_estado
                  GROUP BY virtual_estado.ciclo_comercial, virtual_estado.sk_escenario
                ), ciclos_evaluados AS MATERIALIZED (
                 SELECT ctx.ciclo_comercial,
                    ctx.motivo_apertura,
                    ctx.motivo_cierre,
                    ctx.fecha_principal,
                    ctx.cliente_principal_id,
                    ctx.tramo_principal_id,
                    ctx.destino_principal,
                    ctx.equipo_codigo,
                    ctx.cliente_principal,
                    ctx.tramo_principal,
                    ctx.reales,
                    ctx.virtuales,
                    ctx.propios,
                    ctx.operadores_admitidos,
                    ctx.contiene_terceros,
                    ctx.ig_heredada,
                    ctx.ingreso_completo,
                    ctx.ingreso,
                    ctx.recorrido_completo,
                    ctx.patron_recorrido,
                    ctx.estructura_flexible,
                    ctx.cierre_valido,
                    ctx.tiene_ig,
                    ctx.km_cero,
                    ctx.virtual_sin_tramo,
                    ctx.saltos,
                    ctx.recorrido_piernas,
                    ctx.conexiones,
                    ctx.estructura_valida,
                    ctx.tipo_segmento,
                    s.sk_escenario,
                    COALESCE((ctx.estructura_valida AND (ctx.propios OR ((s.sk_escenario = 3) AND ctx.operadores_admitidos)) AND ctx.ingreso_completo AND (ctx.reales > 0) AND ((s.sk_escenario <> ALL (ARRAY[1, 2])) OR (ctx.virtuales = 0)) AND (v.reales_con_costo = ctx.reales) AND v.costos_completos AND ((ctx.virtuales = 0) OR ((ve.virtuales_con_costo = ctx.virtuales) AND ve.virtuales_completos))), false) AS completo,
                    COALESCE((ctx.estructura_flexible AND (ctx.propios OR ((s.sk_escenario = 3) AND ctx.operadores_admitidos)) AND ctx.ingreso_completo AND (ctx.reales > 0) AND ((s.sk_escenario <> ALL (ARRAY[1, 2])) OR (ctx.virtuales = 0)) AND (v.reales_con_costo = ctx.reales) AND v.costos_completos AND ((ctx.virtuales = 0) OR ((ve.virtuales_con_costo = ctx.virtuales) AND ve.virtuales_completos))), false) AS completo_flexible,
                    v.costo
                   FROM (((contexto ctx
                     CROSS JOIN escenarios.dim_escenario s)
                     LEFT JOIN viajes_ciclo v ON (((v.ciclo_comercial = ctx.ciclo_comercial) AND (v.sk_escenario = s.sk_escenario))))
                     LEFT JOIN virtual_ciclo ve ON (((ve.ciclo_comercial = ctx.ciclo_comercial) AND (ve.sk_escenario = s.sk_escenario))))
                  WHERE (s.sk_escenario = ANY (ARRAY[1, 2, 3]))
                ), fisico_real AS MATERIALIZED (
                 SELECT m.ciclo_comercial,
                    r.escenario_id,
                    count(*) AS reales,
                    bool_and(r.evaluable_fisico) AS completo,
                    sum(r.subtotal_conocido_fisico) AS conocido
                   FROM (mov m
                     JOIN ds_gestion.v_resultado_servicio r ON ((r.nro_viaje = m.nro_viaje)))
                  WHERE (m.es_virtual = 'N'::text)
                  GROUP BY m.ciclo_comercial, r.escenario_id
                ), fisico_virtual AS MATERIALIZED (
                 SELECT virtual_estado.ciclo_comercial,
                    virtual_estado.sk_escenario,
                    count(*) AS virtuales,
                    bool_and(virtual_estado.completo_fisico) AS completo,
                    sum(virtual_estado.costo_fisico) AS conocido
                   FROM virtual_estado
                  GROUP BY virtual_estado.ciclo_comercial, virtual_estado.sk_escenario
                ), financiero AS MATERIALIZED (
                 SELECT c.ciclo_comercial,
                    c.motivo_apertura,
                    c.motivo_cierre,
                    c.fecha_principal,
                    c.cliente_principal_id,
                    c.tramo_principal_id,
                    c.destino_principal,
                    c.equipo_codigo,
                    c.cliente_principal,
                    c.tramo_principal,
                    c.reales,
                    c.virtuales,
                    c.propios,
                    c.operadores_admitidos,
                    c.contiene_terceros,
                    c.ig_heredada,
                    c.ingreso_completo,
                    c.ingreso,
                    c.recorrido_completo,
                    c.patron_recorrido,
                    c.estructura_flexible,
                    c.cierre_valido,
                    c.tiene_ig,
                    c.km_cero,
                    c.virtual_sin_tramo,
                    c.saltos,
                    c.recorrido_piernas,
                    c.conexiones,
                    c.estructura_valida,
                    c.tipo_segmento,
                    c.sk_escenario,
                    c.completo,
                    c.completo_flexible,
                    c.costo,
                    v.reales_con_costo,
                    v.costos_completos,
                    vc.virtuales_con_costo,
                    vc.virtuales_completos,
                    COALESCE(((c.propios OR ((c.sk_escenario = 3) AND c.operadores_admitidos)) AND c.ingreso_completo AND (c.reales > 0) AND ((c.sk_escenario <> ALL (ARRAY[1, 2])) OR (c.virtuales = 0)) AND (v.reales_con_costo = c.reales) AND v.costos_completos AND ((c.virtuales = 0) OR ((vc.virtuales_con_costo = c.virtuales) AND vc.virtuales_completos))), false) AS financiero_comercial_completo,
                    COALESCE(((c.propios OR ((c.sk_escenario = 3) AND c.operadores_admitidos)) AND c.ingreso_completo AND (c.reales > 0) AND ((c.sk_escenario <> ALL (ARRAY[1, 2])) OR (c.virtuales = 0)) AND (fr.reales = c.reales) AND fr.completo AND ((c.virtuales = 0) OR ((fv.virtuales = c.virtuales) AND fv.completo))), false) AS financiero_fisico_completo,
                        CASE
                            WHEN ((fr.conocido IS NOT NULL) OR (fv.conocido IS NOT NULL)) THEN (COALESCE(fr.conocido, (0)::double precision) + COALESCE(fv.conocido, (0)::double precision))
                            ELSE NULL::double precision
                        END AS conocido_fisico
                   FROM ((((ciclos_evaluados c
                     LEFT JOIN viajes_ciclo v ON (((v.ciclo_comercial = c.ciclo_comercial) AND (v.sk_escenario = c.sk_escenario))))
                     LEFT JOIN virtual_ciclo vc ON (((vc.ciclo_comercial = c.ciclo_comercial) AND (vc.sk_escenario = c.sk_escenario))))
                     LEFT JOIN fisico_real fr ON (((fr.ciclo_comercial = c.ciclo_comercial) AND (fr.escenario_id = c.sk_escenario))))
                     LEFT JOIN fisico_virtual fv ON (((fv.ciclo_comercial = c.ciclo_comercial) AND (fv.sk_escenario = c.sk_escenario))))
                )
         SELECT financiero.ciclo_comercial AS ciclo_id,
            financiero.sk_escenario AS escenario_id,
            financiero.fecha_principal AS fecha_documental_principal_id,
                CASE
                    WHEN financiero.ingreso_completo THEN financiero.ingreso
                    ELSE NULL::double precision
                END AS ingreso_estimado,
            financiero.costo AS subtotal_conocido_comercial,
            financiero.conocido_fisico AS subtotal_conocido_fisico,
                CASE
                    WHEN financiero.financiero_comercial_completo THEN financiero.costo
                    ELSE NULL::double precision
                END AS costo_comercial,
                CASE
                    WHEN financiero.financiero_comercial_completo THEN (financiero.ingreso - financiero.costo)
                    ELSE NULL::double precision
                END AS margen_comercial,
                CASE
                    WHEN financiero.financiero_fisico_completo THEN financiero.conocido_fisico
                    ELSE NULL::double precision
                END AS costo_fisico,
                CASE
                    WHEN financiero.financiero_fisico_completo THEN (financiero.ingreso - financiero.conocido_fisico)
                    ELSE NULL::double precision
                END AS margen_fisico,
            financiero.financiero_comercial_completo,
            financiero.financiero_fisico_completo,
            financiero.reales,
            financiero.virtuales,
            financiero.propios,
            financiero.ingreso_completo,
            (financiero.estructura_valida AND ((financiero.sk_escenario = 3) OR (NOT financiero.ig_heredada))) AS estructura_conservadora,
            (financiero.estructura_flexible AND ((financiero.sk_escenario = 3) OR (NOT financiero.ig_heredada))) AS estructura_flexible,
            financiero.cierre_valido,
            (financiero.tiene_ig OR ((financiero.sk_escenario <> 3) AND financiero.ig_heredada)) AS tiene_ig,
            financiero.km_cero,
            financiero.virtual_sin_tramo,
            financiero.saltos,
            COALESCE(((financiero.reales_con_costo = financiero.reales) AND financiero.costos_completos), false) AS costos_reales_completos,
            COALESCE(((financiero.virtuales = 0) OR ((financiero.virtuales_con_costo = financiero.virtuales) AND financiero.virtuales_completos)), false) AS costos_virtuales_completos,
            ((financiero.sk_escenario <> ALL (ARRAY[1, 2])) OR (financiero.virtuales = 0)) AS metodo_virtual_disponible,
                CASE
                    WHEN (financiero.estructura_valida AND ((financiero.sk_escenario = 3) OR (NOT financiero.ig_heredada))) THEN 'Sin bloqueo estructural'::text
                    WHEN (financiero.estructura_flexible AND ((financiero.sk_escenario = 3) OR (NOT financiero.ig_heredada))) THEN 'Solo conexión por base NB'::text
                    WHEN (financiero.tiene_ig OR ((financiero.sk_escenario <> 3) AND financiero.ig_heredada)) THEN 'Inconsistencia geográfica IG'::text
                    WHEN ((financiero.sk_escenario = 3) AND (NOT COALESCE((financiero.motivo_apertura = ANY (ARRAY['sale de Santiago'::text, 'corte en Coquimbo'::text, 'local en Santiago'::text])), false))) THEN 'Apertura no admitida'::text
                    WHEN (NOT financiero.cierre_valido) THEN
                    CASE
                        WHEN (financiero.sk_escenario = 3) THEN 'Cierre no admitido'::text
                        ELSE 'Apertura o cierre no admitidos'::text
                    END
                    ELSE 'Otros problemas de distancia o continuidad'::text
                END AS submotivo_estructura,
            concat_ws('; '::text,
                CASE
                    WHEN (financiero.saltos > 0) THEN 'Conexión entre ciudades distintas'::text
                    ELSE NULL::text
                END,
                CASE
                    WHEN financiero.tiene_ig THEN 'Marca IG'::text
                    ELSE NULL::text
                END,
                CASE
                    WHEN (NOT financiero.cierre_valido) THEN 'Apertura/cierre no admitidos'::text
                    ELSE NULL::text
                END,
                CASE
                    WHEN financiero.km_cero THEN 'Km cero entre ciudades distintas'::text
                    ELSE NULL::text
                END,
                CASE
                    WHEN financiero.virtual_sin_tramo THEN 'Virtual sin tramo'::text
                    ELSE NULL::text
                END) AS condiciones_estructura,
            financiero.recorrido_piernas AS recorrido_movimientos,
            COALESCE(financiero.conexiones, 'Sin salto entre ciudades registrado; establecimientos no identificados'::text) AS conexiones,
                CASE
                    WHEN financiero.propios THEN 'Solo TC'::text
                    WHEN (financiero.operadores_admitidos AND financiero.contiene_terceros) THEN 'Mixto'::text
                    ELSE 'Operador sin clasificar'::text
                END AS tipo_operacion_ciclo,
            ((financiero.sk_escenario = 3) AND financiero.ig_heredada) AS ig_heredada_admitida
           FROM financiero
        )
 SELECT anterior.ciclo_id,
    anterior.escenario_id,
    anterior.fecha_documental_principal_id,
    anterior.ingreso_estimado,
    anterior.subtotal_conocido_comercial,
    anterior.subtotal_conocido_fisico,
    anterior.costo_comercial,
    anterior.margen_comercial,
    anterior.costo_fisico,
    anterior.margen_fisico,
    anterior.financiero_comercial_completo,
    anterior.financiero_fisico_completo,
    anterior.reales,
    anterior.virtuales,
    anterior.propios,
    anterior.ingreso_completo,
    anterior.estructura_conservadora,
    anterior.estructura_flexible,
    anterior.cierre_valido,
    anterior.tiene_ig,
    anterior.km_cero,
    anterior.virtual_sin_tramo,
    anterior.saltos,
    anterior.costos_reales_completos,
    anterior.costos_virtuales_completos,
    anterior.metodo_virtual_disponible,
    anterior.submotivo_estructura,
    anterior.condiciones_estructura,
    anterior.recorrido_movimientos,
    anterior.conexiones,
    anterior.tipo_operacion_ciclo,
    anterior.ig_heredada_admitida
   FROM anterior
  WHERE (anterior.escenario_id <> ALL (ARRAY[1, 2]))
UNION ALL
 SELECT a.ciclo_id,
    a.escenario_id,
    a.fecha_documental_principal_id,
    t.ingreso_estimado,
    t.subtotal_conocido AS subtotal_conocido_comercial,
    t.subtotal_conocido AS subtotal_conocido_fisico,
    t.costo_tarificador AS costo_comercial,
    t.margen_tarificador AS margen_comercial,
    t.costo_tarificador AS costo_fisico,
    t.margen_tarificador AS margen_fisico,
    COALESCE(t.evaluable, false) AS financiero_comercial_completo,
    COALESCE(t.evaluable, false) AS financiero_fisico_completo,
    a.reales,
    a.virtuales,
    COALESCE(t.operadores_admitidos, false) AS propios,
    COALESCE(t.ingreso_completo, false) AS ingreso_completo,
    true AS estructura_conservadora,
    true AS estructura_flexible,
    a.cierre_valido,
    a.tiene_ig,
    a.km_cero,
    a.virtual_sin_tramo,
    a.saltos,
    COALESCE(t.evaluable, false) AS costos_reales_completos,
    true AS costos_virtuales_completos,
    true AS metodo_virtual_disponible,
    'No aplica: cotización independiente por servicio'::text AS submotivo_estructura,
    'No se costean vacíos ni virtuales; se suman los servicios cotizables'::text AS condiciones_estructura,
    a.recorrido_movimientos,
    a.conexiones,
    a.tipo_operacion_ciclo,
    a.ig_heredada_admitida
   FROM (anterior a
     LEFT JOIN ds_gestion.v_tarificador_ciclo t ON (((t.ciclo_id = a.ciclo_id) AND (t.escenario_id = a.escenario_id))))
  WHERE (a.escenario_id = ANY (ARRAY[1, 2]));


--
-- Name: v_ciclo_politica; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_ciclo_politica AS
 SELECT r.ciclo_id,
    r.escenario_id,
    p.politica_id,
    p.nombre AS politica,
    p.version_regla,
    COALESCE((r.financiero_comercial_completo AND
        CASE
            WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
            ELSE r.estructura_flexible
        END), false) AS evaluable,
        CASE
            WHEN COALESCE((r.financiero_comercial_completo AND
            CASE
                WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
                ELSE r.estructura_flexible
            END), false) THEN 'Evaluable'::text
            WHEN (NOT COALESCE(
            CASE
                WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
                ELSE r.estructura_flexible
            END, false)) THEN 'Estructura o trayectoria no evaluable'::text
            WHEN (NOT COALESCE((r.propios OR ((r.escenario_id = 3) AND (r.tipo_operacion_ciclo = 'Mixto'::text))), false)) THEN 'Alcance de operadores no equivalente'::text
            WHEN (NOT COALESCE(r.ingreso_completo, false)) THEN 'Ingreso estimado incompleto'::text
            WHEN (NOT r.metodo_virtual_disponible) THEN 'Virtual sin método equivalente en 1/2'::text
            WHEN ((r.virtuales > 0) AND (NOT r.costos_virtuales_completos)) THEN 'Costos virtuales incompletos'::text
            WHEN (NOT r.costos_reales_completos) THEN 'Costos de viajes reales incompletos'::text
            ELSE 'Costeo o asignación virtual incompletos'::text
        END AS motivo,
    r.fecha_documental_principal_id,
    r.ingreso_estimado,
    r.costo_comercial,
    r.margen_comercial,
    r.submotivo_estructura,
    r.condiciones_estructura,
    r.estructura_conservadora,
    r.estructura_flexible,
    r.propios,
    r.ingreso_completo,
    r.costos_reales_completos,
    r.costos_virtuales_completos,
    r.metodo_virtual_disponible,
    r.recorrido_movimientos,
    r.conexiones
   FROM (ds_gestion.v_resultado_ciclo r
     CROSS JOIN ds_gestion.v_politica_cobertura p);


--
-- Name: v_combustible_certificado; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_combustible_certificado AS
 SELECT id_viaje_fuente AS movimiento_cadena_id,
    nro_viaje,
    equipo_id,
    sk_fecha_inicio AS fecha_documental_id,
    litros_asignados,
    litros_margen,
    ajuste_litros,
    estado_litros,
    fraccion_certificada,
    fuente_ficha,
    motivo_bloque,
    precio_litro_margen,
    costo_combustible AS costo_original,
    costo_combustible_margen,
    costo_ajuste
   FROM combustible.fact_viaje_combustible;


--
-- Name: dim_componente_costo; Type: TABLE; Schema: escenarios; Owner: -
--

CREATE TABLE escenarios.dim_componente_costo (
    sk_componente integer NOT NULL,
    nombre text,
    unidad text,
    tipo text
);


--
-- Name: v_componente; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_componente AS
 SELECT sk_componente AS componente_id,
    nombre,
    unidad,
    tipo
   FROM escenarios.dim_componente_costo;


--
-- Name: v_costo_comercial; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_costo_comercial AS
 SELECT ('R:'::text || (fact_costo_viaje.nro_viaje)::text) AS registro_id,
    'saldo_comercial_real'::text AS tipo_registro,
    NULL::text AS movimiento_origen_id,
    ('R:'::text || (fact_costo_viaje.nro_viaje)::text) AS movimiento_destino_id,
    fact_costo_viaje.sk_escenario AS escenario_id,
    fact_costo_viaje.sk_componente AS componente_id,
    fact_costo_viaje.monto_comercial AS monto,
    fact_costo_viaje.estado_comercial AS estado,
    'saldo_redistribuido_sin_linaje_origen_completo'::text AS alcance_trazabilidad
   FROM escenarios.fact_costo_viaje
UNION ALL
 SELECT ('V:'::text || fact_costo_virtual.id_viaje) AS registro_id,
    'imputacion_virtual'::text AS tipo_registro,
    ('V:'::text || fact_costo_virtual.id_viaje) AS movimiento_origen_id,
        CASE
            WHEN (fact_costo_virtual.nro_viaje_destino IS NOT NULL) THEN ('R:'::text || (fact_costo_virtual.nro_viaje_destino)::text)
            ELSE NULL::text
        END AS movimiento_destino_id,
    fact_costo_virtual.sk_escenario AS escenario_id,
    fact_costo_virtual.sk_componente AS componente_id,
    fact_costo_virtual.monto,
    fact_costo_virtual.estado,
    'origen_y_destino_virtual'::text AS alcance_trazabilidad
   FROM escenarios.fact_costo_virtual;


--
-- Name: v_costo_fisico; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_costo_fisico AS
 SELECT ('R:'::text || (fact_costo_viaje.nro_viaje)::text) AS movimiento_id,
    false AS es_virtual,
    fact_costo_viaje.sk_escenario AS escenario_id,
    fact_costo_viaje.sk_componente AS componente_id,
    fact_costo_viaje.sk_fecha AS fecha_documental_id,
    fact_costo_viaje.cantidad,
    fact_costo_viaje.tasa,
    fact_costo_viaje.monto,
    fact_costo_viaje.estado,
    'escenarios.fact_costo_viaje'::text AS origen
   FROM escenarios.fact_costo_viaje
UNION ALL
 SELECT ('V:'::text || fact_costo_virtual.id_viaje) AS movimiento_id,
    true AS es_virtual,
    fact_costo_virtual.sk_escenario AS escenario_id,
    fact_costo_virtual.sk_componente AS componente_id,
    fact_costo_virtual.sk_fecha AS fecha_documental_id,
    fact_costo_virtual.cantidad,
    fact_costo_virtual.tasa,
    fact_costo_virtual.monto,
    fact_costo_virtual.estado,
    'escenarios.fact_costo_virtual'::text AS origen
   FROM escenarios.fact_costo_virtual;


--
-- Name: v_escenario; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_escenario AS
 SELECT sk_escenario AS escenario_id,
    nombre,
    descripcion
   FROM escenarios.dim_escenario;


--
-- Name: v_evaluacion_ciclo; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_evaluacion_ciclo AS
 SELECT r.ciclo_id,
    r.escenario_id,
    p.politica_id,
    p.nombre AS politica,
    p.version_regla,
    COALESCE((r.financiero_comercial_completo AND
        CASE
            WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
            ELSE r.estructura_flexible
        END), false) AS evaluable,
        CASE
            WHEN COALESCE((r.financiero_comercial_completo AND
            CASE
                WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
                ELSE r.estructura_flexible
            END), false) THEN 'Evaluable'::text
            WHEN (NOT COALESCE(
            CASE
                WHEN (p.politica_id = 'conservador'::text) THEN r.estructura_conservadora
                ELSE r.estructura_flexible
            END, false)) THEN 'Estructura o trayectoria no evaluable'::text
            WHEN (NOT COALESCE((r.propios OR ((r.escenario_id = 3) AND (r.tipo_operacion_ciclo = 'Mixto'::text))), false)) THEN 'Alcance de operadores no equivalente'::text
            WHEN (NOT COALESCE(r.ingreso_completo, false)) THEN 'Ingreso estimado incompleto'::text
            WHEN (NOT r.metodo_virtual_disponible) THEN 'Virtual sin método equivalente en 1/2'::text
            WHEN ((r.virtuales > 0) AND (NOT r.costos_virtuales_completos)) THEN 'Costos virtuales incompletos'::text
            WHEN (NOT r.costos_reales_completos) THEN 'Costos de viajes reales incompletos'::text
            ELSE 'Costeo o asignación virtual incompletos'::text
        END AS motivo
   FROM (ds_gestion.v_resultado_ciclo r
     CROSS JOIN ds_gestion.v_politica_cobertura p);


--
-- Name: v_ingreso_servicio; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_ingreso_servicio AS
 SELECT ('R:'::text || (nro_viaje)::text) AS movimiento_id,
    nro_viaje,
    sk_fecha AS fecha_documental_id,
    cliente_id,
    ingreso AS ingreso_estimado,
        CASE
            WHEN (ingreso IS NULL) THEN 'faltante'::text
            WHEN (ingreso = (0)::double precision) THEN 'cero_registrado'::text
            ELSE 'registrado'::text
        END AS estado_ingreso
   FROM ventas.fact_viaje_venta;


--
-- Name: v_km_corredor; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_km_corredor AS
 WITH m AS (
         SELECT (v_movimiento.fecha_documental_id / 100) AS mes_id,
            to_char((to_date((v_movimiento.fecha_documental_id)::text, 'YYYYMMDD'::text))::timestamp with time zone, 'YYYY-MM'::text) AS mes,
            COALESCE(((v_movimiento.origen || ' → '::text) || v_movimiento.destino), 'Sin corredor'::text) AS corredor,
            v_movimiento.km,
            v_movimiento.vacio_fisico,
            (((v_movimiento.km >= (0)::double precision) AND (v_movimiento.km < 'Infinity'::double precision) AND (v_movimiento.vacio_fisico = ANY (ARRAY['S'::text, 'N'::text])) AND (v_movimiento.calidad_trayectoria = ANY (ARRAY['N'::text, 'NB'::text])) AND (NOT ((v_movimiento.km = (0)::double precision) AND (v_movimiento.origen IS DISTINCT FROM v_movimiento.destino)))) IS TRUE) AS km_evaluable
           FROM ds_gestion.v_movimiento
          WHERE ((NOT v_movimiento.es_virtual) AND (v_movimiento.tipo_operador = 'propio'::text))
        )
 SELECT mes_id,
    mes,
    corredor,
    count(*) AS movimientos_alcance,
    count(*) FILTER (WHERE km_evaluable) AS movimientos_evaluables,
    sum(km) FILTER (WHERE km_evaluable) AS km_reales_evaluables,
        CASE
            WHEN (count(*) FILTER (WHERE km_evaluable) > 0) THEN COALESCE(sum(km) FILTER (WHERE (km_evaluable AND (vacio_fisico = 'S'::text))), (0)::double precision)
            ELSE NULL::double precision
        END AS km_vacios_reales,
    'propios reales; km evaluable: distancia finita no negativa, vacío S/N conocido, trayectoria N/NB, sin 0 km entre ciudades distintas'::text AS regla_km
   FROM m
  GROUP BY mes_id, mes, corredor;


--
-- Name: v_puente_operacional_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_puente_operacional_mensual AS
 SELECT c.mes_id,
    c.sk_escenario,
    p.orden,
    p.concepto,
    p.monto,
    c.periodo_con_fuentes_comunes,
    c.componentes_modelados_completos,
    c.estado_conciliacion,
    c.resultado_operacional_antes_depreciacion AS resultado_objetivo,
    c.residuo_aritmetico,
    c.alcance
   FROM (ds_gestion.v_conciliacion_operacional_mensual c
     CROSS JOIN LATERAL ( VALUES (1,'Margen modelado: subtotal conocido'::text,c.margen_modelado_subtotal), (2,'Sustituir ingreso estimado por ingreso de explotacion contable'::text,c.diferencia_ingresos), (3,'Sustituir fijo modelado por fijo contable operacional'::text,c.diferencia_fijos), (4,'Separar reposicion para comparar antes de depreciacion'::text,c.reposicion_modelada_conocida), (5,'Conciliar otros costos: diferencia agregada pendiente de detalle'::text,c.diferencia_otros_costos)) p(orden, concepto, monto));


--
-- Name: v_rendimiento_tarificador_certificado; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_rendimiento_tarificador_certificado AS
 SELECT nro_viaje,
    equipo_id,
    fecha_documental_id,
    ventana_bloques,
    rendimiento_camion,
    bloques_utilizados,
    fecha_ultimo_bloque,
    fecha_primer_bloque,
    bloques_utilizados_ids,
    rendimiento_supuesto,
    origen_rendimiento,
    rendimiento_previo,
    bloques_previos,
    fecha_ultimo_previo
   FROM combustible.v_rendimiento_tarificador_certificado;


--
-- Name: v_retorno; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_retorno AS
 SELECT movimiento_id,
    nro_viaje,
    (nro_viaje)::text AS viaje_id,
    fecha_documental_id,
    (fecha_documental_id / 100) AS mes_id,
    to_char((to_date((fecha_documental_id)::text, 'YYYYMMDD'::text))::timestamp with time zone, 'YYYY-MM'::text) AS mes,
    origen,
    destino,
    COALESCE(((origen || ' → '::text) || destino), 'Sin corredor'::text) AS corredor,
    cliente_id,
    COALESCE(cliente_nombre, 'Sin cliente'::text) AS cliente,
    producto_id,
    producto_nombre,
    tipo_operador,
    COALESCE((tipo_operador = 'propio'::text), false) AS es_propio,
    ciclo_id,
    rol_comercial,
    vacio_fisico,
    equipo_id,
    equipo_nombre,
    conductor_nombre,
    'viaje real clasificado Retorno en el ciclo comercial; fecha documental ERP, no GPS'::text AS alcance
   FROM ds_gestion.v_movimiento
  WHERE ((NOT es_virtual) AND (clasificacion_viaje = 'Retorno'::text));


--
-- Name: v_rubros_contables_mensual; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_rubros_contables_mensual AS
 WITH base AS (
         SELECT ((f.anio * 100) + f.mes) AS mes_id,
            (f.monto)::numeric AS monto,
            d.linea_eerr,
                CASE
                    WHEN (d.codigo = '310020104'::text) THEN 'comision'::text
                    WHEN (d.clase_tarificador = 'fijo'::text) THEN 'fijo'::text
                    WHEN ((d.clase_tarificador = 'combustible'::text) OR (d.linea_eerr = '007'::text)) THEN 'combustible'::text
                    WHEN (d.clase_tarificador = 'peaje'::text) THEN 'peajes'::text
                    WHEN (d.clase_tarificador = 'neumatico'::text) THEN 'neumaticos'::text
                    WHEN (d.linea_eerr = '006'::text) THEN 'fleteros'::text
                    ELSE 'otros'::text
                END AS rubro
           FROM (costos.fact_contabilidad_mensual f
             JOIN costos.dim_cuenta_contable d USING (sk_cuenta))
          WHERE ((f.anio >= 2025) AND (d.linea_eerr = ANY (ARRAY['002'::text, '003'::text, '004'::text, '005'::text, '006'::text, '007'::text, '008'::text, '009'::text])))
        ), gastos AS (
         SELECT base.mes_id,
            base.rubro,
            sum(base.monto) AS monto,
            count(*) AS registros
           FROM base
          GROUP BY base.mes_id, base.rubro
        )
 SELECT c.mes_id,
    to_char((to_date((c.mes_id)::text, 'YYYYMM'::text))::timestamp with time zone, 'YYYY-MM'::text) AS mes,
    r.codigo,
    r.nombre,
    r.orden,
    (
        CASE r.codigo
            WHEN 'ingreso'::text THEN c.ingreso_explotacion
            WHEN 'costo_total'::text THEN (c.costo_explotacion + c.administracion_ventas)
            WHEN 'resultado'::text THEN c.resultado_operacional_antes_depreciacion
            WHEN 'margen_pct'::text THEN c.margen_operacional_antes_depreciacion_pct
            WHEN 'depreciacion'::text THEN
            CASE
                WHEN (c.registros_depreciacion > 0) THEN c.depreciacion_registrada
                ELSE NULL::numeric
            END
            ELSE COALESCE(g.monto, (0)::numeric)
        END)::double precision AS monto,
        CASE
            WHEN (r.codigo = 'depreciacion'::text) THEN c.registros_depreciacion
            ELSE COALESCE(g.registros, c.registros_contables)
        END AS registros,
    c.ultimo_mes_observado,
    'Empresa completa por periodo contable; rubros de gasto operacional sin solapamiento. Fijo operacional excluye comision y financieros. Depreciacion informativa, fuera del resultado mostrado. No equivale a poblacion de ciclos.'::text AS alcance
   FROM ((ds_gestion.v_referencia_contable_mensual c
     CROSS JOIN ( VALUES ('ingreso'::text,'Ingreso de explotación'::text,1), ('combustible'::text,'Combustible, incluido impuesto específico'::text,2), ('peajes'::text,'Peajes'::text,3), ('neumaticos'::text,'Neumáticos'::text,4), ('comision'::text,'Comisión de viajes'::text,5), ('fleteros'::text,'Pagos a terceros'::text,6), ('fijo'::text,'Fijo operacional'::text,7), ('otros'::text,'Otros gastos operacionales'::text,8), ('costo_total'::text,'Costo operacional total'::text,9), ('resultado'::text,'Resultado antes de depreciación'::text,10), ('margen_pct'::text,'Margen operacional %'::text,11), ('depreciacion'::text,'Depreciación registrada · informativa'::text,12)) r(codigo, nombre, orden))
     LEFT JOIN gastos g ON (((g.mes_id = c.mes_id) AND (g.rubro = r.codigo))))
  WHERE (c.mes_id >= 202501);


--
-- Name: v_tarificador_servicio; Type: VIEW; Schema: ds_gestion; Owner: -
--

CREATE VIEW ds_gestion.v_tarificador_servicio AS
 SELECT q.nro_viaje,
    q.fecha_documental_id,
    q.cliente_id,
    q.equipo_id,
    q.tramo_id,
    q.es_tractoreo,
    q.ingreso_estimado,
    q.clasificacion,
    q.escenario_id,
    q.tipo_carga,
    q.version,
    q.fuente_sha256,
    q.estado_km,
    q.supuesto_rm,
    q.texto_tramo,
    q.n_puntos,
    q.c9,
    q.c10,
    q.c11,
    q.c12,
    q.c13,
    q.b4,
    q.b5,
    q.b6,
    q.b7,
    q.n5,
    q.rendimiento,
    q.i4,
    q.costo_presentacion,
    q.evaluable,
    q.estado,
    q.e10,
    q.e11,
    q.e12,
    q.e13,
    q.e14,
    q.g4,
    q.n4,
    q.g5,
    q.e7,
    q.c21,
    q.e8,
    q.e5,
    q.e6,
    q.g7,
    q.e22,
    q.d22,
    q.c22,
    q.f22,
    q.e15,
    q.f15,
    q.g6,
    q.g8,
    q.d27,
    q.i5,
    q.k5,
    q.i6,
    q.i7,
    q.k15,
    q.j8,
    q.i8,
    q.i9,
    q.i10,
    q.i11,
    q.i12,
    q.i13,
    q.i14,
    q.i15,
    q.i16,
    q.factor_retorno,
    q.subtotal,
    q.i17,
    q.i18,
    q.i19,
    q.i20,
    q.i21,
    q.i22,
    q.k10,
    q.k11,
    q.k12,
    q.j8_estado,
    q.margen_tarificador,
    q.razon_ingreso_cotizacion,
    cy.ciclo_comercial AS ciclo_id,
    cl.razon_social AS cliente,
    'Tarificador'::text AS familia_modelo,
    'Costo del retorno mostrado como cero por decisión de presentación; I17 y margen calculado se conservan'::text AS regla_presentacion
   FROM ((tarificador.fact_cotizacion_servicio q
     LEFT JOIN ciclos.fact_viaje_ciclo cy ON (((cy.nro_viaje = q.nro_viaje) AND (cy.es_virtual = 'N'::text))))
     LEFT JOIN ventas.dim_cliente cl ON ((cl.cliente_id = q.cliente_id)));


--
-- Name: v_capacidad_con_virtuales; Type: VIEW; Schema: escenarios; Owner: -
--

CREATE VIEW escenarios.v_capacidad_con_virtuales AS
 WITH bolsas AS (
         SELECT (fact_costo_virtual.sk_fecha / 10000) AS anio,
            'flota'::text AS bolsa,
            fact_costo_virtual.cf_flota_virtual AS monto
           FROM escenarios.fact_costo_virtual
          WHERE ((fact_costo_virtual.sk_escenario = 3) AND (fact_costo_virtual.sk_componente = 4) AND (fact_costo_virtual.estado = 'ok'::text))
        UNION ALL
         SELECT (fact_costo_virtual.sk_fecha / 10000),
            'rampla'::text,
            fact_costo_virtual.cf_rampla_virtual
           FROM escenarios.fact_costo_virtual
          WHERE ((fact_costo_virtual.sk_escenario = 3) AND (fact_costo_virtual.sk_componente = 4) AND (fact_costo_virtual.estado = 'ok'::text))
        UNION ALL
         SELECT (fact_costo_virtual.sk_fecha / 10000),
            'estructura'::text,
            fact_costo_virtual.cf_estructura_virtual
           FROM escenarios.fact_costo_virtual
          WHERE ((fact_costo_virtual.sk_escenario = 3) AND (fact_costo_virtual.sk_componente = 4) AND (fact_costo_virtual.estado = 'ok'::text))
        ), v AS (
         SELECT bolsas.anio,
            bolsas.bolsa,
            sum(bolsas.monto) AS absorbido_virtual_conocido
           FROM bolsas
          GROUP BY bolsas.anio, bolsas.bolsa
        )
 SELECT c.anio,
    c.bolsa,
    c.monto_bolsa,
    c.capacidad_dias,
    c.cuota_media,
    c.dias_usados,
    c.absorbido,
    c.capacidad_ociosa,
    c.pct_absorbido,
    COALESCE(v.absorbido_virtual_conocido, (0)::double precision) AS absorbido_virtual_conocido,
    ((c.absorbido)::double precision + COALESCE(v.absorbido_virtual_conocido, (0)::double precision)) AS absorbido_real_y_virtual_conocido,
    ((c.capacidad_ociosa)::double precision - COALESCE(v.absorbido_virtual_conocido, (0)::double precision)) AS residual_tras_virtuales_conocidos,
    'solo virtuales con fijo completo; no equivale a capacidad ociosa validada'::text AS alcance_ajuste
   FROM (costos.fact_capacidad_bolsa c
     LEFT JOIN v ON (((v.anio = c.anio) AND (v.bolsa = c.bolsa))));


--
-- Name: v_costo_virtual_resumen; Type: VIEW; Schema: escenarios; Owner: -
--

CREATE VIEW escenarios.v_costo_virtual_resumen AS
 SELECT id_viaje,
    sk_escenario,
    sk_fecha,
    ciclo_comercial,
    nro_viaje_destino,
    max(estado_asignacion) AS estado_asignacion,
    max(estado_tramo) AS estado_tramo,
    max(estado_contexto) AS estado_contexto,
    sum(monto) AS subtotal_conocido,
    (bool_and(((estado = 'ok'::text) AND (monto IS NOT NULL))) AND (count(*) = 6)) AS completo,
        CASE
            WHEN (bool_and(((estado = 'ok'::text) AND (monto IS NOT NULL))) AND (count(*) = 6)) THEN sum(monto)
            ELSE NULL::double precision
        END AS costo_completo,
        CASE
            WHEN (nro_viaje_destino IS NULL) THEN 'conservado sin imputacion comercial'::text
            ELSE 'imputado al principal propio'::text
        END AS destino_del_costo
   FROM escenarios.fact_costo_virtual
  GROUP BY id_viaje, sk_escenario, sk_fecha, ciclo_comercial, nro_viaje_destino;


--
-- Name: v_tramos_virtuales_pendientes; Type: VIEW; Schema: escenarios; Owner: -
--

CREATE VIEW escenarios.v_tramos_virtuales_pendientes AS
 SELECT cy.id_viaje,
    cy.sk_fecha,
    cy.equipo_id,
    cy.ciclo_comercial,
    cy.ciudad_origen_limpia AS origen,
    cy.ciudad_destino_limpia AS destino,
    cy.tipo_virtual,
    cy.km AS km_registrados,
        CASE
            WHEN (cy.tipo_virtual = 'Virtual_Sin_Tramo'::text) THEN 'sin_tramo'::text
            WHEN (cy.km IS NULL) THEN 'distancia_ausente'::text
            WHEN (cy.km < (0)::double precision) THEN 'distancia_invalida'::text
            WHEN ((cy.km = (0)::double precision) AND (COALESCE(cy.ciudad_origen_limpia, ''::text) <> ''::text) AND (COALESCE(cy.ciudad_destino_limpia, ''::text) <> ''::text) AND (cy.ciudad_origen_limpia <> cy.ciudad_destino_limpia)) THEN 'distancia_no_resuelta'::text
            ELSE 'disponible'::text
        END AS estado_tramo,
    true AS requiere_revision_ti,
    NULL::double precision AS km_para_costeo,
    'pendiente_revision'::text AS estado_revision,
    'TI / maestro de tramos'::text AS responsable_sugerido,
    'Verificar extremos y crear o corregir tramo; recargar despues de validar'::text AS accion_requerida,
    'Propuestas calculadas fuera del costo oficial'::text AS politica_estimacion,
    dc.viaje_principal,
    vp.tipo_operador AS operador_principal,
    dc.estado AS estado_geografico_ciclo,
    dc.ingreso AS ingreso_ciclo_referencia
   FROM (((ciclos.fact_viaje_ciclo cy
     LEFT JOIN ciclos.dim_ciclo_comercial dc ON ((dc.ciclo_comercial = cy.ciclo_comercial)))
     LEFT JOIN ciclos.fact_viaje_ciclo cp ON ((cp.id_viaje = dc.viaje_principal)))
     LEFT JOIN ventas.fact_viaje_venta vp ON ((vp.nro_viaje = cp.nro_viaje)))
  WHERE ((cy.es_virtual = 'S'::text) AND (
        CASE
            WHEN (cy.tipo_virtual = 'Virtual_Sin_Tramo'::text) THEN 'sin_tramo'::text
            WHEN (cy.km IS NULL) THEN 'distancia_ausente'::text
            WHEN (cy.km < (0)::double precision) THEN 'distancia_invalida'::text
            WHEN ((cy.km = (0)::double precision) AND (COALESCE(cy.ciudad_origen_limpia, ''::text) <> ''::text) AND (COALESCE(cy.ciudad_destino_limpia, ''::text) <> ''::text) AND (cy.ciudad_origen_limpia <> cy.ciudad_destino_limpia)) THEN 'distancia_no_resuelta'::text
            ELSE 'disponible'::text
        END <> 'disponible'::text));


--
-- Name: v_tramos_virtuales_pendientes_resumen; Type: VIEW; Schema: escenarios; Owner: -
--

CREATE VIEW escenarios.v_tramos_virtuales_pendientes_resumen AS
 SELECT origen,
    destino,
    estado_tramo,
    count(*) AS virtuales_afectados,
    count(DISTINCT equipo_id) AS equipos_afectados,
    count(DISTINCT ciclo_comercial) AS ciclos_afectados,
    min(sk_fecha) AS primera_fecha,
    max(sk_fecha) AS ultima_fecha
   FROM escenarios.v_tramos_virtuales_pendientes
  GROUP BY origen, destino, estado_tramo;


--
-- Name: ajuste_segmento_plaza; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.ajuste_segmento_plaza (
    desde_clave text,
    hasta_clave text,
    id_plaza text,
    accion text,
    km_desde_inicio double precision,
    vigente_desde date,
    motivo text
);


--
-- Name: dim_condicion_tarifa; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_condicion_tarifa (
    sk_condicion bigint NOT NULL,
    categoria_vehicular text,
    horario text,
    modalidad text,
    aplica_a_flota text
);


--
-- Name: dim_condicion_tarifa_sk_condicion_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_condicion_tarifa ALTER COLUMN sk_condicion ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_condicion_tarifa_sk_condicion_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_documento_tarifa; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_documento_tarifa (
    sk_documento bigint NOT NULL,
    url text,
    documento text,
    seccion text,
    anio integer,
    archivo_local text,
    sha256 text,
    bytes bigint,
    fecha_descarga timestamp without time zone
);


--
-- Name: dim_documento_tarifa_sk_documento_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_documento_tarifa ALTER COLUMN sk_documento ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_documento_tarifa_sk_documento_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_lugar; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_lugar (
    sk_lugar bigint NOT NULL,
    clave_lugar text,
    nombre_erp text,
    localidad text,
    sublugar text,
    region text,
    latitud double precision,
    longitud double precision,
    "precision" text,
    fuente text,
    es_base text,
    es_lugar text
);


--
-- Name: dim_lugar_sk_lugar_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_lugar ALTER COLUMN sk_lugar ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_lugar_sk_lugar_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_plaza_peaje; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_plaza_peaje (
    sk_plaza bigint NOT NULL,
    id_plaza text,
    clave_plaza_script text,
    plaza_id_mop text,
    nombre_plaza text,
    codigo_portico text,
    concesion text,
    documento_tarifa text,
    tipo_red text,
    cobro_por_recorrido text,
    ruta text,
    km_ruta double precision,
    region text,
    latitud double precision,
    longitud double precision,
    fuente_coordenadas text,
    posicion text,
    tipo_cobro text,
    fuente_registro text,
    estado text,
    vigente_desde date,
    vigente_hasta date,
    es_actual text
);


--
-- Name: dim_plaza_peaje_sk_plaza_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_plaza_peaje ALTER COLUMN sk_plaza ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_plaza_peaje_sk_plaza_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_segmento_ruta; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.dim_segmento_ruta (
    sk_segmento bigint NOT NULL,
    desde_clave text,
    hasta_clave text,
    sk_lugar_desde bigint,
    sk_lugar_hasta bigint,
    km_ruta double precision,
    horas_ruta double precision,
    estado text,
    version_ruta text
);


--
-- Name: dim_segmento_ruta_sk_segmento_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_segmento_ruta ALTER COLUMN sk_segmento ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_segmento_ruta_sk_segmento_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dim_tramo_sk_tramo_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.dim_tramo ALTER COLUMN sk_tramo ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.dim_tramo_sk_tramo_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fact_peaje_referencial_plaza; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.fact_peaje_referencial_plaza (
    sk_tramo bigint NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    orden_segmento integer NOT NULL,
    sk_segmento bigint,
    orden_en_segmento integer NOT NULL,
    id_plaza text,
    tarifa_base_plaza double precision,
    tarifa_maxima_plaza double precision,
    categoria_base text,
    horario_base text,
    modalidad_base text,
    cobrada text,
    peaje_base double precision,
    peaje_maximo double precision,
    motivo text
);


--
-- Name: fact_peaje_referencial_tramo; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.fact_peaje_referencial_tramo (
    sk_tramo bigint NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    peaje_base double precision,
    peaje_maximo double precision,
    cantidad_plazas integer,
    plazas_sin_tarifa integer,
    estado text
);


--
-- Name: fact_tarifa_peaje; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.fact_tarifa_peaje (
    sk_tarifa bigint NOT NULL,
    sk_plaza bigint,
    id_plaza text,
    sk_condicion bigint,
    variante integer,
    vigente_desde date,
    vigente_hasta date,
    tarifa_clp double precision,
    origen_tarifa text,
    metodo_asignacion text,
    confianza text,
    categoria_texto_lamina text,
    sk_documento bigint,
    fecha_carga date,
    observacion text
);


--
-- Name: fact_tarifa_peaje_sk_tarifa_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.fact_tarifa_peaje ALTER COLUMN sk_tarifa ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.fact_tarifa_peaje_sk_tarifa_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fact_viaje_peaje_sk_viaje_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.fact_viaje_peaje ALTER COLUMN sk_viaje ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME peajes.fact_viaje_peaje_sk_viaje_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: parametro_peaje; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.parametro_peaje (
    parametro text NOT NULL,
    valor text,
    descripcion text
);


--
-- Name: puente_segmento_plaza; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.puente_segmento_plaza (
    id_fila bigint NOT NULL,
    sk_segmento bigint,
    id_plaza text,
    orden integer,
    km_desde_inicio double precision,
    metodo_deteccion text,
    confianza text,
    origen text,
    vigente_desde date,
    vigente_hasta date
);


--
-- Name: puente_segmento_plaza_deteccion; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.puente_segmento_plaza_deteccion (
    id_fila bigint NOT NULL,
    sk_segmento bigint,
    id_plaza text,
    orden integer,
    km_desde_inicio double precision,
    metodo_deteccion text,
    confianza text,
    vigente_desde date,
    vigente_hasta date
);


--
-- Name: puente_segmento_plaza_deteccion_id_fila_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.puente_segmento_plaza_deteccion ALTER COLUMN id_fila ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME peajes.puente_segmento_plaza_deteccion_id_fila_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: puente_segmento_plaza_id_fila_seq; Type: SEQUENCE; Schema: peajes; Owner: -
--

ALTER TABLE peajes.puente_segmento_plaza ALTER COLUMN id_fila ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME peajes.puente_segmento_plaza_id_fila_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: puente_tramo_segmento; Type: TABLE; Schema: peajes; Owner: -
--

CREATE TABLE peajes.puente_tramo_segmento (
    sk_tramo bigint NOT NULL,
    sk_segmento bigint,
    orden integer NOT NULL
);


--
-- Name: v_peaje_tramo_detalle; Type: VIEW; Schema: peajes; Owner: -
--

CREATE VIEW peajes.v_peaje_tramo_detalle AS
 SELECT d.sk_tramo,
    t.sit_stretch_id,
    t.texto_tramo,
    d.orden_segmento,
    d.orden_en_segmento,
    d.id_plaza,
    p.nombre_plaza,
    p.concesion,
    p.ruta,
    p.km_ruta,
    p.posicion,
    p.tipo_cobro,
    d.categoria_base,
    d.horario_base,
    d.modalidad_base,
    d.tarifa_base_plaza,
    d.cobrada,
    d.peaje_base,
    d.peaje_maximo,
    d.motivo,
    d.vigente_desde,
    d.vigente_hasta
   FROM ((peajes.fact_peaje_referencial_plaza d
     JOIN peajes.dim_tramo t ON ((t.sk_tramo = d.sk_tramo)))
     LEFT JOIN peajes.dim_plaza_peaje p ON (((p.id_plaza = d.id_plaza) AND (p.es_actual = 'S'::text))))
  WHERE ((CURRENT_DATE >= d.vigente_desde) AND (CURRENT_DATE <= d.vigente_hasta));


--
-- Name: v_peaje_viaje; Type: VIEW; Schema: peajes; Owner: -
--

CREATE VIEW peajes.v_peaje_viaje AS
 SELECT v.nro_viaje,
    v.nro_hoja_ruta,
    f.fecha,
    t.sit_stretch_id,
    t.texto_tramo,
    v.peaje_referencial,
    v.peaje_referencial_maximo,
    v.estado_peaje
   FROM ((peajes.fact_viaje_peaje v
     JOIN comun.dim_fecha f ON ((f.sk_fecha = v.sk_fecha_inicio)))
     JOIN peajes.dim_tramo t ON ((t.sk_tramo = v.sk_tramo)));


--
-- Name: v_puente_tramo_plaza; Type: VIEW; Schema: peajes; Owner: -
--

CREATE VIEW peajes.v_puente_tramo_plaza AS
 SELECT t.sk_tramo,
    t.sit_stretch_id,
    t.texto_tramo,
    ts.orden AS orden_segmento,
    s.desde_clave,
    s.hasta_clave,
    sp.orden AS orden_en_segmento,
    sp.id_plaza,
    p.nombre_plaza,
    p.concesion,
    p.posicion,
    sp.metodo_deteccion,
    sp.confianza,
    sp.origen,
    sp.vigente_desde,
    sp.vigente_hasta
   FROM ((((peajes.dim_tramo t
     JOIN peajes.puente_tramo_segmento ts ON ((ts.sk_tramo = t.sk_tramo)))
     JOIN peajes.dim_segmento_ruta s ON ((s.sk_segmento = ts.sk_segmento)))
     JOIN peajes.puente_segmento_plaza sp ON ((sp.sk_segmento = s.sk_segmento)))
     LEFT JOIN peajes.dim_plaza_peaje p ON (((p.id_plaza = sp.id_plaza) AND (p.es_actual = 'S'::text))));


--
-- Name: v_tarifa_vigente; Type: VIEW; Schema: peajes; Owner: -
--

CREATE VIEW peajes.v_tarifa_vigente AS
 SELECT p.id_plaza,
    p.nombre_plaza,
    p.concesion,
    p.posicion,
    c.categoria_vehicular,
    c.horario,
    c.modalidad,
    f.variante,
    f.tarifa_clp,
    f.vigente_desde,
    f.vigente_hasta,
    f.origen_tarifa,
    d.url AS documento_url
   FROM (((peajes.fact_tarifa_peaje f
     JOIN peajes.dim_plaza_peaje p ON ((p.sk_plaza = f.sk_plaza)))
     JOIN peajes.dim_condicion_tarifa c ON ((c.sk_condicion = f.sk_condicion)))
     LEFT JOIN peajes.dim_documento_tarifa d ON ((d.sk_documento = f.sk_documento)))
  WHERE ((CURRENT_DATE >= f.vigente_desde) AND (CURRENT_DATE <= f.vigente_hasta));


--
-- Name: carta_archivo; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.carta_archivo (
    archivo text NOT NULL,
    hash_sha256 text,
    tipo text,
    sk_carta integer,
    estado text,
    motivo text,
    remitente text,
    fecha_hora_correo timestamp without time zone,
    asunto text,
    message_id text
);


--
-- Name: dim_carta_precio; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.dim_carta_precio (
    sk_carta integer NOT NULL,
    id_carta text,
    sk_fuente integer,
    nro_carta text,
    fecha_emision date,
    hora_emision time without time zone,
    fecha_vigencia date,
    fecha_hora_correo timestamp without time zone,
    destinatarios text,
    copias integer,
    es_rectificacion text,
    anulada text,
    reemplazada_por text,
    ie_fijo numeric(12,5),
    ie_variable numeric(12,5),
    ie_total numeric(12,5),
    archivo text,
    hash_sha256 text
);


--
-- Name: dim_fuente_precio; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.dim_fuente_precio (
    sk_fuente integer NOT NULL,
    fuente text,
    oilsupplier text,
    descripcion text
);


--
-- Name: fact_costo_combustible_vigente; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.fact_costo_combustible_vigente (
    sk_fuente integer NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    sk_carta integer,
    sk_precio integer,
    origen_precio text,
    localidad_region text,
    volumen_camion numeric(10,3),
    neto numeric(14,5),
    ie_carta numeric(14,5),
    ie_aplicado numeric(14,5),
    ie_corregido text,
    pct_recuperacion_ie numeric(8,5),
    precio_facturado numeric(14,5),
    desembolso_neto_iva numeric(14,5),
    costo_real numeric(14,5)
);


--
-- Name: fact_precio_combustible; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.fact_precio_combustible (
    sk_precio integer NOT NULL,
    sk_carta integer,
    sk_fuente integer,
    localidad_region text,
    solicitante text,
    producto text,
    volumen_camion numeric(10,3),
    neto numeric(14,5),
    neto_deducido text,
    fepp numeric(14,5),
    monto_afecto numeric(14,5),
    iva numeric(14,5),
    ie numeric(14,5),
    precio_final numeric(14,5),
    es_base text,
    es_elegida text,
    es_tct_historico text
);


--
-- Name: parametro_precio; Type: TABLE; Schema: precios; Owner: -
--

CREATE TABLE precios.parametro_precio (
    clave text NOT NULL,
    valor numeric(12,6),
    vigente_desde date NOT NULL,
    vigente_hasta date,
    fuente text,
    nota text
);


--
-- Name: v_costo_combustible_dia; Type: VIEW; Schema: precios; Owner: -
--

CREATE VIEW precios.v_costo_combustible_dia AS
 SELECT d.sk_fecha,
    d.fecha,
    f.fuente,
    f.oilsupplier,
    v.sk_carta,
    v.precio_facturado,
    v.desembolso_neto_iva,
    v.costo_real,
    v.vigente_desde
   FROM ((precios.fact_costo_combustible_vigente v
     JOIN precios.dim_fuente_precio f ON ((f.sk_fuente = v.sk_fuente)))
     JOIN comun.dim_fecha d ON (((d.fecha >= v.vigente_desde) AND (d.fecha < v.vigente_hasta) AND (d.fecha <= CURRENT_DATE))));


--
-- Name: dim_pauta; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.dim_pauta (
    pauta_id bigint NOT NULL,
    nombre text,
    intervalo double precision,
    unidad text,
    fuente text
);


--
-- Name: dim_repuesto; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.dim_repuesto (
    producto_id bigint NOT NULL,
    codigo text,
    descripcion text,
    categoria text,
    unidad text,
    costo_estandar double precision,
    es_combustible text,
    es_mantencion text
);


--
-- Name: dim_tipo_intervencion; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.dim_tipo_intervencion (
    tipo_id integer NOT NULL,
    c_doctype_id bigint,
    tipo_documento text,
    tipo_mant text,
    area_taller text,
    tipo_medicion text,
    es_sanitizacion text
);


--
-- Name: fact_consumo_repuesto; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.fact_consumo_repuesto (
    consumo_id bigint NOT NULL,
    nro_documento text,
    sk_fecha integer,
    fecha date,
    producto_id bigint,
    bodega_id bigint,
    ubicacion_id bigint,
    centro_costo_id bigint,
    unidad_negocio_id bigint,
    sit_workorder_id bigint,
    equipo_id bigint,
    via_imputacion text,
    cantidad double precision,
    costo double precision,
    odometro double precision
);


--
-- Name: fact_estado_diario_equipo; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.fact_estado_diario_equipo (
    equipo_id bigint NOT NULL,
    sk_fecha integer NOT NULL,
    en_flota smallint,
    horas_taller double precision,
    horas_preventiva double precision,
    horas_correctiva double precision,
    horas_cierre_real double precision,
    en_taller smallint,
    en_taller_cierre_real smallint,
    tipo_taller text,
    con_viaje smallint,
    viaje_estimado smallint,
    dia_disponible smallint,
    dia_trabajado smallint,
    viajes_iniciados integer,
    km_viajes double precision,
    ingreso_viajes double precision
);


--
-- Name: fact_orden_taller; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.fact_orden_taller (
    sit_workorder_id bigint NOT NULL,
    nro_ot text,
    tipo_id integer,
    equipo_id bigint,
    pauta_id bigint,
    pauta_siguiente_id bigint,
    seqno integer,
    solicitud_id bigint,
    nro_solicitud text,
    sk_fecha_apertura integer,
    sk_fecha_cierre integer,
    fecha_hora_apertura timestamp without time zone,
    fecha_hora_cierre timestamp without time zone,
    flag_cierre_imputado text,
    docstatus text,
    horas_ciclo double precision,
    odometro double precision,
    horometro double precision,
    prioridad text,
    costo_repuestos double precision,
    lineas_consumo integer
);


--
-- Name: intervalo_pauta_equipo; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.intervalo_pauta_equipo (
    equipo_id bigint NOT NULL,
    pauta_id bigint NOT NULL,
    intervalo double precision,
    unidad text,
    fuente text
);


--
-- Name: parametro_taller; Type: TABLE; Schema: taller; Owner: -
--

CREATE TABLE taller.parametro_taller (
    parametro text NOT NULL,
    valor text,
    unidad text,
    nota text
);


--
-- Name: v_calidad_ot; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_calidad_ot AS
 WITH base AS (
         SELECT o.sit_workorder_id,
            o.nro_ot,
            o.equipo_id,
            o.tipo_id,
            t.tipo_documento,
            o.sk_fecha_apertura,
            o.fecha_hora_apertura,
            o.fecha_hora_cierre,
            o.docstatus,
            o.flag_cierre_imputado,
                CASE
                    WHEN (o.fecha_hora_cierre IS NULL) THEN 'sin_fecha'::text
                    WHEN (o.flag_cierre_imputado = 'N'::text) THEN 'enddate_fuente'::text
                    WHEN (o.flag_cierre_imputado = 'S'::text) THEN 'updated_imputado'::text
                    ELSE 'origen_no_identificado'::text
                END AS origen_cierre,
                CASE
                    WHEN (o.fecha_hora_apertura IS NULL) THEN 'sin_apertura'::text
                    WHEN (o.fecha_hora_cierre IS NULL) THEN 'sin_cierre'::text
                    WHEN (o.fecha_hora_cierre < o.fecha_hora_apertura) THEN 'cierre_anterior_apertura'::text
                    ELSE 'intervalo_no_negativo'::text
                END AS estado_intervalo,
            COALESCE((o.docstatus = ANY (ARRAY['CO'::text, 'CL'::text])), false) AS documento_cerrado
           FROM (taller.fact_orden_taller o
             LEFT JOIN taller.dim_tipo_intervencion t USING (tipo_id))
        ), x AS (
         SELECT base.sit_workorder_id,
            base.nro_ot,
            base.equipo_id,
            base.tipo_id,
            base.tipo_documento,
            base.sk_fecha_apertura,
            base.fecha_hora_apertura,
            base.fecha_hora_cierre,
            base.docstatus,
            base.flag_cierre_imputado,
            base.origen_cierre,
            base.estado_intervalo,
            base.documento_cerrado,
            ((base.origen_cierre = 'enddate_fuente'::text) AND (base.estado_intervalo = 'intervalo_no_negativo'::text) AND base.documento_cerrado) AS evaluable_duracion_cierre_fuente,
            ((base.origen_cierre = 'updated_imputado'::text) AND (base.estado_intervalo = 'intervalo_no_negativo'::text) AND base.documento_cerrado) AS evaluable_duracion_cierre_imputado
           FROM base
        )
 SELECT sit_workorder_id,
    nro_ot,
    equipo_id,
    tipo_id,
    tipo_documento,
    sk_fecha_apertura,
    fecha_hora_apertura,
    fecha_hora_cierre,
    docstatus,
    flag_cierre_imputado,
    origen_cierre,
    estado_intervalo,
    documento_cerrado,
    evaluable_duracion_cierre_fuente,
    evaluable_duracion_cierre_imputado,
        CASE
            WHEN evaluable_duracion_cierre_fuente THEN (EXTRACT(epoch FROM (fecha_hora_cierre - fecha_hora_apertura)) / (3600)::numeric)
            ELSE NULL::numeric
        END AS horas_ciclo_cierre_fuente,
        CASE
            WHEN evaluable_duracion_cierre_imputado THEN (EXTRACT(epoch FROM (fecha_hora_cierre - fecha_hora_apertura)) / (3600)::numeric)
            ELSE NULL::numeric
        END AS horas_ciclo_cierre_imputado
   FROM x;


--
-- Name: VIEW v_calidad_ot; Type: COMMENT; Schema: taller; Owner: -
--

COMMENT ON VIEW taller.v_calidad_ot IS 'Calidad documental: created y enddate no acreditan por sí solos inicio/fin físico de taller. CO/CL identifica documento cerrado. No se modifican fechas.';


--
-- Name: v_calidad_ot_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_calidad_ot_mensual AS
 SELECT (date_trunc('month'::text, fecha_hora_apertura))::date AS mes_apertura,
    tipo_documento,
    count(*) AS ordenes,
    count(*) FILTER (WHERE (origen_cierre = 'enddate_fuente'::text)) AS con_cierre_fuente,
    count(*) FILTER (WHERE (origen_cierre = 'updated_imputado'::text)) AS con_cierre_imputado,
    count(*) FILTER (WHERE (origen_cierre = 'sin_fecha'::text)) AS sin_fecha_cierre,
    count(*) FILTER (WHERE (origen_cierre = 'origen_no_identificado'::text)) AS origen_no_identificado,
    count(*) FILTER (WHERE (estado_intervalo = 'sin_apertura'::text)) AS sin_apertura,
    count(*) FILTER (WHERE (estado_intervalo = 'cierre_anterior_apertura'::text)) AS cierre_anterior_apertura,
    count(*) FILTER (WHERE documento_cerrado) AS documentos_cerrados,
    count(*) FILTER (WHERE evaluable_duracion_cierre_fuente) AS evaluables_cierre_fuente,
    sum(horas_ciclo_cierre_fuente) AS horas_ciclo_cierre_fuente,
    count(*) FILTER (WHERE evaluable_duracion_cierre_imputado) AS evaluables_cierre_imputado,
    sum(horas_ciclo_cierre_imputado) AS horas_ciclo_cierre_imputado
   FROM taller.v_calidad_ot
  GROUP BY ((date_trunc('month'::text, fecha_hora_apertura))::date), tipo_documento;


--
-- Name: v_cobertura_imputacion_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_cobertura_imputacion_mensual AS
 SELECT (date_trunc('month'::text, (c.fecha)::timestamp with time zone))::date AS mes_consumo,
    count(*) AS lineas_mantencion,
    sum((c.costo)::numeric) AS costo_mantencion,
    count(*) FILTER (WHERE ((c.equipo_id IS NOT NULL) AND (c.equipo_id <> '-1'::integer))) AS lineas_con_equipo,
    count(*) FILTER (WHERE ((c.equipo_id IS NULL) OR (c.equipo_id = '-1'::integer))) AS lineas_sin_equipo,
    sum((c.costo)::numeric) FILTER (WHERE ((c.equipo_id IS NOT NULL) AND (c.equipo_id <> '-1'::integer))) AS costo_con_equipo,
    sum((c.costo)::numeric) FILTER (WHERE ((c.equipo_id IS NULL) OR (c.equipo_id = '-1'::integer))) AS costo_sin_equipo,
    ((100.0 * (count(*) FILTER (WHERE ((c.equipo_id IS NOT NULL) AND (c.equipo_id <> '-1'::integer))))::numeric) / (NULLIF(count(*), 0))::numeric) AS cobertura_lineas_pct
   FROM (taller.fact_consumo_repuesto c
     JOIN taller.dim_repuesto p USING (producto_id))
  WHERE ((p.es_mantencion = 'S'::text) AND (p.es_combustible = 'N'::text))
  GROUP BY ((date_trunc('month'::text, (c.fecha)::timestamp with time zone))::date);


--
-- Name: v_cumplimiento_preventivo; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_cumplimiento_preventivo AS
 WITH p AS (
         SELECT o.sit_workorder_id,
            o.nro_ot,
            o.tipo_id,
            o.equipo_id,
            o.pauta_id,
            o.pauta_siguiente_id,
            o.seqno,
            o.solicitud_id,
            o.nro_solicitud,
            o.sk_fecha_apertura,
            o.sk_fecha_cierre,
            o.fecha_hora_apertura,
            o.fecha_hora_cierre,
            o.flag_cierre_imputado,
            o.docstatus,
            o.horas_ciclo,
            o.odometro,
            o.horometro,
            o.prioridad,
            o.costo_repuestos,
            o.lineas_consumo,
            t.tipo_medicion,
            lead(o.sit_workorder_id) OVER w AS ot_siguiente,
            lead(o.fecha_hora_apertura) OVER w AS fecha_siguiente,
            lead(o.odometro) OVER w AS odometro_siguiente,
            lead(o.horometro) OVER w AS horometro_siguiente
           FROM (taller.fact_orden_taller o
             JOIN taller.dim_tipo_intervencion t ON ((t.tipo_id = o.tipo_id)))
          WHERE ((t.tipo_documento = 'OT'::text) AND (t.tipo_mant = 'PREVENTIVA'::text) AND (o.docstatus <> ALL (ARRAY['VO'::text, 'DR'::text])))
          WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura)
        ), i AS (
         SELECT p.sit_workorder_id,
            p.nro_ot,
            p.tipo_id,
            p.equipo_id,
            p.pauta_id,
            p.pauta_siguiente_id,
            p.seqno,
            p.solicitud_id,
            p.nro_solicitud,
            p.sk_fecha_apertura,
            p.sk_fecha_cierre,
            p.fecha_hora_apertura,
            p.fecha_hora_cierre,
            p.flag_cierre_imputado,
            p.docstatus,
            p.horas_ciclo,
            p.odometro,
            p.horometro,
            p.prioridad,
            p.costo_repuestos,
            p.lineas_consumo,
            p.tipo_medicion,
            p.ot_siguiente,
            p.fecha_siguiente,
            p.odometro_siguiente,
            p.horometro_siguiente,
            COALESCE(ie.intervalo, dp.intervalo) AS intervalo,
            COALESCE(ie.unidad, dp.unidad) AS unidad
           FROM ((p
             LEFT JOIN taller.intervalo_pauta_equipo ie ON (((ie.equipo_id = p.equipo_id) AND (ie.pauta_id = p.pauta_siguiente_id))))
             LEFT JOIN taller.dim_pauta dp ON ((dp.pauta_id = p.pauta_siguiente_id)))
        ), x AS (
         SELECT i.sit_workorder_id,
            i.nro_ot,
            i.tipo_id,
            i.equipo_id,
            i.pauta_id,
            i.pauta_siguiente_id,
            i.seqno,
            i.solicitud_id,
            i.nro_solicitud,
            i.sk_fecha_apertura,
            i.sk_fecha_cierre,
            i.fecha_hora_apertura,
            i.fecha_hora_cierre,
            i.flag_cierre_imputado,
            i.docstatus,
            i.horas_ciclo,
            i.odometro,
            i.horometro,
            i.prioridad,
            i.costo_repuestos,
            i.lineas_consumo,
            i.tipo_medicion,
            i.ot_siguiente,
            i.fecha_siguiente,
            i.odometro_siguiente,
            i.horometro_siguiente,
            i.intervalo,
            i.unidad,
                CASE i.unidad
                    WHEN 'KM'::text THEN (i.odometro_siguiente - i.odometro)
                    WHEN 'HORAS'::text THEN (i.horometro_siguiente - i.horometro)
                    WHEN 'DIAS'::text THEN ((EXTRACT(epoch FROM (i.fecha_siguiente - i.fecha_hora_apertura)) / (86400)::numeric))::double precision
                    ELSE NULL::double precision
                END AS uso_hasta_siguiente
           FROM i
        )
 SELECT sit_workorder_id,
    nro_ot,
    equipo_id,
    pauta_id,
    pauta_siguiente_id,
    fecha_hora_apertura,
    odometro,
    horometro,
    ot_siguiente,
    fecha_siguiente,
    intervalo,
    unidad,
    uso_hasta_siguiente,
    (uso_hasta_siguiente - intervalo) AS desvio,
        CASE
            WHEN (intervalo IS NULL) THEN 'sin intervalo'::text
            WHEN (ot_siguiente IS NULL) THEN 'pendiente'::text
            WHEN (uso_hasta_siguiente IS NULL) THEN 'sin lectura'::text
            WHEN (uso_hasta_siguiente <= (intervalo * (((1)::numeric + ((taller.par('tolerancia_preventiva_pct'::text))::numeric / (100)::numeric)))::double precision)) THEN 'a tiempo'::text
            ELSE 'atrasada'::text
        END AS estado
   FROM x;


--
-- Name: v_cobertura_preventiva; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_cobertura_preventiva AS
 WITH x AS (
         SELECT p.sit_workorder_id,
            p.nro_ot,
            p.equipo_id,
            p.pauta_id,
            p.pauta_siguiente_id,
            p.fecha_hora_apertura,
            p.odometro,
            p.horometro,
            p.ot_siguiente,
            p.fecha_siguiente,
            p.intervalo,
            p.unidad,
            p.uso_hasta_siguiente,
            p.desvio,
            p.estado,
                CASE
                    WHEN (p.intervalo IS NULL) THEN 'sin_intervalo'::text
                    WHEN (p.intervalo <= (0)::double precision) THEN 'intervalo_no_positivo'::text
                    WHEN ((p.unidad IS NULL) OR (p.unidad <> ALL (ARRAY['KM'::text, 'HORAS'::text, 'DIAS'::text]))) THEN 'unidad_no_admitida'::text
                    WHEN (p.ot_siguiente IS NULL) THEN 'sin_siguiente_orden'::text
                    WHEN (p.uso_hasta_siguiente IS NULL) THEN 'sin_lectura'::text
                    WHEN (p.uso_hasta_siguiente < (0)::double precision) THEN 'lectura_retrocede'::text
                    WHEN ((p.estado <> ALL (ARRAY['a tiempo'::text, 'atrasada'::text])) OR (p.estado IS NULL)) THEN 'estado_no_evaluable'::text
                    ELSE 'evaluable'::text
                END AS motivo_cobertura
           FROM taller.v_cumplimiento_preventivo p
        )
 SELECT sit_workorder_id,
    nro_ot,
    equipo_id,
    pauta_id,
    pauta_siguiente_id,
    fecha_hora_apertura,
    odometro,
    horometro,
    ot_siguiente,
    fecha_siguiente,
    intervalo,
    unidad,
    uso_hasta_siguiente,
    desvio,
    estado,
    motivo_cobertura,
    (motivo_cobertura = 'evaluable'::text) AS evaluable_cumplimiento,
        CASE
            WHEN (motivo_cobertura = 'evaluable'::text) THEN (estado = 'a tiempo'::text)
            ELSE NULL::boolean
        END AS cumple
   FROM x;


--
-- Name: v_cobertura_preventiva_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_cobertura_preventiva_mensual AS
 SELECT (date_trunc('month'::text, fecha_hora_apertura))::date AS mes_apertura,
    count(*) AS ordenes_preventivas,
    count(*) FILTER (WHERE evaluable_cumplimiento) AS evaluables,
    count(*) FILTER (WHERE (NOT evaluable_cumplimiento)) AS no_evaluables,
    count(*) FILTER (WHERE (motivo_cobertura = 'sin_intervalo'::text)) AS sin_intervalo,
    count(*) FILTER (WHERE (evaluable_cumplimiento AND cumple)) AS a_tiempo,
    count(*) FILTER (WHERE (evaluable_cumplimiento AND (NOT cumple))) AS atrasadas,
    ((100.0 * (count(*) FILTER (WHERE evaluable_cumplimiento))::numeric) / (NULLIF(count(*), 0))::numeric) AS cobertura_pct,
    ((100.0 * (count(*) FILTER (WHERE (evaluable_cumplimiento AND cumple)))::numeric) / (NULLIF(count(*) FILTER (WHERE evaluable_cumplimiento), 0))::numeric) AS cumplimiento_pct
   FROM taller.v_cobertura_preventiva
  GROUP BY ((date_trunc('month'::text, fecha_hora_apertura))::date);


--
-- Name: v_flota_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_flota_mensual AS
 WITH m AS (
         SELECT e.equipo_id,
            f.anio,
            f.mes,
            count(*) AS dias_flota,
            sum(e.en_taller) AS dias_taller,
            sum(
                CASE
                    WHEN (e.tipo_taller = 'preventiva'::text) THEN 1
                    ELSE 0
                END) AS dias_taller_preventiva,
            sum(
                CASE
                    WHEN (e.tipo_taller = 'correctiva'::text) THEN 1
                    ELSE 0
                END) AS dias_taller_correctiva,
            sum(e.en_taller_cierre_real) AS dias_taller_cierre_real,
            sum(e.dia_disponible) AS dias_disponibles,
            sum(e.con_viaje) AS dias_con_viaje,
            sum(e.dia_trabajado) AS dias_trabajados,
            sum(e.viaje_estimado) AS dias_viaje_estimado,
            sum(e.viajes_iniciados) AS viajes,
            sum(e.km_viajes) AS km_viajes,
            sum(e.ingreso_viajes) AS ingreso_viajes
           FROM (taller.fact_estado_diario_equipo e
             JOIN comun.dim_fecha f ON ((f.sk_fecha = e.sk_fecha)))
          GROUP BY e.equipo_id, f.anio, f.mes
        )
 SELECT m.equipo_id,
    m.anio,
    m.mes,
    m.dias_flota,
    m.dias_taller,
    m.dias_taller_preventiva,
    m.dias_taller_correctiva,
    m.dias_taller_cierre_real,
    m.dias_disponibles,
    m.dias_con_viaje,
    m.dias_trabajados,
    m.dias_viaje_estimado,
    m.viajes,
    m.km_viajes,
    m.ingreso_viajes,
    d.codigo,
    d.es_camion,
    d.es_propio,
    d.marca,
    d.modelo,
    (m.ingreso_viajes / (NULLIF(m.dias_trabajados, 0))::double precision) AS ingreso_por_dia_trabajado,
    ((m.dias_taller)::double precision * COALESCE((m.ingreso_viajes / (NULLIF(m.dias_trabajados, 0))::double precision), (sum(m.ingreso_viajes) OVER w / (NULLIF(sum(m.dias_trabajados) OVER w, (0)::numeric))::double precision))) AS costo_oportunidad,
    ((m.dias_taller)::numeric * (taller.par('ingreso_dia_referencia'::text))::numeric) AS costo_oportunidad_referencia
   FROM (m
     JOIN taller.dim_equipo d ON ((d.equipo_id = m.equipo_id)))
  WINDOW w AS (PARTITION BY m.anio, m.mes);


--
-- Name: v_consumo_km_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_consumo_km_mensual AS
 SELECT c.equipo_id,
    f.anio,
    f.mes,
    r.categoria,
    sum(c.cantidad) AS cantidad,
    sum(c.costo) AS costo,
    max(k.km_viajes) AS km_viajes_equipo_mes
   FROM (((taller.fact_consumo_repuesto c
     JOIN taller.dim_repuesto r ON ((r.producto_id = c.producto_id)))
     JOIN comun.dim_fecha f ON ((f.sk_fecha = c.sk_fecha)))
     LEFT JOIN taller.v_flota_mensual k ON (((k.equipo_id = c.equipo_id) AND (k.anio = f.anio) AND (k.mes = f.mes))))
  WHERE ((r.es_combustible = 'N'::text) AND (r.es_mantencion = 'S'::text) AND (c.equipo_id <> '-1'::integer))
  GROUP BY c.equipo_id, f.anio, f.mes, r.categoria;


--
-- Name: v_costo_mantencion_mensual; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_costo_mantencion_mensual AS
 WITH c AS (
         SELECT c_1.equipo_id,
            f.anio,
            f.mes,
            sum(
                CASE
                    WHEN ((c_1.via_imputacion = 'OT'::text) AND (t.tipo_mant = 'PREVENTIVA'::text)) THEN c_1.costo
                    ELSE (0)::double precision
                END) AS costo_ot_preventiva,
            sum(
                CASE
                    WHEN ((c_1.via_imputacion = 'OT'::text) AND (t.tipo_mant = 'CORRECTIVA'::text)) THEN c_1.costo
                    ELSE (0)::double precision
                END) AS costo_ot_correctiva,
            sum(
                CASE
                    WHEN ((c_1.via_imputacion = 'OT'::text) AND (t.tipo_mant = 'NO APLICA'::text)) THEN c_1.costo
                    ELSE (0)::double precision
                END) AS costo_ot_sanitizacion,
            sum(
                CASE
                    WHEN (c_1.via_imputacion = 'UN'::text) THEN c_1.costo
                    ELSE (0)::double precision
                END) AS costo_sin_ot,
            sum(c_1.costo) AS costo_mantencion_total
           FROM ((((taller.fact_consumo_repuesto c_1
             JOIN taller.dim_repuesto r ON ((r.producto_id = c_1.producto_id)))
             JOIN comun.dim_fecha f ON ((f.sk_fecha = c_1.sk_fecha)))
             LEFT JOIN taller.fact_orden_taller o ON ((o.sit_workorder_id = c_1.sit_workorder_id)))
             LEFT JOIN taller.dim_tipo_intervencion t ON ((t.tipo_id = o.tipo_id)))
          WHERE ((r.es_combustible = 'N'::text) AND (r.es_mantencion = 'S'::text) AND (c_1.equipo_id <> '-1'::integer))
          GROUP BY c_1.equipo_id, f.anio, f.mes
        ), k AS (
         SELECT v_flota_mensual.equipo_id,
            v_flota_mensual.anio,
            v_flota_mensual.mes,
            v_flota_mensual.km_viajes
           FROM taller.v_flota_mensual
        )
 SELECT COALESCE(c.equipo_id, k.equipo_id) AS equipo_id,
    COALESCE(c.anio, k.anio) AS anio,
    COALESCE(c.mes, k.mes) AS mes,
    d.codigo,
    d.es_camion,
    d.tipo_equipo,
    COALESCE(c.costo_ot_preventiva, (0)::double precision) AS costo_ot_preventiva,
    COALESCE(c.costo_ot_correctiva, (0)::double precision) AS costo_ot_correctiva,
    COALESCE(c.costo_ot_sanitizacion, (0)::double precision) AS costo_ot_sanitizacion,
    COALESCE(c.costo_sin_ot, (0)::double precision) AS costo_sin_ot,
    COALESCE(c.costo_mantencion_total, (0)::double precision) AS costo_mantencion_total,
    COALESCE(k.km_viajes, (0)::double precision) AS km_viajes
   FROM ((c
     FULL JOIN k ON (((k.equipo_id = c.equipo_id) AND (k.anio = c.anio) AND (k.mes = c.mes))))
     JOIN taller.dim_equipo d ON ((d.equipo_id = COALESCE(c.equipo_id, k.equipo_id))));


--
-- Name: v_ot_abiertas; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_ot_abiertas AS
 SELECT o.sit_workorder_id,
    o.nro_ot,
    t.tipo_documento,
    t.tipo_mant,
    t.area_taller,
    o.equipo_id,
    o.docstatus,
    o.fecha_hora_apertura,
    ((taller.par('fecha_corte_ot'::text))::date - (o.fecha_hora_apertura)::date) AS dias_abierta
   FROM (taller.fact_orden_taller o
     JOIN taller.dim_tipo_intervencion t ON ((t.tipo_id = o.tipo_id)))
  WHERE (o.docstatus <> ALL (ARRAY['CO'::text, 'CL'::text, 'VO'::text]));


--
-- Name: v_ot_mantencion; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_ot_mantencion AS
 SELECT o.sit_workorder_id,
    o.nro_ot,
    o.equipo_id,
    d.codigo,
    d.es_camion,
    d.tipo_equipo,
    t.tipo_mant,
    t.area_taller,
    t.tipo_medicion,
    o.docstatus,
    o.fecha_hora_apertura,
    o.fecha_hora_cierre,
    f.anio AS anio_cierre,
    f.mes AS mes_cierre,
    o.flag_cierre_imputado,
    (o.horas_ciclo / (24.0)::double precision) AS dias_ciclo,
        CASE
            WHEN (o.flag_cierre_imputado = 'N'::text) THEN (o.horas_ciclo / (24.0)::double precision)
            ELSE NULL::double precision
        END AS dias_ciclo_cierre_real,
    o.costo_repuestos
   FROM (((taller.fact_orden_taller o
     JOIN taller.dim_tipo_intervencion t ON ((t.tipo_id = o.tipo_id)))
     LEFT JOIN taller.dim_equipo d ON ((d.equipo_id = o.equipo_id)))
     LEFT JOIN comun.dim_fecha f ON ((f.sk_fecha = o.sk_fecha_cierre)))
  WHERE ((t.tipo_documento = 'OT'::text) AND (o.docstatus <> ALL (ARRAY['VO'::text, 'DR'::text])));


--
-- Name: v_reincidencia_correctiva; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_reincidencia_correctiva AS
 SELECT o.sit_workorder_id,
    o.nro_ot,
    o.equipo_id,
    t.area_taller,
    o.fecha_hora_apertura,
    lag(o.fecha_hora_cierre) OVER w AS cierre_correctiva_anterior,
    (EXTRACT(epoch FROM (o.fecha_hora_apertura - lag(o.fecha_hora_cierre) OVER w)) / (86400)::numeric) AS dias_desde_anterior
   FROM (taller.fact_orden_taller o
     JOIN taller.dim_tipo_intervencion t ON ((t.tipo_id = o.tipo_id)))
  WHERE ((t.tipo_documento = 'OT'::text) AND (t.tipo_mant = 'CORRECTIVA'::text) AND (o.docstatus <> ALL (ARRAY['VO'::text, 'DR'::text])))
  WINDOW w AS (PARTITION BY o.equipo_id ORDER BY o.fecha_hora_apertura);


--
-- Name: v_rendimiento_combustible; Type: VIEW; Schema: taller; Owner: -
--

CREATE VIEW taller.v_rendimiento_combustible AS
 WITH b AS (
         SELECT b_1.equipo_id,
            b_1.bloque,
            b_1.inicio,
            b_1.termino,
            (EXTRACT(year FROM b_1.termino))::integer AS anio,
            (EXTRACT(month FROM b_1.termino))::integer AS mes,
            b_1.litros,
            b_1.km_odometro,
            b_1.km_cargados,
            b_1.km_vacios,
            b_1.rendimiento_odo,
            b_1.calidad,
            ((b_1.calidad = 'ok'::text) AND ((b_1.rendimiento_odo >= ((taller.par('rendimiento_min'::text))::numeric)::double precision) AND (b_1.rendimiento_odo <= ((taller.par('rendimiento_max'::text))::numeric)::double precision))) AS es_valido
           FROM combustible.fact_bloque_combustible b_1
        ), med AS (
         SELECT d_1.modelo,
            percentile_cont((0.5)::double precision) WITHIN GROUP (ORDER BY b_1.rendimiento_odo) AS rendimiento_mediana_modelo
           FROM (b b_1
             JOIN taller.dim_equipo d_1 ON ((d_1.equipo_id = b_1.equipo_id)))
          WHERE b_1.es_valido
          GROUP BY d_1.modelo
        )
 SELECT b.equipo_id,
    b.bloque,
    b.inicio,
    b.termino,
    b.anio,
    b.mes,
    b.litros,
    b.km_odometro,
    b.km_cargados,
    b.km_vacios,
    b.rendimiento_odo,
    b.calidad,
    b.es_valido,
    d.codigo,
    d.modelo,
    d.rendimiento_ficha,
    med.rendimiento_mediana_modelo,
    r.l100_cargado,
    r.l100_vacio,
    (((b.km_cargados * r.l100_cargado) + (b.km_vacios * r.l100_vacio)) / (100.0)::double precision) AS litros_esperados_regresion,
    (b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, (0)::double precision)) AS litros_esperados_modelo,
    (b.km_odometro / NULLIF(d.rendimiento_ficha, (0)::double precision)) AS litros_esperados_ficha,
    COALESCE((((b.km_cargados * r.l100_cargado) + (b.km_vacios * r.l100_vacio)) / (100.0)::double precision), (b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, (0)::double precision))) AS litros_esperados,
        CASE
            WHEN (r.l100_cargado IS NOT NULL) THEN 'regresion'::text
            WHEN (med.rendimiento_mediana_modelo IS NOT NULL) THEN 'mediana_modelo'::text
            ELSE NULL::text
        END AS referencia,
    (b.litros - COALESCE((((b.km_cargados * r.l100_cargado) + (b.km_vacios * r.l100_vacio)) / (100.0)::double precision), (b.km_odometro / NULLIF(med.rendimiento_mediana_modelo, (0)::double precision)))) AS litros_diferencia
   FROM (((b
     LEFT JOIN taller.dim_equipo d ON ((d.equipo_id = b.equipo_id)))
     LEFT JOIN med ON ((med.modelo = d.modelo)))
     LEFT JOIN combustible.ref_regresion_camion r ON ((r.equipo_id = b.equipo_id)));


--
-- Name: ciudad_excel_exacto; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.ciudad_excel_exacto (
    ciudad text NOT NULL,
    clave text,
    indice_columna integer,
    fila_distancia integer,
    puede_ser_origen boolean,
    ciudad_peaje text,
    ciudad_peaje_origen text
);


--
-- Name: dim_parametro; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.dim_parametro (
    sk_parametro integer NOT NULL,
    codigo text,
    nombre text,
    categoria text,
    unidad text,
    origen_valor text,
    descripcion text,
    regla_calculo text,
    referencia_tarificador text
);


--
-- Name: equivalencia_lugar_tarificador; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.equivalencia_lugar_tarificador (
    clave_lugar text NOT NULL,
    nombre_erp text,
    localidad text,
    region text,
    ciudad_tarificador text,
    ciudad_peaje text,
    metodo text,
    estado text
);


--
-- Name: fact_cotizacion_supuesto; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_cotizacion_supuesto (
    nro_viaje bigint NOT NULL,
    escenario_id bigint NOT NULL,
    supuestos text
);


--
-- Name: fact_distancia_tarificador; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_distancia_tarificador (
    origen text NOT NULL,
    destino text NOT NULL,
    km double precision,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    fuente text,
    fuente_sha256 text
);


--
-- Name: fact_parametro_valor; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_parametro_valor (
    sk_parametro integer NOT NULL,
    ambito text NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    valor double precision,
    origen text NOT NULL,
    monto_base double precision,
    cantidad_inductor double precision,
    nota text,
    fecha_registro timestamp without time zone
);


--
-- Name: fact_peaje_tarificador; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_peaje_tarificador (
    origen text,
    destino text,
    origen_norm text NOT NULL,
    destino_norm text NOT NULL,
    vigente_desde date NOT NULL,
    vigente_hasta date,
    peaje double precision,
    nota text
);


--
-- Name: fact_precio_combustible_tarificador; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_precio_combustible_tarificador (
    fecha date NOT NULL,
    sk_fecha integer,
    costo_base double precision,
    costo_tct double precision,
    pct_base double precision,
    origen_pct text,
    recargo double precision,
    precio_tarificador double precision
);


--
-- Name: fact_precio_excel_dia; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.fact_precio_excel_dia (
    fecha date,
    fecha_documental_id bigint NOT NULL,
    base_neto double precision,
    base_ie double precision,
    base_sk_carta bigint,
    base_sk_precio bigint,
    base_hash_sha256 text,
    base_fecha_carta text,
    base_neto_deducido text,
    base_localidad_region text,
    base_origen_precio text,
    base_origen_entrada text,
    base_operacion double precision,
    base_recuperacion double precision,
    base_costo_excel double precision,
    tct_neto double precision,
    tct_ie double precision,
    tct_sk_carta bigint,
    tct_sk_precio bigint,
    tct_hash_sha256 text,
    tct_fecha_carta text,
    tct_neto_deducido text,
    tct_localidad_region text,
    tct_origen_precio text,
    tct_origen_entrada text,
    tct_operacion double precision,
    tct_recuperacion double precision,
    tct_costo_excel double precision,
    pct_base double precision,
    pct_tct double precision,
    colchon double precision,
    precio_excel double precision
);


--
-- Name: km_tramo_tarificador; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.km_tramo_tarificador (
    sit_stretch_id double precision NOT NULL,
    texto_tramo text,
    n_puntos double precision,
    km_erp double precision,
    c9 text,
    c10 text,
    c11 text,
    c12 text,
    c13 text,
    km_pierna_1 double precision,
    km_pierna_2 double precision,
    km_pierna_3 double precision,
    destino_4_sin_km boolean,
    intermedios double precision,
    km_ida double precision,
    km_ida_vuelta double precision,
    estado text,
    motivo text,
    vigente_desde date NOT NULL,
    vigente_hasta date
);


--
-- Name: peaje_excel_exacto; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.peaje_excel_exacto (
    fila integer NOT NULL,
    origen text,
    destino text NOT NULL,
    indice_destino integer,
    peaje double precision,
    estado text
);


--
-- Name: v_parametro_vigente; Type: VIEW; Schema: tarificador; Owner: -
--

CREATE VIEW tarificador.v_parametro_vigente AS
 WITH pedidos AS (
         SELECT DISTINCT fact_parametro_valor.sk_parametro,
            fact_parametro_valor.ambito
           FROM tarificador.fact_parametro_valor
          WHERE ((CURRENT_DATE >= fact_parametro_valor.vigente_desde) AND (CURRENT_DATE < fact_parametro_valor.vigente_hasta))
        )
 SELECT d.codigo,
    d.nombre,
    d.categoria,
    d.unidad,
    p.ambito,
    r.valor,
    r.origen,
    r.vigente_desde,
    r.nota
   FROM ((pedidos p
     JOIN tarificador.dim_parametro d USING (sk_parametro))
     CROSS JOIN LATERAL tarificador.resolver_parametro(d.codigo, CURRENT_DATE, p.ambito) r(ambito_efectivo, valor, origen, vigente_desde, vigente_hasta, nota));


--
-- Name: v_parametro_vigente_candidatos; Type: VIEW; Schema: tarificador; Owner: -
--

CREATE VIEW tarificador.v_parametro_vigente_candidatos AS
 SELECT d.codigo,
    d.nombre,
    v.ambito,
    v.valor,
    v.origen,
    v.vigente_desde,
    v.vigente_hasta,
    v.nota
   FROM (tarificador.fact_parametro_valor v
     JOIN tarificador.dim_parametro d USING (sk_parametro))
  WHERE ((CURRENT_DATE >= v.vigente_desde) AND (CURRENT_DATE < v.vigente_hasta));


--
-- Name: version_excel_exacto; Type: TABLE; Schema: tarificador; Owner: -
--

CREATE TABLE tarificador.version_excel_exacto (
    version text NOT NULL,
    fuente_sha256 text,
    vigente_desde date,
    vigente_hasta date,
    parametros jsonb,
    manifiesto jsonb
);


--
-- Name: v_viaje_descriptivo; Type: VIEW; Schema: ventas; Owner: -
--

CREATE VIEW ventas.v_viaje_descriptivo AS
 SELECT v.nro_viaje,
    v.nro_orden,
    v.sk_fecha,
    v.cliente_id,
    v.producto_id,
    v.sit_stretch_id,
    v.equipo_id,
    v.rampla_id,
    v.operador_grupo,
    v.tipo_operador,
    v.transportista_id,
    v.conductor_id,
    v.conductor_interno,
    v.es_retorno,
    v.es_tractoreo,
    v.estado_viaje,
    v.cantidad,
    v.ingreso,
    v.valor_orden_venta,
    v.precio_cotizado,
    v.valor_facturado,
    v.facturas,
    v.comision_conductor,
    v.pago_tercero,
    v.pct_pago_tercero,
    v.comision_conductor_observada,
    v.pago_tercero_observado,
    c.nombre_completo AS conductor_nombre,
        CASE
            WHEN (v.conductor_id IS NULL) THEN 'sin_id'::text
            WHEN (c.conductor_id IS NULL) THEN 'sin_catalogo'::text
            ELSE 'encontrado'::text
        END AS estado_conductor,
    cl.razon_social AS cliente_nombre,
    t.razon_social AS transportista_nombre,
    e.nombre_fuente AS tracto_nombre,
    r.nombre_fuente AS rampla_nombre,
    p.producto AS producto_nombre
   FROM ((((((ventas.fact_viaje_venta v
     LEFT JOIN dim_comun.dim_conductor c ON ((c.conductor_id = v.conductor_id)))
     LEFT JOIN dim_comun.dim_socio_negocio cl ON ((cl.socio_id = v.cliente_id)))
     LEFT JOIN dim_comun.dim_socio_negocio t ON ((t.socio_id = v.transportista_id)))
     LEFT JOIN dim_comun.dim_equipo e ON ((e.equipo_id = v.equipo_id)))
     LEFT JOIN dim_comun.dim_equipo r ON ((r.equipo_id = v.rampla_id)))
     LEFT JOIN dim_comun.dim_producto_viaje p ON ((p.producto_id = v.producto_id)));


--
-- Name: dim_ciclo_comercial dim_ciclo_comercial_pkey; Type: CONSTRAINT; Schema: ciclos; Owner: -
--

ALTER TABLE ONLY ciclos.dim_ciclo_comercial
    ADD CONSTRAINT dim_ciclo_comercial_pkey PRIMARY KEY (ciclo_comercial);


--
-- Name: dim_tramo_base dim_tramo_base_pkey; Type: CONSTRAINT; Schema: ciclos; Owner: -
--

ALTER TABLE ONLY ciclos.dim_tramo_base
    ADD CONSTRAINT dim_tramo_base_pkey PRIMARY KEY (sit_stretch_id);


--
-- Name: fact_viaje_ciclo fact_viaje_ciclo_pkey; Type: CONSTRAINT; Schema: ciclos; Owner: -
--

ALTER TABLE ONLY ciclos.fact_viaje_ciclo
    ADD CONSTRAINT fact_viaje_ciclo_pkey PRIMARY KEY (id_viaje);


--
-- Name: dim_generacion_camion dim_generacion_camion_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.dim_generacion_camion
    ADD CONSTRAINT dim_generacion_camion_pkey PRIMARY KEY (equipo_id);


--
-- Name: fact_bloque_certificacion fact_bloque_certificacion_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_bloque_certificacion
    ADD CONSTRAINT fact_bloque_certificacion_pkey PRIMARY KEY (equipo_id, bloque);


--
-- Name: fact_bloque_combustible fact_bloque_combustible_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_bloque_combustible
    ADD CONSTRAINT fact_bloque_combustible_pkey PRIMARY KEY (equipo_id, bloque);


--
-- Name: fact_carga_combustible fact_carga_combustible_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_carga_combustible
    ADD CONSTRAINT fact_carga_combustible_pkey PRIMARY KEY (id_carga);


--
-- Name: fact_rendimiento_camion_mes fact_rendimiento_camion_mes_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_rendimiento_camion_mes
    ADD CONSTRAINT fact_rendimiento_camion_mes_pkey PRIMARY KEY (equipo_id, mes);


--
-- Name: fact_viaje_combustible fact_viaje_combustible_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_viaje_combustible
    ADD CONSTRAINT fact_viaje_combustible_pkey PRIMARY KEY (sk_viaje_comb);


--
-- Name: fact_viaje_combustible_tarificador fact_viaje_combustible_tarificador_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_viaje_combustible_tarificador
    ADD CONSTRAINT fact_viaje_combustible_tarificador_pkey PRIMARY KEY (nro_viaje);


--
-- Name: param_ficha_consumo param_ficha_consumo_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.param_ficha_consumo
    ADD CONSTRAINT param_ficha_consumo_pkey PRIMARY KEY (equipo_id, mes);


--
-- Name: parametro_certificacion parametro_certificacion_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.parametro_certificacion
    ADD CONSTRAINT parametro_certificacion_pkey PRIMARY KEY (parametro, vigente_desde);


--
-- Name: parametro_rendimiento_tarificador parametro_rendimiento_tarificador_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.parametro_rendimiento_tarificador
    ADD CONSTRAINT parametro_rendimiento_tarificador_pkey PRIMARY KEY (parametro, vigente_desde);


--
-- Name: ref_regresion_camion ref_regresion_camion_pkey; Type: CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.ref_regresion_camion
    ADD CONSTRAINT ref_regresion_camion_pkey PRIMARY KEY (equipo_id);


--
-- Name: dim_fecha dim_fecha_fecha_key; Type: CONSTRAINT; Schema: comun; Owner: -
--

ALTER TABLE ONLY comun.dim_fecha
    ADD CONSTRAINT dim_fecha_fecha_key UNIQUE (fecha);


--
-- Name: dim_fecha dim_fecha_pkey; Type: CONSTRAINT; Schema: comun; Owner: -
--

ALTER TABLE ONLY comun.dim_fecha
    ADD CONSTRAINT dim_fecha_pkey PRIMARY KEY (sk_fecha);


--
-- Name: registro_carga registro_carga_pkey; Type: CONSTRAINT; Schema: comun; Owner: -
--

ALTER TABLE ONLY comun.registro_carga
    ADD CONSTRAINT registro_carga_pkey PRIMARY KEY (id);


--
-- Name: registro_ejecucion registro_ejecucion_pkey; Type: CONSTRAINT; Schema: comun; Owner: -
--

ALTER TABLE ONLY comun.registro_ejecucion
    ADD CONSTRAINT registro_ejecucion_pkey PRIMARY KEY (id);


--
-- Name: dim_centro_costo dim_centro_costo_pkey; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.dim_centro_costo
    ADD CONSTRAINT dim_centro_costo_pkey PRIMARY KEY (c_activity_id);


--
-- Name: dim_cuenta_contable dim_cuenta_contable_codigo_key; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.dim_cuenta_contable
    ADD CONSTRAINT dim_cuenta_contable_codigo_key UNIQUE (codigo);


--
-- Name: dim_cuenta_contable dim_cuenta_contable_pkey; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.dim_cuenta_contable
    ADD CONSTRAINT dim_cuenta_contable_pkey PRIMARY KEY (sk_cuenta);


--
-- Name: fact_capacidad_bolsa fact_capacidad_bolsa_pkey; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.fact_capacidad_bolsa
    ADD CONSTRAINT fact_capacidad_bolsa_pkey PRIMARY KEY (anio, bolsa);


--
-- Name: fact_viaje_costo_indirecto fact_viaje_costo_indirecto_pkey; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.fact_viaje_costo_indirecto
    ADD CONSTRAINT fact_viaje_costo_indirecto_pkey PRIMARY KEY (nro_viaje, metodo);


--
-- Name: parametro_costo parametro_costo_pkey; Type: CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.parametro_costo
    ADD CONSTRAINT parametro_costo_pkey PRIMARY KEY (parametro, metodo, ambito, vigente_desde);


--
-- Name: dim_conductor dim_conductor_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.dim_conductor
    ADD CONSTRAINT dim_conductor_pkey PRIMARY KEY (conductor_id);


--
-- Name: dim_equipo dim_equipo_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.dim_equipo
    ADD CONSTRAINT dim_equipo_pkey PRIMARY KEY (equipo_id);


--
-- Name: dim_producto_viaje dim_producto_viaje_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.dim_producto_viaje
    ADD CONSTRAINT dim_producto_viaje_pkey PRIMARY KEY (producto_id);


--
-- Name: dim_socio_negocio dim_socio_negocio_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.dim_socio_negocio
    ADD CONSTRAINT dim_socio_negocio_pkey PRIMARY KEY (socio_id);


--
-- Name: dim_tramo_comercial dim_tramo_comercial_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.dim_tramo_comercial
    ADD CONSTRAINT dim_tramo_comercial_pkey PRIMARY KEY (tramo_id);


--
-- Name: roles_socio roles_socio_pkey; Type: CONSTRAINT; Schema: dim_comun; Owner: -
--

ALTER TABLE ONLY dim_comun.roles_socio
    ADD CONSTRAINT roles_socio_pkey PRIMARY KEY (socio_id, rol);


--
-- Name: dim_componente_costo dim_componente_costo_pkey; Type: CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.dim_componente_costo
    ADD CONSTRAINT dim_componente_costo_pkey PRIMARY KEY (sk_componente);


--
-- Name: dim_escenario dim_escenario_pkey; Type: CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.dim_escenario
    ADD CONSTRAINT dim_escenario_pkey PRIMARY KEY (sk_escenario);


--
-- Name: fact_costo_viaje fact_costo_viaje_pkey; Type: CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_viaje
    ADD CONSTRAINT fact_costo_viaje_pkey PRIMARY KEY (nro_viaje, sk_escenario, sk_componente);


--
-- Name: fact_costo_virtual fact_costo_virtual_pkey; Type: CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_virtual
    ADD CONSTRAINT fact_costo_virtual_pkey PRIMARY KEY (id_viaje, sk_escenario, sk_componente);


--
-- Name: fact_cotizacion_viaje fact_cotizacion_viaje_pkey; Type: CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_cotizacion_viaje
    ADD CONSTRAINT fact_cotizacion_viaje_pkey PRIMARY KEY (nro_viaje);


--
-- Name: dim_condicion_tarifa dim_condicion_tarifa_categoria_vehicular_horario_modalidad_key; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_condicion_tarifa
    ADD CONSTRAINT dim_condicion_tarifa_categoria_vehicular_horario_modalidad_key UNIQUE (categoria_vehicular, horario, modalidad);


--
-- Name: dim_condicion_tarifa dim_condicion_tarifa_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_condicion_tarifa
    ADD CONSTRAINT dim_condicion_tarifa_pkey PRIMARY KEY (sk_condicion);


--
-- Name: dim_documento_tarifa dim_documento_tarifa_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_documento_tarifa
    ADD CONSTRAINT dim_documento_tarifa_pkey PRIMARY KEY (sk_documento);


--
-- Name: dim_documento_tarifa dim_documento_tarifa_url_key; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_documento_tarifa
    ADD CONSTRAINT dim_documento_tarifa_url_key UNIQUE (url);


--
-- Name: dim_lugar dim_lugar_clave_lugar_key; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_lugar
    ADD CONSTRAINT dim_lugar_clave_lugar_key UNIQUE (clave_lugar);


--
-- Name: dim_lugar dim_lugar_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_lugar
    ADD CONSTRAINT dim_lugar_pkey PRIMARY KEY (sk_lugar);


--
-- Name: dim_plaza_peaje dim_plaza_peaje_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_plaza_peaje
    ADD CONSTRAINT dim_plaza_peaje_pkey PRIMARY KEY (sk_plaza);


--
-- Name: dim_segmento_ruta dim_segmento_ruta_desde_clave_hasta_clave_key; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_segmento_ruta
    ADD CONSTRAINT dim_segmento_ruta_desde_clave_hasta_clave_key UNIQUE (desde_clave, hasta_clave);


--
-- Name: dim_segmento_ruta dim_segmento_ruta_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_segmento_ruta
    ADD CONSTRAINT dim_segmento_ruta_pkey PRIMARY KEY (sk_segmento);


--
-- Name: dim_tramo dim_tramo_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_tramo
    ADD CONSTRAINT dim_tramo_pkey PRIMARY KEY (sk_tramo);


--
-- Name: fact_peaje_referencial_plaza fact_peaje_referencial_plaza_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_peaje_referencial_plaza
    ADD CONSTRAINT fact_peaje_referencial_plaza_pkey PRIMARY KEY (sk_tramo, vigente_desde, orden_segmento, orden_en_segmento);


--
-- Name: fact_peaje_referencial_tramo fact_peaje_referencial_tramo_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_peaje_referencial_tramo
    ADD CONSTRAINT fact_peaje_referencial_tramo_pkey PRIMARY KEY (sk_tramo, vigente_desde);


--
-- Name: fact_tarifa_peaje fact_tarifa_peaje_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_tarifa_peaje
    ADD CONSTRAINT fact_tarifa_peaje_pkey PRIMARY KEY (sk_tarifa);


--
-- Name: fact_viaje_peaje fact_viaje_peaje_nro_viaje_key; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_viaje_peaje
    ADD CONSTRAINT fact_viaje_peaje_nro_viaje_key UNIQUE (nro_viaje);


--
-- Name: fact_viaje_peaje fact_viaje_peaje_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_viaje_peaje
    ADD CONSTRAINT fact_viaje_peaje_pkey PRIMARY KEY (sk_viaje);


--
-- Name: parametro_peaje parametro_peaje_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.parametro_peaje
    ADD CONSTRAINT parametro_peaje_pkey PRIMARY KEY (parametro);


--
-- Name: puente_segmento_plaza_deteccion puente_segmento_plaza_deteccion_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_segmento_plaza_deteccion
    ADD CONSTRAINT puente_segmento_plaza_deteccion_pkey PRIMARY KEY (id_fila);


--
-- Name: puente_segmento_plaza puente_segmento_plaza_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_segmento_plaza
    ADD CONSTRAINT puente_segmento_plaza_pkey PRIMARY KEY (id_fila);


--
-- Name: puente_tramo_segmento puente_tramo_segmento_pkey; Type: CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_tramo_segmento
    ADD CONSTRAINT puente_tramo_segmento_pkey PRIMARY KEY (sk_tramo, orden);


--
-- Name: carta_archivo carta_archivo_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.carta_archivo
    ADD CONSTRAINT carta_archivo_pkey PRIMARY KEY (archivo);


--
-- Name: dim_carta_precio dim_carta_precio_id_carta_key; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.dim_carta_precio
    ADD CONSTRAINT dim_carta_precio_id_carta_key UNIQUE (id_carta);


--
-- Name: dim_carta_precio dim_carta_precio_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.dim_carta_precio
    ADD CONSTRAINT dim_carta_precio_pkey PRIMARY KEY (sk_carta);


--
-- Name: dim_fuente_precio dim_fuente_precio_fuente_key; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.dim_fuente_precio
    ADD CONSTRAINT dim_fuente_precio_fuente_key UNIQUE (fuente);


--
-- Name: dim_fuente_precio dim_fuente_precio_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.dim_fuente_precio
    ADD CONSTRAINT dim_fuente_precio_pkey PRIMARY KEY (sk_fuente);


--
-- Name: fact_costo_combustible_vigente fact_costo_combustible_vigente_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_costo_combustible_vigente
    ADD CONSTRAINT fact_costo_combustible_vigente_pkey PRIMARY KEY (sk_fuente, vigente_desde);


--
-- Name: fact_precio_combustible fact_precio_combustible_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_precio_combustible
    ADD CONSTRAINT fact_precio_combustible_pkey PRIMARY KEY (sk_precio);


--
-- Name: parametro_precio parametro_precio_pkey; Type: CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.parametro_precio
    ADD CONSTRAINT parametro_precio_pkey PRIMARY KEY (clave, vigente_desde);


--
-- Name: dim_pauta dim_pauta_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.dim_pauta
    ADD CONSTRAINT dim_pauta_pkey PRIMARY KEY (pauta_id);


--
-- Name: dim_repuesto dim_repuesto_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.dim_repuesto
    ADD CONSTRAINT dim_repuesto_pkey PRIMARY KEY (producto_id);


--
-- Name: dim_tipo_intervencion dim_tipo_intervencion_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.dim_tipo_intervencion
    ADD CONSTRAINT dim_tipo_intervencion_pkey PRIMARY KEY (tipo_id);


--
-- Name: fact_consumo_repuesto fact_consumo_repuesto_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.fact_consumo_repuesto
    ADD CONSTRAINT fact_consumo_repuesto_pkey PRIMARY KEY (consumo_id);


--
-- Name: fact_estado_diario_equipo fact_estado_diario_equipo_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.fact_estado_diario_equipo
    ADD CONSTRAINT fact_estado_diario_equipo_pkey PRIMARY KEY (equipo_id, sk_fecha);


--
-- Name: fact_orden_taller fact_orden_taller_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.fact_orden_taller
    ADD CONSTRAINT fact_orden_taller_pkey PRIMARY KEY (sit_workorder_id);


--
-- Name: intervalo_pauta_equipo intervalo_pauta_equipo_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.intervalo_pauta_equipo
    ADD CONSTRAINT intervalo_pauta_equipo_pkey PRIMARY KEY (equipo_id, pauta_id);


--
-- Name: parametro_taller parametro_taller_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.parametro_taller
    ADD CONSTRAINT parametro_taller_pkey PRIMARY KEY (parametro);


--
-- Name: ref_equipo_taller ref_equipo_taller_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.ref_equipo_taller
    ADD CONSTRAINT ref_equipo_taller_pkey PRIMARY KEY (equipo_id);


--
-- Name: resumen_actividad_equipo resumen_actividad_equipo_pkey; Type: CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.resumen_actividad_equipo
    ADD CONSTRAINT resumen_actividad_equipo_pkey PRIMARY KEY (equipo_id);


--
-- Name: ciudad_excel_exacto ciudad_excel_exacto_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.ciudad_excel_exacto
    ADD CONSTRAINT ciudad_excel_exacto_pkey PRIMARY KEY (ciudad);


--
-- Name: dim_parametro dim_parametro_codigo_key; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.dim_parametro
    ADD CONSTRAINT dim_parametro_codigo_key UNIQUE (codigo);


--
-- Name: dim_parametro dim_parametro_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.dim_parametro
    ADD CONSTRAINT dim_parametro_pkey PRIMARY KEY (sk_parametro);


--
-- Name: equivalencia_lugar_tarificador equivalencia_lugar_tarificador_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.equivalencia_lugar_tarificador
    ADD CONSTRAINT equivalencia_lugar_tarificador_pkey PRIMARY KEY (clave_lugar);


--
-- Name: fact_cotizacion_servicio fact_cotizacion_servicio_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_cotizacion_servicio
    ADD CONSTRAINT fact_cotizacion_servicio_pkey PRIMARY KEY (nro_viaje, escenario_id);


--
-- Name: fact_cotizacion_supuesto fact_cotizacion_supuesto_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_cotizacion_supuesto
    ADD CONSTRAINT fact_cotizacion_supuesto_pkey PRIMARY KEY (nro_viaje, escenario_id);


--
-- Name: fact_distancia_tarificador fact_distancia_tarificador_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_distancia_tarificador
    ADD CONSTRAINT fact_distancia_tarificador_pkey PRIMARY KEY (origen, destino, vigente_desde);


--
-- Name: fact_parametro_valor fact_parametro_valor_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_parametro_valor
    ADD CONSTRAINT fact_parametro_valor_pkey PRIMARY KEY (sk_parametro, ambito, vigente_desde, origen);


--
-- Name: fact_peaje_tarificador fact_peaje_tarificador_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_peaje_tarificador
    ADD CONSTRAINT fact_peaje_tarificador_pkey PRIMARY KEY (origen_norm, destino_norm, vigente_desde);


--
-- Name: fact_precio_combustible_tarificador fact_precio_combustible_tarificador_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_precio_combustible_tarificador
    ADD CONSTRAINT fact_precio_combustible_tarificador_pkey PRIMARY KEY (fecha);


--
-- Name: fact_precio_excel_dia fact_precio_excel_dia_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_precio_excel_dia
    ADD CONSTRAINT fact_precio_excel_dia_pkey PRIMARY KEY (fecha_documental_id);


--
-- Name: km_tramo_tarificador km_tramo_tarificador_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.km_tramo_tarificador
    ADD CONSTRAINT km_tramo_tarificador_pkey PRIMARY KEY (sit_stretch_id, vigente_desde);


--
-- Name: peaje_excel_exacto peaje_excel_exacto_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.peaje_excel_exacto
    ADD CONSTRAINT peaje_excel_exacto_pkey PRIMARY KEY (fila, destino);


--
-- Name: version_excel_exacto version_excel_exacto_pkey; Type: CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.version_excel_exacto
    ADD CONSTRAINT version_excel_exacto_pkey PRIMARY KEY (version);


--
-- Name: fact_viaje_venta fact_viaje_venta_pkey; Type: CONSTRAINT; Schema: ventas; Owner: -
--

ALTER TABLE ONLY ventas.fact_viaje_venta
    ADD CONSTRAINT fact_viaje_venta_pkey PRIMARY KEY (nro_viaje);


--
-- Name: ix_vc_cc; Type: INDEX; Schema: ciclos; Owner: -
--

CREATE INDEX ix_vc_cc ON ciclos.fact_viaje_ciclo USING btree (ciclo_comercial);


--
-- Name: ix_vc_nro; Type: INDEX; Schema: ciclos; Owner: -
--

CREATE INDEX ix_vc_nro ON ciclos.fact_viaje_ciclo USING btree (nro_viaje);


--
-- Name: ix_carga_bloque; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_carga_bloque ON combustible.fact_carga_combustible USING btree (equipo_id, bloque);


--
-- Name: ix_certificado_equipo_cierre; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_certificado_equipo_cierre ON combustible.fact_bloque_certificacion USING btree (equipo_id, t_fin, bloque) WHERE certificado;


--
-- Name: ix_comb_tarif_fecha; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_comb_tarif_fecha ON combustible.fact_viaje_combustible_tarificador USING btree (fecha);


--
-- Name: ix_pvb_bloque; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_pvb_bloque ON combustible.puente_viaje_bloque USING btree (equipo_id, bloque);


--
-- Name: ix_pvb_viaje; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_pvb_viaje ON combustible.puente_viaje_bloque USING btree (id_viaje_fuente);


--
-- Name: ix_viaje_comb_calc; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_viaje_comb_calc ON combustible.fact_viaje_combustible USING btree (id_calculo);


--
-- Name: ix_viaje_comb_nro; Type: INDEX; Schema: combustible; Owner: -
--

CREATE INDEX ix_viaje_comb_nro ON combustible.fact_viaje_combustible USING btree (nro_viaje);


--
-- Name: ix_contab_cuenta; Type: INDEX; Schema: costos; Owner: -
--

CREATE INDEX ix_contab_cuenta ON costos.fact_contabilidad_mensual USING btree (sk_cuenta, anio);


--
-- Name: ix_costo_viaje_fecha; Type: INDEX; Schema: escenarios; Owner: -
--

CREATE INDEX ix_costo_viaje_fecha ON escenarios.fact_costo_viaje USING btree (sk_fecha);


--
-- Name: ix_costo_virtual_destino; Type: INDEX; Schema: escenarios; Owner: -
--

CREATE INDEX ix_costo_virtual_destino ON escenarios.fact_costo_virtual USING btree (nro_viaje_destino);


--
-- Name: ix_puente_seg; Type: INDEX; Schema: peajes; Owner: -
--

CREATE INDEX ix_puente_seg ON peajes.puente_segmento_plaza USING btree (sk_segmento);


--
-- Name: ix_tarifa_plaza; Type: INDEX; Schema: peajes; Owner: -
--

CREATE INDEX ix_tarifa_plaza ON peajes.fact_tarifa_peaje USING btree (id_plaza, vigente_desde);


--
-- Name: ix_tramo_stretch; Type: INDEX; Schema: peajes; Owner: -
--

CREATE INDEX ix_tramo_stretch ON peajes.dim_tramo USING btree (sit_stretch_id);


--
-- Name: ix_consumo_equipo; Type: INDEX; Schema: taller; Owner: -
--

CREATE INDEX ix_consumo_equipo ON taller.fact_consumo_repuesto USING btree (equipo_id, sk_fecha);


--
-- Name: ix_consumo_ot; Type: INDEX; Schema: taller; Owner: -
--

CREATE INDEX ix_consumo_ot ON taller.fact_consumo_repuesto USING btree (sit_workorder_id);


--
-- Name: ix_estado_fecha; Type: INDEX; Schema: taller; Owner: -
--

CREATE INDEX ix_estado_fecha ON taller.fact_estado_diario_equipo USING btree (sk_fecha);


--
-- Name: ix_ot_equipo; Type: INDEX; Schema: taller; Owner: -
--

CREATE INDEX ix_ot_equipo ON taller.fact_orden_taller USING btree (equipo_id, fecha_hora_apertura);


--
-- Name: ix_venta_cliente; Type: INDEX; Schema: ventas; Owner: -
--

CREATE INDEX ix_venta_cliente ON ventas.fact_viaje_venta USING btree (cliente_id);


--
-- Name: ix_venta_fecha; Type: INDEX; Schema: ventas; Owner: -
--

CREATE INDEX ix_venta_fecha ON ventas.fact_viaje_venta USING btree (sk_fecha);


--
-- Name: fact_carga_combustible fact_carga_combustible_sk_fecha_fkey; Type: FK CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_carga_combustible
    ADD CONSTRAINT fact_carga_combustible_sk_fecha_fkey FOREIGN KEY (sk_fecha) REFERENCES comun.dim_fecha(sk_fecha);


--
-- Name: fact_viaje_combustible fact_viaje_combustible_sk_fecha_inicio_fkey; Type: FK CONSTRAINT; Schema: combustible; Owner: -
--

ALTER TABLE ONLY combustible.fact_viaje_combustible
    ADD CONSTRAINT fact_viaje_combustible_sk_fecha_inicio_fkey FOREIGN KEY (sk_fecha_inicio) REFERENCES comun.dim_fecha(sk_fecha);


--
-- Name: fact_contabilidad_mensual fact_contabilidad_mensual_sk_cuenta_fkey; Type: FK CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.fact_contabilidad_mensual
    ADD CONSTRAINT fact_contabilidad_mensual_sk_cuenta_fkey FOREIGN KEY (sk_cuenta) REFERENCES costos.dim_cuenta_contable(sk_cuenta);


--
-- Name: fact_contabilidad_mensual fact_contabilidad_mensual_sk_fecha_mes_fkey; Type: FK CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.fact_contabilidad_mensual
    ADD CONSTRAINT fact_contabilidad_mensual_sk_fecha_mes_fkey FOREIGN KEY (sk_fecha_mes) REFERENCES comun.dim_fecha(sk_fecha);


--
-- Name: fact_viaje_costo_indirecto fact_viaje_costo_indirecto_sk_fecha_inicio_fkey; Type: FK CONSTRAINT; Schema: costos; Owner: -
--

ALTER TABLE ONLY costos.fact_viaje_costo_indirecto
    ADD CONSTRAINT fact_viaje_costo_indirecto_sk_fecha_inicio_fkey FOREIGN KEY (sk_fecha_inicio) REFERENCES comun.dim_fecha(sk_fecha);


--
-- Name: fact_costo_viaje fact_costo_viaje_sk_componente_fkey; Type: FK CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_viaje
    ADD CONSTRAINT fact_costo_viaje_sk_componente_fkey FOREIGN KEY (sk_componente) REFERENCES escenarios.dim_componente_costo(sk_componente);


--
-- Name: fact_costo_viaje fact_costo_viaje_sk_escenario_fkey; Type: FK CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_viaje
    ADD CONSTRAINT fact_costo_viaje_sk_escenario_fkey FOREIGN KEY (sk_escenario) REFERENCES escenarios.dim_escenario(sk_escenario);


--
-- Name: fact_costo_virtual fact_costo_virtual_sk_componente_fkey; Type: FK CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_virtual
    ADD CONSTRAINT fact_costo_virtual_sk_componente_fkey FOREIGN KEY (sk_componente) REFERENCES escenarios.dim_componente_costo(sk_componente);


--
-- Name: fact_costo_virtual fact_costo_virtual_sk_escenario_fkey; Type: FK CONSTRAINT; Schema: escenarios; Owner: -
--

ALTER TABLE ONLY escenarios.fact_costo_virtual
    ADD CONSTRAINT fact_costo_virtual_sk_escenario_fkey FOREIGN KEY (sk_escenario) REFERENCES escenarios.dim_escenario(sk_escenario);


--
-- Name: dim_segmento_ruta dim_segmento_ruta_sk_lugar_desde_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_segmento_ruta
    ADD CONSTRAINT dim_segmento_ruta_sk_lugar_desde_fkey FOREIGN KEY (sk_lugar_desde) REFERENCES peajes.dim_lugar(sk_lugar);


--
-- Name: dim_segmento_ruta dim_segmento_ruta_sk_lugar_hasta_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_segmento_ruta
    ADD CONSTRAINT dim_segmento_ruta_sk_lugar_hasta_fkey FOREIGN KEY (sk_lugar_hasta) REFERENCES peajes.dim_lugar(sk_lugar);


--
-- Name: dim_tramo dim_tramo_sk_lugar_destino_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_tramo
    ADD CONSTRAINT dim_tramo_sk_lugar_destino_fkey FOREIGN KEY (sk_lugar_destino) REFERENCES peajes.dim_lugar(sk_lugar);


--
-- Name: dim_tramo dim_tramo_sk_lugar_origen_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.dim_tramo
    ADD CONSTRAINT dim_tramo_sk_lugar_origen_fkey FOREIGN KEY (sk_lugar_origen) REFERENCES peajes.dim_lugar(sk_lugar);


--
-- Name: fact_peaje_referencial_plaza fact_peaje_referencial_plaza_sk_segmento_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_peaje_referencial_plaza
    ADD CONSTRAINT fact_peaje_referencial_plaza_sk_segmento_fkey FOREIGN KEY (sk_segmento) REFERENCES peajes.dim_segmento_ruta(sk_segmento);


--
-- Name: fact_peaje_referencial_plaza fact_peaje_referencial_plaza_sk_tramo_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_peaje_referencial_plaza
    ADD CONSTRAINT fact_peaje_referencial_plaza_sk_tramo_fkey FOREIGN KEY (sk_tramo) REFERENCES peajes.dim_tramo(sk_tramo);


--
-- Name: fact_peaje_referencial_tramo fact_peaje_referencial_tramo_sk_tramo_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_peaje_referencial_tramo
    ADD CONSTRAINT fact_peaje_referencial_tramo_sk_tramo_fkey FOREIGN KEY (sk_tramo) REFERENCES peajes.dim_tramo(sk_tramo);


--
-- Name: fact_tarifa_peaje fact_tarifa_peaje_sk_condicion_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_tarifa_peaje
    ADD CONSTRAINT fact_tarifa_peaje_sk_condicion_fkey FOREIGN KEY (sk_condicion) REFERENCES peajes.dim_condicion_tarifa(sk_condicion);


--
-- Name: fact_tarifa_peaje fact_tarifa_peaje_sk_documento_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_tarifa_peaje
    ADD CONSTRAINT fact_tarifa_peaje_sk_documento_fkey FOREIGN KEY (sk_documento) REFERENCES peajes.dim_documento_tarifa(sk_documento);


--
-- Name: fact_tarifa_peaje fact_tarifa_peaje_sk_plaza_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_tarifa_peaje
    ADD CONSTRAINT fact_tarifa_peaje_sk_plaza_fkey FOREIGN KEY (sk_plaza) REFERENCES peajes.dim_plaza_peaje(sk_plaza);


--
-- Name: fact_viaje_peaje fact_viaje_peaje_sk_fecha_inicio_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_viaje_peaje
    ADD CONSTRAINT fact_viaje_peaje_sk_fecha_inicio_fkey FOREIGN KEY (sk_fecha_inicio) REFERENCES comun.dim_fecha(sk_fecha);


--
-- Name: fact_viaje_peaje fact_viaje_peaje_sk_tramo_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.fact_viaje_peaje
    ADD CONSTRAINT fact_viaje_peaje_sk_tramo_fkey FOREIGN KEY (sk_tramo) REFERENCES peajes.dim_tramo(sk_tramo);


--
-- Name: puente_segmento_plaza puente_segmento_plaza_sk_segmento_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_segmento_plaza
    ADD CONSTRAINT puente_segmento_plaza_sk_segmento_fkey FOREIGN KEY (sk_segmento) REFERENCES peajes.dim_segmento_ruta(sk_segmento);


--
-- Name: puente_tramo_segmento puente_tramo_segmento_sk_segmento_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_tramo_segmento
    ADD CONSTRAINT puente_tramo_segmento_sk_segmento_fkey FOREIGN KEY (sk_segmento) REFERENCES peajes.dim_segmento_ruta(sk_segmento);


--
-- Name: puente_tramo_segmento puente_tramo_segmento_sk_tramo_fkey; Type: FK CONSTRAINT; Schema: peajes; Owner: -
--

ALTER TABLE ONLY peajes.puente_tramo_segmento
    ADD CONSTRAINT puente_tramo_segmento_sk_tramo_fkey FOREIGN KEY (sk_tramo) REFERENCES peajes.dim_tramo(sk_tramo);


--
-- Name: carta_archivo carta_archivo_sk_carta_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.carta_archivo
    ADD CONSTRAINT carta_archivo_sk_carta_fkey FOREIGN KEY (sk_carta) REFERENCES precios.dim_carta_precio(sk_carta);


--
-- Name: dim_carta_precio dim_carta_precio_sk_fuente_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.dim_carta_precio
    ADD CONSTRAINT dim_carta_precio_sk_fuente_fkey FOREIGN KEY (sk_fuente) REFERENCES precios.dim_fuente_precio(sk_fuente);


--
-- Name: fact_costo_combustible_vigente fact_costo_combustible_vigente_sk_carta_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_costo_combustible_vigente
    ADD CONSTRAINT fact_costo_combustible_vigente_sk_carta_fkey FOREIGN KEY (sk_carta) REFERENCES precios.dim_carta_precio(sk_carta);


--
-- Name: fact_costo_combustible_vigente fact_costo_combustible_vigente_sk_fuente_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_costo_combustible_vigente
    ADD CONSTRAINT fact_costo_combustible_vigente_sk_fuente_fkey FOREIGN KEY (sk_fuente) REFERENCES precios.dim_fuente_precio(sk_fuente);


--
-- Name: fact_costo_combustible_vigente fact_costo_combustible_vigente_sk_precio_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_costo_combustible_vigente
    ADD CONSTRAINT fact_costo_combustible_vigente_sk_precio_fkey FOREIGN KEY (sk_precio) REFERENCES precios.fact_precio_combustible(sk_precio);


--
-- Name: fact_precio_combustible fact_precio_combustible_sk_carta_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_precio_combustible
    ADD CONSTRAINT fact_precio_combustible_sk_carta_fkey FOREIGN KEY (sk_carta) REFERENCES precios.dim_carta_precio(sk_carta);


--
-- Name: fact_precio_combustible fact_precio_combustible_sk_fuente_fkey; Type: FK CONSTRAINT; Schema: precios; Owner: -
--

ALTER TABLE ONLY precios.fact_precio_combustible
    ADD CONSTRAINT fact_precio_combustible_sk_fuente_fkey FOREIGN KEY (sk_fuente) REFERENCES precios.dim_fuente_precio(sk_fuente);


--
-- Name: fact_orden_taller fact_orden_taller_tipo_id_fkey; Type: FK CONSTRAINT; Schema: taller; Owner: -
--

ALTER TABLE ONLY taller.fact_orden_taller
    ADD CONSTRAINT fact_orden_taller_tipo_id_fkey FOREIGN KEY (tipo_id) REFERENCES taller.dim_tipo_intervencion(tipo_id);


--
-- Name: fact_parametro_valor fact_parametro_valor_sk_parametro_fkey; Type: FK CONSTRAINT; Schema: tarificador; Owner: -
--

ALTER TABLE ONLY tarificador.fact_parametro_valor
    ADD CONSTRAINT fact_parametro_valor_sk_parametro_fkey FOREIGN KEY (sk_parametro) REFERENCES tarificador.dim_parametro(sk_parametro);


--
-- PostgreSQL database dump complete
--

\unrestrict T47P2Zr5mTht7TShVVc8PbMvVvYe6783N3eMNC28zRbMBNon4opiyyvVGyKhRqI

