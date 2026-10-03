# 데이터 저장과 백업 정책

기준일: 2026-10-04

## PostgreSQL

현재 기본 구성은 Windows PostgreSQL 서비스의 `127.0.0.1:5432`, 데이터베이스 `auto_trader`입니다. 서비스 자동 시작 여부는 Windows 설정에 따릅니다. `FOLIO.cmd`는 연결을 확인하고 꺼진 로컬 서비스를 시작하며, 최초 역할·DB 생성은 설치 안내를 따릅니다.

스키마의 단일 기준은 `auto_trader/schema.sql`입니다. 애플리케이션 시작 시 필요한 테이블과 호환 가능한 컬럼을 확인합니다. 2026-10-03 현재 DB의 public 테이블 34개를 확인했습니다. 테이블 수 확인은 백업·복원 검증을 뜻하지 않습니다.

### 영구 저장 대상

- 관리자와 인증 세션: `admin_users`, `auth_sessions`
- 최초 관리자 연결 귀속: `application_owner`
- 사용자별 증권 연결의 소유자·출처·계좌 참조·가린 번호·중복 판별값: `user_broker_connections`. API 키·Secret·암호화된 키를 보관하는 컬럼은 없습니다.
- 모의계좌 소유자와 마지막 선택 계좌: `accounts.user_id`, `user_paper_preferences`
- 실제 자산 관측 이력: `live_asset_history`. 사용자·계좌별 분당 관측을 저장하고 날짜별 마지막 관측을 차트에 사용합니다.
- 관심종목: `favorite_stocks`
- 두 PAPER 계좌의 잔액·보유수량·매입단가·시작 자산·비용·주문 상태·관리 범위·자동매수분의 전략/수량/청산 조건·마지막 선택 전략: `paper_account_state` (계좌별 저장)
- 전략과 대상 종목: `live_strategies`, `live_strategy_symbols`
- 기본 프리셋 주간 후보 데이터·주간 기준일·생성 시각: `strategy_preset_market`. 현재 주간 데이터 한 건을 유지하며, 사용자 저장 전략과 분리합니다. 갱신·별도 저장 방식은 [PAPER_STRATEGY.md](../specs/PAPER_STRATEGY.md)를 참고합니다.
- 위험 설정과 일별 스냅샷: `risk_settings`, `risk_daily_snapshots`
- 브로커 계좌·주문·이벤트: `broker_accounts`, `live_orders`, `live_order_events`
- 장기 분석·자동 추천·저장한 기업·개인 메모·저장 해제 상태: `long_term_analyses`, `long_term_recommendations`, `long_term_watchlist`, `long_term_watch_notes`, `long_term_watch_exclusions`
- ML 원본 종목·시장 봉, 수집 실행, 거시 지표, 봉별 PAPER 판단, 품질 보고서: `ml_raw_candles`, `ml_market_indicator_candles`, `ml_collection_runs`, `ml_macro_observations`, `ml_strategy_decisions`, `ml_data_quality_reports`. 세부 저장·검증 기준은 [머신러닝 구현 현황](../machine-learning/CURRENT_PROGRESS.md)을 참고합니다.
- PAPER 계좌 식별·소유자와 실행·거래 기록 및 호환 테이블: `stocks`, `accounts`, `positions`, `strategy_runs`, `signals`, `orders`, `executions`, `order_events`, `cash_transactions`. 현재 PAPER 복원의 주 저장소는 `paper_account_state`입니다.

## 메모리와 로컬 파일

PAPER 자산과 주문 상태는 변경할 때 DB에 저장하고 재시작 시 복구합니다. 초기화 버튼은 마지막 시작 자산으로 되돌립니다. 실제 자산 다시 복사는 새 시작 자산을 만듭니다. 마지막 선택 전략은 DB에서 복원해 미리 선택합니다. 자동매매 실행 상태와 전략 성과 기준선·화면의 최신 판단은 메모리에 있어 재시작 시 초기화되며, 사용자가 전략 시작을 눌러 재개합니다. 학습용 판단 데이터는 별도 DB 기록입니다. 다중 워커 간 자산 변경 조정은 지원하지 않으므로 단일 워커만 사용합니다.

PAPER 주문 감사 기록은 기존 관리자 모의계좌의 `.paper-history/orders.jsonl` 또는 `.paper-history/user-{user_id}/{live_copy|experiment}.jsonl`에 남지만 이 파일만으로 계좌 상태를 완전히 복구하지는 않습니다. 일반 외부 API 응답 캐시는 일시 데이터입니다. 기본 프리셋의 주간 후보 데이터는 같은 주의 일관성을 유지하기 위해 별도로 PostgreSQL에 보관합니다.

## API 키와 로컬 설정

- 기존 환경 설정의 관리자 키는 `.env`를 유지합니다. 해당 연결을 다른 사용자에게 상속하지 않습니다.
- 설정 팝업에서 등록·변경한 키는 `.local-secrets/broker-{user_id}.bin`에 Windows DPAPI로 암호화해 저장합니다. 파일 교체는 임시 파일 쓰기 후 수행하며 저장 실패 시 DB의 연결 메타데이터를 되돌립니다.
- API 키·Secret은 DB, 조회 응답, Git에 포함하지 않습니다. `.env`와 `.local-secrets/`는 Git에서 제외됩니다. DB에는 연결 소유 관계와 계좌 정보만 남습니다.
- 이전 버전의 `encrypted_credentials` 컬럼이 있으면 파일로 이전한 뒤 컬럼·관련 제약을 제거합니다. 실제 DB에 이전할 키는 없었으며 컬럼 제거를 확인했습니다.
- 암호화 파일은 Windows 사용자·컴퓨터 환경에 의존합니다. 다른 환경에서는 키를 재등록해야 합니다. 새 설치용 사용자 설정 폴더와 복구 화면은 배포 설계 시 별도로 정합니다.

