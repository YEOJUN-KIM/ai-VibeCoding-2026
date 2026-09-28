"""OpenDART 기업개황·재무·배당·공시 조회 클라이언트."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from threading import Lock
from time import monotonic
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

from .models import CompanyDisclosure, CompanyFinancialYear, CompanyMetric, LiveCompanyProfile
from .settings import settings
from .industries import industry_name


class DartApiError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 10) -> None:
        self.api_key = settings.dart_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.dart_api_base_url).rstrip("/")
        self.timeout = timeout
        self._corp_codes: dict[str, str] | None = None
        self._corp_codes_lock = Lock()
        self._profile_cache: dict[str, tuple[float, LiveCompanyProfile]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _json(self, endpoint: str, **params: str) -> dict:
        query = urlencode({"crtfc_key": self.api_key, **params})
        request = Request(f"{self.base_url}/{endpoint}?{query}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = str(payload.get("status", "000"))
        if status not in {"000", "013"}:
            raise DartApiError(str(payload.get("message") or "OpenDART 요청에 실패했습니다."))
        return payload

    def _load_corp_codes(self) -> dict[str, str]:
        if self._corp_codes is not None:
            return self._corp_codes
        with self._corp_codes_lock:
            if self._corp_codes is not None:
                return self._corp_codes
            query = urlencode({"crtfc_key": self.api_key})
            request = Request(f"{self.base_url}/corpCode.xml?{query}")
            with urlopen(request, timeout=self.timeout) as response:
                zipped = response.read()
            with ZipFile(BytesIO(zipped)) as archive:
                root = ElementTree.fromstring(archive.read("CORPCODE.xml"))
            self._corp_codes = {
                (item.findtext("stock_code") or "").strip(): (item.findtext("corp_code") or "").strip()
                for item in root.findall("list") if (item.findtext("stock_code") or "").strip()
            }
            return self._corp_codes

    @staticmethod
    def _metrics(rows: list[dict], names: tuple[str, ...]) -> list[CompanyMetric]:
        result = []
        for name in names:
            target = name.replace(" ", "")
            row = next((item for item in rows if target in str(item.get("account_nm", "")).replace(" ", "")), None)
            if row:
                current = str(row.get("thstrm_amount") or "-")
                previous = str(row.get("frmtrm_amount") or "-")
                change = None
                try:
                    current_number = Decimal(current.replace(",", ""))
                    previous_number = Decimal(previous.replace(",", ""))
                    if previous_number:
                        change = ((current_number - previous_number) / abs(previous_number)) * Decimal("100")
                except (InvalidOperation, ValueError):
                    pass
                result.append(CompanyMetric(label=name, value=current, previous_value=previous,
                                            change_rate_percent=change))
        return result

    @staticmethod
    def _amount(rows: list[dict], name: str) -> Decimal | None:
        target = name.replace(" ", "")
        row = next((item for item in rows if target in str(item.get("account_nm", "")).replace(" ", "")), None)
        if not row:
            return None
        try:
            return Decimal(str(row.get("thstrm_amount", "")).replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    def company_profile(self, symbol: str) -> LiveCompanyProfile:
        if not self.configured:
            return LiveCompanyProfile(configured=False, available=False,
                                      message="OpenDART API 키를 설정하면 상세 기업정보를 볼 수 있습니다.")
        normalized = symbol.strip().zfill(6)
        cached = self._profile_cache.get(normalized)
        if cached and monotonic() - cached[0] < 600:
            return cached[1]
        try:
            corp_code = self._load_corp_codes().get(normalized)
            if not corp_code:
                return LiveCompanyProfile(configured=True, available=False,
                                          message="이 종목에 연결된 OpenDART 법인정보가 없습니다.")
            year = str(date.today().year - 1)
            overview = self._json("company.json", corp_code=corp_code)
            financial = self._json("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=year,
                                   reprt_code="11011", fs_div="CFS")
            history = []
            for history_year in range(int(year) - 2, int(year) + 1):
                history_payload = financial if str(history_year) == year else self._json(
                    "fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(history_year),
                    reprt_code="11011", fs_div="CFS")
                history_rows = history_payload.get("list") if isinstance(history_payload.get("list"), list) else []
                if history_rows:
                    history.append(CompanyFinancialYear(
                        year=str(history_year), revenue=self._amount(history_rows, "매출액"),
                        operating_income=self._amount(history_rows, "영업이익"),
                        net_income=self._amount(history_rows, "당기순이익"),
                    ))
            dividend = self._json("alotMatter.json", corp_code=corp_code, bsns_year=year,
                                  reprt_code="11011")
            disclosures = self._json("list.json", corp_code=corp_code, bgn_de=f"{year}0101",
                                     end_de=date.today().strftime("%Y%m%d"), page_count="8",
                                     sort="date", sort_mth="desc")
            financial_rows = financial.get("list") if isinstance(financial.get("list"), list) else []
            dividend_rows = dividend.get("list") if isinstance(dividend.get("list"), list) else []
            dividend_metrics = []
            for label in ("주당 현금배당금(원)", "현금배당수익률(%)", "현금배당금총액(백만원)"):
                row = next((item for item in dividend_rows if item.get("se") == label), None)
                if row:
                    dividend_metrics.append(CompanyMetric(label=label,
                        value=str(row.get("thstrm") or "-"), previous_value=str(row.get("frmtrm") or "-")))
            profile = LiveCompanyProfile(
                configured=True, available=True, message=f"OpenDART {year}년 사업보고서 기준",
                fiscal_year=year, corporation_name=overview.get("corp_name"), ceo_name=overview.get("ceo_nm"),
                industry_code=overview.get("induty_code"), industry_name=industry_name(overview.get("induty_code")),
                established_date=overview.get("est_dt"),
                address=overview.get("adres"), homepage=overview.get("hm_url"), fiscal_month=overview.get("acc_mt"),
                financials=self._metrics(financial_rows, ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")),
                financial_history=history,
                dividends=dividend_metrics,
                disclosures=[CompanyDisclosure(title=str(item.get("report_nm", "공시")),
                    receipt_no=str(item.get("rcept_no", "")), receipt_date=str(item.get("rcept_dt", "")),
                    submitter=item.get("flr_nm")) for item in (disclosures.get("list") or [])[:8]],
            )
        except Exception as exc:
            profile = LiveCompanyProfile(configured=True, available=False,
                                         message=f"OpenDART 정보를 불러오지 못했습니다: {exc}")
        self._profile_cache[normalized] = (monotonic(), profile)
        return profile


dart_client = DartClient()
