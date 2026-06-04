# SPEC-TRADING-041 — Implementation Plan

> 코드 미작성. 본 문서는 run 단계 실행 계획·ADR·리스크·테스트 계획·개방 질문 정리.

## 중심 ADR (Architecture Decision Records)

### ADR-1: 종목명 부가는 표시 계층(orchestrator)에서, 리스크 로직은 순수 유지
- **결정:** "한도 위반 차단"/"단기과열 비중 축소" 알림의 종목명은 `tg.system_briefing()` 호출 직전
  메시지 조립 시점(orchestrator)에서 `ticker_name(ticker)` 로 해석해 부가한다.
- **이유:** limits.py 의 breach 문자열은 순수 리스크 판단 산출물 — 표시 관심사(종목명)를 섞으면
  리스크 로직이 표시 의존성을 갖게 된다. `ticker_name`은 orchestrator.py:28 에 이미 import 완료.
- **영향:** limits.py 변경 최소화. orchestrator 4곳 메시지 문자열만 종목명 부가.
  (limits.py breach 텍스트도 코드를 포함하나, 표시 계층에서 이름을 *덧붙이는* 형태로 처리.)

### ADR-2: 매도 P&L 은 평균단가 기준, 기존 balance() 결과 재사용
- **결정:** 매도 실현손익은 `(fill_price − avg_cost) × qty − fee` (평균단가 기준).
  매도 체결 직전 이미 호출된 `bal_after = balance(client)` 의 `holdings[].avg_cost` 를 재사용.
- **이유:** FIFO per-lot 은 과설계·범위 외. 평균단가는 KIS `pchs_avg_pric` 로 즉시 가용.
  매도 체결 직전 balance() 가 이미 호출되므로(orchestrator ~L1203/1659) 여분 API 지연 0.
- **영향:** 여분 inquire-balance 불요(확인 필요 — 개방 질문 1). avg_cost 부재 → P&L 라인 생략.

### ADR-3: /holdings 는 daily_report 의 클라이언트 배선 패턴 재사용
- **결정:** `/holdings` 는 `KisClient(get_settings().trading_mode)`→`balance(client)`→holdings 렌더.
- **이유:** daily_report.py `_collect_portfolio()`(L146-171)가 동일 데이터·동일 try/except 안전
  placeholder 를 이미 구현. emergency.handle() 은 현재 직접 DB 연결만 사용(/pnl) — KIS 클라이언트
  배선 경로를 run 단계가 확인해야 한다(개방 질문 2).
- **영향:** 신규 헬퍼(예: `_holdings_summary()`) 추가. 실패/빈 보유 → 안전 메시지 degrade.

### ADR-4: /pnl 은 경량 net-of-fee gross 유지(FIFO 전환 안 함)
- **결정:** 기존 단일 SQL 에 `orders.fee` 차감 추가, 라벨에 "(추정)" + net-of-fee gross 명시.
- **이유:** /pnl 은 빠른 당일 감(感) 도구 — FIFO per-lot 정밀화는 별도 범위.
- **영향:** _pnl_summary SQL 한 줄 변경 + 라벨 문구. orders 테이블 단일 쿼리 유지.

### ADR-5: paper-first, 가산적 변경만, 기존 포맷 보존
- **결정:** 모든 변경은 가산적(종목명 부가/P&L 라인 추가/신규 명령/라벨 명확화). 기존 알림·명령
  포맷의 구조는 보존한다. SPEC-039 합성 체결·KIS paper inquire-balance(VTTC8434R) 불파괴.
- **이유:** 운영자 일상 사용 도구 — 회귀 시 신뢰 손상. 표시 계층이라 위험은 낮으나 ~1141 통과
  스위트를 회귀시키면 안 된다.

## 마일스톤 (우선순위 기반, 시간 추정 없음)

- **Primary Goal:** REQ-041-1 모든 알림 종목명 표기(orchestrator 4곳) — 즉시 효과, 가장 단순.
- **Secondary Goal:** REQ-041-2 매도 체결 실현손익 부호 표기 — 매도 발생 시 운영자 손익 가시성.
- **Tertiary Goal:** REQ-041-3a/3c /holdings 신규 명령 + /help 등록 — 보유 현황 온디맨드 조회.
- **Final Goal:** REQ-041-3b /pnl net-of-fee 개선 — 손익 정확도·라벨 명확화.
- **횡단:** REQ-041-4 graceful fallback (None 종목명·부재 avg_cost·KIS 실패·빈 보유) — 전 모듈 공통.

## 기술 접근

1. 종목명: orchestrator 4곳(L1163/1188/1619/1642) `tg.system_briefing()` 메시지 문자열에
   `_name = ticker_name(ticker)` 해석 후 `f"{ticker}{f' {_name}' if _name else ''}"` 형태로 부가.
   매매 알림(`trade_briefing`) 포맷과 일관(telegram.py `name_str` 패턴 참고).
