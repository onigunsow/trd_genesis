# SPEC-TRADING-047 — 인수 기준 (Acceptance Criteria)

Given-When-Then 시나리오. 모든 기준은 관측 가능(observable)해야 한다.

## M1 — 읽기 전용 데이터 접근 계층

### AC-1: 읽기 전용 역할은 쓰기/DDL을 거부한다 (REQ-047-1, 4)

- **Given** `dashboard_ro` 역할 마이그레이션이 `trading migrate` 로 적용된 상태에서
- **When** `dashboard_ro` 커넥션으로 `INSERT`/`UPDATE`/`DELETE`/`CREATE TABLE` 을 시도하면
- **Then** Postgres 가 권한 오류(permission denied)로 거부하고, `SELECT` 은 정상 동작한다.
- **증거**: psql 또는 테스트에서 각 쓰기/DDL 시도가 권한 오류로 실패하는 출력.

### AC-2: 읽기 엔드포인트가 실제 데이터를 JSON으로 반환한다 (REQ-047-2, 3)

- **Given** FastAPI 읽기 API 가 `dashboard_ro` DSN 으로 기동된 상태에서
- **When** `/api/status`, `/api/decisions`, `/api/holdings`, `/api/orders`,
  `/api/scorecard`, `/api/audit` 를 GET 하면
- **Then** 각 엔드포인트가 해당 테이블/엣지 산출물의 데이터를 200 + JSON 으로 반환한다.
- **증거**: 각 엔드포인트의 200 응답 본문 캡처(스키마 필드 포함).

### AC-3: 어떤 응답에도 비밀값이 없다 (REQ-047-5)

- **Given** 모든 읽기 엔드포인트가 기동된 상태에서
- **When** 전체 엔드포인트 응답을 수집하여 검사하면
- **Then** DB 비밀번호 / KIS 자격증명 / Telegram 토큰 / `.env` 원문 어느 것도 응답에
  포함되지 않는다.
- **증거**: 응답 본문에 대한 secret 패턴 grep 결과(0건).

## M2 — 결정 피드 페이지

### AC-4: 페이지가 핵심 위젯을 모두 표시한다 (REQ-047-6)

- **Given** API 가 기동되고 표본 데이터가 있는 상태에서
- **When** 브라우저(tailnet 경유)에서 결정 피드 페이지를 열면
- **Then** 라이브 페르소나 결정+근거, 보유/손익, halt/regime 상태, 최근 주문, 최신
  스코어카드(KOSPI 알파 포함)가 화면에 표시된다.
- **증거**: 각 위젯이 데이터로 채워진 페이지 스크린샷/HTML 캡처.

### AC-5: 페이지가 주기적으로 폴링·갱신한다 (REQ-047-7, 8)

- **Given** 페이지가 열려 있는 상태에서
- **When** 새 `persona_decisions` 행이 DB 에 들어오면
- **Then** 다음 폴링 주기(수 초 이내)에 그 결정+근거가 피드 상단에 나타난다.
- **증거**: 네트워크 탭의 주기적 GET + 신규 결정 반영 전후 비교.

### AC-6: halt 상태가 시각적으로 명확하다 (REQ-047-9)

- **Given** `system_state.halt_state = true` 인 상태에서
- **When** 페이지를 열면
- **Then** halt 상태가 명확한 시각 표식으로 드러난다.
- **증거**: halt=true 일 때 페이지 표식 캡처.

## M3 — Grafana 서비스

### AC-7: Grafana가 읽기 전용 역할로 프로비저닝된 대시보드를 띄운다 (REQ-047-10, 11, 12)

- **Given** compose 에 Grafana 서비스가 추가되고 `dashboard_ro` 데이터소스가 프로비저닝
  파일로 정의된 상태에서
- **When** Grafana 를 tailnet 경유로 열면
- **Then** 자산곡선/주문 흐름/halt·regime 타임라인/P&L 패널이 클릭옵스 없이(provisioning)
  데이터로 렌더링된다.
- **증거**: 프로비저닝 파일 존재 + 각 패널이 데이터로 채워진 Grafana 스크린샷.

## M4 — Tailscale 접근 + 보안

### AC-8: tailnet 외부에서 도달 불가능하다 (REQ-047-13, 14) [HARD 게이트]

- **Given** Tailscale 가 설정되고 대시보드/Grafana 가 tailnet/LAN 바인딩된 상태에서
- **When** tailnet 밖(공개 인터넷)에서 호스트의 공인 IP:포트로 접근을 시도하면
- **Then** 연결이 거부/타임아웃되고, tailnet 안에서는 정상 접근된다.
- **증거**: 외부망에서의 연결 실패(거부/타임아웃) + tailnet 내 200 응답의 대비 캡처.
- **참고**: 라이브 자격증명을 보유한 시스템의 노출 차단이므로 이 AC 는 하드 게이트다.

### AC-9: ops 문서와 .env 하드닝이 존재한다 (REQ-047-15, 16)

- **Given** SPEC 구현 완료 상태에서
- **When** 운영 문서를 확인하면
- **Then** (a) CLI 초심자용 단계별 Tailscale 설정 절차, (b) `.env` 자격증명 회전 +
  `chmod 600` 하드닝 절차가 문서화되어 있다.
- **증거**: ops 문서 내용 + `.env` 파일 권한이 `600` 인 `ls -l` 출력.

## 엣지 케이스 (Edge Cases)

- EC-1: 데이터가 비어 있을 때(예: 당일 스냅샷 미생성) 엔드포인트/페이지가 빈 상태를
  깨지지 않고(no 500) 표시한다.
- EC-2: DB 일시 단절 시 페이지가 마지막 값 유지 또는 명확한 오류 표시(무한 스피너 금지).
- EC-3: `realized_pnl_cum` 이 NULL(백필 전)인 경우에도 자산곡선/스코어카드가 정상 렌더.
- EC-4: 읽기 전용 역할로 `edge/` 함수 호출 시 권한 오류가 나면(R1) seam 미비가 드러남 —
  run 단계에서 즉시 잡아야 함.

## 품질 게이트 (Quality Gates)

- 트레이딩 코어 코드/`compose.yaml` 의 기존 서비스 정의는 **변경 없음**(diff 로 확인).
- 신규 코드는 프로젝트 테스트 스위트에서 회귀 0(기존 통과 테스트 유지).
- 읽기 전용 역할의 쓰기/DDL 거부가 테스트로 강제된다(AC-1).
- AC-8(외부 도달 불가)이 실측으로 통과한다.

## Definition of Done

- [ ] M1: `dashboard_ro` 역할 마이그레이션 적용 + 읽기 전용 DSN FastAPI 엔드포인트 동작
- [ ] M2: 폴링 결정 피드 페이지가 모든 핵심 위젯 표시
- [ ] M3: Grafana 컨테이너 + config-as-code 프로비저닝 대시보드
- [ ] M4: tailnet 전용 바인딩(외부 도달 불가 실측) + ops 문서 + `.env` 하드닝
- [ ] 비밀값 무노출(AC-3) 확인
- [ ] 트레이딩 코어 무변경 확인
- [ ] 회귀 테스트 0
- [ ] Open Questions(OQ-1~4) 운영자 확정 반영
