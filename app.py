
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
        zf = get_gtfs()

        # Estos archivos son mucho más pequeños que stop_times.txt.
        stops = list(read_csv(zf, "stops.txt"))
        trips_data = list(read_csv(zf, "trips.txt"))
        calendars = list(read_csv(zf, "calendar.txt"))
        exceptions = list(read_csv(zf, "calendar_dates.txt"))
        routes = list(read_csv(zf, "routes.txt"))

        origin_ids = {
            row["stop_id"] for row in stops
            if origin in row.get("stop_name", "").casefold()
        }
        destination_ids = {
            row["stop_id"] for row in stops
            if destination in row.get("stop_name", "").casefold()
        }

        if not origin_ids or not destination_ids:
            return jsonify({
                "error": "No se han encontrado ambas estaciones en este GTFS.",
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
            row["stop_id"]: row.get("stop_name", "")
            for row in stops
        }
        trip_lookup = {
            row["trip_id"]: row for row in trips_data
        }
        route_lookup = {
            row["route_id"]: row for row in routes
        }

        # Guardamos solo las paradas de origen y destino,
        # no todas las filas del archivo stop_times.txt.
        relevant_stops = origin_ids | destination_ids
        origin_by_trip = {}
        destination_by_trip = {}

        for row in read_csv(zf, "stop_times.txt"):
            stop_id = row.get("stop_id", "")
            if stop_id not in relevant_stops:
                continue

            trip_id = row.get("trip_id", "")
            try:
                sequence = int(row.get("stop_sequence", ""))
            except (TypeError, ValueError):
                continue

            item = {
                "trip_id": trip_id,
                "stop_id": stop_id,
                "stop_sequence": sequence,
                "departure_time": row.get("departure_time", ""),
                "arrival_time": row.get("arrival_time", "")
            }

            if stop_id in origin_ids:
                origin_by_trip.setdefault(trip_id, []).append(item)

            if stop_id in destination_ids:
                destination_by_trip.setdefault(trip_id, []).append(item)

        results = []

        for trip_id in origin_by_trip.keys() & destination_by_trip.keys():
            trip = trip_lookup.get(trip_id)
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
                    if start["stop_sequence"] >= end["stop_sequence"]:
                        continue

                    results.append({
                        "trip_id": trip_id,
                        "route_id": trip.get("route_id", ""),
                        "linea": (
                            route.get("route_short_name")
                            or route.get("route_long_name", "")
                        ),
                        "salida_teorica": start["departure_time"],
                        "llegada_teorica": end["arrival_time"],
                        "origen": stop_names.get(start["stop_id"], origin),
                        "destino": stop_names.get(
                            end["stop_id"], destination
                        )
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
