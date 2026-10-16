"""Toss market data methods. The facade owns shared locks and caches."""

from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urlencode
from decimal import Decimal
from ...stock_search import normalize as normalize_stock_query, stock_match_rank
from ...chart_candles import CHART_RANGES, chart_candles
from ...models import LiveCandidateList, LiveFavoriteStock, LiveStockCandle, LiveStockCandidate, LiveStockDetail, LiveStockSearchPage, LiveStockSearchResult, Stock
from .types import TossApiError


class TossMarketData:
    def kr_market_open_today(self) -> tuple[str, bool]:
        """KST 기준 오늘 국내 통합장이 열리는 날인지 조회한다."""
        kst = timezone(timedelta(hours=9))
        today = datetime.now(kst).date().isoformat()
        now = monotonic()
        if (self._kr_market_day_cache is not None
                and self._kr_market_day_cache[0] == today
                and now < self._kr_market_day_expires_at):
            return self._kr_market_day_cache
        with self._kr_market_day_lock:
            now = monotonic()
            if (self._kr_market_day_cache is not None
                    and self._kr_market_day_cache[0] == today
                    and now < self._kr_market_day_expires_at):
                return self._kr_market_day_cache
            query = urlencode({"date": today})
            payload = self._authorized_json_request(
                f"{self.base_url}/api/v1/market-calendar/KR?{query}"
            )
            result = payload.get("result")
            market_day = result.get("today") if isinstance(result, dict) else None
            if not isinstance(market_day, dict):
                raise TossApiError("토스 API 국내 장 운영 응답 형식이 예상과 다릅니다.")
            market_open_today = isinstance(market_day.get("integrated"), dict)
            previous_day = result.get("previousBusinessDay")
            previous_date = previous_day.get("date") if isinstance(previous_day, dict) else None
            reference_date = str(
                (market_day.get("date") or today)
                if market_open_today else (previous_date or market_day.get("date") or today)
            )
            status = (reference_date, market_open_today)
            self._kr_market_day_cache = status
            self._kr_market_day_expires_at = monotonic() + 300
            return status


    def affordable_domestic_candidates(self, count: int = 5) -> LiveCandidateList:
        now = monotonic()
        if self._candidate_cache is not None and now < self._candidate_expires_at:
            return self._candidate_cache
        with self._candidate_lock:
            now = monotonic()
            if self._candidate_cache is not None and now < self._candidate_expires_at:
                return self._candidate_cache
            cash = self.buying_power().krw_cash_buying_power
            ranked_at, rankings = self._domestic_trading_amount_rankings()
            affordable = []
            for item in sorted(rankings, key=lambda row: row.get("rank", 999)):
                price_data = item.get("price") or {}
                price = self._decimal(price_data.get("lastPrice"))
                if price > 0 and price <= cash:
                    affordable.append((item, price, price_data))
            symbols = [str(item[0].get("symbol", "")) for item in affordable]
            details: dict[str, dict] = {}
            if symbols:
                info_query = urlencode({"symbols": ",".join(symbols)})
                info_payload = self._authorized_json_request(
                    f"{self.base_url}/api/v1/stocks?{info_query}"
                )
                info = info_payload.get("result")
                if not isinstance(info, list):
                    raise TossApiError("토스 API 종목 정보 응답 형식이 예상과 다릅니다.")
                details = {str(item.get("symbol", "")): item for item in info}
            candidates = []
            for item, price, price_data in affordable:
                symbol = str(item.get("symbol", ""))
                detail = details.get(symbol) or {}
                market_detail = detail.get("koreanMarketDetail") or {}
                if (detail.get("securityType") != "STOCK"
                        or not detail.get("isCommonShare")
                        or detail.get("status") != "ACTIVE"
                        or market_detail.get("liquidationTrading")
                        or market_detail.get("krxTradingSuspended")):
                    continue
                rank = int(item.get("rank", 0))
                candidates.append(LiveStockCandidate(
                    rank=rank,
                    symbol=symbol,
                    name=str(detail.get("name") or symbol),
                    price=price,
                    change_rate_percent=self._percent(price_data.get("changeRate")),
                    max_quantity=int(cash // price),
                    reason=f"국내 일일 거래대금 {rank}위 · 주문 가능 조건 충족",
                ))
                if len(candidates) >= count:
                    break
            candidate_list = LiveCandidateList(
                available_cash_krw=cash,
                ranked_at=ranked_at,
                basis="거래대금과 주문 가능 조건을 바탕으로 투자유의 종목을 제외한 탐색 결과",
                disclaimer="투자 권유가 아닌 탐색 후보입니다. 수익을 보장하지 않으며 가격과 주문 가능 금액은 변할 수 있습니다.",
                candidates=candidates,
            )
            self._candidate_cache = candidate_list
            self._candidate_expires_at = monotonic() + 30
            return candidate_list


    def _domestic_trading_amount_rankings(self) -> tuple[str | None, list[dict]]:
        now = monotonic()
        if self._domestic_rankings_cache is not None and now < self._domestic_rankings_expires_at:
            return self._domestic_rankings_cache
        with self._domestic_rankings_lock:
            now = monotonic()
            if self._domestic_rankings_cache is not None and now < self._domestic_rankings_expires_at:
                return self._domestic_rankings_cache
            query = urlencode({
                "type": "MARKET_TRADING_AMOUNT",
                "marketCountry": "KR",
                "duration": "1d",
                "excludeInvestmentCaution": "true",
                "count": 100,
            })
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/rankings?{query}")
            result = payload.get("result")
            rankings = result.get("rankings") if isinstance(result, dict) else None
            if not isinstance(rankings, list):
                raise TossApiError("토스 API 국내 거래대금 순위 응답 형식이 예상과 다릅니다.")
            cached = (
                result.get("rankedAt") if isinstance(result, dict) else None,
                sorted(rankings, key=lambda item: item.get("rank", 999)),
            )
            self._domestic_rankings_cache = cached
            self._domestic_rankings_expires_at = monotonic() + 30
            return cached


    def _domestic_stock_universe(self) -> list[dict]:
        now = monotonic()
        if self._stock_universe_cache is not None and now < self._stock_universe_expires_at:
            return self._stock_universe_cache
        with self._stock_universe_lock:
            now = monotonic()
            if self._stock_universe_cache is not None and now < self._stock_universe_expires_at:
                return self._stock_universe_cache
            universe = []
            for index, market in enumerate(("KOSPI", "KOSDAQ")):
                if index:
                    sleep(1.05)
                query = urlencode({"market": market, "status": "ACTIVE"})
                payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks/all?{query}")
                result = payload.get("result")
                if not isinstance(result, list):
                    raise TossApiError("토스 API 국내 종목 목록 응답 형식이 예상과 다릅니다.")
                universe.extend({**item, "market": market} for item in result)
            self._stock_universe_cache = universe
            self._stock_universe_expires_at = monotonic() + 43200
            return universe


    def warm_domestic_stock_universe(self) -> int:
        """서버 시작 시 종목 목록을 미리 적재해 첫 화면 대기를 줄인다."""
        return len(self._domestic_stock_universe())


    def warm_domestic_stock_directory(self) -> tuple[int, int]:
        """국내주식 첫 화면의 장기 캐시와 인기순 정렬 캐시를 미리 준비한다."""
        stock_count = len(self._domestic_stock_universe())
        _, rankings = self._domestic_trading_amount_rankings()
        popular_symbols = [
            str(item.get("symbol", "")) for item in rankings[:20] if item.get("symbol")
        ]
        if popular_symbols:
            self.domestic_sparklines(popular_symbols, period="1D")
        return stock_count, len(rankings)


    def search_domestic_stocks(self, query: str, page: int = 1,
                               page_size: int = 8, *, include_quotes: bool = True) -> LiveStockSearchPage:
        return self.list_domestic_stocks(
            query=query, page=page, page_size=page_size, sort="POPULAR", include_quotes=include_quotes
        )


    def strategy_preset_candidates(self):
        """Reuse the directory's batched quotes and cached trading metadata."""
        page = self.list_domestic_stocks(security_type="COMMON", sort="POPULAR", page_size=50)
        return [(stock, self._domestic_stock_info(stock.symbol)) for stock in page.results
                if stock.trading_amount_rank and stock.trading_amount_rank <= 50]


    def list_domestic_stocks(self, *, query: str = "", market: str = "ALL",
                             security_type: str = "ALL", sort: str = "POPULAR",
                             page: int = 1, page_size: int = 20, include_quotes: bool = True) -> LiveStockSearchPage:
        normalized = normalize_stock_query(query)
        universe = self._domestic_stock_universe()
        match_ranks = {str(item.get("symbol", "")): stock_match_rank(item, normalized) for item in universe}
        matches = [
            item for item in universe
            if (normalized or not query.strip())
            and match_ranks[str(item.get("symbol", ""))] is not None
            and (market == "ALL" or str(item.get("market", "")) == market)
            and (
                security_type == "ALL"
                or (security_type == "COMMON" and bool(item.get("isCommonShare", False)))
                or str(item.get("securityType", "")) == security_type
            )
        ]
        if include_quotes:
            _, rankings = self._domestic_trading_amount_rankings()
        else:
            # Selection needs identities, not fresh pricing or ranking requests.
            rankings = self._domestic_rankings_cache[1] if self._domestic_rankings_cache else []
        ranking_by_symbol = {str(item.get("symbol", "")): item for item in rankings}
        if sort == "NAME":
            matches.sort(key=lambda item: (str(item.get("name", "")).casefold(),
                                           str(item.get("symbol", ""))))
        elif sort == "CODE":
            matches.sort(key=lambda item: str(item.get("symbol", "")))
        else:
            matches.sort(key=lambda item: (
                int((ranking_by_symbol.get(str(item.get("symbol", ""))) or {}).get("rank", 1_000_000)),
                str(item.get("name", "")).casefold(),
                str(item.get("symbol", "")),
            ))
        # Stable sort keeps the selected directory order within each match quality.
        matches.sort(key=lambda item: match_ranks[str(item.get("symbol", ""))])
        total = len(matches)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        matches = matches[start:start + page_size]
        prices = {}
        details = {}
        if matches and include_quotes:
            symbols = [str(item.get("symbol", "")) for item in matches]
            price_query = urlencode({"symbols": ",".join(symbols)})
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
            result = payload.get("result")
            if not isinstance(result, list):
                raise TossApiError("토스 API 현재가 응답 형식이 예상과 다릅니다.")
            prices = {str(item.get("symbol", "")): item for item in result}
            now = monotonic()
            with self._stock_info_lock:
                details = {symbol: cached[1] for symbol in symbols
                           if (cached := self._stock_info_cache.get(symbol)) and now < cached[0]}
            missing = [symbol for symbol in symbols if symbol not in details]
            if missing:
                detail_query = urlencode({"symbols": ",".join(missing)})
                detail_payload = self._authorized_json_request(
                    f"{self.base_url}/api/v1/stocks?{detail_query}"
                )
                detail_rows = detail_payload.get("result")
                if not isinstance(detail_rows, list):
                    raise TossApiError("토스 API 종목 기본정보 응답 형식이 예상과 다릅니다.")
                fetched = {str(item.get("symbol", "")): item for item in detail_rows}
                details.update(fetched)
                with self._stock_info_lock:
                    expires_at = monotonic() + 43200
                    for symbol, detail in fetched.items():
                        self._stock_info_cache[symbol] = (expires_at, detail)
        results = []
        for item in matches:
            symbol = str(item.get("symbol", ""))
            ranking = ranking_by_symbol.get(symbol)
            current_price = (self._decimal(prices[symbol].get("lastPrice"))
                             if symbol in prices else None)
            shares = self._decimal((details.get(symbol) or {}).get("sharesOutstanding"))
            previous_close = self._decimal(((ranking or {}).get("price") or {}).get("basePrice"))
            results.append(LiveStockSearchResult(
                symbol=symbol,
                name=str(item.get("name", "")),
                market=str(item.get("market", "")),
                security_type=str(item.get("securityType", "")),
                is_common_share=bool(item.get("isCommonShare", False)),
                currency=str((prices.get(symbol) or {}).get("currency", "KRW")),
                price=current_price,
                previous_close=previous_close,
                change_rate_percent=(((current_price / previous_close) - 1) * 100
                                     if current_price is not None and previous_close and previous_close > 0
                                     else None),
                trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
                trading_amount=(self._decimal(ranking.get("tradingAmount")) if ranking else None),
                market_cap=(current_price * shares if current_price is not None and shares else None),
            ))
        return LiveStockSearchPage(
            query=query,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            results=results,
        )


    def domestic_stock(self, symbol: str) -> dict | None:
        normalized = symbol.strip().upper()
        return next(
            (item for item in self._domestic_stock_universe()
             if str(item.get("symbol", "")).upper() == normalized),
            None,
        )


    def _domestic_stock_info(self, symbol: str) -> dict:
        normalized = symbol.strip().upper()
        now = monotonic()
        cached = self._stock_info_cache.get(normalized)
        if cached is not None and now < cached[0]:
            return cached[1]
        query = urlencode({"symbols": normalized})
        payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks?{query}")
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise TossApiError("토스 API 종목 기본정보 응답 형식이 예상과 다릅니다.")
        detail = rows[0] if rows else {}
        with self._stock_info_lock:
            self._stock_info_cache[normalized] = (monotonic() + 43200, detail)
        return detail


    @staticmethod
    def _chart_period(period: str, *, detailed: bool = False) -> tuple[str, int]:
        periods = {
            "1D": ("1m", 390 if detailed else 200),
            "1W": (("1m", 1950) if detailed else ("1d", 7)),
            # 한 달 분봉은 최대 43페이지를 순차 호출해야 해 화면 전환이 지나치게 느리다.
            # 월간 추세에는 거래일별 일봉을 사용해 한 번의 요청으로 응답한다.
            "1M": ("1d", 22),
            "3M": ("1d", 60),
            "1Y": ("1d", 200),
        }
        return periods.get(period.upper(), periods["1M"])


    def _domestic_candles(self, symbol: str, interval: str, count: int,
                          *, cache_seconds: int | None = None) -> list[LiveStockCandle]:
        normalized = symbol.strip().upper()
        cache_key = (normalized, interval, cache_seconds)
        now = monotonic()
        cached = self._candle_cache.get(cache_key)
        if cached is not None and now < cached[0] and cached[1] >= count:
            return cached[2][-count:]
        with self._candle_lock:
            request_lock = self._candle_request_locks.setdefault(cache_key, Lock())
        # 같은 종목·주기의 중복 페이지 수집만 직렬화한다. 서로 다른 종목은 병렬 조회된다.
        with request_lock:
            cached = self._candle_cache.get(cache_key)
            if cached is not None and monotonic() < cached[0] and cached[1] >= count:
                return cached[2][-count:]
            raw_rows: list[dict] = []
            before: str | None = None
            seen_cursors: set[str] = set()
            max_pages = max(1, (count + 199) // 200)
            for page_index in range(max_pages):
                query_values = {"symbol": normalized, "interval": interval, "count": 200}
                if before:
                    query_values["before"] = before
                query = urlencode(query_values)
                payload = self._authorized_json_request(f"{self.base_url}/api/v1/candles?{query}")
                result = payload.get("result")
                rows = result.get("candles") if isinstance(result, dict) else None
                if not isinstance(rows, list):
                    raise TossApiError("토스 API 차트 응답 형식이 예상과 다릅니다.")
                raw_rows.extend(rows)
                if len(raw_rows) >= count:
                    break
                next_before = result.get("nextBefore") if isinstance(result, dict) else None
                if not next_before or next_before in seen_cursors:
                    break
                seen_cursors.add(str(next_before))
                before = str(next_before)
                if page_index + 1 < max_pages:
                    sleep(0.08)
            candles = [LiveStockCandle(
                timestamp=item.get("timestamp"),
                open_price=self._decimal(item.get("openPrice")),
                high_price=self._decimal(item.get("highPrice")),
                low_price=self._decimal(item.get("lowPrice")),
                close_price=self._decimal(item.get("closePrice")),
                volume=self._decimal(item.get("volume")),
            ) for item in reversed(raw_rows)]
            if cache_seconds is None:
                cache_seconds = 1800 if interval == "1m" and count > 2000 else 300
            with self._candle_lock:
                self._candle_cache[cache_key] = (monotonic() + cache_seconds, count, candles)
            return candles[-count:]


    def _market_indicator_candles(
        self, symbol: str, interval: str, count: int, *, cache_seconds: int = 0
    ) -> list[LiveStockCandle]:
        """Return Toss market-index candles in chronological order."""
        normalized = symbol.strip().upper()
        if interval == "1m" and normalized not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("시장 지표 1분봉은 KOSPI와 KOSDAQ만 지원합니다.")
        if interval not in {"1m", "1d"}:
            raise ValueError("시장 지표 봉 단위는 1m 또는 1d여야 합니다.")
        if count <= 0:
            raise ValueError("count는 1 이상이어야 합니다.")

        cache_key = (f"MARKET_INDICATOR:{normalized}", interval, cache_seconds)
        cached = self._candle_cache.get(cache_key)
        if cached is not None and monotonic() < cached[0] and cached[1] >= count:
            return cached[2][-count:]
        with self._candle_lock:
            request_lock = self._candle_request_locks.setdefault(cache_key, Lock())
        with request_lock:
            cached = self._candle_cache.get(cache_key)
            if cached is not None and monotonic() < cached[0] and cached[1] >= count:
                return cached[2][-count:]
            raw_rows: list[dict] = []
            before: str | None = None
            seen_cursors: set[str] = set()
            max_pages = max(1, (count + 199) // 200)
            for page_index in range(max_pages):
                query_values = {"interval": interval, "count": min(200, count - len(raw_rows))}
                if before:
                    query_values["before"] = before
                query = urlencode(query_values)
                payload = self._authorized_json_request(
                    f"{self.base_url}/api/v1/market-indicators/{normalized}/candles?{query}"
                )
                result = payload.get("result")
                rows = result.get("candles") if isinstance(result, dict) else None
                if not isinstance(rows, list):
                    raise TossApiError("토스 API 시장 지표 차트 응답 형식이 예상과 다릅니다.")
                raw_rows.extend(rows)
                if len(raw_rows) >= count:
                    break
                next_before = result.get("nextBefore") if isinstance(result, dict) else None
                if not next_before or str(next_before) in seen_cursors:
                    break
                seen_cursors.add(str(next_before))
                before = str(next_before)
                if page_index + 1 < max_pages:
                    sleep(0.08)
            candles = [
                LiveStockCandle(
                    timestamp=item.get("timestamp"),
                    open_price=self._decimal(item.get("openPrice")),
                    high_price=self._decimal(item.get("highPrice")),
                    low_price=self._decimal(item.get("lowPrice")),
                    close_price=self._decimal(item.get("closePrice")),
                    volume=self._decimal(item.get("volume")),
                )
                for item in reversed(raw_rows)
            ]
            with self._candle_lock:
                self._candle_cache[cache_key] = (monotonic() + cache_seconds, count, candles)
            return candles[-count:]


    def exchange_rate(self, base_currency: str = "USD", quote_currency: str = "KRW") -> dict:
        base = base_currency.strip().upper()
        quote = quote_currency.strip().upper()
        if {base, quote} != {"USD", "KRW"} or base == quote:
            raise ValueError("환율 조회는 USD와 KRW 조합만 지원합니다.")
        query = urlencode({"baseCurrency": base, "quoteCurrency": quote})
        payload = self._authorized_json_request(
            f"{self.base_url}/api/v1/exchange-rate?{query}"
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("validFrom"):
            raise TossApiError("토스 API 환율 응답 형식이 예상과 다릅니다.")
        return result


    def domestic_sparklines(
        self, symbols: list[str], count: int | None = None, period: str = "1D"
    ) -> dict[str, list[Decimal]]:
        interval, period_count = self._chart_period(period)
        candle_count = count or period_count
        lines: dict[str, list[Decimal]] = {}
        for index, symbol in enumerate(symbols):
            if index:
                sleep(0.06)
            try:
                lines[symbol] = [
                    item.close_price for item in self._domestic_candles(symbol, interval, candle_count)
                ]
            except TossApiError as exc:
                if exc.status_code not in {400, 404}:
                    raise
                lines[symbol] = []
        return lines


    def domestic_stock_detail(self, symbol: str, period: str = "1D", *, refresh: bool = False, candle_interval: str | None = None) -> LiveStockDetail:
        if candle_interval is not None and period not in CHART_RANGES.get(candle_interval, {}):
            raise TossApiError("선택한 봉 단위와 조회 기간을 확인하세요.", status_code=422)
        stock = self.domestic_stock(symbol)
        if not stock:
            raise TossApiError("국내 종목을 찾지 못했습니다.", status_code=404,
                               error_code="stock-not-found")
        normalized = str(stock.get("symbol", "")).upper()
        price_query = urlencode({"symbols": normalized})
        price_payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
        price_rows = price_payload.get("result")
        if not isinstance(price_rows, list):
            raise TossApiError("토스 API 현재가 응답 형식이 예상과 다릅니다.")
        price = price_rows[0] if price_rows else {}
        stock_info = self._domestic_stock_info(normalized)
        interval, candle_count = self._chart_period(period, detailed=True)
        if candle_interval is not None:
            if period not in CHART_RANGES.get(candle_interval, {}):
                raise TossApiError("선택한 봉 단위와 조회 기간을 확인하세요.", status_code=422)
            interval = "1m" if candle_interval in ("1m", "1h") else "1d"
            candle_count = CHART_RANGES[candle_interval][period]
        candles = self._domestic_candles(normalized, interval, candle_count, cache_seconds=30 if refresh and interval == "1m" else None)
        current_price = self._decimal(price.get("lastPrice")) if price.get("lastPrice") is not None else None
        _, rankings = self._domestic_trading_amount_rankings()
        ranking = next((item for item in rankings if str(item.get("symbol", "")) == normalized), None)
        daily_candles = (candles if interval == "1d" else self._domestic_candles(normalized, "1d", 2))
        previous_close = daily_candles[-2].close_price if len(daily_candles) >= 2 else None
        if candle_interval is not None:
            candles = chart_candles(candles, candle_interval, period)
        change_rate = ((current_price / previous_close - Decimal("1")) * 100
                       if current_price is not None and previous_close and previous_close > 0 else None)
        shares_outstanding = (self._decimal(stock_info.get("sharesOutstanding"))
                              if stock_info.get("sharesOutstanding") is not None else None)
        korean_market_detail = stock_info.get("koreanMarketDetail")
        if not isinstance(korean_market_detail, dict):
            korean_market_detail = {}
        return LiveStockDetail(
            candle_interval=candle_interval,
            symbol=normalized,
            name=str(stock.get("name", "")),
            market=str(stock.get("market", "")),
            security_type=str(stock.get("securityType", "")),
            is_common_share=bool(stock.get("isCommonShare", False)),
            currency=str(price.get("currency", "KRW")),
            price=current_price,
            change_rate_percent=change_rate, previous_close=previous_close,
            trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
            market_cap=(current_price * shares_outstanding
                        if current_price is not None and shares_outstanding else None),
            shares_outstanding=shares_outstanding,
            trading_amount=(self._decimal(ranking.get("tradingAmount")) if ranking else None),
            trading_volume=(self._decimal(ranking.get("tradingVolume")) if ranking else None),
            english_name=(str(stock_info.get("englishName"))
                          if stock_info.get("englishName") else None),
            isin_code=(str(stock_info.get("isinCode")) if stock_info.get("isinCode") else None),
            list_date=stock_info.get("listDate"),
            listing_status=(str(stock_info.get("status")) if stock_info.get("status") else None),
            nxt_supported=(bool(korean_market_detail.get("nxtSupported"))
                           if "nxtSupported" in korean_market_detail else None),
            krx_trading_suspended=(bool(korean_market_detail.get("krxTradingSuspended"))
                                   if "krxTradingSuspended" in korean_market_detail else None),
            nxt_trading_suspended=(bool(korean_market_detail.get("nxtTradingSuspended"))
                                   if "nxtTradingSuspended" in korean_market_detail else None),
            candles=candles,
        )


    def current_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        """여러 종목의 현재가만 한 번에 조회한다.

        전략 선택처럼 차트·종목 상세·랭킹이 필요 없는 경로에서 상세 조회를
        반복해 API 한도를 소모하지 않도록 분리한 경량 조회다.
        """
        normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if not normalized:
            return {}
        query = urlencode({"symbols": ",".join(normalized)})
        payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{query}")
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise TossApiError("토스 API 현재가 응답 형식이 예상과 다릅니다.")
        return {
            str(item.get("symbol", "")).upper(): self._decimal(item.get("lastPrice"))
            for item in rows
            if item.get("symbol") and item.get("lastPrice") is not None
        }


    def favorite_stock_snapshots(self, favorites: list[dict]) -> list[LiveFavoriteStock]:
        if not favorites:
            return []
        symbols = [str(item["symbol"]) for item in favorites]
        price_query = urlencode({"symbols": ",".join(symbols)})
        payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
        price_result = payload.get("result")
        if not isinstance(price_result, list):
            raise TossApiError("토스 API 관심 종목 현재가 응답 형식이 예상과 다릅니다.")
        prices = {str(item.get("symbol", "")): item for item in price_result}
        _, rankings = self._domestic_trading_amount_rankings()
        ranking_by_symbol = {str(item.get("symbol", "")): item for item in rankings}
        snapshots = []
        for favorite in favorites:
            symbol = str(favorite["symbol"])
            price = prices.get(symbol)
            ranking = ranking_by_symbol.get(symbol)
            ranking_price = (ranking or {}).get("price") or {}
            snapshots.append(LiveFavoriteStock(
                symbol=symbol,
                name=str(favorite["name"]),
                market=str(favorite["market"]),
                security_type=str(favorite["security_type"]),
                is_common_share=bool(favorite["is_common_share"]),
                created_at=favorite["created_at"],
                currency=str((price or {}).get("currency", "KRW")),
                price=(self._decimal(price.get("lastPrice")) if price else None),
                change_rate_percent=(self._percent(ranking_price.get("changeRate"))
                                     if ranking else None),
                trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
            ))
        return snapshots


    def domestic_trading_amount_top(self, count: int = 10) -> tuple[list[Stock], dict[str, Decimal]]:
        query = urlencode({"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR",
                           "duration": "1d", "excludeInvestmentCaution": "true", "count": count})
        ranking_payload = self._authorized_json_request(f"{self.base_url}/api/v1/rankings?{query}")
        rankings = (ranking_payload.get("result") or {}).get("rankings")
        if not isinstance(rankings, list) or not rankings:
            raise TossApiError("토스 API 거래대금 순위 응답 형식이 예상과 다릅니다.")
        rankings = sorted(rankings, key=lambda item: item.get("rank", 999))[:count]
        symbols = [str(item["symbol"]) for item in rankings]
        info_query = urlencode({"symbols": ",".join(symbols)})
        info_payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks?{info_query}")
        info = info_payload.get("result")
        if not isinstance(info, list):
            raise TossApiError("토스 API 종목 정보 응답 형식이 예상과 다릅니다.")
        names = {str(item["symbol"]): str(item["name"]) for item in info}
        stocks = [Stock(symbol=symbol, name=names.get(symbol, symbol)) for symbol in symbols]
        prices = {str(item["symbol"]): self._decimal(
            (item.get("price") or {}).get("lastPrice") if isinstance(item.get("price"), dict)
            else item.get("price")) for item in rankings}
        return stocks, prices
