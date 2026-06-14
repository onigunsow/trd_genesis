---
id: SPEC-TRADING-047
version: 0.1.0
status: draft
created: 2026-06-14
updated: 2026-06-14
author: oni
priority: medium
issue_number: null
---

# SPEC-TRADING-047 — 실시간 모니터링 대시보드 (Tailscale 전용, 읽기 전용)

## HISTORY

- v0.1.0 (2026-06-14): 최초 초안. 운영자가 잠긴 결정사항을 그대로 반영 —
  Tailscale VPN 전용 접근, Grafana + 커스텀 FastAPI/HTML "결정 피드"의 이중 대시보드,
  완전 읽기 전용(제어 액션 없음), 데이터는 이미 Postgres에 존재(주로 읽기 계층).

---

## 개요 (Overview)

트레이딩 시스템의 의사결정과 상태를 집 네트워크 밖에서도 볼 수 있는 실시간 모니터링
페이지를 추가한다. 이 SPEC은 **관측(observability) 전용**이며, 트레이딩 동작을
바꾸거나 엣지(edge)를 개선하지 않는다. 데이터는 이미 Postgres에 존재하므로 이 작업의
본질은 **읽기 계층(read layer)** 을 새로 얹는 것이다 — 트레이딩 코어는 손대지 않는다.

대시보드는 두 가지로 구성된다:

1. **Grafana + Postgres 데이터소스** — 지표/상태 패널 (자산곡선, 주문흐름, halt/regime
   타임라인, 손익). 수치·시계열에 강하다.
2. **커스텀 경량 FastAPI 읽기 API + 단순 HTML 페이지** — "결정 피드"(LLM 페르소나의
   근거/시그널 등 텍스트 위주). Grafana가 텍스트를 잘 못 그리는 영역을 담당한다.

접근은 **Tailscale VPN으로만** 가능하며, 공개 인터넷에 절대 노출되지 않는다.

## 배경 / 근거 (Context)

코드베이스 조사로 확인한 사실:

- 현재 저장소에 **HTTP/웹 서버가 전혀 없다** (FastAPI/uvicorn/flask/aiohttp/http.server
  어느 것도 `src/` 에 부재). 이 SPEC이 첫 웹 서비스다.
- `compose.yaml` 서비스 구성: `postgres`(pgvector/pgvector:pg16) / `app` / `bot` /
  `scheduler`. 모두 `trading-net` 브리지 네트워크. DB 자격증명은 `.env` 평문.
- 필요한 데이터가 이미 Postgres에 존재:
  - `persona_runs` (LLM 응답 + 근거 + 토큰/비용/지연), `persona_decisions`
    (ticker/side/qty/rationale/confidence), `risk_reviews` (verdict) — **결정 피드**
  - `system_state` (halt_state / current_regime / current_risk_appetite /
    late_cycle_defense_active 등) — **현재 상태**
  - `orders`, `fills` — **체결/주문 흐름**
  - `daily_equity_snapshot` (total_assets / stock_eval / cash / unrealized_pnl /
    realized_pnl_cum) — **자산곡선**
  - `audit_log` — **회로차단 트립 / 인시던트**
  - `edge/` 스코어카드 출력 (`scorecard.decide/render`, `benchmark.compute`,
    `report.load_equity_snapshots`, `benchmark.kospi_closes`) — **성과 / KOSPI 알파**
- DB 접근은 `src/trading/db/session.py` 의 `connection()` (plain psycopg, `dict_row`).
  DSN은 `DATABASE_URL` 또는 `POSTGRES_*` 환경변수에서 해석.
- 마이그레이션은 `trading migrate` (멱등, 자기기록 `schema_migrations`)로 적용.
  `001` 만 init 스크립트로 자동 적용되고 이후는 수동/배포 스텝.

운영자는 며칠 내 실거래(live) 투입 예정이므로, 라이브 KIS/Telegram/DB 자격증명을
보유한 시스템을 공개 인터넷에 노출하는 것은 허용 불가다.

## 가정 (Assumptions)

