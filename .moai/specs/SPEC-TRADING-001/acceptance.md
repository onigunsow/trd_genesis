# Acceptance — SPEC-TRADING-001 (v0.2.0)

상위 SPEC: `spec.md` (v0.2.0). 본 문서는 M1~M5 마일스톤별 인수 시나리오를 Given-When-Then 형식으로 정의한다. 본 SPEC 범위는 **M1~M5 (모의 자동 매매)** 까지이며, M6 (실거래) 시나리오는 본 문서에 포함하지 않는다 (단, M2의 live-block 안전장치 시나리오는 포함).

v0.2.0 갱신: 자본 손실 직결 결함 5종 + 운영 결함 4종 + 위생 결함 3종에 대응하는 보강 시나리오 추가.

품질 게이트 공통 (모든 마일스톤):
- 테스트 커버리지 ≥ 85%
- **한도 / 회로차단 / 트랜잭션 모듈은 100% 커버리지**
- ruff + mypy strict clean
- gitleaks pre-commit clean

---

## M1 — Infrastructure & Security & Quality Gates

### M1-S1. Compose 기동 + healthcheck 통과
- **Given** `~/trading/` 디렉토리에 `compose.yaml`, `Dockerfile`, `.env` (perm 600), `pyproject.toml`이 준비되어 있다
- **When** `docker compose up -d`를 실행한다
- **Then** 60초 이내에 `app`과 `postgres` 두 서비스 모두 healthy 상태가 되며, `docker compose exec app python -m trading.healthcheck`가 exit code 0을 반환한다

### M1-S2. .env 권한 600 강제
- **Given** `.env` 파일이 존재한다
- **When** `stat -c '%a' .env`를 실행한다
- **Then** 출력은 정확히 `600`이다. 권한이 다르면 `healthcheck.py`가 실패한다

### M1-S3. backup.sh 산출물 생성·검증
- **Given** postgres 컨테이너가 가동 중이다
- **When** `./backup.sh`를 실행한다
- **Then** `backups/{timestamp}/` 디렉토리에 `pg_dump.gz`, `tar.gz` 산출물이 생성되고, retention 30개 정책으로 오래된 백업이 자동 정리된다. `BACKUP_KEEP=7 ./backup.sh` 실행 시 7개만 유지된다

### M1-S4. live_unlocked 초기 false
- **Given** DB 첫 마이그레이션이 막 완료되었다
- **When** `SELECT live_unlocked FROM system_state;`를 실행한다
- **Then** 결과는 정확히 `false`이다

### M1-S5. 외부 포트 미노출
- **Given** compose 스택이 가동 중이다
- **When** `docker compose ps`로 노출 포트를 확인한다
- **Then** postgres는 어떤 호스트 포트도 바인딩하지 않으며, app도 외부 포트 노출이 없다

### M1-S6. 비-root 컨테이너
- **Given** app 컨테이너가 가동 중이다
- **When** `docker compose exec app id`를 실행한다
- **Then** UID/GID가 `1000:1000`이며 root가 아니다

### M1-S7. gitleaks pre-commit 차단 (v0.2.0 신규)
- **Given** `.pre-commit-config.yaml`에 gitleaks 훅이 등록되어 있다
- **When** 개발자가 `ANTHROPIC_API_KEY=sk-ant-...` 패턴이 포함된 파일을 커밋하려 시도한다
- **Then** 커밋이 차단되고 gitleaks 경고가 출력된다. `KIS_*`, `KRX_*`, `TELEGRAM_*` 패턴도 동일하게 차단된다

### M1-S8. 테스트 커버리지 게이트 (v0.2.0 신규)
- **Given** CI 파이프라인이 가동 중이다
- **When** `pytest --cov`를 실행한다
- **Then** 전체 커버리지 ≥ 85%이고, `src/trading/risk/limits.py`, `src/trading/risk/circuit_breaker.py`, `src/trading/db/transactions.py` 모듈은 정확히 100%이다. 미달 시 빌드 실패

