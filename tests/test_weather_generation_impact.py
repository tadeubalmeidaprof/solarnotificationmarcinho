import unittest
from datetime import date, timedelta
from unittest.mock import patch

from solar_queries import get_weather_generation_impact


class WeatherGenerationImpactTests(unittest.TestCase):
    @patch("solar_queries._station_id", return_value="station")
    @patch("solar_queries.fetch_daily_generation_range")
    @patch("solar_queries.get_generation_period")
    @patch("solar_queries.get_weather_window_summary")
    def test_unfavorable_weather_with_drop_is_likely_impact(
        self,
        weather,
        generation,
        history,
        station_id,
    ):
        weather.return_value = {
            "available": True,
            "date": "2026-10-08",
            "window_label": "07:00-17:00",
            "average_cloud_cover_percent": 82.0,
            "total_precipitation_mm": 4.2,
            "sunshine_hours": 2.4,
            "solar_radiation_wh_m2": 2400.0,
            "average_temperature_c": 25.0,
            "conditions": ["Nublado"],
            "source": "open_meteo",
        }
        generation.return_value = {
            "days_with_data": 1,
            "total_generation_kwh": 18.0,
        }

        start = date(2026, 9, 30)
        history.return_value = [
            {
                "report_date": start + timedelta(days=index),
                "generation_day_kwh": value,
            }
            for index, value in enumerate(
                [24.5, 25.0, 25.2, 24.8, 25.4, 25.1, 24.9, 25.3]
            )
        ]

        result = get_weather_generation_impact(
            "2026-10-08"
        )

        self.assertTrue(result["available"])
        self.assertEqual(
            result["status"],
            "weather_likely_affected",
        )
        self.assertGreater(
            result["generation_drop_percent"],
            20,
        )
        self.assertEqual(
            result["weather_context"],
            "unfavorable",
        )

    @patch("solar_queries._station_id", return_value="station")
    @patch("solar_queries.fetch_daily_generation_range")
    @patch("solar_queries.get_generation_period")
    @patch("solar_queries.get_weather_window_summary")
    def test_favorable_weather_does_not_explain_large_drop(
        self,
        weather,
        generation,
        history,
        station_id,
    ):
        weather.return_value = {
            "available": True,
            "date": "2026-10-08",
            "window_label": "07:00-17:00",
            "average_cloud_cover_percent": 20.0,
            "total_precipitation_mm": 0.0,
            "sunshine_hours": 8.0,
            "solar_radiation_wh_m2": 5200.0,
            "average_temperature_c": 28.0,
            "conditions": ["Céu limpo"],
            "source": "open_meteo",
        }
        generation.return_value = {
            "days_with_data": 1,
            "total_generation_kwh": 17.0,
        }
        history.return_value = [
            {
                "report_date": date(2026, 10, 1)
                + timedelta(days=index),
                "generation_day_kwh": 25.0,
            }
            for index in range(7)
        ]

        result = get_weather_generation_impact(
            "2026-10-08"
        )

        self.assertEqual(
            result["status"],
            "weather_unlikely_to_explain",
        )
        self.assertEqual(
            result["weather_context"],
            "favorable",
        )

    @patch("solar_queries._station_id", return_value="station")
    @patch("solar_queries.fetch_daily_generation_range", return_value=[])
    @patch("solar_queries.get_generation_period")
    @patch("solar_queries.get_weather_window_summary")
    def test_weather_context_survives_without_generation_baseline(
        self,
        weather,
        generation,
        history,
        station_id,
    ):
        weather.return_value = {
            "available": True,
            "date": "2026-10-08",
            "window_label": "07:00-17:00",
            "average_cloud_cover_percent": 85.0,
            "total_precipitation_mm": 1.0,
            "sunshine_hours": 2.0,
            "source": "open_meteo",
        }
        generation.return_value = {
            "days_with_data": 0,
            "total_generation_kwh": 0,
        }

        result = get_weather_generation_impact(
            "2026-10-08"
        )

        self.assertEqual(
            result["status"],
            "weather_context_only",
        )
        self.assertEqual(
            result["confidence"],
            "low",
        )


if __name__ == "__main__":
    unittest.main()
