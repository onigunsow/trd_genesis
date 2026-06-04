# SPEC-TRADING-041 — Acceptance Criteria

> Given-When-Then. 각 요구 영역당 최소 2 시나리오(엣지 케이스 포함). 신규 순수 함수(메시지 조립·P&L
> 계산)는 run 단계 TDD-leaning. 표시 계층 변경이므로 기존 ~1141 통과 스위트 무회귀가 전제.

## AC-1 — 모든 알림에 종목명 표기 [REQ-041-1, 4a]

### AC-1.1 (정상)
- **Given** "한도 위반 차단" 또는 "단기과열 비중 축소" 알림이 종목 `064350`(현대로템)에 대해 발생하고,
  `ticker_name("064350")` 가 `"현대로템"` 을 반환한다.
- **When** `tg.system_briefing()` 메시지가 조립된다(orchestrator 4곳: pre_market trim/breach, intraday trim/breach).
- **Then** 메시지에 `"064350 현대로템"` 형태로 종목코드와 한국 종목명이 함께 표기되어,
  매매 체결 알림(`trade_briefing`)과 일관된다.

### AC-1.2 (엣지: ticker_name None)
- **Given** 알림 대상 종목의 `ticker_name()` 이 None 을 반환한다(매핑 부재).
- **When** 동일 알림 메시지가 조립된다.
- **Then** 시스템은 종목코드만으로 우아하게 fallback 하며(빈 이름 표기·크래시 없음),
  사이클은 정상 진행된다(REQ-041-4a).

## AC-2 — 매도 체결 알림 실현손익(부호+퍼센트) [REQ-041-2, 4b]

### AC-2.1 (정상: 매도 이익)
- **Given** 종목 `064350` 을 평균단가 100,000원에 10주 보유하고, 105,000원에 매도 체결되며 수수료 660원,
  매도 직전 `balance()` 의 `holdings[].avg_cost` 가 가용하다.
- **When** 매도 주문 체결(side=sell) 후 `trade_briefing()` 이 호출된다.
- **Then** 알림에 평균단가 기준 실현손익이 부호·퍼센트와 함께 표기된다:
  `(105,000 − 100,000) × 10 − 660 = +49,340원 (+4.9%)` 형태(예: `"실현손익 +49,340원 (+4.9%)"`).

### AC-2.2 (엣지: avg_cost 부재 → 생략)
- **Given** 매도한 종목의 avg_cost 가 가용하지 않다(전량 매도로 holdings 에서 빠졌거나 데이터 부재).
- **When** 매도 체결 후 `trade_briefing()` 이 호출된다.
- **Then** 시스템은 실현손익 라인을 **우아하게 생략**하며, 0/오값을 진짜처럼 표기하지 않는다(REQ-041-4b).

### AC-2.3 (불변: 매수 알림)
- **Given** 매수(side=buy) 주문이 체결된다.
- **When** `trade_briefing()` 이 호출된다.
- **Then** 매수 알림은 실현손익 라인 없이 기존 포맷 그대로 표기된다(REQ-041-2c).

## AC-3 — /holdings 명령 + /help 등록 [REQ-041-3a/3c, 4c]

### AC-3.1 (정상)
- **Given** 운영자가 텔레그램에서 `/holdings` 를 입력하고, KIS `balance()` 가 2개 보유 종목을 반환한다.
- **When** `emergency.handle("/holdings")` 가 실행된다.
- **Then** 각 보유 종목의 종목명·수량·평균단가·현재가·평가손익(원, 부호)·손익률(%)이 표기되고,
  **총 평가손익(TOTAL)** 요약 라인이 포함된다(daily_report holdings 와 동일 데이터 원천).

### AC-3.2 (엣지: 빈 보유 / KIS API 타임아웃)
- **Given** 보유가 비어 있거나 KIS 클라이언트 구성/조회가 실패(타임아웃 포함)한다.
- **When** `/holdings` 가 처리된다.
- **Then** 시스템은 안전한 메시지로 degrade 하며(봇·사이클 크래시 없음),
  daily_report `_collect_portfolio()` 의 try/except placeholder 패턴을 따른다(REQ-041-4c).

### AC-3.3 (명령 등록)
- **Given** 운영자가 `/help`(또는 `/start`)를 입력한다.
- **When** `_help()` 가 반환된다.
- **Then** `/holdings` 가 명령 목록에 표기된다(REQ-041-3c).

## AC-4 — /pnl net-of-fee 개선 [REQ-041-3b]

### AC-4.1 (정상: 수수료 차감)
- **Given** 당일(CURRENT_DATE) orders 에 매도금액 합 1,000,000원, 매수금액 합 900,000원,
  체결 수수료 합 1,500원이 있다.
- **When** 운영자가 `/pnl` 을 입력해 `_pnl_summary()` 가 실행된다.
- **Then** 보고 실현손익은 gross(1,000,000 − 900,000) 에서 수수료 1,500 을 차감한 NET
  `98,500원` 으로 계산되고, 라벨은 net-of-fee gross·"(추정)" 임을 명시한다(정밀 per-lot 으로 오인 금지).

### AC-4.2 (엣지: 수수료 0 / 거래 없음)
- **Given** 당일 거래가 없거나 수수료가 0 이다.
- **When** `/pnl` 이 실행된다.
- **Then** 시스템은 크래시 없이 0원(또는 거래 없음) 을 안전하게 보고하며, 라벨 문구는 일관 유지된다.

## Definition of Done
- [ ] AC-1~4 시나리오(엣지 포함) 테스트 선행/통과.
- [ ] orchestrator 4 알림(pre_market trim/breach, intraday trim/breach) 종목명 표기 + None fallback.
- [ ] 매도 체결 실현손익(평균단가 기준, 부호+%) 표기 + avg_cost 부재 생략 + 매수 불변.
- [ ] /holdings 신규 명령(종목별 + TOTAL) + 빈 보유/KIS 실패 안전 degrade + /help 등록.
- [ ] /pnl 수수료 차감(net) + 라벨 명확화(FIFO 전환 안 함).
- [ ] DB 마이그레이션 불필요 확정·기록(표시 계층, 스키마 무변경).
- [ ] SPEC-039 합성 체결·KIS paper inquire-balance(VTTC8434R) 불파괴.
- [ ] 기존 알림/명령 포맷 보존(가산적 변경만), 기존 trade_briefing 호출부 byte 불변.
- [ ] 개방 질문 2건 해소(매도 지점 avg_cost 가용성, telegram_bot KIS 클라이언트 배선).

## 품질 게이트
- pytest 커버리지 ≥ 85%(신규 순수 함수: 메시지 조립·P&L 계산·holdings/pnl 포맷).
- 기존 ~1141 통과 스위트 무회귀(0 신규 실패).
- ruff/black 통과. EARS 추적성 유지(spec ↔ acceptance).
- per quality.yaml development_mode(test-backed; 신규 순수 함수 TDD-leaning).
