# PAPER-FINALIZE-01 결과

판정(push 전 작성): **PASS 후보** — 최종 판정·원격 SHA는 push 후 worker_done 보고에 기록(문서가 자기 SHA를 담지 않음).
상태 구분: AI 구현 완료 ✔ / 자동 테스트 ✔(격리 DB) / 실제 FOLIO 앱 **합성** 종단 검증 ✔ / 사용자 직접 확인 ✖(미실시) / 운영 적용 ✖(해당 없음).

## 독립 Codex 검토 (공식 Orca Codex worker, 읽기 전용, 구현자와 분리)
| 회차 | 대상 | 결과 |
|---|---|---|
| 초기 | 4c9a6b8 | P0 0 / P1 4 / P2 3 (인증·CSRF 우회 없음) |
| 재검토 1 | 5b5e4eb | P1-1,3 해소, P1-2 부분(수동 매도 원가), P1-4 부분(자격정보 이관) → P1 2 |
| 재검토 2 | 43e8463 | P1-A,B 해소, 편입 기준가 원가 오용 P1-C 1 |
| 재검토 3 | ef889a7 | P1-C 해소, 구버전 저장 장부 P1-D 1 |
| 최종 | 2baedd2 | **남은 P0/P1: 없음** |

### P1 4건과 수정
1. 계좌 전환 ↔ 제어 요청 경합(api/paper_mvp.py): lock 획득 후 선택 계좌 재검증, 달라졌으면 409 (`locked()`, `account_is_current`).
2. 혼합 보유(수동+자동) 매도 원가·실현손익 불일치(paper.py `_sale_cost_per_share`): 정확한 원가는 AUTO lot만, 나머지는 pool 원가 공유. 매도 원가=잔여 원가 차감분. 일관성 깨진 구버전 저장 장부는 기존 혼합평균 방식으로 기록(음수 원가 불가, 마이그레이션 없음).
3. 평가손실로 총자산<저장 예산이면 status/stop 500(paper_mvp.py): status는 `strict=False`, 저장·시작 검증은 strict 유지.
4. 격리 모드가 LOCAL 증권 연결·자격정보 이관을 우회(user_connections.py, connection_vault.py): 격리 모드에서 저장 연결/vault 읽기 None, 쓰기 예외, 이관 즉시 return.

### P2 (미수정, 기록만)
- P2-1 격리 엔진 가상 시계와 broker.now 불일치(주문 created_at·위험 일자는 실제 날짜) — 합성 검증 한정.
- P2-2 가상 시계 진행이 owner 현재 엔진만 대상(타 사용자·보관 세션 엔진 미진행) — 합성 한정.
- P2-3 상태 조회 실패 시 UI가 직전 정상 상태 유지(실행 중 라벨) — 화면 문구/상태 개선.

## 변경 파일 (기준 4c9a6b8 대비)
auto_trader/api/paper_mvp.py, auto_trader/paper.py, auto_trader/paper_mvp.py, auto_trader/user_connections.py, auto_trader/connection_vault.py, tests/test_paper_finalize01.py(신규 12건), scripts/paper_finalize_e2e.py(신규), docs/plans/ai-tasks/PAPER-FINALIZE-01.md·-result.md, docs/previews/paper-finalize-01/(스크린샷 2·e2e 결과 JSON). 기존 docs/previews/paper-app-ui-01/ 불변. API 경로·응답 계약 변경 없음(409 응답 사례 추가), DB 스키마 변경 없음.
충돌 가능 파일: auto_trader/paper.py, paper_mvp.py, user_connections.py (타 AI 작업 브랜치와 중복 확인 안 됨, 열린 PR은 전부 문서/UI).

## 재현 테스트 (수정 전 실패 → 수정 후 통과)
- tests/test_paper_finalize01.py 12건: 계좌 전환 경합(수정 전 `locked` 부재 오류), 혼합 매도 원가, 수동 선매도, 편입 후 분할 매도(음수 원가), 구버전 장부, status 후 손실, 격리 시 vault·DB 무접근. 수정 전 코드에서 각 회차마다 해당 테스트 실패를 확인.

