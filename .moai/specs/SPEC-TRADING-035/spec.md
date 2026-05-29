---
id: SPEC-TRADING-035
version: 0.1.0
status: draft
created: 2026-05-29
updated: 2026-05-29
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Regime Awareness — Core Loop (SPEC-016 Phase 2 scoped)"
related_specs:
  - SPEC-TRADING-016   # 5-persona 시스템 / 자본 보전 원칙 / Phase 2 Regime Awareness 원본 요구(REQ-016-2-1~4) — 본 SPEC 의 모태
  - SPEC-TRADING-033   # 매도(청산) A 단계 — 변동성 레짐 ATR 임계는 macro regime 과 분리(과매도 방지) 정합
  - SPEC-TRADING-034   # 포트폴리오 페르소나 사이클 연결 B 단계 — REQ-035-4 가 그 입력에 current_regime 주입
  - SPEC-TRADING-012   # decision.jinja 동적 임계(dynamic_thresholds) 선례 — 프롬프트 컨텍스트 주입 패턴 참조
  - SPEC-TRADING-029   # balance()/cash_pct·holdings — 포트폴리오 현금 타깃의 컨텍스트 소스
---

# SPEC-TRADING-035 — Regime Awareness — Core Loop (SPEC-016 Phase 2 scoped)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-29 | 0.1.0 | Initial draft. 매도/배분 개선 3부작(A→B→C) 중 **C**. Macro 페르소나가 이미 emit 하는 `regime`(bull/neutral/bear)·`risk_appetite`(risk-on/neutral/risk-off)가 `persona_runs.response_json` 에만 머물고 어디서도 분기에 쓰이지 않는다는 검증 결과(research.md §1)에 근거. 본 SPEC 은 (1) 두 키를 `system_state` 단일 행 컬럼으로 캐싱(REQ-035-1), (2) Decision/Risk 의 **보수적** regime 분기(REQ-035-2), (3) 매크로 페르소나 데일리 06:00 실행(REQ-035-3), (4) regime→포트폴리오 현금 타깃 시프트(REQ-035-4)를 정의한다. SPEC-016 Q-2(regime 은 Macro LLM 출력을 신뢰), Q-5(신규 테이블 대신 system_state 확장), Q4(B only)를 반영. 신규 데이터 fetcher(신용잔고·예탁금·V-KOSPI)·adaptive impact-5 트리거·Phase 3 불장 모드는 **비목표**. 사용자 정책 결정 반영 — 2026-05-29 | onigunsow |

---

## Scope Summary

본 SPEC 은 SPEC-016 Phase 2("Regime Awareness")를 **사용자가 확정한 범위로 축소 구현**한다.
오늘날 Macro 페르소나는 매 실행마다 시장 체제(`regime`)와 위험선호(`risk_appetite`)를 JSON 으로
**산출하지만**, 이 값들은 500자 텍스트 blob 으로만 Decision/Risk 에 흘러가고 **구조화된 분기 로직이
전무**하다(research.md §1, grep 검증: `current_regime`/`regime_at_decision`/`regime_branch_applied`
0건). 또한 regime 을 생산하는 매크로 페르소나는 **금요일 17:00 주 1회만** 돌아 staleness 가 크다.

C 단계는 이 죽은 신호를 **실제 매매 행동으로 흐르게** 한다: regime/risk_appetite 를
`system_state` 컬럼으로 승격(캐싱)하고, Decision/Risk 가 이를 읽어 **보수적으로** 분기하며(현재
후기 사이클 신호 — 빚투/신용 ~36조, KOSPI 고점권 — 을 감안), 포트폴리오 현금 타깃을 regime 으로
시프트하고, 매크로 페르소나를 **데일리 06:00** 로 추가 실행(CLI 브릿지라 비용 0)하여 신선도를
끌어올린다.

본 SPEC 은 **신규 시장 데이터 수집기를 만들지 않는다.** regime 판단은 SPEC-016 Q2 결정대로 Macro
LLM 의 출력을 신뢰한다(기계적 임계 계산 금지). 기존 `flows`(외국인/기관/개인 순매수, pykrx)와
KOSPI/KOSDAQ OHLCV 만 사용한다.

### A→B→C 위치

- **A = SPEC-033 (완료)**: 자동 손절/익절 워치독(`*/5`), ATR 기반. ATR 임계는 **변동성 레짐**
  (low/normal/high/extreme)을 쓰며 **macro regime 과 분리**된다(과매도 방지) — 본 SPEC 은 stop-loss
  강도를 macro regime 에 연결하지 않는다.
