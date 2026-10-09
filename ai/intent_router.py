from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    return " ".join(text.split())


def _tokens(normalized: str) -> list[str]:
    return normalized.split()


def _token_like(token: str, variants: tuple[str, ...]) -> bool:
    for variant in variants:
        if token == variant:
            return True
        if len(variant) >= 5 and token.startswith(variant):
            return True
        if (
            len(token) >= 5
            and len(variant) >= 5
            and SequenceMatcher(None, token, variant).ratio() >= 0.84
        ):
            return True
    return False


def _has_any(tokens: list[str], variants: tuple[str, ...]) -> bool:
    return any(
        _token_like(token, variants)
        for token in tokens
    )


def _has_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in normalized for phrase in phrases)


def _relative_date(
    normalized: str,
    today: date,
) -> date | None:
    if "anteontem" in normalized:
        return today - timedelta(days=2)
    if "ontem" in normalized:
        return today - timedelta(days=1)
    if "hoje" in normalized or "agora" in normalized:
        return today
    return None


def _month_key(normalized: str, today: date) -> str | None:
    if _has_phrase(
        normalized,
        (
            "este mes",
            "esse mes",
            "mes atual",
            "neste mes",
            "no mes",
        ),
    ):
        return today.strftime("%Y-%m")
    return None


def detect_local_intent(
    message: str,
    *,
    today: date,
) -> dict | None:
    normalized = normalize_text(message)
    if not normalized:
        return None

    tokens = _tokens(normalized)
    target_date = _relative_date(
        normalized,
        today,
    )

    performance_words = (
        "desempenho",
        "rendimento",
        "eficiencia",
        "performance",
        "rendeu",
        "rendendo",
        "performando",
    )
    production_words = (
        "producao",
        "produzindo",
        "produziu",
        "gerando",
        "geracao",
        "gerou",
    )
    quality_words = (
        "bem",
        "normal",
        "ruim",
        "baixo",
        "baixa",
        "abaixo",
        "esperado",
        "esperada",
        "melhor",
        "pior",
        "fraco",
        "fraca",
    )

    performance_signal = _has_any(
        tokens,
        performance_words,
    )
    production_quality_signal = (
        _has_any(tokens, production_words)
        and _has_any(tokens, quality_words)
    )
    weather_impact_signal = (
        _has_any(
            tokens,
            (
                "clima",
                "chuva",
                "nuvem",
                "nublado",
                "tempo",
            ),
        )
        and _has_any(tokens, production_words)
        and _has_any(
            tokens,
            (
                "afetou",
                "afetar",
                "impactou",
                "impactar",
                "prejudicou",
                "prejudicar",
                "reduziu",
                "queda",
            ),
        )
    )

    if (
        performance_signal
        or production_quality_signal
        or weather_impact_signal
    ):
        return {
            "intent": "performance",
            "tool": "diagnosticar_desempenho_diario",
            "arguments": {
                "report_date": (
                    target_date or today
                ).isoformat(),
            },
            "confidence": "high",
        }

    if _has_any(
        tokens,
        (
            "falha",
            "falhas",
            "erro",
            "erros",
            "alarme",
            "alarmes",
        ),
    ) and not _has_any(
        tokens,
        (
            "codigo",
            "significa",
            "explica",
            "explicar",
        ),
    ):
        return {
            "intent": "faults",
            "tool": "consultar_falhas_ativas",
            "arguments": {},
            "confidence": "high",
        }

    maintenance_signal = _has_any(
        tokens,
        (
            "manutencao",
            "revisao",
            "inspecao",
        ),
    )
    maintenance_action = _has_any(
        tokens,
        (
            "registrar",
            "registre",
            "anotar",
            "anote",
            "realizada",
            "realizei",
            "feita",
            "fiz",
        ),
    )
    if maintenance_signal and not maintenance_action:
        return {
            "intent": "maintenance",
            "tool": "consultar_manutencao",
            "arguments": {},
            "confidence": "high",
        }

    status_phrases = (
        "como esta minha usina",
        "como ta minha usina",
        "como vai minha usina",
        "esta tudo certo",
        "ta tudo certo",
        "status da usina",
        "situacao da usina",
        "usina funcionando",
        "usina esta normal",
        "inversor esta normal",
        "inversor funcionando",
    )
    if _has_phrase(normalized, status_phrases):
        return {
            "intent": "status",
            "tool": "consultar_resumo_usina",
            "arguments": {},
            "confidence": "high",
        }

    savings_signal = _has_any(
        tokens,
        (
            "economia",
            "economizei",
            "economizando",
            "poupei",
            "poupanca",
            "dinheiro",
        ),
    )
    if savings_signal:
        year_month = (
            _month_key(normalized, today)
            or today.strftime("%Y-%m")
        )
        return {
            "intent": "savings",
            "tool": "consultar_economia_mes",
            "arguments": {
                "year_month": year_month,
            },
            "confidence": "high",
        }

    weather_signal = _has_any(
        tokens,
        (
            "clima",
            "chuva",
            "choveu",
            "nuvem",
            "nublado",
            "temperatura",
            "tempo",
        ),
    )
    if weather_signal and target_date is not None:
        return {
            "intent": "weather",
            "tool": "consultar_clima",
            "arguments": {
                "report_date": target_date.isoformat(),
            },
            "confidence": "high",
        }

    quantitative_words = (
        "quanto",
        "quantos",
        "total",
        "gerou",
        "gerei",
        "produziu",
        "produzi",
    )
    generation_signal = _has_any(
        tokens,
        (
            "geracao",
            "gerou",
            "gerei",
            "energia",
            "producao",
            "produziu",
            "produzi",
        ),
    )
    if (
        generation_signal
        and _has_any(tokens, quantitative_words)
    ):
        month = _month_key(normalized, today)
        if month:
            start = today.replace(day=1)
            end = today
        else:
            target = target_date or today
            start = target
            end = target

        return {
            "intent": "generation",
            "tool": "consultar_geracao_periodo",
            "arguments": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            "confidence": "high",
        }

    return None
