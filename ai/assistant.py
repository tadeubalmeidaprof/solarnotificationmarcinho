import json
import logging
import os
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai.intent_router import detect_local_intent
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


def _format_slow_degradation_tool_result(
    result: dict,
) -> str | None:
    if not isinstance(result, dict):
        return None

    if not result.get("ok"):
        return (
            "Não consegui analisar a tendência de degradação agora. "
            "Tente novamente em alguns minutos."
        )

    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    if not payload.get("available"):
        status = str(payload.get("status") or "")
        if status == "warming_up":
            observations = payload.get("observations")
            minimum = payload.get("minimum_observations")
            span = payload.get("date_span_days")
            minimum_history_days = payload.get(
                "minimum_history_days",
                45,
            )
            recommended = payload.get(
                "recommended_observations"
            )

            lines = [
                "⏳ *Ainda estou aprendendo o comportamento da sua usina.*"
            ]

            if isinstance(observations, int):
                if isinstance(span, int) and span > 0:
                    lines.append(
                        f"Tenho *{observations} observações válidas*, "
                        f"cobrindo *{span} dias de histórico*."
                    )
                else:
                    lines.append(
                        f"Tenho *{observations} observações válidas* "
                        "até agora."
                    )

            if (
                isinstance(minimum, int)
                and isinstance(minimum_history_days, int)
            ):
                lines.append(
                    "Para analisar degradação lenta com segurança, "
                    f"preciso de pelo menos *{minimum} observações válidas* "
                    f"e *{minimum_history_days} dias de histórico*."
                )

            lines.append(
                "Só entram nessa contagem os dias que passam pelos "
                "critérios mínimos de qualidade e consistência dos dados."
            )

            if isinstance(recommended, int):
                lines.append(
                    f"Com *{recommended} ou mais observações*, "
                    "a análise tende a ficar mais robusta."
                )

            return "\n\n".join(lines)

        reason = str(
            payload.get("reason")
            or "dados insuficientes"
        )
        return (
            "Não consegui concluir a análise de degradação lenta: "
            f"{reason}"
        )

    status = str(payload.get("status") or "inconclusive")
    openings = {
        "stable": (
            "✅ Não encontrei uma tendência consistente de perda "
            "progressiva no histórico disponível."
        ),
        "watch": (
            "🟡 Existem sinais leves de perda progressiva, "
            "mas ainda não são fortes o bastante para concluir degradação."
        ),
        "possible_progressive_loss": (
            "🟠 Há indícios de perda operacional progressiva "
            "que merecem acompanhamento."
        ),
        "probable_progressive_loss": (
            "⚠️ Há sinais estatísticos consistentes de perda "
            "operacional progressiva."
        ),
    }
    opening = openings.get(
        status,
        "Não foi possível classificar a tendência com segurança.",
    )

    details = []
    likelihood = payload.get("degradation_likelihood_percent")
    if isinstance(likelihood, (int, float)):
        details.append(
            f"evidência estatística: {likelihood:.1f}%"
        )

    loss = payload.get("estimated_recent_loss_percent")
    if isinstance(loss, (int, float)):
        details.append(
            f"perda recente estimada: {loss:.2f}%"
        )

    confidence = payload.get("confidence")
    if confidence:
        details.append(
            f"confiança {_confidence_label(str(confidence))}"
        )

    observations = payload.get("observations")
    span = payload.get("date_span_days")
    if (
        isinstance(observations, int)
        and isinstance(span, int)
    ):
        details.append(
            f"{observations} observações em {span} dias"
        )

    dominant = payload.get("dominant_factor")
    if isinstance(dominant, dict):
        label = str(dominant.get("label") or "").strip()
        relative = dominant.get("relative_likelihood_percent")
        if label:
            if isinstance(relative, (int, float)):
                details.append(
                    f"fator mais compatível: {label} ({relative:.1f}% relativo)"
                )
            else:
                details.append(
                    f"fator mais compatível: {label}"
                )

    response = opening
    if details:
        response += "\n\n" + " • ".join(details) + "."

    if (
        payload.get("physical_ageing_assessment")
        == "insufficient_span_for_physical_ageing_claim"
    ):
        response += (
            "\n\nIsso indica perda operacional, se houver, "
            "e não confirma degradação física dos módulos."
        )

    return response



def _pt_number(value, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.{digits}f}".replace(".", ",")


def _short_date_label(value) -> str:
    raw = str(value or "")
    try:
        return datetime.fromisoformat(raw).date().strftime("%d/%m")
    except ValueError:
        return raw or "data consultada"