### M1-S9. 시크릿 마스킹 운영 (v0.2.0 신규)
- **Given** 운영 문서에 시크릿 검증 절차가 정의되어 있다
- **When** 운영자가 `[ -n "$KRX_PW" ] && echo "KRX_PW: present"` 패턴을 실행한다
- **Then** 출력은 `KRX_PW: present` 또는 빈 출력 (변수 부재 시) 이며, 평문 비밀번호는 절대 출력되지 않는다

---

## M2 — KIS API Integration & Order Audit & Trade Safety

### M2-S1. paper 1주 매수 + DB 영속 + Telegram 브리핑
- **Given** `TRADING_MODE=paper`, `live_unlocked=false`, KIS_PAPER_* 시크릿이 유효하다
- **When** `docker compose exec app trading paper-buy --ticker 005930 --qty 1`을 실행한다
- **Then**
  - KIS paper 엔드포인트로 매수 주문이 제출되고 응답이 성공이다
  - `orders` 테이블에 행이 1개 추가되고 `audit_log`에도 기록된다
  - 5초 이내에 `TELEGRAM_CHAT_ID=60443392`로 매매 브리핑 메시지가 도착하며, 메시지에 종목/방향/수량/체결가/수수료 + **자산현황 갱신 (총자산/현금%/주식%)** 이 포함된다

### M2-S2. live mode + live_unlocked=false → 주문 차단
- **Given** `TRADING_MODE=live`, `live_unlocked=false`
- **When** 어떤 경로로든 주문 진입점이 호출된다
- **Then** 주문은 KIS에 전송되지 **않고**, `LIVE_LOCKED` 사유의 예외가 발생하며, `audit_log`에 차단 기록이 남고, Telegram에 차단 알림이 발송된다

### M2-S3. KIS 토큰 캐싱 (1분 재발급 제한 대응)
- **Given** `kis/auth.py`로 첫 토큰을 발급받은 직후 (10초 경과 가정)
- **When** 30초 이내에 다시 토큰을 요청한다
- **Then** 외부 KIS `/oauth2/tokenP` 호출 없이 캐시된 토큰을 반환한다 (HTTP mock으로 외부 호출 0회 검증)

### M2-S4. TRADING_MODE 변경 audit_log 기록
- **Given** 시스템이 `TRADING_MODE=paper`로 가동 중이다
- **When** 운영자가 명시적 절차로 `TRADING_MODE=live`로 전환한다
- **Then** `audit_log`에 `mode_change` 이벤트가 기록되며, 행에는 old, new, operator, timestamp가 포함된다. `live_unlocked`는 여전히 `false`이므로 주문은 차단된다

### M2-S5. KIS 5xx retry + fail-fast (v0.2.0 신규)
- **Given** KIS REST 호출 mock이 5xx 응답을 3회 반환한다
- **When** 주문 제출이 시도된다
- **Then** tenacity가 max 4회까지 retry한 뒤 fail-fast하며, `audit_log`에 실패 기록이 남고 즉시 텔레그램 알림이 발송된다. silent_mode=true 상태에서도 발송된다

### M2-S6. 트랜잭션 atomicity (v0.2.0 신규)
- **Given** orders + audit_log + positions 동시 INSERT/UPDATE 중 audit_log 단계에서 의도적으로 예외 발생
- **When** 트랜잭션이 실행된다
- **Then** 세 테이블 모두 롤백되어 변경이 0건이다. 부분 성공은 발생하지 않는다

### M2-S7. 시그널 멱등성 (UNIQUE 제약, v0.2.0 신규)
- **Given** 같은 `kis_order_no`로 두 번의 주문 INSERT 시도가 발생한다
- **When** 두 번째 INSERT가 실행된다
- **Then** Postgres UNIQUE 제약 위반으로 거부되며, audit_log에 중복 시그널 기록이 남고 텔레그램 알림이 발송된다. 매매는 1회만 실행된다

