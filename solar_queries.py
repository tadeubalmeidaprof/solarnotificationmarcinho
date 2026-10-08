import logging
import os
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from config import env, required_env
from curve_analysis import analyze_power_curve
from database import (
    fetch_active_solis_alarm_events,
    fetch_daily_generation_range,
    fetch_daily_weather_for_date,
    fetch_generation_for_month,
    fetch_maintenance_history,
    fetch_open_maintenance_alert,
    save_maintenance_history_event,
    fetch_solis_alarm_by_code,
)
from maintenance import analyze_operational_performance
from savings_calculator import (
    calculate_savings_with_fio_b,
    calculate_savings_without_fio_b,
)
from solisclient import SolisAPIError, SolisClient, SolisCredentials
from utils import br_number, to_decimal
from weather import get_daily_weather, get_weather_window


PROVIDER = "Solis"
REPORT_TIMEZONE = ZoneInfo("America/Bahia")
CONNECTION_TYPE = "monofasico"
MAX_HISTORY_DAYS = 93
MAINTENANCE_ALERT_TYPES = ("inverter_offline", "possible_soiling")

STATE_LABELS = {
    1: "Normal",
    2: "Offline",
    3: "Alarme",
}

logger = logging.getLogger(__name__)


def _client() -> SolisClient:
    station_id = required_env("SOLIS_STATION_ID")
    return SolisClient(
        SolisCredentials(
            api_id=required_env("SOLIS_API_ID"),
            api_secret=required_env("SOLIS_API_SECRET"),
            base_url=required_env("SOLIS_BASE_URL"),
            station_id=station_id,
        )
    )


def _station_id() -> str:
    return required_env("SOLIS_STATION_ID")