- **B = SPEC-034 (완료)**: 휴면 포트폴리오 페르소나를 decision→portfolio→risk→execute 에 연결,
  buy-only 사이징 규율 + CLI 비용 0(REQ-034-9). 본 SPEC 의 REQ-035-4 가 그 입력에 regime 을 더한다.
- **C = SPEC-035 (본 SPEC)**: macro regime 을 Decision/Risk/Portfolio 행동에 실제로 반영.

---

## Goals

- **G-1**: Macro 페르소나가 산출한 `regime`/`risk_appetite` 가 `system_state` 컬럼으로 조회 가능하다
  (텍스트 파싱 불요).
- **G-2**: Decision/Risk 페르소나가 `current_regime` 을 읽어 **보수적** 분기를 적용한다(테스트로 입증).
- **G-3**: 매크로 페르소나가 평일 데일리(06:00) + 금요일(17:00) 로 실행되어 regime staleness 가 줄어든다.
- **G-4**: 포트폴리오 현금 타깃이 regime 에 따라 보수적으로 시프트한다(매크로 비중조정).
- **G-5**: regime 신선도가 7일을 넘으면 안전 폴백('neutral') + 텔레그램 경고가 동작한다.
- **G-6**: 베이스라인 801 passed 대비 신규 회귀 0, 신규 코드 85%+ 커버리지.

---

## Requirements (EARS)

### REQ-035-1: regime/risk_appetite 의 DB 캐싱 (Ubiquitous + Event-Driven + State-Driven)

시스템은 Macro 페르소나가 산출하는 `regime` 과 `risk_appetite` 를 **구조화된 `system_state` 컬럼으로
캐싱**하여, 다른 페르소나가 텍스트 파싱 없이 조회할 수 있도록 해야 한다.

