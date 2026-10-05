import unicodedata

from solar_queries import get_generation_summary


MENU_MESSAGE = """Olá! Sou o SolCare, seu assistente de energia solar.

Escolha uma opção:

1 - Geração de hoje
2 - Geração deste mês

Digite o número da opção desejada."""


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().strip().split())


def build_reply(message: str) -> str:
    text = normalize_text(message)

    if text in {"oi", "ola", "olá", "menu", "ajuda", "inicio", "início"}:
        return MENU_MESSAGE

    if text == "1":
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['today_kwh']} kWh hoje*.

"
            "Digite *menu* para ver as opções novamente."
        )

    if text == "2":
        summary = get_generation_summary()
        return (
            "Sua usina gerou "
            f"*{summary['month_kwh']} kWh neste mês*.

"
            "Digite *menu* para ver as opções novamente."
        )

    return (
        "Não entendi sua mensagem.

"
        "Digite *menu* para ver as opções disponíveis."
    )