def _number_or_none(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _iso_or_text(value):
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _parse_date(value: str, field_name: str = "data") -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(
            f"{field_name} inválida. Use o formato YYYY-MM-DD."
        ) from exc


def _parse_year_month(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError as exc:
        raise ValueError("Mês inválido. Use o formato YYYY-MM.") from exc
    return parsed.strftime("%Y-%m")


def _decimal_env(name: str) -> Decimal | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return to_decimal(raw)


def _records_from_page(response: dict) -> list[dict]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        page = data.get("page")
        if isinstance(page, dict) and isinstance(page.get("records"), list):
            return [row for row in page["records"] if isinstance(row, dict)]
        if isinstance(data.get("records"), list):
            return [row for row in data["records"] if isinstance(row, dict)]
    return []


def _data_rows(response: dict) -> list[dict]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            return [row for row in data["records"] if isinstance(row, dict)]
        page = data.get("page")
        if isinstance(page, dict) and isinstance(page.get("records"), list):
            return [row for row in page["records"] if isinstance(row, dict)]
    return []


def _find_station(response: dict, station_id: str) -> dict:
    for station in _records_from_page(response):
        if str(station.get("id") or "") == str(station_id):
            return station
    raise SolisAPIError(
        f"Usina com ID {station_id} não encontrada na resposta da Solis."
    )


def _live_station() -> dict:
    station_id = _station_id()
    return _find_station(_client().list_stations(), station_id)


def _state_label(value) -> str:
    try:
        key = int(value)
    except (TypeError, ValueError):
        return "Sem informação"
    return STATE_LABELS.get(key, f"Estado {key}")


def _unit_text(value) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _capacity_kwp(station: dict) -> float | None:
    value = _number_or_none(station.get("capacity"))
    unit = _unit_text(station.get("capacityStr"))

    if value is not None and value > 0:
        if "mw" in unit:
            return value * 1000
        if "w" in unit and "kw" not in unit:
            return value / 1000
        return value

    value = _number_or_none(station.get("inverterPower"))
    if value is None or value <= 0:
        return None

    return value / 1000 if value > 1000 else value


def _power_to_kw(value, unit_hint: str = "") -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None

    unit = _unit_text(unit_hint)
    if "mw" in unit:
        return number * 1000
    if "kw" in unit:
        return number
    if "w" in unit:
        return number / 1000

    return number / 1000 if number > 100 else number


def _coordinates() -> tuple[float, float] | None:
    lat_raw = env("STATION_LATITUDE")
    lon_raw = env("STATION_LONGITUDE")

    if lat_raw and lon_raw:
        return (
            float(lat_raw.replace(",", ".")),
            float(lon_raw.replace(",", ".")),
        )

    try:
        station = _live_station()
    except Exception:
        return None

    lat = _number_or_none(station.get("latitude"))
    lon = _number_or_none(station.get("longitude"))
    if lat is None or lon is None:
        return None
    return lat, lon


def get_generation_summary() -> dict[str, str]:
    station = _live_station()
    return {
        "today_kwh": br_number(station.get("dayEnergy"), 1),
        "month_kwh": br_number(station.get("monthEnergy"), 1),
    }


def get_plant_status() -> dict:
    station = _live_station()
    return {
        "status": _state_label(station.get("state")),
        "power_now_kw": _power_to_kw(
            station.get("power"),
            station.get("powerStr"),
        ),
        "energy_today_kwh": _number_or_none(station.get("dayEnergy")),
        "energy_month_kwh": _number_or_none(station.get("monthEnergy")),
        "energy_year_kwh": _number_or_none(station.get("yearEnergy")),
        "energy_total_kwh": _number_or_none(station.get("allEnergy")),
        "installed_capacity_kwp": _capacity_kwp(station),
    }


def _alarm_identity(alarm: dict) -> tuple:
    return (
        str(alarm.get("alarmCode") or alarm.get("alarm_code") or ""),
        str(alarm.get("alarmBeginTime") or alarm.get("alarm_begin_time") or ""),
        str(alarm.get("alarmDeviceSn") or alarm.get("device_sn") or ""),
    )


def _live_active_alarms() -> list[dict]:
    client = _client()
    station_id = _station_id()
    alarms = []

    for state in (0, 1):
        response = client.list_alarms(
            station_id=station_id,
            page_size=100,
            state=state,
        )
        for row in _records_from_page(response):
            if row.get("alarmEndTime"):
                continue
            alarms.append(row)

    unique = {}
    for alarm in alarms:
        unique[_alarm_identity(alarm)] = alarm
    return list(unique.values())


def get_active_faults_summary() -> dict:
    faults = []
    source = "solis_live"

    try:
        live = _live_active_alarms()
        for fault in live[-10:]:
            faults.append(
                {
                    "code": str(fault.get("alarmCode") or "").strip(),
                    "message": str(
                        fault.get("alarmMsg")
                        or "Alarme informado pela SolisCloud"
                    ).strip(),
                    "advice": str(fault.get("advice") or "").strip(),
                    "level": fault.get("alarmLevel"),
                    "started_at": _iso_or_text(fault.get("alarmBeginTime")),
                    "status": "active",
                }
            )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar alarmes ativos ao vivo na Solis: %s",
            exc,
        )
        source = "database_fallback"
        rows = fetch_active_solis_alarm_events(_station_id(), limit=10)
        for fault in rows:
            faults.append(
                {
                    "code": str(fault.get("alarm_code") or "").strip(),
                    "message": str(
                        fault.get("alarm_message")
                        or "Alarme informado pela SolisCloud"
                    ).strip(),
                    "advice": str(fault.get("advice") or "").strip(),
                    "level": fault.get("alarm_level"),
                    "started_at": _iso_or_text(
                        fault.get("alarm_begin_time")
                    ),
                    "status": "active",
                }
            )

    return {
        "active_fault_count": len(faults),
        "faults": faults,
        "source": source,
    }


def get_maintenance_status() -> dict:
    station_id = _station_id()
    alerts = []

    for alert_type in MAINTENANCE_ALERT_TYPES:
        alert = fetch_open_maintenance_alert(
            provider=PROVIDER,
            station_id=station_id,
            alert_type=alert_type,
        )
        if not alert:
            continue

        alerts.append(
            {
                "type": str(alert.get("alert_type") or alert_type),
                "severity": str(alert.get("severity") or "warning"),
                "status": str(alert.get("status") or ""),
                "drop_percentage": _number_or_none(
                    alert.get("drop_percentage")
                ),
                "expected_generation_kwh": _number_or_none(
                    alert.get("expected_generation_kwh")
                ),
                "observed_generation_kwh": _number_or_none(
                    alert.get("observed_generation_kwh")
                ),
                "probable_cause": str(alert.get("probable_cause") or ""),
                "reference_start_date": _iso_or_text(
                    alert.get("reference_start_date")
                ),
                "reference_end_date": _iso_or_text(
                    alert.get("reference_end_date")
                ),
            }
        )

    return {
        "has_open_alert": bool(alerts),
        "alerts": alerts,
    }


def _months_between(start: date, end: date) -> list[str]:
    current = start.replace(day=1)
    last = end.replace(day=1)
    months = []

    while current <= last:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(
                year=current.year + 1,
                month=1,
            )
        else:
            current = current.replace(month=current.month + 1)

    return months


def _solis_generation_history(
    start: date,
    end: date,
) -> list[dict]:
    client = _client()
    station_id = _station_id()
    rows_by_date = {}

    for year_month in _months_between(start, end):
        response = client.station_month(
            year_month=year_month,
            station_id=station_id,
            time_zone=-3,
            money="BRL",
        )
        for row in _data_rows(response):
            row_date = str(row.get("date") or "").strip()[:10]
            try:
                parsed = date.fromisoformat(row_date)
            except ValueError:
                continue
            if parsed < start or parsed > end:
                continue

            energy = _number_or_none(row.get("energy"))
            if energy is None:
                continue

            radiation_kwh_m2 = _number_or_none(row.get("totalRKwh"))
            rows_by_date[row_date] = {
                "date": row_date,
                "generation_kwh": energy,
                "solar_radiation_kwh_m2": radiation_kwh_m2,
                "source": "solis_station_month",
            }

    return [rows_by_date[key] for key in sorted(rows_by_date)]


def get_generation_period(start_date: str, end_date: str) -> dict:
    start = _parse_date(start_date, "Data inicial")
    end = _parse_date(end_date, "Data final")

    if end < start:
        raise ValueError("A data final não pode ser anterior à data inicial.")

    expected_days = (end - start).days + 1
    if expected_days > MAX_HISTORY_DAYS:
        raise ValueError(
            f"O período máximo por consulta é de {MAX_HISTORY_DAYS} dias."
        )

    today = datetime.now(REPORT_TIMEZONE).date()
    if start > today:
        raise ValueError(
            "Não é possível consultar geração de um período futuro."
        )

    if end > today:
        end = today
        expected_days = (end - start).days + 1

    station_id = _station_id()
    db_rows = fetch_daily_generation_range(
        provider=PROVIDER,
        station_id=station_id,
        start_date=start,
        end_date=end,
    )

    by_date = {}
    for row in db_rows:
        row_date = _iso_or_text(row.get("report_date"))
        if not row_date:
            continue
        by_date[row_date] = {
            "date": row_date,
            "generation_kwh": _number_or_none(
                row.get("generation_day_kwh")
            ),
            "inverter_status": str(
                row.get("inverter_status") or ""
            ),
            "source": "monitoring_history",
        }

    expected_dates = {
        (start + timedelta(days=offset)).isoformat()
        for offset in range(expected_days)
    }

    if not expected_dates.issubset(by_date):
        try:
            for row in _solis_generation_history(start, end):
                if row["date"] in expected_dates:
                    by_date[row["date"]] = row
        except Exception as exc:
            logger.warning(
                "Falha ao consultar histórico diário Solis: %s",
                exc,
            )

    if start <= today <= end:
        try:
            station = _live_station()
            by_date[today.isoformat()] = {
                "date": today.isoformat(),
                "generation_kwh": _number_or_none(
                    station.get("dayEnergy")
                ),
                "inverter_status": _state_label(
                    station.get("state")
                ),
                "source": "solis_live",
            }
        except Exception:
            pass

    ordered = [
        by_date[key]
        for key in sorted(by_date)
        if key in expected_dates
    ]

    total = sum(
        item.get("generation_kwh") or 0
        for item in ordered
    )
    days_with_data = len(ordered)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_generation_kwh": round(total, 3),
        "days_expected": expected_days,
        "days_with_data": days_with_data,
        "complete_history": days_with_data == expected_days,
        "missing_days": max(expected_days - days_with_data, 0),
        "daily": ordered[-14:],
        "daily_details_truncated": len(ordered) > 14,
    }