- **(a) Ubiquitous** — 시스템은 `system_state`(단일 행 id=1) 에 다음 컬럼을 **항상** 보유해야 한다
  (신규 테이블이 아닌 기존 단일 행 확장 — SPEC-016 Q-5):
  - `current_regime TEXT NOT NULL DEFAULT 'neutral'` — 도메인 CHECK `('bull','neutral','bear')`
  - `current_risk_appetite TEXT NOT NULL DEFAULT 'neutral'` — 도메인 CHECK `('risk-on','neutral','risk-off')`
  - `regime_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `regime_source_run_id BIGINT` — FK → `persona_runs.id`
- **(b) Event-Driven** — **When** Macro 페르소나 실행이 성공적으로 응답을 반환하면, **then** 시스템은
  응답 JSON 의 `regime`, `risk_appetite` 를 추출하여 위 컬럼을 UPDATE 하고
  (`regime_updated_at = now()`, `regime_source_run_id = <persona_runs.id>`).
- **(c) State-Driven (TTL)** — **While** `regime_updated_at` 이 7일을 초과하면, **then** 시스템은
  **읽기 시점에** `current_regime` 을 `'neutral'` 로 안전 폴백하고(저장값은 보존, 읽기 결과만 폴백)
  Telegram 경고를 1회 송출해야 한다.
- **(d) Schema guard** — Macro 응답에 `regime` 또는 `risk_appetite` 키가 없으면 시스템은 스키마
  오류를 raise 해야 한다(두 키는 이미 emit 되므로 이는 회귀 방지 가드다). 스키마 오류 시 캐시는
  **갱신하지 않고**(이전 값 보존) 텔레그램으로 통지한다.
- **(e)** regime 읽기·TTL 폴백은 단일 헬퍼(`session.py`)로 노출하여 Decision/Risk/Portfolio 가
  동일 경로로 조회한다.

#### Acceptance Criteria — REQ-035-1

- [ ] 마이그레이션 `024_*.sql` 적용 후 `system_state` 에 4개 컬럼(`current_regime`,
      `current_risk_appetite`, `regime_updated_at`, `regime_source_run_id`)이 존재하고 CHECK
      제약이 잡혀 있다.
- [ ] 마이그레이션 재실행(idempotent)해도 오류 없이 통과한다(information_schema 가드).
- [ ] Macro 페르소나 1회 성공 실행 후 `SELECT current_regime, current_risk_appetite,
      regime_source_run_id FROM system_state` 가 비어있지 않은 enum 값 + 직전 run_id 를 반환한다.
- [ ] TTL 시뮬레이션: `regime_updated_at` 을 8일 전으로 강제 설정 → regime 읽기 헬퍼가
      `'neutral'` 을 반환하고 Telegram 경고가 1회 발생한다(저장된 컬럼값 자체는 변경되지 않는다).
- [ ] Macro 응답에서 `regime` 키를 제거한 경우 스키마 오류가 raise 되고 캐시는 미갱신(이전 값 보존)
      + 텔레그램 통지가 호출된다.
- [ ] `current_regime` 에 도메인 밖 값(`'sideways'` 등) UPDATE 시 DB CHECK 위반으로 거부된다.

---

### REQ-035-2: Decision/Risk 의 보수적 regime 분기 (State-Driven)

Decision 페르소나와 Risk 페르소나는 `current_regime` 을 읽어 **보수적으로** 분기 동작해야 한다.
사용자는 현재 후기 사이클 신호(신용잔고/빚투 ~36조, KOSPI 고점권)를 근거로 **보수 분기**를 명시
선택했다 — Phase 3 의 공격적 모드(1~2종목 집중, 현금 10%)는 **이 SPEC 의 비목표**다.

- **(a) State-Driven — bull (완만한 완화만)** — **While** `current_regime == 'bull'` 이면:
  - 현금 바닥 30% → **20%** (10% 아님), confidence 임계 **−0.05** (−0.1 아님),
    섹터 집중 한도 **완만하게** 완화.
  - **금지**: 1~2종목 집중, 현금 10% (Phase 3, out of scope).
- **(b) State-Driven — bear (긴축)** — **While** `current_regime == 'bear'` 이면:
  - confidence 임계 **+0.1**, 섹터 한도 **긴축**, 레버리지/신용(margin) 매수 **차단**.
- **(c) State-Driven — neutral (무변경)** — **While** `current_regime == 'neutral'` 이면 현재 동작을
  그대로 유지한다(변경 없음).
- **(d) 컨텍스트 주입** — Decision/Risk 의 system 프롬프트에 regime 컨텍스트 라인을 주입한다. 예:
  `"현재 시장 regime: {regime}, risk_appetite: {risk_appetite}. 보수적 분기 적용."`
- **(e) 설계 결정(구현자 판단 + 권고)** — 임계 조정을 (a) **regime 조정 수치를 프롬프트 컨텍스트로
  주입** vs (b) **Python 레벨 enforcement** 중 무엇으로 할지 결정한다.
  - **권고**: (a) **프롬프트 컨텍스트에 regime + 조정된 수치 주입**(이 시스템의 단일 턴 LLM 패턴과
    일관 — SPEC-012 dynamic_thresholds 선례) **+ 하드 현금 바닥은 Python 가드** 병행(LLM 이 무시
    못 하도록 최소한의 코드 enforcement). 기존 risk 한도(`limits.py`)는 최종 hard gate 로 불변.
- **(f) 감사** — regime 분기가 적용된 의사결정은 `persona_runs.regime_at_decision`(신규 컬럼,
  024 에 fold) 에 스냅샷되고, Decision/Risk 응답 JSON 에 `regime_branch_applied:
  "bull"|"neutral"|"bear"` 필드가 추가된다.

#### Acceptance Criteria — REQ-035-2

- [ ] `current_regime='bull'` 강제 후 1 cycle 실행 → 적용된 현금 바닥이 **20%**(10% 아님),
      confidence 임계가 기준 −0.05 임을 컨텍스트/가드에서 확인.
- [ ] `current_regime='bear'` 강제 후 → confidence 임계 +0.1, 섹터 한도 긴축, 레버리지/신용 매수
      차단이 적용된다.
- [ ] `current_regime='neutral'` → Decision/Risk 행동이 기존과 동일(diff 없음).
- [ ] `decision.jinja`, `risk.jinja` 에 regime 컨텍스트 라인이 존재한다(grep 검증).
- [ ] Decision/Risk 응답 JSON 에 `regime_branch_applied` 필드가 들어가고 값이 현재 regime 과 일치한다.
- [ ] 1 cycle 실행 후 `persona_runs.regime_at_decision` 에 해당 실행의 regime 스냅샷이 기록된다.
- [ ] bull 분기에서 현금 비중이 **10% 까지 내려가지 않는다**(보수 분기 보증 — 음성 테스트).
- [ ] 하드 현금 바닥 Python 가드: regime=bull 이라도 현금이 20% 미만이면 신규 buy 가 차단된다.

**Dependencies**: REQ-035-1 (regime DB 컬럼 선행).

---

### REQ-035-3: 매크로 페르소나 데일리 06:00 실행 (Event-Driven)

시스템은 regime staleness 를 줄이기 위해 **매크로 페르소나**(regime 생산자)를 평일 데일리 06:00 KST
에 추가 실행해야 한다.

- **(a) Event-Driven** — **When** 평일(mon-fri) 06:00 KST 가 도래하면, **then** 시스템은 매크로
  페르소나를 1회 실행한다. 기존 금요일 17:00 주간 실행은 **유지**한다(중복 무해, 주간 캐시 갱신).
- **(b)** 추가 실행은 `orchestrator.run_weekly_macro` 의 **기존 CLI 경로**(`is_cli_mode_active()
  → call_persona_via_cli`)를 재사용한다 → 추가 비용 0 (REQ-035 제약).
- **(c)** 스케줄 상수는 `runner.py` 의 Python 코드(`CronTrigger`)다. `scheduler.yaml` 은 런타임에
  로드되지 않으므로(research.md §4) `runner.py` 에 `add_job` 1개를 추가한다.
- **(d) 비목표(명시적 defer)** — adaptive impact-5 뉴스 트리거(REQ-016-2-3 b)는 구현하지 않는다.

#### Acceptance Criteria — REQ-035-3

- [ ] 스케줄러 등록 후 매크로 페르소나 잡이 **2개** 존재한다: 평일 06:00(신규) + 금요일 17:00(기존).
- [ ] 신규 06:00 잡의 트리거가 `day_of_week="mon-fri", hour=6, minute=0` 임을 단위 테스트로 검증.
- [ ] 06:00 잡 실행 시 `is_cli_mode_active()` 경로(`call_persona_via_cli`)를 타고 유료
      `call_persona` 를 호출하지 않는다(비용 0 — mock 호출 검증).
- [ ] 06:00 매크로 실행 성공 시 REQ-035-1(b) 경로로 `system_state` regime 컬럼이 갱신된다.
- [ ] adaptive impact-5 트리거 관련 코드/잡은 추가되지 않았다(비목표 확인).

**Dependencies**: REQ-035-1 (regime 캐시 인프라).

---

### REQ-035-4: regime → 포트폴리오 현금 타깃 시프트 (State-Driven)

포트폴리오 게이트/페르소나는 `current_regime` 을 읽어 **현금 타깃을 보수적으로 시프트**해야 한다
(SPEC-016 Q4 = **B only**: 현금 타깃 조정만, 종목 집중은 비목표).

- **(a) State-Driven** — **While** `current_regime == 'bull'` 이면 현금 타깃을 가이드 범위의
  **하단**으로, `'bear'` 이면 **상단**으로, `'neutral'` 이면 기존 가이드를 유지하도록 시프트한다.
  단 모든 시프트는 **자본 보전 경계 안**(보수 분기 — REQ-035-2 의 bull 현금 바닥 20% 와 정합)에서만
  허용된다.
- **(b)** 포트폴리오 게이트(`portfolio_gate.py`)는 `portfolio.run(...)` 입력에 `current_regime`
  (및 필요 시 regime-shifted 현금 타깃)을 추가한다. `portfolio.jinja` 에 regime-aware 현금 타깃
  가이드 라인을 주입한다.
- **(c)** 포트폴리오 페르소나는 SPEC-034 의 CLI 패턴(REQ-034-9)을 그대로 따른다(비용 0).
- **(d) fail-safe 정합** — regime 읽기 실패/TTL 폴백 시 'neutral' 로 동작하며, SPEC-034 의
  fail-safe(페르소나 실패 시 미조정 통과, 거래 차단 금지)를 위반하지 않는다.
- **(e) 비목표** — sell(청산) 조정 금지(SPEC-033/034 정합), 종목 집중 변경 금지.

#### Acceptance Criteria — REQ-035-4

- [ ] `current_regime='bull'` → 포트폴리오 현금 타깃이 가이드 범위 **하단**으로 시프트(단 20% 미만
      불가 — REQ-035-2 정합).
- [ ] `current_regime='bear'` → 포트폴리오 현금 타깃이 가이드 범위 **상단**으로 시프트.
- [ ] `current_regime='neutral'` → 포트폴리오 현금 가이드가 기존과 동일.
- [ ] `portfolio.jinja` 에 regime-aware 현금 타깃 라인이 존재(grep 검증)하고, `portfolio_gate` 가
      `portfolio.run` 입력에 `current_regime` 을 전달한다.
- [ ] 포트폴리오 페르소나 호출이 `call_persona_via_cli` 경로를 탄다(비용 0).
- [ ] regime 읽기 실패/TTL 폴백 시 포트폴리오는 'neutral' 로 동작하고 사이클을 차단하지 않는다
      (SPEC-034 fail-safe 보존).
- [ ] sell 시그널은 regime 과 무관하게 무조정 통과한다(음성 테스트).

**Dependencies**: REQ-035-1 (regime 컬럼), SPEC-034 (포트폴리오 게이트 입력 경로).

---

## Specifications

### 마이그레이션 024 — system_state regime 컬럼 + persona_runs 감사 컬럼

> raw SQL, 순차(`024_`), 멱등(information_schema 가드 — `023`/`013` 하우스 스타일). `migrate.py`
> 가 자동 발견하며, 파일 스스로 `schema_migrations` 와 `audit_log` 에 기록한다.

파일명 예: `src/trading/db/migrations/024_regime_awareness.sql`

```sql
-- SPEC-TRADING-035 REQ-035-1/REQ-035-2(f): regime awareness 캐싱 + 감사 컬럼.
--
-- Macro 페르소나가 이미 emit 하는 regime/risk_appetite (persona_runs.response_json 에만 존재)를
-- system_state(단일 행 id=1)로 승격하여 Decision/Risk/Portfolio 가 텍스트 파싱 없이 조회한다
-- (SPEC-016 Q-5: 신규 테이블 대신 단일 행 확장). persona_runs.regime_at_decision 은 의사결정
-- 시점의 regime 스냅샷(감사 추적, REQ-035-2(f)).
--
-- 멱등: information_schema.columns 가드. 재실행 안전.

