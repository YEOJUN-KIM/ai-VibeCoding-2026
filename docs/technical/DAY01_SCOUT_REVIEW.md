# DAY01-SCOUT 독립 검증

상태: 2026-11-13 독립 재검증 수행, **수정 필요 / 통합 불가**. D-S1~D-S9의 기존 45개 독립 테스트는 통과했으나 새 증거 상한 로직의 세션 전환에서 D-S10이 재현됐다. 실제 동작·사용자 확인·운영 적용 완료가 아니다.
작업 기록: [20261113-Codex-DAY01-scout-review](../plans/ai-tasks/20261113-Codex-DAY01-scout-review.md).

## 기존 재사용 계약 조사

- `auto_trader/brokers/toss/market_data.py::_domestic_trading_amount_rankings`는 `(rankedAt, 정렬된 list)`를 반환한다. 공급자 조회는 KR 일일 거래대금, 투자주의 제외, 최대 100건이다. 내부 monotonic 만료시간 기준 30초 캐시이며 공급자 원본 기준시각을 저장하지만 최초 네트워크 수신 시각은 반환하지 않는다.
- 이 함수의 결과만으로 역사 순위·최초 수신 시각·메타데이터 이용 가능 시각을 복원할 수 없다. 향후 수집기는 실제 응답 수신 시각을 최초에 별도 기록하고 캐시 재사용 때 보존해야 한다. 이 작업은 실제 API나 수집기를 연결하지 않는다.
- `strategy_presets.build_preset`의 기존 선별 조건은 순위 50 이내, 국내 KRW 보통주, ACTIVE, 거래정지 아님, 정리매매 아님, 가격 1천원 이상, 등락률 절댓값 5% 이하, 거래대금 100억원 이상, 시총 1조원 이상이다. 이는 기존 비교 근거이며 scout의 확정 사양은 Claude 인계 문서로 확인해야 한다.
- `strategy_presets`는 database 및 models를 import하므로 재사용을 이유로 실행 import하지 않았다. 순위 broker 모듈도 실행 import하지 않았다. `risk_policy.py`는 Decimal만 import하는 순수 산술이나 입력 유효성·정책 적용 순서는 호출자 책임이다.
- 순위 원천의 투자주의 사전 제외 때문에 전체 시장 무필터 순위라는 주장도 피해야 한다. 공급자 기준을 그대로 설명하고 51위를 올려 50위로 재번호화하지 않아야 한다.

## 독립 검증 행렬

| 요구 범주 | 적대적 입력 및 확인할 불변조건 |
|---|---|
| 1. Top 50 경계 | 50위 통과/51위 배제, 상위 부적격 제거 후 51위 대체 금지, 행 순서와 무관한 공급자 rank 유지, 역사 순위 생성 금지 |
| 2. 시각 출처 | provider basis/실제 received/available/as-of 구분, 캐시 조회로 재도장 금지, 미래 순위·수신 정보 배제 |
| 3. 모든 사실의 as-of | 가격·종목유형·거래상태·유동성·품질·자본정보 각각 미래 available 시각이면 차단; missing/invalid/naive/future/stale 날짜 거부 |
| 4. 1주 예산 | 1주 원가에 fee/slippage 포함; cash/order/symbol/total 잔여, 예약액, 손실, 슬롯 한도 경계; 개별 가능 후보 목록과 전체 동시 매수 가능성을 명시적으로 구분 |
| 5. 국내·유동성 | 외국/ETF/우선주/정지/정리/불명 메타데이터 fail closed; 단순 실행 가능 플래그를 유동성 증거로 대체 금지 |
| 6. 목표 개수 | 안전 통과 후보 최대 10~15개, 부족하면 축소·0 허용; 수량을 채우려고 조건 완화 금지 |
| 7. 갱신 상태 | 동일 입력 결정성·멱등성, 역순 도착, 재전송·충돌 중복, 세션 전환, timeout/stale, 입력 불변성 |
| 8. 보유 감시 | Top 50 이탈/예산 부족/시세 stale/불명 종목도 감시 대상에 유지, 신규 후보 자격과 보유 감시 분리, 자동 청산 신호 생성 금지 |
| 9. DAY01 접점 | watch 목록만 반환, 신호/주문 없음, 기존 DAY01 소스 불변, 합성 후보→universe 인터페이스 검증 가능 여부 및 미연결 상태 기록 |

