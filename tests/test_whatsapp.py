import unittest
from unittest.mock import Mock, patch

from whatsapp import (
    WhatsAppDeliveryError,
    check_whapi_connection,
    send_interactive_message,
    send_message,
)


class SendMessageTests(unittest.TestCase):
    @patch("whatsapp.requests.post")
    def test_sends_text_with_whapi_contract(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "sent_message": {"id": "message-123"}
        }
        post.return_value = response

        result = send_message(
            "Teste",
            token="token-test",
            chat_id="recipient-test",
        )

        self.assertEqual(result["sent_message"]["id"], "message-123")
        post.assert_called_once_with(
            "https://gate.whapi.cloud/messages/text",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer token-test",
                "Content-Type": "application/json",
            },
            json={
                "to": "recipient-test",
                "body": "Teste",
            },
            timeout=30,
        )

    @patch("whatsapp.requests.post")
    def test_sends_interactive_quick_reply_buttons(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "sent_message": {"id": "interactive-123"}
        }
        post.return_value = response

        result = send_interactive_message(
            "Selecione uma opção:",
            buttons=[
                {
                    "id": "generation_today",
                    "title": "☀️ Geração de hoje",
                },
                {
                    "id": "generation_month",
                    "title": "📊 Geração do mês",
                },
            ],
            footer="Digite menu para voltar.",
            token="token-test",
            chat_id="recipient-test",
        )

        self.assertEqual(result["sent_message"]["id"], "interactive-123")
        post.assert_called_once_with(
            "https://gate.whapi.cloud/messages/interactive",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer token-test",
                "Content-Type": "application/json",
            },
            json={
                "to": "recipient-test",
                "type": "button",
                "body": {"text": "Selecione uma opção:"},
                "action": {
                    "buttons": [
                        {
                            "type": "quick_reply",
                            "title": "☀️ Geração de hoje",
                            "id": "generation_today",
                        },
                        {
                            "type": "quick_reply",
                            "title": "📊 Geração do mês",
                            "id": "generation_month",
                        },
                    ]
                },
                "footer": {"text": "Digite menu para voltar."},
            },
            timeout=30,
        )

    def test_rejects_more_than_three_interactive_buttons(self):
        buttons = [
            {"id": str(index), "title": f"Botão {index}"}
            for index in range(4)
        ]

        with self.assertRaises(WhatsAppDeliveryError):
            send_interactive_message(
                "Teste",
                buttons=buttons,
                token="token-test",
                chat_id="recipient-test",
            )

    @patch("whatsapp.requests.post")
    def test_rejects_response_without_sent_message_id(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"sent_message": {}}
        post.return_value = response

        with self.assertRaises(WhatsAppDeliveryError):
            send_message(
                "Teste",
                token="token-test",
                chat_id="recipient-test",
            )

    def test_requires_token_and_chat_id(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(WhatsAppDeliveryError):
                send_message("Teste")

    @patch("whatsapp.requests.post")
    @patch("whatsapp.requests.head")
    @patch("whatsapp.requests.get")
    def test_health_check_does_not_send_message(
        self,
        get,
        head,
        post,
    ):
        health = Mock()
        health.status_code = 200
        health.ok = True
        get.return_value = health

        contact = Mock()
        contact.status_code = 200
        head.return_value = contact

        result = check_whapi_connection(
            token="token-test",
            chat_id="5511999999999@s.whatsapp.net",
        )

        self.assertTrue(result["api_reachable"])
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["channel_ok"])
        self.assertTrue(result["chat_exists"])
        self.assertFalse(result["message_sent"])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