DO $$
BEGIN
    -- system_state.current_regime
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'current_regime'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN current_regime TEXT NOT NULL DEFAULT 'neutral'
                CHECK (current_regime IN ('bull','neutral','bear'));
    END IF;

    -- system_state.current_risk_appetite
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'current_risk_appetite'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN current_risk_appetite TEXT NOT NULL DEFAULT 'neutral'
                CHECK (current_risk_appetite IN ('risk-on','neutral','risk-off'));
    END IF;

    -- system_state.regime_updated_at
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'regime_updated_at'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN regime_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
    END IF;

    -- system_state.regime_source_run_id (FK -> persona_runs.id)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'regime_source_run_id'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN regime_source_run_id BIGINT
                REFERENCES persona_runs(id);
    END IF;

    -- persona_runs.regime_at_decision (감사 스냅샷)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'persona_runs' AND column_name = 'regime_at_decision'
    ) THEN
        ALTER TABLE persona_runs
            ADD COLUMN regime_at_decision TEXT
                CHECK (regime_at_decision IS NULL
                       OR regime_at_decision IN ('bull','neutral','bear'));
    END IF;
END $$;

COMMENT ON COLUMN system_state.current_regime IS
    'SPEC-TRADING-035 REQ-035-1: Macro 페르소나가 산출한 시장 체제 캐시. '
    'TTL 7일 초과 시 읽기 시점에 neutral 로 안전 폴백(저장값은 보존).';
