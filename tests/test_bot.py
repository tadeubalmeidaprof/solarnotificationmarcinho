import os
import unittest
from unittest.mock import patch

from bot import MENU_MESSAGE, build_reply


class BotTests(unittest.TestCase):
    def test_greeting_opens_menu(self):
        self.assertEqual(build_reply("Olá!"), MENU_MESSAGE)

    @patch("bot.get_generation_summary")
    def test_option_one_returns_today_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply("1")

        self.assertIn("18,42 kWh hoje", reply)

    @patch("bot.get_generation_summary")
    def test_option_two_returns_month_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,42",
            "month_kwh": "312,70",
        }

        reply = build_reply("2")

        self.assertIn("312,70 kWh neste mês", reply)


if __name__ == "__main__":
    unittest.main()
