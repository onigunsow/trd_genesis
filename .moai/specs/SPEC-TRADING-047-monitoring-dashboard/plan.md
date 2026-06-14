# SPEC-TRADING-047 — 구현 계획 (Implementation Plan)

## 기술적 접근 (Technical Approach)

이 작업은 **새 서비스만 추가하는 읽기 계층**이다. 트레이딩 코어(`app`/`bot`/`scheduler`)
와 기존 코드는 건드리지 않는다. 데이터는 이미 Postgres 에 있다.

신규 구성요소:

1. **읽기 전용 Postgres 역할** — 새 마이그레이션(`030_dashboard_readonly_role.sql`,
   다음 번호 예약)으로 `dashboard_ro` 역할 생성: `GRANT CONNECT` + 대상 테이블에
   `GRANT SELECT` 만. INSERT/UPDATE/DELETE/DDL 권한 없음. `trading migrate` 로 적용
   (멱등·자기기록 패턴 준수). 비밀번호는 `.env` 의 새 변수로 주입.
   - 주의: `compose.yaml` 의 마이그레이션 디렉터리는 `docker-entrypoint-initdb.d` 로도
     마운트되어 있으나(init 전용), 기존 DB 에는 init 이 다시 안 돈다. 따라서 역할 생성은
     반드시 `trading migrate` 경로로 적용해야 한다(신규 빈 DB 와 기존 DB 양쪽 커버).

2. **FastAPI 읽기 API** (신규 모듈, 예: `src/trading/dashboard/api.py`)
   - 읽기 엔드포인트가 `dashboard_ro` 역할의 별도 DSN(`DASHBOARD_DATABASE_URL`)으로
     접속. 기존 `session.connection()` 은 쓰기 권한 역할을 쓰므로 **재사용하지 않고**,
     읽기 전용 DSN 전용의 얇은 커넥션 헬퍼를 둔다(권한 분리 보장).
   - 질의는 가능한 한 `edge/` 의 순수 읽기 함수 재사용: `report.load_equity_snapshots`,
     `benchmark.kospi_closes`/`compute`, `scorecard.decide`/`render`. 단, 이 함수들이
     기본 `connection()`(쓰기 역할)을 내부적으로 쓰는지 확인 필요 — 쓴다면 읽기 전용
     커넥션을 주입할 수 있게 seam 을 추가하거나, 대시보드 전용 SELECT 래퍼를 둔다
     (트레이딩 로직 복제는 금지, 단순 SELECT 만).
   - 엔드포인트(초안): `/api/decisions`(persona_decisions + persona_runs 근거 조인),
     `/api/status`(system_state), `/api/holdings`(positions/balance), `/api/orders`
     (최근 orders/fills), `/api/scorecard`(edge 스코어카드 + KOSPI 알파),
     `/api/audit`(audit_log 회로차단 트립). 모두 GET, JSON.

3. **결정 피드 HTML/JS 페이지** (신규, 예: `src/trading/dashboard/static/index.html` + 소량 JS)
   - 의존성 없는 단순 페이지가 수 초 간격으로 위 엔드포인트를 폴링. SSE/WS 미사용.
   - 표시: 페르소나 결정 + 근거(텍스트), 보유/손익, halt/regime, 최근 주문, 스코어카드.

4. **Grafana 컨테이너** (compose 신규 서비스)
   - `dashboard_ro` 역할을 가리키는 Postgres 데이터소스를 프로비저닝 파일로 정의
     (`grafana/provisioning/datasources/*.yaml`, `.../dashboards/*.yaml` + 대시보드 JSON).
   - 패널: 자산곡선(daily_equity_snapshot), 주문 흐름(orders/fills),
     halt/regime 타임라인(audit_log + system_state 이력), P&L.

5. **Tailscale** (호스트 레벨 ops)
   - 호스트에 Tailscale 설치·로그인. FastAPI/Grafana 포트는 tailnet 인터페이스(및/또는
     LAN)에만 바인딩하고 공개 인터넷에 publish 하지 않는다. compose port 매핑은
     `127.0.0.1:`/tailnet IP 바인딩으로 제한하거나 호스트 방화벽으로 통제.

## 구현 도메인 라우팅 (Run 단계 분배)

이 작업은 **세 도메인**에 걸친다. `/moai run` 이 올바르게 라우팅하도록 명시:

- **expert-devops**: Tailscale 설치/바인딩, Grafana 컨테이너 + 프로비저닝,
  `compose.yaml` 신규 서비스 추가, `.env` 하드닝 ops 문서, 포트 바인딩/방화벽.
