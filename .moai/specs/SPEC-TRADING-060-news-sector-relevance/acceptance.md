# SPEC-TRADING-060 수용 기준 (Acceptance Criteria)

형식: Given-When-Then. 실행: 컨테이너 전용(`docker exec trading-app`), 오프라인 pytest + 통합 테스트(실 Postgres).

## 시나리오 1 — 2026-07-03 오경보 재현 → 발화 0 [핵심 HARD 게이트, REQ-060-3·4]

**Given** 2026-07-03 라이브 DB(`news_alerts_sent` ⋈ `news_articles`)에서 실측한 **실제** 두 클러스터 구성으로 fixture 를 만든다(허수아비 금지):

- **클러스터 A (01:15 발화, 태깅=`finance_banking`)** — 멤버 섹터 실측 분포: `stock_market` x22 · `semiconductor` x9 · `macro_economy` x2 · `finance_banking` x1 · `biotech_pharma` x1 (총 35). 대표 멤버 제목 예: "한화솔루션 석유화학 제품 가격 인하"(저장 `stock_market`), "은행권 FDI 원스톱 서비스"(저장 `semiconductor`), "임신중절수술"(저장 `stock_market`). impact_max = 5.
- **클러스터 B (08:15 발화, 태깅=`defense_aerospace`)** — 멤버 섹터 실측 분포: `energy_commodities` x9(82%) · `defense_aerospace` x2 · `finance_banking` x0 (총 11). 멤버 제목 예: "반도체 흔들릴 때 피난처 된 금융주"(저장 `energy_commodities`), "SK하이닉스 목표가 상향"(저장 `energy_commodities`), "S&S 철강 BSI 7월 전망"(저장 `energy_commodities`), "포스코 철 스크랩 구매"(저장 `energy_commodities`), "수전해 수소 STS 강관 납품"(저장 `energy_commodities`), "코스피 8000선 반도체 피크아웃"(저장 `energy_commodities`), "NH농협은행 농식품펀드"(저장 `defense_aerospace`), "AWS FDE 10억 달러 투자"(저장 `defense_aerospace`). impact_max = 5.
- 운영자 실보유 = 015760(한국전력, `ticker_metadata.sector`=`전기·가스` → `energy_commodities`), 316140(우리금융지주, `ticker_metadata.sector`=`금융` → `finance_banking`). 워치리스트 없음.

**When** `tag_portfolio_relevance`를 신규 3중 게이트로 실행한다.

**Then — 클러스터 A 단계별 walk-through (억제):**
1. 다수결(REQ-060-3): impact 가중 최빈 = `stock_market`(22개 캐치올). 첫 기사 상속의 `finance_banking`(1개, 3%)은 폐기된다.
2. 티커 직접일치(REQ-060-4a): 실보유 회사명(`한국전력`(015760)·`우리금융지주`(316140))을 멤버 제목/분석키워드에 정확 부분문자열로 검사. 멤버 661135 "우리은행 삼성월렛머니, 가입자 250만명 돌파" 에는 `우리금융지주`가 부분문자열로 **없다**(별칭 `우리은행`은 매칭 안 함 — D9). 다른 멤버 제목에도 두 회사명이 없음 → **미발화**(결정론적).
3. 섹터 경로(REQ-060-4b): 클러스터 섹터 `stock_market` 은 캐치올이라 **섹터기반 알림 자격에서 명시적으로 제외**된다(D10, 코로보레이션 후보 불가). 설령 자격이 있었다 해도 `stock_market` ∉ 보유 섹터 {`energy_commodities`, `finance_banking`}(정밀-우선 매핑이 캐치올로 매핑하지 않으므로 보유 섹터에 캐치올 부재) → 섹터 불일치. finance 는 1개(3%)뿐이라 quorum 도 불성립. → 미발화.
4. 결과: **발화 0**.

