import unicodedata

from solar_queries import get_generation_summary


TODAY_BUTTON_ID = "generation_today"
MONTH_BUTTON_ID = "generation_month"

MENU_BODY = """Olá! 👋 Bem-vindo ao *SolCare*.
Estou aqui para ajudar você a acompanhar sua usina solar de forma rápida e simples.

Selecione uma opção:"""

MENU_FOOTER = "Digite menu a qualquer momento para voltar às opções."

MENU_BUTTONS = [
    {
        "id": TODAY_BUTTON_ID,
        "title": "☀️ Geração de hoje",
    },
    {
        "id": MONTH_BUTTON_ID,
        "title": "📊 Geração do mês",
    },
]

MENU_MESSAGE = f"""{MENU_BODY}

1 - ☀️ Geração de hoje
2 - 📊 Geração do mês

{MENU_FOOTER}"""

MENU_TRIGGERS = {"oi", "ola", "menu", "ajuda", "inicio"}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(
        ch if ch.isalnum() or ch.isspace() else " "
        for ch in text
    )
    return " ".join(text.lower().strip().split())


def is_menu_request(message: str) -> bool:
    return normalize_text(message) in MENU_TRIGGERS


def build_reply(message: str) -> str:
    raw_value = str(message or "").strip().lower()
    text = normalize_text(message)

    if is_menu_request(message):
        return MENU_MESSAGE

    if raw_value == TODAY_BUTTON_ID or text == "1":
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['today_kwh']} kWh hoje*.\n\n"
            "Digite *menu* para ver as opções novamente."
        )

    if raw_value == MONTH_BUTTON_ID or text == "2":
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['month_kwh']} kWh neste mês*.\n\n"
            "Digite *menu* para ver as opções novamente."
        )

    return (
        "Não entendi sua mensagem.\n\n"
        "Digite *menu* para ver as opções disponíveis."
    )
