import unittest
from unittest.mock import patch

from whatsapp import WhatsAppDeliveryError
from webhook import extract_incoming_messages, process_message


AUTHORIZED_CHAT_ID = "5511999999999@s.whatsapp.net"


class WebhookTests(unittest.TestCase):
    def test_extracts_incoming_text_and_button_reply_messages(self):
        payload = {
            "messages": [
                {
                    "chat_id": AUTHORIZED_CHAT_ID,
                    "from_me": False,
                    "type": "text",
                    "text": {"body": "Olá"},
                },
                {
                    "chat_id": AUTHORIZED_CHAT_ID,
                    "from_me": False,
                    "type": "reply",
                    "reply": {
                        "type": "buttons_reply",
                        "buttons_reply": {
                            "id": "ButtonsV3:generation_today",
                            "title": "☀️ Geração de hoje",
                        },
                    },
                },
                {
                    "chat_id": AUTHORIZED_CHAT_ID,
                    "from_me": True,
                    "type": "text",
                    "text": {"body": "Resposta do bot"},
                },
                {
                    "chat_id": AUTHORIZED_CHAT_ID,
                    "from_me": False,
                    "type": "image",
                },
            ]
        }

        self.assertEqual(
            extract_incoming_messages(payload),
            [
                (AUTHORIZED_CHAT_ID, "Olá"),
                (AUTHORIZED_CHAT_ID, "generation_today"),
            ],
        )

    def test_ignores_reply_types_other_than_buttons(self):
        payload = {
            "messages": [
                {
                    "chat_id": AUTHORIZED_CHAT_ID,
                    "from_me": False,
                    "type": "reply",
                    "reply": {
                        "type": "list_reply",
                        "list_reply": {"id": "unexpected"},
                    },
                }
            ]
        }

        self.assertEqual(extract_incoming_messages(payload), [])

    @patch("webhook.send_message")
    @patch("webhook.send_interactive_message")
    def test_greeting_sends_interactive_menu(
        self,
        send_interactive,
        send_text,
    ):
        with patch.dict(
            "os.environ",
            {"AUTHORIZED_CHAT_ID": AUTHORIZED_CHAT_ID},
            clear=True,
        ):
            process_message(AUTHORIZED_CHAT_ID, "Olá!")

        send_interactive.assert_called_once()
        send_text.assert_not_called()

    @patch("webhook.send_message")
    @patch("webhook.send_interactive_message")
    def test_interactive_menu_falls_back_to_text(
        self,
        send_interactive,
        send_text,
    ):
        send_interactive.side_effect = WhatsAppDeliveryError(
            "Falha simulada."
        )

        with patch.dict(
            "os.environ",
            {"AUTHORIZED_CHAT_ID": AUTHORIZED_CHAT_ID},
            clear=True,
        ):
            process_message(AUTHORIZED_CHAT_ID, "menu")

        send_interactive.assert_called_once()
        send_text.assert_called_once()

    @patch("webhook.send_message")
    @patch("webhook.send_interactive_message")
    def test_unauthorized_chat_is_ignored(
        self,
        send_interactive,
        send_text,
    ):
        with patch.dict(
            "os.environ",
            {"AUTHORIZED_CHAT_ID": AUTHORIZED_CHAT_ID},
            clear=True,
        ):
            process_message("5500000000000@s.whatsapp.net", "Olá")

        send_interactive.assert_not_called()
        send_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
