# SPEC-TRADING-053 (compact) — 유료 API 비용 누수 완전 차단

status: draft v0.5.0 · priority: high · 2026-06-19 · labels:[trading,cli,cost,api-zero-leak,news,personas] · 코드 한정(운영 액션 이미 적용)

## 목표
strict_cost_zero_mode ON인 동안 **5개** 유료 호출 지점(messages.create=analyzer.py:235·base.py:280/353/408·daily_report.py:436)에서 `client.messages.create()` 0건(관측 가능 증명). CLI/워처 死면 유료 폴백 금지·비용 0 스킵·pending 보존·사이클 graceful 진행. SPEC-052/016 보완. strict OFF는 SPEC-016 폴백 바이트 단위 불변. Zero-leak는 strict ON 게이트 하 절대적(carve-out). ("6"은 페르소나 디스패처 6개만 지칭.)

## 검증된 5 근본원인
1. **셸 dead path:** analyze_news.sh:16/daily_screen.sh:16 부재 nvm → CLI 실패 → 직접 유료 API(6/18 22:16). 작동=.local/bin/claude.
2. **공유 술어 미스매치:** _call_haiku(analyzer.py:235)·_llm_text(daily_report.py:436)가 같은 @block_if_cli_only_mode 공유·is_cli_only_mode(base.py:82) strict 미참조. raise(133)는 결함 아님=채택 차단(호출자 catch 631/637).
3. **self-defeating 가드:** should_defer_paid_call(base.py:577)이 strict AND(플래그)만 차단·_record_cli_failure 3연속→cli_personas_enabled=False 자동전환→CLI 死 순간 가드 풀림.
4. **watcher 중복:** persona_watcher.sh flock 부재→systemd 재시작 고아 중복(6/18 16:00).
5. **[CRITICAL D-CRIT-1] 페르소나 디스패처 직접 누수:** 6 페르소나(decision/micro/portfolio/macro/risk/retrospective)가 `if is_cli_mode_active(): via_cli else: call_persona`. **is_cli_mode_active(base.py:949)≠is_cli_only_mode** — cli_personas_enabled=False **또는 워처 stale**이면 False·**strict 미참조**. else `call_persona`(224)는 데코레이터도 should_defer도 없이 messages.create(280/353/408) 직접. should_defer는 call_persona_via_cli(824) 안에만. → strict ON+워처死=유료 누수. **거짓 전제 교정: "call_persona는 via_cli 폴백으로만"=사실 아님.**

## 5 유료 호출지점(F 계측)
analyzer.py:235(Haiku, 공유술어 B)·base.py:280/353/408(call_persona 3지점, **REQ-053-G 진입점 가드**)·daily_report.py:436(_llm_text Sonnet 휴면, 공유술어 B). (디스패처는 6개지만 messages.create 지점은 5개.)

## EARS (7 모듈)
- **A** 셸 CLI경로 해소+A4 daily_screen 기계폴백 보존.
- **B** 공유 술어 is_cli_only_mode strict 인지화(SSOT)→**raise+호출자catch=graceful skip**(ADR-002). B4 strict OFF·cli_only raise 보존. **B5(교정)** call_persona는 폴백 전용 아님=디스패처 else 직접 호출, 봉인은 G. **B6** scheduler:204 strict시 defer.
- **C** should_defer 분리+strict fail-closed(last-known ON시만, 빈캐시→fail-open, in-process캐시).
- **D** strict ON시 _record_cli_failure 자동전환 부수효과만 생략·카운터 리셋 항상. **D4** is_cli_mode_active(949) strict 인지화(보조 라우팅 안전망).
- **E** persona_watcher.sh flock.
- **F** 5지점 messages.create 직전 PAID_CALL 계측(거짓PASS 방지)·strict OFF 불변. 차단 audit emit: analyzer=호출자 catch(analyzer.py:641 NEWS_INTEL_HAIKU_FAIL 인접), call_persona=진입점 가드 raise 직전(데코레이터엔 audit 없음, D4).
- **G(신규, D-CRIT-1 주 방어)** call_persona(224) **진입점 should_defer 가드**(반드시 `try:` 263 **이전** 배치=내부 except 393이 RuntimeError 삼켜 PersonaResult(error) 변환 방지, D3)→strict ON시 messages.create 전 raise(라우팅 무관 3지점 원천 봉인)+CLI_DEGRADED_DEFER 계측. **graceful skip(D2): 디스패처는 로컬 try/except 없음→raise가 상위 경계(scheduler _wrap runner.py:158 / orchestrator 1123-1125)서 흡수→cost-0 사이클 스킵**(디스패처 신규 catch 불필요). G4 strict OFF 불변.

## 6 ADR
- **001** strict ON 게이트. **002(재작성)** raise+호출자 catch=graceful skip. **003** C+D 방어심층. **004** 공유 술어 strict인지화 SSOT+blast radius 4호출자. **005** strict fail-closed+콜드스타트 fail-open. **006(신규)** call_persona 진입점 가드=주 방어(라우팅 누락·미래 페르소나 대비), is_cli_mode_active strict(D4)=보조.

## Exclusions
anthropic import/API경로(_call_haiku/call_persona/_llm_text) 제거금지·DB스키마/마이그 변경금지(fail-closed=in-process캐시)·trading/sizing/decision 미변경·billing/재인증 운영자책임·watcher CLI경로 재변경금지.

## Files (MODIFY)
scripts/analyze_news.sh·daily_screen.sh·persona_watcher.sh · analyzer.py · daily_report.py · personas/base.py(공유술어 B·should_defer C·_record_cli_failure D·**call_persona 진입점 가드 G(try 263 이전)**·**is_cli_mode_active strict D4**·계측 F). **디스패처 6개는 신규 코드 없음**(상위 경계 흡수). (+grep nvm 스윕)

## 순서·리스크
구현순: base.py(B→C→**G 진입점 가드(try 이전)+계측→D4→D**→F)→analyzer.py(F)→daily_report.py(F+휴면가드)→셸(A)→watcher(E). 디스패처 신규 코드 없음. repro-first, 회귀0(~1702). R1 fail-closed 트레이드오프. R2 공유 술어 4호출자 blast. R3 호출자 catch·narrative live. **R5(정정 D2) 디스패처는 catch 안 함→raise가 _wrap(runner.py:158)/orchestrator(1123-1125)서 흡수=cost-0 스킵. run: orchestrator 재-raise가 전체 사이클 중단 안 시키는지 확인.**

## 검증 게이트 (run+redeploy 후)
뉴스 슬롯 "Sending NNN lines to Claude CLI"+no-such-file 0 · **strict ON+워처 stale에서 디스패처 call_persona가 messages.create 미도달(유료 0)** · 관측가능 PAID_CALL 0건(5지점 계측)+cost=0 · watcher 중복 0 · 뉴스 복원 · 차단 시 CLI_DEGRADED_DEFER 관측.