**Then — 클러스터 B 단계별 walk-through (억제, 코로보레이션이 load-bearing):**
1. 다수결(REQ-060-3): impact 가중 최빈 = `energy_commodities`(9개). 첫 기사 상속의 `defense_aerospace`(2개)는 폐기된다.
2. 티커 직접일치(REQ-060-4a): 실보유 회사명(`한국전력`·`우리금융지주`)이 클러스터 B 멤버 제목/분석키워드에 부분문자열로 없음(멤버는 SK하이닉스·포스코·NH농협은행·AWS 등, 두 회사명 미포함) → **미발화**.
3. 섹터 quorum(REQ-060-4b): `energy_commodities` 동의율 82% >= 50% → **quorum 통과**.
4. 보유 섹터 일치: `energy_commodities` ∈ 보유 {`energy_commodities`(015760 한국전력), `finance_banking`} → **일치**. ⚠️ 여기까지(다수결+quorum+섹터일치)만으로는 **오경보가 재발화한다**(감사 D1 지적).
5. **키워드 코로보레이션(REQ-060-4, 결정적 차단)**: 멤버 제목을 `sector_classifier._SECTOR_KEYWORDS` 로 채점 — 에너지 키워드(유가·원유·OPEC·정유·LNG·원자재·이란·사우디·중동 등)는 어느 제목에도 없음(**score(energy) = 0**). 반면 "반도체 피크아웃"·"반도체 흔들릴 때" → `semiconductor`(득점 4), "철강 BSI"·"포스코" → `steel_materials`(득점 4). 확증 규칙(D12): S=`energy_commodities` 는 `score(S) >= 1 AND score(S) == max` 를 요구하는데 score(S)=0 < 1 이고 max=4(semiconductor·steel 공동) 미만 → **미확증**. semiconductor·steel 의 4:4 동점 여부는 무관(energy 가 0 이므로 어떤 동점 처리에서도 탈락) → 섹터 경로 발화 불가.
6. 결과: **발화 0**. (코로보레이션 게이트가 없으면 이 클러스터는 발화한다 — 이 단계가 D1 을 닫는 핵심.)

**And**
- `news_alerts_sent`에 두 오경보의 키가 신규로 기록되지 않는다.
- 라이브검증: 07-03 데이터로 `tag_portfolio_relevance` 재실행 시 두 클러스터 모두 `alerts_sent` 증가 0.

## 시나리오 2 — 정당한 포트폴리오 연관 뉴스 → 발화 [REQ-060-4]

**Given** 클러스터 C: 대표 제목 "우리금융지주, 분기 실적 어닝 서프라이즈", sector `finance_banking`, 멤버 기사 6개 중 5개(83%)가 `finance_banking` 동의, impact_max = 5. 멤버 제목에 "은행"·"지주"·"증권사" 등 finance 키워드가 존재해 코로보레이션 확증됨. 운영자 실보유에 316140(우리금융지주, `ticker_metadata.sector` = `금융` → finance_banking) 포함.

**When** `tag_portfolio_relevance`를 실행한다.

**Then**
- 경로 (a) 티커 직접일치: 클러스터가 316140을 언급/연관 → live holdings 매칭 → 발화(섹터 판정 무관). **또는** 경로 (b) 섹터 경로: `finance_banking` quorum 83% >= 50% AND 보유 섹터 일치 AND 코로보레이션 확증(제목 finance 키워드 argmax) → 발화.
- 텔레그램 메시지 포맷 불변: `[NEWS ALERT] 우리금융지주… (Impact 5/5, Sector: finance_banking) — 포트폴리오 관련 고위험 뉴스 감지`.

## 시나리오 2b — 티커 직접일치: 회사명 부분문자열 (양성/음성) [REQ-060-4a, D9]

**Given** 운영자 실보유에 316140(우리금융지주, `ticker_metadata.name` = `우리금융지주`)이 포함되고 워치리스트는 없다. 두 클러스터를 별도로 평가한다:
- **클러스터 P (양성)**: 대표 제목 "우리금융지주, 분기 실적 어닝 서프라이즈" 를 멤버로 포함. 클러스터 섹터는 임의(섹터 판정과 무관).
- **클러스터 N (음성)**: 멤버 제목이 "우리은행 삼성월렛머니, 가입자 250만명 돌파" 뿐이고 회사명 `우리금융지주` 는 어떤 멤버 제목/분석키워드에도 부분문자열로 없다(자회사 브랜드 `우리은행` 만 등장). 섹터 경로도 불성립(quorum/코로보레이션 미충족)한다고 가정.

**When** 각 클러스터에 `_ticker_direct_match(cluster, live_tickers={316140:'우리금융지주'})` 를 실행한다.

**Then**
- 클러스터 P → **True**(회사명 `우리금융지주` 가 멤버 제목의 정확 부분문자열) → 섹터 판정과 무관하게 발화.
- 클러스터 N → **False**(별칭 `우리은행` 은 매칭하지 않음, 퍼지·자회사 금지 — D9) → 티커 경로 미발화. 섹터 경로도 불성립이므로 최종 **발화 0**.
- 이 음성 케이스가 2026-07-03 클러스터 A 재현의 발화 0 을 결정론적으로 보장하는 핵심 규칙이다(661135 "우리은행…" 은 316140 에 티커 일치하지 않음).

## 시나리오 3 — 미매핑 티커/섹터 → 크래시 없음·가짜 매칭 없음 [REQ-060-1]

