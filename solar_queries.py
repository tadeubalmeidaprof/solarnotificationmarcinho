import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from solisclient import SolisAPIError, SolisClient, SolisCredentials


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")
    return value


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _format_kwh(value) -> str:
    number = _to_decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return (
        f"{number:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _find_station(response: dict, station_id: str) -> dict:
    records = (
        response
        .get("data", {})
        .get("page", {})
        .get("records", [])
    )

    for station in records:
        if str(station.get("id")) == str(station_id):
            return station

    raise SolisAPIError(
        f"Usina com ID {station_id} não encontrada na resposta da Solis."
    )


def get_generation_summary() -> dict[str, str]:
    station_id = required_env("SOLIS_STATION_ID")

    client = SolisClient(
        SolisCredentials(
            api_id=required_env("SOLIS_API_ID"),
            api_secret=required_env("SOLIS_API_SECRET"),
            base_url=required_env("SOLIS_BASE_URL"),
            station_id=station_id,
        )
    )

    station = _find_station(client.list_stations(), station_id)

    return {
        "today_kwh": _format_kwh(station.get("dayEnergy", 0)),
        "month_kwh": _format_kwh(station.get("monthEnergy", 0)),
    }
