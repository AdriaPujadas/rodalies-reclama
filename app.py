import csv
import io
import os
import requests
import zipfile
import json
import time
import hashlib
from datetime import date, datetime, timezone
from flask import Flask, jsonify, request

app = Flask(__name__)
_ultima_captura_monotonic = 0

GTFS_URL = "https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip"



def read_csv(zf, filename):
    try:
        with zf.open(filename) as f:
            content = io.TextIOWrapper(f, encoding="utf-8-sig")
            reader = csv.DictReader(content)

            if reader.fieldnames:
                reader.fieldnames = [
                    name.strip() if name else name
                    for name in reader.fieldnames
                ]

            return [
                {
                    key.strip() if key else key: (
                        value.strip() if isinstance(value, str) else value
                    )
                    for key, value in row.items()
                    if key is not None
                }
                for row in reader
            ]

    except KeyError:
        return []



def iter_csv(zf, filename):
    try:
        with zf.open(filename) as f:
            content = io.TextIOWrapper(f, encoding="utf-8-sig")
            reader = csv.DictReader(content)

            if reader.fieldnames:
                reader.fieldnames = [
                    name.strip() if name else name
                    for name in reader.fieldnames
                ]

            for row in reader:
                yield {
                    key.strip() if key else key: (
                        value.strip() if isinstance(value, str) else value
                    )
                    for key, value in row.items()
                    if key is not None
                }

    except KeyError:
        return




def get_gtfs():
    if not GTFS_URL:
        raise ValueError(
            "Falta configurar GTFS_URL con la URL oficial del archivo GTFS."
        )

    response = requests.get(GTFS_URL, timeout=60)
    response.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(response.content))


def active_service(service_id, travel_date, calendars, exceptions):
    day = travel_date.strftime("%A").lower()
    weekday_map = {
        "monday": "monday",
        "tuesday": "tuesday",
        "wednesday": "wednesday",
        "thursday": "thursday",
        "friday": "friday",
        "saturday": "saturday",
        "sunday": "sunday",
    }
    weekday = weekday_map[day]
    date_key = travel_date.strftime("%Y%m%d")
    active = False

    for row in calendars:
        if row["service_id"] == service_id:
            active = (
                row["start_date"] <= date_key <= row["end_date"]
                and row.get(weekday, "0") == "1"
            )
            break

    for row in exceptions:
        if row["service_id"] == service_id and row["date"] == date_key:
            active = row["exception_type"] == "1"

    return active


@app.get("/")
def home():
    return jsonify({
        "app": "Rodalies Reclama",
        "status": "API activa",
        "endpoint": "/api/trips",
        "required": ["origin", "destination", "date"]
    })


@app.get("/api/test")
def test():
    return jsonify({"status": "ok", "mensaje": "La aplicación responde"})



