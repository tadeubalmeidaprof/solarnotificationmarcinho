import hashlib
import logging
import os

from database import (
    fetch_ai_conversation_messages,
    prune_ai_conversation_messages,
    save_ai_conversation_message,
)


logger = logging.getLogger(__name__)
MAX_HISTORY_MESSAGES = 8
MAX_STORED_MESSAGES = 12


def _enabled() -> bool:
    value = os.getenv("AI_MEMORY_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on", "sim"}


def _conversation_key(chat_id: str) -> str:
    return hashlib.sha256(
        str(chat_id or "").strip().encode("utf-8")
    ).hexdigest()


def load_recent_messages(chat_id: str) -> list[dict[str, str]]:
    if not _enabled() or not str(chat_id or "").strip():
        return []

    try:
        messages = fetch_ai_conversation_messages(
            conversation_key=_conversation_key(chat_id),
            limit=MAX_HISTORY_MESSAGES,
        )
    except Exception:
        logger.exception("Falha ao carregar memória curta da IA.")
        return []

    return [
        {
            "role": message["role"],
            "content": str(message["content"])[:2500],
        }
        for message in messages
        if message.get("role") in {"user", "assistant"}
        and str(message.get("content") or "").strip()
    ]


def save_conversation_exchange(
    chat_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    if not _enabled() or not str(chat_id or "").strip():
        return

    conversation_key = _conversation_key(chat_id)

    try:
        save_ai_conversation_message(
            conversation_key=conversation_key,
            role="user",
            content=str(user_message)[:2500],
        )
        save_ai_conversation_message(
            conversation_key=conversation_key,
            role="assistant",
            content=str(assistant_message)[:4000],
        )
        prune_ai_conversation_messages(
            conversation_key=conversation_key,
            keep=MAX_STORED_MESSAGES,
        )
    except Exception:
        logger.exception("Falha ao salvar memória curta da IA.")