모든 범주에 bool-as-int, NaN/Infinity, 음수, 잘못된 timezone, 중복 rank/symbol 및 상충 메타데이터를 포함한다. 구조 전체를 거부하거나 해당 종목을 배제하는 정책은 명세와 일관되어야 하며 예외가 실행을 허용하는 경로로 이어지면 결함이다.

## 안전한 실행 계획과 한계

Claude의 커밋 및 공개 API를 받은 뒤 정적 import 감사를 먼저 한다. 독립 테스트는 합성 fixture와 표준 unittest로 구성하고 python -B로 bytecode 생성을 막는다. 운영 설정·DB·broker import가 필요하면 해당 경로는 실행하지 않고 미검증으로 기록한다.

기존 DAY01 호환성은 보존 브랜치 소스 blob/hash·인터페이스 정적 확인을 우선한다. 기존 259개 회귀 테스트는 읽기 전용 대상 worktree의 소유권과 안전 import를 확인한 뒤 승인 범위에서 최종 한 번만 실행하며, 현재까지 실행하지 않았다. 과거 259개 통과 기록은 이번 결과가 아니다.

## 구현 인계와 실제 검증 결과

- Claude 구현 `7532380cb3134bc56079b5618064bbb3c24f06f6`의 신규 4파일 전체 경로를 확인하고 정확한 로컬 cherry-pick을 수행했다(`e590400`); 기존 DAY01 소스는 합치거나 복사하지 않았다.
- 첫 실제 재현: `select_watchlist(T, (), portfolio=Portfolio(T+1초, 0, positions=(Position('FUTURE_ONLY',3),)))`가 과거 시점 보유 종목·수량을 노출했다(D-S1). 조정자를 통해 Claude에 보고했다.
- 승인된 수정 `afaee13547598950c46887f64390e3c904726a72`를 `96a6b785ce6e669588f150a9197792fa51802dbc`로 cherry-pick했다. 미래 포트폴리오 전체 무시 및 stale known+future prefix 회귀를 독립 테스트로 확인했다.
- `tests/test_day01_scout_review.py`는 구현자 테스트 fixture를 재사용하지 않은 **40개 합성 테스트**이다. 현 구현에서 31개 통과, 9개 실패, 오류·skip 없음. 실패를 expectedFailure로 숨기지 않았다.
- 실행 명령: `python -B -m unittest tests.test_day01_scout_review` → 40개, 실패 9개.
- 합동 검증 명령: `python -B -m unittest tests.test_day01_scout tests.test_day01_scout_review tests.test_chart_candles` → **109개, 실패 9개**. Claude 기본 66개 및 기존 chart 3개는 통과. `chart_candles`/models는 소스 import를 먼저 감사했으며 stdlib/pydantic만 사용하고 운영 설정·DB·broker 연결을 import하지 않는다.
- 독립 테스트의 preserved DAY01 `select_universe` 접점은 읽기 전용 `importlib`로 감사한 순수 모듈만 로드하여 합성 WATCH 심볼 전달을 확인했다. 입력 universe를 고정하는 형식 호환성 확인이며 과거 순위 생성·백테스트·주문 연결이 아니다. 후보가 0개일 때 기존 DAY01의 비어 있지 않은 universe 계약 때문에 호출자가 replay를 건너뛰어야 한다.

## 최초 재현 결함 및 수정 이력