@app.get("/api/trips")
def trips():
    origin = request.args.get("origin", "").strip().casefold()
    destination = request.args.get("destination", "").strip().casefold()
    date_text = request.args.get("date", "")

    if not origin or not destination or not date_text:
        return jsonify({
            "error": "Indica origin, destination y date (AAAA-MM-DD)."
        }), 400

    try:
        travel_date = date.fromisoformat(date_text)

        with get_gtfs() as zf:
            # Leer las estaciones y buscar los identificadores.
            stops = read_csv(zf, "stops.txt")

            origin_ids = {
                s["stop_id"] for s in stops
                if origin in s.get("stop_name", "").casefold()
            }
            destination_ids = {
                s["stop_id"] for s in stops
                if destination in s.get("stop_name", "").casefold()
            }

            if not origin_ids or not destination_ids:
                return jsonify({
                    "error": "No se han encontrado ambas estaciones.",
                    "origin_matches": [
                        s.get("stop_name", "") for s in stops
                        if origin in s.get("stop_name", "").casefold()
                    ],
                    "destination_matches": [
                        s.get("stop_name", "") for s in stops
                        if destination in s.get("stop_name", "").casefold()
                    ]
                }), 404

            stop_names = {
                s["stop_id"]: s.get("stop_name", "")
                for s in stops
            }
            del stops

            # Conservar solo los viajes que pasan por el origen
            # y el destino, y sus datos mínimos.
            relevant_stops = origin_ids | destination_ids
            origin_by_trip = {}
            destination_by_trip = {}

            for row in iter_csv(zf, "stop_times.txt"):
                stop_id = row.get("stop_id", "")
                if stop_id not in relevant_stops:
                    continue

                trip_id = row.get("trip_id", "")
                try:
                    sequence = int(row.get("stop_sequence", ""))
                except (TypeError, ValueError):
                    continue

                item = (
                    sequence,
                    row.get("departure_time", ""),
                    row.get("arrival_time", ""),
                    stop_id
                )

                if stop_id in origin_ids:
                    origin_by_trip.setdefault(trip_id, []).append(item)

                if stop_id in destination_ids:
                    destination_by_trip.setdefault(trip_id, []).append(item)

            del row

            # Solo necesitamos los viajes candidatos, no todo trips.txt.
            candidate_ids = (
                origin_by_trip.keys() & destination_by_trip.keys()
            )
            if not candidate_ids:
                return jsonify({
                    "origen_consulta": origin,
                    "destino_consulta": destination,
                    "fecha": date_text,
                    "fuente": "GTFS oficial de Renfe",
                    "resultados": [],
                    "total": 0
                })

            trips_data = {
                t["trip_id"]: t
                for t in iter_csv(zf, "trips.txt")
                if t.get("trip_id") in candidate_ids
            }

            calendars = read_csv(zf, "calendar.txt")
            exceptions = read_csv(zf, "calendar_dates.txt")
            routes = read_csv(zf, "routes.txt")

            route_lookup = {
                r["route_id"]: r for r in routes
            }

            results = []
            for trip_id in candidate_ids:
                trip = trips_data.get(trip_id)
                if not trip:
                    continue

                service_id = trip.get("service_id", "")
                if (calendars or exceptions) and not active_service(
                    service_id, travel_date, calendars, exceptions
                ):
                    continue

                route = route_lookup.get(trip.get("route_id", ""), {})

                for start in origin_by_trip[trip_id]:
                    for end in destination_by_trip[trip_id]:
                        if start[0] >= end[0]:
                            continue

                        results.append({
                            "trip_id": trip_id,
                            "route_id": trip.get("route_id", ""),
                            "linea": (
                                route.get("route_short_name")
                                or route.get("route_long_name", "")
                            ),
                            "salida_teorica": start[1],
                            "llegada_teorica": end[2],
                            "origen": stop_names.get(start[3], origin),
                            "destino": stop_names.get(end[3], destination)
                        })

            results.sort(key=lambda r: r["salida_teorica"])

        return jsonify({
            "origen_consulta": origin,
            "destino_consulta": destination,
            "fecha": date_text,
            "fuente": "GTFS oficial de Renfe",
            "resultados": results,
            "total": len(results),
            "nota": (
                "Horarios teóricos; no indican por sí solos "
                "el retraso real."
            )
        })

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Error consultando GTFS")
        return jsonify({
            "error": "No se pudo consultar el archivo GTFS.",
            "detalle": str(exc)
        }), 502

@app.get("/api/debug")
def debug():
    try:
        with get_gtfs() as zf:
            stops = read_csv(zf, "stops.txt")

            matches = [
                {
                    "stop_id": s.get("stop_id", ""),
                    "stop_name": s.get("stop_name", ""),
                    "stop_code": s.get("stop_code", ""),
                    "parent_station": s.get("parent_station", ""),
                    "location_type": s.get("location_type", ""),
                    "stop_lat": s.get("stop_lat", ""),
                    "stop_lon": s.get("stop_lon", "")
                }
                for s in stops
                if "tarragona" in s.get("stop_name", "").casefold()
                or "sants" in s.get("stop_name", "").casefold()
            ]

            return jsonify({
                "total_estaciones_gtfs": len(stops),
                "coincidencias": matches,
                "total_coincidencias": len(matches),
                "nota": (
                    "La presencia de una estación en el feed "
                    "en tiempo real no confirma por sí sola "
                    "su equivalencia con una estación estática."
                )
            })

    except Exception as exc:
        app.logger.exception("Error en diagnóstico GTFS")
        return jsonify({"error": str(exc)}), 502

