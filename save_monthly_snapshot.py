import os
from datetime import datetime
from zoneinfo import ZoneInfo

from database import save_monthly_generation_snapshot
from solisdaily import (
    SolisAPIError,
    SolisClient,
    SolisCredentials,
    extract_station_from_list,
    load_environment,
    to_float,
)


REPORT_TIMEZONE = ZoneInfo("America/Bahia")


def main() -> int:
    if not os.getenv("DATABASE_URL", "").strip():
        print("DATABASE_URL não configurada. Snapshot mensal não será salvo.")
        return 0

    env = load_environment()

    credentials = SolisCredentials(
        api_id=env["SOLIS_API_ID"],
        api_secret=env["SOLIS_API_SECRET"],
        base_url=env["SOLIS_BASE_URL"],
        station_id=env["SOLIS_STATION_ID"],
    )

    client = SolisClient(credentials)

    try:
        station_list = client.list_stations()
        station = extract_station_from_list(station_list, env["SOLIS_STATION_ID"])
    except SolisAPIError as exc:
        raise RuntimeError(f"Erro na API Solis ao salvar snapshot mensal: {exc}") from exc

    station_id = str(env["SOLIS_STATION_ID"])
    generation_month_kwh = to_float(station.get("monthEnergy", 0))
    today = datetime.now(REPORT_TIMEZONE).date()

    save_monthly_generation_snapshot(
        station_id=station_id,
        report_date=today,
        generation_kwh=generation_month_kwh,
    )

    print(
        "Snapshot mensal salvo no Supabase:",
        {
            "station_id": station_id,
            "report_date": today.isoformat(),
            "generation_month_kwh": generation_month_kwh,
        },
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
