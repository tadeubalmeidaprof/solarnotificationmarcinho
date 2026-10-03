import unittest
from unittest.mock import Mock, patch

from whatsapp import WhatsAppDeliveryError, send_message


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


if __name__ == "__main__":
    unittest.main()
