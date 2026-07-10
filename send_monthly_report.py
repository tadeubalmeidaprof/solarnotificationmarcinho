import calendar
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from database import fetch_generation_for_month, to_decimal
from savings_calculator import calculate_savings_without_fio_b
from solisdaily import send_whatsapp_message


REPORT_TIMEZONE = ZoneInfo("America/Bahia")
CONNECTION_TYPE = "monofasico"

MONTH_NAMES_PT = {
    1: "janeiro",
    2: "fevereiro",
    3: "março",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}


def env(name: str, default: str = "", required: bool = False) -> str:
    value = os.getenv(name, default).strip()

    if required and not value:
        raise RuntimeError(f"Variável {name} não configurada.")

    return value


def month_info_from_date(reference_date: date) -> tuple[str, str, int]:
    year_month = reference_date.strftime("%Y-%m")
    month_label = f"{MONTH_NAMES_PT[reference_date.month]} de {reference_date.year}"
    days_in_month = calendar.monthrange(reference_date.year, reference_date.month)[1]

    return year_month, month_label, days_in_month


def previous_month(reference_date: date) -> tuple[str, str, int]:
    first_day_current_month = reference_date.replace(day=1)
    last_day_previous_month = first_day_current_month - timedelta(days=1)

    return month_info_from_date(last_day_previous_month)


def get_report_month(reference_date: date) -> tuple[str, str, int]:
    forced_year_month = os.getenv("REPORT_YEAR_MONTH", "").strip()

    if not forced_year_month:
        return previous_month(reference_date)

    try:
        forced_date = datetime.strptime(forced_year_month, "%Y-%m").date()
    except ValueError as exc:
        raise RuntimeError(
            "REPORT_YEAR_MONTH inválido. Use o formato YYYY-MM, por exemplo 2026-07."
        ) from exc

    return month_info_from_date(forced_date)


def br_number(value, decimals: int = 1) -> str:
    number = Decimal(value).quantize(
        Decimal("1." + "0" * decimals),
        rounding=ROUND_HALF_UP,
    )
    return f"{number:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_brl(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{rounded:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def get_average_consumption(generation_kwh: Decimal) -> Decimal:
    configured = os.getenv("AVERAGE_CONSUMPTION_KWH", "").strip()

    if configured:
        return to_decimal(configured)

    # Fallback seguro para manter o relatório funcionando quando o consumo médio
    # ainda não foi cadastrado. Considera que toda a geração do mês foi compensável
    # e soma o mínimo monofásico de 30 kWh.
    return generation_kwh + Decimal("30")


def build_monthly_message(
    month_label: str,
    generation_kwh: Decimal,
    average_daily_kwh: Decimal,
    tariff: Decimal,
    savings_data: dict,
) -> str:
    return f"""☀️ *Olá, Marcio! Aqui está seu Relatório Solar Mensal*

Resumo de {month_label}:

⚡ Geração total: {br_number(generation_kwh, 1)} kWh
⚖️ Energia compensada estimada: {br_number(savings_data["compensated_energy_kwh"], 1)} kWh
🔋 Créditos estimados: {br_number(savings_data["generated_credits_kwh"], 1)} kWh
📊 Média diária: {br_number(average_daily_kwh, 1)} kWh/dia
💰 Economia estimada: {format_brl(savings_data["estimated_savings"])}

📌 Ligação considerada: monofásica
📌 Custo mínimo considerado: {br_number(savings_data["minimum_kwh"], 0)} kWh
📌 Tarifa considerada: {format_brl(tariff)} por kWh.

Valor estimado com base na geração registrada, consumo médio cadastrado e regras de compensação informadas.
"""


def main() -> int:
    today = datetime.now(REPORT_TIMEZONE).date()
    year_month, month_label, days_in_month = get_report_month(today)

    station_id = env("SOLIS_STATION_ID")
    result = fetch_generation_for_month(year_month=year_month, station_id=station_id or None)

    if not result:
        raise RuntimeError(
            f"Nenhum registro encontrado no Supabase para o mês {year_month}. "
            "Verifique se o snapshot mensal está rodando corretamente."
        )

    found_station_id, generation_kwh = result
    tariff = to_decimal(env("ENERGY_TARIFF", required=True))
    average_consumption_kwh = get_average_consumption(generation_kwh)
    savings_data = calculate_savings_without_fio_b(
        generation_month_kwh=generation_kwh,
        average_consumption_month_kwh=average_consumption_kwh,
        final_tariff_kwh=tariff,
        connection_type=CONNECTION_TYPE,
    )
    average_daily_kwh = generation_kwh / Decimal(days_in_month)

    print(
        "Dados do relatório mensal:",
        {
            "station_id": found_station_id,
            "year_month": year_month,
            "generation_kwh": str(generation_kwh),
            "average_consumption_kwh": str(average_consumption_kwh),
            "tariff": str(tariff),
            "savings": str(savings_data["estimated_savings"]),
            "compensated_energy_kwh": str(savings_data["compensated_energy_kwh"]),
            "generated_credits_kwh": str(savings_data["generated_credits_kwh"]),
            "average_daily_kwh": str(average_daily_kwh),
        },
    )

    message = build_monthly_message(
        month_label=month_label,
        generation_kwh=generation_kwh,
        average_daily_kwh=average_daily_kwh,
        tariff=tariff,
        savings_data=savings_data,
    )

    print("Mensagem mensal para Marcio:")
    print(message)

    result = send_whatsapp_message(
        message=message,
        phone=env("CALLMEBOT_PHONE", required=True),
        api_key=env("CALLMEBOT_APIKEY", required=True),
    )

    print("Relatório mensal enviado com sucesso:", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