def get_recent_generation(days: int) -> dict:
    try:
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("Quantidade de dias inválida.") from exc

    if days < 1 or days > 31:
        raise ValueError(
            "A consulta de últimos dias aceita valores entre 1 e 31."
        )

    today = datetime.now(REPORT_TIMEZONE).date()
    start = today - timedelta(days=days - 1)
    return get_generation_period(
        start_date=start.isoformat(),
        end_date=today.isoformat(),
    )


def get_monthly_generation(year_month: str) -> dict:
    normalized = _parse_year_month(year_month)
    current_month = datetime.now(REPORT_TIMEZONE).strftime("%Y-%m")
    station_id = _station_id()

    snapshot = fetch_generation_for_month(
        year_month=normalized,
        station_id=station_id,
        provider=PROVIDER,
    )
    snapshot_value = float(snapshot[1]) if snapshot else None

    if normalized != current_month:
        if snapshot_value is not None:
            return {
                "available": True,
                "year_month": normalized,
                "generation_kwh": snapshot_value,
                "source": "monthly_snapshot",
            }

        try:
            history = _solis_generation_history(
                date.fromisoformat(normalized + "-01"),
                (
                    date.fromisoformat(normalized + "-28")
                    + timedelta(days=4)
                ).replace(day=1) - timedelta(days=1),
            )
        except Exception:
            history = []

        if history:
            return {
                "available": True,
                "year_month": normalized,
                "generation_kwh": round(
                    sum(
                        row.get("generation_kwh") or 0
                        for row in history
                    ),
                    3,
                ),
                "source": "solis_history",
            }

        return {
            "available": False,
            "year_month": normalized,
            "reason": "monthly_data_unavailable",
        }

    live_value = None
    try:
        live_value = _number_or_none(
            _live_station().get("monthEnergy")
        )
    except Exception:
        pass

    if live_value is not None and (
        snapshot_value is None
        or live_value >= snapshot_value
    ):
        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": live_value,
            "source": "solis_live",
        }

    if snapshot_value is not None:
        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": snapshot_value,
            "source": "monthly_snapshot_fallback",
        }

    return {
        "available": False,
        "year_month": normalized,
        "reason": "current_month_unavailable",
    }


