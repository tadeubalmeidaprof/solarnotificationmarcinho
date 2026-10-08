import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from ai.memory import load_recent_messages, save_conversation_exchange
from ai.prompt import SYSTEM_PROMPT
from ai.provider import AIProviderError, AIRateLimitError, create_chat_completion
from ai.tools import AIToolError, TOOL_DEFINITIONS, execute_tool


logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 3
MAX_INPUT_CHARS = 1200
RUNTIME_TIMEZONE = ZoneInfo("America/Bahia")


def _enabled() -> bool:
    value = os.getenv("AI_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on", "sim"}


def _provider_supported() -> bool:
    provider = os.getenv("AI_PROVIDER", "groq").strip().lower() or "groq"
    return provider == "groq"


def _safe_tool_result(tool_name: str, arguments: dict) -> dict:
    try:
        return {
            "ok": True,
            "data": execute_tool(tool_name, arguments),
        }
    except AIToolError as exc:
        logger.warning("Tool de IA recusada: %s", exc)
        return {"ok": False, "error": "Consulta inválida para esta ferramenta."}
    except Exception:
        logger.exception("Falha ao executar tool de IA: %s", tool_name)
        return {
            "ok": False,
            "error": "Não foi possível consultar esse dado agora.",
        }


def _parse_arguments(raw_arguments) -> dict:
    if raw_arguments in (None, ""):
        return {}

    if isinstance(raw_arguments, dict):
        return raw_arguments

    try:
        parsed = json.loads(str(raw_arguments))
    except json.JSONDecodeError as exc:
        raise AIToolError("Argumentos de ferramenta inválidos.") from exc

    if not isinstance(parsed, dict):
        raise AIToolError("Argumentos de ferramenta devem ser um objeto JSON.")

    return parsed


def _runtime_context() -> str:
    now = datetime.now(RUNTIME_TIMEZONE)
    return (
        "Contexto temporal do SolCare: "
        f"agora é {now.isoformat()}, fuso America/Bahia. "
        "Use essa referência para resolver datas relativas."
    )


def ask_solcare_ai(message: str, chat_id: str = "") -> str | None:
    if not _enabled():
        return None

    if not _provider_supported():
        logger.error("AI_PROVIDER não suportado; esperado: groq.")
        return None

    user_message = str(message or "").strip()
    if not user_message:
        return None

    if len(user_message) > MAX_INPUT_CHARS:
        user_message = user_message[:MAX_INPUT_CHARS]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": _runtime_context()},
    ]
    messages.extend(load_recent_messages(chat_id))
    messages.append({"role": "user", "content": user_message})

    try:
        for tool_round in range(MAX_TOOL_ROUNDS + 1):
            response_message = create_chat_completion(
                messages=messages,
                tools=TOOL_DEFINITIONS,
            )

            tool_calls = response_message.get("tool_calls") or []
            if not tool_calls:
                content = str(response_message.get("content") or "").strip()
                if not content:
                    raise AIProviderError("Groq retornou uma resposta vazia.")

                save_conversation_exchange(
                    chat_id=chat_id,
                    user_message=user_message,
                    assistant_message=content,
                )
                return content

            if tool_round >= MAX_TOOL_ROUNDS:
                raise AIProviderError("Limite de chamadas de ferramentas excedido.")

            assistant_message = {
                "role": "assistant",
                "content": response_message.get("content"),
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    raise AIProviderError("Groq retornou uma tool call inválida.")

                function = tool_call.get("function") or {}
                tool_name = str(function.get("name") or "").strip()
                tool_call_id = str(tool_call.get("id") or "").strip()

                if not tool_name or not tool_call_id:
                    raise AIProviderError("Groq retornou uma tool call incompleta.")

                try:
                    arguments = _parse_arguments(function.get("arguments"))
                    logger.info("Executando tool de IA: %s", tool_name)
                    result = _safe_tool_result(tool_name, arguments)
                except AIToolError:
                    result = {
                        "ok": False,
                        "error": "A ferramenta recebeu parâmetros inválidos.",
                    }

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            result,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )

        raise AIProviderError("Limite de chamadas de ferramentas excedido.")
    except AIRateLimitError:
        logger.warning("Groq atingiu o limite de requisições; usando fallback.")
    except AIProviderError as exc:
        logger.warning("Groq indisponível; usando fallback: %s", exc)
    except Exception:
        logger.exception("Falha inesperada no assistente de IA; usando fallback.")

    return None