COMMENT ON COLUMN system_state.regime_source_run_id IS
    'SPEC-TRADING-035 REQ-035-1(b): current_regime 을 갱신한 persona_runs.id.';
COMMENT ON COLUMN persona_runs.regime_at_decision IS
    'SPEC-TRADING-035 REQ-035-2(f): 해당 Decision/Risk 실행 시점의 current_regime 스냅샷(감사).';

INSERT INTO schema_migrations (version) VALUES ('024_regime_awareness')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"024_regime_awareness"}'::JSONB);
```

> 컬럼명/파일명은 권고안이며 구현 시 일관성 내에서 조정 가능. FK `regime_source_run_id →
> persona_runs(id)` 는 `persona_runs` 테이블이 선행 존재함을 전제(004_personas.sql).

### regime 읽기 헬퍼 (TTL 포함) — `src/trading/db/session.py`

- 신규 헬퍼(예: `get_effective_regime() -> tuple[str, str]`)는 `get_system_state()` 로 행을 읽고,
  `regime_updated_at` 이 `now() - 7d` 보다 오래면 `('neutral', 'neutral')` 반환 + 텔레그램 경고 1회.
  그 외엔 저장된 `(current_regime, current_risk_appetite)` 반환.
- `regime_updated_at = now()` 갱신은 `update_system_state` 의 현재 `updated_at` 마커 방식과 동일하게
  처리(또는 regime 전용 raw UPDATE 1회). research.md §2.1 의 제약 참고.

### Macro 후처리 (REQ-035-1 b/d) — `orchestrator.run_weekly_macro` / `macro.py`

- 매크로 실행 성공(`res.response_json` 존재) 후: `regime`, `risk_appetite` 추출 → 누락 시 스키마
  오류 raise + 캐시 미갱신 + 텔레그램 통지; 정상 시 `update_system_state(current_regime=...,
  current_risk_appetite=..., regime_source_run_id=res.persona_run_id)` + `regime_updated_at=now()`.

---

## Constraints (구현 제약 — 반드시 준수)

- **CLI 경로 강제**: 모든 페르소나 호출은 정식 분기 `is_cli_mode_active() →
  call_persona_via_cli`(`src/trading/personas/base.py:747`, `:555`)를 사용한다. 데일리 매크로는
  `orchestrator.run_weekly_macro` 의 기존 CLI 경로를 재사용한다. **bare `call_persona`
  (`base.py:210`) 금지** — cli_only_mode 에서 유료/크래시.
- **마이그레이션**: raw SQL `024_<name>.sql`, 순차, 멱등(information_schema 가드, `023` 본보기),
  `migrate.py` 가 적용. system_state 모델/헬퍼를 그에 맞게 갱신.
- **Lint**: ruff 는 BLE001 을 select 하지 **않는다** → `# noqa: BLE001` 금지(RUF100 유발).
  평범한 `except Exception:` 사용.
