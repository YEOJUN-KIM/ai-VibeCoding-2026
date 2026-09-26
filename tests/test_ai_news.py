import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from auto_trader.ai_news import AiNewsBriefingService
from auto_trader.models import NewsArticle, NewsDigest


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def digest():
    return NewsDigest(
        query="삼성전자",
        generated_at=datetime.now(timezone.utc),
        source_name="test",
        overview="기사 2건",
        key_topics=["반도체"],
        article_count=2,
        articles=[
            NewsArticle(title="AI 반도체 투자 확대", url="https://example.com/1",
                        source="매체A", published_at=datetime.now(timezone.utc)),
            NewsArticle(title="공급망 위험 점검", url="https://example.com/2",
                        source="매체B", published_at=datetime.now(timezone.utc)),
        ],
    )


class AiNewsBriefingTests(unittest.TestCase):
    def test_unconfigured_service_keeps_headline_digest(self):
        result = AiNewsBriefingService().enrich(digest())
        self.assertEqual(result.ai_status, "not_configured")
        self.assertIsNone(result.ai_briefing)

    def test_disabled_service_does_not_call_api_even_with_key(self):
        service = AiNewsBriefingService(api_key="test-key", enabled=False)
        with patch("auto_trader.ai_news.urlopen") as mocked:
            result = service.enrich(digest())
        self.assertEqual(result.ai_status, "not_configured")
        mocked.assert_not_called()

    @patch("auto_trader.ai_news.urlopen")
    def test_structured_response_is_parsed_and_cached(self, mocked):
        briefing = {
            "summary": "반도체 투자 확대와 공급망 위험이 함께 보도됐습니다.",
            "key_points": ["AI 반도체 투자 확대"],
            "opportunity_factors": ["투자 확대"],
            "risk_factors": ["공급망 위험"],
            "related_entities": ["삼성전자"],
        }
        mocked.return_value = FakeResponse({
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(briefing, ensure_ascii=False)}
            ]}]
        })
        service = AiNewsBriefingService(api_key="test-key", model="test-model")
        first = service.enrich(digest())
        second = service.enrich(digest())
        self.assertEqual(first.ai_status, "ready")
        self.assertEqual(first.ai_briefing.summary, briefing["summary"])
        self.assertEqual(second.ai_briefing, first.ai_briefing)
        self.assertEqual(mocked.call_count, 1)
        request = mocked.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertNotIn("test-key", request.data.decode())

    @patch("auto_trader.ai_news.urlopen", side_effect=OSError("offline"))
    def test_api_failure_falls_back_without_breaking_news(self, _):
        result = AiNewsBriefingService(api_key="test-key").enrich(digest())
        self.assertEqual(result.ai_status, "unavailable")
        self.assertIsNone(result.ai_briefing)


if __name__ == "__main__":
    unittest.main()
