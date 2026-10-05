import logging
import os
from threading import Thread

from flask import Flask, jsonify, request

from bot import (
    MENU_BODY,
    MENU_BUTTONS,
    MENU_FOOTER,
    MENU_MESSAGE,
    build_reply,
    is_menu_request,
)
from whatsapp import (
    WhatsAppDeliveryError,
    send_interactive_message,
    send_message,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def authorized_chat_id() -> str:
    value = os.getenv("AUTHORIZED_CHAT_ID", "").strip()
    if not value:
        raise RuntimeError("Variável AUTHORIZED_CHAT_ID não configurada.")
    return value


def _message_chat_id(message: dict) -> str:
    return str(
        message.get("chat_id")
        or message.get("from")
        or ""
    ).strip()


def _extract_button_reply_id(message: dict) -> str:
    if message.get("type") != "reply":
        return ""

    reply = (
        message.get("reply", {})
        if isinstance(message.get("reply"), dict)
        else {}
    )
    if reply.get("type") != "buttons_reply":
        return ""

    buttons_reply = (
        reply.get("buttons_reply", {})
        if isinstance(reply.get("buttons_reply"), dict)
        else {}
    )
    button_id = str(buttons_reply.get("id") or "").strip()
    if not button_id:
        return ""

    if ":" in button_id:
        prefix, value = button_id.split(":", 1)
        if prefix.lower().startswith("buttons"):
            button_id = value.strip()

    return button_id


def extract_incoming_messages(payload: dict) -> list[tuple[str, str]]:
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return []

    extracted = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        if message.get("from_me") is True:
            continue

        chat_id = _message_chat_id(message)
        if not chat_id:
            continue

        body = ""

        if message.get("type") == "text":
            text = (
                message.get("text", {})
                if isinstance(message.get("text"), dict)
                else {}
            )
            body = str(text.get("body") or "").strip()
        elif message.get("type") == "reply":
            body = _extract_button_reply_id(message)

        if body:
            extracted.append((chat_id, body))

    return extracted


def extract_text_messages(payload: dict) -> list[tuple[str, str]]:
    """Compatibilidade com o nome usado no primeiro MVP."""
    return extract_incoming_messages(payload)


def _send_menu(chat_id: str) -> None:
    try:
        send_interactive_message(
            MENU_BODY,
            buttons=MENU_BUTTONS,
            footer=MENU_FOOTER,
            chat_id=chat_id,
        )
        logger.info("Menu interativo do bot enviado com sucesso.")
    except WhatsAppDeliveryError:
        logger.exception(
            "Falha ao enviar menu interativo; usando fallback textual."
        )
        send_message(MENU_MESSAGE, chat_id=chat_id)
        logger.info("Menu textual de fallback enviado com sucesso.")


def process_message(chat_id: str, body: str) -> None:
    try:
        if chat_id != authorized_chat_id():
            logger.info("Mensagem ignorada de chat não autorizado.")
            return

        if is_menu_request(body):
            _send_menu(chat_id)
            return

        reply = build_reply(body)
        send_message(reply, chat_id=chat_id)
        logger.info("Resposta do bot enviada com sucesso.")
    except WhatsAppDeliveryError:
        logger.exception("Falha ao enviar resposta pela Whapi.")
    except Exception:
        logger.exception("Falha ao processar mensagem recebida.")


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/webhook/whapi")
def whapi_webhook():
    payload = request.get_json(silent=True) or {}
    messages = extract_incoming_messages(payload)

    for chat_id, body in messages:
        Thread(
            target=process_message,
            args=(chat_id, body),
            daemon=True,
        ).start()

    return "", 200
