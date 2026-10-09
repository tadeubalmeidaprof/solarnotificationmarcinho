import logging
import os
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from config import env, required_env
from curve_analysis import ALGORITHM_VERSION, analyze_power_curve
from degradation_analysis import analyze_slow_degradation
from database import (
    save_solar_curve_points,
    save_daily_curve_analysis,
    save_slow_degradation_analysis,
    fetch_solar_curve_points,
    fetch_solar_curve_history,
    fetch_daily_curve_analysis_range,
    fetch_daily_curve_analysis_history,
    fetch_daily_curve_analysis,
    fetch_latest_slow_degradation_analysis,
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
                row.get("timeStr") or row.get("time"),
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



def get_weather_generation_impact(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    """
    Estima se o clima provavelmente contribuiu para a geração do dia.

    Esta análise é deliberadamente independente da curva ao vivo do inversor,
    para continuar útil quando o endpoint de potência do fabricante estiver
    temporariamente indisponível. Ela cruza clima horário, geração observada e
    a mediana recente da própria usina. O resultado indica compatibilidade
    estatística, não causalidade física comprovada.
    """
    parsed_date = _parse_date(
        report_date,
        "Data da análise climática",
    )
    today = datetime.now(REPORT_TIMEZONE).date()
    if parsed_date > today:
        raise ValueError(
            "Não é possível analisar impacto climático em um dia futuro."
        )

    weather = get_weather_window_summary(
        report_date=parsed_date.isoformat(),
        start_hour=start_hour,
        end_hour=end_hour,
    )

    generation = get_generation_period(
        start_date=parsed_date.isoformat(),
        end_date=parsed_date.isoformat(),
    )
    current_generation = None
    if generation.get("days_with_data"):
        current_generation = _number_or_none(
            generation.get("total_generation_kwh")
        )

    history_start = parsed_date - timedelta(days=14)
    history_end = parsed_date - timedelta(days=1)
    history_rows = []
    if history_end >= history_start:
        try:
            history_rows = fetch_daily_generation_range(
                provider=PROVIDER,
                station_id=_station_id(),
                start_date=history_start,
                end_date=history_end,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao obter histórico para impacto climático: %s",
                exc,
            )

    historical_values = []
    for row in history_rows:
        value = _number_or_none(
            row.get("generation_day_kwh")
        )
        if value is None or value <= 0:
            continue
        historical_values.append(value)

    historical_values = historical_values[-10:]
    baseline_generation = (
        float(median(historical_values))
        if len(historical_values) >= 3
        else None
    )

    drop_percent = None
    if (
        current_generation is not None
        and baseline_generation is not None
        and baseline_generation > 0
    ):
        drop_percent = max(
            0.0,
            (
                baseline_generation
                - current_generation
            )
            / baseline_generation
            * 100.0,
        )

    cloud_cover = _number_or_none(
        weather.get("average_cloud_cover_percent")
    )
    rainfall = _number_or_none(
        weather.get("total_precipitation_mm")
    )
    sunshine_hours = _number_or_none(
        weather.get("sunshine_hours")
    )
    radiation = _number_or_none(
        weather.get("solar_radiation_wh_m2")
    )

    if not weather.get("available"):
        weather_context = "unknown"
    elif (
        (cloud_cover is not None and cloud_cover >= 75)
        or (rainfall is not None and rainfall >= 3)
        or (sunshine_hours is not None and sunshine_hours < 3)
    ):
        weather_context = "unfavorable"
    elif (
        (cloud_cover is not None and cloud_cover >= 55)
        or (rainfall is not None and rainfall >= 0.5)
        or (
            sunshine_hours is not None
            and sunshine_hours < 5
        )
    ):
        weather_context = "mixed"
    else:
        weather_context = "favorable"

    baseline_days = len(historical_values)

    if not weather.get("available"):
        status = "weather_data_unavailable"
        confidence = "low"
    elif (
        current_generation is None
        or baseline_generation is None
    ):
        status = "weather_context_only"
        confidence = "low"
    elif (
        weather_context == "unfavorable"
        and drop_percent is not None
        and drop_percent >= 12
    ):
        status = "weather_likely_affected"
        confidence = (
            "high"
            if (
                baseline_days >= 7
                and drop_percent >= 20
                and (
                    (cloud_cover or 0) >= 85
                    or (rainfall or 0) >= 5
                    or (
                        sunshine_hours is not None
                        and sunshine_hours < 2.5
                    )
                )
            )
            else "moderate"
        )
    elif (
        weather_context == "mixed"
        and drop_percent is not None
        and drop_percent >= 15
    ):
        status = "weather_may_have_affected"
        confidence = "moderate" if baseline_days >= 5 else "low"
    elif (
        weather_context == "favorable"
        and drop_percent is not None
        and drop_percent >= 15
    ):
        status = "weather_unlikely_to_explain"
        confidence = "moderate" if baseline_days >= 5 else "low"
    elif (
        drop_percent is not None
        and drop_percent < 10
    ):
        status = "no_clear_weather_impact"
        confidence = "moderate" if baseline_days >= 5 else "low"
    else:
        status = "inconclusive"
        confidence = "low"

    return {
        "available": bool(weather.get("available")),
        "date": parsed_date.isoformat(),
        "window_label": weather.get(
            "window_label",
            f"{start_hour:02d}:00-{end_hour:02d}:00",
        ),
        "status": status,
        "confidence": confidence,
        "weather_context": weather_context,
        "generation_kwh": (
            round(current_generation, 3)
            if current_generation is not None
            else None
        ),
        "recent_baseline_generation_kwh": (
            round(baseline_generation, 3)
            if baseline_generation is not None
            else None
        ),
        "baseline_days_used": baseline_days,
        "generation_drop_percent": (
            round(drop_percent, 1)
            if drop_percent is not None
            else None
        ),
        "weather": {
            "average_cloud_cover_percent": cloud_cover,
            "total_precipitation_mm": rainfall,
            "sunshine_hours": sunshine_hours,
            "solar_radiation_wh_m2": radiation,
            "average_temperature_c": _number_or_none(
                weather.get("average_temperature_c")
            ),
            "conditions": weather.get("conditions") or [],
            "source": weather.get("source"),
        },
        "method": "weather_plus_recent_generation_baseline",
        "interpretation_limits": [
            (
                "O resultado mede compatibilidade entre clima e queda de geração; "
                "não prova causalidade."
            ),
            (
                "A referência usa a mediana recente da própria usina e pode ser "
                "afetada por sazonalidade, manutenção ou mudanças operacionais."
            ),
            (
                "Quando a curva do inversor estiver disponível, a análise de "
                "desempenho diário fornece evidência adicional."
            ),
        ],
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
    today = now.date().isoformat()

    return {
        "generated_at": now.isoformat(),
        "plant": _safe_component(get_plant_status),
        "active_faults": _safe_component(
            get_active_faults_summary
        ),
        "maintenance": _safe_component(
            get_maintenance_status
        ),
        "maintenance_history": _safe_component(
            get_real_maintenance_history,
            5,
        ),
        "weather_today": _safe_component(
            get_weather_summary,
            today,
        ),
        "curve_today": _safe_component(
            get_curve_anomaly_analysis,
            today,
        ),
        "performance_today": _safe_component(
            get_performance_diagnostic,
            today,
        ),
        "slow_degradation": _safe_component(
            get_slow_degradation_analysis,
            180,
            False,
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
                row.get("timeStr") or row.get("time"),
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


def _curve_weather_from_db(
    station_id: str,
    report_date: date,
) -> dict:
    row = fetch_daily_weather_for_date(
        provider=PROVIDER,
        station_id=station_id,
        report_date=report_date,
    )
    if not row:
        return {}

    min_temp = _number_or_none(row.get("temperature_min_c"))
    max_temp = _number_or_none(row.get("temperature_max_c"))
    average_temp = None
    if min_temp is not None and max_temp is not None:
        average_temp = (min_temp + max_temp) / 2
    elif min_temp is not None:
        average_temp = min_temp
    elif max_temp is not None:
        average_temp = max_temp

    return {
        "available": True,
        "source": "daily_weather",
        "average_cloud_cover_percent": _number_or_none(
            row.get("cloud_cover_percent")
        ),
        "total_precipitation_mm": _number_or_none(
            row.get("rainfall_mm")
        ),
        "average_temperature_c": average_temp,
    }


def _curve_weather(
    station_id: str,
    report_date: date,
    start_hour: int,
    end_hour: int,
) -> dict:
    stored = _curve_weather_from_db(
        station_id,
        report_date,
    )
    if stored:
        return stored

    try:
        return get_weather_window_summary(
            report_date=report_date.isoformat(),
            start_hour=start_hour,
            end_hour=end_hour,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar clima para curva em %s: %s",
            report_date.isoformat(),
            exc,
        )
        return {}


def _stored_curve_profiles(
    station_id: str,
    before_date: date,
    start_hour: int,
    end_hour: int,
    lookback_days: int = 45,
) -> list[dict]:
    rows = fetch_solar_curve_history(
        provider=PROVIDER,
        station_id=station_id,
        before_date=before_date,
        lookback_days=lookback_days,
    )

    grouped: dict[date, dict] = {}
    for row in rows:
        day = row.get("report_date")
        if not isinstance(day, date):
            try:
                day = date.fromisoformat(str(day))
            except (TypeError, ValueError):
                continue

        minute = int(row.get("minute_of_day") or 0)
        hour = minute // 60
        if not start_hour <= hour < end_hour:
            continue

        item = grouped.setdefault(
            day,
            {
                "date": day,
                "points": [],
                "weather": {},
            },
        )

        timestamp = datetime(
            day.year,
            day.month,
            day.day,
            minute // 60,
            minute % 60,
            tzinfo=REPORT_TIMEZONE,
        )
        item["points"].append(
            {
                "timestamp": timestamp,
                "power_w": float(row.get("power_w") or 0),
            }
        )

        if not item["weather"]:
            item["weather"] = {
                "average_cloud_cover_percent": _number_or_none(
                    row.get("cloud_cover_percent")
                ),
                "total_precipitation_mm": _number_or_none(
                    row.get("rain_mm")
                ),
                "average_temperature_c": _number_or_none(
                    row.get("average_temperature_c")
                ),
            }

    return [
        grouped[key]
        for key in sorted(grouped, reverse=True)
    ]


def _curve_day_complete(
    parsed_date: date,
    end_hour: int,
) -> bool:
    now = datetime.now(REPORT_TIMEZONE)
    if parsed_date < now.date():
        return True
    if parsed_date > now.date():
        return False

    if end_hour == 24:
        settle_time = datetime.combine(
            now.date(),
            dt_time(23, 59),
            tzinfo=REPORT_TIMEZONE,
        )
    else:
        settle_time = datetime.combine(
            now.date(),
            dt_time(end_hour, 20),
            tzinfo=REPORT_TIMEZONE,
        )

    return now >= settle_time


def _stored_curve_result(
    row: dict,
    start_hour: int,
    end_hour: int,
) -> dict:
    details = row.get("details")
    if not isinstance(details, dict):
        details = {}

    return {
        "date": _iso_or_text(row.get("report_date")),
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "source": str(row.get("source") or "solis_station_day"),
        "analysis": details,
        "storage": {
            "persisted": True,
            "algorithm_version": str(
                row.get("algorithm_version") or ""
            ),
        },
        "interpretation_limits": [
            (
                "O score compara a curva com o histórico da própria usina; "
                "não confirma defeito isoladamente."
            ),
            (
                "Clima, persistência, alarmes e manutenções reais devem ser "
                "considerados antes de recomendar intervenção."
            ),
        ],
    }


def collect_curve_analysis_for_date(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
    persist: bool = True,
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

    station_id = _station_id()
    rows, station = _station_day_rows(parsed_date)
    peak_power_kwp = _capacity_kwp(station)
    if peak_power_kwp is None or peak_power_kwp <= 0:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "status": "inconclusive",
            "reason": "Capacidade instalada da usina indisponível.",
        }

    points = _curve_points_for_analysis(
        rows,
        station,
        parsed_date,
        start_hour,
        end_hour,
    )
    complete_day = _curve_day_complete(
        parsed_date,
        end_hour,
    )

    if persist and points:
        save_solar_curve_points(
            provider=PROVIDER,
            station_id=station_id,
            report_date=parsed_date,
            points=points,
            peak_power_kwp=peak_power_kwp,
            source="solis_station_day",
        )

    historical_profiles = _stored_curve_profiles(
        station_id,
        parsed_date,
        start_hour,
        end_hour,
        lookback_days=45,
    )

    existing_dates = {
        item["date"]
        for item in historical_profiles
        if item.get("date") is not None
    }

    if len(historical_profiles) < 5:
        for offset in range(1, 15):
            historical_date = parsed_date - timedelta(days=offset)
            if historical_date in existing_dates:
                continue

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
                    "Falha ao buscar curva Solis histórica de %s: %s",
                    historical_date.isoformat(),
                    exc,
                )
                continue

            if not historical_points:
                continue

            historical_profiles.append(
                {
                    "date": historical_date,
                    "points": historical_points,
                    "weather": _curve_weather_from_db(
                        station_id,
                        historical_date,
                    ),
                }
            )
            existing_dates.add(historical_date)

            if persist:
                save_solar_curve_points(
                    provider=PROVIDER,
                    station_id=station_id,
                    report_date=historical_date,
                    points=historical_points,
                    peak_power_kwp=peak_power_kwp,
                    source="solis_station_day",
                )

            if len(historical_profiles) >= 8:
                break

    weather = _curve_weather(
        station_id,
        parsed_date,
        start_hour,
        end_hour,
    )

    previous_analyses = fetch_daily_curve_analysis_history(
        provider=PROVIDER,
        station_id=station_id,
        before_date=parsed_date,
        limit=5,
    )
    previous_scores = [
        float(row["anomaly_score"])
        for row in previous_analyses
        if row.get("anomaly_score") is not None
    ]

    analysis = analyze_power_curve(
        points=points,
        peak_power_kwp=peak_power_kwp,
        historical_profiles=historical_profiles,
        weather=weather if weather.get("available") else {},
        complete_day=complete_day,
        historical_analysis_scores=previous_scores,
    )

    if (
        persist
        and complete_day
        and analysis.get("available")
    ):
        save_daily_curve_analysis(
            provider=PROVIDER,
            station_id=station_id,
            report_date=parsed_date,
            source="solis_station_day",
            peak_power_kwp=peak_power_kwp,
            analysis=analysis,
            weather=weather,
        )

    return {
        "date": parsed_date.isoformat(),
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "source": "solis_station_day",
        "analysis": analysis,
        "storage": {
            "persisted": bool(
                persist
                and complete_day
                and analysis.get("available")
            ),
            "historical_curve_days_available": len(
                historical_profiles
            ),
        },
        "interpretation_limits": [
            (
                "O score usa baseline robusto da própria usina, mediana/MAD "
                "por horário, recência e similaridade climática."
            ),
            (
                "O score mede desvio operacional, não confirma defeito."
            ),
            (
                "Manutenção deve considerar persistência, clima, alarmes "
                "e histórico real de intervenções."
            ),
        ],
    }


def get_curve_anomaly_analysis(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(
        report_date,
        "Data da análise da curva",
    )
    start_hour = int(start_hour)
    end_hour = int(end_hour)

    if (
        start_hour == 7
        and end_hour == 17
        and parsed_date <= datetime.now(REPORT_TIMEZONE).date()
    ):
        station_id = _station_id()
        stored = fetch_daily_curve_analysis(
            provider=PROVIDER,
            station_id=station_id,
            report_date=parsed_date,
        )
        if (
            stored
            and str(stored.get("algorithm_version") or "")
            == ALGORITHM_VERSION
        ):
            return _stored_curve_result(
                stored,
                start_hour,
                end_hour,
            )

    return collect_curve_analysis_for_date(
        parsed_date.isoformat(),
        start_hour=start_hour,
        end_hour=end_hour,
        persist=(
            start_hour == 7
            and end_hour == 17
        ),
    )



def get_slow_degradation_analysis(
    window_days: int = 180,
    persist: bool = False,
) -> dict:
    safe_window = max(
        45,
        min(int(window_days), 366),
    )
    station_id = _station_id()
    today = datetime.now(
        REPORT_TIMEZONE
    ).date()

    rows = fetch_daily_curve_analysis_history(
        provider=PROVIDER,
        station_id=station_id,
        before_date=today + timedelta(days=1),
        limit=366,
    )
    maintenance_events = fetch_maintenance_history(
        provider=PROVIDER,
        station_id=station_id,
        limit=30,
    )

    analysis = analyze_slow_degradation(
        rows,
        maintenance_events=maintenance_events,
        requested_window_days=safe_window,
    )

    persisted = False
    storage_error = None
    if (
        persist
        and analysis.get("available")
    ):
        try:
            save_slow_degradation_analysis(
                provider=PROVIDER,
                station_id=station_id,
                analysis_date=today,
                window_days=safe_window,
                analysis=analysis,
            )
            persisted = True
        except Exception as exc:
            storage_error = type(exc).__name__
            logger.warning(
                "Não foi possível persistir degradação lenta: %s",
                storage_error,
            )

    return {
        **analysis,
        "window_days": safe_window,
        "storage": {
            "persisted": persisted,
            "error": storage_error,
        },
    }



def _curve_performance_index(row: dict) -> float | None:
    normalized_energy = _number_or_none(
        row.get("normalized_energy_hours")
    )
    active_hours = _number_or_none(
        row.get("active_hours")
    )
    if (
        normalized_energy is None
        or active_hours is None
        or active_hours < 2.5
    ):
        return None

    return normalized_energy / active_hours


def _maintenance_weather_distance(
    first: dict,
    second: dict,
) -> float:
    pieces = []

    first_cloud = _number_or_none(
        first.get("cloud_cover_percent")
    )
    second_cloud = _number_or_none(
        second.get("cloud_cover_percent")
    )
    if first_cloud is not None and second_cloud is not None:
        pieces.append(abs(first_cloud - second_cloud) / 20.0)

    first_temp = _number_or_none(
        first.get("avg_temperature_c")
    )
    second_temp = _number_or_none(
        second.get("avg_temperature_c")
    )
    if first_temp is not None and second_temp is not None:
        pieces.append(abs(first_temp - second_temp) / 8.0)

    first_rain = _number_or_none(first.get("rain_mm"))
    second_rain = _number_or_none(second.get("rain_mm"))
    if first_rain is not None and second_rain is not None:
        first_wet = first_rain > 1
        second_wet = second_rain > 1
        pieces.append(0.0 if first_wet == second_wet else 1.5)

    return (
        sum(pieces) / len(pieces)
        if pieces
        else 1.0
    )


def get_maintenance_impact(
    event_type: str = "",
    days_before: int = 7,
    days_after: int = 7,
) -> dict:
    safe_before = max(3, min(int(days_before), 30))
    safe_after = max(3, min(int(days_after), 30))
    station_id = _station_id()

    events = fetch_maintenance_history(
        provider=PROVIDER,
        station_id=station_id,
        limit=30,
    )

    normalized_type = str(event_type or "").strip().lower()
    if normalized_type:
        events = [
            event
            for event in events
            if str(event.get("event_type") or "").lower()
            == normalized_type
        ]

    if not events:
        return {
            "available": False,
            "status": "no_maintenance_event",
            "reason": (
                "Não há manutenção real registrada para essa consulta."
            ),
        }

    event = events[0]
    event_date = event.get("event_date")
    if not isinstance(event_date, date):
        event_date = _parse_date(
            str(event_date),
            "Data da manutenção",
        )

    today = datetime.now(REPORT_TIMEZONE).date()
    before_start = event_date - timedelta(days=safe_before)
    before_end = event_date - timedelta(days=1)
    after_start = event_date + timedelta(days=1)
    after_end = min(
        today,
        event_date + timedelta(days=safe_after),
    )

    if after_end < after_start:
        return {
            "available": False,
            "status": "waiting_post_maintenance_data",
            "event_date": event_date.isoformat(),
            "reason": (
                "Ainda não há dias completos após a manutenção."
            ),
        }

    before_rows = fetch_daily_curve_analysis_range(
        provider=PROVIDER,
        station_id=station_id,
        start_date=before_start,
        end_date=before_end,
    )
    after_rows = fetch_daily_curve_analysis_range(
        provider=PROVIDER,
        station_id=station_id,
        start_date=after_start,
        end_date=after_end,
    )

    before_rows = [
        row for row in before_rows
        if _curve_performance_index(row) is not None
    ]
    after_rows = [
        row for row in after_rows
        if _curve_performance_index(row) is not None
    ]

    pairs = []
    used_before = set()
    for after_row in after_rows:
        candidates = []
        for index, before_row in enumerate(before_rows):
            if index in used_before:
                continue
            distance = _maintenance_weather_distance(
                before_row,
                after_row,
            )
            candidates.append(
                (distance, index, before_row)
            )

        if not candidates:
            break

        distance, index, before_row = min(
            candidates,
            key=lambda item: item[0],
        )
        if distance > 1.75:
            continue

        used_before.add(index)
        pairs.append(
            {
                "before": before_row,
                "after": after_row,
                "weather_distance": distance,
            }
        )

    weather_matched = len(pairs) >= 3
    if weather_matched:
        before_selected = [
            pair["before"] for pair in pairs
        ]
        after_selected = [
            pair["after"] for pair in pairs
        ]
    else:
        before_selected = before_rows
        after_selected = after_rows

    if (
        len(before_selected) < 3
        or len(after_selected) < 3
    ):
        return {
            "available": False,
            "status": "insufficient_curve_history",
            "event_date": event_date.isoformat(),
            "before_days_found": len(before_rows),
            "after_days_found": len(after_rows),
            "reason": (
                "São necessários pelo menos 3 dias válidos antes "
                "e 3 depois da manutenção."
            ),
        }

    before_indexes = [
        _curve_performance_index(row)
        for row in before_selected
    ]
    after_indexes = [
        _curve_performance_index(row)
        for row in after_selected
    ]
    before_indexes = [
        value for value in before_indexes
        if value is not None
    ]
    after_indexes = [
        value for value in after_indexes
        if value is not None
    ]

    before_median = float(median(before_indexes))
    after_median = float(median(after_indexes))
    change_percent = (
        (after_median - before_median)
        / before_median
        * 100.0
        if before_median > 0
        else None
    )

    before_energy = [
        float(row["normalized_energy_hours"])
        for row in before_selected
        if row.get("normalized_energy_hours") is not None
    ]
    after_energy = [
        float(row["normalized_energy_hours"])
        for row in after_selected
        if row.get("normalized_energy_hours") is not None
    ]
    energy_change = None
    if before_energy and after_energy:
        before_energy_median = float(median(before_energy))
        after_energy_median = float(median(after_energy))
        if before_energy_median > 0:
            energy_change = (
                (after_energy_median - before_energy_median)
                / before_energy_median
                * 100.0
            )

    if change_percent is None:
        status = "inconclusive"
    elif change_percent >= 8:
        status = "improvement_observed"
    elif change_percent <= -8:
        status = "decline_observed"
    else:
        status = "no_clear_change"

    confidence = (
        "high"
        if weather_matched and len(pairs) >= 5
        else "moderate"
        if weather_matched
        else "low"
    )

    return {
        "available": True,
        "status": status,
        "confidence": confidence,
        "event": {
            "event_date": event_date.isoformat(),
            "event_type": str(event.get("event_type") or ""),
            "description": str(event.get("description") or ""),
        },
        "comparison": {
            "before_days_used": len(before_selected),
            "after_days_used": len(after_selected),
            "weather_matched_pairs": len(pairs),
            "weather_matching_used": weather_matched,
            "performance_index_before": round(before_median, 4),
            "performance_index_after": round(after_median, 4),
            "performance_change_percent": (
                round(change_percent, 1)
                if change_percent is not None
                else None
            ),
            "normalized_energy_change_percent": (
                round(energy_change, 1)
                if energy_change is not None
                else None
            ),
        },
        "metric_explanation": (
            "O índice principal é horas equivalentes divididas pelas "
            "horas produtivas. Quando possível, dias antes e depois são "
            "pareados por clima semelhante. O resultado mostra associação "
            "temporal e não prova causalidade da manutenção."
        ),
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