@app.get("/api/debug-trips")
def debug_trips():
    try:
        with get_gtfs() as zf:
            target_ids = {"71500", "71801"}
            counts = {"71500": 0, "71801": 0}
            examples = {"71500": [], "71801": []}

            for row in iter_csv(zf, "stop_times.txt"):
                stop_id = row.get("stop_id", "")
                if stop_id in target_ids:
                    counts[stop_id] += 1

                    if len(examples[stop_id]) < 5:
                        examples[stop_id].append({
                            "trip_id": row.get("trip_id", ""),
                            "stop_sequence": row.get("stop_sequence", ""),
                            "arrival_time": row.get("arrival_time", ""),
                            "departure_time": row.get("departure_time", "")
                        })

            return jsonify({
                "paradas_en_stop_times": counts,
                "ejemplos": examples
            })

    except Exception as exc:
        app.logger.exception("Error en diagnóstico de viajes")
        return jsonify({"error": str(exc)}), 502

@app.get("/api/cruce-retrasos")
def cruce_retrasos():
    fecha_text = request.args.get("date", "").strip()
    origen = request.args.get("origin", "Tarragona").strip()
    destino = request.args.get(
        "destination", "Barcelona-Sants"
    ).strip()

    if not fecha_text:
        return jsonify({
            "error": "Indica date (AAAA-MM-DD)."
        }), 400

    try:
        date.fromisoformat(fecha_text)
    except ValueError:
        return jsonify({
            "error": "La fecha debe tener formato AAAA-MM-DD."
        }), 400

    # Obtener el horario teórico usando la lógica existente.
    try:
        with app.test_request_context(
            "/api/trips",
            query_string={
                "origin": origen,
                "destination": destino,
                "date": fecha_text
            }
        ):
            respuesta = trips()

        if isinstance(respuesta, tuple):
            respuesta = respuesta[0]

        horarios = respuesta.get_json()
        if respuesta.status_code != 200:
            return jsonify({
                "error": "No se pudo obtener el horario teórico.",
                "detalle": horarios
            }), respuesta.status_code

        # Consultar el feed oficial en tiempo real.
        url = "https://gtfsrt.renfe.com/trip_updates.json"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        feed = response.json()

        actualizaciones = []

        for entity in feed.get("entity", []):
            update = entity.get("tripUpdate")
            if not update:
                continue

            trip = update.get("trip", {})
            trip_id = trip.get("tripId", "")

            # Regla provisional: quitar el prefijo 5101J.
            sufijo = (
                trip_id[5:]
                if trip_id.startswith("5101J")
                else trip_id
            )

            for parada in update.get("stopTimeUpdate", []):
                llegada = parada.get("arrival", {})
                salida = parada.get("departure", {})

                retraso_llegada = llegada.get("delay")
                retraso_salida = salida.get("delay")

                actualizaciones.append({
                    "trip_id_tiempo_real": trip_id,
                    "sufijo_candidato": sufijo,
                    "stop_id": parada.get("stopId", ""),
                    "retraso_llegada_segundos": retraso_llegada,
                    "retraso_salida_segundos": retraso_salida,
                    "estado_viaje": trip.get(
                        "scheduleRelationship", ""
                    )
                })

        resultados = []

        for horario in horarios.get("resultados", []):
            trip_id = horario.get("trip_id", "")

            # No se afirma que compartir sufijo confirme el servicio.
            sufijo_horario = (
                trip_id[5:]
                if trip_id.startswith("5102V")
                else trip_id
            )

            candidatos = [
                a for a in actualizaciones
                if a["sufijo_candidato"] == sufijo_horario
            ]

            resultados.append({
                **horario,
                "coincidencia_tiempo_real": (
                    "candidata_por_sufijo" if candidatos
                    else "sin_coincidencia"
                ),
                "coincidencias_candidatas": candidatos
            })

        return jsonify({
            "fecha_horario": fecha_text,
            "origen": origen,
            "destino": destino,
            "fuente_horario": "GTFS oficial de Renfe",
            "fuente_tiempo_real": "Renfe GTFS Realtime",
            "actualizacion_feed": feed.get(
                "header", {}
            ).get("timestamp"),
            "total_horarios": len(resultados),
            "total_con_candidatos": sum(
                1 for r in resultados
                if r["coincidencias_candidatas"]
            ),
            "nota": (
                "Las coincidencias por sufijo son provisionales. "
                "Los retrasos corresponden a paradas del feed en "
                "tiempo real y no se atribuyen automáticamente "
                "al trayecto completo ni a la fecha consultada."
            ),
            "resultados": resultados
        })

    except requests.RequestException:
        app.logger.exception("Error consultando feed en tiempo real")
        return jsonify({
            "error": "No se pudo consultar el feed de Renfe."
        }), 502
    except (ValueError, TypeError, AttributeError):
        app.logger.exception("Error procesando el cruce")
        return jsonify({
            "error": "No se pudieron interpretar los datos del cruce."
        }), 502

