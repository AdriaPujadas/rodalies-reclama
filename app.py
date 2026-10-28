
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
            content = io.TextIOWrapper(f, encoding="utf-8-sig")
            return list(csv.DictReader(content))
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

        stops = read_csv(zf, "stops.txt")
        stop_times = read_csv(zf, "stop_times.txt")
        trips_data = read_csv(zf, "trips.txt")
        calendars = read_csv(zf, "calendar.txt")
        exceptions = read_csv(zf, "calendar_dates.txt")
        routes = read_csv(zf, "routes.txt")

        def matches(name, query):
            return query in name.casefold()

        origin_ids = {
            row["stop_id"] for row in stops
            if matches(row.get("stop_name", ""), origin)
        }
        destination_ids = {
            row["stop_id"] for row in stops
            if matches(row.get("stop_name", ""), destination)
        }

        if not origin_ids or not destination_ids:
            return jsonify({
                "error": "No se han encontrado ambas estaciones en este GTFS.",
                "origin_matches": [
                    s.get("stop_name") for s in stops
                    if matches(s.get("stop_name", ""), origin)
                ],
                "destination_matches": [
                    s.get("stop_name") for s in stops
                    if matches(s.get("stop_name", ""), destination)
                ]
            }), 404

        by_trip = {}
        for row in stop_times:
            by_trip.setdefault(row["trip_id"], []).append(row)

        trip_lookup = {row["trip_id"]: row for row in trips_data}
        route_lookup = {row["route_id"]: row for row in routes}
        results = []

        for trip_id, rows in by_trip.items():
            rows.sort(key=lambda r: int(r["stop_sequence"]))
            origin_rows = [r for r in rows if r["stop_id"] in origin_ids]
            destination_rows = [
                r for r in rows if r["stop_id"] in destination_ids
            ]

            for start in origin_rows:
                for end in destination_rows:
                    if int(start["stop_sequence"]) >= int(end["stop_sequence"]):
                        continue

                    trip = trip_lookup.get(trip_id)
                    if not trip:
                        continue

                    service_id = trip.get("service_id", "")
                    if calendars or exceptions:
                        if not active_service(
                            service_id, travel_date, calendars, exceptions
                        ):
                            continue

                    route = route_lookup.get(trip.get("route_id", ""), {})
                    results.append({
                        "trip_id": trip_id,
                        "route_id": trip.get("route_id", ""),
                        "linea": route.get("route_short_name")
                                 or route.get("route_long_name", ""),
                        "salida_teorica": start.get("departure_time", ""),
                        "llegada_teorica": end.get("arrival_time", ""),
                        "origen": next(
                            (s["stop_name"] for s in stops
                             if s["stop_id"] == start["stop_id"]), origin
                        ),
                        "destino": next(
                            (s["stop_name"] for s in stops
                             if s["stop_id"] == end["stop_id"]), destination
                        )
                    })

        results.sort(key=lambda r: r["salida_teorica"])

        return jsonify({
            "origen_consulta": origin,
            "destino_consulta": destination,
            "fecha": date_text,
            "fuente": "GTFS configurado en el servidor",
            "resultados": results,
            "total": len(results),
            "nota": "Horarios teóricos; no indican por sí solos el retraso real."
        })

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        app.logger.exception("Error consultando GTFS")
        return jsonify({
            "error": "No se pudo consultar el archivo GTFS.",
            "detalle": str(exc)
        }), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
