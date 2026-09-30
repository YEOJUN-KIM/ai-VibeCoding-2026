# 데이터 저장과 백업 정책

## PostgreSQL

현재 기본 구성은 Windows PostgreSQL 서비스의 `127.0.0.1:5432`, 데이터베이스 `auto_trader`입니다. Windows 시작 시 서비스가 함께 시작하므로 별도 프로젝트 DB 시작 명령은 필요하지 않습니다.

스키마의 단일 기준은 `auto_trader/schema.sql`입니다. 애플리케이션 시작 시 필요한 테이블과 호환 가능한 컬럼을 확인합니다.

### 영구 저장 대상

- 관리자와 인증 세션: `admin_users`, `auth_sessions`
- 관심종목: `favorite_stocks`
- 전략과 대상 종목: `live_strategies`, `live_strategy_symbols`
- 위험 설정과 일별 스냅샷: `risk_settings`, `risk_daily_snapshots`
- 브로커 계좌·주문·이벤트: `broker_accounts`, `live_orders`, `live_order_events`
- 장기 분석·자동 추천·내 후보: `long_term_analyses`, `long_term_recommendations`, `long_term_watchlist`
- 호환 및 향후 확장을 위한 거래 테이블: `stocks`, `accounts`, `positions`, `strategy_runs`, `signals`, `orders`, `executions`, `order_events`, `cash_transactions`

## 메모리와 로컬 파일

현재 PAPER 계좌, 포지션, 자동매매 실행 상태와 당일 계산 상태는 앱 메모리에 있습니다. 서버를 재시작하면 초기화되며 다중 Uvicorn 워커를 사용하면 프로세스별로 상태가 갈라지므로 단일 워커만 사용합니다.

PAPER 주문 감사 기록은 `.paper-history/orders.jsonl`에 남지만 이 파일만으로 계좌 상태를 완전히 복구하지는 않습니다. 외부 API 응답 캐시는 일시 데이터이며 영구 저장 대상으로 보지 않습니다.

## 개발 단계 백업 정책

- 현재는 중요한 사용자 데이터가 거의 없으므로 수동 이관용 `.dump` 파일을 계속 보관하지 않습니다.
- 재구성 기준은 Git에 포함된 `schema.sql`, 코드와 필요한 마이그레이션 스크립트입니다.
- `.env`, 로컬 DB 데이터 폴더, PAPER 감사 기록과 `backups/`는 Git에 포함하지 않습니다.
- 포트 55432의 프로젝트 내부 DB 데이터와 시작·종료·이관 스크립트는 5432 이전 완료 후 제거했습니다.
- 로컬 DB를 다시 만들 때는 Windows PostgreSQL 서비스와 `schema.sql`을 사용합니다.

## 릴리스 전 백업 정책

실사용 데이터가 쌓이기 전 다음을 확정해야 합니다.

1. 자동 백업 주기와 보관 개수
2. 백업 파일의 암호화와 접근 권한
3. 앱과 분리된 저장 위치
4. PostgreSQL 버전이 맞는 환경에서의 정기 복구 시험
5. 복구 시점과 손실 가능한 데이터 범위 안내

백업 생성만 성공한 상태는 완료가 아닙니다. 실제 복구 시험을 통과해야 운영 백업으로 인정합니다.
