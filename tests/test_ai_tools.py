import unittest
from unittest.mock import patch

from ai.tools import AIToolError, TOOL_DEFINITIONS, execute_tool


class AIToolsTests(unittest.TestCase):
    def test_all_tool_schemas_reject_additional_properties(self):
        for tool in TOOL_DEFINITIONS:
            self.assertFalse(
                tool["function"]["parameters"]["additionalProperties"]
            )

    @patch("ai.tools.get_plant_status")
    def test_executes_status_tool(self, status):
        status.return_value = {"status": "Normal"}
        self.assertEqual(
            execute_tool("consultar_resumo_usina", {}),
            {"status": "Normal"},
        )

    @patch("ai.tools.get_generation_period")
    def test_executes_generation_period(self, generation):
        generation.return_value = {"total_generation_kwh": 42.0}
        result = execute_tool(
            "consultar_geracao_periodo",
            {
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
            },
        )
        self.assertEqual(result["total_generation_kwh"], 42.0)

    @patch("ai.tools.get_recent_generation")
    def test_executes_recent_days(self, recent):
        recent.return_value = {"days_with_data": 7}
        result = execute_tool(
            "consultar_ultimos_dias",
            {"days": 7},
        )
        self.assertEqual(result["days_with_data"], 7)

    @patch("ai.tools.get_weather_window_summary")
    def test_weather_tool_accepts_window(self, weather):
        weather.return_value = {"available": True}
        execute_tool(
            "consultar_clima",
            {
                "report_date": "2026-10-07",
                "start_hour": 7,
                "end_hour": 17,
            },
        )
        weather.assert_called_once_with(
            report_date="2026-10-07",
            start_hour=7,
            end_hour=17,
        )

    @patch("ai.tools.get_solar_generation_hours")
    def test_solar_hours_tool(self, solar):
        solar.return_value = {"active_generation_hours": 8.0}
        execute_tool(
            "consultar_horas_solares_usina",
            {
                "report_date": "2026-10-07",
                "start_hour": 7,
                "end_hour": 17,
            },
        )
        solar.assert_called_once_with(
            report_date="2026-10-07",
            start_hour=7,
            end_hour=17,
        )

    @patch("ai.tools.get_performance_diagnostic")
    def test_performance_diagnostic_tool(self, diagnostic):
        diagnostic.return_value = {
            "available": True,
            "diagnostic": {"status": "normal"},
        }
        result = execute_tool(
            "diagnosticar_desempenho_diario",
            {
                "report_date": "2026-10-07",
                "start_hour": 7,
                "end_hour": 17,
            },
        )
        self.assertTrue(result["available"])

    def test_rejects_unknown_tool(self):
        with self.assertRaises(AIToolError):
            execute_tool("apagar_banco", {})

    def test_rejects_unexpected_arguments(self):
        with self.assertRaises(AIToolError):
            execute_tool(
                "consultar_resumo_usina",
                {"token": "x"},
            )

    def test_rejects_missing_required_arguments(self):
        with self.assertRaises(AIToolError):
            execute_tool(
                "consultar_geracao_periodo",
                {"start_date": "2026-10-01"},
            )


if __name__ == "__main__":
    unittest.main()
