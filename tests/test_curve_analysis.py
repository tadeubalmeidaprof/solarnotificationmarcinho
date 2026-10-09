import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from curve_analysis import analyze_power_curve


TZ = ZoneInfo("America/Bahia")


def make_curve(
    *,
    peak_w=8000,
    low_midday=False,
    suppressed=False,
):
    base = datetime(2026, 10, 7, 7, 0, tzinfo=TZ)
    points = []
    for index in range(121):
        ts = base + timedelta(minutes=5 * index)
        hour_from_start = index / 12
        shape = max(0.0, 1 - abs(hour_from_start - 5) / 5)
        power = peak_w * 0.85 * shape
        if suppressed:
            power *= 0.55
        if low_midday and 11 <= ts.hour < 12:
            power = 0
        points.append({"timestamp": ts, "power_w": power})
    return points


class CurveAnalysisTests(unittest.TestCase):
    def test_normal_curve_is_not_flagged(self):
        history = [make_curve() for _ in range(4)]
        result = analyze_power_curve(
            points=make_curve(),
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 20,
                "total_precipitation_mm": 0,
            },
        )
        self.assertEqual(result["status"], "normal")

    def test_midday_interruption_is_detected(self):
        result = analyze_power_curve(
            points=make_curve(low_midday=True),
            peak_power_kwp=8,
            historical_profiles=[make_curve() for _ in range(4)],
            weather={
                "average_cloud_cover_percent": 20,
                "total_precipitation_mm": 0,
            },
        )
        types = {item["type"] for item in result["anomalies"]}
        self.assertIn("midday_interruption", types)
        self.assertIn(result["status"], {"attention", "anomaly_detected"})

    def test_suppressed_peak_needs_favorable_weather(self):
        history = [make_curve() for _ in range(4)]
        favorable = analyze_power_curve(
            points=make_curve(suppressed=True),
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 15,
                "total_precipitation_mm": 0,
            },
        )
        bad_weather = analyze_power_curve(
            points=make_curve(suppressed=True),
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 90,
                "total_precipitation_mm": 8,
            },
        )

        favorable_types = {
            item["type"] for item in favorable["anomalies"]
        }
        bad_types = {
            item["type"] for item in bad_weather["anomalies"]
        }
        self.assertIn("suppressed_peak", favorable_types)
        self.assertNotIn("suppressed_peak", bad_types)

    def test_partial_day_does_not_flag_early_end_or_suppressed_peak(self):
        history = [make_curve() for _ in range(4)]
        partial = make_curve(suppressed=True)[:70]
        result = analyze_power_curve(
            points=partial,
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 20,
                "total_precipitation_mm": 0,
            },
            complete_day=False,
        )
        types = {item["type"] for item in result["anomalies"]}
        self.assertNotIn("early_generation_end", types)
        self.assertNotIn("suppressed_peak", types)


    def test_baseline_prioritizes_climatically_similar_days(self):
        current = make_curve()
        history = []

        for index in range(5):
            points = [
                {
                    **point,
                    "timestamp": point["timestamp"] - timedelta(days=index + 1),
                }
                for point in make_curve()
            ]
            history.append(
                {
                    "date": points[0]["timestamp"].date(),
                    "points": points,
                    "weather": {
                        "average_cloud_cover_percent": 20 + index,
                        "total_precipitation_mm": 0,
                        "average_temperature_c": 30,
                    },
                }
            )

        for index in range(3):
            points = [
                {
                    **point,
                    "timestamp": point["timestamp"] - timedelta(days=index + 7),
                }
                for point in make_curve(suppressed=True)
            ]
            history.append(
                {
                    "date": points[0]["timestamp"].date(),
                    "points": points,
                    "weather": {
                        "average_cloud_cover_percent": 95,
                        "total_precipitation_mm": 10,
                        "average_temperature_c": 22,
                    },
                }
            )

        result = analyze_power_curve(
            points=current,
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 20,
                "total_precipitation_mm": 0,
                "average_temperature_c": 30,
            },
        )

        self.assertEqual(result["status"], "normal")
        self.assertGreaterEqual(result["baseline_days_used"], 5)
        self.assertTrue(
            all(
                item["weather_similarity"] >= 0.35
                for item in result["selected_history"]
            )
        )

    def test_persistence_uses_previous_anomaly_scores(self):
        history = [
            [
                {
                    **point,
                    "timestamp": point["timestamp"] - timedelta(days=index + 1),
                }
                for point in make_curve()
            ]
            for index in range(6)
        ]

        result = analyze_power_curve(
            points=make_curve(suppressed=True),
            peak_power_kwp=8,
            historical_profiles=history,
            weather={
                "average_cloud_cover_percent": 15,
                "total_precipitation_mm": 0,
            },
            historical_analysis_scores=[65, 72, 18, 63],
        )

        self.assertTrue(result["persistence"]["persistent"])
        self.assertGreaterEqual(
            result["persistence"]["persistent_days"],
            3,
        )
        self.assertGreaterEqual(result["anomaly_score"], 41)


if __name__ == "__main__":
    unittest.main()
