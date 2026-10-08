import os
import time
from datetime import date, datetime, timedelta

import requests


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHERAPI_BASE_URL = "https://api.weatherapi.com/v1"
REQUEST_TIMEOUT = 20
RECENT_HISTORY_DAYS = 5
OPEN_METEO_RETRIES = 3
DEFAULT_START_HOUR = 7
DEFAULT_END_HOUR = 17


class WeatherProviderError(RuntimeError):
    pass


class WeatherRateLimitError(WeatherProviderError):
    pass


def classify_weather(
    cloud_cover_percent: float,
    rainfall_mm: float,
    solar_radiation_wh_m2: float | None,
) -> str:
    if solar_radiation_wh_m2 is None:
        if rainfall_mm > 5 or cloud_cover_percent >= 80:
            return "unfavorable"
        if rainfall_mm <= 1 and cloud_cover_percent <= 45:
            return "favorable"
        return "partially_favorable"

    if rainfall_mm > 5 or solar_radiation_wh_m2 < 2000:
        return "unfavorable"

    if rainfall_mm <= 1 and cloud_cover_percent <= 45 and solar_radiation_wh_m2 >= 3500:
        return "favorable"

    return "partially_favorable"


def _open_meteo_url(report_date: date) -> str:
    if report_date >= date.today() - timedelta(days=RECENT_HISTORY_DAYS):
        return OPEN_METEO_FORECAST_URL

    return OPEN_METEO_ARCHIVE_URL


def _request_open_meteo(url: str, params: dict) -> dict:
    last_error = None

    for attempt in range(OPEN_METEO_RETRIES):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < OPEN_METEO_RETRIES - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise WeatherProviderError("Falha de rede no Open-Meteo.") from exc

        if response.status_code == 429:
            last_error = WeatherRateLimitError("Open-Meteo limitou as requisições.")
            if attempt < OPEN_METEO_RETRIES - 1:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 0.75 * (2 ** attempt)
                except ValueError:
                    wait = 0.75 * (2 ** attempt)
                time.sleep(min(max(wait, 0.25), 3.0))
                continue
            raise last_error

        if response.status_code >= 500:
            last_error = WeatherProviderError(
                f"Open-Meteo indisponível: HTTP {response.status_code}."
            )
            if attempt < OPEN_METEO_RETRIES - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise last_error

        try:
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise WeatherProviderError(
                f"Open-Meteo rejeitou a consulta: HTTP {response.status_code}."
            ) from exc
        except ValueError as exc:
            raise WeatherProviderError("Open-Meteo retornou JSON inválido.") from exc

    raise WeatherProviderError(str(last_error or "Falha no Open-Meteo."))


def _hourly_variables() -> str:
    return ",".join(
        [
            "temperature_2m",
            "precipitation",
            "cloud_cover",
            "shortwave_radiation",
            "sunshine_duration",
            "is_day",
        ]
    )


def _open_meteo_hourly_rows(
    latitude: float,
    longitude: float,
    report_date: date,
) -> list[dict]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": _hourly_variables(),
        "timezone": "America/Bahia",
        "start_date": report_date.isoformat(),
        "end_date": report_date.isoformat(),
    }

    payload = _request_open_meteo(_open_meteo_url(report_date), params)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []

    rows = []
    for index, timestamp in enumerate(times):
        def value(name):
            values = hourly.get(name) or []
            return values[index] if index < len(values) else None

        rows.append(
            {
                "time": str(timestamp),
                "temperature_c": value("temperature_2m"),
                "precipitation_mm": value("precipitation"),
                "cloud_cover_percent": value("cloud_cover"),
                "shortwave_radiation_w_m2": value("shortwave_radiation"),
                "sunshine_seconds": value("sunshine_duration"),
                "is_day": value("is_day"),
                "condition_text": None,
            }
        )

    if not rows:
        raise WeatherProviderError(
            "Open-Meteo não retornou dados horários para a data solicitada."
        )

    return rows


def _weatherapi_key() -> str:
    return os.getenv("WEATHERAPI_KEY", "").strip()