- A1: 단일 호스트 홈랩(homelab)에서 모든 컨테이너가 돌아간다. (멀티호스트 아님)
- A2: 호스트에 Docker / docker compose 가 이미 설치·동작 중이다.
- A3: 운영자는 CLI 초심자다 → 최종 run 단계의 설치 절차는 단계별로 풀어 써야 한다.
- A4: Pi-hole 은 집에서 DNS 용도로만 운용된다. **Pi-hole 은 원격 접근 수단이 아니다.**
  접근 계층은 Tailscale 이며, Pi-hole 은 선택적으로 친숙한 LAN 호스트명을 제공하거나
  tailnet DNS 역할을 할 수 있다(필수 아님).
- A5: 시스템은 저빈도(5/15분 사이클)이므로, 결정 피드는 수 초 간격 폴링으로 충분하다.
- A6: 자산곡선/스코어카드 등 일부 지표는 일 1회 갱신(장마감 스냅샷)이라 실시간이 아니다.

## 요구사항 (EARS Requirements)

### M1 — 읽기 전용 데이터 접근 계층 (Read-only data access layer)

- REQ-047-1 (Ubiquitous): 대시보드 시스템은 트레이딩 데이터에 접근할 때 전용 **읽기 전용
  Postgres 역할(role)** 만 사용 **shall**한다. 이 역할은 INSERT/UPDATE/DELETE/DDL
  권한을 가지지 않는다(SELECT 전용).
- REQ-047-2 (Ubiquitous): FastAPI 읽기 API는 §배경에 열거한 테이블만 SELECT 하여 JSON으로
  반환 **shall**한다. 기존 `edge/`·`db` 질의 코드를 재사용하며 트레이딩 로직을 복제하지
  않는다.
- REQ-047-3 (Event-Driven): **When** 클라이언트가 읽기 엔드포인트를 호출하면, the system
  **shall** 읽기 전용 역할의 커넥션으로만 질의를 수행한다.
- REQ-047-4 (Unwanted): **If** 어떤 코드 경로가 대시보드 역할을 통해 쓰기(write)나 DDL을
  시도하면, **then** the system **shall** DB 권한 수준에서 그 시도를 거부한다(방어의 심층화).
- REQ-047-5 (Unwanted): the system **shall** API 응답이나 UI에 어떤 비밀값(secret)도
  — DB 비밀번호, KIS 자격증명, Telegram 토큰, `.env` 내용 — 렌더링하지 **않는다(shall not)**.

### M2 — 커스텀 결정 피드 페이지 (Custom decision-feed page)

- REQ-047-6 (Ubiquitous): 결정 피드 페이지는 다음을 표시 **shall**한다 — 라이브 페르소나
  결정 + 근거, 현재 보유종목/손익, halt/regime 상태, 최근 주문, 최신 엣지 스코어카드
  (KOSPI 알파 포함).
- REQ-047-7 (State-Driven): **While** 페이지가 열려 있는 동안, the system **shall** 수 초
  간격으로 FastAPI 엔드포인트를 폴링하여 표시 데이터를 갱신한다. (SSE/WebSocket 은 선택,
  필수 아님 — §ADR 참조)
- REQ-047-8 (Event-Driven): **When** 신규 `persona_decisions` 행이 생기면, 다음 폴링 시
  the system **shall** 그 결정과 근거를 피드 상단에 노출한다.
- REQ-047-9 (State-Driven): **While** `system_state.halt_state` 가 true 인 동안, the
  system **shall** 페이지에 halt 상태를 시각적으로 명확히 표시한다.

### M3 — Grafana 서비스 (Grafana service)

- REQ-047-10 (Ubiquitous): compose 에 Grafana 컨테이너를 추가하고 **읽기 전용 Postgres
  역할** 을 가리키는 Postgres 데이터소스를 사용 **shall**한다.
- REQ-047-11 (Ubiquitous): Grafana 데이터소스와 대시보드는 **config-as-code(프로비저닝
  파일)** 로 정의 **shall**한다 — 클릭옵스(click-ops) 금지.
- REQ-047-12 (Ubiquitous): 프로비저닝된 대시보드는 최소한 자산곡선, 주문 흐름,
  halt/regime 타임라인, 손익(P&L) 패널을 포함 **shall**한다.