- **테스트**: `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **801 passed /
  6 pre-existing fail**(web_scraper ×1, volatility ×2, tools/registry ×3) — **신규 회귀 0**.
  신규 코드 85%+ 커버리지(TRUST 5).
- **알림**: TTL 폴백 + regime 모드 변경 경고는 기존 Telegram notifier 경유.
- **브랜치**: 작업 브랜치는 이미 `fix/SPEC-TRADING-026-overheating-softening` — **신규 브랜치 생성
  금지.**
- **데이터 제약**: 신규 fetcher 금지. 기존 `flows`(외국인/기관/개인 순매수, pykrx) + KOSPI/KOSDAQ
  OHLCV 만 사용. regime 은 Macro LLM 출력을 신뢰(기계적 임계 계산 금지 — SPEC-016 Q2).

---

## Deferred / Non-Goals (명시적 비목표)

- **신규 시장 데이터 fetcher**: 신용잔고(margin balance), 투자자예탁금(investor deposits),
  V-KOSPI(변동성 지수) (REQ-016-2-4). 향후 SPEC 으로 defer.
- **adaptive impact-5 매크로 뉴스 트리거** (REQ-016-2-3 b). 향후 SPEC 으로 defer.
- **A(손절) 의 regime 연결**: ATR 손절/익절 임계는 별개의 **변동성 레짐**
  (`strategy/volatility/thresholds.py` low/normal/high/extreme)을 쓴다. macro regime 은 stop-loss
  강도와 **분리** 유지(과매도 방지).
- **Phase 3 불장 모드**: 1~2종목 집중, 현금 10%, 후기 사이클 천장 방어. 별도 향후 SPEC.
- **종목 집중(포트폴리오)**: REQ-035-4 는 현금 타깃 시프트만(Q4 = B only). 종목 수 변경 없음.
- **risk 한도(`limits.py`)·회로차단·halt 게이트 로직 변경 없음** — 최종 hard gate 로 불변.

---

## Risks

| ID | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R-1 | LLM 이 프롬프트의 regime 조정 수치를 무시(컨텍스트 주입 한계) | bull 에서 현금이 20% 미만으로 내려갈 수 있음 | REQ-035-2(e) 권고대로 **하드 현금 바닥 Python 가드** 병행. AC 로 검증. |
| R-2 | regime staleness — 매크로가 데일리여도 휴장/장애로 7일 초과 | 잘못된 체제로 분기 | REQ-035-1(c) TTL 'neutral' 폴백 + 텔레그램 경고. |
| R-3 | bull 분기가 과도하게 공격적이 되어 자본 보전 위반 | 가족 부양 자본 손실 | **보수 분기** 채택(현금 바닥 20%, conf −0.05). Phase 3 공격 모드 비목표. |
| R-4 | `update_system_state` 가 `regime_updated_at=now()` 같은 함수 필드를 일반 처리 못 함 | 갱신 누락/오류 | research.md §2.1 — 헬퍼에 마커 추가 또는 regime 전용 raw UPDATE. |
| R-5 | FK `regime_source_run_id` 가 존재하지 않는 run_id 참조 | 마이그레이션/UPDATE 실패 | 실패한 매크로 실행은 캐시 미갱신(REQ-035-1 d). 성공 run 의 id 만 기록. |
| R-6 | bear 의 "레버리지/신용 매수 차단" 이 paper 모드에서 의미 모호 | 무효 분기 | paper 에서도 컨텍스트/가드로 표현(일관). 실거래 시 실효. |

---

## Open Questions

- **Q-1 (구현 결정 — REQ-035-2(e))**: 임계 조정을 프롬프트 컨텍스트 주입 vs Python enforcement 중
  무엇으로? **권고 = 프롬프트 컨텍스트 주입 + 하드 현금 바닥 Python 가드 병행.** 구현자가 run 단계에서
  확정.
- **Q-2**: `update_system_state` 헬퍼를 확장해 `regime_updated_at=now()` 를 마커로 처리할지, regime
  전용 raw UPDATE 함수를 둘지? (research.md §2.1) — 구현 디테일, run 에서 결정.
- **Q-3**: bull/bear 의 "섹터 한도 완만 완화/긴축" 의 정확한 수치(예: 40%→45% / 40%→35%)는 SPEC 에
  고정하지 않았다. 보수 원칙(완만)만 명시 — 구현 시 합리적 보수값 채택 후 AC 로 방향성만 검증.
- **Q-4**: 데일리 06:00 매크로가 `build_macro_context`(06:00 데이터 잡)와 같은 분에 돌면 데이터
  신선도 경합 가능 — 매크로 페르소나 잡을 06:05~06:10 으로 둘지? (현 SPEC 은 06:00 명시, 구현자가
  데이터 의존성 확인 후 미세 조정 허용.)

---

## Traceability

| 요구 | SPEC-016 원본 | 영향 파일 | 테스트(신규) |
|---|---|---|---|
| REQ-035-1 | REQ-016-2-1 | `db/migrations/024_*.sql`(신규), `db/session.py`(regime 헬퍼+TTL), `personas/macro.py`/`orchestrator.run_weekly_macro`(후처리), `prompts/macro.jinja`(스키마 명시), Telegram notifier(TTL/스키마 경고) | `tests/db/test_regime_cache.py` |
| REQ-035-2 | REQ-016-2-2 (보수 변형) | `personas/decision.py`, `personas/risk.py`, `prompts/decision.jinja`, `prompts/risk.jinja`, `db/migrations/024_*.sql`(`regime_at_decision`), 현금 바닥 Python 가드 | `tests/personas/test_regime_branching.py` |
| REQ-035-3 | REQ-016-2-3 (부분, impact-5 defer) | `scheduler/runner.py`(데일리 06:00 잡), `orchestrator.run_weekly_macro`(CLI 경로 재사용) | `tests/scheduler/test_macro_frequency.py` |
| REQ-035-4 | REQ-016-2-2 확장 / Q4=B | `personas/portfolio.py`, `personas/portfolio_gate.py`, `prompts/portfolio.jinja`, `db/session.py`(regime 읽기) | `tests/personas/test_portfolio_regime.py` |

| 외부 의존 | 설명 |
|---|---|
| SPEC-TRADING-016 | Phase 2 원본 요구(REQ-016-2-1~4), Q-2/Q-5/Q4 결정 |
| SPEC-TRADING-033 | 변동성 레짐 ATR — macro regime 과 분리 정합(A) |
| SPEC-TRADING-034 | 포트폴리오 게이트 입력 경로(B), CLI 패턴(REQ-034-9), fail-safe |
| SPEC-TRADING-012 | decision.jinja dynamic_thresholds — 컨텍스트 주입 선례 |
| SPEC-TRADING-029 | balance()/cash_pct·holdings — 현금 타깃 컨텍스트 |
