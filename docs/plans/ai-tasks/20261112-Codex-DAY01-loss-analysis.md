# 20261112-Codex-DAY01-loss-analysis

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261112-Codex-DAY01-loss-analysis |
| 목표 / 이유 | 기존 A48/B68 왕복 손실의 원장·비용·청산·분포·후보 거절 증거를 분석하고 사실/UNKNOWN/독립 검증 가설 분리 |
| 담당 AI | Codex 분석·문서; Claude 독립 감사; coordinator 인계 |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-codex-20261112 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-codex-20261112 |
| 기준 커밋 전체 SHA | d45c236a860659f39830cac34ce50d98d549cb3b; 코드/tests 후보293cc09360671e64327e8081b179eaceb8351190 |
| 수정 허용 파일 | 이 기록과 docs/technical/DAY01_LOSS_ANALYSIS.md, 모두 새 파일 |
| 선행 작업 / commit / PR | DAY01-capital-multiday/d45c236; 신규 PR 없음 |
| 공통 파일 담당 / 순서 | 기존 파일 수정 없음; Codex 분석 증거 인계 후 Claude 독립 감사 |
| 완료 조건 | A48/B68 전 원장 CSV/JSON·비용 bridge·8개 질문 표·분포·공통/추가/변경 거래·481후보 분석·해시불변·한계·가설 |
| 승인된 검증 / 정확한 명령 | 새 scratch의 python -B analyze_losses.py, 표준라이브러리 Decimal/JSON만; 엔진 import·replay·테스트 없음 |
| 격리 | DB/API/env/서버/워커/실제 주문 접근 없음; 기존 capital-multiday evidence-index+150 outputs만 읽기 |
| 미실행 / 금지 | 코드/tests/input/기존 문서·증거 수정, 새로운 데이터 탐색, backtest/run_input/조건실험/최적화/설치/push/PR/main |
| 승인 상태 / 근거 | 승인: 사용자 task_18452bea79dc / ctx_1a0ec1b8534c의 기존 손실 분석 지시 |
| 진행 상태 | 승인된 기존 결과 분석·문서 반영 완료, Claude 독립 감사 대기; 수익성 검증·사용자 확인·운영 적용과 구분 |

## 착수 확인

- 직접 clean HEAD d45c236을 확인했다. coordinator가 소스/tests293cc093동일·PR11 변경 없음을 확인했고 신규 문서2개만 담당한다. 다른 Worktree 변경/병합은 없다.
- 현재 AGENTS·협업 절차·작업 템플릿·Orca 파견 절차를 적용한다. 공통 API/DB 계약 변경은 없다.
- 새 scratch는 `C:\Users\User\AppData\Roaming\orca\chat-scratch\376ad8c599508167fe6aca0b5c437566\day01-loss-analysis\codex`이다. 허용된 기존150 indexed파일의 byte·SHA를 모두 대조하고 index자체도 before 목록에 추가했다.
- 결과 전 analysis-plan.json에 반시간 entry/exit bin, modeled/accounting holding [0,5)/[5,15)/[15,30)/[30,60)/[60,120)/120분이상, literal exit reason, FIFO·부분매도 원가비례 매칭, A/B stable key, rejected가격 UNKNOWN 정책을 기록했다.
- 기존481ENTRY에는 vwap/previous_vwap/range_high가 있지만 후보 가격·volume 수치는 저장되지 않았다. 승인 BUY signal의 reservation으로부터만 가용 신호가격/금액을 역산하거나 저장된 원래6개 진단값을 쓴다. 이후 실제 추정체결 open을 거절 후보의 신호가격으로 사용하지 않는다. 거절 후보의 가상 손익은 계산하지 않는다.

## 범위 변경과 인계

범위 확대 없음. 표준라이브러리 분석 driver·전체 원장·공식·manifest는 새 scratch에만 기록하고 준비 즉시 coordinator를 통해 Claude에게 인계한다.

## 결과