### M4 — Tailscale 접근 + 보안 (Tailscale access + security)

- REQ-047-13 (Ubiquitous): the system **shall** 호스트에서 Tailscale 로 대시보드(FastAPI)
  와 Grafana 를 tailnet(및/또는 LAN)으로만 도달 가능하게 바인딩한다.
- REQ-047-14 (Unwanted): the system **shall** 대시보드/Grafana 포트를 공개 인터넷에 절대
  publish 하지 **않으며(shall not)**, 포트포워딩을 구성하지 않는다.
- REQ-047-15 (Ubiquitous): 운영 절차(ops procedure)로 Tailscale 설정을 문서화 **shall**
  한다(CLI 초심자 대상 단계별).
- REQ-047-16 (Ubiquitous): edge-report 가 지적한 관련 보안 항목 — `.env` 평문 자격증명
  하드닝(주기적 회전 + `chmod 600`) — 을 본 SPEC의 운영 문서에 포함 **shall**한다.

## 비기능 요구사항 (Non-Functional)

- NFR-1 (보안): VPN 전용. 공개 노출/포트포워딩 없음. 읽기 전용 DB 역할(심층 방어).
  UI/API 에 비밀값 렌더링 금지.
- NFR-2 (격리): 새 서비스만 추가한다. 기존 `app`/`bot`/`scheduler`/`postgres` 정의와
  트레이딩 코어 코드는 변경하지 않는다.
- NFR-3 (재사용): 질의는 가능하면 `edge/`·`db` 코드를 재사용한다(트레이딩 로직 복제 금지).
- NFR-4 (저빈도 적합성): 폴링 간격은 5/15분 사이클 cadence 에 맞춘 수 초 수준이면 충분하다.

## Exclusions (What NOT to Build)

- EXC-1: **제어 액션 없음.** halt/resume, 주문 제출/취소, 모드 변경, 설정 변경 등 어떤
  쓰기/제어 경로도 만들지 않는다. 제어는 기존 CLI/Telegram 에 그대로 둔다.
- EXC-2: **공개 인터넷 노출 없음.** 리버스 프록시를 통한 공개 도메인, 포트포워딩,
  ngrok/Cloudflare Tunnel 등 어떤 공개 접근 경로도 구성하지 않는다.
- EXC-3: **트레이딩 코어 미변경.** 페르소나/오케스트레이터/리스크/엣지 계산 로직을 수정하지
  않는다. 새 데이터 산출이나 새 지표 계산을 추가하지 않는다(기존 산출물의 표시만).
- EXC-4: **인증·사용자 관리 시스템 신규 구축 없음.** Grafana 기본 인증(basic auth)
  활성화는 운영 결정사항(§Open Questions)이며, 자체 SSO/계정 시스템은 만들지 않는다.
- EXC-5: **SSE/WebSocket 미구현(이번 범위).** 실시간 푸시는 폴링으로 대체하며, SSE/WS 는
  명시적으로 연기(defer)한다.
- EXC-6: **모바일 앱/네이티브 클라이언트 없음.** 브라우저에서 보는 단순 HTML 페이지만.
- EXC-7: **알림 라우팅 변경 없음.** Grafana 알림/Alertmanager 등 신규 알림 채널을 만들지
  않는다(알림은 기존 Telegram 경로 유지).

## 정직성 고지 (Honesty)

- 본 SPEC은 **관측 전용**이다. 트레이딩 동작·수익률·엣지를 바꾸지 않는다. 가시성만 높인다.
- 데이터는 이미 Postgres 에 있으므로, 위험은 "새 계산"이 아니라 "노출 표면(exposure)"에
  있다 — 그래서 읽기 전용 역할 + VPN 전용이 핵심 통제다.

## 관련 SPEC

- SPEC-TRADING-044 (measurement-infrastructure): 스코어카드/KOSPI 알파 산출. M2에서 표시.
- SPEC-TRADING-042 (broker-truth-ledger): `daily_equity_snapshot`/`orders`/`fills` 진실원장.
- SPEC-TRADING-035/036: `system_state` 의 regime/late_cycle 컬럼(상태 패널 소스).
