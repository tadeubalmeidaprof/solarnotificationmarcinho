import json
import logging
import os
import unicodedata
from datetime import datetime, timedelta
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


def _normalize_intent_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    return " ".join(text.lower().strip().split())


def _confidence_label(value: str) -> str:
    return {
        "high": "alta",
        "moderate": "moderada",
        "low": "baixa",
        "very_low": "muito baixa",
    }.get(str(value or "").lower(), str(value or "indefinida"))


def _format_curve_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None

    if not result.get("ok"):
        return (
            "Não consegui consultar a curva agora. "
            "Tente novamente em alguns minutos."
        )

    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        return None

    if not analysis.get("available"):
        reason = str(
            analysis.get("reason")
            or payload.get("reason")
            or "dados insuficientes"
        )
        return f"Não consegui concluir a análise da curva: {reason}"

    raw_date = str(payload.get("date") or "")
    try:
        parsed_date = datetime.fromisoformat(raw_date).date()
        date_label = parsed_date.strftime("%d/%m")
    except ValueError:
        date_label = raw_date or "dia consultado"

    status = str(analysis.get("status") or "inconclusive")
    if status == "normal":
        opening = (
            f"✅ A curva de {date_label} ficou dentro do padrão histórico "
            "da própria usina."
        )
    elif status == "minor_variation":
        opening = (
            f"🟡 A curva de {date_label} teve pequenas variações, "
            "sem anomalia relevante no resultado geral."
        )
    elif status == "attention":
        opening = (
            f"🟠 A curva de {date_label} apresentou desvios que merecem "
            "acompanhamento."
        )
    elif status == "anomaly_detected":
        opening = (
            f"⚠️ A curva de {date_label} apresentou uma anomalia relevante."
        )
    elif status == "partial_day":
        opening = (
            f"⏳ A curva de {date_label} ainda está parcial, então a análise "
            "final não é confiável."
        )
    else:
        opening = (
            f"Não foi possível concluir com segurança a curva de {date_label}."
        )

    details = []
    score = analysis.get("anomaly_score")
    if isinstance(score, (int, float)):
        details.append(f"Score de anomalia: {score:.1f}/100")

    confidence = analysis.get("confidence")
    if confidence:
        details.append(
            f"confiança {_confidence_label(str(confidence))}"
        )

    baseline_days = analysis.get("baseline_days_used")
    if isinstance(baseline_days, int) and baseline_days > 0:
        details.append(
            f"baseline de {baseline_days} dias históricos"
        )

    persistence = analysis.get("persistence") or {}
    if isinstance(persistence, dict) and persistence.get("persistent"):
        persistent_days = persistence.get("persistent_days")
        comparable_days = persistence.get("comparable_days")
        if persistent_days and comparable_days:
            details.append(
                f"padrão persistente em {persistent_days}/{comparable_days} "
                "dias comparáveis"
            )

    anomalies = analysis.get("anomalies") or []
    if (
        status == "normal"
        and isinstance(anomalies, list)
        and anomalies
    ):
        details.append(
            f"{len(anomalies)} sinal(is) de baixa relevância sem elevar "
            "o diagnóstico geral"
        )

    if details:
        return opening + "\n\n" + " • ".join(details) + "."

    return opening


def _tool_fallback(tool_name: str, result: dict) -> str | None:
    if tool_name == "analisar_curva_geracao":
        return _format_curve_tool_result(result)

    if isinstance(result, dict) and not result.get("ok"):
        return str(
            result.get("error")
            or "Não foi possível consultar esse dado agora."
        )

    return None


def _try_local_curve_intent(
    user_message: str,
    chat_id: str,
) -> str | None:
    normalized = _normalize_intent_text(user_message)
    if "curva" not in normalized:
        return None

    intent_words = {
        "anomalia",
        "anormal",
        "estranha",
        "queda",
        "oscilacao",
        "pico",
        "irregular",
    }
    if not any(word in normalized for word in intent_words):
        return None

    today = datetime.now(RUNTIME_TIMEZONE).date()
    if "ontem" in normalized:
        target = today - timedelta(days=1)
    elif "hoje" in normalized:
        target = today
    else:
        return None

    logger.info(
        "Atalho local para análise de curva: %s",
        target.isoformat(),
    )
    result = _safe_tool_result(
        "analisar_curva_geracao",
        {"report_date": target.isoformat()},
    )
    reply = _format_curve_tool_result(result)

    if reply:
        save_conversation_exchange(
            chat_id=chat_id,
            user_message=user_message,
            assistant_message=reply,
        )

    return reply


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

    local_reply = _try_local_curve_intent(
        user_message,
        chat_id,
    )
    if local_reply:
        return local_reply

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": _runtime_context()},
    ]
    messages.extend(load_recent_messages(chat_id))
    messages.append({"role": "user", "content": user_message})

    last_tool_name = ""
    last_tool_result = None

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
                    last_tool_name = tool_name
                    last_tool_result = result
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
        fallback = _tool_fallback(
            last_tool_name,
            last_tool_result,
        )
        if fallback:
            save_conversation_exchange(
                chat_id=chat_id,
                user_message=user_message,
                assistant_message=fallback,
            )
            return fallback
    except AIProviderError as exc:
        logger.warning("Groq indisponível; usando fallback: %s", exc)
        fallback = _tool_fallback(
            last_tool_name,
            last_tool_result,
        )
        if fallback:
            save_conversation_exchange(
                chat_id=chat_id,
                user_message=user_message,
                assistant_message=fallback,
            )
            return fallback
    except Exception:
        logger.exception("Falha inesperada no assistente de IA; usando fallback.")
        fallback = _tool_fallback(
            last_tool_name,
            last_tool_result,
        )
        if fallback:
            save_conversation_exchange(
                chat_id=chat_id,
                user_message=user_message,
                assistant_message=fallback,
            )
            return fallback

    return None