### M2-S8. 거래정지 종목 사전 차단 (v0.2.0 신규)
- **Given** Decision 페르소나가 거래정지 종목 매수 시그널을 냈다
- **When** Risk APPROVE 후 KIS 종목정보 조회를 사전 검증한다
- **Then** 주문은 KIS에 전송되지 않고, `audit_log`에 사유 `HALTED_TICKER`로 기록되며 텔레그램에 차단 알림이 발송된다. 관리종목/투자위험/상하한가 도달 종목도 동일하게 차단

### M2-S9. 매수가능금액 동시성 (v0.2.0 신규)
- **Given** 매수가능금액 1,000,000원이고 KIS `nrcvb_buy_amt` (미체결 매수금) 800,000원이 존재한다
- **When** 250,000원 매수 주문이 시도된다
- **Then** 가용 잔액 200,000원 (1,000,000 - 800,000) 이 250,000원보다 작으므로 사전 차단된다. advisory lock으로 동시 주문도 직렬화

---

## M3 — Market Data & Benchmark Backtesting

### M3-S1. 어댑터 fetch + 2019-01-01 백필 + idempotent
- **Given** `ohlcv` 테이블이 비어 있다
- **When** `pykrx_adapter.fetch(symbol='005930', start='2019-01-01', end='2026-05-02')`를 실행한다
- **Then** 약 1,800개 거래일 행이 `ohlcv`에 upsert된다
- **And When** 같은 호출을 다시 실행한다
- **Then** 외부 호출 0회, DB 행 수 변동 없음

### M3-S2. SMA cross 백테스트가 CAGR/MDD/Sharpe 산출
- **Given** 2019-01-01부터 KOSPI200 종목의 OHLCV가 백필되어 있다
- **When** `trading run-strategy --name sma_cross --symbol 005930 --from 2019-01-01 --to 2024-12-31`을 실행한다
- **Then** 결과 객체에 CAGR, MDD, Sharpe, trade ledger가 포함되며 DB `benchmark_runs`에 1행 기록된다

### M3-S3. dual momentum 벤치마크 산출
- **Given** 워치리스트 OHLCV가 캐시되어 있다
- **When** `trading run-strategy --name dual_momentum`을 실행한다
- **Then** CAGR/MDD/Sharpe 산출이 완료되고, 결과는 추후 M5 평가 리포트에서 페르소나 forward 매매와 비교 가능한 형식이다

### M3-S4. KRX 로그인 graceful degradation (v0.2.0 신규)
- **Given** `KRX_ID`/`KRX_PW`가 잘못 설정되어 KRX 홈페이지 로그인이 실패한다
- **When** 일일 데이터 적재가 실행된다
- **Then** OHLCV 적재는 정상 작동하며, fundamentals/flows 적재는 graceful skip된다. 경고 로그와 텔레그램 1회 알림이 발송된다. 시스템 가동은 계속된다

### M3-S5. fundamentals/flows 캐시 + 페르소나 input (v0.2.0 신규)
- **Given** 워치리스트 종목 005930에 대해 일일 PER/PBR/EPS/BPS/DIV/DPS/시가총액 + 외국인/기관/개인 매매가 `fundamentals`/`flows` 테이블에 캐시되어 있다
- **When** Micro persona가 호출된다
- **Then** persona prompt에 fundamentals/flows 값이 자동 주입되며 `persona_runs.prompt`에서 확인 가능하다

### M3-S6. FRED 5종 거시 input (v0.2.0 신규)
- **Given** RRPONTSYD, BAMLH0A0HYM2, DCOILWTICO, STLFSI4, DTWEXBGS 5종이 `macro_indicators`에 캐시되어 있다
- **When** Macro persona (Opus 4.7) 가 호출된다
- **Then** persona prompt에 5종 시계열이 모두 포함되며 `persona_runs.prompt`에서 확인 가능하다

---

