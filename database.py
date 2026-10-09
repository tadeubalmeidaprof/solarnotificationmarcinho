import json
import os
from datetime import date
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from utils import to_decimal


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError("Variável DATABASE_URL não configurada.")

    return database_url


def connect(database_url: str | None = None):
    database_url = database_url or get_database_url()

    if "sslmode=" in database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(database_url, sslmode="require")


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
) -> tuple[str, Any] | None:
    station_id = (station_id or "").strip()

    if station_id:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE station_id = %s
              AND year_month = %s
            LIMIT 1;
        """
        params = (station_id, year_month)
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


def save_daily_generation(
    provider: str,
    station_id: str,
    report_date: date,
    generation_day_kwh,
    generation_month_kwh,
    inverter_status: str = "",
    device_sn: str = "",
) -> None:
    query = """
        INSERT INTO daily_generation (
            provider,
            station_id,
            report_date,
            generation_day_kwh,
            generation_month_kwh,
            inverter_status,
            device_sn,
            collected_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (provider, station_id, report_date)
        DO UPDATE SET
            generation_day_kwh = EXCLUDED.generation_day_kwh,
            generation_month_kwh = GREATEST(
                daily_generation.generation_month_kwh,
                EXCLUDED.generation_month_kwh
            ),
            inverter_status = EXCLUDED.inverter_status,
            device_sn = EXCLUDED.device_sn,
            collected_at = NOW(),
            updated_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    report_date.isoformat(),
                    to_decimal(generation_day_kwh),
                    to_decimal(generation_month_kwh),
                    inverter_status,
                    device_sn,
                ),
            )


def save_daily_weather(
    provider: str,
    station_id: str,
    report_date: date,
    latitude,
    longitude,
    weather: dict[str, Any],
) -> None:
    query = """
        INSERT INTO daily_weather (
            FORNECEDOR,
            IDUSINA,
            DATARELATORIO,
            LATITUDE,
            LONGITUDE,
            PERCENTUALNUVENS,
            CHUVAMM,
            RADIACAOSOLARWHM2,
            HORASSOL,
            TEMPERATURAMINIMAC,
            TEMPERATURAMAXIMAC,
            CLASSIFICACAOCLIMA,
            PROVEDORCLIMA,
            DADOSBRUTOS,
            COLETADOEM,
            ATUALIZADOEM
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (FORNECEDOR, IDUSINA, DATARELATORIO)
        DO UPDATE SET
            LATITUDE = EXCLUDED.LATITUDE,
            LONGITUDE = EXCLUDED.LONGITUDE,
            PERCENTUALNUVENS = EXCLUDED.PERCENTUALNUVENS,
            CHUVAMM = EXCLUDED.CHUVAMM,
            RADIACAOSOLARWHM2 = EXCLUDED.RADIACAOSOLARWHM2,
            HORASSOL = EXCLUDED.HORASSOL,
            TEMPERATURAMINIMAC = EXCLUDED.TEMPERATURAMINIMAC,
            TEMPERATURAMAXIMAC = EXCLUDED.TEMPERATURAMAXIMAC,
            CLASSIFICACAOCLIMA = EXCLUDED.CLASSIFICACAOCLIMA,
            PROVEDORCLIMA = EXCLUDED.PROVEDORCLIMA,
            DADOSBRUTOS = EXCLUDED.DADOSBRUTOS,
            COLETADOEM = NOW(),
            ATUALIZADOEM = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    report_date.isoformat(),
                    to_decimal(latitude),
                    to_decimal(longitude),
                    to_decimal(weather.get("PERCENTUALNUVENS")),
                    to_decimal(weather.get("CHUVAMM")),
                    to_decimal(weather.get("RADIACAOSOLARWHM2")),
                    to_decimal(weather.get("HORASSOL")),
                    to_decimal(weather.get("TEMPERATURAMINIMAC")),
                    to_decimal(weather.get("TEMPERATURAMAXIMAC")),
                    weather.get("CLASSIFICACAOCLIMA", "unknown"),
                    weather.get("PROVEDORCLIMA", "open-meteo"),
                    json.dumps(weather.get("DADOSBRUTOS", {}), ensure_ascii=False),
                ),
            )


def fetch_monitoring_history(
    provider: str,
    station_id: str,
    limit: int = 45,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            g.report_date,
            g.generation_day_kwh,
            g.generation_month_kwh,
            g.inverter_status,
            w.PERCENTUALNUVENS AS "PERCENTUALNUVENS",
            w.CHUVAMM AS "CHUVAMM",
            w.RADIACAOSOLARWHM2 AS "RADIACAOSOLARWHM2",
            w.HORASSOL AS "HORASSOL",
            w.CLASSIFICACAOCLIMA AS "CLASSIFICACAOCLIMA"
        FROM daily_generation g
        INNER JOIN daily_weather w
            ON w.FORNECEDOR = g.provider
           AND w.IDUSINA = g.station_id
           AND w.DATARELATORIO = g.report_date
        WHERE g.provider = %s
          AND g.station_id = %s
        ORDER BY g.report_date DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (provider, str(station_id), limit))
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_open_maintenance_alert(
    provider: str,
    station_id: str,
    alert_type: str,
) -> dict[str, Any] | None:
    query = """
        SELECT *
        FROM maintenance_alerts
        WHERE provider = %s
          AND station_id = %s
          AND alert_type = %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified')
        ORDER BY created_at DESC
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (provider, str(station_id), alert_type))
            row = cursor.fetchone()

    return dict(row) if row else None


