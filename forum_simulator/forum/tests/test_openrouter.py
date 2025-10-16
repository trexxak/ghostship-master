from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase
from django.utils import timezone

from forum import openrouter


class OpenRouterFallbackTests(TestCase):
    def setUp(self) -> None:
        openrouter._offline_until = None
        self._orig_headers = dict(openrouter.HEADERS)
        self._orig_api_key = openrouter.API_KEY
        openrouter.API_KEY = "dummy-key"
        openrouter.HEADERS["Authorization"] = "Bearer test-key"

    def tearDown(self) -> None:
        openrouter._offline_until = None
        openrouter.API_KEY = self._orig_api_key
        openrouter.HEADERS.clear()
        openrouter.HEADERS.update(self._orig_headers)

    @patch("forum.openrouter.requests.post")
    def test_mark_offline_on_404_and_short_circuit(self, mock_post) -> None:
        class DummyResponse:
            status_code = 404

            def raise_for_status(self) -> None:
                raise requests.HTTPError(response=self)

            def json(self) -> dict[str, str]:
                return {"error": "not found"}

        mock_post.return_value = DummyResponse()

        first = openrouter.generate_completion("test prompt")
        self.assertFalse(first["success"])
        self.assertTrue(openrouter._offline_until is not None)
        first_call_count = mock_post.call_count
        self.assertEqual(first_call_count, 1)

        with patch("forum.openrouter.requests.post") as second_mock:
            second = openrouter.generate_completion("second prompt")
            self.assertFalse(second["success"])
            second_mock.assert_not_called()

    def test_offline_window_expires(self) -> None:
        openrouter._offline_until = timezone.now() - timedelta(seconds=1)
        with patch("forum.openrouter.requests.post") as mock_post:
            mock_post.return_value = self._successful_response()
            result = openrouter.generate_completion("prompt")
        self.assertTrue(result["success"])
        self.assertEqual(mock_post.call_count, 1)

    def _successful_response(self):
        class DummyResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, list[dict[str, dict[str, str]]]]:
                return {"choices": [{"message": {"content": "ok"}}]}

        return DummyResponse()
