
import csv
import io
import os
import requests
import zipfile
from datetime import date
from flask import Flask, jsonify, request

app = Flask(__name__)
GTFS_URL = "https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip"





def read_csv(zf, filename):
    try:
        with zf.open(filename) as f:
            content = f.read().decode("utf-8-sig")
            return list(csv.DictReader(io.StringIO(content)))
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
                    "parent_station": s.get("parent_station", "")
                }
                for s in stops
                if "tarragona" in s.get("stop_name", "").casefold()
                or "barcelona-sants" in s.get("stop_name", "").casefold()
                or "barcelona sants" in s.get("stop_name", "").casefold()
                or "sants" in s.get("stop_name", "").casefold()
            ]

            return jsonify({
                "total_estaciones_gtfs": len(stops),
                "coincidencias": matches[:100],
                "total_coincidencias": len(matches)
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

            viajes.append({
                "id": entity.get("id", ""),
                "trip_id": trip.get("tripId", ""),
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
    estaciones = {"71500": "Tarragona", "71801": "Barcelona-Sants"}

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        coincidencias = []

        for entity in data.get("entity", []):
            update = entity.get("tripUpdate", {})
            trip = update.get("trip", {})

            for parada in update.get("stopTimeUpdate", []):
                stop_id = parada.get("stopId", "")

                if stop_id not in estaciones:
                    continue

                llegada = parada.get("arrival", {})
                salida = parada.get("departure", {})

                coincidencias.append({
                    "estacion": estaciones[stop_id],
                    "stop_id": stop_id,
                    "trip_id_tiempo_real": trip.get("tripId", ""),
                    "route_id": trip.get("routeId", ""),
                    "estado_viaje": trip.get("scheduleRelationship", ""),
                    "retraso_llegada_segundos": llegada.get("delay"),
                    "retraso_salida_segundos": salida.get("delay"),
                    "actualizacion": update.get("timestamp")
                })

        return jsonify({
            "fuente": "Renfe GTFS Realtime",
            "actualizacion": data.get("header", {}).get("timestamp"),
            "total_coincidencias": len(coincidencias),
            "coincidencias": coincidencias
        })

    except requests.RequestException:
        app.logger.exception("Error consultando trayecto")
        return jsonify({"error": "No se pudo conectar con Renfe."}), 502
    except (ValueError, TypeError):
        app.logger.exception("Error interpretando trayecto")
        return jsonify({"error": "Respuesta de Renfe no válida."}), 502
