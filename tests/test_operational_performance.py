import unittest

from maintenance import analyze_operational_performance


def metric(
    date_value: str,
    ratio: float,
    active_hours: float = 6.0,
    peak_kwp: float = 8.0,
) -> dict:
    equivalent_hours = ratio * active_hours
    return {
        "date": date_value,
        "active_generation_hours": active_hours,
        "equivalent_full_power_hours": equivalent_hours,
        "average_power_fraction_of_peak": ratio,
        "plant_peak_power_kwp": peak_kwp,
        "estimated_energy_in_window_kwh": (
            peak_kwp * equivalent_hours
        ),
    }


FAVORABLE_WEATHER = {
    "available": True,
    "average_cloud_cover_percent": 30,
    "total_precipitation_mm": 0,
    "average_temperature_c": 28,
    "max_temperature_c": 33,
}


class OperationalPerformanceTests(unittest.TestCase):
    def test_normal_when_current_day_is_close_to_own_history(self):
        history = [
            metric(f"2026-10-0{day}", 0.50)
            for day in range(1, 7)
        ]
        current = metric("2026-10-07", 0.48)

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=FAVORABLE_WEATHER,
        )

        self.assertEqual(result["status"], "normal")
        self.assertFalse(result["maintenance_suspected"])
        self.assertFalse(result["inspection_recommended"])

    def test_single_large_drop_is_attention_not_maintenance(self):
        history = [
            metric(f"2026-10-0{day}", 0.50)
            for day in range(1, 5)
        ]
        current = metric("2026-10-07", 0.34)

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=FAVORABLE_WEATHER,
        )

        self.assertEqual(result["status"], "attention")
        self.assertFalse(result["maintenance_suspected"])
        self.assertEqual(result["persistent_low_days_count"], 1)

    def test_persistent_drop_in_favorable_weather_suspects_maintenance(self):
        history = [
            metric("2026-09-29", 0.50),
            metric("2026-09-30", 0.52),
            metric("2026-10-01", 0.49),
            metric("2026-10-02", 0.51),
            metric("2026-10-03", 0.34),
            metric("2026-10-04", 0.33),
        ]
        current = metric("2026-10-05", 0.32)

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=FAVORABLE_WEATHER,
        )

        self.assertEqual(result["status"], "maintenance_suspected")
        self.assertTrue(result["maintenance_suspected"])
        self.assertTrue(result["inspection_recommended"])
        self.assertGreaterEqual(result["persistent_low_days_count"], 2)
        self.assertGreater(result["drop_percent_vs_baseline"], 25)

    def test_bad_weather_prevents_maintenance_conclusion(self):
        history = [
            metric("2026-09-29", 0.50),
            metric("2026-09-30", 0.52),
            metric("2026-10-01", 0.49),
            metric("2026-10-02", 0.51),
            metric("2026-10-03", 0.30),
            metric("2026-10-04", 0.30),
        ]
        current = metric("2026-10-05", 0.28)
        weather = {
            "average_cloud_cover_percent": 88,
            "total_precipitation_mm": 7,
            "average_temperature_c": 25,
            "max_temperature_c": 28,
        }

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=weather,
        )

        self.assertEqual(
            result["status"],
            "weather_likely_explains_reduction",
        )
        self.assertFalse(result["maintenance_suspected"])

    def test_high_temperature_raises_conservative_threshold(self):
        history = [
            metric("2026-09-29", 0.50),
            metric("2026-09-30", 0.50),
            metric("2026-10-01", 0.50),
            metric("2026-10-02", 0.50),
            metric("2026-10-03", 0.36),
            metric("2026-10-04", 0.36),
        ]
        current = metric("2026-10-05", 0.36)
        weather = {
            "average_cloud_cover_percent": 20,
            "total_precipitation_mm": 0,
            "average_temperature_c": 34,
            "max_temperature_c": 38,
        }

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=weather,
        )

        self.assertEqual(result["temperature_context"], "high")
        self.assertEqual(result["status"], "attention")
        self.assertFalse(result["maintenance_suspected"])

    def test_active_fault_takes_priority_over_maintenance_guess(self):
        history = [
            metric(f"2026-10-0{day}", 0.50)
            for day in range(1, 7)
        ]
        current = metric("2026-10-07", 0.30)

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=FAVORABLE_WEATHER,
            active_fault_count=1,
        )

        self.assertEqual(result["status"], "technical_fault_present")
        self.assertFalse(result["maintenance_suspected"])
        self.assertTrue(result["inspection_recommended"])

    def test_insufficient_history_is_inconclusive(self):
        history = [
            metric("2026-10-01", 0.50),
            metric("2026-10-02", 0.52),
        ]
        current = metric("2026-10-03", 0.30)

        result = analyze_operational_performance(
            current_metric=current,
            historical_metrics=history,
            weather=FAVORABLE_WEATHER,
        )

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["maintenance_suspected"])
        self.assertEqual(result["baseline_days_used"], 2)


if __name__ == "__main__":
    unittest.main()