| ID | 실제 재현 및 영향 | 독립 테스트 |
|---|---|---|
| D-S2 | max_history=2 상태에 알려진 현재 스냅샷과 미래 스냅샷 8개를 한 batch로 추가하면 미래 자료가 현재 이력을 퇴출한다. 기준 결과 OK가 NO_SNAPSHOT으로 바뀌어 prefix invariance 위반. | `test_future_snapshot_must_not_evict_known_history` |
| D-S3 | 동일 ID의 다른 내용 또는 같은 공급자 기준시각의 다른 내용이 refresh batch로 들어오면 먼저 입력한 것만 채택하고 후보를 생성한다. 순서를 뒤집으면 가격·결과가 바뀐다. 직접 selector의 충돌 차단과 달리 refresh가 충돌 증거를 지운다. | `test_refresh_conflicting_snapshot_batch_does_not_choose_first`, `test_different_ids_conflicting_same_provider_basis_fail_closed` |
| D-S4 | 같은 ID/내용의 늦은 수신 뒤 원래 이른 수신을 넣으면 이른 시각을 보존하지 않는다. 직접 selector에 같은 내용/다른 ID를 주어도 최신 수신 시각으로 provenance와 만료가 갱신된다. | `test_same_content_earlier_receipt_must_survive_reverse_arrival`, `test_direct_selector_cached_content_preserves_earliest_receipt` |
| D-S5 | 동일 이용 가능 시각의 상충 포트폴리오에 같은 보유수량, 다른 가격(100/200)을 주면 첫 항목의 보유 가격을 채택한다. 감시 출력이 입력 순서에 의존. | `test_conflicting_portfolio_held_prices_are_order_independent` |
| D-S6 | 공개 RankedRow(True, ...)를 공개 RankingSnapshot으로 전달하면 rank=True 후보가 생성되어 bool 검증 우회. 직접 snapshot에 list rows를 주면 frozen인데도 외부 list.clear로 내용 변경 가능. | `test_public_rankedrow_bool_rank_cannot_bypass_validation`, `test_direct_snapshot_owns_immutable_rows` |
| D-S7 | require_liquidity/readiness/capital=False opt-out에 사실이 제공되어 있으면 fully_eligible=True가 된다. 조정자가 전달한 opt-out은 unverified라는 계약과 다름. | `test_opt_out_never_claims_fully_eligible_even_with_present_facts` |

위 결함은 조정자에게 재현과 함께 전달했고 Claude가 구현 수정을 담당했다. Codex는 소스를 수정하지 않았다. 승인된 Claude 수정 `a82dfb257559c436bcb74380831869bcdae10a08`의 실제 diff와 소유 4파일 경로를 검토하고 `561692e`로 exact cherry-pick했다. 원본 독립 테스트 40개는 전부 통과했으며 Claude 기본 72개와 기존 chart 3개를 포함한 최초 재검증 115개가 통과했다. 이는 아래 추가 일반화 검증 이전 결과이다.

## 2차 일반화 검증과 수정 이력

수정 diff에서 인과적 미래 입력 분리·캐시 최초 수신 선택·충돌 포트폴리오의 UNKNOWN 병합·공개 생성자 검증·opt-out 갭 표시를 확인했다. 특히 `_merge`의 이력 절단과 `_canonical_snapshots`의 내용 중복 통합이 충돌 증거를 지우는지 새 회귀 5개로 확인했다. 미래 버퍼의 receipt 도달 시 활성화, 미래 충돌의 과거 prefix 불변, 알려진 최신 unknown basis의 차단은 통과했다. 다음 두 사례는 실패했다.

| ID | 실제 재현 및 영향 | 독립 테스트 |
|---|---|---|
| D-S8 | `refresh(empty(max_history=1),T,new_snapshots=(A,B))`에서 A/B가 동일 ID·기준시각, 다른 내용·수신시각이면 먼저 받은 A가 잘린다. 충돌을 이미 알았는데도 B를 OK로 허용하고 신규 후보를 생성한다. 기본 상한에서도 이력 압력으로 같은 문제가 생길 수 있다. bounded 이력과 충돌 증거 보존은 별도 책임이어야 한다. | `test_conflict_must_not_disappear_when_history_is_truncated` |
| D-S9 | A(id=original,내용1), alias(id=collision,같은 내용1), B(id=collision,다른 내용2·다른 기준시각)를 직접 selector에 주면 내용 통합 시 alias ID가 사라진다. 알려진 동일 ID 충돌이 없어진 것으로 처리돼 B가 OK·신규 후보로 허용된다. 원래 수신시각 통합 시에도 모든 causal ID alias의 충돌 정보는 유지해야 한다. | `test_snapshot_alias_collision_survives_content_deduplication` |