def compare_months(first_year_month: str, second_year_month: str) -> dict:
    first_normalized = _parse_year_month(first_year_month)
    second_normalized = _parse_year_month(second_year_month)

    first = get_monthly_generation(first_normalized)
    second = get_monthly_generation(second_normalized)

    if not first.get("available") or not second.get("available"):
        return {
            "available": False,
            "first": first,
            "second": second,
            "reason": "missing_monthly_data",
        }

    now = datetime.now(REPORT_TIMEZONE)
    current_month = now.strftime("%Y-%m")
    current_day = now.day

    first_value = float(first.get("generation_kwh") or 0)
    second_value = float(second.get("generation_kwh") or 0)
    raw_difference = second_value - first_value
    raw_percentage = (
        (raw_difference / first_value) * 100
        if first_value > 0
        else None
    )

    result = {
        "available": True,
        "first": first,
        "second": second,
        "raw_difference_kwh": round(raw_difference, 3),
        "raw_difference_percent_relative_to_first": (
            round(raw_percentage, 2)
            if raw_percentage is not None
            else None
        ),
        "comparison_mode": "full_months",
        "months_are_directly_comparable": True,
    }

    if current_month not in {
        first_normalized,
        second_normalized,
    }:
        result.update(
            {
                "difference_kwh": round(raw_difference, 3),
                "difference_percent_relative_to_first": (
                    round(raw_percentage, 2)
                    if raw_percentage is not None
                    else None
                ),
                "higher_month": (
                    second_normalized
                    if second_value > first_value
                    else first_normalized
                    if first_value > second_value
                    else "equal"
                ),
            }
        )
        return result

    result.update(
        {
            "comparison_mode": "equivalent_partial_period",
            "months_are_directly_comparable": False,
            "partial_month": current_month,
            "partial_month_through_day": current_day,
            "warning": (
                "Um dos meses ainda está em andamento. "
                "Os totais mensais brutos não devem ser comparados diretamente."
            ),
        }
    )

    def equivalent_period(year_month: str) -> dict:
        parsed = datetime.strptime(year_month, "%Y-%m")
        start = date(parsed.year, parsed.month, 1)
        try:
            end = date(
                parsed.year,
                parsed.month,
                current_day,
            )
        except ValueError:
            next_month = (
                start.replace(year=start.year + 1, month=1)
                if start.month == 12
                else start.replace(month=start.month + 1)
            )
            end = next_month - timedelta(days=1)

        return get_generation_period(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    try:
        first_period = equivalent_period(first_normalized)
        second_period = equivalent_period(second_normalized)
    except Exception:
        return result

    if (
        not first_period.get("complete_history")
        or not second_period.get("complete_history")
    ):
        result["fair_comparison_available"] = False
        result["fair_comparison"] = {
            "through_day": current_day,
            "first_period": first_period,
            "second_period": second_period,
        }
        return result

    first_partial = float(
        first_period.get("total_generation_kwh") or 0
    )
    second_partial = float(
        second_period.get("total_generation_kwh") or 0
    )
    fair_difference = second_partial - first_partial
    fair_percentage = (
        (fair_difference / first_partial) * 100
        if first_partial > 0
        else None
    )

    result["fair_comparison_available"] = True
    result["fair_comparison"] = {
        "through_day": current_day,
        "first_year_month": first_normalized,
        "second_year_month": second_normalized,
        "first_generation_kwh": first_partial,
        "second_generation_kwh": second_partial,
        "difference_kwh": round(fair_difference, 3),
        "difference_percent_relative_to_first": (
            round(fair_percentage, 2)
            if fair_percentage is not None
            else None
        ),
        "higher_period": (
            second_normalized
            if second_partial > first_partial
            else first_normalized
            if first_partial > second_partial
            else "equal"
        ),
    }
    return result


def get_savings_summary(year_month: str) -> dict:
    monthly = get_monthly_generation(year_month)
    if not monthly.get("available"):
        return {
            "available": False,
            "year_month": monthly.get("year_month"),
            "reason": "generation_unavailable",
        }

    tariff = _decimal_env("ENERGY_TARIFF")
    if tariff is None or tariff <= 0:
        return {
            "available": False,
            "year_month": monthly["year_month"],
            "reason": "tariff_not_configured",
        }

    generation = to_decimal(monthly.get("generation_kwh"))
    average_consumption = _decimal_env(
        "AVERAGE_CONSUMPTION_KWH"
    )
    if average_consumption is None:
        average_consumption = generation + Decimal("30")

    tusd_fio_b = _decimal_env("TUSD_FIO_B_KWH")
    fio_b_percentage = _decimal_env("FIO_B_PERCENTAGE")

    if (
        tusd_fio_b is not None
        and fio_b_percentage is not None
    ):
        calculation = calculate_savings_with_fio_b(
            generation_month_kwh=generation,
            average_consumption_month_kwh=average_consumption,
            final_tariff_kwh=tariff,
            tusd_fio_b_kwh=tusd_fio_b,
            fio_b_percentage=fio_b_percentage,
            connection_type=CONNECTION_TYPE,
        )
        mode = "com_fio_b"
    else:
        calculation = calculate_savings_without_fio_b(
            generation_month_kwh=generation,
            average_consumption_month_kwh=average_consumption,
            final_tariff_kwh=tariff,
            connection_type=CONNECTION_TYPE,
        )
        mode = "estimativa_sem_fio_b"

    return {
        "available": True,
        "year_month": monthly["year_month"],
        "generation_kwh": float(generation),
        "estimated_savings_brl": float(
            calculation["estimated_savings"]
        ),
        "compensated_energy_kwh": float(
            calculation["compensated_energy_kwh"]
        ),
        "generated_credits_kwh": float(
            calculation["generated_credits_kwh"]
        ),
        "calculation_mode": mode,
    }


def _sanitize_weather(
    data: dict,
    report_date: date,
    source: str,
) -> dict:
    return {
        "available": True,
        "date": report_date.isoformat(),
        "cloud_cover_percent": _number_or_none(
            data.get(
                "cloud_cover_percent",
                data.get("PERCENTUALNUVENS"),
            )
        ),
        "rainfall_mm": _number_or_none(
            data.get("rainfall_mm", data.get("CHUVAMM"))
        ),
        "solar_radiation_wh_m2": _number_or_none(
            data.get(
                "solar_radiation_wh_m2",
                data.get("RADIACAOSOLARWHM2"),
            )
        ),
        "sunshine_hours": _number_or_none(
            data.get(
                "sunshine_hours",
                data.get("HORASSOL"),
            )
        ),
        "temperature_min_c": _number_or_none(
            data.get(
                "temperature_min_c",
                data.get("TEMPERATURAMINIMAC"),
            )
        ),
        "temperature_max_c": _number_or_none(
            data.get(
                "temperature_max_c",
                data.get("TEMPERATURAMAXIMAC"),
            )
        ),
        "weather_class": str(
            data.get(
                "weather_class",
                data.get("CLASSIFICACAOCLIMA"),
            )
            or "unknown"
        ),
        "source": source,
    }


def get_weather_summary(report_date: str) -> dict:
    parsed_date = _parse_date(report_date, "Data do clima")
    today = datetime.now(REPORT_TIMEZONE).date()

    if parsed_date > today:
        raise ValueError(
            "Não é possível consultar clima futuro nesta ferramenta."
        )

    stored = fetch_daily_weather_for_date(
        provider=PROVIDER,
        station_id=_station_id(),
        report_date=parsed_date,
    )
    if stored:
        return _sanitize_weather(
            stored,
            parsed_date,
            "monitoring_history",
        )

    coordinates = _coordinates()
    if coordinates is None:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "station_location_not_configured",
        }

    try:
        weather = get_daily_weather(
            latitude=coordinates[0],
            longitude=coordinates[1],
            report_date=parsed_date,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar provedor climático para %s: %s",
            parsed_date.isoformat(),
            exc,
        )
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "weather_provider_unavailable",
        }

    return _sanitize_weather(
        weather,
        parsed_date,
        str(weather.get("PROVEDORCLIMA") or "weather_provider"),
    )


