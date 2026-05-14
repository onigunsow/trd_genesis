---
spec_id: SPEC-TRADING-023
version: 0.1.0
status: draft
created: 2026-05-14
priority: high
---

# Acceptance Criteria — SPEC-TRADING-023

본 문서는 SPEC-023 의 6개 핵심 acceptance 시나리오를 Given/When/Then 형식으로 정의한다. 각 시나리오는 spec.md 의 REQ-023-1 ~ REQ-023-6 과 매핑된다.

---

## 시나리오 1 — 281820 시나리오 정상 auto-expansion (REQ-023-1, REQ-023-2, REQ-023-5)

**Given**:
- micro persona 가 candidate `281820` (케이씨텍) 을 confidence 0.55 로 반환
- `cache.get_latest_ohlcv_ts("281820")` 가 `None` 반환 (캐시 미스)
- `dynamic_tickers` 테이블에 `281820` 미등록
- SPEC-018 의 blocked_tickers 에 `281820` 미포함
- pykrx_adapter 가 281820 에 대해 정상 응답

**When**:
- pre_market cycle 의 orchestrator 가 micro→decision 전환 hook 에 도달

**Then**:
1. `expand_universe_for_tickers(["281820"], cycle_kind="pre_market")` 이 호출됨
2. `pykrx_adapter.fetch_ohlcv("281820", today-90d, today)` 가 호출되어 ~ 60~62 행 (영업일) upsert
3. `pykrx_adapter.fetch_flows("281820", today-90d, today)` 가 호출되어 동등 수 행 upsert
4. `dynamic_tickers` 테이블에 row `(ticker="281820", source="micro_recommendation", first_seen_at=now)` INSERT
5. candidate 리스트에 `281820` 유지된 채로 decision persona 호출
6. decision persona 가 OHLCV/flows 데이터를 기반으로 정상 signals 반환 (HOLD 가 OHLCV 부재 사유였다면 이제는 fundamental + technical 종합 판단으로 BUY/HOLD/SELL 중 하나)
7. INFO 로그에 `expand_universe_for_tickers: cycle_kind=pre_market requested_tickers=1 success_count=1 error_count=0 timeout_count=0 total_rows_upserted=~120 duration_ms=<N> dynamic_universe_size=1` 출력

---

## 시나리오 2 — 다음날 16:00 cron 이 dynamic_ticker 자동 포함 (REQ-023-2, REQ-023-5, SPEC-019 통합)

**Given**:
- 어제 시나리오 1 로 `281820` 이 `dynamic_tickers` 에 등록됨
- 오늘 16:00 KST mon-fri AND `is_trading_day()` True
- SPEC-019 의 `data_refresh_ohlcv` cron 이 trigger

**When**:
- `refresh_market_data.refresh_ohlcv()` 가 호출되어 `get_data_universe()` 평가

**Then**:
1. `get_data_universe()` 반환 리스트에 `281820` 포함됨 (`dynamic_universe.list_active()` 의 contribution 으로)
2. 281820 은 priority order 의 "dynamic" bucket 에 속함 (screened 다음, holdings 이전)
3. `refresh_ohlcv` 의 loop 에서 281820 의 incremental fetch 발생 (전일 16:00 이후 1일치)
4. `cache.upsert_ohlcv` 가 281820 의 오늘 행 upsert
5. SPEC-019 의 metric 로그 `success_count` 가 +1 증가 (이전 universe 대비)
6. 별도 auto-expansion 트리거 없이 영구 monitoring 진행 — micro 가 다시 추천해도 has_recent_ohlcv() True 로 expand 호출되지 않음

---

## 시나리오 3 — Delisted ticker graceful drop (REQ-023-3)

**Given**:
- micro persona 가 candidate `XXXXXX` (가상의 delisted ticker) 를 추천
- `cache.get_latest_ohlcv_ts("XXXXXX")` 가 None 반환
- pykrx_adapter 가 `XXXXXX` 에 대해 `KeyError` 또는 빈 DataFrame 반환 (delisted)

**When**:
- orchestrator hook 이 `expand_universe_for_tickers(["XXXXXX"], cycle_kind="intraday")` 호출

