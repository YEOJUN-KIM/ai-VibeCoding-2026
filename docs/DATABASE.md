# 데이터 저장과 백업 정책

## PostgreSQL

현재 기본 구성은 Windows PostgreSQL 서비스의 `127.0.0.1:5432`, 데이터베이스 `auto_trader`입니다. Windows 시작 시 서비스가 함께 시작하므로 별도 프로젝트 DB 시작 명령은 필요하지 않습니다.

스키마의 단일 기준은 `auto_trader/schema.sql`입니다. 애플리케이션 시작 시 필요한 테이블과 호환 가능한 컬럼을 확인합니다.

### 영구 저장 대상

- 관리자와 인증 세션: `admin_users`, `auth_sessions`
- 관심종목: `favorite_stocks`
- 두 PAPER 계좌의 잔액·보유수량·매입단가·시작 자산·비용·주문 상태·관리 범위·자동매수분의 전략/수량/청산 조건: `paper_account_state` (계좌별 저장)
- 전략과 대상 종목: `live_strategies`, `live_strategy_symbols`
- 기본 프리셋 주간 후보 데이터·주간 기준일·생성 시각: `strategy_preset_market`. 현재 주간 데이터 한 건을 유지하며, 사용자 저장 전략과 분리합니다. 갱신·별도 저장 방식은 [PAPER_STRATEGY.md](PAPER_STRATEGY.md)를 참고합니다.
- 위험 설정과 일별 스냅샷: `risk_settings`, `risk_daily_snapshots`
- 브로커 계좌·주문·이벤트: `broker_accounts`, `live_orders`, `live_order_events`
- 장기 분석·자동 추천·내 후보: `long_term_analyses`, `long_term_recommendations`, `long_term_watchlist`
- 호환 및 향후 확장을 위한 거래 테이블: `stocks`, `accounts`, `positions`, `strategy_runs`, `signals`, `orders`, `executions`, `order_events`, `cash_transactions`

## 메모리와 로컬 파일

PAPER 자산과 주문 상태는 변경할 때 DB에 저장하고 재시작 시 복구합니다. 초기화 버튼은 마지막 시작 자산으로 되돌립니다. 실제 자산 다시 복사는 새 시작 자산을 만듭니다. 자동매매 실행 상태와 전략 성과의 기준선은 메모리에 있으며 재시작 후 전략을 다시 선택해 실행합니다. 다중 워커 간 자산 변경 조정은 지원하지 않으므로 단일 워커만 사용합니다.

PAPER 주문 감사 기록은 `.paper-history/orders.jsonl`에 남지만 이 파일만으로 계좌 상태를 완전히 복구하지는 않습니다. 일반 외부 API 응답 캐시는 일시 데이터입니다. 기본 프리셋의 주간 후보 데이터는 같은 주의 일관성을 유지하기 위해 별도로 PostgreSQL에 보관합니다.

## 백업과 복원 확인

프로젝트 폴더에서 실행합니다.

```powershell
python backup_database.py
python backup_database.py --verify
```

첫 명령은 `.backups/`에 PostgreSQL custom-format 백업과 검증 정보 JSON을 만듭니다. 두 번째 명령은 별도 임시 PostgreSQL 인스턴스에 복원하고 public 스키마의 모든 테이블 행 수와 내용 SHA-256을 원본 스냅샷과 비교합니다. 원본을 변경하지 않으며 검증용 인스턴스는 종료·제거합니다. Windows PostgreSQL 18 도구가 필요합니다.

실행 중 앱이 데이터를 갱신해도 일관된 백업이 되도록 pg_export_snapshot을 공유합니다. 복원 시험은 데이터 내용 검증이며 원래 계정 권한이나 서비스 전체 재설정 시험은 아닙니다.

2026-10-01 실제 백업을 별도 인스턴스에 복원해 22개 테이블의 내용 일치를 확인했습니다.

## 운영 절차

- 관찰 목록과 전략을 변경한 날, 앱 업데이트 전에 백업합니다. 정기 자동 실행은 아직 설정하지 않았습니다.
- 백업에는 계정·세션과 거래 정보가 들어 있으므로 개인 저장소에서 관리합니다. `.backups/`는 Git에서 제외됩니다.
- 최근 7개 일별 백업과 4개 주별 백업을 보관하는 것을 기준으로 합니다. 현재 도구는 기존 백업을 자동 삭제하지 않습니다.
- `.env`와 `.paper-history/orders.jsonl`은 DB 백업에 포함되지 않으므로 따로 보관합니다. 모의계좌 자산은 DB 백업에 포함되며 자동매매 실행 상태는 복원하지 않습니다.
- 로컬 디스크 손실에도 대비하려면 백업을 개인 외부 저장소에도 복사해야 합니다. 외부 복사와 암호화는 아직 설정하지 않았습니다.

실제 장애 복구 시에는 앱을 정지하고, 백업을 새 DB에 `pg_restore --exit-on-error --no-owner --no-privileges`로 복원합니다. `.env`의 POSTGRES_DB를 새 DB로 연결한 뒤 계정·전략·관찰 목록을 확인합니다. 기존 DB는 확인이 끝날 때까지 유지합니다. 사용자 역할과 권한은 복구 환경에서 별도 설정합니다.

## PAPER 관리 정보

`paper_account_state.state`에는 `management_scope`와 `auto_lots`를 함께 저장합니다. 자동매수분별 전략 ID·이름·수량·단가·취득 시각·청산 조건을 보존하며 새 테이블을 추가하지 않습니다. 기존 저장 상태에 해당 키가 없으면 AUTO 범위와 빈 자동매수 기록으로 복구합니다. 예전 주문 이력만 보고 소유 전략을 추정하지 않습니다.

화면에서 표시하는 계좌와 실행 중인 워커는 별개입니다. 각 계좌의 워커·선택 전략은 프로세스 메모리에 보관하며, 계좌 전환으로 워커를 종료하지 않습니다. 최근 선택한 설정 전략 목록은 사용자별 브라우저 localStorage에 저장합니다.