@app.get("/api/debug-columns")
def debug_columns():
    try:
        with get_gtfs() as zf:
            with zf.open("stop_times.txt") as f:
                import io
                import csv

                content = io.TextIOWrapper(f, encoding="utf-8-sig")
                reader = csv.DictReader(content)
                return jsonify({
                    "columnas": reader.fieldnames,
                    "primera_fila": next(reader, None)
                })
    except Exception as exc:
        app.logger.exception("Error leyendo columnas GTFS")
        return jsonify({"error": str(exc)}), 502

@app.get("/api/incidencias")
def incidencias():
    url = "https://gtfsrt.renfe.com/alerts.json"

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        incidencias = []

        for entity in data.get("entity", []):
            alert = entity.get("alert", {})
            translations = (
                alert.get("descriptionText", {}).get("translation", [])
            )

            descripcion = next(
                (
                    item.get("text", "")
                    for item in translations
                    if item.get("language") in ("es", "ca")
                ),
                translations[0].get("text", "") if translations else ""
            )

            if not descripcion:
                continue

            incidencias.append({
                "id": entity.get("id", ""),
                "descripcion": descripcion,
                "rutas": [
                    item.get("routeId")
                    for item in alert.get("informedEntity", [])
                    if item.get("routeId")
                ],
                "estaciones": [
                    item.get("stopId")
                    for item in alert.get("informedEntity", [])
                    if item.get("stopId")
                ]
            })

        return jsonify({
            "fuente": "Renfe GTFS Realtime",
            "actualizacion": data.get("header", {}).get("timestamp"),
            "total": len(incidencias),
            "incidencias": incidencias
        })

    except requests.RequestException:
        app.logger.exception("Error conectando con incidencias de Renfe")
        return jsonify({
            "error": "No se pudo conectar con los datos de incidencias de Renfe."
        }), 502
    except (ValueError, TypeError):
        app.logger.exception("Respuesta de incidencias no válida")
        return jsonify({
            "error": "Renfe ha devuelto datos que no se han podido interpretar."
        }), 502

