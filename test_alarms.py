import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from solisclient import SolisAPIError, SolisClient, SolisCredentials


TIMEZONE = ZoneInfo("America/Bahia")
LEVEL_LABELS = {
    1: "informativo",
    2: "normal",
    3: "urgente",
}
STATE_LABELS = {
    0: "não tratado",
    1: "tratado",
    2: "recuperado",
}
SENSITIVE_KEY_PARTS = (
    "serial",
    "sn",
    "station",
    "plant",
    "name",
    "user",
    "address",
    "addr",
)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")
    return value


def get_records(response: dict) -> list[dict]:
    records = (
        response
        .get("data", {})
        .get("page", {})
        .get("records", [])
    )
    return [item for item in records if isinstance(item, dict)]


def clean_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def sanitize_extended(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                continue
            safe[key] = sanitize_extended(item)
        return safe

    if isinstance(value, list):
        return [sanitize_extended(item) for item in value]

    return value


def main() -> int:
    days = int(os.getenv("ALARM_TEST_DAYS", "365").strip() or "90")
    if days < 1 or days > 3650:
        raise RuntimeError("ALARM_TEST_DAYS deve estar entre 1 e 3650.")

    today = datetime.now(TIMEZONE).date()
    begin_date = today - timedelta(days=days)

    credentials = SolisCredentials(
        api_id=required_env("SOLIS_API_ID"),
        api_secret=required_env("SOLIS_API_SECRET"),
        base_url=required_env("SOLIS_BASE_URL"),
        station_id=required_env("SOLIS_STATION_ID"),
    )
    client = SolisClient(credentials)

    try:
        response = client.list_alarms(
            station_id=credentials.station_id,
            begin_date=begin_date.isoformat(),
            end_date=today.isoformat(),
            page_size=100,
        )
    except SolisAPIError as exc:
        raise RuntimeError(f"Falha ao consultar alarmes da SolisCloud: {exc}") from exc

    records = get_records(response)
    records.sort(
        key=lambda item: str(item.get("alarmBeginTime") or ""),
        reverse=True,
    )

    print("Teste de leitura de alarmes Solis concluído.")
    print(f"Período consultado: {begin_date.isoformat()} a {today.isoformat()}")
    print(f"Alarmes encontrados: {len(records)}")

    if len(records) >= 100:
        print(
            "Aviso: a consulta retornou 100 registros. "
            "Pode haver alarmes adicionais fora desta primeira página."
        )

    if not records:
        print("Nenhum alarme encontrado no período.")
        return 0

    for index, alarm in enumerate(records, start=1):
        level = alarm.get("alarmLevel")
        state = alarm.get("state")
        message = clean_text(alarm.get("alarmMsg"))
        advice = clean_text(alarm.get("advice"))
        extended = sanitize_extended(alarm.get("warningInfoData"))

        try:
            level_number = int(level)
        except (TypeError, ValueError):
            level_number = None

        try:
            state_number = int(state)
        except (TypeError, ValueError):
            state_number = None

        print("")
        print(f"Alarme {index}")
        print(f"  Código: {clean_text(alarm.get('alarmCode')) or '-'}")
        print(
            "  Nível: "
            f"{LEVEL_LABELS.get(level_number, clean_text(level) or '-')}"
        )
        print(
            "  Estado: "
            f"{STATE_LABELS.get(state_number, clean_text(state) or '-')}"
        )
        print(f"  Início: {clean_text(alarm.get('alarmBeginTime')) or '-'}")
        print(f"  Fim: {clean_text(alarm.get('alarmEndTime')) or '-'}")
        print(f"  Mensagem: {message or '-'}")
        print(f"  Orientação: {advice or '-'}")

        if extended not in (None, "", {}, []):
            print(
                "  Dados estendidos: "
                + json.dumps(extended, ensure_ascii=False, sort_keys=True)
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
