# SPEC-TRADING-060 구현 계획 — 뉴스 섹터 분류·연관성 판정 개편

기준 origin/main: d50fe04 · 방법론: TDD (repro-first) · 실행: 컨테이너 전용

## 핵심 설계 판단

1. **REQ-060-2의 "CLI가 섹터 emit" 경로는 이미 존재·배선됨** (재조사로 확인).
   - `prompts.py:44-52`가 `sector` 필드(canonical set + 라우팅)를 이미 요청.
   - `analyzer.py:_corrected_sector`(81) + `_store_results:545`(배치) + `import_host_results:890`(CLI)가 `news_articles.sector`를 이미 교정.
   - `test_sector_from_analysis.py`가 이미 존재.
   - → M2는 **신규 구축이 아니라 경화·폴백체인 명시화**. 프롬프트 토큰 증가·중복 구축 회피.
   - **[중요·D2]** M2 폴백 체인은 **누락/무효** 섹터만 잡는다. 07-03 지배적 실패 모드는 "유효(canonical) 하지만 의미상 틀림"(예 "피난처 된 금융주" → `energy_commodities`)이며, 이는 M2 로 교정되지 않는다. **알림 영향 억제는 M3 의 코로보레이션 게이트가 책임**(개별 기사 섹터를 뒤집지 않고 클러스터 수준 독립 확증으로 발화만 억제).
2. **진짜 클러스터-레벨 결함 = `clustering.py:193` `group_articles[0]["sector"]`** (임의 순서 첫 기사). 이것이 M3 다수결의 핵심.
3. **quorum·코로보레이션은 스키마 변경 없이 계산 가능** — 알림 시점 `article_ids` → `news_articles.sector`/`title` join. quorum = 최빈 섹터 동의 비율; 코로보레이션 = 멤버 제목을 `sector_classifier._SECTOR_KEYWORDS` 로 채점한 argmax. `story_clusters` 마이그레이션 불필요(선호).
4. **하드코딩 철폐 = 단일 해소 함수 + 정밀-우선 YAML 매핑** — `TICKER_SECTOR_MAP`(fan_in 3)을 `ticker_metadata` + YAML 매핑 기반 함수로 대체. 매핑은 정밀 우선(모호 업종명 = `None`).
5. **[D1] 다수결+quorum+섹터일치만으로는 부족**하다. 클러스터 B(energy 82% quorum, 015760 한국전력이 energy 보유)는 이 3조건을 모두 통과해 **재발화**한다. 코로보레이션(4번째 조건)이 이를 닫는 유일한 장치 — 게이트 on/off 대조 테스트로 증명.
6. **[D4] full_coverage 폴백 = 고위험 알림에 대해 비활성**. 매핑 섹터 0개 → 무게이트 전수 발화 금지. 티커 직접일치만 허용.
7. **[D9] 티커 직접일치 = 회사명 부분문자열**(추출기 신규 구축 아님). 뉴스 스키마에 tickers 컬럼·엔티티 추출이 없으므로, 실보유+워치리스트의 `ticker_metadata.name` 을 멤버 제목/분석키워드에 정확 부분문자열로 대조. 자회사/별칭 미인식(정밀 우선 — 별칭 표는 향후 확장, Exclusions). 이 경로가 full_coverage 비활성(6번) 하에서 유일 발화 경로이자 R4/R8 과억제 안전망.

## 업종명 → news 섹터 정밀-우선 매핑 (M1 seed, 실 `ticker_metadata` 13개 distinct 기준)

라이브 실측(54행·13개 distinct sector). **명확한 것만 매핑, 모호한 것은 `None`(캐치올 절대 금지)**:

| 업종명(실 리터럴) | news 섹터 키 | 판정 |
|---|---|---|
| `금융` | `finance_banking` | 명확 |
| `제약` | `biotech_pharma` | 명확 |
| `금속` | `steel_materials` | 명확 |
| `IT 서비스` | `it_ai` | 명확 |
| `유통` | `retail_consumer` | 명확 |
| `전기·가스` | `energy_commodities` | 명확(한국전력류 유틸리티) |
| `전기·전자` | `None` | **모호(D13)** — 반도체+이차전지+가전+방산+중전기 혼재, `화학`과 동급 |
| `운송장비·부품` | `None` | 모호(자동차/조선/항공 혼재) |
| `기계·장비` | `None` | 모호 |
| `운송·창고` | `None` | 모호(물류/해운) |
| `화학` | `None` | 모호(석화/배터리/제약 혼재) |
| `통신` | `None` | 모호(telecom ≠ it_ai 명확 아님) |
| `음식료·담배` | `None` | 모호(clean canonical 키 없음) |

