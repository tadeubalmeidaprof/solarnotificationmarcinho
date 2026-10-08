import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from database import (
    confirm_maintenance_alert,
    create_maintenance_alert,
    expire_stale_pending_maintenance_alerts,
    fetch_monitoring_history,
    fetch_open_maintenance_alert,
    mark_integrator_notified,
    resolve_open_maintenance_alerts,
    resolve_other_open_maintenance_alerts,
    save_daily_generation,
    save_daily_weather,
)
from maintenance import analyze_maintenance_need
from solisdaily import (
    SolisAPIError,
    SolisClient,
    SolisCredentials,
    extract_station_from_list,
    load_environment,
    to_float,
)
from weather import get_daily_weather
from whatsapp import send_message


REPORT_TIMEZONE = ZoneInfo("America/Bahia")
PROVIDER = "Solis"

STATE_LABELS = {
    1: "Normal",
    2: "Offline",
    3: "Alarme",
}


def decimal_env(name: str, default: str) -> Decimal:
    value = os.getenv(name, "").strip() or default
    return Decimal(value.replace(",", "."))


def integer_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value or default)


def format_number(value, decimals: int = 1) -> str:
    return (
        f"{float(value):,.{decimals}f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _coordinates(station: dict) -> tuple[float, float]:
    latitude_raw = os.getenv("STATION_LATITUDE", "").strip()
    longitude_raw = os.getenv("STATION_LONGITUDE", "").strip()

    if latitude_raw and longitude_raw:
        return (
            float(latitude_raw.replace(",", ".")),
            float(longitude_raw.replace(",", ".")),
        )

    latitude = station.get("latitude")
    longitude = station.get("longitude")
    if latitude in (None, "") or longitude in (None, ""):
        raise RuntimeError(
            "Localização da usina não disponível. "
            "Configure STATION_LATITUDE e STATION_LONGITUDE."
        )

    return float(latitude), float(longitude)


def build_integrator_alert_message(
    alert: dict,
    station_id: str,
) -> str:
    return f"""🔎 SolCare — Análise preventiva de desempenho

Usina: {station_id}

Foi identificada uma redução persistente de {format_number(alert['drop_percentage'], 1)}% em dias com condições climáticas favoráveis.

Geração diária esperada: {format_number(alert['expected_generation_kwh'], 1)} kWh
Geração diária observada: {format_number(alert['observed_generation_kwh'], 1)} kWh
Dias favoráveis analisados: {alert['favorable_days_count']}

Possíveis causas:
{alert['probable_cause']}

Recomendamos uma inspeção técnica antes de qualquer intervenção.
"""


def main() -> None:
    env = load_environment()
    station_id = str(env["SOLIS_STATION_ID"])

    client = SolisClient(
        SolisCredentials(
            api_id=env["SOLIS_API_ID"],
            api_secret=env["SOLIS_API_SECRET"],
            base_url=env["SOLIS_BASE_URL"],
            station_id=station_id,
        )
    )

    try:
        station = extract_station_from_list(
            client.list_stations(),
            station_id,
        )
    except SolisAPIError as exc:
        raise RuntimeError(
            f"Falha ao consultar a SolisCloud: {exc}"
        ) from exc

    now = datetime.now(REPORT_TIMEZONE)
    report_date = now.date()

    try:
        state = int(station.get("state"))
    except (TypeError, ValueError):
        state = None

    save_daily_generation(
        provider=PROVIDER,
        station_id=station_id,
        report_date=report_date,
        generation_day_kwh=to_float(
            station.get("dayEnergy", 0)
        ),
        generation_month_kwh=to_float(
            station.get("monthEnergy", 0)
        ),
        inverter_status=STATE_LABELS.get(
            state,
            str(station.get("state") or ""),
        ),
        device_sn="",
    )

    latitude, longitude = _coordinates(station)
    weather = get_daily_weather(
        latitude=latitude,
        longitude=longitude,
        report_date=report_date,
    )

    save_daily_weather(
        provider=PROVIDER,
        station_id=station_id,
        report_date=report_date,
        latitude=latitude,
        longitude=longitude,
        weather=weather,
    )

    print(
        "Monitoramento diário Solis salvo:",
        {
            "station_id": station_id,
            "report_date": report_date.isoformat(),
            "generation_day_kwh": station.get("dayEnergy", 0),
            "generation_month_kwh": station.get("monthEnergy", 0),
            "weather_class": weather.get("CLASSIFICACAOCLIMA"),
            "radiation_wh_m2": weather.get("RADIACAOSOLARWHM2"),
            "rain_mm": weather.get("CHUVAMM"),
        },
    )

    confirmation_days = integer_env(
        "MAINTENANCE_CONFIRMATION_DAYS",
        5,
    )
    expire_stale_pending_maintenance_alerts(
        provider=PROVIDER,
        station_id=station_id,
        confirmation_days=confirmation_days,
    )

    history = fetch_monitoring_history(
        provider=PROVIDER,
        station_id=station_id,
        limit=integer_env(
            "MAINTENANCE_HISTORY_DAYS",
            45,
        ),
    )

    analysis = analyze_maintenance_need(
        history=history,
        minimum_recent_days=integer_env(
            "MAINTENANCE_RECENT_DAYS",
            4,
        ),
        minimum_baseline_days=integer_env(
            "MAINTENANCE_BASELINE_DAYS",
            7,
        ),
        drop_threshold_percent=decimal_env(
            "MAINTENANCE_DROP_PERCENT",
            "25",
        ),
        minimum_radiation_wh_m2=decimal_env(
            "MAINTENANCE_MIN_RADIATION_WH_M2",
            "3000",
        ),
    )

    print("Resultado da análise preventiva Solis:", analysis)

    if not analysis.get("alert"):
        if analysis.get("reason") == "drop_below_threshold":
            resolve_open_maintenance_alerts(
                provider=PROVIDER,
                station_id=station_id,
            )
        return

    alert_type = str(analysis["alert_type"])

    resolve_other_open_maintenance_alerts(
        provider=PROVIDER,
        station_id=station_id,
        current_alert_type=alert_type,
    )

    existing_alert = fetch_open_maintenance_alert(
        provider=PROVIDER,
        station_id=station_id,
        alert_type=alert_type,
    )

    if not existing_alert:
        alert_id = create_maintenance_alert(
            provider=PROVIDER,
            station_id=station_id,
            alert=analysis,
        )
        print(
            f"Alerta {alert_id} criado como pending_confirmation."
        )
        return

    alert_id = int(existing_alert["id"])
    status = str(existing_alert["status"])

    if status == "integrator_notified":
        return

    if status == "pending_confirmation":
        previous_reference_end = existing_alert.get(
            "reference_end_date"
        )
        current_reference_end = analysis.get(
            "reference_end_date"
        )

        if (
            previous_reference_end
            and current_reference_end
            and current_reference_end <= previous_reference_end
        ):
            return

        confirm_maintenance_alert(
            alert_id,
            analysis,
        )
        status = "confirmed"

    if status != "confirmed":
        raise RuntimeError(
            f"Estado inesperado do alerta {alert_id}: {status}"
        )

    send_message(
        build_integrator_alert_message(
            analysis,
            station_id,
        )
    )
    mark_integrator_notified(alert_id)


if __name__ == "__main__":
    main()