## M4 — 5-Persona Intraday System & Telegram Briefing & Paper Auto-Trading

### M4-S1. Macro 페르소나 금요일 17:00 가동 + 7일 캐시
- **Given** 금요일 17:00 KST가 도래했다
- **When** scheduler가 Macro 페르소나(Opus 4.7)를 호출한다
- **Then** `persona_runs`에 1행 기록, Telegram 브리핑 5초 이내 발송, 응답이 7일간 캐시되어 다음 금요일까지 Decision 페르소나가 참조 가능하다

### M4-S2. Pre-market 07:30 시퀀스
- **Given** 영업일 07:30 KST가 도래했고 Macro 캐시가 유효하다
- **When** scheduler가 Pre-market 시퀀스를 시작한다
- **Then** Micro(07:30 풀 분석) → Decision(07:50) → Risk(08:00) 순서로 호출되며, 각 페르소나마다 `persona_runs` 기록 + Telegram 브리핑 5초 이내 발송이 수행된다

### M4-S3. 장중 정기 사이클 (Micro 캐시 재사용)
- **Given** 09:30, 11:00, 13:30, 14:30 KST 중 하나의 시각이다 (영업일)
- **When** scheduler가 장중 사이클을 시작한다
- **Then** Micro는 호출되지 **않고** 직전 캐시가 재사용되며, Decision + Risk만 호출된다

### M4-S4. 이벤트 트리거 ±3% (보유 종목)
- **Given** 보유 종목 005930이 있고 장중 시간대(09:00~15:30)이다
- **When** 현재가가 직전 종가 대비 +3%를 돌파한다
- **Then** 60초 이내에 Decision 페르소나가 트리거 컨텍스트와 함께 호출되고, Risk → 코드 룰 → 매매 파이프라인이 완료된다

### M4-S5. DART 신규 공시 트리거
- **Given** 보유 또는 워치리스트 종목에 신규 DART 공시가 도착했다
- **When** dart_adapter polling이 신규 공시를 감지한다
- **Then** 60초 이내에 Decision 페르소나가 호출된다

### M4-S6. VIX/USD-KRW 트리거
- **Given** VIX가 +15% 또는 USD/KRW가 ±1% 이동했다
- **When** events 모니터가 변동을 감지한다
- **Then** 60초 이내에 Decision 페르소나가 호출된다

### M4-S7. 페르소나 응답 영구 기록 (감사)
- **Given** 임의의 페르소나가 호출되었다
- **When** 응답이 반환된다
- **Then** `persona_runs` 행에 다음 컬럼이 모두 채워진다: `persona_name`, `model`, `prompt`, `response`, `input_tokens`, `output_tokens`, `cost_krw`, `latency_ms`, `timestamp`, `trigger_context`. 누락 시 acceptance 실패

### M4-S8. 페르소나 응답 Telegram 브리핑 5초 SLA
- **Given** 페르소나 호출이 완료되었다
- **When** 응답 시각으로부터 5초가 경과한다
- **Then** `TELEGRAM_CHAT_ID=60443392`에 브리핑이 도달해야 한다. 5초 SLA 위반은 acceptance 실패

### M4-S9. 매매 체결 Telegram 브리핑 (자산현황 포함)
- **Given** Risk APPROVE + 코드 룰 통과로 매매가 KIS에 제출되어 체결되었다
- **When** KIS 응답이 도달한다
- **Then** 5초 이내 Telegram 브리핑에 종목/방향/수량/체결가/수수료 + **갱신된 자산현황** 이 포함되어 발송된다

### M4-S10. Risk REJECT → 매매 차단 + Telegram 알림
- **Given** Decision이 매수 시그널을 냈다
- **When** Risk 페르소나가 `REJECT` 응답을 반환한다
- **Then** 어떤 KIS 주문도 제출되지 않고, `risk_reviews`에 REJECT 행이 기록되며, Telegram에 거절 사유 브리핑이 발송된다