def get_weather_window_summary(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(report_date, "Data do clima")
    today = datetime.now(REPORT_TIMEZONE).date()

    if parsed_date > today:
        raise ValueError(
            "Não é possível consultar clima futuro nesta ferramenta."
        )

    coordinates = _coordinates()
    if coordinates is None:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "station_location_not_configured",
        }

    try:
        return get_weather_window(
            latitude=coordinates[0],
            longitude=coordinates[1],
            report_date=parsed_date,
            start_hour=start_hour,
            end_hour=end_hour,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar clima horário para %s (%s-%s): %s",
            parsed_date.isoformat(),
            start_hour,
            end_hour,
            exc,
        )
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "reason": "weather_provider_unavailable",
        }


def _parse_power_timestamp(
    value,
    report_date: date,
) -> datetime:
    if value is None:
        raise ValueError("Timestamp vazio.")

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(
            timestamp,
            REPORT_TIMEZONE,
        )

    text = str(value).strip()
    if not text:
        raise ValueError("Timestamp vazio.")

    if text.isdigit():
        return _parse_power_timestamp(int(text), report_date)

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_time = datetime.strptime(
                text,
                fmt,
            ).time()
            return datetime.combine(
                report_date,
                parsed_time,
                tzinfo=REPORT_TIMEZONE,
            )
        except ValueError:
            pass

    parsed = datetime.fromisoformat(
        text.replace("Z", "+00:00").replace(" ", "T")
    )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=REPORT_TIMEZONE)
    return parsed.astimezone(REPORT_TIMEZONE)


def _station_day_rows(report_date: date) -> tuple[list[dict], dict]:
    station = _live_station()
    response = _client().station_day(
        report_date=report_date.isoformat(),
        station_id=_station_id(),
        time_zone=-3,
        money="BRL",
    )
    return _data_rows(response), station


def _power_scale_to_w(
    rows: list[dict],
    peak_kwp: float | None,
    unit_hint: str,
) -> float:
    values = [
        _number_or_none(row.get("power"))
        for row in rows
    ]
    values = [value for value in values if value is not None]
    if not values:
        return 1.0

    max_value = max(values)
    unit = _unit_text(unit_hint)

    if "kw" in unit and max_value <= max(
        25.0,
        (peak_kwp or 0) * 3,
    ):
        return 1000.0

    if "w" in unit and "kw" not in unit:
        return 1.0

    if peak_kwp and max_value <= max(
        20.0,
        peak_kwp * 2.5,
    ):
        return 1000.0

    return 1.0