검증 파일은 구현자 복사본이 아닌 이 브랜치 원본이며 공개 생성자의 유효한 list rows 입력에 대해 실제 불변성 검사를 강화했다(정확한 기존 digest를 유지하도록 입력을 구성). expectedFailure·실패 skip 없음. 조정자에게 두 실제 재현을 전달했고 소스 수정은 하지 않았다.

승인된 Claude 수정 `d6a2a661d13a2f49c4a1f466ab981e36d1529d06`의 실제 4파일 diff를 읽고 `63ce8db`로 exact cherry-pick했다. ID alias 및 공급자 기준시각 증거를 중복 통합·history 절단 전에 보존하도록 바뀌었으며, 원본 독립 45개·Claude 기본 78개·chart 3개인 126개가 전부 통과했다. 다음 신규 경계 검증 전의 결과이다.

## 3차 경계 검증의 미해결 결함 D-S10

증거 상한 도입에 맞춰 최소 4개 검증을 추가했다. 미래 claim 대량 입력의 과거 prefix 불변과 같은 세션에서 overflow 격리 유지·보유 감시 독립은 통과했다. **KST 날짜 전환은 refresh와 ingest_fetch 양쪽 경로에서 실패했다.**

`MAX_CLAIMS=3`으로 축소한 동일 로직에서 전날 두 스냅샷의 4개 claim으로 격리된 상태(3개 보존)에 다음 날 신선한 스냅샷의 2개 claim을 추가한다. `_merge`가 전날 자료를 purge하기 전에 3+2>3을 판정하고, refresh가 그 후 전날 자료를 제거해도 `claims_overflow_day`를 새 날짜로 찍어 다시 격리한다. 실제 4096 상한에서도 전날 4096개 + 다음 날 2개로 같은 문제가 생긴다. 구현자 테스트는 rollover 직전에 상한을 3에서 4096으로 되돌려 이 조건을 놓쳤다.

- `test_expired_day_claims_must_not_requarantine_next_session`: 새 날짜 overflow 표식이 None이 아니라 `2026-11-14`로 남음.
- `test_fetch_ingestion_also_purges_expired_claims_before_overflow`: ingest_fetch를 먼저 거치면 이후 refresh도 INVALID_SNAPSHOT으로 남음.
- 조정자에게 재현을 전달했다. 만료 claim·snapshot 정리는 상한 계산·절단 전에 수행해야 하며 두 공개 경로의 의미가 일치해야 한다. 보유 감시나 주문 경로는 변경할 필요가 없다.

현재 기능은 순수 WATCH 모듈로 구현됐으나 자동 테스트 미해결 실패가 있어 통합 서명은 불가하다. 이는 실제 API 연결이 검증됐다는 의미가 아니며 실제 수집기는 여전히 미연결이다.

| 검증 범위 | 정확한 모듈 | 현재 결과 |
|---|---|---|
| Claude 기본 | `tests.test_day01_scout` | 78개 통과 |
| Codex 독립 | `tests.test_day01_scout_review` | 49개 중 47 통과 / 2 실패(D-S10 두 경로) |
| 기존 최소 회귀 | `tests.test_chart_candles` | 3개 통과 |
| 위 세 모듈 합동 | `python -B -m unittest tests.test_day01_scout tests.test_day01_scout_review tests.test_chart_candles` | **130개 중 128 통과 / 2 실패**, exit 1 |
| 기존 DAY01 보존 | 아래 네 exact 모듈 | 별도 승인 실행 259개 통과; 이번 Scout 수정 후 반복하지 않음 |

이전 109개는 66+40+3, 120개는 72+45+3, D-S8/D-S9 수정 직후 126개는 78+45+3이었다. 현재 130개 및 별도 259개와 누적·중복 합산하지 않는다. 이번 합동 상세 stdout/stderr는 chat scratch `scout-round3-tests.log`에 기록했다. 계정·provider 사용량을 조회하지 않았고 토큰·비용 실측값은 제공되지 않았다; 호출 횟수는 조정자가 관리한다.

## 기존 DAY01 보존 검증