**Then**:
1. pykrx fetch 가 예외 발생 (또는 빈 결과)
2. logger.warning (`auto_expansion failed for XXXXXX: <exception>`) 출력
3. `dynamic_tickers` 테이블에 `XXXXXX` row **추가되지 않음** (REQ-023-3 b)
4. orchestrator 의 candidate 리스트에서 `XXXXXX` 제거
5. 나머지 candidate (있다면) 로 decision persona 정상 호출
6. 나머지 candidate 가 0개여도 decision persona 는 빈 리스트로 호출되어 `signals: []` 반환 — 예외 전파 없음
7. INFO 로그의 metric: `success_count=0 error_count=1` 표기
8. 다음날 SPEC-019 daily refresh 가 XXXXXX 를 fetch 시도하지 않음 (dynamic_tickers 미등록)

---

## 시나리오 4 — FIFO eviction at 100-cap (REQ-023-2 d, REQ-023-5 d)

**Given**:
- `dynamic_tickers` 테이블에 정확히 100 개 row 존재 (cap 도달)
- 가장 오래된 row 의 `first_seen_at = 2026-01-01 00:00:00+09:00`, ticker = `OLDEST`
- micro persona 가 신규 ticker `NEWEST` 추천 (universe-out)
- `pykrx_adapter` 가 `NEWEST` 에 정상 응답

**When**:
- orchestrator hook 이 `expand_universe_for_tickers(["NEWEST"], ...)` 호출

**Then**:
1. `NEWEST` 의 fetch 성공
2. `dynamic_universe.register("NEWEST", "micro_recommendation")` 호출 시 cap 검사 발생
3. 100-cap 도달 감지 → 단일 transaction 으로:
   - DELETE FROM dynamic_tickers WHERE ticker = 'OLDEST'
   - INSERT INTO dynamic_tickers (ticker, source, first_seen_at, last_used_at) VALUES ('NEWEST', 'micro_recommendation', now(), now())
4. transaction COMMIT 후 `SELECT COUNT(*) FROM dynamic_tickers` = 100 (cap 유지)
5. `OLDEST` 는 `list_active()` 반환에서 제외
6. `NEWEST` 는 `list_active()` 반환에 포함
7. INFO 로그 2건: `dynamic_universe evicted ticker=OLDEST (FIFO, was first_seen=2026-01-01...)` + `dynamic_universe registered ticker=NEWEST source=micro_recommendation`
8. 다음날 SPEC-019 daily refresh 에서 OLDEST 는 더 이상 fetch 되지 않음 (universe-out)

---

## 시나리오 5 — Timeout drop (REQ-023-4, REQ-023-3)

**Given**:
- micro persona 가 4개 candidate 추천 (`A`, `B`, `C`, `D`), 모두 universe-out
- `pykrx_adapter.fetch_ohlcv` 가 ticker `C` 에 대해 35s 행 (실제 pykrx hang 또는 mock delay)
- per-ticker timeout = 30s, total batch timeout = 120s

**When**:
- `expand_universe_for_tickers(["A", "B", "C", "D"], cycle_kind="pre_market")` 호출

**Then**:
1. ticker A fetch 정상 완료 (~ 2s)
2. ticker B fetch 정상 완료 (~ 2s)
3. ticker C fetch 가 30s 경과 시 abort, logger.warning (`auto_expansion timeout for C after 30s`)
4. ticker C 는 `dynamic_tickers` 에 등록되지 않고, candidate 리스트에서 제거
5. ticker D fetch 정상 완료 (~ 2s)
6. 전체 batch 소요 시간 < 120s 이므로 total timeout 발동 없음
7. INFO 로그의 metric: `success_count=3 error_count=0 timeout_count=1`
8. decision persona 는 `[A, B, D]` 로 호출됨 (C 제외)
9. 별도 시나리오: 모든 candidate 가 30s 씩 hang 시 → ticker A 30s + ticker B 30s + ticker C 30s + ticker D 30s = 120s = total timeout 도달 → ticker D 의 fetch 가 시작 전 abort 되거나 진행 중 abort 됨 → decision persona 는 `[]` 로 호출되어 `signals: []` 반환