### M4-S11. 09:00 KRX 시가 매매
- **Given** Pre-market 08:05에 코드 룰까지 통과한 매매 시그널이 큐에 있다
- **When** 09:00 KST KRX가 개장한다
- **Then** 큐의 시그널이 시가 시장가 주문으로 KIS paper에 제출되고, 체결 후 M4-S9 브리핑이 발송된다

### M4-S12. 동시 SoD 조건 (Risk + 코드 룰)
- **Given** Decision이 매매 시그널을 냈다
- **When** Risk APPROVE이지만 코드 룰 (예: 종목당 20% 한도) 위반이다
- **Then** 매매는 차단된다. 반대 케이스도 동일

### M4-S13. Decision 페르소나 박세훈 7-rule 인지 (v0.2.0 신규)
- **Given** Decision 페르소나가 호출된다
- **When** persona_runs.prompt를 확인한다
- **Then** 시스템 프롬프트에 박세훈 본인 트레이딩 원칙 + 7-rule (현금 30~50%, 익절 RSI>85, 손절 -7%, 섹터 40%, 종목 3~7개, 가치트랩 회피, 공매도 금지) + 매매 비용 인지 + 매매 빈도 가이드가 포함되어 있다

### M4-S14. Decision 응답 비용 인지 (v0.2.0 신규)
- **Given** Decision 페르소나가 매수 시그널을 낸다
- **When** persona_runs.response를 확인한다
- **Then** 응답 본문에 매매 비용 차감 후 순익 추정 (예: "수수료 차감 후 +0.5% 순익" 또는 동등한 표현)이 포함된다

### M4-S15. Risk 페르소나 비용 위반 REJECT (v0.2.0 신규)
- **Given** Decision이 익절 시그널을 +0.3% 평가익에서 냈다 (수수료 차감 시 손실)
- **When** Risk 페르소나가 호출된다
- **Then** Risk는 비용 위반 사유로 `REJECT`를 반환하고, risk_reviews에 사유가 기록된다

---

## M5 — Risk, Cost, Calendar & Observability & 3-Week Paper Operation

### M5-S1. 한도 위반 → 회로차단 + Telegram + 후속 차단
- **Given** 일일 누적 손실이 -1.0%에 도달했다
- **When** 새 매매 시그널이 도착한다
- **Then** `halt_state=true`로 전환되고, 새 주문은 차단되며, Telegram에 회로차단 알림이 발송된다

### M5-S2. /halt 명령 즉시 반영
- **Given** Telegram bot이 가동 중이고 `chat_id=60443392`이다
- **When** 박세훈이 `/halt`를 전송한다
- **Then** 5초 이내 `halt_state=true`로 전환되고, 확인 메시지가 회신된다. 다른 chat_id의 `/halt`는 무시된다

### M5-S3. /resume 명령
- **Given** `halt_state=true`이다
- **When** 박세훈이 `/resume`을 전송한다
- **Then** 5초 이내 `halt_state=false`로 전환되고 확인 메시지가 회신된다. `audit_log`에 기록된다

### M5-S4. 16:00 일일 리포트 (매매 0건이어도 발송)
- **Given** 영업일 16:00 KST가 도래했다
- **When** scheduler가 일일 리포트 생성을 트리거한다
- **Then** Sonnet 4.6으로 리포트가 생성되어 Telegram에 발송된다. 당일 매매가 0건이어도 리포트는 생성되며, 페르소나 호출 요약 + 토큰 비용 + SoD 통계가 포함된다

### M5-S5. Portfolio 페르소나 보유 5종 게이팅
- **Given** 현재 보유 종목 수가 4개이다
- **When** Decision이 매매 시그널을 낸다
- **Then** Portfolio 페르소나는 호출되지 않는다
- **Given** 다음 매매로 보유 종목이 5개가 되었다
- **When** 다음 사이클에서 Decision이 매매 시그널을 낸다
- **Then** Portfolio 페르소나가 호출되어 사이즈를 조정하거나 reject한다

