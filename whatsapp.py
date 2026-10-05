import os

import requests


DEFAULT_WHAPI_API_URL = "https://gate.whapi.cloud"
REQUEST_TIMEOUT = 30
MAX_QUICK_REPLY_BUTTONS = 3
MAX_BUTTON_TITLE_LENGTH = 25


class WhatsAppDeliveryError(Exception):
    pass


def _required(value: str | None, env_name: str) -> str:
    resolved = (value or os.getenv(env_name, "")).strip()
    if not resolved:
        raise WhatsAppDeliveryError(
            f"Variável {env_name} não configurada para envio via Whapi."
        )
    return resolved


def _resolve_connection(
    *,
    token: str | None,
    chat_id: str | None,
    api_url: str | None,
) -> tuple[str, str, str]:
    resolved_token = _required(token, "WHAPI_TOKEN")
    resolved_chat_id = _required(chat_id, "WHAPI_CHAT_ID")
    resolved_api_url = (
        api_url
        or os.getenv("WHAPI_API_URL", "")
        or DEFAULT_WHAPI_API_URL
    ).strip().rstrip("/")

    return resolved_token, resolved_chat_id, resolved_api_url


def _post_message(
    *,
    endpoint: str,
    payload: dict,
    token: str,
    api_url: str,
) -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{api_url}{endpoint}",
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

    resolved_token, resolved_chat_id, resolved_api_url = _resolve_connection(
        token=token,
        chat_id=chat_id,
        api_url=api_url,
    )

    return _post_message(
        endpoint="/messages/text",
        payload={
            "to": resolved_chat_id,
            "body": text,
        },
        token=resolved_token,
        api_url=resolved_api_url,
    )


def send_interactive_message(
    message: str,
    *,
    buttons: list[dict[str, str]],
    footer: str | None = None,
    token: str | None = None,
    chat_id: str | None = None,
    api_url: str | None = None,
) -> dict:
    text = str(message or "").strip()
    if not text:
        raise WhatsAppDeliveryError(
            "Mensagem interativa do WhatsApp não pode ser vazia."
        )

    if not buttons or len(buttons) > MAX_QUICK_REPLY_BUTTONS:
        raise WhatsAppDeliveryError(
            "Mensagem interativa deve ter entre 1 e 3 botões."
        )

    normalized_buttons = []
    seen_ids = set()

    for button in buttons:
        if not isinstance(button, dict):
            raise WhatsAppDeliveryError(
                "Cada botão interativo deve ser um objeto com id e title."
            )

        button_id = str(button.get("id") or "").strip()
        title = str(button.get("title") or "").strip()

        if not button_id or not title:
            raise WhatsAppDeliveryError(
                "Cada botão interativo deve ter id e title."
            )

        if len(title) > MAX_BUTTON_TITLE_LENGTH:
            raise WhatsAppDeliveryError(
                "Título de botão interativo excede 25 caracteres."
            )

        if button_id in seen_ids:
            raise WhatsAppDeliveryError(
                "IDs dos botões interativos devem ser únicos."
            )

        seen_ids.add(button_id)
        normalized_buttons.append(
            {
                "type": "quick_reply",
                "title": title,
                "id": button_id,
            }
        )

    resolved_token, resolved_chat_id, resolved_api_url = _resolve_connection(
        token=token,
        chat_id=chat_id,
        api_url=api_url,
    )

    payload = {
        "to": resolved_chat_id,
        "type": "button",
        "body": {"text": text},
        "action": {"buttons": normalized_buttons},
    }

    resolved_footer = str(footer or "").strip()
    if resolved_footer:
        payload["footer"] = {"text": resolved_footer}

    return _post_message(
        endpoint="/messages/interactive",
        payload=payload,
        token=resolved_token,
        api_url=resolved_api_url,
    )
