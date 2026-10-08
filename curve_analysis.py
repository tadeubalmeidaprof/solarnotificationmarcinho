from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _minutes(timestamp: datetime) -> int:
    return timestamp.hour * 60 + timestamp.minute


def _median_sample_seconds(points: list[dict]) -> float:
    deltas = []
    for current, nxt in zip(points, points[1:]):
        delta = (nxt["timestamp"] - current["timestamp"]).total_seconds()
        if 0 < delta <= 1200:
            deltas.append(delta)
    return float(median(deltas)) if deltas else 300.0


def _profile(
    points: list[dict],
    peak_power_kwp: float,
    threshold_w: float,
) -> dict:
    if not points:
        return {"available": False}

    points = sorted(points, key=lambda item: item["timestamp"])
    sample_seconds = _median_sample_seconds(points)
    peak_w = peak_power_kwp * 1000
    significant = [
        point
        for point in points
        if float(point["power_w"]) >= threshold_w
    ]

    active_hours = (
        len(significant) * sample_seconds / 3600
        if significant
        else 0.0
    )

    return {
        "available": True,
        "peak_fraction": (
            max(float(point["power_w"]) for point in points) / peak_w
            if peak_w > 0
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
        "active_hours": active_hours,
        "sample_seconds": sample_seconds,
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
                start_ts = points[segment_start]["timestamp"]
                end_ts = points[segment_end]["timestamp"]
                duration = (
                    end_ts - start_ts
                ).total_seconds() + _median_sample_seconds(
                    points[segment_start:segment_end + 1]
                )
                segments.append(
                    {
                        "start": start_ts,
                        "end": end_ts,
                        "duration_minutes": duration / 60,
                    }
                )
            segment_start = None

    return segments


def analyze_power_curve(
    *,
    points: list[dict],
    peak_power_kwp: float | None,
    historical_profiles: list[list[dict]] | None = None,
    weather: dict | None = None,
    complete_day: bool = True,
) -> dict:
    """
    Analisa o formato de uma curva de potência sem diagnosticar defeitos.

    O resultado é conservador: condições de nuvens/chuva reduzem confiança e
    evitam transformar variação natural em suspeita de manutenção.
    """
    if not points:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "Curva de potência indisponível.",
        }

    if peak_power_kwp is None or peak_power_kwp <= 0:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "Potência pico da usina indisponível.",
        }

    normalized = []
    for point in points:
        timestamp = point.get("timestamp")
        power_w = _number(point.get("power_w"))
        if not isinstance(timestamp, datetime) or power_w is None:
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "power_w": max(power_w, 0.0),
            }
        )

    normalized.sort(key=lambda item: item["timestamp"])
    if len(normalized) < 6:
        return {
            "available": False,
            "status": "inconclusive",
            "reason": "A curva tem amostras insuficientes para análise.",
        }

    peak_w = peak_power_kwp * 1000
    threshold_w = max(50.0, peak_w * 0.05)
    current = _profile(normalized, peak_power_kwp, threshold_w)
    weather_context = _weather_context(weather)

    historical = []
    for profile_points in historical_profiles or []:
        clean = []
        for point in profile_points:
            timestamp = point.get("timestamp")
            power_w = _number(point.get("power_w"))
            if isinstance(timestamp, datetime) and power_w is not None:
                clean.append(
                    {
                        "timestamp": timestamp,
                        "power_w": max(power_w, 0.0),
                    }
                )
        profile = _profile(clean, peak_power_kwp, threshold_w)
        if profile.get("available") and profile.get("active_hours", 0) >= 3:
            historical.append(profile)

    baseline = {}
    if len(historical) >= 3:
        starts = [
            row["generation_start_minute"]
            for row in historical
            if row.get("generation_start_minute") is not None
        ]
        ends = [
            row["generation_end_minute"]
            for row in historical
            if row.get("generation_end_minute") is not None
        ]
        peaks = [
            row["peak_fraction"]
            for row in historical
            if row.get("peak_fraction") is not None
        ]
        if starts:
            baseline["generation_start_minute"] = median(starts)
        if ends:
            baseline["generation_end_minute"] = median(ends)
        if peaks:
            baseline["peak_fraction"] = median(peaks)

    anomalies = []
    info = []

    significant_indexes = [
        index
        for index, point in enumerate(normalized)
        if point["power_w"] >= threshold_w
    ]

    if len(significant_indexes) >= 2:
        first_index = significant_indexes[0]
        last_index = significant_indexes[-1]
        interruptions = []
        for segment in _segment_below_threshold(
            normalized,
            threshold_w,
            first_index,
            last_index,
        ):
            midpoint = segment["start"] + (
                segment["end"] - segment["start"]
            ) / 2
            if (
                9 <= midpoint.hour < 15
                and segment["duration_minutes"] >= 15
            ):
                interruptions.append(segment)

        if interruptions:
            longest = max(
                segment["duration_minutes"]
                for segment in interruptions
            )
            anomalies.append(
                {
                    "type": "midday_interruption",
                    "severity": (
                        "high" if longest >= 30 else "medium"
                    ),
                    "count": len(interruptions),
                    "longest_minutes": round(longest, 1),
                    "explanation": (
                        "Houve período relevante de potência abaixo do limiar "
                        "no miolo do dia, entre trechos de geração."
                    ),
                }
            )

    sudden_events = []
    for index in range(len(normalized) - 1):
        current_point = normalized[index]
        next_point = normalized[index + 1]
        delta_seconds = (
            next_point["timestamp"] - current_point["timestamp"]
        ).total_seconds()
        if not 0 < delta_seconds <= 900:
            continue

        before = current_point["power_w"]
        after = next_point["power_w"]
        absolute_drop = before - after
        relative_drop = absolute_drop / before if before > 0 else 0

        if (
            before >= peak_w * 0.35
            and absolute_drop >= peak_w * 0.30
            and relative_drop >= 0.50
        ):
            sudden_events.append(
                {
                    "time": next_point["timestamp"].strftime("%H:%M"),
                    "drop_fraction_of_peak": round(
                        absolute_drop / peak_w,
                        3,
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
                "events": sudden_events[:5],
                "explanation": (
                    "Foram detectadas quedas rápidas de potência. "
                    "Nuvens podem causar esse padrão, por isso o contexto "
                    "meteorológico influencia a confiança."
                ),
            }
        )

    tolerance_w = max(20.0, peak_w * 0.005)
    plateau_start = 0
    plateaus = []
    for index in range(1, len(normalized) + 1):
        if index < len(normalized):
            same_level = abs(
                normalized[index]["power_w"]
                - normalized[index - 1]["power_w"]
            ) <= tolerance_w
        else:
            same_level = False

        if same_level:
            continue

        if index - plateau_start >= 4:
            segment = normalized[plateau_start:index]
            duration = (
                segment[-1]["timestamp"] - segment[0]["timestamp"]
            ).total_seconds() + _median_sample_seconds(segment)
            average_power = sum(
                point["power_w"] for point in segment
            ) / len(segment)
            if duration >= 1200 and average_power >= peak_w * 0.15:
                plateaus.append(
                    {
                        "start": segment[0]["timestamp"].strftime("%H:%M"),
                        "end": segment[-1]["timestamp"].strftime("%H:%M"),
                        "duration_minutes": round(duration / 60, 1),
                        "fraction_of_peak": round(
                            average_power / peak_w,
                            3,
                        ),
                    }
                )
        plateau_start = index

    clipping = [
        plateau for plateau in plateaus
        if plateau["fraction_of_peak"] >= 0.90
    ]
    suspicious_flat = [
        plateau for plateau in plateaus
        if plateau["fraction_of_peak"] < 0.90
    ]

    if clipping:
        info.append(
            {
                "type": "clipping_like_plateau",
                "count": len(clipping),
                "segments": clipping[:3],
                "explanation": (
                    "Há patamar próximo da potência nominal. Isso pode ser "
                    "clipping normal do inversor e não é tratado como falha."
                ),
            }
        )

    if suspicious_flat:
        anomalies.append(
            {
                "type": "unusual_flatline",
                "severity": "low",
                "count": len(suspicious_flat),
                "segments": suspicious_flat[:3],
                "explanation": (
                    "A curva ficou quase constante por tempo prolongado abaixo "
                    "da potência nominal. Pode ser característica operacional "
                    "ou limitação de telemetria; requer correlação com outros dias."
                ),
            }
        )

    if complete_day and baseline:
        start_now = current.get("generation_start_minute")
        end_now = current.get("generation_end_minute")
        peak_now = current.get("peak_fraction")

        baseline_start = baseline.get("generation_start_minute")
        baseline_end = baseline.get("generation_end_minute")
        baseline_peak = baseline.get("peak_fraction")

        if (
            start_now is not None
            and baseline_start is not None
            and start_now - baseline_start >= 45
            and weather_context == "favorable"
        ):
            anomalies.append(
                {
                    "type": "delayed_generation_start",
                    "severity": "medium",
                    "delay_minutes": round(
                        start_now - baseline_start,
                        1,
                    ),
                    "explanation": (
                        "A geração começou bem mais tarde que a mediana "
                        "dos dias históricos comparáveis."
                    ),
                }
            )

        if (
            end_now is not None
            and baseline_end is not None
            and baseline_end - end_now >= 45
            and weather_context == "favorable"
        ):
            anomalies.append(
                {
                    "type": "early_generation_end",
                    "severity": "medium",
                    "advance_minutes": round(
                        baseline_end - end_now,
                        1,
                    ),
                    "explanation": (
                        "A geração terminou bem mais cedo que a mediana "
                        "dos dias históricos comparáveis."
                    ),
                }
            )

        if (
            peak_now is not None
            and baseline_peak is not None
            and baseline_peak >= 0.35
            and peak_now < baseline_peak * 0.70
            and weather_context == "favorable"
        ):
            anomalies.append(
                {
                    "type": "suppressed_peak",
                    "severity": "medium",
                    "current_peak_fraction": round(peak_now, 3),
                    "historical_median_peak_fraction": round(
                        baseline_peak,
                        3,
                    ),
                    "explanation": (
                        "O pico do dia ficou muito abaixo da referência "
                        "histórica apesar de clima favorável."
                    ),
                }
            )

    high_count = sum(
        1 for item in anomalies if item.get("severity") == "high"
    )
    medium_count = sum(
        1 for item in anomalies if item.get("severity") == "medium"
    )

    if high_count:
        status = "anomaly_detected"
    elif medium_count >= 2:
        status = "anomaly_detected"
    elif anomalies:
        status = "attention"
    else:
        status = "normal"

    if weather_context == "unfavorable" and status == "anomaly_detected":
        status = "attention"

    if status == "normal":
        confidence = "moderate" if len(historical) >= 3 else "low"
    elif weather_context == "favorable" and len(historical) >= 3:
        confidence = "moderate"
    else:
        confidence = "low"

    return {
        "available": True,
        "status": status,
        "confidence": confidence,
        "weather_context": weather_context,
        "complete_day": bool(complete_day),
        "baseline_days_used": len(historical),
        "meaningful_generation_threshold_w": round(threshold_w, 1),
        "current_profile": {
            "peak_fraction_of_installed": (
                round(current["peak_fraction"], 3)
                if current.get("peak_fraction") is not None
                else None
            ),
            "generation_start_minute": current.get(
                "generation_start_minute"
            ),
            "generation_end_minute": current.get(
                "generation_end_minute"
            ),
            "active_hours_approx": round(
                current.get("active_hours", 0),
                2,
            ),
        },
        "baseline_profile": {
            key: round(value, 2)
            for key, value in baseline.items()
        },
        "anomalies": anomalies,
        "informational_patterns": info,
        "maintenance_conclusion": (
            "A análise da curva identifica padrões anormais, mas não confirma "
            "defeito nem necessidade de manutenção sozinha. O diagnóstico deve "
            "ser combinado com clima, alarmes, persistência e histórico."
        ),
    }