def _summarize_power_curve(
    rows: list[dict],
    station: dict,
    parsed_date: date,
    start_hour: int,
    end_hour: int,
) -> dict:
    peak_kwp = _capacity_kwp(station)
    scale = _power_scale_to_w(
        rows,
        peak_kwp,
        str(station.get("powerStr") or ""),
    )

    points = []
    for row in rows:
        try:
            timestamp = _parse_power_timestamp(
                row.get("time"),
                parsed_date,
            )
        except (TypeError, ValueError):
            continue

        if timestamp.date() != parsed_date:
            continue
        if not start_hour <= timestamp.hour < end_hour:
            continue

        raw_power = _number_or_none(row.get("power"))
        if raw_power is None:
            continue

        radiation = _number_or_none(row.get("totalR"))
        points.append(
            {
                "timestamp": timestamp,
                "power_w": max(raw_power * scale, 0.0),
                "radiation_w_m2": (
                    max(radiation, 0.0)
                    if radiation is not None
                    else None
                ),
            }
        )

    points.sort(key=lambda item: item["timestamp"])

    if not points:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "reason": "solis_power_curve_unavailable",
        }

    meaningful_threshold_w = 50.0
    if peak_kwp and peak_kwp > 0:
        meaningful_threshold_w = max(
            50.0,
            peak_kwp * 1000 * 0.05,
        )

    active_seconds = 0.0
    energy_kwh = 0.0
    radiation_wh_m2 = 0.0
    radiation_available = False
    significant_points = []
    peak_observed_w = 0.0

    for index, point in enumerate(points):
        if index + 1 < len(points):
            delta_seconds = (
                points[index + 1]["timestamp"]
                - point["timestamp"]
            ).total_seconds()
            if delta_seconds <= 0 or delta_seconds > 1200:
                delta_seconds = 300.0
        else:
            delta_seconds = 300.0

        power_w = point["power_w"]
        peak_observed_w = max(
            peak_observed_w,
            power_w,
        )
        energy_kwh += (
            power_w * (delta_seconds / 3600)
        ) / 1000

        radiation = point.get("radiation_w_m2")
        if radiation is not None:
            radiation_available = True
            radiation_wh_m2 += (
                radiation * (delta_seconds / 3600)
            )

        if power_w >= meaningful_threshold_w:
            active_seconds += delta_seconds
            significant_points.append(point)

    active_hours = active_seconds / 3600
    equivalent_hours = None
    average_fraction = None

    if peak_kwp and peak_kwp > 0:
        equivalent_hours = energy_kwh / peak_kwp
        if active_hours > 0:
            average_fraction = (
                equivalent_hours / active_hours
            )

    return {
        "available": True,
        "date": parsed_date.isoformat(),
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "source": "solis_station_day",
        "samples": len(points),
        "plant_peak_power_kwp": (
            round(peak_kwp, 3)
            if peak_kwp is not None
            else None
        ),
        "meaningful_generation_threshold_w": round(
            meaningful_threshold_w,
            1,
        ),
        "active_generation_hours": round(
            active_hours,
            2,
        ),
        "equivalent_full_power_hours": (
            round(equivalent_hours, 2)
            if equivalent_hours is not None
            else None
        ),
        "average_power_fraction_of_peak": (
            round(average_fraction, 4)
            if average_fraction is not None
            else None
        ),
        "estimated_energy_in_window_kwh": round(
            energy_kwh,
            2,
        ),
        "solar_radiation_wh_m2": (
            round(radiation_wh_m2, 1)
            if radiation_available
            else None
        ),
        "peak_observed_kw": round(
            peak_observed_w / 1000,
            3,
        ),
        "generation_start": (
            significant_points[0]["timestamp"].strftime("%H:%M")
            if significant_points
            else None
        ),
        "generation_end": (
            significant_points[-1]["timestamp"].strftime("%H:%M")
            if significant_points
            else None
        ),
        "metric_explanation": (
            "active_generation_hours mede o tempo acima de 5% da potência "
            "instalada (mínimo 50 W). equivalent_full_power_hours é a energia "
            "da janela dividida pela capacidade instalada. "
            "solar_radiation_wh_m2 usa totalR da Solis quando disponível."
        ),
    }