---

## 시나리오 6 — Daily report integration (REQ-023-6)

**Given**:
- 오늘 (2026-05-14) 의 cycle 들에서 auto-expansion 총 3건 발생: `281820`, `068270`, `005935`
- `dynamic_tickers` 테이블에서 `first_seen_at::date = '2026-05-14'` 인 row = 3개
- 16:00 KST `daily_report` cron 실행

**When**:
- `daily_report.generate()` 가 호출되어 보고서 작성

**Then**:
1. 보고서 본문에 다음 행이 정확히 포함됨:
   `오늘 auto-expansion: 3건 (티커: 005935, 068270, 281820)` (정렬: ticker code ascending)
2. 발생 건수 0건일 때는 다음 중 하나:
   - 행 자체 미표시 (선호) — 보고서 간결성 우선
   - 또는 `오늘 auto-expansion: 없음` 명시
3. SPEC-019 의 기존 daily report 행 (cron 잡 metric, stale 상태) 은 모두 그대로 유지 — 회귀 없음
4. Telegram daily report 메시지에 본 행 포함되어 운영자에게 한 번에 전달
5. 별도의 즉시 Telegram 알람은 송출되지 않음 (REQ-023-6 f)

---

## 추가 회귀 시나리오 (보조 검증, optional)

### 시나리오 R-1 — SPEC-018 blocked_tickers filter 와의 순서

**Given**:
- micro 가 candidate `281820` 추천 (universe-out)
- SPEC-018 의 blocked_tickers 에 `281820` 포함 (단기과열로 가정)

**When**:
- orchestrator hook 이 candidate 처리

**Then**:
1. auto-expansion 이 **먼저** 실행되어 281820 의 데이터 fetch + dynamic_tickers 등록
2. **그 다음** blocked_tickers filter 가 281820 을 candidate 리스트에서 제거
3. decision persona 호출 시 candidate 리스트에 281820 없음
4. 281820 은 dynamic_tickers 에 영구 등록 — 다음날 blocked 해제 시 즉시 monitoring 가능

WHY: blocked 는 일시적 상태 (수일~수주). dynamic_universe 등록은 영구. 데이터를 미리 확보하면 blocked 해제 직후 즉시 활용 가능.

### 시나리오 R-2 — 모든 candidate 가 이미 universe 내

**Given**:
- micro 가 candidate `["005930", "000660", "035420"]` 추천 (모두 DEFAULT_WATCHLIST + KOSPI200 top-50 내)
- 모든 ticker 의 `cache.get_latest_ohlcv_ts` 가 today 또는 yesterday 반환 (recent)

**When**:
- orchestrator hook 도달

**Then**:
1. `to_expand = []` (universe-out 후보 없음)
2. `expand_universe_for_tickers` 호출되지 않음 (또는 빈 리스트로 호출되어 즉시 no-op 반환)
3. decision persona 가 원본 candidate 리스트 그대로 호출
4. 추가 pykrx API 호출 0건 — 비용 무시 가능
5. dynamic_tickers 변경 없음

---

## Definition of Done

본 SPEC 이 완료된 것으로 간주되는 조건:

- [ ] 6개 핵심 시나리오 (1~6) 가 단위 테스트 / 통합 테스트로 모두 검증
- [ ] 회귀 시나리오 R-1, R-2 가 단위 테스트로 검증
- [ ] 기존 488개 테스트 전수 통과 (회귀 없음)
- [ ] coverage ≥ 85% (`.moai/config/sections/quality.yaml` 기준)
- [ ] ruff / black 통과
- [ ] PR 사용자 리뷰 통과
- [ ] `make redeploy` 후 5/5 healthcheck 통과
- [ ] APScheduler 의 cron 잡 카운트 변동 없음 (SPEC-019 의 19개 그대로)
- [ ] 실제 cycle 에서 auto-expansion 동작 검증 (다음 pre_market 또는 intraday 에서 universe-out ticker 추천 발생 시)
- [ ] daily report 의 auto-expansion 행 정상 표시 (또는 미발생 시 미표시)
- [ ] 다음날 SPEC-019 daily refresh 가 dynamic_tickers 자동 포함 검증
