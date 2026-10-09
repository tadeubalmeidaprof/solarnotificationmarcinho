import json
import unittest
from unittest.mock import patch

from ai.assistant import ask_solcare_ai
from ai.provider import AIProviderError, AIRateLimitError


AI_ENV = {
    "AI_ENABLED": "true",
    "AI_PROVIDER": "groq",
    "GROQ_API_KEY": "gsk_test",
}


class AIAssistantTests(unittest.TestCase):
    @patch("ai.assistant.create_chat_completion")
    def test_disabled_ai_does_not_call_provider(self, completion):
        with patch.dict("os.environ", {"AI_ENABLED": "false"}, clear=True):
            self.assertIsNone(ask_solcare_ai("Como está minha usina?"))

        completion.assert_not_called()

    @patch("ai.assistant.create_chat_completion")
    def test_returns_direct_answer(self, completion):
        completion.return_value = {
            "role": "assistant",
            "content": "Posso ajudar com o monitoramento solar.",
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai("O que você faz?")

        self.assertEqual(reply, "Posso ajudar com o monitoramento solar.")

    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_executes_tool_and_returns_final_answer(self, completion, execute_tool):
        completion.side_effect = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "consultar_resumo_usina",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "🟢 Sua usina está funcionando normalmente.",
            },
        ]
        execute_tool.return_value = {
            "status": "Normal",
            "power_now_kw": 3.8,
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai("Minha usina está funcionando?")

        self.assertEqual(reply, "🟢 Sua usina está funcionando normalmente.")
        execute_tool.assert_called_once_with("consultar_resumo_usina", {})
        second_messages = completion.call_args_list[1].kwargs["messages"]
        tool_message = second_messages[-1]
        self.assertEqual(tool_message["role"], "tool")
        tool_payload = json.loads(tool_message["content"])
        self.assertTrue(tool_payload["ok"])
        self.assertEqual(tool_payload["data"]["status"], "Normal")

    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_invalid_tool_arguments_do_not_execute_tool(self, completion, execute_tool):
        completion.side_effect = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "consultar_resumo_usina",
                            "arguments": "not-json",
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "Não consegui consultar esse dado agora.",
            },
        ]

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai("Minha usina está funcionando?")

        self.assertEqual(reply, "Não consegui consultar esse dado agora.")
        execute_tool.assert_not_called()


    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_curve_today_uses_local_fallback_without_groq(
        self,
        completion,
        execute_tool,
    ):
        execute_tool.return_value = {
            "date": "2026-10-08",
            "analysis": {
                "available": True,
                "status": "normal",
                "anomaly_score": 5.0,
                "confidence": "high",
                "baseline_days_used": 20,
                "anomalies": [],
                "persistence": {
                    "persistent": False,
                },
            },
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai(
                "A curva de hoje teve alguma anomalia?"
            )

        self.assertIn("dentro do padrão histórico", reply)
        self.assertIn("5.0/100", reply)
        execute_tool.assert_called_once()
        completion.assert_not_called()

    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_rate_limit_after_curve_tool_returns_tool_fallback(
        self,
        completion,
        execute_tool,
    ):
        completion.side_effect = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_curve",
                        "type": "function",
                        "function": {
                            "name": "analisar_curva_geracao",
                            "arguments": (
                                '{"report_date":"2026-10-07"}'
                            ),
                        },
                    }
                ],
            },
            AIRateLimitError("limite"),
        ]
        execute_tool.return_value = {
            "date": "2026-10-07",
            "analysis": {
                "available": True,
                "status": "normal",
                "anomaly_score": 1.6,
                "confidence": "high",
                "baseline_days_used": 20,
                "anomalies": [],
                "persistence": {
                    "persistent": False,
                },
            },
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai(
                "Analise a curva do dia 2026-10-07."
            )

        self.assertIn("dentro do padrão histórico", reply)
        self.assertIn("1.6/100", reply)
        self.assertEqual(completion.call_count, 2)


    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_degradation_question_uses_local_tool_without_groq(
        self,
        completion,
        execute_tool,
    ):
        execute_tool.return_value = {
            "available": True,
            "status": "probable_progressive_loss",
            "degradation_likelihood_percent": 84.7,
            "estimated_recent_loss_percent": 4.3,
            "confidence": "high",
            "observations": 112,
            "date_span_days": 118,
            "dominant_factor": {
                "label": "sujeira/acúmulo sobre os módulos",
                "relative_likelihood_percent": 38.0,
            },
            "physical_ageing_assessment": (
                "insufficient_span_for_physical_ageing_claim"
            ),
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai(
                "Minha usina tem sinais de degradação?"
            )

        self.assertIn(
            "perda operacional progressiva",
            reply,
        )
        self.assertIn("84.7%", reply)
        self.assertIn(
            "não confirma degradação física",
            reply,
        )
        execute_tool.assert_called_once_with(
            "consultar_degradacao_lenta",
            {},
        )
        completion.assert_not_called()

    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_losing_yield_phrase_uses_degradation_local_tool(
        self,
        completion,
        execute_tool,
    ):
        execute_tool.return_value = {
            "available": False,
            "status": "warming_up",
            "observations": 18,
            "date_span_days": 22,
            "minimum_observations": 35,
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai(
                "Minha usina está perdendo rendimento?"
            )

        self.assertIn(
            "histórico suficiente",
            reply,
        )
        self.assertIn(
            "35 observações",
            reply,
        )
        execute_tool.assert_called_once_with(
            "consultar_degradacao_lenta",
            {},
        )
        completion.assert_not_called()

    @patch("ai.assistant.execute_tool")
    @patch("ai.assistant.create_chat_completion")
    def test_rate_limit_after_degradation_tool_returns_tool_fallback(
        self,
        completion,
        execute_tool,
    ):
        completion.side_effect = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_degradation",
                        "type": "function",
                        "function": {
                            "name": "consultar_degradacao_lenta",
                            "arguments": '{"window_days":180}',
                        },
                    }
                ],
            },
            AIRateLimitError("limite"),
        ]
        execute_tool.return_value = {
            "available": True,
            "status": "possible_progressive_loss",
            "degradation_likelihood_percent": 61.0,
            "estimated_recent_loss_percent": 3.2,
            "confidence": "moderate",
            "observations": 75,
            "date_span_days": 82,
            "dominant_factor": {
                "label": "sujeira/acúmulo sobre os módulos",
                "relative_likelihood_percent": 34.0,
            },
            "physical_ageing_assessment": (
                "insufficient_span_for_physical_ageing_claim"
            ),
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai(
                "Avalie a tendência histórica da minha usina."
            )

        self.assertIn(
            "indícios de perda operacional progressiva",
            reply,
        )
        self.assertIn("61.0%", reply)
        self.assertEqual(completion.call_count, 2)

    @patch("ai.assistant.create_chat_completion")
    def test_provider_error_returns_none_for_bot_fallback(self, completion):
        completion.side_effect = AIProviderError("indisponível")

        with patch.dict("os.environ", AI_ENV, clear=True):
            self.assertIsNone(ask_solcare_ai("Como está minha usina?"))

    @patch("ai.assistant.create_chat_completion")
    def test_rate_limit_returns_none_for_bot_fallback(self, completion):
        completion.side_effect = AIRateLimitError("limite")

        with patch.dict("os.environ", AI_ENV, clear=True):
            self.assertIsNone(ask_solcare_ai("Como está minha usina?"))


    @patch("ai.assistant.save_conversation_exchange")
    @patch("ai.assistant.load_recent_messages")
    @patch("ai.assistant.create_chat_completion")
    def test_chat_context_is_loaded_and_saved(
        self,
        completion,
        load_history,
        save_exchange,
    ):
        load_history.return_value = [
            {"role": "user", "content": "Quanto gerei hoje?"},
            {"role": "assistant", "content": "20 kWh."},
        ]
        completion.return_value = {
            "role": "assistant",
            "content": "Ontem foram 18 kWh.",
        }

        with patch.dict("os.environ", AI_ENV, clear=True):
            reply = ask_solcare_ai("E ontem?", chat_id="5511@c.us")

        self.assertEqual(reply, "Ontem foram 18 kWh.")
        load_history.assert_called_once_with("5511@c.us")
        save_exchange.assert_called_once_with(
            chat_id="5511@c.us",
            user_message="E ontem?",
            assistant_message="Ontem foram 18 kWh.",
        )


if __name__ == "__main__":
    unittest.main()
