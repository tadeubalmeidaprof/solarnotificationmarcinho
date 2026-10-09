from __future__ import annotations

import json
import math
from datetime import date, datetime
from statistics import median
from typing import Any, Iterable

ALGORITHM_VERSION = "slow-degradation-v1.0"
MINIMUM_OBSERVATIONS = 35
MINIMUM_DATE_SPAN_DAYS = 45
RECOMMENDED_OBSERVATIONS = 90
PHYSICAL_AGEING_MIN_SPAN_DAYS = 300
EPSILON = 1e-9


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _details(row: dict) -> dict:
    value = row.get("details")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _component(row: dict, details: dict, name: str) -> float:
    direct = _number(row.get(f"{name}_score"))
    if direct is not None:
        return _clamp(direct, 0.0, 100.0)
    nested = _number((details.get("component_scores") or {}).get(name))
    return _clamp(nested or 0.0, 0.0, 100.0)


def _ratio(current: Any, baseline: Any) -> float | None:
    current = _number(current)
    baseline = _number(baseline)
    if current is None or baseline is None or baseline <= 0:
        return None
    return _clamp(current / baseline, 0.25, 1.75)


def _quality_weight(row: dict, details: dict) -> float:
    current = details.get("current_profile") or {}
    samples = _number(row.get("samples"))
    if samples is None:
        samples = _number(current.get("samples"))
    active_hours = _number(row.get("active_hours"))
    if active_hours is None:
        active_hours = _number(current.get("active_hours"))

    telemetry = _component(row, details, "telemetry")
    missing = _number(current.get("missing_sample_ratio")) or 0.0
    complete_day = details.get("complete_day")

    weight = 1.0
    if complete_day is False:
        weight *= 0.25
    if samples is not None:
        weight *= _clamp(samples / 60.0, 0.35, 1.0)
    if active_hours is not None:
        weight *= _clamp((active_hours - 1.5) / 4.5, 0.30, 1.0)
    weight *= 1.0 - 0.65 * _clamp(telemetry / 100.0)
    weight *= 1.0 - 0.60 * _clamp(missing)

    weather_context = str(
        row.get("weather_context")
        or details.get("weather_context")
        or ""
    )
    if weather_context == "unfavorable":
        weight *= 0.72
    elif weather_context in {"mixed_or_unknown", ""}:
        weight *= 0.90

    return _clamp(weight, 0.05, 1.0)


def _extract_daily_rows(rows: Iterable[dict]) -> list[dict]:
    prepared: list[dict] = []
    for row in rows or []:
        report_date = _as_date(row.get("report_date"))
        if report_date is None:
            continue
        details = _details(row)
        current = details.get("current_profile") or {}
        baseline = details.get("baseline_profile") or {}

        energy_ratio = _ratio(
            row.get("normalized_energy_hours")
            if row.get("normalized_energy_hours") is not None
            else current.get("normalized_energy_hours"),
            baseline.get("normalized_energy_hours"),
        )
        if energy_ratio is None:
            deficit = _number(
                (details.get("comparison") or {}).get(
                    "energy_deficit_percent"
                )
            )
            if deficit is not None:
                energy_ratio = _clamp(
                    1.0 - deficit / 100.0,
                    0.25,
                    1.75,
                )

        peak_ratio = _ratio(
            row.get("peak_fraction")
            if row.get("peak_fraction") is not None
            else current.get("peak_fraction_of_installed"),
            baseline.get("peak_fraction"),
        )
        if peak_ratio is None:
            deficit = _number(
                (details.get("comparison") or {}).get(
                    "peak_deficit_percent"
                )
            )
            if deficit is not None:
                peak_ratio = _clamp(
                    1.0 - deficit / 100.0,
                    0.25,
                    1.75,
                )

        if energy_ratio is None and peak_ratio is None:
            continue

        prepared.append(
            {
                "date": report_date,
                "energy_loss": (
                    1.0 - energy_ratio
                    if energy_ratio is not None
                    else None
                ),
                "peak_loss": (
                    1.0 - peak_ratio
                    if peak_ratio is not None
                    else None
                ),
                "shape_score": _component(
                    row,
                    details,
                    "shape",
                ),
                "interruption_score": _component(
                    row,
                    details,
                    "interruption",
                ),
                "volatility_score": _component(
                    row,
                    details,
                    "volatility",
                ),
                "telemetry_score": _component(
                    row,
                    details,
                    "telemetry",
                ),
                "peak_score": _component(
                    row,
                    details,
                    "peak",
                ),
                "quality_weight": _quality_weight(
                    row,
                    details,
                ),
                "weather_context": str(
                    row.get("weather_context")
                    or details.get("weather_context")
                    or ""
                ),
                "cloud_cover_percent": _number(
                    row.get("cloud_cover_percent")
                ),
                "rain_mm": _number(row.get("rain_mm")),
                "avg_temperature_c": _number(
                    row.get("avg_temperature_c")
                ),
                "details": details,
            }
        )

    deduplicated = {
        item["date"]: item
        for item in prepared
    }
    return [
        deduplicated[key]
        for key in sorted(deduplicated)
    ]