## 백업과 복원 확인

프로젝트 폴더에서 실행합니다.

```powershell
python backup_database.py
python backup_database.py --verify
```

첫 명령은 `.backups/`에 PostgreSQL custom-format 백업과 검증 정보 JSON을 만듭니다. 두 번째 명령은 별도 임시 PostgreSQL 인스턴스에 복원하고 public 스키마의 모든 테이블 행 수와 내용 SHA-256을 원본 스냅샷과 비교합니다. 원본을 변경하지 않으며 검증용 인스턴스는 종료·제거합니다. Windows PostgreSQL 18 도구가 필요합니다.

실행 중 앱이 데이터를 갱신해도 일관된 백업이 되도록 pg_export_snapshot을 공유합니다. 복원 시험은 데이터 내용 검증이며 원래 계정 권한이나 서비스 전체 재설정 시험은 아닙니다.

2026-10-01 실제 백업을 별도 인스턴스에 복원해 22개 테이블의 내용 일치를 확인했습니다.

2026-10-03 최신 백업 `postgres-20261003-182421-7e57257f.dump`를 별도 임시 PostgreSQL 인스턴스에 복원해 public 테이블 34개의 행 수와 내용 해시가 원본 스냅샷과 일치함을 확인했습니다. 모의계좌 2개·전략 3개·실제 주문 2건과 학습용 기록을 포함합니다. 이는 다른 PC의 설치·권한·API 키 재등록 검증을 뜻하지 않습니다. 해당 백업과 검증 JSON, 본컴 이전 안내는 Git에서 제외되는 `.backups/`에 보관합니다.

## 운영 절차

- 저장한 기업 목록과 전략을 변경한 날, 앱 업데이트 전에 백업합니다. 정기 자동 실행은 아직 설정하지 않았습니다.
- 백업에는 계정·세션과 거래 정보가 들어 있으므로 개인 저장소에서 관리합니다. `.backups/`는 Git에서 제외됩니다.
- 최근 7개 일별 백업과 4개 주별 백업을 보관하는 것을 기준으로 합니다. 현재 도구는 기존 백업을 자동 삭제하지 않습니다.
- `.env`, `.local-secrets/`, `.paper-history/`는 DB 백업에 포함되지 않으므로 별도로 보관합니다. 키 파일 복사만으로 다른 컴퓨터에서 복호화할 수 있다고 가정하지 않습니다. 모의계좌 자산은 DB 백업에 포함되며 자동매매 실행 상태는 복원하지 않습니다.
- 로컬 디스크 손실에도 대비하려면 백업을 개인 외부 저장소에도 복사해야 합니다. 외부 복사와 암호화는 아직 설정하지 않았습니다.

실제 장애 복구 시에는 앱을 정지하고, 백업을 새 DB에 `pg_restore --exit-on-error --no-owner --no-privileges`로 복원합니다. `.env`의 POSTGRES_DB를 새 DB로 연결한 뒤 계정·전략·저장한 기업 목록을 확인합니다. 기존 DB는 확인이 끝날 때까지 유지합니다. 사용자 역할과 권한은 복구 환경에서 별도 설정합니다.

## PAPER 관리 정보

`paper_account_state.state`에는 `management_scope`, `auto_lots`, `last_strategy_selection`을 함께 저장합니다. 자동매수분별 전략 ID·이름·수량·단가·취득 시각·청산 조건을 보존하며 새 테이블을 추가하지 않습니다. 기존 저장 상태에 해당 키가 없으면 AUTO 범위·빈 자동매수 기록·선택 전략 없음으로 복구합니다. 예전 주문 이력만 보고 소유 전략을 추정하지 않습니다.

화면에서 표시하는 계좌와 실행 중인 워커는 별개입니다. 각 계좌 워커는 프로세스 메모리에 보관하며 계좌 전환으로 종료하지 않습니다. 마지막 선택은 `last_strategy_selection`의 사용자 ID·전략 ID·대상 종목으로 저장합니다. 복구 시 해당 사용자의 저장 전략을 조회해 DRY RUN 여부와 종목 복구 가능 여부를 확인한 뒤 엔진을 설정하며, 자동 실행하지 않습니다.

설정 화면의 최근 선택 전략 목록은 사용자별 브라우저 localStorage에 저장하며 PAPER의 계좌별 마지막 전략과는 별개입니다. 내 자산 가림 선택도 브라우저에 저장하지만 실제 자산 값은 localStorage에 저장하지 않습니다.

현재 증권 클라이언트·PAPER 계좌·실행 상태·위험 한도는 사용자별로 분리되어 있습니다. 기존 `paper-default`·`paper-experiment`는 최초 관리자에게 귀속하고 추가 사용자 계좌는 사용자 ID를 포함한 이름으로 생성합니다. 마지막 선택 계좌는 `user_paper_preferences`, 각 계좌의 마지막 전략은 `paper_account_state`에 저장합니다. 공개 다중 사용자 서비스·여러 실제 계좌 전환은 지원하지 않습니다.
