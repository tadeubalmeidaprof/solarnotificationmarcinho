import unicodedata

from ai.assistant import ask_solcare_ai
from solar_queries import get_generation_summary


TODAY_BUTTON_ID = "generation_today"
MONTH_BUTTON_ID = "generation_month"
HELP_BUTTON_ID = "help_examples"

MENU_BODY = """Olá! 👋 Bem-vindo ao *SolCare*.
Você pode usar os atalhos abaixo ou conversar comigo normalmente.

Ex.: *Como está minha usina?*"""

MENU_FOOTER = "Digite menu para voltar aqui a qualquer momento."

MENU_BUTTONS = [
    {
        "id": TODAY_BUTTON_ID,
        "title": "☀️ Geração de hoje",
    },
    {
        "id": MONTH_BUTTON_ID,
        "title": "📊 Geração do mês",
    },
    {
        "id": HELP_BUTTON_ID,
        "title": "💬 O que posso perguntar?",
    },
]

MENU_MESSAGE = f"""{MENU_BODY}

1 - ☀️ Geração de hoje
2 - 📊 Geração do mês
3 - 💬 O que posso perguntar?

{MENU_FOOTER}"""

HELP_MESSAGE = """💬 *Você pode perguntar com suas próprias palavras.*

Alguns exemplos:

☀️ *Geração* — Quanto gerei hoje?
📈 *Desempenho* — Minha usina está rendendo bem?
📉 *Tendência* — Minha usina está perdendo rendimento?
⚠️ *Falhas* — Tem alguma falha ativa?
🛠️ *Manutenção* — Preciso fazer manutenção?
🌦️ *Clima* — O clima afetou minha geração?
💰 *Economia* — Quanto economizei este mês?

Também posso analisar a curva de geração, comparar períodos e explicar códigos de falha.

_Não precisa copiar as frases. Pergunte do seu jeito._"""

UNKNOWN_MESSAGE = (
    "Não entendi sua mensagem.\n\n"
    "Digite *menu* para ver as opções disponíveis."
)

MENU_TRIGGERS = {"oi", "ola", "menu", "ajuda", "inicio"}
TODAY_ALIASES = {"1", "geracao de hoje", "ver geracao de hoje"}
MONTH_ALIASES = {"2", "geracao do mes", "ver geracao do mes"}
HELP_ALIASES = {
    "3",
    "exemplos",
    "perguntas",
    "ver perguntas",
    "o que posso perguntar",
    "o que voce faz",
    "como posso usar",
    "como usar",
}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        ch for ch in text
        if not unicodedata.combining(ch)
    )
    text = "".join(
        ch if ch.isalnum() or ch.isspace() else " "
        for ch in text
    )
    return " ".join(text.lower().strip().split())


def is_menu_request(message: str) -> bool:
    return normalize_text(message) in MENU_TRIGGERS


def build_reply(message: str, chat_id: str = "") -> str:
    raw_value = str(message or "").strip().lower()
    normalized = normalize_text(message)

    if is_menu_request(message):
        return MENU_MESSAGE

    if (
        raw_value == TODAY_BUTTON_ID
        or normalized in TODAY_ALIASES
    ):
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['today_kwh']} kWh hoje*.\n\n"
            "Digite *menu* para ver as opções novamente."
        )

    if (
        raw_value == MONTH_BUTTON_ID
        or normalized in MONTH_ALIASES
    ):
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['month_kwh']} kWh neste mês*.\n\n"
            "Digite *menu* para ver as opções novamente."
        )

    if (
        raw_value == HELP_BUTTON_ID
        or normalized in HELP_ALIASES
    ):
        return HELP_MESSAGE

    ai_reply = ask_solcare_ai(
        message,
        chat_id=chat_id,
    )
    return ai_reply or UNKNOWN_MESSAGE
