from statistics import median, pstdev
from decimal import Decimal
from typing import Any

from utils import to_decimal


def analyze_maintenance_need(
    history: list[dict[str, Any]],
    minimum_recent_days: int = 4,
    minimum_baseline_days: int = 7,
    max_baseline_days: int = 21,
    drop_threshold_percent: Decimal = Decimal("25"),
    abrupt_drop_threshold_percent: Decimal = Decimal("15"),
    abrupt_drop_min_stdev_multiplier: Decimal = Decimal("2"),
    minimum_radiation_wh_m2: Decimal = Decimal("3000"),
) -> dict[str, Any]:
    if not history:
        return {"alert": False, "reason": "no_history"}

    ordered = sorted(history, key=lambda item: item["report_date"])

    valid_days = []
    for row in ordered:
        generation = to_decimal(row.get("generation_day_kwh"))
        radiation = to_decimal(row.get("RADIACAOSOLARWHM2"))
        rainfall = to_decimal(row.get("CHUVAMM"))

        if radiation < minimum_radiation_wh_m2:
            continue

        if rainfall > Decimal("5"):
            continue

        performance_ratio = generation / radiation if radiation > 0 else Decimal("0")

        valid_days.append(
            {
                **row,
                "generation": generation,
                "radiation": radiation,
                "performance_ratio": performance_ratio,
            }
        )

    required_days = minimum_recent_days + minimum_baseline_days
    if len(valid_days) < required_days:
        return {
            "alert": False,
            "reason": "insufficient_favorable_history",
            "available_favorable_days": len(valid_days),
            "required_favorable_days": required_days,
        }

    recent_days = valid_days[-minimum_recent_days:]
    baseline_days = valid_days[:-minimum_recent_days]

    if len(baseline_days) > max_baseline_days:
        baseline_days = baseline_days[-max_baseline_days:]

    baseline_ratios = [float(day["performance_ratio"]) for day in baseline_days]
    recent_ratios = [float(day["performance_ratio"]) for day in recent_days]

    baseline_median = Decimal(str(median(baseline_ratios)))
    recent_median = Decimal(str(median(recent_ratios)))

    if baseline_median <= 0:
        return {"alert": False, "reason": "invalid_baseline"}

    baseline_stdev = Decimal(str(pstdev(baseline_ratios))) if len(baseline_ratios) > 1 else Decimal("0")

    drop_percentage = (
        (baseline_median - recent_median)
        / baseline_median
        * Decimal("100")
    )

    expected_generation = sum(
        day["radiation"] * baseline_median for day in recent_days
    ) / Decimal(len(recent_days))

    observed_generation = sum(
        day["generation"] for day in recent_days
    ) / Decimal(len(recent_days))

    zero_generation_with_good_sun = all(
        day["generation"] <= Decimal("0.1") for day in recent_days
    )

    common_details = {
        "baseline_performance_ratio": str(baseline_median),
        "recent_performance_ratio": str(recent_median),
        "baseline_stdev": str(baseline_stdev),
        "baseline_days_used": len(baseline_days),
    }

    if zero_generation_with_good_sun:
        return {
            "alert": True,
            "alert_type": "inverter_offline",
            "severity": "critical",
            "drop_percentage": Decimal("100"),
            "expected_generation_kwh": expected_generation,
            "observed_generation_kwh": observed_generation,
            "favorable_days_count": len(recent_days),
            "reference_start_date": recent_days[0]["report_date"],
            "reference_end_date": recent_days[-1]["report_date"],
            "probable_cause": "Possível falha, desligamento ou problema de comunicação.",
            "details": common_details,
        }

    sustained_drop = drop_percentage >= drop_threshold_percent

    # queda menor que o limiar fixo, mas grande em relação ao desvio padrão
    # da própria usina (ex: sombra nova, sujeira começando)
    abrupt_drop = (
        baseline_stdev > 0
        and drop_percentage >= abrupt_drop_threshold_percent
        and (baseline_median - recent_median) >= (baseline_stdev * abrupt_drop_min_stdev_multiplier)
    )

    if not sustained_drop and not abrupt_drop:
        return {
            "alert": False,
            "reason": "drop_below_threshold",
            "drop_percentage": drop_percentage,
        }

    probable_cause = (
        "Possível sujeira, sombreamento ou perda parcial de desempenho. "
        "É necessária inspeção antes de concluir a causa."
    )
    if abrupt_drop and not sustained_drop:
        probable_cause = (
            "Queda moderada, mas fora do padrão histórico da usina — "
            "pode ser sombreamento novo ou início de sujidade."
        )

    return {
        "alert": True,
        "alert_type": "possible_soiling",
        "severity": "warning",
        "drop_percentage": drop_percentage,
        "expected_generation_kwh": expected_generation,
        "observed_generation_kwh": observed_generation,
        "favorable_days_count": len(recent_days),
        "reference_start_date": recent_days[0]["report_date"],
        "reference_end_date": recent_days[-1]["report_date"],
        "probable_cause": probable_cause,
        "details": {
            **common_details,
            "minimum_radiation_wh_m2": str(minimum_radiation_wh_m2),
            "drop_threshold_percent": str(drop_threshold_percent),
            "abrupt_drop_threshold_percent": str(abrupt_drop_threshold_percent),
            "triggered_by": "sustained_drop" if sustained_drop else "abrupt_drop",
        },
    }