→ **명확 6개 매핑 / 모호 7개 미매핑(`None`)**.

- 실보유 검증: 015760(`전기·가스`)→`energy_commodities`, 316140(`금융`)→`finance_banking` 둘 다 해소됨. 전기·전자 강등은 두 실보유에 영향 없음(둘 다 전기·전자 아님).
- 미매핑 업종명은 `None` → "섹터 없음"(no match). 신규 업종명 등장 시 로그 경고(hammer 아님), 캐치올 금지.
- **[HARD]** 옛 리터럴('전기가스업'·'금융업'·'전기전자')로 seed 금지 — 실 리터럴은 가운뎃점·단형(commit 7236acd pykrx 컬럼 거짓그린 교훈).

### D13 근거 — `전기·전자` 라이브 실측 (`docker exec trading-postgres psql`, read-only)

`SELECT ticker, name, industry FROM ticker_metadata WHERE sector='전기·전자' ORDER BY ticker;` 결과(13행):

```
ticker | name | industry
000150 |      | 전기·전자
000660 |      | 전기·전자   (SK하이닉스 — 반도체)
003670 |      | 전기·전자
005930 |      | 전기·전자   (삼성전자 — 반도체)
006400 |      | 전기·전자   (삼성SDI — 이차전지)
009150 |      | 전기·전자
010120 |      | 전기·전자
011070 |      | 전기·전자
066570 |      | 전기·전자   (LG전자 — 가전)
267260 |      | 전기·전자   (HD현대일렉트릭 — 중전기)
272210 |      | 전기·전자   (한화시스템 — 방산)
298040 |      | 전기·전자   (효성중공업 — 중전기)
373220 |      | 전기·전자   (LG에너지솔루션 — 이차전지)
```

**핵심 사실**: 13행 모두 `name` 이 빈 문자열이고 `industry` 가 sector 와 **동일한 `전기·전자`**(세분 값 없음). 대조로 015760 = name `한국전력`/industry `전기·가스`, 316140 = name `우리금융지주`/industry `기타금융` 은 채워져 있으나 전기·전자 로스터는 채워지지 않음.

**판정**: 감사가 제안한 옵션 (a) "industry 레벨 세분(`반도체`→semiconductor·`이차전지`→auto_ev_battery)"은 **불가능**하다 — industry 컬럼이 `전기·전자` 리터럴 그대로라 `반도체`·`전지` 부분문자열이 존재하지 않음. 따라서 데이터가 옵션 **(b) `None` 강등**을 강제한다. 정직한 사유: 티커 정체성으로는 반도체(삼성전자·SK하이닉스)+이차전지(LG엔솔·삼성SDI)+가전(LG전자)+방산(한화시스템)+중전기(HD현대일렉트릭·효성중공업)가 혼재해 `화학`(→None)과 동급의 이질성이며, DB 가 이를 가를 세분 신호를 전혀 제공하지 않는다. `전기·전자` 를 semiconductor 로 매핑하면 미래 전기·전자 보유가 반도체 클러스터 오경보를 받게 되어 정밀-우선 원칙에 정면 위배된다. 현 실보유(015760/316140)에는 영향 없음.

## 마일스톤

### M1 — taxonomy 확장 + 티커→섹터 단일 진실원천 (REQ-060-1)

