import json
import os
from datetime import date
from decimal import Decimal

import psycopg2


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError("Variável DATABASE_URL não configurada.")

    return database_url


def connect(database_url: str | None = None):
    database_url = database_url or get_database_url()

    # Supabase normalmente exige SSL. Se a URL já trouxer sslmode,
    # respeitamos o valor da própria connection string.
    if "sslmode=" in database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(database_url, sslmode="require")


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")

    text = str(value).strip().replace(",", ".")

    if not text:
        return Decimal("0")

    return Decimal(text)


def save_monthly_generation_snapshot(
    station_id: str,
    report_date: date,
    generation_kwh,
    provider: str = "",
) -> None:
    if not station_id:
        raise ValueError("station_id não pode ser vazio.")

    year_month = report_date.strftime("%Y-%m")
    generation = to_decimal(generation_kwh)

    query = """
        INSERT INTO monthly_generation (
            station_id,
            year_month,
            generation_kwh,
            last_report_date,
            provider,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (station_id, year_month)
        DO UPDATE SET
            generation_kwh = GREATEST(
                monthly_generation.generation_kwh,
                EXCLUDED.generation_kwh
            ),
            last_report_date = GREATEST(
                monthly_generation.last_report_date,
                EXCLUDED.last_report_date
            ),
            provider = COALESCE(NULLIF(EXCLUDED.provider, ''), monthly_generation.provider),
            updated_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    str(station_id),
                    year_month,
                    generation,
                    report_date.isoformat(),
                    provider,
                ),
            )


def fetch_generation_for_month(
    year_month: str,
    station_id: str | None = None,
    provider: str | None = None,
) -> tuple[str, Decimal] | None:
    station_id = (station_id or "").strip()
    provider = (provider or "").strip()

    if station_id and provider:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE station_id = %s
              AND year_month = %s
              AND provider = %s
            LIMIT 1;
        """
        params = (station_id, year_month, provider)
    elif station_id:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE station_id = %s
              AND year_month = %s
            LIMIT 1;
        """
        params = (station_id, year_month)
    elif provider:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE year_month = %s
              AND provider = %s
            ORDER BY updated_at DESC
            LIMIT 1;
        """
        params = (year_month, provider)
    else:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE year_month = %s
            ORDER BY updated_at DESC
            LIMIT 1;
        """
        params = (year_month,)

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

    if not row:
        return None

    return str(row[0]), to_decimal(row[1])


def upsert_solis_alarm_event(
    station_id: str,
    device_sn: str,
    alarm_code: str,
    alarm_level: int | None,
    alarm_message: str,
    advice: str,
    alarm_begin_time: str,
    alarm_end_time: str,
    state: int | None,
    warning_info_data,
    raw_payload: dict,
    status: str,
) -> tuple[int, bool, object]:
    if not station_id:
        raise ValueError("station_id não pode ser vazio.")

    if not alarm_begin_time:
        raise ValueError("alarm_begin_time não pode ser vazio.")

    insert_query = """
        INSERT INTO solis_alarm_events (
            station_id,
            device_sn,
            alarm_code,
            alarm_level,
            alarm_message,
            advice,
            alarm_begin_time,
            alarm_end_time,
            state,
            warning_info_data,
            raw_payload,
            status,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), %s,
            %s::jsonb, %s::jsonb, %s, NOW(), NOW()
        )
        ON CONFLICT (
            station_id,
            device_sn,
            alarm_code,
            alarm_begin_time
        )
        DO NOTHING
        RETURNING id, notified_at;
    """

    update_query = """
        UPDATE solis_alarm_events
        SET
            alarm_level = COALESCE(%s, alarm_level),
            alarm_message = COALESCE(NULLIF(%s, ''), alarm_message),
            advice = COALESCE(NULLIF(%s, ''), advice),
            alarm_end_time = COALESCE(NULLIF(%s, ''), alarm_end_time),
            state = COALESCE(%s, state),
            warning_info_data = %s::jsonb,
            raw_payload = %s::jsonb,
            status = %s,
            updated_at = NOW()
        WHERE station_id = %s
          AND device_sn = %s
          AND alarm_code = %s
          AND alarm_begin_time = %s
        RETURNING id, notified_at;
    """

    warning_json = json.dumps(warning_info_data, ensure_ascii=False)
    raw_json = json.dumps(raw_payload, ensure_ascii=False)

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                insert_query,
                (
                    str(station_id),
                    str(device_sn or ""),
                    str(alarm_code or "unknown"),
                    alarm_level,
                    alarm_message,
                    advice,
                    alarm_begin_time,
                    alarm_end_time,
                    state,
                    warning_json,
                    raw_json,
                    status,
                ),
            )
            row = cursor.fetchone()

            if row:
                return int(row[0]), True, row[1]

            cursor.execute(
                update_query,
                (
                    alarm_level,
                    alarm_message,
                    advice,
                    alarm_end_time,
                    state,
                    warning_json,
                    raw_json,
                    status,
                    str(station_id),
                    str(device_sn or ""),
                    str(alarm_code or "unknown"),
                    alarm_begin_time,
                ),
            )
            row = cursor.fetchone()

    if not row:
        raise RuntimeError("Não foi possível recuperar o alarme Solis após o upsert.")

    return int(row[0]), False, row[1]


def mark_solis_alarm_notified(event_id: int) -> None:
    query = """
        UPDATE solis_alarm_events
        SET notified_at = COALESCE(notified_at, NOW()),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (event_id,))
