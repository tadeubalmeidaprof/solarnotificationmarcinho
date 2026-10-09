from __future__ import annotations

import math
from datetime import date, datetime
from statistics import median
from typing import Any


ALGORITHM_VERSION = "curve-v2.0"
DEFAULT_SAMPLE_SECONDS = 300.0
ANOMALY_THRESHOLD = 41.0


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _weather_context(weather: dict | None) -> str:
    weather = weather or {}
    clouds = _number(weather.get("average_cloud_cover_percent"))
    rain = _number(weather.get("total_precipitation_mm"))

    if clouds is not None and clouds >= 75:
        return "unfavorable"
    if rain is not None and rain > 3:
        return "unfavorable"
    if (
        clouds is not None
        and clouds <= 55
        and (rain is None or rain <= 1)
    ):
        return "favorable"
    return "mixed_or_unknown"


def _rain_class(weather: dict | None) -> str:
    rain = _number((weather or {}).get("total_precipitation_mm"))
    if rain is None:
        return "unknown"
    if rain <= 1:
        return "dry"
    if rain <= 3:
        return "light"
    return "wet"


def _weather_similarity(current: dict | None, historical: dict | None) -> float:
    current = current or {}
    historical = historical or {}
    pieces: list[float] = []

    current_cloud = _number(current.get("average_cloud_cover_percent"))
    historical_cloud = _number(historical.get("average_cloud_cover_percent"))
    if current_cloud is not None and historical_cloud is not None:
        pieces.append(max(0.15, 1.0 - abs(current_cloud - historical_cloud) / 80.0))

    current_temp = _number(current.get("average_temperature_c"))
    historical_temp = _number(historical.get("average_temperature_c"))
    if current_temp is not None and historical_temp is not None:
        pieces.append(max(0.25, math.exp(-abs(current_temp - historical_temp) / 10.0)))

    current_rain = _rain_class(current)
    historical_rain = _rain_class(historical)
    if current_rain != "unknown" and historical_rain != "unknown":
        if current_rain == historical_rain:
            pieces.append(1.0)
        elif {current_rain, historical_rain} <= {"dry", "light"}:
            pieces.append(0.75)
        else:
            pieces.append(0.35)

    if not pieces:
        return 0.60

    return sum(pieces) / len(pieces)


def _minutes(timestamp: datetime) -> int:
    return timestamp.hour * 60 + timestamp.minute