def _format_performance_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui analisar o desempenho agora."
        )

    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    date_label = _short_date_label(payload.get("date"))

    if not payload.get("available"):
        status = str(payload.get("status") or "")
        if status == "inconclusive_window_in_progress":
            return (
                f"⏳ Ainda é cedo para fechar o desempenho de {date_label}. "
                "A janela solar analisada ainda não terminou."
            )
        reason = str(
            payload.get("reason")
            or "dados insuficientes para uma conclusão segura"
        )
        return (
            f"Não consegui concluir o desempenho de {date_label}: {reason}"
        )

    diagnostic = payload.get("diagnostic")
    if not isinstance(diagnostic, dict):
        return None

    status = str(diagnostic.get("status") or "inconclusive")
    openings = {
        "normal": (
            f"✅ O desempenho da sua usina em {date_label} "
            "ficou próximo do padrão histórico."
        ),
        "weather_likely_explains_reduction": (
            f"🌦️ O desempenho em {date_label} ficou abaixo do padrão, "
            "mas o clima provavelmente explica boa parte da redução."
        ),
        "technical_fault_present": (
            f"⚠️ O desempenho em {date_label} exige atenção porque "
            "há falha técnica ativa registrada."
        ),
        "maintenance_suspected": (
            f"🛠️ O desempenho em {date_label} ficou abaixo do padrão "
            "e há sinais que justificam inspeção/manutenção."
        ),
        "attention": (
            f"🟠 O desempenho em {date_label} ficou abaixo do padrão "
            "e merece acompanhamento."
        ),
        "inconclusive_low_generation_window": (
            f"⏳ Não há dados suficientes em {date_label} para avaliar "
            "o desempenho com confiança."
        ),
        "inconclusive": (
            f"⏳ Ainda não há histórico suficiente para avaliar "
            f"o desempenho de {date_label} com segurança."
        ),
    }
    opening = openings.get(
        status,
        f"Analisei o desempenho de {date_label}.",
    )

    details = []
    daily = payload.get("daily_generation_kwh")
    if isinstance(daily, (int, float)):
        details.append(
            f"geração do dia: *{_pt_number(daily, 2)} kWh*"
        )

    drop = diagnostic.get("drop_percent_vs_baseline")
    if isinstance(drop, (int, float)):
        details.append(
            f"diferença para o histórico: *-{_pt_number(drop, 1)}%*"
            if drop > 0
            else "sem queda relevante frente ao histórico"
        )

    baseline_days = diagnostic.get("baseline_days_used")
    if isinstance(baseline_days, int) and baseline_days > 0:
        details.append(
            f"referência de {baseline_days} dia(s)"
        )

    confidence = diagnostic.get("confidence")
    if confidence:
        details.append(
            f"confiança {_confidence_label(str(confidence))}"
        )

    response = opening
    if details:
        response += "\n\n" + " • ".join(details) + "."

    explanations = diagnostic.get("explanations")
    if isinstance(explanations, list) and explanations:
        first = str(explanations[-1] or "").strip()
        if first:
            response += "\n\n" + first

    return response


def _format_status_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui consultar a situação da usina agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status") or "Sem informação")
    details = []
    power = payload.get("power_now_kw")
    today = payload.get("energy_today_kwh")
    month = payload.get("energy_month_kwh")

    if isinstance(power, (int, float)):
        details.append(f"potência agora: *{_pt_number(power, 2)} kW*")
    if isinstance(today, (int, float)):
        details.append(f"hoje: *{_pt_number(today, 2)} kWh*")
    if isinstance(month, (int, float)):
        details.append(f"mês: *{_pt_number(month, 2)} kWh*")

    response = f"☀️ Status da usina: *{status}*."
    if details:
        response += "\n\n" + " • ".join(details) + "."
    return response


def _format_faults_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui consultar as falhas agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    count = int(payload.get("active_fault_count") or 0)
    faults = payload.get("faults") or []
    if count <= 0:
        return "✅ Não encontrei falhas ativas na usina neste momento."

    lines = [
        f"⚠️ Encontrei *{count} falha(s) ativa(s)*."
    ]
    for fault in faults[:3]:
        if not isinstance(fault, dict):
            continue
        code = str(fault.get("code") or "").strip()
        message = str(fault.get("message") or "Falha registrada").strip()
        label = f"{code}: {message}" if code else message
        lines.append(f"• {label}")
    return "\n".join(lines)