### M5-S6. Retrospective 일요일 리포트 (자동 적용 X)
- **Given** 일요일 임의 시각이 도래했다
- **When** scheduler가 Retrospective 페르소나를 호출한다
- **Then** 회고 리포트가 생성되고 시스템 프롬프트 개선 제안이 `retrospectives` 테이블에 기록된다. 시스템 프롬프트는 자동 변경되지 않는다

### M5-S7. 침묵 모드 자동 진입
- **Given** 직전 3회 연속 Decision 응답이 모두 "신규 시그널 없음"이다
- **When** 다음 일반 정기 사이클이 가동된다
- **Then** `silent_mode=true`로 전환되고, 정기 페르소나 브리핑은 발송되지 않는다. 단 매매·이벤트 트리거·회로차단 + **시스템 에러**는 발송된다

### M5-S8. /verbose 명령으로 침묵 해제
- **Given** `silent_mode=true`이다
- **When** 박세훈이 `/verbose`를 전송한다
- **Then** 즉시 `silent_mode=false`로 전환되고 확인 메시지가 회신된다

### M5-S9. 백업 → 복원 리허설
- **Given** 운영 중인 DB에 데이터가 누적되어 있다
- **When** `./backup.sh`로 백업을 만들고 새 docker compose 환경에 restore한다
- **Then** orders, persona_runs, audit_log 행 수가 백업 시점과 일치하며 healthcheck가 통과한다

### M5-S10. 3주 모의 운영 평가 리포트
- **Given** M5 가동 후 영업일 ~15일이 경과했다
- **When** 운영자가 평가 리포트 생성을 트리거한다
- **Then** 리포트에 다음 항목이 포함된다:
  - 시스템 무중단 가동 (컨테이너 재시작 ≤ 2회)
  - 모든 페르소나 응답·매매·리스크 검토의 DB 기록 완전성 (% 단위)
  - 회로차단기·긴급 정지 우회 사례 (0이어야 함)
  - /halt /resume 정상 작동 audit_log 증거
  - 백업 복원 리허설 통과 여부
  - Risk 페르소나 SoD 작동 (REJECT + HOLD 합계 ≠ 0)
  - 페르소나 응답 일관성 노트 (Retrospective 분석 인용)
  - paper PnL vs SMA cross / dual momentum 벤치마크 비교 (참고용)
  - MDD
  - **누적 매매 수수료 + 거래세 + 슬리피지 추정**
  - 페르소나 토큰 비용 합계 (KRW)

### M5-S11. 페르소나 비용 추적
- **Given** M5 가동 중 페르소나 호출이 다수 발생했다
- **When** `SELECT date_trunc('month', timestamp), SUM(cost_krw) FROM persona_runs GROUP BY 1;`을 실행한다
- **Then** 월간 누적 비용이 산출되며, 일일 리포트에 누적 비용 항목으로 자동 포함된다

---

### v0.2.0 비용 모델 신규 시나리오

### M5-S12. 매수 수수료 자동 캡처 (paper vs live)
- **Given** `TRADING_MODE=paper`이고 005930을 1,000,000원어치 매수한다
- **When** 주문이 체결된다
- **Then** `orders.fee = 0`이다 (PAPER_FEE_BUY=0)
- **Given** `TRADING_MODE=live`이고 동일 매수 (가정)
- **Then** `orders.fee = 1,000,000 × 0.00015 = 150` 원이 채워진다

### M5-S13. 매도 수수료 시장별 차등
- **Given** 005930 (KOSPI) 1,000,000원 매도가 체결된다
- **When** orders.fee가 채워진다
- **Then** `fee = 1,000,000 × 0.00345 = 3,450` 원이다 (LIVE_FEE_SELL_KOSPI)
- **Given** 035720 (KOSDAQ) 1,000,000원 매도가 체결된다
- **Then** `fee = 1,000,000 × 0.00195 = 1,950` 원이다 (LIVE_FEE_SELL_KOSDAQ)