- 조정자가 승인한 읽기 전용 경로: `C:/Users/User/orca/workspaces/ai-VibeCoding-2026/day01-claude-review-20261112`.
- 실행 전 HEAD `6a034f91bf34acd0a29cab703d275650b02bcd0e`, auto_trader tree `7292df68b2f2e6523d7751923b29110841daa203`, tests tree `f4e140a196aafd227024ad928e583ffc579784d7`, status clean. 실행 이후에도 tree와 status 동일 확인.
- 감사 대상은 `test_day01`, `test_day01_review`, `test_day01_research`, `test_day01_research_review`와 이들이 사용하는 순수 day01/day01_research/risk_policy 및 inert package init. 소스·테스트에는 운영 설정·DB·broker import가 없다. 별도 safe_regression_runner는 이번 대상과 달리 settings를 허용하는 경로가 있어 사용하지 않았다.
- 첫 승인 실행은 도구 출력 처리에서 진행 중 session 메타데이터를 놓쳐 일부 진행 점만 확보했다. **최종 결과 미확보이므로 통과로 계산하지 않는다.** 조정자의 추가 지시에 따라 필터된 Python 프로세스 읽기 전용 점검에서 대상 active=0을 확인한 뒤 증거 수집용 재실행 1회를 승인받았다. 기본 sandbox에서 프로세스 조회가 거부된 결과는 종료 증거로 사용하지 않았으며, 별도 허용된 읽기 전용 조회에서 정상 확인했다.
- 재실행은 `python -B -c`에서 `sys.path.insert(0,'tests')` 후 위 네 정확한 모듈만 unittest loader로 로드한다. 전체 discovery 없음. 끝에 tests/failures/errors/skipped와 unsafe import 목록을 출력하고 성공 여부를 exit code로 보존한다. stdout/stderr·시작/끝 시각·exit code는 chat scratch의 `day01-259-review.log`, `day01-259-review-result.txt`에만 저장한다.
- 최종 재실행 결과: **259개 통과, 실패 0, 오류 0, skip 0, unsafe_imports=[]; exit code 0**. 2026-11-13 09:25:07~09:26:05 KST, 57.424초. Python unittest가 stderr에 출력하는 진행 문자는 PowerShell에서 NativeCommandError 형식으로 감싸졌지만 실제 Python 종료 코드·unittest 결과·JSON 결과는 모두 성공이다. Scout 변경으로 이 순수 기존 소스를 다시 실행할 필요는 없다.

## 검증 범위와 다음 인계

- 독립 테스트 작성/자동 테스트: D-S1~D-S9 수정 후 기존 45개 모두 통과; 추가 4개 중 D-S10 두 경로가 실패하여 수정 필요.
- 실제 API·실계좌·장중·UI·운영 적용: 미실행 및 승인 범위 밖.
- API·DB 계약: 기존 계약 변경 없음. scout 신규 순수 인터페이스를 검토했고 위 미해결 사항이 있다.
- 기존 기능 영향: Claude 신규 순수 scout 모듈은 실제 경로에 연결되지 않았다. Codex 변경은 신규 테스트와 검토 문서·작업 기록뿐이다.
- GPT 검토 사항: 시각 출처와 캐시 보존, 모든 사실의 이용 가능 시각, 개별 affordability와 watchlist 전체 예산 의미, 실제 수집기 연결 이전 한계.
- 학습 개념: point-in-time 검증은 가격 외 모든 판단 사실의 이용 가능 시각에도 적용된다. 후보 집합과 주문·보유 감시 집합은 서로 다른 책임을 갖는다.

직접 확인: 위 합동 명령은 합성 검증만 수행하며 현재 예상 결과는 130개 중 2개 실패이다. Claude 수정 후 동일 명령을 다시 실행하여 실패 0개를 확인해야 통합 검토 후보가 된다. 실제 수집기의 최초 수신시각 보존, 상태·유동성·모든 메타데이터의 이용 가능 시각, snapshot/claim 영속 저장은 별도 작업이며 실환경 검증·main 병합·배포는 사용자 승인이 필요하다. 저장소는 https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 이고 작업 브랜치는 `YEOJUN-KIM/day01-scout-codex-review-20261113`; push/PR은 지시에 따라 수행하지 않는다. 다른 AI와 파일 충돌을 피하도록 구현 파일은 Claude 소유로 유지한다.
