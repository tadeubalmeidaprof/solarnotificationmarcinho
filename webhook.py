import logging
import os
from threading import Thread

from flask import Flask, jsonify, request

from bot import build_reply
from whatsapp import WhatsAppDeliveryError, send_message


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


def extract_text_messages(payload: dict) -> list[tuple[str, str]]:
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return []

    extracted = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        if message.get("from_me") is True:
            continue

        if message.get("type") != "text":
            continue

        chat_id = str(
            message.get("chat_id")
            or message.get("from")
            or ""
        ).strip()

        text = (
            message.get("text", {})
            if isinstance(message.get("text"), dict)
            else {}
        )
        body = str(text.get("body") or "").strip()

        if chat_id and body:
            extracted.append((chat_id, body))

    return extracted


def process_message(chat_id: str, body: str) -> None:
    try:
        if chat_id != authorized_chat_id():
            logger.info("Mensagem ignorada de chat não autorizado.")
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
    messages = extract_text_messages(payload)

    for chat_id, body in messages:
        Thread(
            target=process_message,
            args=(chat_id, body),
            daemon=True,
        ).start()

    return "", 200
