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
            updated_at
        )
        VALUES (%s, %s, %s, %s, NOW())
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
                ),
            )


def fetch_generation_for_month(
    year_month: str,
    station_id: str | None = None,
) -> tuple[str, Decimal] | None:
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