def expire_stale_pending_maintenance_alerts(
    provider: str,
    station_id: str,
    confirmation_days: int,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'expired',
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND status = 'pending_confirmation'
          AND created_at < NOW() - (%s * INTERVAL '1 day');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (provider, str(station_id), confirmation_days))
            return cursor.rowcount


def create_maintenance_alert(
    provider: str,
    station_id: str,
    alert: dict[str, Any],
    status: str = "pending_confirmation",
) -> int:
    query = """
        INSERT INTO maintenance_alerts (
            provider,
            station_id,
            alert_type,
            severity,
            reference_start_date,
            reference_end_date,
            expected_generation_kwh,
            observed_generation_kwh,
            drop_percentage,
            favorable_days_count,
            probable_cause,
            details,
            status,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb, %s, NOW(), NOW()
        )
        RETURNING id;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    alert["alert_type"],
                    alert.get("severity", "warning"),
                    alert.get("reference_start_date"),
                    alert.get("reference_end_date"),
                    to_decimal(alert.get("expected_generation_kwh")),
                    to_decimal(alert.get("observed_generation_kwh")),
                    to_decimal(alert.get("drop_percentage")),
                    int(alert.get("favorable_days_count", 0)),
                    alert.get("probable_cause", ""),
                    json.dumps(alert.get("details", {}), ensure_ascii=False),
                    status,
                ),
            )
            row = cursor.fetchone()

    return int(row[0])


def confirm_maintenance_alert(
    alert_id: int,
    alert: dict[str, Any],
) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'confirmed',
            severity = %s,
            reference_start_date = %s,
            reference_end_date = %s,
            expected_generation_kwh = %s,
            observed_generation_kwh = %s,
            drop_percentage = %s,
            favorable_days_count = %s,
            probable_cause = %s,
            details = %s::jsonb,
            updated_at = NOW()
        WHERE id = %s
          AND status = 'pending_confirmation';
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    alert.get("severity", "warning"),
                    alert.get("reference_start_date"),
                    alert.get("reference_end_date"),
                    to_decimal(alert.get("expected_generation_kwh")),
                    to_decimal(alert.get("observed_generation_kwh")),
                    to_decimal(alert.get("drop_percentage")),
                    int(alert.get("favorable_days_count", 0)),
                    alert.get("probable_cause", ""),
                    json.dumps(alert.get("details", {}), ensure_ascii=False),
                    alert_id,
                ),
            )


def mark_integrator_notified(alert_id: int) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'integrator_notified',
            integrator_notified_at = COALESCE(integrator_notified_at, NOW()),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'confirmed';
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (alert_id,))


def resolve_other_open_maintenance_alerts(
    provider: str,
    station_id: str,
    current_alert_type: str,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, NOW()),
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND alert_type <> %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (provider, str(station_id), current_alert_type),
            )
            return cursor.rowcount


def resolve_open_maintenance_alerts(
    provider: str,
    station_id: str,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, NOW()),
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (provider, str(station_id)))
            return cursor.rowcount


def ensure_growatt_fault_events_table() -> None:
    query = """
        CREATE TABLE IF NOT EXISTS growatt_fault_events (
            id BIGSERIAL PRIMARY KEY,
            station_id TEXT NOT NULL,
            device_sn TEXT NOT NULL,
            fault_code TEXT NOT NULL,
            fault_code_raw TEXT NOT NULL,
            fault_message TEXT,
            fault_time TIMESTAMP NOT NULL,
            recovery_time TIMESTAMP,
            device_type TEXT,
            solution TEXT,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('historical', 'active', 'resolved')),
            normal_checks INTEGER NOT NULL DEFAULT 0,
            last_normal_live_time TIMESTAMP,
            notified_at TIMESTAMPTZ,
            recovery_notified_at TIMESTAMPTZ,
            resolved_at TIMESTAMP,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (device_sn, fault_code_raw, fault_time)
        );

        CREATE INDEX IF NOT EXISTS idx_growatt_fault_events_active
            ON growatt_fault_events (device_sn, status)
            WHERE status = 'active';

        ALTER TABLE growatt_fault_events
            ENABLE ROW LEVEL SECURITY;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def upsert_growatt_fault_event(
    station_id: str,
    device_sn: str,
    fault_code: str,
    fault_code_raw: str,
    fault_message: str,
    fault_time,
    recovery_time,
    device_type: str,
    solution: str,
    raw_payload: dict[str, Any],
    initial_status: str,
) -> tuple[dict[str, Any], bool]:
    insert_query = """
        INSERT INTO growatt_fault_events (
            station_id,
            device_sn,
            fault_code,
            fault_code_raw,
            fault_message,
            fault_time,
            recovery_time,
            device_type,
            solution,
            raw_payload,
            status,
            resolved_at,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s, %s, NOW(), NOW()
        )
        ON CONFLICT (device_sn, fault_code_raw, fault_time)
        DO NOTHING
        RETURNING *;
    """

    resolved_at = recovery_time if initial_status == "resolved" else None

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                insert_query,
                (
                    str(station_id),
                    str(device_sn),
                    str(fault_code),
                    str(fault_code_raw),
                    fault_message,
                    fault_time,
                    recovery_time,
                    device_type,
                    solution,
                    json.dumps(raw_payload, ensure_ascii=False),
                    initial_status,
                    resolved_at,
                ),
            )
            row = cursor.fetchone()

            if row:
                return dict(row), True

            cursor.execute(
                """
                    UPDATE growatt_fault_events
                    SET
                        recovery_time = COALESCE(%s, recovery_time),
                        fault_message = COALESCE(NULLIF(%s, ''), fault_message),
                        device_type = COALESCE(NULLIF(%s, ''), device_type),
                        solution = COALESCE(NULLIF(%s, ''), solution),
                        raw_payload = %s::jsonb,
                        updated_at = NOW()
                    WHERE device_sn = %s
                      AND fault_code_raw = %s
                      AND fault_time = %s
                    RETURNING *;
                """,
                (
                    recovery_time,
                    fault_message,
                    device_type,
                    solution,
                    json.dumps(raw_payload, ensure_ascii=False),
                    str(device_sn),
                    str(fault_code_raw),
                    fault_time,
                ),
            )
            existing = cursor.fetchone()

    if not existing:
        raise RuntimeError("Não foi possível recuperar o evento Growatt após o upsert.")

    return dict(existing), False