- [기술 보고서](../../technical/DAY01_LOSS_ANALYSIS.md)에8개 질문별 표를 반영했다. 기존150 outputs와 index151개의 SHA·size가 전후 일치했다. analyze_losses.py는 표준라이브러리 Decimal만 사용하며 engine import/replay/새 조건/테스트/새 데이터 탐색0이다. 실행 `python -B <새 scratch>/analyze_losses.py`,2026-11-12 08:03:51.760065~08:03:54.774583 UTC,exit0/stderr0bytes다.
- A48/B68 full lot와232개 fill, 부분체결0을 매칭했다. 모든 체결의 reference/adverse price·fee/tax 내림·slippage·cash_delta·realized_profit, 최종cash/summary net·21일 daily net이 일치했다. 전체 CSV/JSON에 entry/exit signal·estimated open·observed/settlement·가격·수량·비용·두 시간축의 보유기간·literal reason을 보존했다.
- 비용 bridge는 A reference gross−1,800 → slipped gross−4,917.75 → net−12,012.75, B−2,350 →−9,254.375 →−25,027.375다. 이미 slipped 가격에 들어간 slippage를 이중차감하지 않았다. 기준gross도 음수여서 비용만의 손실이라고 결론내리지 않았다.
- literal STOP A32/B41의 net 합은−26,723.075/−51,858.2, TARGET12/18은+13,204.425/+26,298.175, 시간청산4/9는+1,505.9/+532.65다. AMBIGUOUS_STOP_FIRST literal체결0·미상literal0이며 지연·청산교체 event는 따로 기록해 인과효과로 취급하지 않았다.
- net 승/패16/32와24/44, 평균 이익919.3953/1,228.174·평균 손실절댓값835.0961/1,238.717·표본 payoff1.10095/0.99149·표본 손익분기승률47.5976%/50.2137%는 기술통계이며 조건 최적화나 미래 목표가 아니다.22종목·21일0행과 사전 반시간·두 보유기간 분류를 모두 표로 제공했다.
- stable entry key로 동일38건 net각−5,425.8, 공통 변경0, A전용10건−6,586.95/B전용30건−19,601.575를 분리했다. B−A 차이−13,014.625를 실제 집합 차이로 대조했으며 B가 A전체+20건은 아니었다.481개 동일후보의 실제 status전이도 별도 JSON에 있다.
- 후보 가격은 승인48/68개 예약액 역산과 원래6개 저장 진단만 known이며 나머지 A427/B407은 UNKNOWN이다. 모든 후보의 volume 수치·volume ratio는 UNKNOWN이고 VWAP/range_high는 실제 저장값만 사용했다. 거절 후보의 미래 가상손익을 계산하지 않았다. 가정 유동성·근사VWAP·지연은 신뢰도 제약이지 입증된 손실 인과가 아니다.
- 새 scratch의 manifest·formula·analysis-plan·input integrity·전체 CSV/JSON·findings·late events/associations·A/B비교를 msg_6837d2b5904a로 coordinator에게 즉시 인계했다. Claude 독립 감사가 남았으며 user는 원장 net 합·비용 bridge·literal reasons·공통 key·후보 UNKNOWN 분모와 before/after해시를 대조할 수 있다. 새 실행은 이번 지시 범위가 아니다.
- 구현 개발·소스/tests/API·DB 계약 변경0, 기존 문서/시장원본/산출물 수정0, 새 테스트0, DB/API/계좌/주문/비밀/서버/워커/설치/push/PR/main0이다. 기술 선택은 기존 직렬화 증거를 순수 산술로 분석해 운영 부작용을 막는 방식이며, 장점은 감사 재현성·불변성이고 한계는 기록되지 않은 후보정보·실제 실행자료·OOS 부재다. 배울 개념은 gross와net 비용 bridge, event/관측시각·실제집합/반사실 가상의 구분이다.
- 두 새 문서만 로컬 commit하며 자체 SHA·clean은 완료 보고와 scratch repo-final-state에 기록한다. 신규 PR/원격전송은 없고 통합·배포에는 사용자 별도 승인이 필요하다. 전략은 UNVALIDATED이며 Claude 검토·사용자 확인·운영 적용은 미완료, 실제 미래 수익성·거절가상손익·지연의 인과효과·실행유동성은 미검증이다. GPT 검토 항목은8개 질문의 증거 분모·시간축·비용 이중차감 방지·UNKNOWN 처리·후보/거래 집합 매칭이다. 사용량/비용은 표시되지 않아 unavailable이다.