@app.get("/api/retrasos")
def retrasos():
    url = "https://gtfsrt.renfe.com/trip_updates.json"

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        viajes = []

        for entity in data.get("entity", []):
            update = entity.get("tripUpdate")
            if not update:
                continue

            trip = update.get("trip", {})
            retrasos_paradas = []

            for parada in update.get("stopTimeUpdate", []):
                llegada = parada.get("arrival", {})
                salida = parada.get("departure", {})

                retrasos_paradas.append({
                    "estacion_id": parada.get("stopId", ""),
                    "secuencia": parada.get("stopSequence"),
                    "retraso_llegada_segundos": llegada.get("delay"),
                    "retraso_salida_segundos": salida.get("delay"),
                    "estado": parada.get("scheduleRelationship", "")
                })

            

            trip_id = trip.get("tripId", "")

            # Identificador provisional para cruzar con el GTFS estático.
            trip_id_normalizado = (
                trip_id[5:]
                if trip_id.startswith("5101J")
                else trip_id
            )

            viajes.append({
                "id": entity.get("id", ""),
                "trip_id": trip_id,
                "trip_id_normalizado": trip_id_normalizado,
                "route_id": trip.get("routeId", ""),
                "estado": trip.get("scheduleRelationship", ""),
                "actualizacion": update.get("timestamp"),
                "paradas": retrasos_paradas
            })

        return jsonify({
            "fuente": "Renfe GTFS Realtime",
            "actualizacion": data.get("header", {}).get("timestamp"),
            "total_viajes": len(viajes),
            "viajes": viajes[:200]
        })

    except requests.RequestException:
        app.logger.exception("Error consultando actualizaciones de viajes")
        return jsonify({
            "error": "No se pudo conectar con las actualizaciones de Renfe."
        }), 502
    except (ValueError, TypeError):
        app.logger.exception("Respuesta de viajes no válida")
        return jsonify({
            "error": "No se han podido interpretar los datos de Renfe."
        }), 502


        
    except requests.RequestException:
        app.logger.exception("Error consultando actualizaciones de viajes")
        return jsonify({
            "error": "No se pudo conectar con las actualizaciones de Renfe."
        }), 502
    except (ValueError, TypeError):
        app.logger.exception("Respuesta de viajes no válida")
        return jsonify({
            "error": "No se han podido interpretar los datos de Renfe."
        }), 502

@app.get("/api/debug-retrasos")
def debug_retrasos():
    url = "https://gtfsrt.renfe.com/trip_updates.json"

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        paradas = {}
        retrasos_conocidos = 0
        viajes_con_retraso = 0
        viajes_cancelados = 0

        for entity in data.get("entity", []):
            update = entity.get("tripUpdate", {})
            trip = update.get("trip", {})

            if trip.get("scheduleRelationship") == "CANCELED":
                viajes_cancelados += 1

            tiene_retraso = False

            for parada in update.get("stopTimeUpdate", []):
                stop_id = parada.get("stopId", "")
                if not stop_id:
                    continue

                if stop_id not in paradas:
                    paradas[stop_id] = {
                        "actualizaciones": 0,
                        "retrasos_conocidos": 0,
                        "retraso_maximo_segundos": None
                    }

                info = paradas[stop_id]
                info["actualizaciones"] += 1

                for momento in ("arrival", "departure"):
                    retraso = parada.get(momento, {}).get("delay")

                    if retraso is not None:
                        info["retrasos_conocidos"] += 1
                        retrasos_conocidos += 1

                        actual = info["retraso_maximo_segundos"]
                        if actual is None or retraso > actual:
                            info["retraso_maximo_segundos"] = retraso

                        if retraso > 0:
                            tiene_retraso = True

            if tiene_retraso:
                viajes_con_retraso += 1

        return jsonify({
            "fuente": "Renfe GTFS Realtime",
            "total_viajes": len(data.get("entity", [])),
            "paradas_distintas": len(paradas),
            "retrasos_conocidos": retrasos_conocidos,
            "viajes_con_retraso": viajes_con_retraso,
            "viajes_cancelados": viajes_cancelados,
            "paradas": paradas
        })

    except requests.RequestException:
        app.logger.exception("Error consultando diagnóstico")
        return jsonify({"error": "No se pudo conectar con Renfe."}), 502