1. **[MODIFY] `data/sector_taxonomy.yaml`**: 각 시장 블록에 `news_sector_map` 추가 — **실 `ticker_metadata` 업종명(가운뎃점·단형)** → 영문 news 섹터 키. 위 "정밀-우선 매핑" 표를 그대로 seed(**명확 6개 매핑, 모호 7개는 항목 미기재 = `None`**; `전기·전자` 포함 D13). US 블록은 주석 예시로 준비(비활성).
2. **[MODIFY] `data/sector_taxonomy.py`**: `news_sector(raw_industry, market=None) -> str | None` 추가 — 업종명을 news 섹터 키로 변환, 미매핑 시 `None`(가짜 캐치올 금지). 기존 `active_market()`·`_load_taxonomy()`·lru_cache 패턴 재사용.
3. **[NEW] `news/ticker_sector.py`** (또는 `context_builder` 내 신규 함수): `resolve_ticker_sector(ticker, market=None) -> str | None` — (a) `ticker_metadata`에서 업종명 lookup → (b) `sector_taxonomy.news_sector(업종명)`. 미존재/미매핑 → `None`. 이것이 신규 단일 진실원천(**@MX:ANCHOR** 대상, fan_in 예상 >= 3).
4. **[MODIFY] `news/context_builder.py`**: `TICKER_SECTOR_MAP`(43-75, **21종목**)·`get_sector_for_ticker`(78) 제거 → `resolve_ticker_sector` 위임. `build_micro_news`의 `get_sector_for_ticker` 호출부(210)를 미매핑 티커 skip 로직으로 교정(가짜 stock_market 금지).
5. **[MODIFY] `news/intelligence/relevance.py`**: `get_watchlist_sectors`(25) — `TICKER_SECTOR_MAP` import(14) 제거, `resolve_ticker_sector` 사용. 미매핑 티커는 섹터 맵에서 제외(no fake match). `_load_watchlist_tickers`의 예외 폴백(72)이 `TICKER_SECTOR_MAP.keys()` 반환하던 것 → **빈 리스트**. **[D4 결정]** sector map 이 비면(매핑 섹터 0개) → `full_coverage_mode` 의 무게이트 고위험 발화(104-106) 를 **비활성**: 섹터기반 critical 알림 미발화, 티커 직접일치만 허용. (`[투자 주목]` 비-critical 태깅의 full-coverage 동작은 보존 가능하나, impact-5 텔레그램 발화는 반드시 게이트를 거친다.)
6. **[MODIFY] `news/intelligence/reporter.py`**: `TICKER_SECTOR_MAP` import(23)·사용(315) → `resolve_ticker_sector`. 미매핑 skip.

### M2 — 기사 섹터 CLI 경로 경화 + 폴백 체인 명시 (REQ-060-2)

1. **[VERIFY] 기존 경로 실측**: 컨테이너에서 `import_host_results` 실행 흔적·`news_articles.sector` 교정 여부 확인(라이브 DB). 프롬프트/`_corrected_sector` 회귀 없음 확인. **주의(D2)**: 실측 결과 지배적 오태깅은 "유효 canonical 하지만 의미상 틀림"(누락/무효 아님)이므로 M2 폴백 체인의 사정거리 밖임을 문서화 — 이 사실이 M3 코로보레이션 게이트를 정당화한다. M2 는 기사 섹터를 완벽히 고치려 하지 않는다.
2. **[MODIFY] `news/intelligence/analyzer.py`**: `_corrected_sector`의 폴백 체인을 **명시적 문서화 + 검증 강화** — CLI `sector` 유효 → 적용 / 무효·누락 → 키워드 분류기(`classify_sector`) 결과로 강등 / 그마저 fallback == 피드 → 유지. importer 스키마 검증(누락 필드에 크래시 금지)은 이미 `_validate_results`(400)에 있으므로 `sector` 무효 케이스만 보강.
3. **[MODIFY] `news/normalizer.py`**: 변경 없음 예상(ingest 시 `classify_sector` 폴백은 이미 last-resort). 필요 시 주석만 폴백 체인 순서 명시.
   - 주: M2는 대부분 **검증·명시화**. 신규 프롬프트 필드 추가 없음(토큰 증가 회피).

### M3 — 클러스터 다수결 + 알림 3중 게이팅(quorum + 코로보레이션) (REQ-060-3·4)

