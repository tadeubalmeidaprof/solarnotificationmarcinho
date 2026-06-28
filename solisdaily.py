import logging
import os
import sys
from typing import Any

import requests

from solis_client import SolisAPIError, SolisClient, SolisCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
REQUEST_TIMEOUT = 15

STATION_STATUS_LABELS = {
    1: "Online ✅",
    2: "Offline ⚠️",
    3: "Em alarme 🚨",
}

REQUIRED_ENV_VARS = (
    "SOLIS_API_ID",
    "SOLIS_API_SECRET",
    "SOLIS_BASE_URL",
    "SOLIS_STATION_ID",
    "CALLMEBOT_PHONE",
    "CALLMEBOT_APIKEY",
)


class WhatsAppDeliveryError(Exception):
    pass


def load_environment() -> dict[str, str]:
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise EnvironmentError(f"Variáveis de ambiente faltando: {', '.join(missing)}")
    return {var: os.environ[var] for var in REQUIRED_ENV_VARS}


def build_message(station_data: dict[str, Any]) -> str:
    info = station_data.get("data", {})

    energy_today = info.get("eToday", "N/D")
    energy_unit = info.get("eTodayStr", "kWh")
    current_power = info.get("pac", "N/D")
    power_unit = info.get("pacStr", "kW")
    status_label = STATION_STATUS_LABELS.get(info.get("state"), "Desconhecido")

    return (
        "* Olá, Marcio! Relatório Diário Da Sua Energia Solar* ☀️\n\n"
        f"Geração hoje: {energy_today} {energy_unit}\n"
        f"Potência atual: {current_power} {power_unit}\n"
        f"Status da usina: {status_label}"
    )


def send_whatsapp_message(message: str, phone: str, api_key: str) -> str:
    params = {"phone": phone, "text": message, "apikey": api_key}
    try:
        response = requests.get(CALLMEBOT_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise WhatsAppDeliveryError("Falha ao enviar mensagem via CallMeBot") from exc
    return response.text


def main() -> int:
    try:
        env = load_environment()
    except EnvironmentError as exc:
        logger.error(exc)
        return 1

    credentials = SolisCredentials(
        api_id=env["SOLIS_API_ID"],
        api_secret=env["SOLIS_API_SECRET"],
        base_url=env["SOLIS_BASE_URL"],
        station_id=env["SOLIS_STATION_ID"],
    )
    client = SolisClient(credentials)

    try:
        station_data = client.get_station_detail()
    except SolisAPIError as exc:
        logger.error(exc)
        return 1

    message = build_message(station_data)
    logger.info("Mensagem gerada:\n%s", message)

    try:
        result = send_whatsapp_message(message, env["CALLMEBOT_PHONE"], env["CALLMEBOT_APIKEY"])
    except WhatsAppDeliveryError as exc:
        logger.error(exc)
        return 1

    logger.info("Mensagem enviada com sucesso: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