def fetch_active_growatt_faults() -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM growatt_fault_events
        WHERE status = 'active'
        ORDER BY fault_time ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_pending_growatt_recovery_notifications() -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM growatt_fault_events
        WHERE status = 'resolved'
          AND notified_at IS NOT NULL
          AND recovery_notified_at IS NULL
        ORDER BY recovery_time ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def mark_growatt_fault_notified(event_id: int) -> None:
    query = """
        UPDATE growatt_fault_events
        SET notified_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (event_id,))


def mark_growatt_fault_recovery_notified(event_id: int) -> None:
    query = """
        UPDATE growatt_fault_events
        SET recovery_notified_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (event_id,))


def increment_growatt_fault_normal_check(event_id: int, live_time) -> int:
    query = """
        UPDATE growatt_fault_events
        SET normal_checks = normal_checks + 1,
            last_normal_live_time = %s,
            updated_at = NOW()
        WHERE id = %s
          AND status = 'active'
          AND (
              last_normal_live_time IS NULL
              OR last_normal_live_time <> %s
          )
        RETURNING normal_checks;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (live_time, event_id, live_time),
            )
            row = cursor.fetchone()

            if row:
                return int(row[0])

            cursor.execute(
                """
                    SELECT normal_checks
                    FROM growatt_fault_events
                    WHERE id = %s;
                """,
                (event_id,),
            )
            existing = cursor.fetchone()

    return int(existing[0]) if existing else 0


def reset_growatt_fault_normal_checks() -> None:
    query = """
        UPDATE growatt_fault_events
        SET normal_checks = 0,
            last_normal_live_time = NULL,
            updated_at = NOW()
        WHERE status = 'active'
          AND normal_checks <> 0;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def mark_growatt_fault_resolved(event_id: int, resolved_at) -> None:
    query = """
        UPDATE growatt_fault_events
        SET status = 'resolved',
            resolved_at = %s,
            recovery_time = COALESCE(recovery_time, %s),
            normal_checks = 0,
            last_normal_live_time = NULL,
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (resolved_at, resolved_at, event_id),
            )

# ---------------------------------------------------------------------------
# Consultas do assistente conversacional
# ---------------------------------------------------------------------------

def fetch_daily_generation_range(
    provider: str,
    station_id: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            report_date,
            generation_day_kwh,
            generation_month_kwh,
            inverter_status
        FROM daily_generation
        WHERE provider = %s
          AND station_id = %s
          AND report_date BETWEEN %s AND %s
        ORDER BY report_date ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_daily_weather_for_date(
    provider: str,
    station_id: str,
    report_date: date,
) -> dict[str, Any] | None:
    query = """
        SELECT
            DATARELATORIO AS report_date,
            PERCENTUALNUVENS AS cloud_cover_percent,
            CHUVAMM AS rainfall_mm,
            RADIACAOSOLARWHM2 AS solar_radiation_wh_m2,
            HORASSOL AS sunshine_hours,
            TEMPERATURAMINIMAC AS temperature_min_c,
            TEMPERATURAMAXIMAC AS temperature_max_c,
            CLASSIFICACAOCLIMA AS weather_class
        FROM daily_weather
        WHERE FORNECEDOR = %s
          AND IDUSINA = %s
          AND DATARELATORIO = %s
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    report_date.isoformat(),
                ),
            )
            row = cursor.fetchone()

    return dict(row) if row else None