### M5-S14. Cost-aware 한도 검증
- **Given** 자본 10,000,000원, 종목당 한도 20% (2,000,000원), live 모드
- **When** 1,995,000원 + 추정 수수료 매수 시도
- **Then** `notional + estimate_fee = 1,995,000 + 299 ≈ 1,995,299`로 한도 2,000,000 미만이므로 통과
- **When** 1,999,500원 매수 시도
- **Then** `notional + estimate_fee ≈ 1,999,800`로 한도에 근접하나 통과
- **When** 1,999,800원 매수 시도
- **Then** `notional + estimate_fee = 2,000,100 > 2,000,000`로 거부

### M5-S15. 일일 리포트 누적 비용 표시
- **Given** 당일 매매 5건이 발생했다
- **When** 16:00 일일 리포트가 생성된다
- **Then** 리포트에 "누적 매매 수수료: XXX원", "누적 거래세: XXX원", "추정 슬리피지: XXX원"가 한 줄 이상씩 포함된다

### M5-S16. backtest와 live 비용 일치
- **Given** SMA cross backtest와 paper 페르소나 forward 결과가 모두 존재한다
- **When** 두 결과를 비교한다
- **Then** backtest 엔진이 사용한 수수료/세금 상수가 `src/trading/config.py`의 live 상수와 정확히 일치한다 (단위 테스트로 검증)

---

### v0.2.0 캘린더 / 누적 손익 / 시스템 에러 / 백업 무결성 신규 시나리오

### M5-S17. 휴장일 페르소나 호출 0건
- **Given** 2026-05-05 (어린이날, 공휴일) 이다
- **When** 정기 사이클 시각이 도래한다
- **Then** 페르소나는 호출되지 않으며, audit_log에 `cycle_skipped: holiday=childrens_day` 기록이 남는다. Anthropic API 호출 0건

### M5-S18. 12/31 KRX 폐장 skip
- **Given** 12월 31일이다 (KRX 폐장)
- **When** 정기 사이클 시각이 도래한다
- **Then** 사이클 skip + audit_log 기록

### M5-S19. 누적 손익 mark-to-market
- **Given** M5 가동 중 매매가 누적되어 있다
- **When** 일일 리포트가 생성된다
- **Then** KIS balance 조회 결과를 기반으로 일/주/월 누적 손익이 계산되어 리포트에 포함된다

### M5-S20. Anthropic API credits 부족 즉시 알림
- **Given** Anthropic API가 credits 부족 (HTTP 429 또는 429-equivalent) 응답을 반환한다
- **When** Macro persona 호출이 실패한다
- **Then** silent_mode 여부와 관계없이 즉시 텔레그램 알림이 발송되고 audit_log에 기록된다

### M5-S21. KIS API 장애 즉시 알림
- **Given** KIS API가 일시 장애로 5xx 반복 응답한다
- **When** retry가 모두 소진된다
- **Then** silent_mode 여부와 관계없이 즉시 텔레그램 알림이 발송된다

### M5-S22. 백업 무결성 검증
- **Given** `backup.sh`가 실행된다
- **When** 백업 산출물이 생성된 직후
- **Then** `pg_dump --schema-only`로 백업의 스키마 무결성이 검증된다. 검증 통과 시 정상 완료, 실패 시 텔레그램 경보 발송

---

## 본 SPEC 종료 조건

위 M1~M5의 모든 시나리오 (v0.2.0 신규 시나리오 포함) 통과 + 3주 평가 리포트(M5-S10) 산출 시점에 본 SPEC은 종료된다. M6 (실거래) 진입 결정은 본 SPEC 범위가 아니며, 별도 SPEC 5개 후보 (TRADING-002 Live Entry, TRADING-003 Intraday Precision, TRADING-004 Market Microstructure, TRADING-005 CI/CD & Operations, TRADING-006 Robustness 전담) 로 분할 작성된다.
