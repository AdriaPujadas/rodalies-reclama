
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
            yield from csv.DictReader(content)
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