**Given** `ticker_metadata`에 없는 티커(예 신규 상장 999999) 또는 업종명이 `news_sector_map`에 없는 티커가 워치리스트에 있다.

**When** `resolve_ticker_sector('999999')` 및 `get_watchlist_sectors()`를 호출한다.

**Then**
- `resolve_ticker_sector` → `None`(가짜 "stock_market" 캐치올 금지).
- `get_watchlist_sectors`에서 해당 티커는 섹터 맵에서 제외된다(no fake match). 크래시·예외 없음.
- 이 티커는 어떤 클러스터 섹터와도 매칭되지 않아 오경보를 유발하지 않는다.

## 시나리오 4 — 실보유 015760/316140 매핑 (하드코딩 철폐 실증) [REQ-060-1]

**Given** 라이브 `ticker_metadata`(54행·13개 distinct 업종명, `sector_loader`로 갱신)의 실제 값:
- 015760 한국전력: `sector` = `전기·가스`(가운뎃점), `industry` = `전기·가스`.
- 316140 우리금융지주: `sector` = `금융`, `industry` = `기타금융`.
- `TICKER_SECTOR_MAP`에는 두 티커 모두 없음(과거 폴백 = stock_market).
- YAML `news_sector_map`(정밀 우선)에서 `금융` → `finance_banking`, `전기·가스` → `energy_commodities` 로 매핑됨.

**When** 컨테이너에서 `resolve_ticker_sector('015760')`, `resolve_ticker_sector('316140')`를 실행한다.

**Then**
- 316140 → `finance_banking`(업종명 `금융` 매핑). 015760 → `energy_commodities`(업종명 `전기·가스` 매핑).
- 결과가 하드코딩이 아닌 DB `ticker_metadata` + YAML 매핑에서 유도됨을 코드 경로로 확인(`TICKER_SECTOR_MAP` import 부재).
- **주의**: '전기가스업'·'금융업'·'전기전자' 같은 옛 리터럴로 YAML 을 작성하면 두 티커 모두 `None` 으로 미해소되어 full coverage 로 폴백 → 오늘보다 악화. YAML 은 **실 `ticker_metadata` 업종명(가운뎃점·단형)** 그대로 seed 해야 한다.

## 시나리오 5 — 클러스터 다수결·tie-break [REQ-060-3]

**Given** 클러스터에 기사 4개: {A: semiconductor impact 3, B: finance_banking impact 5, C: finance_banking impact 2, D: semiconductor impact 3}.

**When** `_majority_sector`를 계산한다.

**Then**
- impact 가중: finance_banking = 5+2 = 7, semiconductor = 3+3 = 6 → finance_banking 채택.
- 단일 기사 클러스터: 그 기사 섹터.
- impact 가중 동수 케이스: 기사 수 → 최종 동점 시 최고 impact 기사 섹터. 결정론적.

## 시나리오 6 — CLI 섹터 필드 누락 → 폴백 체인 [REQ-060-2]

**Given** analyze_news CLI 결과의 한 항목에 `sector` 필드가 없거나 무효값("nonsense")이다. 해당 기사의 피드 섹터 = `macro_economy`, 제목에 "반도체 HBM" 포함.

**When** `import_host_results()`가 결과를 import 한다.

**Then**
- `_corrected_sector`가 무효 sector 를 무시(None) → 크래시 없음.
- 폴백 체인: CLI 무효 → 키워드 분류기(`classify_sector`)가 "반도체"/"HBM" 히트로 `semiconductor` 반환 가능 → 적용. 키워드도 미확신 → 피드 `macro_economy` 유지(last-resort).
- importer 는 어느 경우에도 유료 API 폴백을 유발하지 않는다(strict_cost_zero).

## 엣지 케이스

