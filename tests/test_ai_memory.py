import unittest
from unittest.mock import patch

from ai.memory import load_recent_messages, save_conversation_exchange


MEMORY_ENV = {"AI_MEMORY_ENABLED": "true"}


class AIMemoryTests(unittest.TestCase):
    @patch("ai.memory.fetch_ai_conversation_messages")
    def test_loads_recent_messages_without_exposing_chat_id(self, fetch_messages):
        fetch_messages.return_value = [
            {"role": "user", "content": "Quanto gerei?"},
            {"role": "assistant", "content": "20 kWh."},
        ]

        with patch.dict("os.environ", MEMORY_ENV, clear=True):
            result = load_recent_messages("5511999999999@c.us")

        self.assertEqual(len(result), 2)
        conversation_key = fetch_messages.call_args.kwargs["conversation_key"]
        self.assertEqual(len(conversation_key), 64)
        self.assertNotIn("5511999999999", conversation_key)

    @patch("ai.memory.prune_ai_conversation_messages")
    @patch("ai.memory.save_ai_conversation_message")
    def test_saves_exchange_and_prunes_history(self, save_message, prune):
        with patch.dict("os.environ", MEMORY_ENV, clear=True):
            save_conversation_exchange(
                "5511999999999@c.us",
                "E ontem?",
                "Ontem foram 18 kWh.",
            )

        self.assertEqual(save_message.call_count, 2)
        prune.assert_called_once()

    @patch("ai.memory.fetch_ai_conversation_messages")
    def test_disabled_memory_does_not_query_database(self, fetch_messages):
        with patch.dict(
            "os.environ",
            {"AI_MEMORY_ENABLED": "false"},
            clear=True,
        ):
            self.assertEqual(
                load_recent_messages("5511999999999@c.us"),
                [],
            )

        fetch_messages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