def _weighted_median(
    values: list[tuple[float, float]],
) -> float | None:
    clean = [
        (float(value), max(float(weight), 0.0))
        for value, weight in values
        if value is not None
        and weight
        and weight > 0
    ]
    if not clean:
        return None

    clean.sort(key=lambda pair: pair[0])
    total = sum(weight for _, weight in clean)
    running = 0.0

    for value, weight in clean:
        running += weight
        if running >= total / 2.0:
            return value

    return clean[-1][0]


def _series(
    items: list[dict],
    field: str,
    min_weight: float = 0.22,
) -> list[tuple[float, float]]:
    if not items:
        return []

    origin = items[0]["date"]
    result = []
    for item in items:
        value = _number(item.get(field))
        if (
            value is None
            or item["quality_weight"] < min_weight
        ):
            continue
        result.append(
            (
                (item["date"] - origin).days,
                value,
            )
        )
    return result


def _theil_sen(
    series: list[tuple[float, float]],
) -> dict:
    if len(series) < 3:
        return {
            "slope_per_day": None,
            "intercept": None,
            "pairs": 0,
        }

    slopes = []
    for index, (x1, y1) in enumerate(
        series[:-1]
    ):
        for x2, y2 in series[index + 1 :]:
            delta = x2 - x1
            if delta > 0:
                slopes.append((y2 - y1) / delta)

    if not slopes:
        return {
            "slope_per_day": None,
            "intercept": None,
            "pairs": 0,
        }

    slope = float(median(slopes))
    intercept = float(
        median(
            y - slope * x
            for x, y in series
        )
    )
    return {
        "slope_per_day": slope,
        "intercept": intercept,
        "pairs": len(slopes),
    }


def _mann_kendall(
    series: list[tuple[float, float]],
) -> dict:
    count = len(series)
    if count < 5:
        return {
            "tau": None,
            "z": None,
            "p_value": None,
        }

    values = [value for _, value in series]
    score = 0
    for index, current in enumerate(
        values[:-1]
    ):
        for later in values[index + 1 :]:
            score += (
                1
                if later > current
                else -1
                if later < current
                else 0
            )

    ties: dict[float, int] = {}
    for value in values:
        key = round(value, 10)
        ties[key] = ties.get(key, 0) + 1

    tie_term = sum(
        tied * (tied - 1) * (2 * tied + 5)
        for tied in ties.values()
        if tied > 1
    )
    variance = (
        count
        * (count - 1)
        * (2 * count + 5)
        - tie_term
    ) / 18.0

    if variance <= 0:
        z_score = 0.0
    elif score > 0:
        z_score = (
            score - 1
        ) / math.sqrt(variance)
    elif score < 0:
        z_score = (
            score + 1
        ) / math.sqrt(variance)
    else:
        z_score = 0.0

    p_value = math.erfc(
        abs(z_score) / math.sqrt(2.0)
    )
    tau = score / (
        0.5 * count * (count - 1)
    )

    return {
        "tau": tau,
        "z": z_score,
        "p_value": p_value,
    }


def _robust_center_scale(
    values: list[float],
) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0

    center = float(median(values))
    mad = float(
        median(
            abs(value - center)
            for value in values
        )
    )
    return center, max(
        1.4826 * mad,
        0.008,
    )