- **빈 `ticker_metadata` / 매핑 섹터 0개 (D4)**: `resolve_ticker_sector`가 모든 티커에 `None` → `get_watchlist_sectors` 빈 dict → **섹터기반 고위험 알림 비활성**(무게이트 full-coverage 발화 금지). 크래시 없음. 티커 직접일치 경로는 여전히 동작. (기존 `relevance.py:104-106` 의 "impact>=임계 모든 클러스터 무조건 relevant" 우회 경로를 고위험 알림에 대해 명시적으로 차단 — REQ-060-4.)
- **코로보레이션 미확증(승리 섹터 vs 제목 키워드 불일치)**: 섹터 경로 발화 억제, 티커 직접일치만 허용.
- **빈 회사명 가드 (D9 보강)**: `ticker_metadata.name=''` 인 티커(라이브 실측 54행 중 44행)는 직접일치 후보에서 **제외** — 빈 문자열은 모든 텍스트의 부분문자열이므로 미가드 시 전수 발화. `_ticker_direct_match(cluster, live_tickers={'005930': ''})` → **False**(크래시 없음); 공백뿐 name(`{'005930': '  '}`)도 동일하게 **False**. 채워진 티커(예 316140 `우리금융지주`)만 매칭 대상.
- **캐치올 클러스터 섹터(`stock_market`·`macro_economy`)**: **섹터기반 알림 자격에서 명시적 제외**(D10) → 코로보레이션 후보 불가 → 섹터 경로 발화 불가. 주의: `stock_market` 은 `_SECTOR_KEYWORDS` 에 키워드 세트가 **존재**하므로(`코스피`·`코스닥`·`국민연금`·`증시`…, SPEC-026 c3 r2) "키워드가 없어 미확증"이 아니라 **명시적 캐치올 가드**로 배제한다. 정밀-우선 매핑이 어떤 업종명도 캐치올로 매핑하지 않으므로 보유 섹터에 캐치올이 존재할 수 없다(따라서 섹터 일치 자체가 불성립).
- **빈 클러스터/단일 멤버**: `_majority_sector`가 단일 섹터 반환, quorum = 100%(단, 코로보레이션은 여전히 요구).
- **article_ids 없는 클러스터**: quorum 계산 불가 → 섹터 경로 발화 억제(안전측), 티커 직접일치만 허용.
- **비거래일/뉴스 없음**: 파이프라인 무해 통과, 알림 0.

## Definition of Done

- [ ] REQ-060-1~5 전부 EARS 대응 구현.
- [ ] 시나리오 1(오경보 재현 → 발화 0, **실측 구성** 클러스터 A: stock_market x22 최다 / 클러스터 B: energy x9(82%)·defense x2) repro-first 테스트 RED → GREEN. **클러스터 B 는 코로보레이션 게이트 없이는 발화함을 테스트로 증명**(게이트 on/off 대조).
- [ ] 시나리오 2~6 + 엣지 케이스 테스트 통과.
- [ ] YAML `news_sector_map` 이 실 `ticker_metadata` 의 13개 distinct 업종명(가운뎃점·단형)을 기준으로 seed 되고, 정밀-우선(**명확 6개 매핑 / 모호 7개 `None`**, `전기·전자` 는 D13 근거로 `None`)임을 테스트로 확인. 실보유 015760(`전기·가스`)·316140(`금융`) 100% 해소.
- [ ] 티커 직접일치(D9): `ticker_metadata.name` 정확 부분문자열 매칭 — 양성(제목 "우리금융지주…" + 보유 316140), 음성(별칭 "우리은행"만 → 미일치), 분석키워드 경로 테스트(시나리오 2b).
- [ ] 코로보레이션 게이트: 승리 섹터 vs 멤버 제목 키워드 최고득점 불일치 → 섹터 경로 억제 테스트. **캐치올(`stock_market`·`macro_economy`) 승리 섹터 → 명시적 자격 제외(D10)** + **동점 규칙(S 가 max 공동 달성 시 확증, S < max 억제 — D12)** 테스트.
- [ ] full_coverage 비활성(D4): 매핑 섹터 0개 → 섹터기반 고위험 알림 미발화, 티커 직접일치만 동작 테스트.
- [ ] `TICKER_SECTOR_MAP`·`get_sector_for_ticker` 소스에서 완전 제거(grep 0건). `relevance.py` 예외 폴백이 `TICKER_SECTOR_MAP.keys()` 반환하던 경로 제거.
- [ ] `clustering.py`의 `group_articles[0]["sector"]` 제거, 다수결로 대체.
- [ ] SPEC-026 테스트 통과 또는 의식적 갱신(변경 사유 기록).
- [ ] 텔레그램 메시지 포맷 불변(문자열 diff 0).
- [ ] 오프라인 pytest 회귀 0, 통합 테스트(실 Postgres) 통과, ruff clean.
- [ ] 마이그레이션 불필요 확인(story_clusters 스키마 불변) 또는 가산 마이그레이션만.
- [ ] 라이브검증: 컨테이너에서 `resolve_ticker_sector('316140')`→'finance_banking'·`resolve_ticker_sector('015760')`→'energy_commodities', 07-03 오경보 재실행 시 미발화(클러스터 B 는 코로보레이션 미확증으로), 정상 연관 뉴스 정상 발화.

## Quality Gate 기준

- 오경보 재현 테스트(시나리오 1)는 **HARD 게이트** — 통과 없이 병합 불가.
- 실 SQL 경로(`resolve_ticker_sector` DB lookup, quorum join)는 통합 테스트로 거짓그린 차단([[reference_integration_tests]]).
- 라이브 `docker exec` 검증 없이 완료 선언 금지([[feedback_no_lies]]).
