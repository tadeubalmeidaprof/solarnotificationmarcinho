import unittest

from webhook import extract_text_messages


class WebhookTests(unittest.TestCase):
    def test_extracts_only_incoming_text_messages(self):
        payload = {
            "messages": [
                {
                    "chat_id": "5511999999999@s.whatsapp.net",
                    "from_me": False,
                    "type": "text",
                    "text": {"body": "Olá"},
                },
                {
                    "chat_id": "5511999999999@s.whatsapp.net",
                    "from_me": True,
                    "type": "text",
                    "text": {"body": "Resposta do bot"},
                },
                {
                    "chat_id": "5511999999999@s.whatsapp.net",
                    "from_me": False,
                    "type": "image",
                },
            ]
        }

        self.assertEqual(
            extract_text_messages(payload),
            [("5511999999999@s.whatsapp.net", "Olá")],
        )


if __name__ == "__main__":
    unittest.main()
