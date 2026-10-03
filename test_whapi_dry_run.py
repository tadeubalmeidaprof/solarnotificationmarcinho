import os
from decimal import Decimal
from unittest.mock import Mock, patch

from send_monthly_report import build_monthly_message
from solis_alarm_monitor import build_alarm_message
from solisdaily import build_message
from whatsapp import send_message


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Secret {name} não configurado.")
    return value


def validate_chat_id(chat_id: str) -> None:
    if "@" not in chat_id:
        raise RuntimeError(
            "WHAPI_CHAT_ID parece inválido. Use o identificador retornado pela Whapi."
        )


def fake_whapi_response() -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "sent_message": {
            "id": "dry-run-message-id"
        }
    }
    return response


def main() -> int:
    token = required_env("WHAPI_TOKEN")
    chat_id = required_env("WHAPI_CHAT_ID")
    validate_chat_id(chat_id)

    daily_message = build_message(
        {
            "dayEnergy": 12.34,
            "monthEnergy": 123.45,
        },
        "03/10/2026 às 12:00",
    )

    monthly_message = build_monthly_message(
        month_label="setembro de 2026",
        generation_kwh=Decimal("960.0"),
        average_daily_kwh=Decimal("32.0"),
        tariff=Decimal("1.00"),
        savings=Decimal("930.00"),
    )

    alarm_message = build_alarm_message(
        {
            "alarmCode": "301",
            "alarmLevel": 2,
            "alarmMsg": "Dry-run alarm",
            "advice": "Dry-run advice",
            "alarmBeginTime": "2026-10-03 12:00:00",
            "state": 0,
        }
    )

    messages = {
        "relatorio_diario": daily_message,
        "relatorio_mensal": monthly_message,
        "alarme": alarm_message,
    }

    with patch("whatsapp.requests.post") as post:
        post.return_value = fake_whapi_response()

        for name, message in messages.items():
            result = send_message(
                message,
                token=token,
                chat_id=chat_id,
            )

            if result.get("sent_message", {}).get("id") != "dry-run-message-id":
                raise RuntimeError(f"Dry-run falhou para {name}.")

            print(
                f"{name}: OK "
                f"(mensagem montada, payload Whapi validado, nenhum envio real)"
            )

        if post.call_count != len(messages):
            raise RuntimeError(
                "Quantidade inesperada de chamadas simuladas para a Whapi."
            )

        for call in post.call_args_list:
            args, kwargs = call
            if args[0] != "https://gate.whapi.cloud/messages/text":
                raise RuntimeError("Endpoint Whapi inesperado no dry-run.")

            headers = kwargs["headers"]
            payload = kwargs["json"]

            if headers.get("Authorization") != f"Bearer {token}":
                raise RuntimeError("Header Authorization inválido no dry-run.")

            if payload.get("to") != chat_id:
                raise RuntimeError("WHAPI_CHAT_ID não foi aplicado corretamente.")

            if not payload.get("body"):
                raise RuntimeError("Mensagem vazia no payload Whapi.")

    print("")
    print("Dry-run concluído com sucesso.")
    print("Nenhuma requisição HTTP real foi enviada para a Whapi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
