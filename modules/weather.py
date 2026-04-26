"""Open-Meteo weather fetch. Free, no API key required."""

import logging

import requests

from . import database

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 10


def fetch_current(latitude: float, longitude: float) -> dict | None:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "dew_point_2m",
            "wind_speed_10m",
            "cloud_cover",
            "precipitation",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    try:
        response = requests.get(OPEN_METEO_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Weather fetch failed: %s", exc)
        return None

    current = response.json().get("current", {})
    return {
        "outdoor_temp": current.get("temperature_2m"),
        "outdoor_humidity": current.get("relative_humidity_2m"),
        "dewpoint": current.get("dew_point_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "cloud_cover": current.get("cloud_cover"),
        "precipitation": current.get("precipitation"),
    }


def poll(config: dict) -> dict | None:
    weather_cfg = config.get("weather", {})
    reading = fetch_current(weather_cfg["latitude"], weather_cfg["longitude"])
    if reading is not None:
        database.record_weather_reading(reading)
    return reading