2. 매도 P&L: `trade_briefing()` 에 선택적 `realized_pnl`/`realized_pct` 인자 추가(default None →
   기존 호출 불변). orchestrator 매도 체결 후 `bal_after.holdings` 에서 avg_cost 조회 →
   순수 함수 `compute_sell_pnl(fill_price, avg_cost, qty, fee)` 로 계산 → side=sell + avg_cost
   가용 시에만 전달. buy 또는 avg_cost None → 라인 생략.
3. /holdings: emergency.handle() 에 `if cmd == "/holdings": return _holdings_summary()` 분기.
   `_holdings_summary()` 는 daily_report `_collect_portfolio()` 패턴(KisClient→balance→try/except)
   재사용, 종목별 라인 + TOTAL 평가손익. 빈 보유/실패 → 안전 메시지.
4. /pnl: `_pnl_summary` SQL 에 `- COALESCE(SUM(fee) FILTER (WHERE status IN('filled','partial')),0)`
   차감, 라벨에 net-of-fee gross·"(추정)" 명시.
5. /help: `_help()` 문자열에 `/holdings   보유 현황·평가손익` 라인 추가.
6. graceful fallback: 모든 신규 경로에 None/부재/예외 분기(REQ-041-4).

## 리스크 및 대응

| 리스크 | 대응 |
|---|---|
| 매도 P&L 이 잘못된 0/오값 표기 | avg_cost 부재 시 라인 **생략**(REQ-041-4b), 순수 함수 단위 테스트 |
| limits.py breach 문자열에 코드 중복(코드+이름 어색) | 표시 계층에서 이름만 부가, breach 텍스트는 그대로(이중 표기 점검) |
| /holdings KIS 타임아웃·실패로 봇 크래시 | daily_report try/except placeholder 패턴 재사용(REQ-041-4c) |
| 기존 trade_briefing 호출부 회귀 | 신규 인자 default None — 기존 호출 byte 불변, 회귀 테스트 |
| paper 합성 체결과 avg_cost 불일치 | SPEC-039 합성 체결 + KIS paper inquire-balance(VTTC8434R) 경로로 검증 |
| /pnl 라벨 오인(정밀 실현손익으로 착각) | "(추정)" 유지 + net-of-fee gross 명시 문구 |
| ~1141 통과 스위트 회귀 | 가산적 변경만, 전 스위트 회귀 확인(0 신규 실패) |

## 마이그레이션

- 잠정: **불필요**(표시 계층, 신규 컬럼 없음. 현재 최신 029, 027 결번).
- run 단계 첫 확인: 본 SPEC 의 모든 변경이 스키마 무관함을 명시적으로 확인·기록.

## 테스트 계획 (TRUST 5 — 신규 순수 함수는 TDD-leaning)

1. **종목명 부가 메시지 조립** (REQ-041-1, 4a):
   - 정상 ticker → `"코드 이름"` 표기.
   - `ticker_name()` None → 코드만 fallback(빈 이름·크래시 없음).
   - 4 알림 유형(pre_market trim/breach, intraday trim/breach) 메시지 문자열 검증.
2. **매도 P&L 계산·포맷** (REQ-041-2, 4b):
   - `compute_sell_pnl()` 양수/음수/0 부호 + 퍼센트 포맷(`"+12,340원 (+3.2%)"`).
   - avg_cost 부재 → 라인 생략(None 반환/생략 분기).
   - buy 알림은 P&L 라인 없음(side 분기).
3. **/holdings 포맷 + /pnl net-of-fee** (REQ-041-3, 4c):
   - holdings 렌더(종목명·수량·avg_cost·현재가·평가손익·% + TOTAL).
   - 빈 보유 / KIS 실패(예외) → 안전 placeholder 메시지.
   - `_pnl_summary` 수수료 차감 계산 검증(fee>0 케이스에서 gross−fee).
   - `/holdings` 가 `/help` 목록에 등장.
4. **회귀:** 기존 ~1141 통과 스위트 무회귀(0 신규 실패). 기존 trade_briefing/system_briefing
   호출부 포맷 보존.

## 개방 질문 (run 단계 해소 필수)

1. **매도 체결 지점 avg_cost 가용성:** orchestrator 매도 체결 후 `bal_after = balance(client)` 의
   `holdings[]` 에서 *매도한 종목*의 avg_cost 가 (전량 매도 시 보유 0으로 빠질 수 있어) 여전히
   조회 가능한가? 전량 매도면 holdings 에서 사라질 수 있으므로 — 체결 *전* balance 의 avg_cost 를
   캡처하거나, 매도 시그널에 avg_cost 를 동반해야 할 수 있음. run 단계가 확인 후 최소 fetch 결정.
2. **telegram_bot 컨텍스트의 KIS 클라이언트 배선:** telegram_bot.py L75 는 `emergency.handle()` 만
   호출하고 KisClient 를 구성하지 않음. `/holdings` 는 `KisClient(get_settings().trading_mode)` 를
   emergency.py 내부에서 직접 구성(daily_report 패턴)할지, 호출부에서 주입할지 run 단계가 결정.
   daily_report `_collect_portfolio()` 가 내부 구성 패턴을 선례로 제공.
3. **마이그레이션 불필요 확정:** 표시 계층임을 재확인하고 스키마 무변경을 명시 기록.