## 실행한 검증 (모두 격리 테스트 DB folio_mvp_test @127.0.0.1:55432, Toss/운영 키 없음, `python -m unittest`)
최종 코드(2baedd2) 기준: test_paper_finalize01 12건 OK / test_paper_app_ui01 4 / test_paper_mvp 23 / test_paper_safety01 13 / test_paper_safety02 35 / test_paper_settings01 14 / test_auth 14 / test_application_composition 2 / test_paper 23 / test_paper_accounts 5 / test_paper_management 9 / test_paper_feed 4 / test_paper_market_hours 8 / test_manual_limits 3 / test_live_orders 5 / test_live_order_service 8 — 전부 종료코드 0. test_architecture_regression: **최초 실패**(`ModuleNotFoundError: refactor_scenarios`, 종료코드 1, 1건 오류) = 실행 방법 문제(tests 디렉터리가 import 경로에 없음), 코드 결함 아님 → `PYTHONPATH=tests`로 재실행 5건 OK(코드 0). JS: test_paper_status/records/risk.js 종료코드 0.
앱 종단(실제 auto_trader.main:app, 127.0.0.1:8766 및 신규 DB 127.0.0.1:8767, FOLIO_MVP_ISOLATED=1, 합성 시계/시장): scripts/paper_finalize_e2e.py **21/21 PASS**(로그인→/paper-mvp→계좌준비→설정 저장→CSRF 거부→시작→합성 Scout 가상 BUY/SELL→status=/account=/orders, 현금·총자산·손익 항등식→정지→로그아웃→401→재로그인 상태 유지). scripts/paper_app_ui_e2e.py 신규 DB **23/23 PASS**(비로그인 401/303, CSRF 403, start_guard 409). 첫 앱에서의 동일 스크립트는 21/23 — 12·14번 실패는 이미 계좌가 준비된 상태에서 가정이 어긋난 **환경 순서 문제**(코드 결함 아님), 신규 DB로 재실행 23/23.
초기 환경 문제: 첫 로그인 401은 테스트 DB 관리자 계정/비밀번호 불일치 → 테스트 DB의 관리자 해시를 임시 비밀번호로 재설정(테스트 DB 한정); 비밀번호 20자 제한 및 e2e 도우미의 빈 응답(204) JSON 파싱 오류를 수정(스크립트 한정).
브라우저(Orca): /paper-mvp 홈·투자 내역 캡처, 'PAPER·합성 데이터' 배지, 총자산 10,154,015원·실현손익 +154,015원이 API(10,154,014.9 / 154,014.9)와 일치.

## 미실행
전체 suite, 실제 시세/장 시간, 실제 Toss·LIVE, 운영 DB·서버, 모바일, 사용자 직접 확인, 합성 환경 밖 동작.

## 정리
컨테이너 folio-mvp-app-test-db(ID 02438232fa51…, postgres:latest, 127.0.0.1:55432→5432, DB folio_mvp_test(+folio_mvp_test_guard 이번 작업에서 생성), 볼륨/바인드 없음·tmpfs, 이전 PAPER-APP-UI-01 검증용 일회성)만 stop/rm. 전후 비교: my-postgres(Exited), tower-preview-tunnel-1, tower-preview-game-1 불변, 볼륨·이미지·prune 없음. 격리 앱 프로세스 2개(포트 8766/8767)는 종료.

## 사용자 직접 확인 절차 (미실시)
격리 앱 환경 필요(테스트 DB·FOLIO_MVP_ISOLATED=1, 승인 필요). `/paper-mvp`에서 실험계좌 선택 → 투자 설정 저장 → 시작 → 합성 데이터 배지 확인 → 정지. 실제 시세·실거래는 이 검증의 범위 밖.

## GPT 검토 사항
paper.py 원가 분리 정책(AUTO lot만 정확 원가, 나머지 pool, 구버전 불일치 장부는 혼합평균), account_is_current 방식, P2 3건 재분류 여부.
핵심 개념: (1) 락 획득 전에 고른 대상의 재검증(TOCTOU), (2) 원가 기준(청산 기준가)과 회계 원가의 분리.
