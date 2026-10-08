import os

import requests


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_COMPLETION_TOKENS = 600


class AIProviderError(RuntimeError):
    pass


class AIRateLimitError(AIProviderError):
    pass


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    try:
        value = float(raw.replace(",", "."))
    except ValueError as exc:
        raise AIProviderError(f"Variável {name} inválida.") from exc

    if value <= 0:
        raise AIProviderError(f"Variável {name} deve ser maior que zero.")

    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise AIProviderError(f"Variável {name} inválida.") from exc

    if value <= 0:
        raise AIProviderError(f"Variável {name} deve ser maior que zero.")

    return value


def create_chat_completion(messages: list[dict], tools: list[dict]) -> dict:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise AIProviderError("GROQ_API_KEY não configurada.")

    model = os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    timeout = _float_env("GROQ_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    max_tokens = _int_env(
        "GROQ_MAX_COMPLETION_TOKENS",
        DEFAULT_MAX_COMPLETION_TOKENS,
    )

    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning_effort": "low",
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
    }

    try:
        response = requests.post(
            GROQ_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout:
        raise AIProviderError("Tempo limite excedido ao consultar a Groq.") from None
    except requests.RequestException:
        raise AIProviderError("Falha de rede ao consultar a Groq.") from None

    if response.status_code == 429:
        raise AIRateLimitError("Limite de requisições da Groq atingido.")

    if response.status_code >= 500:
        raise AIProviderError("Groq temporariamente indisponível.")

    if not response.ok:
        raise AIProviderError(
            f"Groq rejeitou a requisição com HTTP {response.status_code}."
        )

    try:
        data = response.json()
    except ValueError:
        raise AIProviderError("Groq retornou uma resposta inválida.") from None

    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise AIProviderError("Groq retornou uma resposta sem escolhas.")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AIProviderError("Groq retornou uma mensagem inválida.")

    return message