@app.get("/api/debug-trayecto")
def debug_trayecto():
    url = "https://gtfsrt.renfe.com/trip_updates.json"
    estaciones = {
        "71500": "Tarragona",
        "71801": "Barcelona-Sants",
        "71503": "Tarragona (variante por confirmar)",
        "71802": "Barcelona-Sants (variante por confirmar)"
    }

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        coincidencias = []

        for entity in data.get("entity", []):
            update = entity.get("tripUpdate")
            if not update:
                continue

            trip = update.get("trip", {})
            trip_id = trip.get("tripId", "")

            for parada in update.get("stopTimeUpdate", []):
                stop_id = parada.get("stopId", "")

                if stop_id not in estaciones:
                    continue

                llegada = parada.get("arrival", {})
                salida = parada.get("departure", {})

                coincidencias.append({
                    "estacion": estaciones[stop_id],
                    "stop_id": stop_id,
                    "trip_id_tiempo_real": trip_id,
                    "trip_id_sufijo_candidato": (
                        trip_id[5:]
                        if trip_id.startswith("5101J")
                        else trip_id
                    ),
                    "route_id": trip.get("routeId", ""),
                    "estado_viaje": trip.get("scheduleRelationship", ""),
                    "secuencia_parada": parada.get("stopSequence"),
                    "retraso_llegada_segundos": llegada.get("delay"),
                    "retraso_salida_segundos": salida.get("delay"),
                    "relacion_horario": parada.get("scheduleRelationship", "")
                })

        return jsonify({
            "fuente": "Renfe GTFS Realtime",
            "actualizacion_feed": data.get("header", {}).get("timestamp"),
            "total_coincidencias": len(coincidencias),
            "coincidencias": coincidencias
        })

    except requests.RequestException:
        app.logger.exception("Error consultando trayecto")
        return jsonify({
            "error": "No se pudo conectar con Renfe."
        }), 502

    except (ValueError, TypeError):
        app.logger.exception("Error interpretando trayecto")
        return jsonify({
            "error": "Respuesta de Renfe no válida."
        }), 502

@app.route("/api/capturar", methods=["POST", "OPTIONS"])

def capturar():
    if request.method == "OPTIONS":
        respuesta = app.make_response("")
        respuesta.headers["Access-Control-Allow-Origin"] = "https://hoppscotch.io"
        respuesta.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        respuesta.headers["Access-Control-Allow-Headers"] = "X-Capture-Token, Content-Type"
        return respuesta, 200

    token = request.headers.get("X-Capture-Token", "")

    if not os.environ.get("CAPTURE_TOKEN"):
        return jsonify({"error": "CAPTURE_TOKEN no está configurado"}), 500

    if token != os.environ["CAPTURE_TOKEN"]:
        return jsonify({"error": "No autorizado"}), 401

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_KEY", "")

    if not supabase_url or not supabase_key:
        return jsonify({"error": "Falta configurar Supabase"}), 500

    try:
        urls = {
            "alerts": "https://gtfsrt.renfe.com/alerts.json",
            "trip_updates": "https://gtfsrt.renfe.com/trip_updates.json",
            "trip_updates_ld": "https://gtfsrt.renfe.com/trip_updates_LD.json"
        }

        feeds = {}

        for nombre, url in urls.items():
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            feeds[nombre] = response.json()

        contenido = json.dumps(
            feeds,
            sort_keys=True,
            separators=(",", ":")
        )

        huella = hashlib.sha256(
            contenido.encode("utf-8")
        ).hexdigest()

        fila = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "feed_timestamp": str(
                feeds["alerts"].get("header", {}).get("timestamp", "")
            ) or None,
            "alerts": feeds["alerts"],
            "trip_updates": feeds["trip_updates"],
            "trip_updates_ld": feeds["trip_updates_ld"],
            "sha256": huella
        }

        respuesta = requests.post(
            f"{supabase_url}/rest/v1/renfe_capturas",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            },
            json=fila,
            timeout=30
        )

        respuesta.raise_for_status()

        return jsonify({
            "status": "ok",
            "mensaje": "Captura de los tres feeds guardada en Supabase",
            "captured_at": fila["captured_at"],
            "sha256": huella
        }), 201

    except requests.RequestException:
        app.logger.exception("Error guardando captura")
        return jsonify({
            "error": "No se pudo descargar Renfe o guardar en Supabase"
        }), 502

    except (ValueError, TypeError):
        app.logger.exception("Error procesando captura")
        return jsonify({
            "error": "Los datos recibidos no son válidos"
        }), 502

