"""공개 RSS에서 국내 증시 뉴스를 읽고 짧은 보도 흐름을 만든다."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import NewsArticle, NewsDigest, NewsIssue


class NewsFeedError(RuntimeError):
    """뉴스 피드를 불러오거나 해석하지 못했을 때 발생한다."""


class NewsService:
    feed_url = "https://news.google.com/rss/search"
    source_name = "Google 뉴스 RSS"
    _stop_words = {
        "관련", "대한", "통해", "위한", "최근", "오늘", "내일", "국내", "증시", "주식",
        "시장", "기업", "투자", "전망", "기자", "뉴스", "단독", "종합", "속보", "코스피",
        "코스닥", "상승", "하락", "급등", "급락", "올해", "이번", "지난", "에서", "으로",
        "간다", "나왔다", "나온다", "있다", "없다", "한다", "될까", "왜", "공개", "주목",
        "기대", "우려", "분석", "가능성", "본격", "최대", "최고", "최저", "돌파", "소식",
        "대해서", "가운데", "가장", "새로운", "다시", "계속", "앞두고", "따르면", "대비",
    }
    _aliases = {
        "삼전": "삼성전자",
        "하닉": "sk하이닉스",
        "닉스": "sk하이닉스",
        "에스케이하이닉스": "sk하이닉스",
    }
    _meaningful_number_terms = {"2차전지", "3d", "5g", "6g"}
    _suffixes = ("으로", "에서", "에게", "까지", "부터", "에는", "에도", "보다", "처럼", "은", "는", "이", "가", "을", "를", "의", "와", "과", "에", "도")

    def __init__(self, *, cache_seconds: int = 600, timeout_seconds: float = 5.0):
        self.cache_seconds = cache_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[tuple[str, int], tuple[float, NewsDigest]] = {}
        self._lock = Lock()

    @staticmethod
    def _published_at(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _clean_title(title: str, source: str) -> str:
        clean = " ".join(title.split())
        suffix = f" - {source}"
        return clean[:-len(suffix)].strip() if source and clean.endswith(suffix) else clean

    def _normalize_word(self, word: str) -> str:
        normalized = word.casefold()
        for suffix in self._suffixes:
            if normalized.endswith(suffix) and len(normalized) >= len(suffix) + 2:
                normalized = normalized[:-len(suffix)]
                break
        return self._aliases.get(normalized, normalized)

    def _title_tokens(self, title: str) -> set[str]:
        tokens: set[str] = set()
        for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", title):
            normalized = self._normalize_word(word)
            if (normalized in self._stop_words or normalized == "vs"
                    or (any(character.isdigit() for character in normalized)
                        and normalized not in self._meaningful_number_terms)
                    or (normalized.isascii() and len(normalized) < 3 and normalized != "ai")):
                continue
            tokens.add(normalized)
        return tokens

    def _topics(self, query: str, articles: list[NewsArticle]) -> list[str]:
        query_words = {self._normalize_word(word) for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", query)}
        words: Counter[str] = Counter()
        for article in articles:
            words.update(self._title_tokens(article.title) - query_words)
        repeated = [word for word, count in words.most_common(8) if count >= 2]
        return repeated[:5] or [word for word, _ in words.most_common(3)]

    @staticmethod
    def _same_issue(left: set[str], right: set[str]) -> bool:
        if not left or not right:
            return False
        common = len(left & right)
        if common < 2:
            return False
        overlap = common / min(len(left), len(right))
        jaccard = common / len(left | right)
        return overlap >= 0.35 or jaccard >= 0.2

    def _issues(self, articles: list[NewsArticle]) -> list[NewsIssue]:
        clusters: list[tuple[set[str], list[NewsArticle]]] = []
        for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
            tokens = self._title_tokens(article.title)
            matched = next((cluster for cluster in clusters if self._same_issue(tokens, cluster[0])), None)
            if matched is None:
                clusters.append((tokens, [article]))
            else:
                matched[1].append(article)
        issues = [NewsIssue(
            title=cluster_articles[0].title,
            article_count=len(cluster_articles),
            source_count=len({article.source for article in cluster_articles if article.source}) or 1,
            latest_at=max(article.published_at for article in cluster_articles),
            key_topics=self._topics("", cluster_articles),
            articles=cluster_articles,
        ) for _, cluster_articles in clusters]
        return sorted(
            issues,
            key=lambda issue: (issue.article_count, issue.source_count, issue.latest_at),
            reverse=True,
        )

    def _digest(self, query: str, articles: list[NewsArticle]) -> NewsDigest:
        topics = self._topics(query, articles)
        issues = self._issues(articles)
        source_count = len({article.source for article in articles if article.source})
        if articles:
            overview = f"최근 기사 {len(articles)}건을 {len(issues)}개 이슈, {source_count or 1}개 매체로 묶었습니다."
            if topics:
                overview += f" 반복해서 등장한 화제는 {', '.join(topics[:3])}입니다."
        else:
            overview = "검색된 최근 기사가 없습니다. 검색어를 바꾸거나 잠시 후 다시 확인해 주세요."
        return NewsDigest(
            query=query,
            generated_at=datetime.now(timezone.utc),
            source_name=self.source_name,
            overview=overview,
            key_topics=topics,
            article_count=len(articles),
            articles=articles,
            issue_count=len(issues),
            issues=issues,
        )

    def _parse(self, query: str, payload: bytes, limit: int) -> NewsDigest:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise NewsFeedError("뉴스 피드 형식을 읽지 못했습니다.") from exc
        articles: list[NewsArticle] = []
        seen: set[str] = set()
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source = (item.findtext("source") or "출처 미상").strip()
            if not title or not link or link in seen or not link.startswith("https://"):
                continue
            seen.add(link)
            clean_title = self._clean_title(title, source)
            articles.append(NewsArticle(
                title=clean_title,
                url=link,
                source=source,
                published_at=self._published_at(item.findtext("pubDate")),
            ))
            if len(articles) >= limit:
                break
        return self._digest(query, articles)

    def search(self, query: str, *, limit: int = 12) -> NewsDigest:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            raise ValueError("뉴스 검색어를 입력해 주세요.")
        key = (normalized.casefold(), limit)
        now = monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
        url = f"{self.feed_url}?{urlencode({'q': normalized, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'})}"
        request = Request(url, headers={
            "Accept": "application/rss+xml, application/xml;q=0.9",
            "User-Agent": "ai-VibeCoding-2026/0.3 news-reader",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                digest = self._parse(normalized, response.read(), limit)
        except NewsFeedError:
            raise
        except Exception as exc:
            raise NewsFeedError("뉴스 제공처에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
        with self._lock:
            self._cache[key] = (now + self.cache_seconds, digest)
        return digest

news_service = NewsService()