def _ewma_cusum(
    series: list[tuple[float, float]],
) -> dict:
    if len(series) < 12:
        return {
            "ewma_z": 0.0,
            "cusum_z": 0.0,
            "shift_score": 0.0,
        }

    values = [
        value
        for _, value in series
    ]
    baseline_count = max(
        10,
        min(len(values) // 3, 45),
    )
    center, scale = _robust_center_scale(
        values[:baseline_count]
    )

    alpha = 0.20
    ewma = 0.0
    positive_cusum = 0.0
    maximum_cusum = 0.0

    for value in values:
        z_score = _clamp(
            (value - center) / scale,
            -6.0,
            6.0,
        )
        ewma = (
            alpha * z_score
            + (1 - alpha) * ewma
        )
        positive_cusum = max(
            0.0,
            positive_cusum
            + z_score
            - 0.50,
        )
        maximum_cusum = max(
            maximum_cusum,
            positive_cusum,
        )

    ewma_evidence = _clamp(
        (ewma - 0.75) / 2.25
    )
    cusum_evidence = _clamp(
        (maximum_cusum - 4.0) / 12.0
    )

    return {
        "ewma_z": round(ewma, 3),
        "cusum_z": round(
            maximum_cusum,
            3,
        ),
        "shift_score": max(
            ewma_evidence,
            cusum_evidence,
        ),
    }


def _trend_evidence(
    series: list[tuple[float, float]],
    full_change: float,
) -> dict:
    sen = _theil_sen(series)
    mann_kendall = _mann_kendall(series)
    slope = sen["slope_per_day"]

    if slope is None or len(series) < 5:
        return {
            "evidence": 0.0,
            "observed_change": None,
            "sen": sen,
            "mann_kendall": mann_kendall,
        }

    span = max(
        series[-1][0] - series[0][0],
        1.0,
    )
    observed_change = slope * span
    direction = _clamp(
        observed_change
        / max(full_change, EPSILON)
    )

    tau = max(
        0.0,
        _number(
            mann_kendall.get("tau")
        )
        or 0.0,
    )
    p_value = _number(
        mann_kendall.get("p_value")
    )
    significance = (
        0.20
        if p_value is None
        else _clamp(
            (0.20 - p_value) / 0.15
        )
    )
    monotonicity = _clamp(
        tau / 0.45
    )

    evidence = direction * (
        0.45
        + 0.30 * monotonicity
        + 0.25 * significance
    )

    return {
        "evidence": _clamp(evidence),
        "observed_change": observed_change,
        "sen": sen,
        "mann_kendall": mann_kendall,
    }


def _recent_vs_reference(
    items: list[dict],
    field: str,
) -> dict:
    valid = [
        (
            item.get(field),
            item["quality_weight"],
        )
        for item in items
        if _number(item.get(field)) is not None
    ]

    if len(valid) < 20:
        return {
            "recent_median": None,
            "reference_median": None,
            "delta": None,
            "persistent_ratio": None,
        }

    recent_count = min(
        21,
        max(10, len(valid) // 4),
    )
    recent = valid[-recent_count:]
    historical = valid[:-recent_count]

    if len(historical) < 10:
        return {
            "recent_median": None,
            "reference_median": None,
            "delta": None,
            "persistent_ratio": None,
        }

    recent_median = _weighted_median(
        recent
    )
    reference_median = _weighted_median(
        historical
    )
    delta = (
        None
        if (
            recent_median is None
            or reference_median is None
        )
        else recent_median - reference_median
    )

    threshold = max(
        (reference_median or 0.0) + 0.02,
        0.025,
    )
    persistent_ratio = (
        sum(
            1
            for value, _ in recent
            if (
                value is not None
                and value >= threshold
            )
        )
        / len(recent)
    )

    return {
        "recent_median": recent_median,
        "reference_median": reference_median,
        "delta": delta,
        "persistent_ratio": persistent_ratio,
    }


def _maintenance_cut(
    items: list[dict],
    maintenance_events: Iterable[dict] | None,
) -> tuple[list[dict], dict | None]:
    reset_types = {
        "cleaning",
        "corrective",
        "repair",
        "replacement",
        "electrical",
        "inverter",
        "panel",
    }
    events = []

    for event in maintenance_events or []:
        event_date = _as_date(
            event.get("event_date")
        )
        event_type = str(
            event.get("event_type") or ""
        ).lower()

        if (
            event_date
            and event_type in reset_types
        ):
            events.append(
                (event_date, event_type)
            )

    if not events or not items:
        return items, None

    event_date, event_type = max(events)
    after = [
        item
        for item in items
        if item["date"] > event_date
    ]

    if len(after) >= MINIMUM_OBSERVATIONS:
        return after, {
            "event_date": event_date.isoformat(),
            "event_type": event_type,
            "history_reset": True,
        }

    return items, {
        "event_date": event_date.isoformat(),
        "event_type": event_type,
        "history_reset": False,
        "reason": (
            "insufficient_post_maintenance_history"
        ),
    }


def _cause_likelihoods(
    items: list[dict],
    metrics: dict,
    span_days: int,
    weather_coverage: float,
) -> list[dict]:
    def recent_mean(field: str) -> float:
        recent = items[
            -min(21, len(items)) :
        ]
        values = [
            _number(item.get(field))
            for item in recent
        ]
        clean = [
            value
            for value in values
            if value is not None
        ]
        return (
            sum(clean) / len(clean)
            if clean
            else 0.0
        )

    estimated_loss = max(
        0.0,
        float(
            metrics.get(
                "estimated_loss_fraction"
            )
            or 0.0
        ),
    )
    annualized = metrics.get(
        "annualized_trend_fraction"
    )
    shape = (
        recent_mean("shape_score")
        / 100.0
    )
    interruption = (
        recent_mean("interruption_score")
        / 100.0
    )
    volatility = (
        recent_mean("volatility_score")
        / 100.0
    )
    telemetry = (
        recent_mean("telemetry_score")
        / 100.0
    )
    peak = (
        recent_mean("peak_score")
        / 100.0
    )
    gradual = float(
        metrics.get("trend_consensus")
        or 0.0
    )

    scores = {
        "soiling": (
            0.18
            + 1.45 * gradual
            + 1.25 * estimated_loss
            + 0.35 * peak
            - 0.70 * interruption
            - 0.55 * telemetry
        ),
        "shading_or_obstruction": (
            0.12
            + 1.20 * shape
            + 0.50 * estimated_loss
            + 0.25 * gradual
            - 0.30 * telemetry
        ),
        "inverter_or_power_limitation": (
            0.10
            + 1.25 * peak
            + 0.55 * estimated_loss
            + 0.30 * gradual
            + 0.25 * interruption
        ),
        "intermittent_electrical_issue": (
            0.08
            + 1.10 * interruption
            + 0.90 * volatility
            + 0.35 * estimated_loss
        ),
        "telemetry_or_sensor_issue": (
            0.08
            + 1.80 * telemetry
            + 0.45
            * (1.0 - weather_coverage)
        ),
        "natural_module_ageing": 0.03,
        "weather_or_model_uncertainty": (
            0.12
            + 1.20
            * (1.0 - weather_coverage)
        ),
    }

    if (
        annualized is not None
        and span_days
        >= PHYSICAL_AGEING_MIN_SPAN_DAYS
    ):
        annualized_positive = max(
            0.0,
            float(annualized),
        )
        plausible_ageing = _clamp(
            (
                annualized_positive
                - 0.002
            )
            / 0.028
        )
        competing_faults = max(
            interruption,
            volatility,
            telemetry,
            peak * 0.7,
        )
        scores[
            "natural_module_ageing"
        ] += (
            1.60
            * plausible_ageing
            * (
                1.0
                - 0.75
                * competing_faults
            )
        )
    else:
        scores[
            "natural_module_ageing"
        ] *= 0.35

    exponentials = {
        key: math.exp(
            _clamp(value, -3.0, 4.0)
        )
        for key, value in scores.items()
    }
    total = (
        sum(exponentials.values())
        or 1.0
    )

    labels = {
        "soiling": (
            "sujeira/acúmulo sobre os módulos"
        ),
        "shading_or_obstruction": (
            "sombreamento ou obstrução progressiva"
        ),
        "inverter_or_power_limitation": (
            "limitação de inversor/potência"
        ),
        "intermittent_electrical_issue": (
            "falha elétrica/intermitência"
        ),
        "telemetry_or_sensor_issue": (
            "telemetria/sensor/dados"
        ),
        "natural_module_ageing": (
            "envelhecimento natural dos módulos"
        ),
        "weather_or_model_uncertainty": (
            "clima ou incerteza do modelo"
        ),
    }

    ranked = [
        {
            "factor": key,
            "label": labels[key],
            "relative_likelihood_percent": round(
                exponentials[key]
                / total
                * 100.0,
                1,
            ),
        }
        for key in exponentials
    ]
    ranked.sort(
        key=lambda item: (
            item[
                "relative_likelihood_percent"
            ]
        ),
        reverse=True,
    )
    return ranked


def analyze_slow_degradation(
    daily_rows: Iterable[dict],
    *,
    maintenance_events: Iterable[dict] | None = None,
    requested_window_days: int = 180,
) -> dict:
    """
    Detecta subdesempenho lento com estatística robusta.

    O resultado representa evidência de perda operacional progressiva.
    Sem irradiância no plano do arranjo, temperatura de módulo e histórico
    longo, ele não deve ser interpretado como medição laboratorial da
    degradação física dos módulos.
    """
    all_items = _extract_daily_rows(
        daily_rows
    )
    requested_window_days = max(
        45,
        min(
            int(requested_window_days),
            366,
        ),
    )

    if all_items:
        cutoff = (
            all_items[-1]["date"].toordinal()
            - requested_window_days
            + 1
        )
        all_items = [
            item
            for item in all_items
            if (
                item["date"].toordinal()
                >= cutoff
            )
        ]

    items, maintenance_context = (
        _maintenance_cut(
            all_items,
            maintenance_events,
        )
    )

    observation_count = len(items)
    span_days = (
        (
            items[-1]["date"]
            - items[0]["date"]
        ).days
        + 1
        if items
        else 0
    )

    if (
        observation_count
        < MINIMUM_OBSERVATIONS
        or span_days < MINIMUM_DATE_SPAN_DAYS
    ):
        return {
            "available": False,
            "algorithm_version": (
                ALGORITHM_VERSION
            ),
            "status": "warming_up",
            "reason": (
                "Histórico ainda insuficiente para separar "
                "tendência lenta de variação diária."
            ),
            "observations": (
                observation_count
            ),
            "date_span_days": span_days,
            "minimum_observations": (
                MINIMUM_OBSERVATIONS
            ),
            "minimum_history_days": (
                MINIMUM_DATE_SPAN_DAYS
            ),
            "recommended_observations": (
                RECOMMENDED_OBSERVATIONS
            ),
            "maintenance_context": (
                maintenance_context
            ),
        }

    energy_series = _series(
        items,
        "energy_loss",
    )
    peak_series = _series(
        items,
        "peak_loss",
    )
    shape_series = _series(
        items,
        "shape_score",
    )

    energy_trend = _trend_evidence(
        energy_series,
        full_change=0.055,
    )
    peak_trend = _trend_evidence(
        peak_series,
        full_change=0.055,
    )
    shape_trend = _trend_evidence(
        shape_series,
        full_change=18.0,
    )
    shift = _ewma_cusum(
        energy_series
    )
    persistence = _recent_vs_reference(
        items,
        "energy_loss",
    )

    recent_loss = persistence.get(
        "recent_median"
    )
    reference_loss = persistence.get(
        "reference_median"
    )

    if recent_loss is None:
        recent_pairs = [
            (
                item["energy_loss"],
                item["quality_weight"],
            )
            for item in items[-21:]
            if (
                item.get("energy_loss")
                is not None
            )
        ]
        recent_loss = (
            _weighted_median(
                recent_pairs
            )
            or 0.0
        )

    if reference_loss is None:
        reference_loss = 0.0

    estimated_loss_fraction = max(
        0.0,
        float(recent_loss),
    )
    persistence_delta = max(
        0.0,
        float(
            persistence.get("delta")
            or 0.0
        ),
    )
    persistent_ratio = float(
        persistence.get(
            "persistent_ratio"
        )
        or 0.0
    )

    persistence_evidence = (
        _clamp(
            persistence_delta / 0.045
        )
        * 0.55
        + _clamp(
            (
                persistent_ratio
                - 0.35
            )
            / 0.55
        )
        * 0.45
    )

    trend_signals = [
        energy_trend["evidence"],
        peak_trend["evidence"],
        shape_trend["evidence"],
    ]
    positive_signals = sum(
        1
        for value in trend_signals
        if value >= 0.35
    )
    agreement_evidence = (
        positive_signals / 3.0
    )

    trend_consensus = (
        0.55
        * energy_trend["evidence"]
        + 0.25
        * peak_trend["evidence"]
        + 0.20
        * shape_trend["evidence"]
    )

    raw_evidence = (
        0.31
        * energy_trend["evidence"]
        + 0.14
        * peak_trend["evidence"]
        + 0.13
        * shape_trend["evidence"]
        + 0.18
        * persistence_evidence
        + 0.14
        * shift["shift_score"]
        + 0.10
        * agreement_evidence
    )

    likelihood = 1.0 / (
        1.0
        + math.exp(
            -8.0
            * (
                raw_evidence
                - 0.48
            )
        )
    )

    quality_values = [
        item["quality_weight"]
        for item in items
    ]
    average_quality = (
        sum(quality_values)
        / len(quality_values)
    )

    weather_available = sum(
        1
        for item in items
        if (
            item.get(
                "cloud_cover_percent"
            )
            is not None
            or item.get("rain_mm")
            is not None
            or item.get(
                "avg_temperature_c"
            )
            is not None
        )
    )
    weather_coverage = (
        weather_available
        / observation_count
    )

    observation_score = _clamp(
        (
            observation_count - 25
        )
        / 95.0
    )
    span_score = _clamp(
        (span_days - 30)
        / 180.0
    )
    channel_score = (
        len(energy_series)
        / observation_count
        + len(peak_series)
        / observation_count
    ) / 2.0

    confidence_score = _clamp(
        0.32 * observation_score
        + 0.24 * span_score
        + 0.22 * average_quality
        + 0.12 * channel_score
        + 0.10 * weather_coverage
    )

    shrinkage = (
        0.45
        + 0.55
        * confidence_score
    )
    likelihood = (
        0.50
        + (
            likelihood - 0.50
        )
        * shrinkage
    )

    slope = energy_trend["sen"].get(
        "slope_per_day"
    )
    annualized = None
    if (
        slope is not None
        and span_days >= 180
    ):
        annualized = _clamp(
            float(slope) * 365.25,
            -0.25,
            0.25,
        )

    probability_percent = round(
        _clamp(likelihood) * 100.0,
        1,
    )

    if probability_percent < 25:
        status = "stable"
    elif probability_percent < 45:
        status = "watch"
    elif probability_percent < 70:
        status = (
            "possible_progressive_loss"
        )
    else:
        status = (
            "probable_progressive_loss"
        )

    if estimated_loss_fraction < 0.02:
        severity = "low"
    elif estimated_loss_fraction < 0.05:
        severity = "moderate"
    elif estimated_loss_fraction < 0.10:
        severity = "relevant"
    else:
        severity = "high"

    if (
        confidence_score >= 0.78
        and observation_count >= 120
    ):
        confidence = "high"
    elif (
        confidence_score >= 0.56
        and observation_count >= 60
    ):
        confidence = "moderate"
    else:
        confidence = "low"

    metrics = {
        "estimated_loss_fraction": (
            estimated_loss_fraction
        ),
        "recent_energy_loss_median": (
            float(recent_loss)
        ),
        "reference_energy_loss_median": (
            float(reference_loss)
        ),
        "persistence_delta": (
            persistence_delta
        ),
        "persistent_ratio": (
            persistent_ratio
        ),
        "trend_consensus": (
            trend_consensus
        ),
        "annualized_trend_fraction": (
            annualized
        ),
        "average_quality_weight": (
            average_quality
        ),
        "weather_coverage": (
            weather_coverage
        ),
    }

    factors = _cause_likelihoods(
        items,
        metrics,
        span_days,
        weather_coverage,
    )

    return {
        "available": True,
        "algorithm_version": (
            ALGORITHM_VERSION
        ),
        "status": status,
        "severity": severity,
        "degradation_likelihood_percent": (
            probability_percent
        ),
        "confidence": confidence,
        "confidence_score_percent": round(
            confidence_score * 100.0,
            1,
        ),
        "observations": observation_count,
        "date_span_days": span_days,
        "analysis_start_date": (
            items[0]["date"].isoformat()
        ),
        "analysis_end_date": (
            items[-1]["date"].isoformat()
        ),
        "estimated_recent_loss_percent": round(
            estimated_loss_fraction
            * 100.0,
            2,
        ),
        "annualized_trend_percent": (
            round(
                annualized * 100.0,
                2,
            )
            if annualized is not None
            else None
        ),
        "physical_ageing_assessment": (
            "eligible_for_long_term_inference"
            if (
                span_days
                >= PHYSICAL_AGEING_MIN_SPAN_DAYS
            )
            else (
                "insufficient_span_for_physical_ageing_claim"
            )
        ),
        "trend_tests": {
            "energy_loss": {
                "theil_sen_slope_per_day": (
                    energy_trend[
                        "sen"
                    ].get(
                        "slope_per_day"
                    )
                ),
                "observed_change_percent": round(
                    (
                        energy_trend.get(
                            "observed_change"
                        )
                        or 0.0
                    )
                    * 100.0,
                    3,
                ),
                "mann_kendall_tau": (
                    energy_trend[
                        "mann_kendall"
                    ].get("tau")
                ),
                "mann_kendall_p_value": (
                    energy_trend[
                        "mann_kendall"
                    ].get("p_value")
                ),
                "evidence_score": round(
                    energy_trend[
                        "evidence"
                    ]
                    * 100.0,
                    1,
                ),
            },
            "peak_loss": {
                "theil_sen_slope_per_day": (
                    peak_trend[
                        "sen"
                    ].get(
                        "slope_per_day"
                    )
                ),
                "observed_change_percent": round(
                    (
                        peak_trend.get(
                            "observed_change"
                        )
                        or 0.0
                    )
                    * 100.0,
                    3,
                ),
                "mann_kendall_tau": (
                    peak_trend[
                        "mann_kendall"
                    ].get("tau")
                ),
                "mann_kendall_p_value": (
                    peak_trend[
                        "mann_kendall"
                    ].get("p_value")
                ),
                "evidence_score": round(
                    peak_trend[
                        "evidence"
                    ]
                    * 100.0,
                    1,
                ),
            },
            "shape": {
                "theil_sen_slope_per_day": (
                    shape_trend[
                        "sen"
                    ].get(
                        "slope_per_day"
                    )
                ),
                "observed_change_points": round(
                    shape_trend.get(
                        "observed_change"
                    )
                    or 0.0,
                    3,
                ),
                "mann_kendall_tau": (
                    shape_trend[
                        "mann_kendall"
                    ].get("tau")
                ),
                "mann_kendall_p_value": (
                    shape_trend[
                        "mann_kendall"
                    ].get("p_value")
                ),
                "evidence_score": round(
                    shape_trend[
                        "evidence"
                    ]
                    * 100.0,
                    1,
                ),
            },
            "ewma_cusum": shift,
        },
        "persistence": {
            "recent_energy_loss_percent": round(
                float(recent_loss)
                * 100.0,
                2,
            ),
            "reference_energy_loss_percent": round(
                float(reference_loss)
                * 100.0,
                2,
            ),
            "delta_percent": round(
                persistence_delta
                * 100.0,
                2,
            ),
            "recent_days_below_baseline_ratio": round(
                persistent_ratio,
                3,
            ),
        },
        "likely_factors": factors,
        "dominant_factor": (
            factors[0]
            if factors
            else None
        ),
        "data_quality": {
            "average_quality_weight": round(
                average_quality,
                3,
            ),
            "weather_coverage": round(
                weather_coverage,
                3,
            ),
            "usable_energy_days": len(
                energy_series
            ),
            "usable_peak_days": len(
                peak_series
            ),
        },
        "maintenance_context": (
            maintenance_context
        ),
        "interpretation_limits": [
            (
                "A probabilidade é um índice estatístico de evidência, "
                "não uma probabilidade causal Bayesiana calibrada em laboratório."
            ),
            (
                "Sem irradiância no plano dos módulos e temperatura de célula, "
                "o sistema detecta perda operacional progressiva, não mede "
                "diretamente degradação física do módulo."
            ),
            (
                "As probabilidades de causa são relativas entre hipóteses e "
                "devem orientar inspeção, não substituir diagnóstico técnico em campo."
            ),
        ],
    }
