import unittest
from unittest.mock import Mock, patch

import requests

from ai.provider import (
    AIProviderError,
    AIRateLimitError,
    create_chat_completion,
)


BASE_ENV = {
    "GROQ_API_KEY": "gsk_test",
    "GROQ_MODEL": "openai/gpt-oss-20b",
    "GROQ_TIMEOUT_SECONDS": "7",
    "GROQ_MAX_COMPLETION_TOKENS": "600",
}


class AIProviderTests(unittest.TestCase):
    @patch("ai.provider.requests.post")
    def test_sends_expected_request_without_leaking_key_in_payload(self, post):
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {
            "choices": [
                {"message": {"role": "assistant", "content": "Tudo normal."}}
            ]
        }
        post.return_value = response

        with patch.dict("os.environ", BASE_ENV, clear=True):
            message = create_chat_completion(
                messages=[{"role": "user", "content": "Como está a usina?"}],
                tools=[],
            )

        self.assertEqual(message["content"], "Tudo normal.")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["timeout"], 7.0)
        self.assertEqual(kwargs["json"]["model"], "openai/gpt-oss-20b")
        self.assertEqual(kwargs["json"]["max_completion_tokens"], 600)
        self.assertEqual(kwargs["json"]["reasoning_effort"], "low")
        self.assertFalse(kwargs["json"]["parallel_tool_calls"])
        self.assertNotIn("gsk_test", str(kwargs["json"]))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer gsk_test")

    @patch("ai.provider.requests.post")
    def test_timeout_becomes_controlled_provider_error(self, post):
        post.side_effect = requests.Timeout()

        with patch.dict("os.environ", BASE_ENV, clear=True):
            with self.assertRaises(AIProviderError):
                create_chat_completion([], [])

    @patch("ai.provider.requests.post")
    def test_rate_limit_has_specific_error(self, post):
        post.return_value = Mock(status_code=429, ok=False)

        with patch.dict("os.environ", BASE_ENV, clear=True):
            with self.assertRaises(AIRateLimitError):
                create_chat_completion([], [])

    @patch("ai.provider.requests.post")
    def test_invalid_json_is_rejected(self, post):
        response = Mock(status_code=200, ok=True)
        response.json.side_effect = ValueError("invalid")
        post.return_value = response

        with patch.dict("os.environ", BASE_ENV, clear=True):
            with self.assertRaises(AIProviderError):
                create_chat_completion([], [])

    @patch("ai.provider.requests.post")
    def test_missing_api_key_fails_before_network_call(self, post):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(AIProviderError):
                create_chat_completion([], [])

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
