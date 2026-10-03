import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from database import mark_solis_alarm_notified, upsert_solis_alarm_event
from solisclient import SolisAPIError, SolisClient, SolisCredentials
from solisdaily import WhatsAppDeliveryError, send_whatsapp_message


TIMEZONE = ZoneInfo("America/Bahia")

LEVEL_LABELS = {
    1: "Informativo",
    2: "Alarme",
    3: "Urgente",
}

STATE_LABELS = {
    0: "Não tratado",
    1: "Tratado",
    2: "Recuperado",
}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")
    return value


def int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def clean_text(value, limit: int = 800) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_records(response: dict) -> list[dict]:
    records = (
        response
        .get("data", {})
        .get("page", {})
        .get("records", [])
    )
    return [item for item in records if isinstance(item, dict)]


def get_record_id(record: dict) -> int | None:
    for key in ("alarmId", "id"):
        value = record.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def fetch_recent_alarms(
    client: SolisClient,
    station_id: str,
    begin_date: str,
    end_date: str,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict]:
    alarms: list[dict] = []
    min_id = None

    for _ in range(max_pages):
        response = client.list_alarms(
            station_id=station_id,
            begin_date=begin_date,
            end_date=end_date,
            page_size=page_size,
            min_id=min_id,
        )
        records = get_records(response)
        alarms.extend(records)

        if len(records) < page_size:
            break

        last_id = get_record_id(records[-1])
        if last_id is None:
            raise RuntimeError(
                "A Solis retornou uma página cheia de alarmes sem alarmId/id. "
                "Não é seguro continuar sem paginação."
            )

        next_min_id = last_id + 1
        if min_id is not None and next_min_id <= min_id:
            raise RuntimeError("A paginação de alarmes da Solis não avançou.")

        min_id = next_min_id
    else:
        raise RuntimeError(
            f"Consulta de alarmes excedeu o limite de {max_pages} páginas."
        )

    return alarms


def parse_alarm_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, TIMEZONE)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    if text.isdigit():
        return parse_alarm_datetime(int(text))

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=TIMEZONE)
        return parsed.astimezone(TIMEZONE)
    except ValueError:
        pass

    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue

    return None


def format_alarm_time(value) -> str:
    parsed = parse_alarm_datetime(value)
    if parsed:
        return parsed.strftime("%d/%m/%Y às %H:%M")
    return clean_text(value) or "-"


def alarm_is_resolved(alarm: dict) -> bool:
    return as_int(alarm.get("state")) == 2 or bool(
        clean_text(alarm.get("alarmEndTime"))
    )


def resolved_alarm_is_recent(alarm: dict) -> bool:
    max_age = timedelta(
        minutes=int_env("SOLIS_ALARM_NOTIFY_MAX_AGE_MINUTES", 120)
    )

    reference = (
        parse_alarm_datetime(alarm.get("alarmEndTime"))
        or parse_alarm_datetime(alarm.get("alarmBeginTime"))
    )
    if reference is None:
        return False

    age = datetime.now(TIMEZONE) - reference
    return timedelta(0) <= age <= max_age


def should_notify(alarm: dict, notified_at) -> bool:
    if notified_at is not None:
        return False

    if not alarm_is_resolved(alarm):
        return True

    return resolved_alarm_is_recent(alarm)


def warning_info_text(value) -> str:
    if value in (None, "", {}, []):
        return ""

    if isinstance(value, (str, int, float)):
        return clean_text(value, limit=200)

    return clean_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        limit=300,
    )