@app.after_request
def añadir_cors(respuesta):
    respuesta.headers["Access-Control-Allow-Origin"] = "https://hoppscotch.io"
    respuesta.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    respuesta.headers["Access-Control-Allow-Headers"] = "X-Capture-Token, Content-Type"
    return respuesta

@app.get("/api/feeds-tiempo-real")
def feeds_tiempo_real():
    fuentes = {
        "cercanias": "https://gtfsrt.renfe.com/trip_updates.json",
        "larga_distancia": "https://gtfsrt.renfe.com/trip_updates_LD.json"
    }

    resultados = {}

    for nombre, url in fuentes.items():
        try:
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            feed = response.json()

            total_viajes = 0
            total_paradas = 0
            total_retrasos = 0
            total_suprimidas = 0
            total_cancelados = 0
            ejemplos = []

            for entity in feed.get("entity", []):
                update = entity.get("tripUpdate")
                if not update:
                    continue

                total_viajes += 1
                trip = update.get("trip", {})

                if trip.get("scheduleRelationship") == "CANCELED":
                    total_cancelados += 1

                for parada in update.get("stopTimeUpdate", []):
                    total_paradas += 1

                    if parada.get("scheduleRelationship") == "SKIPPED":
                        total_suprimidas += 1

                    retraso = parada.get("departure", {}).get("delay")
                    if retraso is None:
                        retraso = parada.get("arrival", {}).get("delay")

                    if retraso is not None and retraso > 0:
                        total_retrasos += 1

                if len(ejemplos) < 5:
                    ejemplos.append({
                        "trip_id": trip.get("tripId", ""),
                        "estado": trip.get("scheduleRelationship", "")
                    })

            resultados[nombre] = {
                "estado": "ok",
                "timestamp_feed": feed.get("header", {}).get("timestamp"),
                "total_viajes": total_viajes,
                "total_paradas": total_paradas,
                "paradas_con_retraso": total_retrasos,
                "paradas_suprimidas": total_suprimidas,
                "viajes_cancelados": total_cancelados,
                "ejemplos": ejemplos
            }

        except requests.RequestException as exc:
            resultados[nombre] = {
                "estado": "error",
                "detalle": str(exc)
            }
        except (ValueError, TypeError) as exc:
            resultados[nombre] = {
                "estado": "error",
                "detalle": str(exc)
            }

    return jsonify({
        "fuente": "Feeds GTFS-RT de Renfe",
        "resultados": resultados
    })