- **expert-backend**: FastAPI 읽기 API, 읽기 전용 역할 마이그레이션
  (`030_dashboard_readonly_role.sql`), 읽기 전용 DSN 커넥션 헬퍼, `edge/` 재사용 seam.
- **expert-frontend**: 결정 피드 HTML/JS 페이지(폴링), 상태 시각화.

세 도메인은 서로 다른 파일군을 만지므로(신규 디렉터리 위주) 충돌 위험이 낮다. 단,
`compose.yaml` 은 devops·backend 양쪽이 참조하므로 devops 가 단독 소유로 편집한다.

## 마일스톤 (Milestones, priority 기반 — 시간추정 없음)

- **M1 (Priority High)** — 읽기 전용 데이터 접근 계층: `dashboard_ro` 역할 마이그레이션 +
  읽기 전용 DSN 커넥션 헬퍼 + FastAPI 읽기 엔드포인트(JSON). 보안의 토대이므로 최우선.
- **M2 (Priority High)** — 결정 피드 페이지: 폴링 HTML/JS, 텍스트 위주 패널. M1 의존.
- **M3 (Priority Medium)** — Grafana 서비스: 컨테이너 + config-as-code 프로비저닝 패널.
  M1(읽기 역할)에 의존, M2 와 병렬 가능.
- **M4 (Priority High)** — Tailscale 접근 + 보안: tailnet 전용 바인딩 + `.env` 하드닝 +
  ops 문서. 노출 통제이므로, 어떤 것을 외부에서 도달 가능하게 만들기 **전에** 완료해야 한다.
  → 순서 제약: **M4 의 바인딩/노출 통제가 M2·M3 의 외부 도달성보다 먼저 확정**되어야 한다.

권장 실행 순서: M1 → (M4 바인딩 통제 확립) → M2/M3 병렬 → M4 문서/하드닝 마무리.

## 리스크 (Risks)

- R1: `edge/` 읽기 함수가 내부적으로 쓰기 역할 `connection()` 을 사용하면, 읽기 전용
  역할로 그대로 못 쓴다. → 대응: 읽기 전용 DSN 을 주입할 seam 추가 또는 대시보드 전용
  단순 SELECT 래퍼(로직 복제 아님). [확인 필요-1]
- R2: 마이그레이션 디렉터리의 init-only 마운트 때문에 기존 DB 에 역할이 안 생길 수 있음.
  → 대응: `trading migrate` 로 적용(신규/기존 양쪽 커버), 멱등 보장.
- R3: 포트 바인딩 실수로 공개 노출 가능성. → 대응: tailnet/LAN IP 또는 127.0.0.1 바인딩 +
  호스트 방화벽, run 단계에서 외부 접근 차단을 실측 검증(AC).
- R4: Grafana 기본 자격증명/익명 접근이 VPN 뒤라도 위험. → 운영 결정(§OQ): basic auth 권장.
- R5: 운영자 CLI 초심자 → run 단계 설치 절차는 단계별로 풀어 써야 함(A3).

## ADR (Architecture Decision Record)

### ADR-047-1: 실시간 메커니즘 — 폴링 vs SSE/WebSocket

- **결정: 짧은 간격 폴링(권장).** 시스템이 5/15분 저빈도 cadence 이므로 수 초 폴링으로
  충분하고 가장 단순하다. 의존성 없는 HTML/JS 로 구현 가능.
- **대안(연기): SSE/WebSocket.** 실시간 푸시가 필요할 만큼의 빈도가 아니며, 서버측
  상태/연결관리 복잡도만 늘린다. 향후 고빈도 전환 시 재검토(defer).

## Open Questions (운영자 확인 필요 — orchestrator가 AskUserQuestion으로 질의)

- OQ-1: 컨테이너 + Tailscale 를 어느 호스트에서 돌릴 것인가(현 트레이딩 호스트 동일? 별도?).
- OQ-2: VPN 뒤라도 Grafana 인증(basic auth)을 둘 것인가? → **권장: 예(basic auth).**
  (심층 방어; tailnet 공유 기기에서의 우발 접근 차단)
- OQ-3: 결정 피드의 보존/범위 — 최근 며칠치(N일)를 보여줄 것인가? 기본값 제안 필요.
- OQ-4: SPEC-044 비용보정(net cost-adjusted) 스코어카드를 페이지에 함께 노출할 것인가?

## [확인 필요] 항목

- [확인 필요-1]: `edge/` 의 `report`/`benchmark`/`scorecard` 함수가 읽기 전용 DSN 주입을
  지원하는지(또는 seam 추가가 필요한지) run 단계 시작 시 코드 확인.
