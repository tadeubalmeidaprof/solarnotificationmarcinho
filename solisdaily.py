import logging
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from solis_client import SolisAPIError, SolisClient, SolisCredentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
REPORT_TIMEZONE = ZoneInfo("America/Bahia")

REQUIRED_ENV_VARS = (
    "SOLIS_API_ID",
    "SOLIS_API_SECRET",
    "SOLIS_BASE_URL",
    "SOLIS_STATION_ID",
    "GREEN_API_URL",
    "GREEN_API_ID_INSTANCE",
    "GREEN_API_TOKEN_INSTANCE",
    "GREEN_API_PHONE",
)


class WhatsAppDeliveryError(Exception):
    pass


def load_environment() -> dict[str, str]:
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente faltando: {', '.join(missing)}"
        )
    return {var: os.environ[var].strip() for var in REQUIRED_ENV_VARS}


def extract_station_from_list(
    response: dict[str, Any],
    station_id: str,
) -> dict[str, Any]:
    records = (
        response
        .get("data", {})
        .get("page", {})
        .get("records", [])
    )
    if not records:
        raise SolisAPIError("Nenhuma usina encontrada na resposta da Solis.")
    for station in records:
        if str(station.get("id")) == str(station_id):
            return station
    raise SolisAPIError(
        f"Usina com ID {station_id} não encontrada na resposta da Solis."
    )


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_message(station: dict[str, Any], report_date: str) -> str:
    energy_today = to_float(station.get("dayEnergy", 0))
    energy_month = to_float(station.get("monthEnergy", 0))
    return (
        "☀️ *Olá, Marcio! Aqui está seu Relatório Solar*\n\n"
        f"Data: {report_date}\n"
        f"Geração hoje: {energy_today:.2f} kWh\n"
        f"Geração no mês: {energy_month:.2f} kWh"
    )


def send_whatsapp_message(
    message: str,
    phone: str,
    api_url: str,
    id_instance: str,
    token_instance: str,
) -> str:
    url = f"{api_url.rstrip('/')}/waInstance{id_instance}/sendMessage/{token_instance}"
    payload = {
        "chatId": f"{phone}@c.us",
        "message": message,
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise WhatsAppDeliveryError(
            "Falha ao enviar mensagem via Green API"
        ) from exc
    return response.text


def main() -> int:
    try:
        env = load_environment()
    except EnvironmentError as exc:
        logger.error("%s", exc)
        return 1

    credentials = SolisCredentials(
        api_id=env["SOLIS_API_ID"],
        api_secret=env["SOLIS_API_SECRET"],
        base_url=env["SOLIS_BASE_URL"],
        station_id=env["SOLIS_STATION_ID"],
    )
    client = SolisClient(credentials)

    try:
        logger.info("Buscando lista de usinas na SolisCloud...")
        station_list = client.list_stations()
        station = extract_station_from_list(
            station_list,
            env["SOLIS_STATION_ID"],
        )
    except SolisAPIError as exc:
        logger.error("Erro na API Solis: %s", exc)
        return 1

    now = datetime.now(REPORT_TIMEZONE)
    report_date = now.strftime("%d/%m/%Y às %H:%M")
    message = build_message(station, report_date)
    logger.info("Mensagem gerada:\n%s", message)

    try:
        result = send_whatsapp_message(
            message=message,
            phone=env["GREEN_API_PHONE"],
            api_url=env["GREEN_API_URL"],
            id_instance=env["GREEN_API_ID_INSTANCE"],
            token_instance=env["GREEN_API_TOKEN_INSTANCE"],
        )
    except WhatsAppDeliveryError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Mensagem enviada com sucesso: %s", result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Falha inesperada na execução do relatório diário")
        sys.exit(1)