def fetch_ai_conversation_messages(
    conversation_key: str,
    limit: int = 8,
) -> list[dict[str, str]]:
    query = """
        SELECT role, content
        FROM ai_conversation_messages
        WHERE conversation_key = %s
        ORDER BY id DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (conversation_key, int(limit)))
            rows = cursor.fetchall()

    return [
        {
            "role": str(row["role"]),
            "content": str(row["content"]),
        }
        for row in reversed(rows)
    ]


def save_ai_conversation_message(
    conversation_key: str,
    role: str,
    content: str,
) -> None:
    if role not in {"user", "assistant"}:
        raise ValueError("role inválido para memória da IA.")

    query = """
        INSERT INTO ai_conversation_messages (
            conversation_key,
            role,
            content
        )
        VALUES (%s, %s, %s);
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    conversation_key,
                    role,
                    str(content),
                ),
            )


def prune_ai_conversation_messages(
    conversation_key: str,
    keep: int = 12,
) -> None:
    query = """
        DELETE FROM ai_conversation_messages
        WHERE conversation_key = %s
          AND (
              created_at < NOW() - INTERVAL '30 days'
              OR id NOT IN (
                  SELECT id
                  FROM ai_conversation_messages
                  WHERE conversation_key = %s
                  ORDER BY id DESC
                  LIMIT %s
              )
          );
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    conversation_key,
                    conversation_key,
                    int(keep),
                ),
            )

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


def fetch_active_solis_alarm_events(
    station_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            id,
            station_id,
            device_sn,
            alarm_code,
            alarm_level,
            alarm_message,
            advice,
            alarm_begin_time,
            alarm_end_time,
            state,
            status,
            updated_at
        FROM solis_alarm_events
        WHERE station_id = %s
          AND status = 'active'
        ORDER BY alarm_begin_time DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (str(station_id), int(limit)))
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_solis_alarm_by_code(
    station_id: str,
    alarm_code: str,
) -> dict[str, Any] | None:
    query = """
        SELECT
            id,
            station_id,
            device_sn,
            alarm_code,
            alarm_level,
            alarm_message,
            advice,
            alarm_begin_time,
            alarm_end_time,
            state,
            status,
            updated_at
        FROM solis_alarm_events
        WHERE station_id = %s
          AND alarm_code = %s
        ORDER BY alarm_begin_time DESC
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (str(station_id), str(alarm_code).strip()),
            )
            row = cursor.fetchone()

    return dict(row) if row else None