1. **[MODIFY] `news/intelligence/clustering.py:193`**: `sector_val = group_articles[0]["sector"]` → `_majority_sector(group_articles)`. 신규 순수함수 `_majority_sector(articles) -> str`: impact_score 가중 최빈 → 동수 시 기사 수 → 최종 동점 시 최고 impact 기사 섹터(결정론적 tie-break).
2. **[NEW] `news/intelligence/relevance.py` `_cluster_sector_quorum(cluster) -> float`**: `article_ids` → `news_articles.sector` join → 클러스터 섹터에 동의하는 멤버 비율(0.0~1.0). 스키마 불변(계산만).
3. **[NEW·D2] `news/intelligence/relevance.py` `_sector_corroborated(cluster, sector) -> bool`**: `article_ids` → `news_articles.title` join → 멤버 제목을 `sector_classifier._SECTOR_KEYWORDS` **재사용**해 섹터별 채점 → **`score(sector) >= 1 AND score(sector) == max(전 섹터 득점)`** 이면 `True`(승리 섹터가 최고 득점을 단독/공동 달성; sector 를 포함하는 동점은 확증으로 계수 — 결정론적 동점 규칙 D12). **[D10] 캐치올 명시 제외**: `sector in {'stock_market','macro_economy'}` 이면 키워드 채점과 무관하게 즉시 `False`(코로보레이션 후보 불가). 주의 — `stock_market` 은 `_SECTOR_KEYWORDS` 에 키워드 세트가 **존재**하므로(`코스피`·`코스닥`·`국민연금`·`증시`…, SPEC-026 c3 r2) "키워드가 없어 자연히 미확증"이라는 서술은 거짓이며, 반드시 명시적 캐치올 가드로 배제한다. `macro_economy` 는 `_SECTOR_KEYWORDS` 에 키 자체가 없다. **신규 키워드 세트 금지**(sector_classifier 세트만 재사용). 순수 계산·비용 0.
4. **[NEW·D9] `news/intelligence/relevance.py` `_ticker_direct_match(cluster, live_tickers) -> bool`**: 뉴스 모듈에는 기존 티커 추출기가 **없다**(스키마 확인: `news_articles`·`news_analysis`·`story_clusters` 에 tickers 컬럼 없음). 따라서 추출 대신 **회사명 부분문자열 매칭**으로 정의 — 각 `ticker in live_tickers`(실보유 ∪ 워치리스트)의 `ticker_metadata.name`(공식 회사명, 예 `우리금융지주`·`한국전력`)을 클러스터 멤버의 `article_ids` → `news_articles.title` **또는** `news_analysis.keywords`(배열 원소) 에 대해 정확 부분문자열로 검사. 하나라도 히트하면 `True`. **빈 name 가드**: `ticker_metadata.name` 이 빈 문자열/공백뿐인 티커는 후보에서 제외(빈 문자열은 모든 텍스트의 부분문자열 — 미가드 시 name 미채움 44/54 티커가 전수 발화 유발). **퍼지·자회사·브랜드 별칭 금지**(정밀 우선): `name` 문자열 자체가 부분문자열일 때만. 예 클러스터 A 제목 "우리은행 삼성월렛머니…"에는 회사명 `우리금융지주`가 부분문자열로 없음 → 316140 미일치(발화 0 결정론 보존). 순수 계산·비용 0(DB 읽기만). **@MX:NOTE**.
5. **[MODIFY] `news/intelligence/relevance.py` `tag_portfolio_relevance`(75)**: 섹터 매칭 분기(108-112)를 3중 게이트로 교체 — (a) `_ticker_direct_match`(클러스터 회사명 부분문자열 ∩ live holdings+watchlist, 섹터 무관) OR (b) 섹터일치 AND `_cluster_sector_quorum >= 0.5` AND `_sector_corroborated`(승리 섹터 캐치올이면 자격 제외 — D10). **D4**: `full_coverage_mode`(92) 진입 시 섹터기반 critical 발화 비활성(티커 직접일치만). 발화 게이트(`_send_critical_alert` 호출부 128)에 삽입. **@MX:WARN**(자본·운영자 신호 게이트, 오경보 억제 로직).
6. **[MODIFY·N1 HARD] holdings+watchlist 알림시점 조회 + 회사명**: `_load_watchlist_tickers`(47)는 "그대로 재사용" 불가 — **라이브 실측: 쿼리가 `positions WHERE quantity > 0` 인데 실컬럼은 `qty`** 라서 이 쿼리는 항상 예외로 떨어져 하드코딩 `TICKER_SECTOR_MAP.keys()` 폴백을 반환해 왔다(감사 review-3 N1 발견 — 기존 프로덕션 결함). 반드시: (a) 쿼리를 `qty` 컬럼으로 **수정**하고, (b) 이 로더를 M4.3 통합테스트(실 Postgres, trading_test) 범위에 **포함**하며, (c) M4.4(c) **정상발화(양성) 검증을 필수**로 한다 — 게이트 강화가 "알림 전면 침묵"으로 오구현되면 미발화 HARD 게이트(M4.4b)가 틀린 이유로 통과하기 때문. `_ticker_direct_match` 용으로 각 티커의 `ticker_metadata.name` 을 함께 조회(부분문자열 매칭 대상). 실보유는 알림 시점 라이브 조회(REQ-060-4 [Ubiquitous]).