def analyze_operational_performance(
    current_metric: dict[str, Any],
    historical_metrics: list[dict[str, Any]],
    weather: dict[str, Any] | None = None,
    active_fault_count: int | None = 0,
    existing_maintenance_alert: bool = False,
    minimum_baseline_days: int = 4,
    minimum_active_hours: float = 3.5,
    attention_drop_percent: float = 15.0,
    maintenance_drop_percent: float = 25.0,
    persistent_drop_percent: float = 20.0,
) -> dict[str, Any]:
    """
    Avalia desempenho operacional sem confundir energia com uma medida
    independente de insolação.

    O índice principal é a potência média normalizada durante as horas em que
    a usina esteve produzindo de forma relevante:

        energia_na_janela / (potencia_pico * horas_ativas)

    Esse índice é comparado com o histórico da própria usina. Temperatura e
    clima são usados como contexto, não como correção física exata, porque não
    temos temperatura de módulo nem coeficiente térmico dos painéis.
    """

    def number(value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def metric_ratio(metric: dict[str, Any]) -> float | None:
        direct = number(metric.get("average_power_fraction_of_peak"))
        if direct is not None and direct > 0:
            return direct

        active_hours = number(metric.get("active_generation_hours"))
        equivalent_hours = number(metric.get("equivalent_full_power_hours"))
        if (
            active_hours is None
            or equivalent_hours is None
            or active_hours <= 0
            or equivalent_hours < 0
        ):
            return None

        return equivalent_hours / active_hours

    current_ratio = metric_ratio(current_metric)
    current_active_hours = number(current_metric.get("active_generation_hours"))
    peak_power_kwp = number(current_metric.get("plant_peak_power_kwp"))
    observed_window_kwh = number(
        current_metric.get("estimated_energy_in_window_kwh")
    )

    base_result = {
        "status": "inconclusive",
        "maintenance_suspected": False,
        "inspection_recommended": False,
        "confidence": "low",
        "baseline_days_used": 0,
        "persistent_low_days_count": 0,
        "drop_percent_vs_baseline": None,
        "expected_window_generation_kwh": None,
        "observed_window_generation_kwh": observed_window_kwh,
        "weather_context": "unknown",
        "temperature_context": "unknown",
        "explanations": [],
        "limitations": [
            (
                "A análise usa desempenho histórico da própria usina. "
                "Não substitui inspeção técnica."
            ),
            (
                "Temperatura ambiente não é temperatura do módulo; por isso "
                "não aplicamos uma correção térmica teórica exata."
            ),
        ],
    }

    if (
        current_ratio is None
        or current_active_hours is None
        or current_active_hours <= 0
        or peak_power_kwp is None
        or peak_power_kwp <= 0
    ):
        base_result["explanations"].append(
            "Dados operacionais insuficientes para calcular o índice de desempenho."
        )
        return base_result

    valid_history = []
    for item in sorted(
        historical_metrics,
        key=lambda row: str(row.get("date") or ""),
    ):
        ratio = metric_ratio(item)
        active_hours = number(item.get("active_generation_hours"))
        if ratio is None or active_hours is None:
            continue
        if active_hours < minimum_active_hours:
            continue
        if ratio <= 0:
            continue

        valid_history.append(
            {
                **item,
                "_ratio": ratio,
                "_active_hours": active_hours,
            }
        )

    comparable_history = [
        item
        for item in valid_history
        if abs(item["_active_hours"] - current_active_hours) <= 1.5
    ]

    if len(comparable_history) >= minimum_baseline_days:
        selected_history = comparable_history
        baseline_selection = "similar_generation_duration"
    else:
        selected_history = valid_history
        baseline_selection = "all_qualified_days"

    recent_prior = []
    baseline_rows = selected_history
    if len(selected_history) >= minimum_baseline_days + 2:
        recent_prior = selected_history[-2:]
        baseline_rows = selected_history[:-2]

    if len(baseline_rows) > 8:
        baseline_rows = baseline_rows[-8:]

    if len(baseline_rows) < minimum_baseline_days:
        base_result["baseline_days_used"] = len(baseline_rows)
        base_result["explanations"].append(
            "Ainda não há histórico operacional suficiente para formar uma referência confiável."
        )
        return base_result

    baseline_ratio = float(
        median([row["_ratio"] for row in baseline_rows])
    )
    if baseline_ratio <= 0:
        base_result["explanations"].append(
            "A referência histórica encontrada é inválida."
        )
        return base_result

    if baseline_selection == "all_qualified_days":
        base_result["explanations"].append(
            "Não havia dias suficientes com duração de geração muito parecida; "
            "a referência precisou usar dias qualificados com durações diferentes."
        )

    drop_percent = max(
        0.0,
        ((baseline_ratio - current_ratio) / baseline_ratio) * 100,
    )
    expected_window_kwh = (
        peak_power_kwp
        * current_active_hours
        * baseline_ratio
    )

    base_result.update(
        {
            "baseline_days_used": len(baseline_rows),
            "baseline_selection": baseline_selection,
            "baseline_average_power_fraction_of_peak": round(
                baseline_ratio,
                4,
            ),
            "current_average_power_fraction_of_peak": round(
                current_ratio,
                4,
            ),
            "drop_percent_vs_baseline": round(drop_percent, 1),
            "expected_window_generation_kwh": round(
                expected_window_kwh,
                2,
            ),
        }
    )

    low_days = 1 if drop_percent >= persistent_drop_percent else 0
    for item in recent_prior:
        historical_drop = max(
            0.0,
            ((baseline_ratio - item["_ratio"]) / baseline_ratio) * 100,
        )
        if historical_drop >= persistent_drop_percent:
            low_days += 1

    base_result["persistent_low_days_count"] = low_days

    weather = weather or {}
    cloud_cover = number(weather.get("average_cloud_cover_percent"))
    rainfall = number(weather.get("total_precipitation_mm"))
    average_temp = number(weather.get("average_temperature_c"))
    max_temp = number(weather.get("max_temperature_c"))

    if cloud_cover is not None or rainfall is not None:
        clouds = cloud_cover if cloud_cover is not None else 0.0
        rain = rainfall if rainfall is not None else 0.0

        if clouds <= 55 and rain <= 1:
            weather_context = "favorable"
        elif clouds >= 75 or rain > 3:
            weather_context = "unfavorable"
        else:
            weather_context = "mixed"
    else:
        weather_context = "unknown"

    base_result["weather_context"] = weather_context

    if (
        (max_temp is not None and max_temp >= 35)
        or (average_temp is not None and average_temp >= 32)
    ):
        base_result["temperature_context"] = "high"
        base_result["explanations"].append(
            "Temperatura ambiente elevada pode reduzir parte do rendimento, "
            "mas sem temperatura de módulo não é seguro quantificar essa perda."
        )
    elif average_temp is not None or max_temp is not None:
        base_result["temperature_context"] = "normal_or_moderate"

    if active_fault_count is not None and active_fault_count > 0:
        base_result.update(
            {
                "status": "technical_fault_present",
                "inspection_recommended": True,
                "confidence": "high",
            }
        )
        base_result["explanations"].append(
            "Há falha ativa registrada; ela deve ser investigada antes de atribuir "
            "a baixa geração a sujeira ou manutenção preventiva."
        )
        return base_result

    if current_active_hours < minimum_active_hours:
        base_result["status"] = "inconclusive_low_generation_window"
        base_result["explanations"].append(
            "A janela de geração relevante foi curta demais para um diagnóstico confiável."
        )
        return base_result

    if drop_percent < attention_drop_percent:
        base_result.update(
            {
                "status": "normal",
                "confidence": "moderate",
            }
        )
        base_result["explanations"].append(
            "O desempenho ficou próximo do padrão histórico da própria usina."
        )
        return base_result

    if weather_context == "unfavorable":
        base_result.update(
            {
                "status": "weather_likely_explains_reduction",
                "confidence": "moderate",
            }
        )
        base_result["explanations"].append(
            "Nuvens e/ou chuva podem explicar uma parte importante da redução observada."
        )
        return base_result

    if existing_maintenance_alert and drop_percent >= attention_drop_percent:
        base_result.update(
            {
                "status": "maintenance_suspected",
                "maintenance_suspected": True,
                "inspection_recommended": True,
                "confidence": "high",
            }
        )
        base_result["explanations"].append(
            "A perda atual coincide com um alerta preventivo já aberto no SolCare."
        )
        return base_result

    persistent = low_days >= 2
    temperature_high = base_result["temperature_context"] == "high"
    effective_maintenance_threshold = (
        maintenance_drop_percent + 5.0
        if temperature_high
        else maintenance_drop_percent
    )

    if (
        weather_context == "favorable"
        and drop_percent >= effective_maintenance_threshold
        and persistent
    ):
        base_result.update(
            {
                "status": "maintenance_suspected",
                "maintenance_suspected": True,
                "inspection_recommended": True,
                "confidence": (
                    "high"
                    if drop_percent >= 35 and len(baseline_rows) >= 6
                    else "moderate"
                ),
            }
        )
        base_result["explanations"].append(
            "A usina teve uma janela produtiva suficiente, clima sem forte explicação "
            "para a queda e desempenho abaixo do padrão em mais de um dia."
        )
        base_result["explanations"].append(
            "Vale inspecionar sujeira, sombreamento novo, conexões e comportamento do inversor."
        )
        return base_result

    if drop_percent >= maintenance_drop_percent:
        base_result.update(
            {
                "status": "attention",
                "inspection_recommended": drop_percent >= 40,
                "confidence": "moderate",
            }
        )
        if persistent:
            base_result["explanations"].append(
                "Há perda persistente, mas o contexto climático não é forte o bastante "
                "para atribuí-la com segurança à manutenção."
            )
        else:
            base_result["explanations"].append(
                "A queda foi relevante, mas ainda apareceu em apenas um dia; "
                "é mais seguro acompanhar antes de concluir necessidade de manutenção."
            )
        return base_result

    base_result.update(
        {
            "status": "attention",
            "confidence": "low",
        }
    )
    base_result["explanations"].append(
        "A queda é perceptível, mas ainda está abaixo do limiar conservador usado "
        "para suspeitar de manutenção."
    )
    return base_result