@app.get("/api/trayecto-tiempo-real")
def trayecto_tiempo_real():
    from zoneinfo import ZoneInfo

    origen = request.args.get("origen", "71500")
    destino = request.args.get("destino", "71801")
    fecha_texto = request.args.get("fecha")

    if not fecha_texto:
        fecha_texto = datetime.now(
            ZoneInfo("Europe/Madrid")
        ).date().isoformat()

    try:
        fecha_consulta = date.fromisoformat(fecha_texto)
    except ValueError:
        return jsonify({
            "error": "Fecha no válida. Utiliza AAAA-MM-DD."
        }), 400

    fuentes = {
        "cercanias": "https://gtfsrt.renfe.com/trip_updates.json",
        "larga_distancia": "https://gtfsrt.renfe.com/trip_updates_LD.json"
    }

    sin_incidencias = []
    con_incidencias = []
    errores = {}
    sin_fecha = 0

    def obtener_fecha(trip):
        valor = trip.get("startDate")

        if valor:
            try:
                if len(valor) == 8:
                    return datetime.strptime(
                        valor, "%Y%m%d"
                    ).date()
                return date.fromisoformat(valor)
            except ValueError:
                pass

        trip_id = str(trip.get("tripId", ""))

        try:
            return date.fromisoformat(trip_id[-10:])
        except ValueError:
            return None

    def datos_parada(parada):
        llegada = parada.get("arrival", {})
        salida = parada.get("departure", {})

        retraso = salida.get("delay")
        if retraso is None:
            retraso = llegada.get("delay")

        return {
            "stop_id": parada.get("stopId"),
            "llegada": llegada.get("time"),
            "salida": salida.get("time"),
            "retraso_segundos": retraso,
            "estado": parada.get("scheduleRelationship")
        }

    for nombre, url in fuentes.items():
        try:
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            feed = response.json()

            for entity in feed.get("entity", []):
                update = entity.get("tripUpdate")
                if not update:
                    continue

                trip = update.get("trip", {})
                fecha_tren = obtener_fecha(trip)

                if fecha_tren is None:
                    sin_fecha += 1
                    continue

                if fecha_tren != fecha_consulta:
                    continue

                paradas = update.get("stopTimeUpdate", [])
                ids = [
                    str(p.get("stopId", ""))
                    for p in paradas
                ]

                if origen not in ids or destino not in ids:
                    continue

                i_origen = ids.index(origen)
                i_destino = ids.index(destino)

                if i_origen >= i_destino:
                    continue

                parada_origen = paradas[i_origen]
                parada_destino = paradas[i_destino]
                intermedias = paradas[i_origen + 1:i_destino]

                estado_viaje = trip.get(
                    "scheduleRelationship", "UNKNOWN"
                )

                origen_datos = datos_parada(parada_origen)
                destino_datos = datos_parada(parada_destino)

                suprimidas = [
                    str(p.get("stopId"))
                    for p in paradas[i_origen:i_destino + 1]
                    if p.get("scheduleRelationship") == "SKIPPED"
                ]

                retrasos = [
                    datos_parada(p)["retraso_segundos"]
                    for p in paradas[i_origen:i_destino + 1]
                ]

                hay_retraso = any(
                    isinstance(r, (int, float)) and r > 0
                    for r in retrasos
                )

                motivos = []

                if estado_viaje == "CANCELED":
                    motivos.append("Viaje cancelado")

                if parada_origen.get("scheduleRelationship") == "SKIPPED":
                    motivos.append("Origen suprimido")

                if parada_destino.get("scheduleRelationship") == "SKIPPED":
                    motivos.append("Destino suprimido")

                if any(
                    p.get("scheduleRelationship") == "SKIPPED"
                    for p in intermedias
                ):
                    motivos.append("Parada intermedia suprimida")

                if hay_retraso:
                    motivos.append("Retraso comunicado")

                tren = {
                    "fuente": nombre,
                    "fecha": fecha_tren.isoformat(),
                    "trip_id": trip.get("tripId"),
                    "estado_viaje": estado_viaje,
                    "origen": origen_datos,
                    "destino": destino_datos,
                    "paradas_suprimidas": suprimidas,
                    "incidencias": motivos
                }

                if motivos:
                    con_incidencias.append(tren)
                else:
                    sin_incidencias.append(tren)

        except requests.RequestException as exc:
            errores[nombre] = str(exc)
        except (ValueError, TypeError) as exc:
            errores[nombre] = str(exc)

    return jsonify({
        "trayecto": "Tarragona - Barcelona-Sants",
        "fecha_consultada": fecha_consulta.isoformat(),
        "origen_id": origen,
        "destino_id": destino,
        "trenes_sin_incidencias_detectadas": sin_incidencias,
        "trenes_con_incidencias": con_incidencias,
        "total_sin_incidencias": len(sin_incidencias),
        "total_con_incidencias": len(con_incidencias),
        "registros_sin_fecha_identificable": sin_fecha,
        "errores": errores
    })