> 게이트 순서(결정론): 다수결(M3.1) → 티커직접(`ticker_metadata.name` 부분문자열 히트 시 즉시 발화) → [섹터일치 → quorum >= 0.5 → 코로보레이션(캐치올 제외·동점 규칙 D12)] 모두 통과 시에만 섹터 발화. 클러스터 B 는 quorum/일치 통과 후 **코로보레이션에서 탈락**(제목 키워드 채점 energy=0, argmax = semiconductor(4)/steel(4) ≠ energy — energy 가 max 미만이자 0 이므로 semi/steel 동점과 무관하게 미확증).

### M4 — 테스트·검증·배포 (REQ-060-5)

1. **[NEW] repro-first 회귀 테스트** (`tests/news/intelligence/`):
   - `test_false_alert_repro_2026_07_03.py`: 07-03 두 오경보 클러스터를 **실측 구성**으로 재구성 — 클러스터 A(`stock_market` x22 최다·`finance_banking` x1) / 클러스터 B(`energy_commodities` x9(82%)·`defense_aerospace` x2, 제목에 SK하이닉스·철강 BSI·포스코·반도체 피크아웃 포함) → 신규 3중 규칙에서 **발화 0** 단언(RED → GREEN). **클러스터 B 는 코로보레이션 게이트 on/off 대조**: off 시 발화(감사 D1 증명), on 시 억제.
   - `test_cluster_majority_sector.py`: 다수결·동수·tie-break·단일기사 케이스.
   - `test_ticker_sector_resolution.py`: 015760(`전기·가스`)→energy_commodities / 316140(`금융`)→finance_banking / 모호 업종명(`화학`·`통신`)→None / 미매핑 티커→None / 빈 ticker_metadata.
   - `test_alert_gate.py`: quorum >= 0.5·코로보레이션 확증 → 발화 / quorum < 0.5 억제 / 코로보레이션 미확증 억제 / **캐치올 승리 섹터 → 자격 제외(D10)** / 코로보레이션 동점 규칙(S 가 max 공동 달성 시 확증, S < max 억제 — D12) / **full_coverage(매핑 0개) → 섹터 발화 비활성·티커직접만**.
   - `test_ticker_direct_match.py` (D9): 회사명 정확 부분문자열 — 양성(제목 "우리금융지주 어닝 서프라이즈" + 보유 316140 → True) / 음성(제목 "우리은행 삼성월렛머니…"만, 보유 316140 → False, 별칭 미인식) / 분석키워드 경로(keywords 배열에 "한국전력" 원소 + 보유 015760 → True) / 미보유 회사명 → False / **빈 name(`''`) 티커 → False (가드, 전수매치 금지)**.
2. **[VERIFY] SPEC-026 테스트 유지**: `test_sector_classifier.py`·`test_sector_from_analysis.py`·`test_relevance.py` 통과 또는 의식적 갱신(하드코딩 제거로 `test_relevance.py`의 TICKER_SECTOR_MAP 의존 케이스는 갱신 필요).
3. **[VERIFY] 통합 테스트**: `tests/integration/`(실 Postgres, [[reference_integration_tests]]) — `resolve_ticker_sector`·quorum 계산의 실 SQL 거짓그린 방지(mock이 못 잡는 컬럼 불일치).
4. **배포**: 커밋·푸시 → make redeploy(app/bot/scheduler) → 마이그레이션 **불필요**(story_clusters 스키마 불변, sector_taxonomy.yaml·코드만) → 라이브검증: (a) 컨테이너에서 `resolve_ticker_sector('316140')` → 'finance_banking'·`resolve_ticker_sector('015760')` → 'energy_commodities' 확인, (b) `tag_portfolio_relevance` 재실행 시 07-03 오경보 재발 없음(클러스터 B 코로보레이션 미확증), (c) 정상 연관 뉴스(316140 언급 or `금융` 섹터 quorum+코로보레이션)는 정상 발화.

## 리스크 분석

