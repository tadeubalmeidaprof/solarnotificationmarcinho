import unittest
from unittest.mock import patch

from bot import (
    HELP_BUTTON_ID,
    HELP_MESSAGE,
    MENU_BUTTONS,
    MENU_MESSAGE,
    MONTH_BUTTON_ID,
    TODAY_BUTTON_ID,
    build_reply,
    is_menu_request,
)


class BotTests(unittest.TestCase):
    def test_greeting_opens_menu(self):
        self.assertTrue(is_menu_request("Olá!"))
        self.assertEqual(build_reply("Olá!"), MENU_MESSAGE)

    @patch("bot.get_generation_summary")
    def test_option_one_returns_today_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply("1")

        self.assertIn("18,42 kWh hoje", reply)


    def test_menu_introduces_free_form_questions(self):
        self.assertIn(
            "conversar comigo normalmente",
            MENU_MESSAGE,
        )
        self.assertIn(
            "Como está minha usina?",
            MENU_MESSAGE,
        )
        self.assertEqual(len(MENU_BUTTONS), 3)

    @patch("bot.ask_solcare_ai")
    def test_help_button_returns_examples_without_ai(self, ask_ai):
        reply = build_reply(HELP_BUTTON_ID)

        self.assertEqual(reply, HELP_MESSAGE)
        self.assertIn("Quanto gerei hoje?", reply)
        self.assertIn(
            "Minha usina está perdendo rendimento?",
            reply,
        )
        self.assertIn("Tem alguma falha ativa?", reply)
        ask_ai.assert_not_called()

    @patch("bot.ask_solcare_ai")
    def test_help_text_alias_returns_examples_without_ai(self, ask_ai):
        reply = build_reply("O que posso perguntar?")

        self.assertEqual(reply, HELP_MESSAGE)
        ask_ai.assert_not_called()

    @patch("bot.get_generation_summary")
    def test_today_button_returns_today_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply(TODAY_BUTTON_ID)

        self.assertIn("18,42 kWh hoje", reply)

    @patch("bot.get_generation_summary")
    def test_option_two_returns_month_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply("2")

        self.assertIn("312,70 kWh neste mês", reply)

    @patch("bot.get_generation_summary")
    def test_month_button_returns_month_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply(MONTH_BUTTON_ID)

        self.assertIn("312,70 kWh neste mês", reply)


if __name__ == "__main__":
    unittest.main()
