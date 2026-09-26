import unittest
from unittest.mock import patch

from auto_trader.news import NewsFeedError, NewsService


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>삼성전자, AI 반도체 투자 확대 - 매일경제</title><link>https://news.google.com/rss/articles/one</link><pubDate>Fri, 25 Sep 2026 01:00:00 GMT</pubDate><source>매일경제</source></item>
  <item><title>삼성전자 AI 반도체 공급망 강화 - 한국경제</title><link>https://news.google.com/rss/articles/two</link><pubDate>Fri, 25 Sep 2026 02:00:00 GMT</pubDate><source>한국경제</source></item>
  <item><title>원달러 환율 급등에 수입기업 부담 확대 - 연합뉴스</title><link>https://news.google.com/rss/articles/three</link><pubDate>Fri, 25 Sep 2026 03:00:00 GMT</pubDate><source>연합뉴스</source></item>
</channel></rss>""".encode()


class FakeResponse:
    def __init__(self, payload=RSS):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class NewsServiceTests(unittest.TestCase):
    def test_feed_is_parsed_summarized_and_cached(self):
        service = NewsService(cache_seconds=60)
        with patch("auto_trader.news.urlopen", return_value=FakeResponse()) as mocked:
            first = service.search("삼성전자", limit=8)
            second = service.search("삼성전자", limit=8)
        self.assertIs(first, second)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first.article_count, 3)
        self.assertEqual(first.articles[0].title, "삼성전자, AI 반도체 투자 확대")
        self.assertEqual(first.issue_count, 2)
        grouped = next(issue for issue in first.issues if issue.article_count == 2)
        self.assertEqual(grouped.source_count, 2)
        self.assertEqual(grouped.articles[0].source, "한국경제")
        self.assertEqual(first.issues[0], grouped)
        self.assertIn("ai", first.key_topics)
        self.assertIn("반도체", first.key_topics)
        self.assertIn("2개 이슈", first.overview)
        self.assertIn("3개 매체", first.overview)

    def test_weak_words_and_aliases_are_normalized(self):
        service = NewsService()
        self.assertEqual(service._normalize_word("삼전"), "삼성전자")
        tokens = service._title_tokens("삼전 30만원 간다, 반도체 전망은 다시 주목")
        self.assertIn("삼성전자", tokens)
        self.assertIn("반도체", tokens)
        self.assertNotIn("간다", tokens)
        self.assertNotIn("전망", tokens)
        self.assertNotIn("주목", tokens)
        self.assertNotIn("30만원", tokens)

    def test_company_and_topic_pair_is_enough_to_group_an_issue(self):
        service = NewsService()
        left = service._title_tokens("삼성전자 특별성과급 보상 발표")
        right = service._title_tokens("삼전 성과급에 파격 보상")
        self.assertTrue(service._same_issue(left, right))

    def test_invalid_xml_has_readable_error(self):
        service = NewsService()
        with patch("auto_trader.news.urlopen", return_value=FakeResponse(b"not xml")):
            with self.assertRaisesRegex(NewsFeedError, "피드 형식"):
                service.search("반도체")

    def test_empty_query_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "검색어"):
            NewsService().search("   ")

if __name__ == "__main__":
    unittest.main()
