import unittest
from datetime import date

from ai.intent_router import detect_local_intent


TODAY = date(2026, 10, 8)


class LocalIntentRouterTests(unittest.TestCase):
    def route(self, text):
        return detect_local_intent(
            text,
            today=TODAY,
        )

    def test_performance_paraphrases(self):
        samples = [
            "Como foi o desempenho da minha usina hoje?",
            "Minha usina rendeu bem hoje?",
            "Ela está gerando abaixo do normal?",
            "A produção de hoje foi ruim?",
            "O clima prejudicou a geração hoje?",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                result = self.route(sample)
                self.assertIsNotNone(result)
                self.assertEqual(
                    result["intent"],
                    "performance",
                )
                self.assertEqual(
                    result["tool"],
                    "diagnosticar_desempenho_diario",
                )
                self.assertEqual(
                    result["arguments"]["report_date"],
                    "2026-10-08",
                )

    def test_performance_defaults_to_today(self):
        result = self.route(
            "Minha usina está rendendo bem?"
        )
        self.assertEqual(
            result["intent"],
            "performance",
        )
        self.assertEqual(
            result["arguments"]["report_date"],
            "2026-10-08",
        )

    def test_status_paraphrase(self):
        result = self.route(
            "Tá tudo certo com a minha usina?"
        )
        self.assertEqual(result["intent"], "status")
        self.assertEqual(
            result["tool"],
            "consultar_resumo_usina",
        )

    def test_fault_paraphrase(self):
        result = self.route(
            "Tem algum erro ou alarme ativo?"
        )
        self.assertEqual(result["intent"], "faults")

    def test_fault_code_explanation_stays_with_llm(self):
        result = self.route(
            "O que significa o erro 102?"
        )
        self.assertIsNone(result)

    def test_maintenance_query(self):
        result = self.route(
            "Preciso fazer uma revisão na usina?"
        )
        self.assertEqual(
            result["intent"],
            "maintenance",
        )

    def test_maintenance_registration_stays_with_llm(self):
        result = self.route(
            "Registre a manutenção feita hoje"
        )
        self.assertIsNone(result)

    def test_generation_yesterday(self):
        result = self.route(
            "Quanto ela produziu ontem?"
        )
        self.assertEqual(
            result["intent"],
            "generation",
        )
        self.assertEqual(
            result["arguments"],
            {
                "start_date": "2026-10-07",
                "end_date": "2026-10-07",
            },
        )

    def test_generation_current_month(self):
        result = self.route(
            "Quanto gerei neste mês?"
        )
        self.assertEqual(
            result["intent"],
            "generation",
        )
        self.assertEqual(
            result["arguments"],
            {
                "start_date": "2026-10-01",
                "end_date": "2026-10-08",
            },
        )

    def test_weather_impact_question_uses_resilient_tool(self):
        result = self.route(
            "O clima de hoje afetou minha geração?"
        )
        self.assertEqual(
            result["intent"],
            "weather_impact",
        )
        self.assertEqual(
            result["tool"],
            "analisar_impacto_clima",
        )
        self.assertEqual(
            result["arguments"]["report_date"],
            "2026-10-08",
        )

    def test_weather_yesterday(self):
        result = self.route(
            "Como estava o clima ontem?"
        )
        self.assertEqual(result["intent"], "weather")
        self.assertEqual(
            result["arguments"]["report_date"],
            "2026-10-07",
        )

    def test_savings_defaults_to_current_month(self):
        result = self.route(
            "Quanto estou economizando com a usina?"
        )
        self.assertEqual(result["intent"], "savings")
        self.assertEqual(
            result["arguments"]["year_month"],
            "2026-10",
        )


if __name__ == "__main__":
    unittest.main()