def _minute_bin(timestamp: datetime, size: int = 5) -> int:
    minute = _minutes(timestamp)
    return (minute // size) * size


def _normalize_points(points: list[dict]) -> list[dict]:
    clean = []
    for point in points or []:
        timestamp = point.get("timestamp")
        power_w = _number(point.get("power_w"))
        if not isinstance(timestamp, datetime) or power_w is None:
            continue
        clean.append(
            {
                "timestamp": timestamp,
                "power_w": max(power_w, 0.0),
            }
        )

    clean.sort(key=lambda item: item["timestamp"])
    return clean


def _median_sample_seconds(points: list[dict]) -> float:
    deltas = []
    for current, nxt in zip(points, points[1:]):
        delta = (nxt["timestamp"] - current["timestamp"]).total_seconds()
        if 0 < delta <= 1200:
            deltas.append(delta)

    return float(median(deltas)) if deltas else DEFAULT_SAMPLE_SECONDS


def _delta_seconds(points: list[dict], index: int, fallback: float) -> float:
    if index + 1 >= len(points):
        return fallback

    delta = (
        points[index + 1]["timestamp"] - points[index]["timestamp"]
    ).total_seconds()

    if delta <= 0 or delta > 1200:
        return fallback

    return float(delta)


def _weighted_median(values: list[tuple[float, float]]) -> float | None:
    clean = [
        (float(value), max(float(weight), 0.0))
        for value, weight in values
        if value is not None and weight is not None and float(weight) > 0
    ]
    if not clean:
        return None

    clean.sort(key=lambda item: item[0])
    total_weight = sum(weight for _, weight in clean)
    midpoint = total_weight / 2.0
    running = 0.0

    for value, weight in clean:
        running += weight
        if running >= midpoint:
            return value

    return clean[-1][0]


def _mad(values: list[float], center: float) -> float:
    if not values:
        return 0.0
    return float(median(abs(value - center) for value in values))


def _profile(
    points: list[dict],
    peak_power_kwp: float,
    threshold_w: float,
) -> dict:
    points = _normalize_points(points)
    if not points:
        return {"available": False}

    sample_seconds = _median_sample_seconds(points)
    peak_w = peak_power_kwp * 1000.0
    energy_kwh = 0.0
    active_seconds = 0.0
    significant: list[dict] = []
    normalized_values: list[float] = []

    for index, point in enumerate(points):
        delta_seconds = _delta_seconds(points, index, sample_seconds)
        power_w = point["power_w"]
        energy_kwh += power_w * (delta_seconds / 3600.0) / 1000.0

        normalized = power_w / peak_w if peak_w > 0 else 0.0
        normalized_values.append(normalized)

        if power_w >= threshold_w:
            active_seconds += delta_seconds
            significant.append(point)

    volatility_values = [
        abs(current - previous)
        for previous, current in zip(normalized_values, normalized_values[1:])
    ]
    volatility = (
        float(median(volatility_values))
        if volatility_values
        else 0.0
    )

    span_seconds = max(
        0.0,
        (points[-1]["timestamp"] - points[0]["timestamp"]).total_seconds(),
    )
    expected_samples = (
        int(span_seconds / sample_seconds) + 1
        if sample_seconds > 0
        else len(points)
    )
    missing_ratio = (
        max(0.0, 1.0 - len(points) / expected_samples)
        if expected_samples > 0
        else 0.0
    )

    return {
        "available": True,
        "samples": len(points),
        "sample_seconds": sample_seconds,
        "energy_kwh": energy_kwh,
        "normalized_energy_hours": (
            energy_kwh / peak_power_kwp
            if peak_power_kwp > 0
            else None
        ),
        "active_hours": active_seconds / 3600.0,
        "peak_fraction": (
            max(normalized_values)
            if normalized_values
            else None
        ),
        "generation_start_minute": (
            _minutes(significant[0]["timestamp"])
            if significant
            else None
        ),
        "generation_end_minute": (
            _minutes(significant[-1]["timestamp"])
            if significant
            else None
        ),
        "volatility": volatility,
        "missing_ratio": missing_ratio,
    }


def _segment_below_threshold(
    points: list[dict],
    threshold_w: float,
    start_index: int,
    end_index: int,
) -> list[dict]:
    segments = []
    segment_start = None

    for index in range(start_index, end_index + 1):
        low = float(points[index]["power_w"]) < threshold_w

        if low and segment_start is None:
            segment_start = index

        is_last = index == end_index
        if segment_start is not None and (not low or is_last):
            segment_end = index if low and is_last else index - 1

            if segment_end >= segment_start:
                segment = points[segment_start : segment_end + 1]
                duration_seconds = (
                    segment[-1]["timestamp"] - segment[0]["timestamp"]
                ).total_seconds() + _median_sample_seconds(segment)

                segments.append(
                    {
                        "start": segment[0]["timestamp"],
                        "end": segment[-1]["timestamp"],
                        "duration_minutes": duration_seconds / 60.0,
                    }
                )

            segment_start = None

    return segments


def _flat_segments(
    points: list[dict],
    peak_w: float,
) -> tuple[list[dict], list[dict]]:
    if not points:
        return [], []

    tolerance_w = max(20.0, peak_w * 0.005)
    plateau_start = 0
    clipping: list[dict] = []
    suspicious: list[dict] = []

    for index in range(1, len(points) + 1):
        same_level = False
        if index < len(points):
            same_level = abs(
                points[index]["power_w"]
                - points[index - 1]["power_w"]
            ) <= tolerance_w

        if same_level:
            continue

        if index - plateau_start >= 4:
            segment = points[plateau_start:index]
            duration = (
                segment[-1]["timestamp"] - segment[0]["timestamp"]
            ).total_seconds() + _median_sample_seconds(segment)
            average_power = sum(
                point["power_w"] for point in segment
            ) / len(segment)

            if duration >= 1200 and average_power >= peak_w * 0.15:
                item = {
                    "start": segment[0]["timestamp"].strftime("%H:%M"),
                    "end": segment[-1]["timestamp"].strftime("%H:%M"),
                    "duration_minutes": round(duration / 60.0, 1),
                    "fraction_of_peak": round(average_power / peak_w, 3),
                }

                if item["fraction_of_peak"] >= 0.90:
                    clipping.append(item)
                else:
                    suspicious.append(item)

        plateau_start = index

    return clipping, suspicious


def _history_date(item: dict, points: list[dict]) -> date | None:
    raw = item.get("date") or item.get("report_date")
    if isinstance(raw, date):
        return raw

    if raw:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            pass

    if points:
        return points[0]["timestamp"].date()

    return None


def _prepare_history(
    *,
    historical_profiles: list | None,
    current_weather: dict | None,
    current_date: date,
    peak_power_kwp: float,
    threshold_w: float,
    max_days: int = 20,
) -> list[dict]:
    candidates: list[dict] = []

    for raw_item in historical_profiles or []:
        if isinstance(raw_item, dict) and "points" in raw_item:
            raw_points = raw_item.get("points") or []
            weather = raw_item.get("weather") or {}
            explicit_weight = _number(raw_item.get("weight"))
        else:
            raw_points = raw_item if isinstance(raw_item, list) else []
            weather = {}
            explicit_weight = None
            raw_item = {}

        points = _normalize_points(raw_points)
        profile = _profile(points, peak_power_kwp, threshold_w)

        if not profile.get("available"):
            continue

        if profile.get("active_hours", 0.0) < 2.5:
            continue

        historical_date = _history_date(raw_item, points)
        days_ago = (
            max(1, (current_date - historical_date).days)
            if historical_date is not None
            else len(candidates) + 1
        )

        recency_weight = math.exp(-days_ago / 30.0)
        climate_similarity = _weather_similarity(
            current_weather,
            weather,
        )
        weight = (
            explicit_weight
            if explicit_weight is not None and explicit_weight > 0
            else recency_weight * climate_similarity
        )

        candidates.append(
            {
                "points": points,
                "profile": profile,
                "weather": weather,
                "date": historical_date,
                "days_ago": days_ago,
                "weather_similarity": climate_similarity,
                "weight": weight,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["weight"],
            -item["days_ago"],
        ),
        reverse=True,
    )

    selected = [
        item
        for item in candidates
        if item["weather_similarity"] >= 0.35
    ][:max_days]

    if len(selected) < min(5, len(candidates)):
        selected = candidates[:max_days]

    return selected


def _curve_baseline(
    history: list[dict],
    peak_w: float,
) -> dict[int, dict]:
    buckets: dict[int, list[tuple[float, float]]] = {}

    for day in history:
        for point in day["points"]:
            minute = _minute_bin(point["timestamp"])
            normalized = point["power_w"] / peak_w if peak_w > 0 else 0.0
            buckets.setdefault(minute, []).append(
                (normalized, day["weight"])
            )

    baseline: dict[int, dict] = {}

    for minute, weighted_values in buckets.items():
        if len(weighted_values) < 3:
            continue

        center = _weighted_median(weighted_values)
        if center is None:
            continue

        values = [value for value, _ in weighted_values]
        baseline[minute] = {
            "median": center,
            "mad": _mad(values, center),
            "samples": len(values),
        }

    return baseline


def _score_deficit(
    current: float | None,
    expected: float | None,
    tolerance: float,
    full_score_at: float,
) -> tuple[float, float | None]:
    if (
        current is None
        or expected is None
        or expected <= 0
    ):
        return 0.0, None

    deficit = max(0.0, (expected - current) / expected)
    score = _clamp(
        (deficit - tolerance)
        / max(full_score_at - tolerance, 0.01)
        * 100.0
    )

    return score, deficit


def _shape_score(
    points: list[dict],
    baseline: dict[int, dict],
    peak_w: float,
) -> tuple[float, float, list[dict]]:
    contributions = []
    deviations = []
    matched = 0

    for point in points:
        minute = _minute_bin(point["timestamp"])
        reference = baseline.get(minute)
        if reference is None:
            continue

        matched += 1
        current = point["power_w"] / peak_w if peak_w > 0 else 0.0
        expected = float(reference["median"])

        if current >= expected:
            contributions.append(0.0)
            continue

        robust_scale = max(
            1.4826 * float(reference["mad"]),
            0.04,
        )
        z_score = (expected - current) / robust_scale
        contribution = _clamp(
            (z_score - 1.5) / 3.5 * 100.0
        )
        contributions.append(contribution)

        if contribution >= 35:
            deviations.append(
                {
                    "time": point["timestamp"].strftime("%H:%M"),
                    "current_fraction": round(current, 3),
                    "expected_fraction": round(expected, 3),
                    "robust_z": round(z_score, 2),
                }
            )

    coverage = matched / len(points) if points else 0.0
    score = (
        sum(contributions) / len(contributions)
        if contributions
        else 0.0
    )

    return score, coverage, deviations[:8]


def _interruption_score(
    points: list[dict],
    threshold_w: float,
) -> tuple[float, list[dict]]:
    significant_indexes = [
        index
        for index, point in enumerate(points)
        if point["power_w"] >= threshold_w
    ]

    if len(significant_indexes) < 2:
        return 0.0, []

    segments = []
    for segment in _segment_below_threshold(
        points,
        threshold_w,
        significant_indexes[0],
        significant_indexes[-1],
    ):
        midpoint = segment["start"] + (
            segment["end"] - segment["start"]
        ) / 2

        if (
            9 <= midpoint.hour < 15
            and segment["duration_minutes"] >= 15
        ):
            segments.append(segment)

    if not segments:
        return 0.0, []

    total_minutes = sum(
        item["duration_minutes"]
        for item in segments
    )
    longest = max(
        item["duration_minutes"]
        for item in segments
    )

    score = max(
        _clamp(total_minutes / 60.0 * 100.0),
        _clamp(longest / 35.0 * 100.0),
    )

    safe_segments = [
        {
            "start": item["start"].strftime("%H:%M"),
            "end": item["end"].strftime("%H:%M"),
            "duration_minutes": round(item["duration_minutes"], 1),
        }
        for item in segments[:5]
    ]

    return score, safe_segments


def _sudden_drops(points: list[dict], peak_w: float) -> list[dict]:
    events = []

    for current, nxt in zip(points, points[1:]):
        delta_seconds = (
            nxt["timestamp"] - current["timestamp"]
        ).total_seconds()

        if not 0 < delta_seconds <= 900:
            continue

        before = current["power_w"]
        after = nxt["power_w"]
        absolute_drop = before - after
        relative_drop = absolute_drop / before if before > 0 else 0.0

        if (
            before >= peak_w * 0.35
            and absolute_drop >= peak_w * 0.30
            and relative_drop >= 0.50
        ):
            events.append(
                {
                    "time": nxt["timestamp"].strftime("%H:%M"),
                    "drop_fraction_of_peak": round(
                        absolute_drop / peak_w,
                        3,
                    ),
                    "relative_drop": round(relative_drop, 3),
                }
            )

    return events[:5]


def _window_score(
    current: dict,
    baseline_start: float | None,
    baseline_end: float | None,
) -> tuple[float, dict]:
    delay = 0.0
    advance = 0.0

    start_now = current.get("generation_start_minute")
    end_now = current.get("generation_end_minute")

    if start_now is not None and baseline_start is not None:
        delay = max(0.0, float(start_now) - baseline_start)

    if end_now is not None and baseline_end is not None:
        advance = max(0.0, baseline_end - float(end_now))

    effective = max(delay, advance)
    score = _clamp(
        (effective - 30.0) / 90.0 * 100.0
    )

    return score, {
        "start_delay_minutes": round(delay, 1),
        "early_end_minutes": round(advance, 1),
    }


def _volatility_score(
    current_volatility: float,
    history: list[dict],
) -> tuple[float, float | None]:
    values = [
        (
            float(item["profile"]["volatility"]),
            float(item["weight"]),
        )
        for item in history
        if item["profile"].get("volatility") is not None
    ]

    baseline = _weighted_median(values)
    if baseline is None:
        return 0.0, None

    floor = 0.01
    ratio = current_volatility / max(baseline, floor)
    score = _clamp(
        (ratio - 1.75) / 2.25 * 100.0
    )

    return score, ratio


def _telemetry_score(
    current: dict,
    suspicious_flat: list[dict],
) -> float:
    missing_component = _clamp(
        float(current.get("missing_ratio") or 0.0) / 0.20 * 100.0
    )

    flat_minutes = sum(
        float(item.get("duration_minutes") or 0.0)
        for item in suspicious_flat
    )
    flat_component = _clamp(
        flat_minutes / 60.0 * 100.0
    )

    return max(missing_component, flat_component)


def _baseline_metric(
    history: list[dict],
    field: str,
) -> float | None:
    values = []

    for item in history:
        value = _number(item["profile"].get(field))
        if value is None:
            continue
        values.append((value, item["weight"]))

    return _weighted_median(values)


def _confidence(
    baseline_days: int,
    shape_coverage: float,
    complete_day: bool,
) -> str:
    if not complete_day:
        return "low"

    if baseline_days >= 10 and shape_coverage >= 0.70:
        return "high"

    if baseline_days >= 5 and shape_coverage >= 0.55:
        return "moderate"

    if baseline_days >= 3:
        return "low"

    return "very_low"


def _status_from_score(score: float, complete_day: bool) -> tuple[str, str]:
    if not complete_day:
        return "partial_day", "inconclusive"

    if score <= 20:
        return "normal", "normal"

    if score <= 40:
        return "minor_variation", "minor"

    if score <= 60:
        return "attention", "moderate"

    if score <= 80:
        return "anomaly_detected", "relevant"

    return "anomaly_detected", "strong"


def analyze_power_curve(
    *,
    points: list[dict],
    peak_power_kwp: float | None,
    historical_profiles: list | None = None,
    weather: dict | None = None,
    complete_day: bool = True,
    historical_analysis_scores: list[float] | None = None,
) -> dict:
    """
    Analisa a curva da própria usina contra um baseline histórico robusto.

    O baseline usa potência normalizada, mediana/MAD por faixa de 5 minutos,
    peso por recência e, quando disponível, similaridade climática.
    """
    normalized = _normalize_points(points)

    if not normalized:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "Curva de potência indisponível.",
            "algorithm_version": ALGORITHM_VERSION,
        }

    if peak_power_kwp is None or peak_power_kwp <= 0:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "Potência pico da usina indisponível.",
            "algorithm_version": ALGORITHM_VERSION,
        }

    if len(normalized) < 6:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "A curva tem amostras insuficientes para análise.",
            "algorithm_version": ALGORITHM_VERSION,
        }

    peak_w = float(peak_power_kwp) * 1000.0
    threshold_w = max(50.0, peak_w * 0.05)
    current = _profile(
        normalized,
        float(peak_power_kwp),
        threshold_w,
    )
    current_date = normalized[0]["timestamp"].date()

    history = _prepare_history(
        historical_profiles=historical_profiles,
        current_weather=weather,
        current_date=current_date,
        peak_power_kwp=float(peak_power_kwp),
        threshold_w=threshold_w,
    )

    baseline_curve = _curve_baseline(
        history,
        peak_w,
    )

    shape_score, shape_coverage, shape_deviations = _shape_score(
        normalized,
        baseline_curve,
        peak_w,
    )

    expected_energy_hours = _baseline_metric(
        history,
        "normalized_energy_hours",
    )
    energy_score, energy_deficit = _score_deficit(
        current.get("normalized_energy_hours"),
        expected_energy_hours,
        tolerance=0.10,
        full_score_at=0.40,
    )

    expected_peak = _baseline_metric(
        history,
        "peak_fraction",
    )
    peak_score, peak_deficit = _score_deficit(
        current.get("peak_fraction"),
        expected_peak,
        tolerance=0.10,
        full_score_at=0.35,
    )

    interruption_score, interruption_segments = _interruption_score(
        normalized,
        threshold_w,
    )

    baseline_start = _baseline_metric(
        history,
        "generation_start_minute",
    )
    baseline_end = _baseline_metric(
        history,
        "generation_end_minute",
    )
    window_score, window_details = _window_score(
        current,
        baseline_start,
        baseline_end,
    )

    volatility_score, volatility_ratio = _volatility_score(
        float(current.get("volatility") or 0.0),
        history,
    )

    clipping, suspicious_flat = _flat_segments(
        normalized,
        peak_w,
    )
    telemetry_score = _telemetry_score(
        current,
        suspicious_flat,
    )

    if not complete_day:
        energy_score = 0.0
        peak_score = 0.0
        window_score = 0.0

    component_scores = {
        "shape": shape_score,
        "energy": energy_score,
        "peak": peak_score,
        "interruption": interruption_score,
        "window": window_score,
        "volatility": volatility_score,
        "telemetry": telemetry_score,
    }

    weights = {
        "shape": 0.30,
        "energy": 0.20,
        "peak": 0.15,
        "interruption": 0.15,
        "window": 0.08,
        "volatility": 0.07,
        "telemetry": 0.05,
    }

    anomaly_score = sum(
        component_scores[key] * weights[key]
        for key in weights
    )

    weather_context = _weather_context(weather)
    if weather_context == "unfavorable":
        anomaly_score *= 0.85

    prior_scores = [
        float(score)
        for score in (historical_analysis_scores or [])
        if _number(score) is not None
    ][:4]
    persistence_observations = [anomaly_score] + prior_scores
    persistent_days = sum(
        1
        for score in persistence_observations
        if score >= ANOMALY_THRESHOLD
    )
    comparable_days = len(persistence_observations)
    persistence_ratio = (
        persistent_days / comparable_days
        if comparable_days
        else 0.0
    )

    if (
        anomaly_score >= ANOMALY_THRESHOLD
        and comparable_days >= 3
        and persistence_ratio >= 0.60
    ):
        anomaly_score = min(100.0, anomaly_score + 5.0)

    anomaly_score = round(anomaly_score, 1)
    status, severity_band = _status_from_score(
        anomaly_score,
        complete_day,
    )
    confidence = _confidence(
        len(history),
        shape_coverage,
        complete_day,
    )

    anomalies = []
    sudden_events = _sudden_drops(
        normalized,
        peak_w,
    )

    if shape_score >= 45:
        anomalies.append(
            {
                "type": "historical_shape_deviation",
                "severity": "medium",
                "score": round(shape_score, 1),
                "deviations": shape_deviations,
                "explanation": (
                    "Trechos da curva ficaram abaixo do envelope histórico "
                    "robusto da própria usina."
                ),
            }
        )

    if interruption_segments:
        anomalies.append(
            {
                "type": "midday_interruption",
                "severity": (
                    "high"
                    if interruption_score >= 70
                    else "medium"
                ),
                "score": round(interruption_score, 1),
                "segments": interruption_segments,
                "explanation": (
                    "Houve período relevante abaixo do limiar de geração "
                    "no miolo do dia."
                ),
            }
        )

    if sudden_events:
        anomalies.append(
            {
                "type": "sudden_power_drops",
                "severity": (
                    "medium"
                    if weather_context == "favorable"
                    else "low"
                ),
                "count": len(sudden_events),
                "events": sudden_events,
                "explanation": (
                    "Foram detectadas quedas rápidas de potência. "
                    "O clima e a persistência definem o peso desse indício."
                ),
            }
        )

    if peak_score >= 45:
        anomalies.append(
            {
                "type": "suppressed_peak",
                "severity": "medium",
                "score": round(peak_score, 1),
                "current_peak_fraction": round(
                    float(current.get("peak_fraction") or 0.0),
                    3,
                ),
                "historical_median_peak_fraction": (
                    round(expected_peak, 3)
                    if expected_peak is not None
                    else None
                ),
                "explanation": (
                    "O pico do dia ficou bem abaixo do padrão histórico "
                    "ponderado da usina."
                ),
            }
        )

    if energy_score >= 45:
        anomalies.append(
            {
                "type": "energy_below_expected",
                "severity": "medium",
                "score": round(energy_score, 1),
                "deficit_percent": (
                    round((energy_deficit or 0.0) * 100.0, 1)
                ),
                "explanation": (
                    "A área normalizada sob a curva ficou abaixo do "
                    "baseline histórico comparável."
                ),
            }
        )

    if window_score >= 35:
        anomaly_type = (
            "delayed_generation_start"
            if window_details["start_delay_minutes"]
            >= window_details["early_end_minutes"]
            else "early_generation_end"
        )
        anomalies.append(
            {
                "type": anomaly_type,
                "severity": "medium",
                "score": round(window_score, 1),
                **window_details,
                "explanation": (
                    "A janela produtiva se afastou da referência histórica."
                ),
            }
        )

    if suspicious_flat:
        anomalies.append(
            {
                "type": "unusual_flatline",
                "severity": "low",
                "score": round(telemetry_score, 1),
                "segments": suspicious_flat[:3],
                "explanation": (
                    "A potência permaneceu quase constante por tempo "
                    "prolongado abaixo da potência nominal."
                ),
            }
        )

    informational_patterns = []
    if clipping:
        informational_patterns.append(
            {
                "type": "clipping_like_plateau",
                "segments": clipping[:3],
                "explanation": (
                    "Há patamar próximo da potência nominal; isso pode "
                    "representar clipping normal do inversor."
                ),
            }
        )

    baseline_profile = {
        "normalized_energy_hours": (
            round(expected_energy_hours, 3)
            if expected_energy_hours is not None
            else None
        ),
        "peak_fraction": (
            round(expected_peak, 3)
            if expected_peak is not None
            else None
        ),
        "generation_start_minute": (
            round(baseline_start, 1)
            if baseline_start is not None
            else None
        ),
        "generation_end_minute": (
            round(baseline_end, 1)
            if baseline_end is not None
            else None
        ),
    }

    return {
        "available": True,
        "algorithm_version": ALGORITHM_VERSION,
        "status": status,
        "severity_band": severity_band,
        "confidence": confidence,
        "anomaly_score": anomaly_score,
        "weather_context": weather_context,
        "complete_day": bool(complete_day),
        "baseline_days_used": len(history),
        "shape_baseline_coverage": round(shape_coverage, 3),
        "meaningful_generation_threshold_w": round(threshold_w, 1),
        "component_scores": {
            key: round(value, 1)
            for key, value in component_scores.items()
        },
        "current_profile": {
            "samples": current.get("samples"),
            "energy_kwh": round(
                float(current.get("energy_kwh") or 0.0),
                3,
            ),
            "normalized_energy_hours": (
                round(
                    float(current["normalized_energy_hours"]),
                    3,
                )
                if current.get("normalized_energy_hours") is not None
                else None
            ),
            "active_hours": round(
                float(current.get("active_hours") or 0.0),
                2,
            ),
            "peak_fraction_of_installed": (
                round(
                    float(current["peak_fraction"]),
                    3,
                )
                if current.get("peak_fraction") is not None
                else None
            ),
            "generation_start_minute": current.get(
                "generation_start_minute"
            ),
            "generation_end_minute": current.get(
                "generation_end_minute"
            ),
            "volatility": round(
                float(current.get("volatility") or 0.0),
                4,
            ),
            "missing_sample_ratio": round(
                float(current.get("missing_ratio") or 0.0),
                4,
            ),
        },
        "baseline_profile": baseline_profile,
        "comparison": {
            "energy_deficit_percent": (
                round((energy_deficit or 0.0) * 100.0, 1)
                if energy_deficit is not None
                else None
            ),
            "peak_deficit_percent": (
                round((peak_deficit or 0.0) * 100.0, 1)
                if peak_deficit is not None
                else None
            ),
            "volatility_ratio": (
                round(volatility_ratio, 2)
                if volatility_ratio is not None
                else None
            ),
            **window_details,
        },
        "persistence": {
            "persistent_days": persistent_days,
            "comparable_days": comparable_days,
            "ratio": round(persistence_ratio, 3),
            "persistent": (
                anomaly_score >= ANOMALY_THRESHOLD
                and comparable_days >= 3
                and persistence_ratio >= 0.60
            ),
        },
        "selected_history": [
            {
                "date": (
                    item["date"].isoformat()
                    if item.get("date") is not None
                    else None
                ),
                "days_ago": item["days_ago"],
                "weight": round(item["weight"], 4),
                "weather_similarity": round(
                    item["weather_similarity"],
                    3,
                ),
            }
            for item in history
        ],
        "anomalies": anomalies,
        "informational_patterns": informational_patterns,
        "maintenance_conclusion": (
            "O score mede desvio operacional, não confirma defeito. "
            "Recomendação de manutenção deve considerar persistência, "
            "clima, alarmes e histórico real de intervenções."
        ),
    }