def _weatherapi_hourly_rows(
    latitude: float,
    longitude: float,
    report_date: date,
) -> list[dict]:
    api_key = _weatherapi_key()
    if not api_key:
        raise WeatherProviderError("WEATHERAPI_KEY não configurada.")

    today = date.today()
    if report_date == today:
        endpoint = "forecast.json"
        params = {
            "key": api_key,
            "q": f"{latitude},{longitude}",
            "days": 1,
            "aqi": "no",
            "alerts": "no",
            "lang": "pt",
        }
    else:
        endpoint = "history.json"
        params = {
            "key": api_key,
            "q": f"{latitude},{longitude}",
            "dt": report_date.isoformat(),
            "lang": "pt",
        }

    try:
        response = requests.get(
            f"{WEATHERAPI_BASE_URL}/{endpoint}",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise WeatherProviderError("Falha ao consultar WeatherAPI.") from exc
    except ValueError as exc:
        raise WeatherProviderError("WeatherAPI retornou JSON inválido.") from exc

    forecast_days = ((payload.get("forecast") or {}).get("forecastday") or [])
    if not forecast_days:
        raise WeatherProviderError("WeatherAPI não retornou dados horários.")

    rows = []
    for item in forecast_days[0].get("hour") or []:
        if not isinstance(item, dict):
            continue
        condition = item.get("condition") or {}
        rows.append(
            {
                "time": str(item.get("time") or ""),
                "temperature_c": item.get("temp_c"),
                "precipitation_mm": item.get("precip_mm"),
                "cloud_cover_percent": item.get("cloud"),
                "shortwave_radiation_w_m2": None,
                "sunshine_seconds": None,
                "is_day": item.get("is_day"),
                "condition_text": str(condition.get("text") or ""),
            }
        )

    if not rows:
        raise WeatherProviderError("WeatherAPI não retornou dados horários.")

    return rows


def get_hourly_weather_rows(
    latitude: float,
    longitude: float,
    report_date: date,
) -> tuple[list[dict], str]:
    try:
        return (
            _open_meteo_hourly_rows(
                latitude=latitude,
                longitude=longitude,
                report_date=report_date,
            ),
            "open-meteo",
        )
    except WeatherProviderError:
        if not _weatherapi_key():
            raise

    return (
        _weatherapi_hourly_rows(
            latitude=latitude,
            longitude=longitude,
            report_date=report_date,
        ),
        "weatherapi",
    )


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace(" ", "T"))