# ---------------------------------------------------------------------------
# Histórico real de manutenção
# ---------------------------------------------------------------------------

MAINTENANCE_EVENT_TYPES = {
    "cleaning",
    "inspection",
    "preventive",
    "corrective",
    "repair",
    "replacement",
    "electrical",
    "inverter",
    "panel",
    "other",
}


def save_maintenance_history_event(
    provider: str,
    station_id: str,
    event_date: date,
    event_type: str,
    description: str,
    performed_by: str = "",
    notes: str = "",
    source: str = "whatsapp",
) -> dict[str, Any]:
    normalized_type = str(event_type or "").strip().lower()
    if normalized_type not in MAINTENANCE_EVENT_TYPES:
        raise ValueError("Tipo de manutenção inválido.")

    description = str(description or "").strip()
    if not description:
        raise ValueError("Descrição da manutenção não pode ser vazia.")

    if len(description) > 1200:
        raise ValueError("Descrição da manutenção é muito longa.")

    performed_by = str(performed_by or "").strip()
    notes = str(notes or "").strip()
    provider = str(provider)
    station_id = str(station_id)
    event_date_iso = event_date.isoformat()
    source = str(source or "whatsapp")

    insert_query = """
        INSERT INTO maintenance_history (
            provider,
            station_id,
            event_date,
            event_type,
            description,
            performed_by,
            notes,
            source
        )
        VALUES (%s, %s, %s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s)
        ON CONFLICT DO NOTHING
        RETURNING
            id,
            event_date,
            event_type,
            description,
            performed_by,
            notes,
            source,
            created_at;
    """

    select_query = """
        SELECT
            id,
            event_date,
            event_type,
            description,
            performed_by,
            notes,
            source,
            created_at
        FROM maintenance_history
        WHERE provider = %s
          AND station_id = %s
          AND event_date = %s
          AND event_type = %s
          AND description = %s
        ORDER BY id DESC
        LIMIT 1;
    """

    params = (
        provider,
        station_id,
        event_date_iso,
        normalized_type,
        description,
        performed_by,
        notes,
        source,
    )

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(insert_query, params)
            row = cursor.fetchone()

            if row:
                return dict(row)

            cursor.execute(
                select_query,
                (
                    provider,
                    station_id,
                    event_date_iso,
                    normalized_type,
                    description,
                ),
            )
            existing = cursor.fetchone()

    if not existing:
        raise RuntimeError(
            "Não foi possível registrar nem recuperar a manutenção."
        )

    return dict(existing)


def fetch_maintenance_history(
    provider: str,
    station_id: str,
    limit: int = 10,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 30))

    conditions = [
        "provider = %s",
        "station_id = %s",
    ]
    params: list[Any] = [str(provider), str(station_id)]

    if start_date is not None:
        conditions.append("event_date >= %s")
        params.append(start_date.isoformat())

    if end_date is not None:
        conditions.append("event_date <= %s")
        params.append(end_date.isoformat())

    params.append(safe_limit)

    query = f"""
        SELECT
            id,
            event_date,
            event_type,
            description,
            performed_by,
            notes,
            source,
            created_at
        FROM maintenance_history
        WHERE {" AND ".join(conditions)}
        ORDER BY event_date DESC, id DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Histórico de curvas e inteligência de desempenho
# ---------------------------------------------------------------------------

def _nullable_decimal(value):
    if value is None:
        return None
    return to_decimal(value)


def save_solar_curve_points(
    provider: str,
    station_id: str,
    report_date: date,
    points: list[dict[str, Any]],
    peak_power_kwp,
    source: str,
) -> int:
    peak_w = None
    if peak_power_kwp not in (None, ""):
        peak_w = float(peak_power_kwp) * 1000.0

    rows = []
    for point in points:
        timestamp = point.get("timestamp")
        power_w = point.get("power_w")

        if timestamp is None or power_w is None:
            continue

        minute_of_day = int(timestamp.hour) * 60 + int(timestamp.minute)
        normalized_power = (
            float(power_w) / peak_w
            if peak_w and peak_w > 0
            else None
        )

        rows.append(
            (
                str(provider),
                str(station_id),
                report_date.isoformat(),
                minute_of_day,
                timestamp,
                to_decimal(power_w),
                _nullable_decimal(normalized_power),
                str(source),
                str(point.get("quality") or "observed"),
            )
        )

    if not rows:
        return 0

    query = """
        INSERT INTO solar_curve_points (
            provider,
            station_id,
            report_date,
            minute_of_day,
            measured_at,
            power_w,
            normalized_power,
            source,
            quality,
            collected_at
        )
        VALUES %s
        ON CONFLICT (
            provider,
            station_id,
            report_date,
            minute_of_day
        )
        DO UPDATE SET
            measured_at = EXCLUDED.measured_at,
            power_w = EXCLUDED.power_w,
            normalized_power = EXCLUDED.normalized_power,
            source = EXCLUDED.source,
            quality = EXCLUDED.quality,
            collected_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            execute_values(cursor, query, rows)

    return len(rows)


