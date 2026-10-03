import os

import requests


DEFAULT_WHAPI_API_URL = "https://gate.whapi.cloud"
REQUEST_TIMEOUT = 30


class WhatsAppDeliveryError(Exception):
    pass


def _required(value: str | None, env_name: str) -> str:
    resolved = (value or os.getenv(env_name, "")).strip()
    if not resolved:
        raise WhatsAppDeliveryError(
            f"Variável {env_name} não configurada para envio via Whapi."
        )
    return resolved


def send_message(
    message: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    api_url: str | None = None,
) -> dict:
    text = str(message or "").strip()
    if not text:
        raise WhatsAppDeliveryError("Mensagem do WhatsApp não pode ser vazia.")

    resolved_token = _required(token, "WHAPI_TOKEN")
    resolved_chat_id = _required(chat_id, "WHAPI_CHAT_ID")
    resolved_api_url = (
        api_url
        or os.getenv("WHAPI_API_URL", "")
        or DEFAULT_WHAPI_API_URL
    ).strip().rstrip("/")

    url = f"{resolved_api_url}/messages/text"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {resolved_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": resolved_chat_id,
        "body": text,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise WhatsAppDeliveryError(
            "Falha ao enviar mensagem via Whapi."
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise WhatsAppDeliveryError(
            "Whapi respondeu sem JSON válido."
        ) from exc

    sent_message = data.get("sent_message")
    message_id = (
        sent_message.get("id")
        if isinstance(sent_message, dict)
        else None
    )

    if not message_id:
        raise WhatsAppDeliveryError(
            "Whapi não confirmou o envio com sent_message.id."
        )

    return data
