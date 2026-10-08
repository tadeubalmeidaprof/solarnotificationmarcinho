import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import solar_queries
from solar_queries import (
    _capacity_kwp,
    _summarize_power_curve,
    compare_months,
    get_generation_period,
    get_monthly_generation,
    get_performance_diagnostic,
)


class SolarQueriesSolisTests(unittest.TestCase):
    def test_capacity_converts_units(self):
        self.assertEqual(
            _capacity_kwp({"capacity": 8.8, "capacityStr": "kWp"}),
            8.8,
        )
        self.assertEqual(
            _capacity_kwp({"capacity": 8800, "capacityStr": "W"}),
            8.8,
        )

    def test_summarizes_solis_curve(self):
        station = {
            "capacity": 8,
            "capacityStr": "kW",
            "powerStr": "W",
        }
        rows = [
            {"time": "2026-10-07 10:00", "power": 1000, "totalR": 300},
            {"time": "2026-10-07 10:05", "power": 4000, "totalR": 600},
            {"time": "2026-10-07 10:10", "power": 8000, "totalR": 800},
        ]
        result = _summarize_power_curve(
            rows,
            station,
            date(2026, 10, 7),
            7,
            17,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["plant_peak_power_kwp"], 8.0)
        self.assertGreater(result["active_generation_hours"], 0)
        self.assertGreater(result["equivalent_full_power_hours"], 0)
        self.assertGreater(result["solar_radiation_wh_m2"], 0)

    @patch("solar_queries._live_station")
    @patch("solar_queries._solis_generation_history")
    @patch("solar_queries.fetch_daily_generation_range")
    @patch("solar_queries._station_id", return_value="station-1")
    def test_generation_period_uses_solis_history(
        self,
        station_id,
        db_rows,
        history,
        live,
    ):
        db_rows.return_value = []
        history.return_value = [
            {
                "date": "2026-10-05",
                "generation_kwh": 10.0,
                "source": "solis_station_month",
            },
            {
                "date": "2026-10-06",
                "generation_kwh": 11.0,
                "source": "solis_station_month",
            },
        ]

        with patch(
            "solar_queries.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(
                2026, 10, 7, 18, 0,
                tzinfo=solar_queries.REPORT_TIMEZONE,
            )
            result = get_generation_period(
                "2026-10-05",
                "2026-10-06",
            )

        self.assertTrue(result["complete_history"])
        self.assertEqual(result["total_generation_kwh"], 21.0)
        live.assert_not_called()

    @patch("solar_queries._live_station")
    @patch("solar_queries.fetch_generation_for_month")
    @patch("solar_queries._station_id", return_value="station-1")
    def test_current_month_falls_back_to_snapshot(
        self,
        station_id,
        snapshot,
        live,
    ):
        snapshot.return_value = (
            "station-1",
            Decimal("248.4"),
        )
        live.side_effect = RuntimeError("Solis indisponível")

        with patch(
            "solar_queries.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(
                2026, 10, 7, 18, 0,
                tzinfo=solar_queries.REPORT_TIMEZONE,
            )
            result = get_monthly_generation("2026-10")

        self.assertTrue(result["available"])
        self.assertEqual(result["generation_kwh"], 248.4)
        self.assertEqual(
            result["source"],
            "monthly_snapshot_fallback",
        )

    @patch("solar_queries.get_generation_period")
    @patch("solar_queries.get_monthly_generation")
    def test_compare_current_month_uses_equivalent_days(
        self,
        monthly,
        period,
    ):
        monthly.side_effect = [
            {
                "available": True,
                "year_month": "2026-09",
                "generation_kwh": 1000,
            },
            {
                "available": True,
                "year_month": "2026-10",
                "generation_kwh": 250,
            },
        ]
        period.side_effect = [
            {
                "complete_history": True,
                "total_generation_kwh": 230,
            },
            {
                "complete_history": True,
                "total_generation_kwh": 250,
            },
        ]

        with patch(
            "solar_queries.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(
                2026, 10, 7, 19, 0,
                tzinfo=solar_queries.REPORT_TIMEZONE,
            )
            result = compare_months(
                "2026-09",
                "2026-10",
            )

        self.assertFalse(
            result["months_are_directly_comparable"]
        )
        self.assertTrue(
            result["fair_comparison_available"]
        )
        self.assertEqual(
            result["fair_comparison"]["through_day"],
            7,
        )

    @patch("solar_queries._station_day_rows")
    def test_diagnostic_waits_until_window_finishes(
        self,
        station_day,
    ):
        with patch(
            "solar_queries.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(
                2026, 10, 8, 14, 0,
                tzinfo=solar_queries.REPORT_TIMEZONE,
            )
            result = get_performance_diagnostic(
                "2026-10-08",
                start_hour=7,
                end_hour=17,
            )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["status"],
            "inconclusive_window_in_progress",
        )
        station_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