def _format_maintenance_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui consultar a manutenção agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    alerts = payload.get("alerts") or []
    if not payload.get("has_open_alert"):
        return (
            "✅ Não há alerta preventivo de manutenção aberto "
            "para a usina neste momento."
        )

    lines = ["🛠️ Há alerta(s) de manutenção que merecem atenção:"]
    for alert in alerts[:3]:
        if not isinstance(alert, dict):
            continue
        cause = str(alert.get("probable_cause") or "").strip()
        severity = str(alert.get("severity") or "").strip()
        drop = alert.get("drop_percentage")
        parts = []
        if severity:
            parts.append(severity)
        if isinstance(drop, (int, float)):
            parts.append(f"queda {_pt_number(drop, 1)}%")
        if cause:
            parts.append(cause)
        if parts:
            lines.append("• " + " — ".join(parts))
    return "\n".join(lines)


def _format_generation_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui consultar a geração agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    total = payload.get("total_generation_kwh")
    days = int(payload.get("days_with_data") or 0)
    if days <= 0 or not isinstance(total, (int, float)):
        return "Ainda não encontrei dados de geração para esse período."

    start = _short_date_label(payload.get("start_date"))
    end = _short_date_label(payload.get("end_date"))
    if start == end:
        response = (
            f"☀️ Em {start}, sua usina gerou "
            f"*{_pt_number(total, 2)} kWh*."
        )
    else:
        response = (
            f"📊 De {start} a {end}, sua usina gerou "
            f"*{_pt_number(total, 2)} kWh*."
        )

    missing = int(payload.get("missing_days") or 0)
    if missing > 0:
        response += (
            f"\n\nHá {missing} dia(s) sem dado no histórico consultado."
        )
    return response



def _format_weather_impact_tool_result(
    result: dict,
) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui analisar o impacto do clima agora."
        )

    payload = result.get("data")
    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status") or "inconclusive")
    date_label = _short_date_label(payload.get("date"))

    openings = {
        "weather_likely_affected": (
            f"🌦️ Sim. O clima de {date_label} provavelmente contribuiu "
            "para reduzir a geração."
        ),
        "weather_may_have_affected": (
            f"🌥️ O clima de {date_label} pode ter contribuído para a "
            "redução da geração, mas a evidência ainda não é conclusiva."
        ),
        "weather_unlikely_to_explain": (
            f"☀️ O clima de {date_label} não parece explicar sozinho a "
            "queda de geração observada."
        ),
        "no_clear_weather_impact": (
            f"✅ Não encontrei sinal claro de impacto climático relevante "
            f"na geração de {date_label}."
        ),
        "weather_context_only": (
            f"🌦️ Tenho os dados climáticos de {date_label}, mas ainda não "
            "há histórico de geração suficiente para estimar o impacto com segurança."
        ),
        "weather_data_unavailable": (
            f"Não consegui obter dados climáticos suficientes para {date_label}."
        ),
        "inconclusive": (
            f"🌦️ O clima pode ter influenciado a geração de {date_label}, "
            "mas os dados atuais não permitem concluir com segurança."
        ),
    }
    response = openings.get(
        status,
        "Não foi possível classificar o impacto climático com segurança.",
    )

    details = []
    generation = payload.get("generation_kwh")
    baseline = payload.get("recent_baseline_generation_kwh")
    drop = payload.get("generation_drop_percent")
    confidence = payload.get("confidence")
    weather = payload.get("weather") or {}

    if isinstance(generation, (int, float)):
        details.append(
            f"geração: *{_pt_number(generation, 2)} kWh*"
        )
    if isinstance(baseline, (int, float)):
        details.append(
            f"mediana recente: *{_pt_number(baseline, 2)} kWh*"
        )
    if isinstance(drop, (int, float)):
        details.append(
            f"diferença: *-{_pt_number(drop, 1)}%*"
            if drop > 0
            else "sem queda relevante frente ao histórico"
        )

    cloud = weather.get("average_cloud_cover_percent")
    rain = weather.get("total_precipitation_mm")
    sunshine = weather.get("sunshine_hours")

    if isinstance(cloud, (int, float)):
        details.append(
            f"nebulosidade média: {_pt_number(cloud, 0)}%"
        )
    if isinstance(rain, (int, float)) and rain > 0:
        details.append(
            f"chuva: {_pt_number(rain, 1)} mm"
        )
    if isinstance(sunshine, (int, float)):
        details.append(
            f"sol na janela: {_pt_number(sunshine, 1)} h"
        )
    if confidence:
        details.append(
            f"confiança {_confidence_label(str(confidence))}"
        )

    if details:
        response += "\n\n" + " • ".join(details) + "."

    response += (
        "\n\nEssa análise indica compatibilidade entre clima e geração; "
        "não prova causalidade sozinha."
    )
    return response