| 리스크 | 영향 | 완화 |
|---|---|---|
| R1 — CLI 출력 스키마 드리프트 | LLM이 `sector` 누락/오타 → importer 오작동 | `_validate_results`+`_corrected_sector`가 이미 무효 시 무시(폴백). M2에서 무효 케이스 테스트 추가. 크래시 금지 유지. |
| R2 — 업종명↔news 섹터 매핑 갭·리터럴 불일치 | **옛 리터럴('금융업'·'전기가스업'·'전기전자')로 seed 시 전 보유 미해소 → full coverage → 오늘보다 악화** (감사 D3, pykrx 컬럼 거짓그린 교훈 commit 7236acd) | YAML 을 **실 `ticker_metadata` 13개 distinct 업종명(가운뎃점·단형)** 으로 seed(위 매핑 표). `news_sector` 미매핑 시 `None`(no fake match). 통합 테스트로 015760/316140 실해소 확인. 미커버 업종명은 로그 경고(hammer 아님). |
| R3 — 다수결이 정당한 소수 섹터 억제 | 진짜 고임팩트 소수 기사가 다수 저임팩트에 묻힘 | 가중을 **impact 우선**으로 설계(고임팩트 소수가 이길 수 있음). tie-break도 최고 impact 기사. |
| R4 — quorum·코로보레이션 임계가 정상 알림 억제 | 혼합 섹터 클러스터에서 정당한 알림 누락 | 티커 **직접일치**(D9 회사명 부분문자열, quorum·코로보레이션 무관)가 OR 안전망. 섹터-only 만 3중 요구. 임계는 상수화(추후 튜닝 가능). 단 별칭 미인식이므로 회사명 표기 변형이 잦은 종목은 섹터 경로에 의존(수용 — 정밀 우선). |
| R8 — 코로보레이션이 정당한 알림 억제(과억제) | 제목에 키워드가 빈약한 정당 섹터 뉴스가 미확증으로 눌림 | 티커 직접일치 경로가 안전망(섹터 판정 무관, D9 회사명 부분문자열). 코로보레이션은 **섹터-only** 경로에만 적용. 승리 섹터가 최고 득점(공동 포함)이면 되므로(득점 임계 낮음, >= 1) 정상 뉴스는 통과. 과억제 여부는 라이브 알림 로그로 관측. |
| R5 — 하드코딩 제거로 `test_relevance.py` 회귀 | TICKER_SECTOR_MAP 의존 테스트 깨짐 | 의식적 갱신(REQ-060-5 허용). 신규 `resolve_ticker_sector` 기반으로 재작성. |
| R6 — 실 SQL 거짓그린 | mock DB가 quorum join·ticker_metadata 컬럼 불일치를 못 잡음 | M4 통합 테스트(실 Postgres) 필수. 라이브 `docker exec` 검증 게이트. 메모리 2연발 교훈([[project_spec_042_status]]) 반영. |
| R7 — analyze_news 프롬프트 토큰 증가 | M2가 프롬프트 필드 추가 시 토큰·비용 압박 | **M2는 신규 필드 미추가**(sector 이미 요청됨). 검증·명시화만. 토큰 증가 0. |

## 영향 파일 (delta 마커)

- [MODIFY] `src/trading/data/sector_taxonomy.yaml` — `news_sector_map` 블록 추가
- [MODIFY] `src/trading/data/sector_taxonomy.py` — `news_sector()` 추가
- [NEW] `src/trading/news/ticker_sector.py` — `resolve_ticker_sector()` (@MX:ANCHOR)
- [MODIFY] `src/trading/news/context_builder.py` — `TICKER_SECTOR_MAP`/`get_sector_for_ticker` 제거, 위임
- [MODIFY] `src/trading/news/intelligence/relevance.py` — `get_watchlist_sectors`·`_cluster_sector_quorum`·`_sector_corroborated`(캐치올 배제·동점 규칙)·`_ticker_direct_match`(회사명 부분문자열, D9)·full_coverage 비활성 (@MX:WARN)
- [MODIFY] `src/trading/news/intelligence/reporter.py` — `TICKER_SECTOR_MAP` 사용 제거
- [MODIFY] `src/trading/news/intelligence/clustering.py` — `group_articles[0]` → `_majority_sector`
- [MODIFY] `src/trading/news/intelligence/analyzer.py` — 폴백 체인 명시·무효 sector 검증 보강
- [NEW] `tests/news/intelligence/test_false_alert_repro_2026_07_03.py`
- [NEW] `tests/news/intelligence/test_cluster_majority_sector.py`
- [NEW] `tests/news/test_ticker_sector_resolution.py`
- [NEW] `tests/news/intelligence/test_alert_gate.py` (quorum + 코로보레이션 + 캐치올 제외 + 동점 규칙 + full_coverage 비활성)
- [NEW] `tests/news/intelligence/test_ticker_direct_match.py` (회사명 부분문자열 양성/음성/별칭 미인식/분석키워드 경로, D9)
- [MODIFY] `tests/news/intelligence/test_relevance.py` — 하드코딩 제거 반영 갱신
- 마이그레이션: **불필요**(story_clusters 스키마 불변). 필요 판명 시 가산만.

