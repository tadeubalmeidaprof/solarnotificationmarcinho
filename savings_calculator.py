from decimal import Decimal, ROUND_HALF_UP


MINIMUM_KWH_BY_CONNECTION_TYPE = {
    "monofasico": Decimal("30"),
    "bifasico": Decimal("50"),
    "trifasico": Decimal("100"),
}


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")

    text = str(value).strip().replace(",", ".")

    if not text:
        return Decimal("0")

    return Decimal(text)


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_percentage(value) -> Decimal:
    percentage = to_decimal(value)

    # Aceita 60 ou 0.60.
    if percentage > 1:
        return percentage / Decimal("100")

    return percentage


def calculate_base_savings(
    generation_month_kwh,
    average_consumption_month_kwh,
    final_tariff_kwh,
    connection_type="monofasico",
):
    generation_month_kwh = to_decimal(generation_month_kwh)
    average_consumption_month_kwh = to_decimal(average_consumption_month_kwh)
    final_tariff_kwh = to_decimal(final_tariff_kwh)
    connection_type = str(connection_type or "monofasico").strip().lower()

    if connection_type not in MINIMUM_KWH_BY_CONNECTION_TYPE:
        raise ValueError(
            "connection_type inválido. Use: monofasico, bifasico ou trifasico."
        )

    minimum_kwh = MINIMUM_KWH_BY_CONNECTION_TYPE[connection_type]

    compensable_consumption_kwh = max(
        average_consumption_month_kwh - minimum_kwh,
        Decimal("0"),
    )

    compensated_energy_kwh = min(
        generation_month_kwh,
        compensable_consumption_kwh,
    )

    generated_credits_kwh = max(
        generation_month_kwh - compensated_energy_kwh,
        Decimal("0"),
    )

    gross_savings = compensated_energy_kwh * final_tariff_kwh

    return {
        "generation_month_kwh": generation_month_kwh,
        "average_consumption_month_kwh": average_consumption_month_kwh,
        "final_tariff_kwh": final_tariff_kwh,
        "connection_type": connection_type,
        "minimum_kwh": minimum_kwh,
        "compensable_consumption_kwh": compensable_consumption_kwh,
        "compensated_energy_kwh": compensated_energy_kwh,
        "generated_credits_kwh": generated_credits_kwh,
        "gross_savings": round_money(gross_savings),
    }


def calculate_savings_without_fio_b(
    generation_month_kwh,
    average_consumption_month_kwh,
    final_tariff_kwh,
    connection_type="monofasico",
):
    data = calculate_base_savings(
        generation_month_kwh=generation_month_kwh,
        average_consumption_month_kwh=average_consumption_month_kwh,
        final_tariff_kwh=final_tariff_kwh,
        connection_type=connection_type,
    )

    data["has_fio_b"] = False
    data["fio_b_cost"] = Decimal("0.00")
    data["estimated_savings"] = data["gross_savings"]

    return data


def calculate_savings_with_fio_b(
    generation_month_kwh,
    average_consumption_month_kwh,
    final_tariff_kwh,
    tusd_fio_b_kwh,
    fio_b_percentage,
    connection_type="monofasico",
):
    data = calculate_base_savings(
        generation_month_kwh=generation_month_kwh,
        average_consumption_month_kwh=average_consumption_month_kwh,
        final_tariff_kwh=final_tariff_kwh,
        connection_type=connection_type,
    )

    tusd_fio_b_kwh = to_decimal(tusd_fio_b_kwh)
    fio_b_percentage = normalize_percentage(fio_b_percentage)

    fio_b_cost = (
        data["compensated_energy_kwh"]
        * tusd_fio_b_kwh
        * fio_b_percentage
    )

    estimated_savings = data["gross_savings"] - fio_b_cost

    data["has_fio_b"] = True
    data["tusd_fio_b_kwh"] = tusd_fio_b_kwh
    data["fio_b_percentage"] = fio_b_percentage
    data["fio_b_cost"] = round_money(fio_b_cost)
    data["estimated_savings"] = round_money(estimated_savings)

    return data