def get_weather_window(
    latitude: float,
    longitude: float,
    report_date: date,
    start_hour: int = DEFAULT_START_HOUR,
    end_hour: int = DEFAULT_END_HOUR,
) -> dict:
    try:
        start_hour = int(start_hour)
        end_hour = int(end_hour)
    except (TypeError, ValueError) as exc:
        raise ValueError("Horário inicial e final devem ser números inteiros.") from exc

    if not 0 <= start_hour <= 23 or not 1 <= end_hour <= 24:
        raise ValueError("Horários devem estar entre 0 e 24.")

    if end_hour <= start_hour:
        raise ValueError("O horário final deve ser posterior ao horário inicial.")

    if end_hour - start_hour > 16:
        raise ValueError("A janela climática não pode ultrapassar 16 horas.")

    rows, source = get_hourly_weather_rows(
        latitude=latitude,
        longitude=longitude,
        report_date=report_date,
    )

    selected = []
    for row in rows:
        try:
            timestamp = _parse_hour(row.get("time"))
        except (TypeError, ValueError):
            continue

        if (
            timestamp.date() == report_date
            and start_hour <= timestamp.hour < end_hour
        ):
            selected.append(row)

    if not selected:
        raise WeatherProviderError("Não há dados climáticos na faixa horária solicitada.")

    temperatures = [
        float(row["temperature_c"])
        for row in selected
        if row.get("temperature_c") is not None
    ]
    clouds = [
        float(row["cloud_cover_percent"])
        for row in selected
        if row.get("cloud_cover_percent") is not None
    ]
    precipitation = sum(
        float(row.get("precipitation_mm") or 0)
        for row in selected
    )

    sunshine_values = [
        float(row["sunshine_seconds"])
        for row in selected
        if row.get("sunshine_seconds") is not None
    ]
    radiation_values = [
        float(row["shortwave_radiation_w_m2"])
        for row in selected
        if row.get("shortwave_radiation_w_m2") is not None
    ]

    cloud_average = (
        sum(clouds) / len(clouds)
        if clouds
        else None
    )
    radiation_wh_m2 = (
        sum(radiation_values)
        if radiation_values
        else None
    )
    sunshine_hours = (
        sum(sunshine_values) / 3600
        if sunshine_values
        else None
    )

    conditions = []
    for row in selected:
        condition = str(row.get("condition_text") or "").strip()
        if condition and condition not in conditions:
            conditions.append(condition)

    return {
        "available": True,
        "date": report_date.isoformat(),
        "start_hour": start_hour,
        "end_hour": end_hour,
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "hours_with_data": len(selected),
        "source": source,
        "average_cloud_cover_percent": (
            round(cloud_average, 1)
            if cloud_average is not None
            else None
        ),
        "total_precipitation_mm": round(precipitation, 2),
        "average_temperature_c": (
            round(sum(temperatures) / len(temperatures), 1)
            if temperatures
            else None
        ),
        "min_temperature_c": (
            round(min(temperatures), 1)
            if temperatures
            else None
        ),
        "max_temperature_c": (
            round(max(temperatures), 1)
            if temperatures
            else None
        ),
        "sunshine_hours": (
            round(sunshine_hours, 2)
            if sunshine_hours is not None
            else None
        ),
        "solar_radiation_wh_m2": (
            round(radiation_wh_m2, 1)
            if radiation_wh_m2 is not None
            else None
        ),
        "conditions": conditions[:4],
        "hourly": [
            {
                "time": row.get("time"),
                "temperature_c": row.get("temperature_c"),
                "precipitation_mm": row.get("precipitation_mm"),
                "cloud_cover_percent": row.get("cloud_cover_percent"),
                "shortwave_radiation_w_m2": row.get("shortwave_radiation_w_m2"),
                "sunshine_minutes": (
                    round(float(row.get("sunshine_seconds") or 0) / 60, 1)
                    if row.get("sunshine_seconds") is not None
                    else None
                ),
                "condition_text": row.get("condition_text"),
            }
            for row in selected
        ],
    }


def get_daily_weather(
    latitude: float,
    longitude: float,
    report_date: date,
) -> dict:
    rows, source = get_hourly_weather_rows(
        latitude=latitude,
        longitude=longitude,
        report_date=report_date,
    )

    temperatures = [
        float(row["temperature_c"])
        for row in rows
        if row.get("temperature_c") is not None
    ]
    clouds = [
        float(row["cloud_cover_percent"])
        for row in rows
        if row.get("cloud_cover_percent") is not None
    ]
    rainfall = sum(float(row.get("precipitation_mm") or 0) for row in rows)
    sunshine_values = [
        float(row["sunshine_seconds"])
        for row in rows
        if row.get("sunshine_seconds") is not None
    ]
    radiation_values = [
        float(row["shortwave_radiation_w_m2"])
        for row in rows
        if row.get("shortwave_radiation_w_m2") is not None
    ]

    cloud_cover = sum(clouds) / len(clouds) if clouds else 0.0
    radiation_wh_m2 = sum(radiation_values) if radiation_values else None
    sunshine_hours = (
        sum(sunshine_values) / 3600
        if sunshine_values
        else None
    )

    return {
        "PERCENTUALNUVENS": round(cloud_cover, 2),
        "CHUVAMM": round(rainfall, 2),
        "RADIACAOSOLARWHM2": (
            round(radiation_wh_m2, 2)
            if radiation_wh_m2 is not None
            else None
        ),
        "HORASSOL": (
            round(sunshine_hours, 2)
            if sunshine_hours is not None
            else None
        ),
        "TEMPERATURAMINIMAC": min(temperatures) if temperatures else None,
        "TEMPERATURAMAXIMAC": max(temperatures) if temperatures else None,
        "CLASSIFICACAOCLIMA": classify_weather(
            cloud_cover,
            rainfall,
            radiation_wh_m2,
        ),
        "PROVEDORCLIMA": source,
        "DADOSBRUTOS": {
            "source": source,
            "hourly": rows,
        },
    }