def _format_weather_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui consultar o clima agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None
    if not payload.get("available"):
        return "Não há dados climáticos suficientes para esse período."

    details = []
    cloud = payload.get("average_cloud_cover_percent")
    rain = payload.get("total_precipitation_mm")
    temp = payload.get("average_temperature_c")

    if isinstance(cloud, (int, float)):
        details.append(f"nuvens: {_pt_number(cloud, 0)}%")
    if isinstance(rain, (int, float)):
        details.append(f"chuva: {_pt_number(rain, 1)} mm")
    if isinstance(temp, (int, float)):
        details.append(f"temperatura média: {_pt_number(temp, 1)} °C")

    date_label = _short_date_label(payload.get("date"))
    response = f"🌦️ Clima da usina em {date_label}"
    if details:
        response += ":\n" + " • ".join(details) + "."
    else:
        response += "."
    return response


def _format_savings_tool_result(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        return str(
            result.get("error")
            or "Não consegui calcular a economia agora."
        )
    payload = result.get("data")
    if not isinstance(payload, dict):
        return None
    if not payload.get("available"):
        return (
            "Ainda não tenho os dados necessários para calcular "
            "a economia desse mês."
        )

    savings = payload.get("estimated_savings_brl")
    generation = payload.get("generation_kwh")
    if not isinstance(savings, (int, float)):
        return None

    response = (
        f"💰 Economia estimada no mês: *R$ {_pt_number(savings, 2)}*."
    )
    if isinstance(generation, (int, float)):
        response += (
            f"\n\nGeração considerada: {_pt_number(generation, 2)} kWh."
        )
    return response


def _format_standard_tool_result(
    tool_name: str,
    result: dict,
) -> str | None:
    formatters = {
        "diagnosticar_desempenho_diario": _format_performance_tool_result,
        "consultar_resumo_usina": _format_status_tool_result,
        "consultar_falhas_ativas": _format_faults_tool_result,
        "consultar_manutencao": _format_maintenance_tool_result,
        "consultar_geracao_periodo": _format_generation_tool_result,
        "consultar_clima": _format_weather_tool_result,
        "analisar_impacto_clima": _format_weather_impact_tool_result,
        "consultar_economia_mes": _format_savings_tool_result,
    }
    formatter = formatters.get(tool_name)
    return formatter(result) if formatter else None


def _try_local_common_intent(
    user_message: str,
    chat_id: str,
) -> str | None:
    intent = detect_local_intent(
        user_message,
        today=datetime.now(RUNTIME_TIMEZONE).date(),
    )
    if not intent:
        return None

    tool_name = str(intent.get("tool") or "")
    arguments = intent.get("arguments")
    if not tool_name or not isinstance(arguments, dict):
        return None

    logger.info(
        "Roteamento local de intenção: %s",
        intent.get("intent"),
    )
    result = _safe_tool_result(
        tool_name,
        arguments,
    )
    reply = _format_standard_tool_result(
        tool_name,
        result,
    )

    if reply:
        save_conversation_exchange(
            chat_id=chat_id,
            user_message=user_message,
            assistant_message=reply,
        )

    return reply

def _tool_fallback(tool_name: str, result: dict) -> str | None:
    if tool_name == "analisar_curva_geracao":
        return _format_curve_tool_result(result)

    if tool_name == "consultar_degradacao_lenta":
        return _format_slow_degradation_tool_result(result)

    standard = _format_standard_tool_result(
        tool_name,
        result,
    )
    if standard:
        return standard

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


def _try_local_degradation_intent(
    user_message: str,
    chat_id: str,
) -> str | None:
    normalized = _normalize_intent_text(user_message)

    direct_terms = {
        "degradacao",
        "degradando",
    }
    phrases = (
        "perdendo rendimento",
        "perda de rendimento",
        "rendimento caindo",
        "queda de rendimento",
        "perdendo eficiencia",
        "perda de eficiencia",
        "menos eficiente",
        "perda gradual",
        "queda gradual",
        "piora gradual",
        "piorando com o tempo",
        "perdendo desempenho",
        "perda de desempenho",
    )

    if (
        not any(term in normalized for term in direct_terms)
        and not any(phrase in normalized for phrase in phrases)
    ):
        return None

    logger.info(
        "Atalho local para análise de degradação lenta."
    )
    result = _safe_tool_result(
        "consultar_degradacao_lenta",
        {},
    )
    reply = _format_slow_degradation_tool_result(
        result
    )

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

    local_degradation_reply = _try_local_degradation_intent(
        user_message,
        chat_id,
    )
    if local_degradation_reply:
        return local_degradation_reply

    local_reply = _try_local_curve_intent(
        user_message,
        chat_id,
    )
    if local_reply:
        return local_reply

    local_common_reply = _try_local_common_intent(
        user_message,
        chat_id,
    )
    if local_common_reply:
        return local_common_reply

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