## Reference (재사용 패턴)

- **시장별 YAML 로더 패턴**: `src/trading/data/sector_taxonomy.py`(`active_market()`·`_load_taxonomy()`·lru_cache·미매핑 graceful `None`) — `news_sector()`를 동일 패턴으로 확장.
- **ticker_metadata 조회**: `src/trading/dashboard/sector_loader.py:_lookup_from_frame`·`_tickers_from_db`(DB 조회·per-ticker try/except) — `resolve_ticker_sector` DB lookup 참조.
- **CLI 비용 0 가드**: SPEC-053 패턴(`analyzer.py:235` PAID_CALL 계측, `is_cli_only_mode` 술어) — importer 가 유료 폴백 유발 안 함을 보장.
- **다수결/Counter**: `clustering.py:_compute_dominant_sentiment`(107, Counter.most_common) — `_majority_sector` 동일 관용구 + impact 가중.
- **코로보레이션 키워드 채점**: `sector_classifier._SECTOR_KEYWORDS` + `_score`(73) — `_sector_corroborated` 가 멤버 제목에 동일 채점 로직 재사용(신규 키워드 세트 구축 금지). **캐치올(`stock_market`·`macro_economy`)은 명시적 가드로 배제**(D10) — `stock_market` 은 키워드 세트가 존재하므로 "키워드 없음" 논리에 의존하지 말 것. 정밀-우선 매핑이 어떤 업종명도 캐치올로 매핑하지 않으므로 캐치올 승리 섹터는 보유 섹터와 결코 일치하지 않는다(억제-안전).
- **알림 dedup·throttle**: `relevance.py:_alert_keys`/`_any_alerted_recently`(187-215) — quorum 게이트는 이 발화 경로 **앞단**에 삽입(dedup 로직 불변).
- **통합 테스트**: [[reference_integration_tests]] `pytest tests/integration/ -m integration`(trading_test DB) — 실 SQL 거짓그린 차단.

## @MX 태그 계획

- **`resolve_ticker_sector`** (`news/ticker_sector.py`, 신규): **@MX:ANCHOR** — fan_in 예상 >= 3(context_builder·relevance·reporter가 대체 소비). `@MX:REASON` = 티커→섹터 단일 진실원천, 미매핑 시 반드시 `None`(가짜 캐치올 금지)이 오경보 억제의 불변식.
- **`tag_portfolio_relevance` 발화 게이트** (`relevance.py`, 수정): **@MX:WARN** — `@MX:REASON` = 자본/운영자 신호 게이트. quorum·코로보레이션·티커직접·full_coverage 비활성 조건이 잘못되면 오경보(2026-07-03류) 재발 또는 정당 알림 누락. **코로보레이션은 클러스터 B 재발화를 막는 유일 장치(D1)** — 분기 복잡도 상승 주의.
- **`_ticker_direct_match`** (`relevance.py`, 신규·D9): **@MX:NOTE** — 실보유+워치리스트 `ticker_metadata.name` 을 멤버 제목/분석키워드에 정확 부분문자열 매칭(뉴스 모듈 티커 추출기 부재 대체). `@MX:REASON` = 퍼지·별칭 금지가 재현 결정론(클러스터 A 발화 0)의 불변식.
- **`_sector_corroborated`** (`relevance.py`, 신규): **@MX:NOTE** — 멤버 제목 키워드 채점으로 승리 섹터 독립 확증(score(S) >= 1 AND == max, 캐치올 명시 배제 D10, 동점 규칙 D12). `sector_classifier._SECTOR_KEYWORDS` 재사용(신규 세트 금지).
- **`_majority_sector`** (`clustering.py`, 신규): **@MX:NOTE** — impact 가중 최빈 + 결정론적 tie-break 규칙 문서화(첫 기사 상속 결함 교정 의도).
- **`news_sector`** (`sector_taxonomy.py`, 신규): **@MX:NOTE** — 업종명→news 섹터 매핑, 미매핑 `None` 반환 규약.
- 언어: `code_comments: ko`(language.yaml) → @MX 설명 한국어.