def get_solar_generation_hours(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(report_date, "Data da geração")
    if parsed_date > datetime.now(REPORT_TIMEZONE).date():
        raise ValueError(
            "Não é possível consultar geração de um dia futuro."
        )

    start_hour = int(start_hour)
    end_hour = int(end_hour)
    if not 0 <= start_hour <= 23:
        raise ValueError("Horário inicial inválido.")
    if not 1 <= end_hour <= 24:
        raise ValueError("Horário final inválido.")
    if end_hour <= start_hour:
        raise ValueError(
            "O horário final deve ser posterior ao inicial."
        )

    rows, station = _station_day_rows(parsed_date)
    return _summarize_power_curve(
        rows,
        station,
        parsed_date,
        start_hour,
        end_hour,
    )


def get_performance_diagnostic(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(
        report_date,
        "Data do diagnóstico",
    )
    now = datetime.now(REPORT_TIMEZONE)

    if parsed_date > now.date():
        raise ValueError(
            "Não é possível diagnosticar um dia futuro."
        )

    start_hour = int(start_hour)
    end_hour = int(end_hour)

    if parsed_date == now.date():
        settle_hour = min(end_hour, 23)
        settle_time = datetime.combine(
            now.date(),
            dt_time(settle_hour, 20),
            tzinfo=REPORT_TIMEZONE,
        )
        if end_hour == 24:
            settle_time = datetime.combine(
                now.date(),
                dt_time(23, 59),
                tzinfo=REPORT_TIMEZONE,
            )

        if now < settle_time:
            return {
                "available": False,
                "date": parsed_date.isoformat(),
                "status": "inconclusive_window_in_progress",
                "window_label": (
                    f"{start_hour:02d}:00-{end_hour:02d}:00"
                ),
                "available_after": settle_time.isoformat(),
                "reason": (
                    "A janela solar ainda não terminou. "
                    "O SolCare não conclui manutenção com dados parciais."
                ),
            }

    current_rows, current_station = _station_day_rows(
        parsed_date
    )
    current_metric = _summarize_power_curve(
        current_rows,
        current_station,
        parsed_date,
        start_hour,
        end_hour,
    )

    if not current_metric.get("available"):
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "status": "inconclusive_missing_power_curve",
            "reason": "Curva de potência do dia indisponível.",
        }

    if not current_metric.get("plant_peak_power_kwp"):
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "status": "inconclusive_missing_peak_power",
            "reason": "Capacidade instalada da usina indisponível.",
        }

    historical_metrics = []
    for offset in range(1, 11):
        historical_date = parsed_date - timedelta(days=offset)
        try:
            rows, station = _station_day_rows(
                historical_date
            )
            metric = _summarize_power_curve(
                rows,
                station,
                historical_date,
                start_hour,
                end_hour,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao obter curva Solis de %s: %s",
                historical_date.isoformat(),
                exc,
            )
            continue

        if metric.get("available"):
            historical_metrics.append(metric)
        if len(historical_metrics) >= 8:
            break

    weather = get_weather_window_summary(
        report_date=parsed_date.isoformat(),
        start_hour=start_hour,
        end_hour=end_hour,
    )

    daily_generation_kwh = None
    try:
        generation = get_generation_period(
            start_date=parsed_date.isoformat(),
            end_date=parsed_date.isoformat(),
        )
        if generation.get("days_with_data"):
            daily_generation_kwh = _number_or_none(
                generation.get("total_generation_kwh")
            )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar geração diária no diagnóstico: %s",
            exc,
        )

    active_fault_count = None
    existing_maintenance_alert = False

    if parsed_date == now.date():
        try:
            active_fault_count = int(
                get_active_faults_summary().get(
                    "active_fault_count",
                    0,
                )
            )
        except Exception:
            active_fault_count = None

        try:
            existing_maintenance_alert = bool(
                get_maintenance_status().get(
                    "has_open_alert"
                )
            )
        except Exception:
            pass

    diagnostic = analyze_operational_performance(
        current_metric=current_metric,
        historical_metrics=historical_metrics,
        weather=(
            weather
            if weather.get("available")
            else {}
        ),
        active_fault_count=active_fault_count,
        existing_maintenance_alert=existing_maintenance_alert,
    )

    return {
        "available": True,
        "date": parsed_date.isoformat(),
        "window_label": current_metric.get(
            "window_label"
        ),
        "daily_generation_kwh": daily_generation_kwh,
        "operational_metric": {
            "active_generation_hours": current_metric.get(
                "active_generation_hours"
            ),
            "equivalent_full_power_hours": current_metric.get(
                "equivalent_full_power_hours"
            ),
            "average_power_fraction_of_peak": current_metric.get(
                "average_power_fraction_of_peak"
            ),
            "estimated_energy_in_window_kwh": current_metric.get(
                "estimated_energy_in_window_kwh"
            ),
            "plant_peak_power_kwp": current_metric.get(
                "plant_peak_power_kwp"
            ),
            "peak_observed_kw": current_metric.get(
                "peak_observed_kw"
            ),
            "solar_radiation_wh_m2": current_metric.get(
                "solar_radiation_wh_m2"
            ),
            "generation_start": current_metric.get(
                "generation_start"
            ),
            "generation_end": current_metric.get(
                "generation_end"
            ),
        },
        "weather": {
            "available": bool(weather.get("available")),
            "source": weather.get("source"),
            "average_cloud_cover_percent": weather.get(
                "average_cloud_cover_percent"
            ),
            "total_precipitation_mm": weather.get(
                "total_precipitation_mm"
            ),
            "average_temperature_c": weather.get(
                "average_temperature_c"
            ),
            "max_temperature_c": weather.get(
                "max_temperature_c"
            ),
        },
        "historical_days_found": len(historical_metrics),
        "active_fault_count": active_fault_count,
        "existing_maintenance_alert": (
            existing_maintenance_alert
        ),
        "diagnostic": diagnostic,
        "interpretation_limits": [
            (
                "Horas equivalentes são uma métrica operacional da usina, "
                "não duração meteorológica oficial de insolação."
            ),
            (
                "Temperatura ambiente é apenas contexto. Sem temperatura "
                "do módulo e coeficiente térmico dos painéis, não é aplicada "
                "correção térmica exata."
            ),
            (
                "Manutenção só é suspeitada com histórico suficiente e "
                "evidência persistente; um único dia ruim gera no máximo atenção."
            ),
        ],
    }


def get_fault_code_info(code: str) -> dict:
    normalized = str(code or "").strip()
    if not normalized:
        raise ValueError("Código de alarme vazio.")

    alarm = fetch_solis_alarm_by_code(
        station_id=_station_id(),
        alarm_code=normalized,
    )
    if not alarm:
        return {
            "known": False,
            "code": normalized,
        }

    return {
        "known": True,
        "code": normalized,
        "message": str(
            alarm.get("alarm_message") or ""
        ),
        "advice": str(alarm.get("advice") or ""),
        "level": alarm.get("alarm_level"),
        "status": str(alarm.get("status") or ""),
        "started_at": _iso_or_text(
            alarm.get("alarm_begin_time")
        ),
        "ended_at": _iso_or_text(
            alarm.get("alarm_end_time")
        ),
    }


def _safe_component(callback, *args) -> dict:
    try:
        return {
            "available": True,
            "data": callback(*args),
        }
    except Exception:
        return {"available": False}


def get_comprehensive_analysis() -> dict:
    now = datetime.now(REPORT_TIMEZONE)
    current_month = now.strftime("%Y-%m")

    return {
        "generated_at": now.isoformat(),
        "plant": _safe_component(get_plant_status),
        "active_faults": _safe_component(
            get_active_faults_summary
        ),
        "maintenance": _safe_component(
            get_maintenance_status
        ),
        "weather_today": _safe_component(
            get_weather_summary,
            now.date().isoformat(),
        ),
        "performance_today": _safe_component(
            get_performance_diagnostic,
            now.date().isoformat(),
        ),
        "savings_current_month": _safe_component(
            get_savings_summary,
            current_month,
        ),
    }


def _curve_points_for_analysis(
    rows: list[dict],
    station: dict,
    parsed_date: date,
    start_hour: int,
    end_hour: int,
) -> list[dict]:
    peak_kwp = _capacity_kwp(station)
    scale = _power_scale_to_w(
        rows,
        peak_kwp,
        str(station.get("powerStr") or ""),
    )

    points = []
    for row in rows:
        try:
            timestamp = _parse_power_timestamp(
                row.get("time"),
                parsed_date,
            )
        except (TypeError, ValueError):
            continue

        if timestamp.date() != parsed_date:
            continue
        if not start_hour <= timestamp.hour < end_hour:
            continue

        raw_power = _number_or_none(row.get("power"))
        if raw_power is None:
            continue

        points.append(
            {
                "timestamp": timestamp,
                "power_w": max(raw_power * scale, 0.0),
            }
        )

    points.sort(key=lambda item: item["timestamp"])
    return points