def fetch_solar_curve_points(
    provider: str,
    station_id: str,
    report_date: date,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            report_date,
            minute_of_day,
            measured_at,
            power_w,
            normalized_power,
            source,
            quality
        FROM solar_curve_points
        WHERE provider = %s
          AND station_id = %s
          AND report_date = %s
        ORDER BY minute_of_day ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    report_date.isoformat(),
                ),
            )
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_solar_curve_history(
    provider: str,
    station_id: str,
    before_date: date,
    lookback_days: int = 45,
) -> list[dict[str, Any]]:
    safe_days = max(1, min(int(lookback_days), 366))

    query = """
        SELECT
            cp.report_date,
            cp.minute_of_day,
            cp.measured_at,
            cp.power_w,
            cp.normalized_power,
            cp.source,
            w.PERCENTUALNUVENS AS cloud_cover_percent,
            w.CHUVAMM AS rain_mm,
            CASE
                WHEN w.TEMPERATURAMINIMAC IS NULL
                 AND w.TEMPERATURAMAXIMAC IS NULL
                    THEN NULL
                WHEN w.TEMPERATURAMINIMAC IS NULL
                    THEN w.TEMPERATURAMAXIMAC
                WHEN w.TEMPERATURAMAXIMAC IS NULL
                    THEN w.TEMPERATURAMINIMAC
                ELSE (
                    w.TEMPERATURAMINIMAC
                    + w.TEMPERATURAMAXIMAC
                ) / 2.0
            END AS average_temperature_c
        FROM solar_curve_points cp
        LEFT JOIN daily_weather w
          ON w.FORNECEDOR = cp.provider
         AND w.IDUSINA = cp.station_id
         AND w.DATARELATORIO = cp.report_date
        WHERE cp.provider = %s
          AND cp.station_id = %s
          AND cp.report_date < %s
          AND cp.report_date >= %s::date - (%s * INTERVAL '1 day')
        ORDER BY cp.report_date DESC, cp.minute_of_day ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    before_date.isoformat(),
                    before_date.isoformat(),
                    safe_days,
                ),
            )
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def save_daily_curve_analysis(
    provider: str,
    station_id: str,
    report_date: date,
    source: str,
    peak_power_kwp,
    analysis: dict[str, Any],
    weather: dict[str, Any] | None = None,
) -> None:
    weather = weather or {}
    profile = analysis.get("current_profile") or {}
    scores = analysis.get("component_scores") or {}
    persistence = analysis.get("persistence") or {}

    query = """
        INSERT INTO daily_curve_analysis (
            provider,
            station_id,
            report_date,
            algorithm_version,
            source,
            peak_power_kwp,
            samples,
            energy_kwh,
            normalized_energy_hours,
            active_hours,
            peak_fraction,
            generation_start_minute,
            generation_end_minute,
            shape_score,
            energy_score,
            peak_score,
            interruption_score,
            window_score,
            volatility_score,
            telemetry_score,
            anomaly_score,
            status,
            confidence,
            persistence_ratio,
            persistent_days,
            comparable_days,
            baseline_days_used,
            weather_context,
            cloud_cover_percent,
            rain_mm,
            avg_temperature_c,
            details,
            calculated_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (provider, station_id, report_date)
        DO UPDATE SET
            algorithm_version = EXCLUDED.algorithm_version,
            source = EXCLUDED.source,
            peak_power_kwp = EXCLUDED.peak_power_kwp,
            samples = EXCLUDED.samples,
            energy_kwh = EXCLUDED.energy_kwh,
            normalized_energy_hours = EXCLUDED.normalized_energy_hours,
            active_hours = EXCLUDED.active_hours,
            peak_fraction = EXCLUDED.peak_fraction,
            generation_start_minute = EXCLUDED.generation_start_minute,
            generation_end_minute = EXCLUDED.generation_end_minute,
            shape_score = EXCLUDED.shape_score,
            energy_score = EXCLUDED.energy_score,
            peak_score = EXCLUDED.peak_score,
            interruption_score = EXCLUDED.interruption_score,
            window_score = EXCLUDED.window_score,
            volatility_score = EXCLUDED.volatility_score,
            telemetry_score = EXCLUDED.telemetry_score,
            anomaly_score = EXCLUDED.anomaly_score,
            status = EXCLUDED.status,
            confidence = EXCLUDED.confidence,
            persistence_ratio = EXCLUDED.persistence_ratio,
            persistent_days = EXCLUDED.persistent_days,
            comparable_days = EXCLUDED.comparable_days,
            baseline_days_used = EXCLUDED.baseline_days_used,
            weather_context = EXCLUDED.weather_context,
            cloud_cover_percent = EXCLUDED.cloud_cover_percent,
            rain_mm = EXCLUDED.rain_mm,
            avg_temperature_c = EXCLUDED.avg_temperature_c,
            details = EXCLUDED.details,
            calculated_at = NOW(),
            updated_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    report_date.isoformat(),
                    str(analysis.get("algorithm_version") or ""),
                    str(source),
                    _nullable_decimal(peak_power_kwp),
                    int(profile.get("samples") or 0),
                    _nullable_decimal(profile.get("energy_kwh")),
                    _nullable_decimal(
                        profile.get("normalized_energy_hours")
                    ),
                    _nullable_decimal(profile.get("active_hours")),
                    _nullable_decimal(
                        profile.get("peak_fraction_of_installed")
                    ),
                    profile.get("generation_start_minute"),
                    profile.get("generation_end_minute"),
                    _nullable_decimal(scores.get("shape")),
                    _nullable_decimal(scores.get("energy")),
                    _nullable_decimal(scores.get("peak")),
                    _nullable_decimal(scores.get("interruption")),
                    _nullable_decimal(scores.get("window")),
                    _nullable_decimal(scores.get("volatility")),
                    _nullable_decimal(scores.get("telemetry")),
                    _nullable_decimal(analysis.get("anomaly_score")),
                    str(analysis.get("status") or "inconclusive"),
                    str(analysis.get("confidence") or "very_low"),
                    _nullable_decimal(persistence.get("ratio")),
                    int(persistence.get("persistent_days") or 0),
                    int(persistence.get("comparable_days") or 0),
                    int(analysis.get("baseline_days_used") or 0),
                    str(
                        analysis.get("weather_context")
                        or "mixed_or_unknown"
                    ),
                    _nullable_decimal(
                        weather.get("average_cloud_cover_percent")
                    ),
                    _nullable_decimal(
                        weather.get("total_precipitation_mm")
                    ),
                    _nullable_decimal(
                        weather.get("average_temperature_c")
                    ),
                    json.dumps(analysis, ensure_ascii=False, default=str),
                ),
            )


def fetch_daily_curve_analysis(
    provider: str,
    station_id: str,
    report_date: date,
) -> dict[str, Any] | None:
    query = """
        SELECT *
        FROM daily_curve_analysis
        WHERE provider = %s
          AND station_id = %s
          AND report_date = %s
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    report_date.isoformat(),
                ),
            )
            row = cursor.fetchone()

    return dict(row) if row else None


def fetch_daily_curve_analysis_history(
    provider: str,
    station_id: str,
    before_date: date,
    limit: int = 45,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 366))

    query = """
        SELECT *
        FROM daily_curve_analysis
        WHERE provider = %s
          AND station_id = %s
          AND report_date < %s
        ORDER BY report_date DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    before_date.isoformat(),
                    safe_limit,
                ),
            )
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_daily_curve_analysis_range(
    provider: str,
    station_id: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM daily_curve_analysis
        WHERE provider = %s
          AND station_id = %s
          AND report_date BETWEEN %s AND %s
        ORDER BY report_date ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                query,
                (
                    str(provider),
                    str(station_id),
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )
            rows = cursor.fetchall()

    return [dict(row) for row in rows]
