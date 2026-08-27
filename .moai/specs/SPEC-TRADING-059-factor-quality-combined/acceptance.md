# SPEC-TRADING-059 acceptance — 저변동성 + 퀄리티 결합 팩터

> Given-When-Then 인수 시나리오. 모든 검증은 컨테이너(`docker exec trading-app`)에서
> 실행한다. 단위테스트는 픽스처 주입(네트워크 차단), 실데이터는 KRX 세션 컨테이너.

## DoD (Definition of Done)

- [ ] M4: `compute_quality_signal`(ROE=EPS/BPS) 순수 함수 — 결정적·PIT·결측 명시 제외, 픽스처 단위테스트 통과.
- [ ] M4: 펀더멘털 이력 backfill + PIT 펀더멘털 로더 — 스키마 변경 0, ts≤cutoff 불변식.
- [ ] M4: 펀더멘털 커버리지 fail-closed 게이트 + 상폐 펀더멘털 회수 실증 기록.
- [ ] M5: `combine_factor_scores`(z-score 합성) + 월간+허용밴드 1/N + 회전 측정, `engine.run`·`adapt_to_scorecard` 경유.
- [ ] M6: 결합 walk-forward + Bonferroni(N≥2) + 50% 할인 + `compose_verdict` 단일 AND + 결합-증분 비교.
- [ ] M6: `factor-alpha` CLI 동작(entry-alpha 패턴), 정직 판정 리포트 출력.
- [ ] 058/057 회귀 0(기존 테스트 그린). money-weighted 알파 미사용. 라이브 경로 미접촉.
- [ ] 정직 프레이밍: "결합 알파 없음" / "퀄리티 미개선" = 유효한 성공으로 서술.

## AC-1 (REQ-059-M4-1/4-4) 퀄리티 신호 결정성·결측 제외
- **Given** EPS/BPS 픽스처(일부 종목 BPS≤0 또는 null) + as_of_date
- **When** `compute_quality_signal`를 두 번 호출
- **Then** 동일 ROE 랭킹 반환(결정적); BPS≤0/null 종목은 `excluded`에 기록되고 랭킹에서 제외(impute 없음).

## AC-2 (REQ-059-M4-3) 퀄리티 point-in-time
- **Given** as_of_date 이후 EPS/BPS 행을 포함한 픽스처
- **When** as_of_date 기준 신호 계산
- **Then** as_of_date 이후 데이터가 랭킹에 영향 0(미래 누출 없음).

## AC-3 (REQ-059-M4-6) 펀더멘털 커버리지 fail-CLOSED
- **Given** 펀더멘털 커버리지 게이트 결과 = absent 또는 상폐 펀더멘털 회수 불가
- **When** 결합 백테스트 판정
- **Then** 결과가 "fundamentals-survivorship-biased upper bound — bound only"로 강제 다운그레이드되고, signed alpha 보고가 차단된다.

## AC-4 (REQ-059-M5-1/5-2) z-score 결합 + 1/N
- **Given** 저변동 랭킹 + 퀄리티 랭킹 픽스처
- **When** `combine_factor_scores(method="zscore")` → top-N 선택
- **Then** 결합 = z(역변동)+z(ROE)로 top-N 선정, 선택 종목 비중 = 1/N 등가중(합=1.0), ML 가중 미사용.

## AC-5 (REQ-059-M5-3) 월간 + 허용 밴드 저회전
- **Given** 월간 리밸런스 결합 비중 행렬
- **When** `measure_turnover` 측정
- **Then** 회전율 보고됨; 허용 밴드 적용 시 비밴드 대비 회전 감소; 50%/월 초과 시 위반 플래그.

## AC-6 (REQ-059-M5-5 / C-7) time-weighted only
- **Given** 결합 백테스트 `BacktestResult`
- **When** GO 게이트 입력 생성
- **Then** `adapt_to_scorecard`만 경유(time-weighted alpha); `benchmark.py` money-weighted 알파가 어디에서도 GO 입력에 유입되지 않음.

## AC-7 (REQ-059-M6-2) Bonferroni N≥2 자동 강화
- **Given** 저변동/퀄리티/결합 3변형 검정
- **When** `apply_bonferroni(n_factors=K)` (K≥2)
- **Then** 조정 유의수준 = α/K로 강화됨; 양성 부호만으로 PASS 불가.

## AC-8 (REQ-059-M6-4a/4b) 단일 AND 판정 + 결합 증분
- **Given** walk-forward OOS 결과(n≥30, 게이트 통과)
- **When** `compose_verdict` 호출 + 결합 vs 저변동-단독 비교
- **Then** 생존편향/펀더멘털→n<30→Bonferroni→50%할인알파→scorecard GO 모두 충족해야 PASS; 결합이 저변동 단독을 못 이기면 "퀄리티 미개선=유효한 성공"으로 서술.

## AC-9 (REQ-059-M6-5) 표본 floor = 리밸런스 수
- **Given** 리밸런스 주기 n<30 (intra-rebalance trade 다수)
- **When** 판정
- **Then** INCONCLUSIVE(알파 부호·크기 무관); trade 수로 PASS leak 없음.

## AC-10 (REQ-059-M6-6/6-7) CLI + 페이퍼 전용
- **Given** `factor-alpha` CLI 실행(컨테이너)
- **When** 결합 walk-forward 완료
- **Then** 판정 리포트 출력; GO여도 페이퍼 OOS 전용; `order.py`/`smoke_gate.py`/live_unlocked 미접촉.

## AC-11 (REQ-059-M6-8) 정직성 플래그
- **Given** 임의 판정 결과
- **When** 리포트 렌더
- **Then** (a) 비용 floor 플래그, (b) 게이트 bound-only 시 최우선 경고, (c) ROE=EPS/BPS 프록시 한계, (d) pykrx EPS PIT caveat(해당 시)가 모두 표시됨.

## 엣지 케이스
- 전 종목 펀더멘털 결측 윈도 → 해당 리밸런스 스킵(0 비중), 크래시 없음.
- 저변동·퀄리티 랭킹 교집합이 N 미만 → 가용 종목으로 1/N(축소 N 명시).
- 058 저변동 단독 결과 부재(미실행) → 결합-증분 비교는 저변동 단독을 동일 하니스로 재계산.
- 펀더멘털 backfill 미완(R-1) → M5/M6 BLOCKED 상태 명시, 거짓 PASS 금지.

## 품질 게이트
- 058/057 기존 테스트 회귀 0. 신규 순수 함수 ≥85% 커버리지. SQL/DB 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 통과. ruff 클린.