def get_curve_anomaly_analysis(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(
        report_date,
        "Data da análise da curva",
    )
    now = datetime.now(REPORT_TIMEZONE)

    if parsed_date > now.date():
        raise ValueError(
            "Não é possível analisar um dia futuro."
        )

    start_hour = int(start_hour)
    end_hour = int(end_hour)
    if not 0 <= start_hour <= 23:
        raise ValueError("Horário inicial inválido.")
    if not 1 <= end_hour <= 24:
        raise ValueError("Horário final inválido.")
    if end_hour <= start_hour:
        raise ValueError(
            "O horário final deve ser posterior ao inicial."
        )

    rows, station = _station_day_rows(parsed_date)
    peak_power_kwp = _capacity_kwp(station)
    points = _curve_points_for_analysis(
        rows,
        station,
        parsed_date,
        start_hour,
        end_hour,
    )

    historical_profiles = []
    for offset in range(1, 9):
        historical_date = parsed_date - timedelta(days=offset)
        try:
            historical_rows, historical_station = _station_day_rows(
                historical_date
            )
            historical_points = _curve_points_for_analysis(
                historical_rows,
                historical_station,
                historical_date,
                start_hour,
                end_hour,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao obter curva Solis histórica para análise em %s: %s",
                historical_date.isoformat(),
                exc,
            )
            continue

        if historical_points:
            historical_profiles.append(historical_points)
        if len(historical_profiles) >= 5:
            break

    try:
        weather = get_weather_window_summary(
            report_date=parsed_date.isoformat(),
            start_hour=start_hour,
            end_hour=end_hour,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar clima para análise da curva: %s",
            exc,
        )
        weather = {}

    complete_day = parsed_date < now.date()
    if parsed_date == now.date():
        settle_hour = min(end_hour, 23)
        settle_time = datetime.combine(
            now.date(),
            dt_time(settle_hour, 20),
            tzinfo=REPORT_TIMEZONE,
        )
        if end_hour == 24:
            settle_time = datetime.combine(
                now.date(),
                dt_time(23, 59),
                tzinfo=REPORT_TIMEZONE,
            )
        complete_day = now >= settle_time

    analysis = analyze_power_curve(
        points=points,
        peak_power_kwp=peak_power_kwp,
        historical_profiles=historical_profiles,
        weather=weather if weather.get("available") else {},
        complete_day=complete_day,
    )

    return {
        "date": parsed_date.isoformat(),
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "source": "solis_station_day",
        "analysis": analysis,
        "interpretation_limits": [
            (
                "Padrões na curva são indícios operacionais, não diagnóstico "
                "definitivo de defeito."
            ),
            (
                "Nuvens, chuva, sombreamento transitório e telemetria podem "
                "produzir quedas ou oscilações semelhantes."
            ),
            (
                "Conclusão de manutenção deve combinar curva, clima, alarmes, "
                "persistência e histórico."
            ),
        ],
    }


def record_maintenance_event(
    event_date: str,
    event_type: str,
    description: str,
    performed_by: str = "",
    notes: str = "",
) -> dict:
    parsed_date = _parse_date(event_date, "Data da manutenção")
    if parsed_date > datetime.now(REPORT_TIMEZONE).date():
        raise ValueError(
            "O histórico real só aceita manutenções já realizadas."
        )

    event = save_maintenance_history_event(
        provider=PROVIDER,
        station_id=_station_id(),
        event_date=parsed_date,
        event_type=event_type,
        description=description,
        performed_by=performed_by,
        notes=notes,
        source="whatsapp",
    )

    return {
        "saved": True,
        "event": {
            "event_date": _iso_or_text(event.get("event_date")),
            "event_type": str(event.get("event_type") or ""),
            "description": str(event.get("description") or ""),
            "performed_by": str(event.get("performed_by") or ""),
            "notes": str(event.get("notes") or ""),
        },
    }


def get_real_maintenance_history(
    limit: int = 10,
    start_date: str = "",
    end_date: str = "",
) -> dict:
    safe_limit = max(1, min(int(limit), 30))
    parsed_start = (
        _parse_date(start_date, "Data inicial")
        if str(start_date or "").strip()
        else None
    )
    parsed_end = (
        _parse_date(end_date, "Data final")
        if str(end_date or "").strip()
        else None
    )

    if parsed_start and parsed_end and parsed_end < parsed_start:
        raise ValueError(
            "A data final deve ser igual ou posterior à inicial."
        )

    rows = fetch_maintenance_history(
        provider=PROVIDER,
        station_id=_station_id(),
        limit=safe_limit,
        start_date=parsed_start,
        end_date=parsed_end,
    )

    events = [
        {
            "event_date": _iso_or_text(row.get("event_date")),
            "event_type": str(row.get("event_type") or ""),
            "description": str(row.get("description") or ""),
            "performed_by": str(row.get("performed_by") or ""),
            "notes": str(row.get("notes") or ""),
        }
        for row in rows
    ]

    return {
        "count": len(events),
        "events": events,
    }