def build_alarm_message(alarm: dict) -> str:
    code = clean_text(alarm.get("alarmCode")) or "não informado"
    level = as_int(alarm.get("alarmLevel"))
    state = as_int(alarm.get("state"))
    message = clean_text(alarm.get("alarmMsg"))
    advice = clean_text(alarm.get("advice"))
    warning_info = warning_info_text(alarm.get("warningInfoData"))

    lines = [
        "SolCare - Alarme detectado na usina solar",
        "",
        f"Solis - Código {code}",
        f"Nível: {LEVEL_LABELS.get(level, clean_text(alarm.get('alarmLevel')) or '-')}",
        f"Falha: {message or 'Alarme informado pela SolisCloud'}",
        f"Detectado em: {format_alarm_time(alarm.get('alarmBeginTime'))}",
    ]

    if warning_info:
        lines.append(f"Subcódigo/dados: {warning_info}")

    if advice:
        lines.extend(["", f"Orientação: {advice}"])

    if alarm_is_resolved(alarm):
        lines.extend(
            [
                "",
                "Situação atual: a SolisCloud já registra recuperação.",
                f"Normalização: {format_alarm_time(alarm.get('alarmEndTime'))}",
            ]
        )
    elif state is not None:
        lines.extend(
            [
                "",
                f"Estado na SolisCloud: {STATE_LABELS.get(state, str(state))}",
            ]
        )

    return "\n".join(lines)


def notify_alarm(alarm: dict) -> None:
    result = send_whatsapp_message(
        message=build_alarm_message(alarm),
        phone=required_env("CALLMEBOT_PHONE"),
        api_key=required_env("CALLMEBOT_APIKEY"),
    )

    if "message queued" not in result.lower():
        raise WhatsAppDeliveryError(
            "CallMeBot respondeu sem confirmar o enfileiramento da mensagem."
        )


def main() -> None:
    station_id = required_env("SOLIS_STATION_ID")
    lookback_days = int_env("SOLIS_ALARM_LOOKBACK_DAYS", 30)

    if lookback_days < 1 or lookback_days > 365:
        raise RuntimeError("SOLIS_ALARM_LOOKBACK_DAYS deve estar entre 1 e 365.")

    today = datetime.now(TIMEZONE).date()
    begin_date = today - timedelta(days=lookback_days)

    credentials = SolisCredentials(
        api_id=required_env("SOLIS_API_ID"),
        api_secret=required_env("SOLIS_API_SECRET"),
        base_url=required_env("SOLIS_BASE_URL"),
        station_id=station_id,
    )
    client = SolisClient(credentials)

    try:
        alarms = fetch_recent_alarms(
            client=client,
            station_id=station_id,
            begin_date=begin_date.isoformat(),
            end_date=today.isoformat(),
        )
    except SolisAPIError as exc:
        raise RuntimeError(f"Falha ao consultar alarmes da SolisCloud: {exc}") from exc

    if not alarms:
        print("Monitoramento Solis concluído: nenhum alarme encontrado.")
        return

    notifications_sent = 0

    alarms.sort(
        key=lambda item: str(item.get("alarmBeginTime") or "")
    )

    for alarm in alarms:
        alarm_code = clean_text(alarm.get("alarmCode")) or "unknown"
        alarm_begin_time = clean_text(alarm.get("alarmBeginTime"))

        if not alarm_begin_time:
            print(
                "Ignorando alarme sem horário de início:",
                {"alarm_code": alarm_code},
            )
            continue

        state = as_int(alarm.get("state"))
        status = "resolved" if alarm_is_resolved(alarm) else "active"

        event_id, _created, notified_at = upsert_solis_alarm_event(
            station_id=station_id,
            device_sn=clean_text(alarm.get("alarmDeviceSn")),
            alarm_code=alarm_code,
            alarm_level=as_int(alarm.get("alarmLevel")),
            alarm_message=clean_text(alarm.get("alarmMsg")),
            advice=clean_text(alarm.get("advice")),
            alarm_begin_time=alarm_begin_time,
            alarm_end_time=clean_text(alarm.get("alarmEndTime")),
            state=state,
            warning_info_data=alarm.get("warningInfoData"),
            raw_payload=alarm,
            status=status,
        )

        if not should_notify(alarm, notified_at):
            continue

        notify_alarm(alarm)
        mark_solis_alarm_notified(event_id)
        notifications_sent += 1

        print(
            "Notificação de alarme Solis enviada:",
            {
                "alarm_code": alarm_code,
                "status": status,
            },
        )

    print(
        "Monitoramento Solis concluído:",
        {
            "alarms_found": len(alarms),
            "notifications_sent": notifications_sent,
        },
    )


if __name__ == "__main__":
    main()
