import math
import unittest
from datetime import date, timedelta

from degradation_analysis import analyze_slow_degradation


def _synthetic_rows(
    days=120,
    progressive=False,
    telemetry_score=0.0,
):
    start = date(2026, 1, 1)
    rows = []

    for index in range(days):
        noise = (
            0.004 * math.sin(index * 1.7)
            + 0.002 * math.cos(index * 0.31)
        )
        progress = (
            index / max(days - 1, 1)
        )
        energy_loss = (
            0.06 * progress
            if progressive
            else 0.0
        ) + noise
        peak_loss = (
            0.045 * progress
            if progressive
            else 0.0
        ) + noise * 0.5
        shape_score = (
            20.0 * progress
            if progressive
            else 4.0 + 2.0 * math.sin(index / 8)
        )

        baseline_energy = 4.2
        baseline_peak = 0.82
        current_energy = (
            baseline_energy
            * (1.0 - energy_loss)
        )
        current_peak = (
            baseline_peak
            * (1.0 - peak_loss)
        )

        rows.append(
            {
                "report_date": (
                    start
                    + timedelta(days=index)
                ),
                "samples": 110,
                "active_hours": 7.5,
                "normalized_energy_hours": (
                    current_energy
                ),
                "peak_fraction": current_peak,
                "shape_score": shape_score,
                "energy_score": max(
                    energy_loss,
                    0.0,
                ) * 500,
                "peak_score": max(
                    peak_loss,
                    0.0,
                ) * 500,
                "interruption_score": 2.0,
                "volatility_score": 3.0,
                "telemetry_score": telemetry_score,
                "weather_context": "favorable",
                "cloud_cover_percent": (
                    25
                    + 10
                    * math.sin(index / 9)
                ),
                "rain_mm": 0.0,
                "avg_temperature_c": (
                    28
                    + 2
                    * math.sin(index / 30)
                ),
                "details": {
                    "complete_day": True,
                    "current_profile": {
                        "normalized_energy_hours": (
                            current_energy
                        ),
                        "peak_fraction_of_installed": (
                            current_peak
                        ),
                        "samples": 110,
                        "active_hours": 7.5,
                        "missing_sample_ratio": 0.0,
                    },
                    "baseline_profile": {
                        "normalized_energy_hours": (
                            baseline_energy
                        ),
                        "peak_fraction": (
                            baseline_peak
                        ),
                    },
                    "component_scores": {
                        "shape": shape_score,
                        "interruption": 2.0,
                        "volatility": 3.0,
                        "telemetry": (
                            telemetry_score
                        ),
                        "peak": max(
                            peak_loss,
                            0.0,
                        ) * 500,
                    },
                },
            }
        )

    return rows


class SlowDegradationAnalysisTests(
    unittest.TestCase
):
    def test_requires_enough_history(self):
        result = analyze_slow_degradation(
            _synthetic_rows(days=20)
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["status"],
            "warming_up",
        )

    def test_stable_series_remains_low_risk(self):
        result = analyze_slow_degradation(
            _synthetic_rows(days=120),
            requested_window_days=180,
        )

        self.assertTrue(result["available"])
        self.assertEqual(
            result["status"],
            "stable",
        )
        self.assertLess(
            result[
                "degradation_likelihood_percent"
            ],
            25,
        )
        self.assertLess(
            result[
                "estimated_recent_loss_percent"
            ],
            1.5,
        )

    def test_detects_progressive_loss(self):
        result = analyze_slow_degradation(
            _synthetic_rows(
                days=120,
                progressive=True,
            ),
            requested_window_days=180,
        )

        self.assertTrue(result["available"])
        self.assertEqual(
            result["status"],
            "probable_progressive_loss",
        )
        self.assertGreater(
            result[
                "degradation_likelihood_percent"
            ],
            70,
        )
        self.assertGreater(
            result[
                "estimated_recent_loss_percent"
            ],
            4.0,
        )

        energy = result[
            "trend_tests"
        ]["energy_loss"]
        self.assertGreater(
            energy["mann_kendall_tau"],
            0.5,
        )
        self.assertLess(
            energy["mann_kendall_p_value"],
            0.01,
        )

    def test_bad_telemetry_reduces_confidence(self):
        clean = analyze_slow_degradation(
            _synthetic_rows(
                days=120,
                progressive=True,
            )
        )
        noisy = analyze_slow_degradation(
            _synthetic_rows(
                days=120,
                progressive=True,
                telemetry_score=75,
            )
        )

        self.assertLess(
            noisy[
                "confidence_score_percent"
            ],
            clean[
                "confidence_score_percent"
            ],
        )
        factors = {
            item["factor"]: item[
                "relative_likelihood_percent"
            ]
            for item in noisy[
                "likely_factors"
            ]
        }
        self.assertGreater(
            factors["telemetry_or_sensor_issue"],
            20.0,
        )

    def test_cause_weights_are_normalized(self):
        result = analyze_slow_degradation(
            _synthetic_rows(
                days=120,
                progressive=True,
            )
        )
        total = sum(
            item[
                "relative_likelihood_percent"
            ]
            for item in result[
                "likely_factors"
            ]
        )
        self.assertAlmostEqual(
            total,
            100.0,
            delta=0.3,
        )

    def test_physical_ageing_claim_is_gated(self):
        result = analyze_slow_degradation(
            _synthetic_rows(
                days=240,
                progressive=True,
            ),
            requested_window_days=366,
        )

        self.assertEqual(
            result[
                "physical_ageing_assessment"
            ],
            "insufficient_span_for_physical_ageing_claim",
        )
        self.assertIsNotNone(
            result[
                "annualized_trend_percent"
            ]
        )


if __name__ == "__main__":
    unittest.main()
