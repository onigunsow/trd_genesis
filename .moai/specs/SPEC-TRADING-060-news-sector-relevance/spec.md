---
id: SPEC-TRADING-060
version: 0.3.1
status: draft
created: 2026-07-03
updated: 2026-07-03
author: oni
priority: high
issue_number: 0
labels: [news, sector, relevance, alerts, hardcoding, cost-zero]
---

# SPEC-TRADING-060 — 뉴스 섹터 분류·포트폴리오 연관성 판정 개편 (오경보 제거 및 하드코딩 철폐)

작성: 2026-07-03 · 기준 origin/main: d50fe04 · 범위: 뉴스 인텔리전스(섹터 분류 품질 + 알림 게이팅), 시장중립·비용 0

## HISTORY

- 2026-07-03 v0.3.1 (plan-audit review-3 N1~N4 반영): **N1(major)** `_load_watchlist_tickers` "재사용(이미 DB 조회)" 전제는 거짓 — 라이브 쿼리가 `positions.quantity`(실컬럼 `qty`)라 항상 예외 폴백(하드코딩)으로 동작해 왔음(기존 프로덕션 결함 발견). plan M3.6 에 qty 쿼리 수정·통합테스트 포함·정상발화(양성) 검증 필수를 HARD 로 명시(오구현 시 알림 전면 침묵이 미발화 게이트를 틀린 이유로 통과). **N2** 공백뿐 name 가드 테스트 케이스 추가. **N3** spec-compact 영향 파일에 test_ticker_direct_match.py 추가. **N4** name backfill Exclusions 항목 신설(dangling 참조 해소). (+감사 전 오케스트레이터 추가: 빈 name 직접일치 제외 가드 — 44/54행 name='' 실측.)
- 2026-07-03 v0.3.0 (draft revision per plan-audit review-2 (D9–D14)): 적대적 감사(FAIL 0.78) 신규 6개 결함(D9~D14) 전수 반영. **D9** REQ-060-4(a) "티커 직접일치" 경로를 완전·결정론적으로 정의 — 뉴스 모듈에 티커 추출기가 없으므로 메커니즘 명시: 알림 시점에 실보유+워치리스트 티커의 공식 회사명(`ticker_metadata.name` 예 `우리금융지주`·`한국전력`)이 멤버 기사의 **제목**(`news_articles.title`) 또는 그 기사의 **분석 키워드**(`news_analysis.keywords`)에 **정확 부분문자열**로 등장할 때만 일치. 자회사/브랜드 별칭(예 `우리은행`→우리금융지주)은 매칭 안 함(정밀 우선; 별칭 표는 문서화된 향후 확장 → Exclusions). 알려진 엣지 검증: 클러스터 A 기사 661135 "우리은행 삼성월렛머니…" 는 `우리금융지주` 가 부분문자열이 아님 → 티커 미일치 → 재현 결정론(발화 0) 보존. 티커 경로 수용 시나리오(양성/음성) 추가. **D10** "캐치올은 키워드 세트가 없으므로 확증 불가"는 **거짓 전제**(`sector_classifier.py:43-46` 의 `stock_market` 키워드 세트 존재, SPEC-026 c3 r2) → 전 파일에서 제거. 명시적 정규 규칙으로 대체: 캐치올(`stock_market`·`macro_economy`)은 **섹터기반 알림 자격에서 명시적 제외**(코로보레이션 후보 불가·업종명→캐치올 매핑 불가, 정밀-우선 표와 일관). **D11** 클러스터 B walk-through 에서 `석유화학` 오염 제거(01:15 클러스터 A 소속, 클러스터 B 멤버 제목 아님). **D12** 코로보레이션 동점 처리 결정론화: 확증 = score(S) >= 1 AND score(S) == max(전 섹터 득점). 클러스터 B 실측(semiconductor 4·steel 4·energy 0)은 energy 가 0 이므로 동점 처리와 무관하게 미확증. **D13** `전기·전자`→semiconductor 는 정밀-우선 원칙과 충돌 + 라이브 industry 컬럼이 세분 신호 없음(값=`전기·전자` 그대로) → **`None`(미매핑) 으로 강등**(반도체+이차전지+가전+방산+중전기 혼재, `화학`과 동급). 정밀-우선 매핑 = **명확 6개 / 모호 7개**(전기·전자가 None 으로 이동). 실보유 015760/316140 영향 없음. **D14** spec-compact.md "013개" 오타 → "13개". D1~D8 수정 전부 유지.
- 2026-07-03 v0.2.0 (draft, plan-audit review-1 반영): 적대적 감사(FAIL 0.55) 8개 결함(D1~D8) 전수 반영. **D1** 08:15 오경보 클러스터의 실제 구성(라이브 DB 실측: `energy_commodities` x9(82%)·`defense_aerospace` x2·`finance_banking` x0)을 근거·수용 시나리오에 반영 — 기존 fixture는 허수아비였고 다수결+quorum+섹터일치 규칙만으로는 **오경보가 재발화**함을 확인. **D2** 지배적 실패 모드 = "canonical 이지만 의미상 틀린" 섹터("피난처 된 금융주" → energy_commodities) → 누락/무효 폴백 체인으로는 못 잡음 → **키워드 코로보레이션(corroboration) 게이트**(REQ-060-4에 추가, 멤버 제목을 `sector_classifier` 키워드로 채점하여 승리 섹터의 독립 증거 요구) 도입. **D3** 실 `ticker_metadata` 업종명 리터럴(가운뎃점·단형: `금융`·`전기·가스`·`전기·전자`·`금속`…)로 전 파일 교정 + 정밀 우선(precision-first) 매핑. **D4** `full_coverage_mode` 상호작용 명시(매핑 섹터 0개 시 섹터기반 고위험 알림 비활성). **D5** `labels` frontmatter 추가. **D6** 클러스터 과잉병합을 수용 잔여 리스크로 명시. **D7** `TICKER_SECTOR_MAP` 21종목 정정. **D8** EARS 정규문에서 file:line 제거(근거/reference로 이동).
- 2026-07-03 v0.1.0 (draft): 최초 작성. 2026-07-03 라이브 오경보 2건(01:15 클러스터 → finance_banking 태깅 / 08:15 클러스터 → defense_aerospace 태깅) 근본원인 진단 후 EARS 5모듈로 정형화. **재조사 결과 REQ-060-2의 "CLI가 섹터를 emit" 경로는 이미 존재·배선됨**(SPEC-026 A2) → 본 SPEC은 신규 구축이 아니라 (a) 클러스터 섹터 다수결 교정 + (b) 알림 게이팅 강화 + (c) TICKER_SECTOR_MAP 하드코딩 철폐가 핵심임을 명시.

## 배경 / 사건 (2026-07-03 라이브, DB 실측 — `news_alerts_sent` ⋈ `news_articles.sector`)

운영자에게 오늘(2026-07-03) "포트폴리오 관련 고위험 뉴스 감지"(Impact 5/5) 텔레그램 오경보 2건이 발송됨. **두 클러스터의 실제 멤버 섹터 구성을 라이브 DB에서 재도출**(read-only)한 결과는 다음과 같다:

| 시각 | 태깅된(발화) Sector | 대표 제목 | 멤버 섹터 실측 분포 |
|---|---|---|---|
| 01:15 (클러스터 A) | `finance_banking` | 한화솔루션 석유화학·환율 등 | `stock_market` x22 · `semiconductor` x9 · `macro_economy` x2 · `finance_banking` x1 · `biotech_pharma` x1 (총 35) |
| 08:15 (클러스터 B) | `defense_aerospace` | 반도체 흔들릴 때 피난처 된 금융주… | `energy_commodities` x9(82%) · `defense_aerospace` x2 · `finance_banking` x0 (총 11) |

핵심 실측 사실:

1. **발화 섹터는 첫 기사 상속의 산물**이다. 클러스터 A는 다수(catch-all `stock_market` 22개)와 무관하게 `finance_banking`(1개, 3%)으로 태깅됐고, 클러스터 B는 다수(`energy_commodities` 9개)와 무관하게 `defense_aerospace`(2개)로 태깅됐다 — 둘 다 `group_articles[0]` 임의 첫 기사가 클러스터 섹터를 결정(RC3).
2. **멤버 섹터 자체가 "canonical 이지만 의미상 틀림"** 이다. 클러스터 B의 대표 기사 "반도체 흔들릴 때 피난처 된 금융주"는 DB에 `energy_commodities`로 저장돼 있다(금융/증시 기사인데 에너지로 오태깅). "은행권 FDI 원스톱 서비스" → `semiconductor`, "임신중절수술" → `stock_market` 등도 동종. 이는 **누락/무효값이 아니라 유효(canonical) 하지만 틀린 값** 이라, 누락/무효만 잡는 폴백 체인으로는 교정되지 않는다(→ REQ-060-4 코로보레이션 게이트의 존재 이유).
3. **클러스터가 주제적으로 과잉병합**됐다. 클러스터 B는 NH농협은행·AWS·포스코·SK하이닉스·철강 BSI·수소 강관·"피난처 된 금융주"를 한 클러스터로 union-find 병합한다 — "쓰레기 클러스터의 다수결도 쓰레기"일 수 있다(→ Exclusions의 수용 잔여 리스크).

DB `story_clusters`(2026-07-01, 07-03) 전반에 섹터 오태깅이 체계적으로 퍼져 있음(예: "KB금융 회장 연임" → `semiconductor`, "신한투자증권 SOL메이트" → `energy_commodities`). **이는 최근 회귀가 아니라 오래된 품질 결함**이다.

두 오경보의 공통 메커니즘: **잘못 태깅된 클러스터 섹터**가 **단순 섹터-문자열 일치**(quorum·코로보레이션·티커 검증 없음)로 운영자 보유 섹터에 매칭되어 Impact-5 임계에서 즉시 텔레그램 발화. **주의: 다수결+quorum+섹터일치만으로는 클러스터 B가 재발화한다** — `energy_commodities`(82% quorum 충족) 가 실보유 015760(한국전력=`전기·가스`→energy_commodities) 섹터와 일치하기 때문. 오직 REQ-060-4의 **키워드 코로보레이션 게이트**만이 이를 억제한다(멤버 제목 키워드 argmax ≠ energy → 미확증 → 섹터기반 발화 불가).

## 근거 (코드 실측, 2026-07-03)

| 근본원인 | 사실 | file:line |
|---|---|---|
| RC1 피드 상속 | 기사 sector 는 RSS 피드 sector 를 상속(`NewsSource.sector`). 범용 economy 피드의 모든 기사가 그 태그를 물려받음. | `news/sources.py:37`, `rss_fetcher.py:275/316/345`, `web_scraper.py:293` |
| RC2 보수적 재분류 | `classify_sector`(SPEC-026 c3)는 title-가중 점수 >= 2 **AND** 피드 섹터 점수를 엄격히 상회할 때만 override. 대부분의 오태깅은 **미교정**. 캐치올(`macro_economy`·`stock_market`)로는 절대 재분류 안 함. 동점 시 피드 섹터 유지. | `news/sector_classifier.py:84-109`, 호출부 `normalizer.py:188` |
| RC3 클러스터 섹터 = 첫 기사 | 클러스터 섹터 = union-find 그룹의 **임의 순서 첫 기사** sector. 다수결 아님 → 오태깅 멤버 1개가 클러스터 전체를 오염. | `news/intelligence/clustering.py:193` (`sector_val = group_articles[0]["sector"]`) |
| RC4 하드코딩 맵 | 포트폴리오 연관성은 하드코딩 `TICKER_SECTOR_MAP`(**21종목**)에 의존. 실보유 015760(한국전력)·316140(우리금융지주) **미포함** → `get_watchlist_sectors` 가 "stock_market" 캐치올로 폴백. 운영자 [HARD] 하드코딩 금지·멀티마켓 지침(2026-07-01, [[feedback_no_hardcoding_multimarket]]) 위반. | `news/context_builder.py:43-75`, 소비 `relevance.py:41`, `reporter.py:315` |
| RC5 알림 게이트 | "포트폴리오 관련 고위험"은 `cluster_sector in sector_tickers` **단일 섹터 문자열 매칭**만으로 발화. quorum·코로보레이션·티커 직접일치 검증 없음. 또한 매핑 실패 시 `full_coverage_mode`(sector map 이 비면 impact>=임계 **모든** 클러스터를 무조건 relevant 태깅)로 진입해 게이트가 구조적으로 우회됨. | `news/intelligence/relevance.py:104-110`, 발화 `_send_critical_alert:228-249` |
| RC6 실 업종명 리터럴 | `ticker_metadata`(54행·13개 distinct) 업종명은 **가운뎃점·단형**: `금융`·`전기·가스`·`전기·전자`·`금속`·`IT 서비스`·`화학`·`운송·창고`·`제약`·`기계·장비`·`통신`·`음식료·담배`·`운송장비·부품`·`유통`. 실보유 015760 = `전기·가스`, 316140 = `금융`(industry `기타금융`). '전기가스업'·'금융업'·'전기전자' 같은 옛 리터럴로 YAML 을 채우면 전 보유가 미해소 → full coverage → **오늘보다 악화**(pykrx 컬럼 거짓그린 교훈, commit 7236acd). | 라이브 `ticker_metadata` 실측 |

### 이미 존재하는 자산 (재사용 대상 — 신규 구축 금지)

| 자산 | 상태 | 위치 |
|---|---|---|
| **CLI 섹터 emit** | **이미 배선됨**. analyze_news 프롬프트가 `sector` 필드(canonical set + 라우팅 규칙)를 요청하고, import 시 `news_articles.sector`를 교정. | `prompts.py:44-52`, `analyzer.py:_corrected_sector:81` + `_store_results:545` + `import_host_results:890` |
| 시장별 섹터 taxonomy 로더 | `sector_taxonomy.yaml` + `active_market()`(env TRADING_MARKET, 기본 KR) 로더. SPEC-054/059 외부화. | `data/sector_taxonomy.py`, `data/sector_taxonomy.yaml` |
| `ticker_metadata` 테이블 | ticker/sector/industry/name(54행·13개 distinct, sector = pykrx 업종명 실 리터럴 예 `금융`·`전기·가스`(가운뎃점·단형)). `sector_loader`(SPEC-054)가 일간 갱신. | `db/migrations/036·037`, `dashboard/sector_loader.py` |
| 비용 0 CLI 경로 | 호스트 cron `scripts/analyze_news.sh` `claude -p` 6회/일 → `data/analysis_results.json` → `import_host_results()`. **유일한 LLM 예산**(strict_cost_zero ON). | `scripts/analyze_news.sh`, `analyzer.py:800` |
| news 섹터 canonical set | `SECTORS`(영문 키 finance_banking·semiconductor…). | `news/sources.py:14` |

## 목표

1. 하드코딩 `TICKER_SECTOR_MAP` 철폐 — 티커→news 섹터를 단일 진실원천(DB `ticker_metadata` 업종명 + 시장별 YAML **정밀-우선** 매핑)으로 해소. 모호한 업종명은 `None`(미매핑) — 캐치올 금지.
2. 클러스터 섹터 품질 개선 — 첫 기사 상속 → **다수결**.
3. 알림 게이팅 강화(3중) — 티커 직접일치 **또는** (quorum 섹터 일치 **AND** 키워드 코로보레이션). 즉 다수결·quorum 만으로는 부족하고, 승리 섹터가 멤버 제목의 독립 키워드 증거로 확증돼야 섹터기반 발화 가능.
4. 2026-07-03 오경보 2건이 신규 3중 게이트에서 **발화 0** 이어야 함(수용 시나리오로 실 데이터 walk-through 검증). 클러스터 A는 다수결+보유섹터 불일치로, 클러스터 B는 **코로보레이션 미확증**으로 각각 억제.
5. 회귀 0·다운타임 0·비용 0.

## EARS 요구사항

### REQ-060-1 — 하드코딩 철폐: 티커→섹터 단일 진실원천 (RC4·RC6)

- **[Ubiquitous]** 시스템은 티커→news 섹터 해소를 하드코딩 리터럴이 아닌 **단일 진실원천**(DB 업종명 + 시장별 외부 YAML 매핑)으로 수행해야 하며(shall), 시장 종속 리터럴을 코드에 두어서는 안 된다.
- **[Event-Driven]** **When** 포트폴리오/워치리스트 티커의 섹터를 조회할 때, 시스템은 티커 메타데이터의 업종명을 lookup 하고 이를 외부 매핑으로 news 섹터 키에 변환해야 한다(shall).
- **[Ubiquitous]** 시스템은 업종명→news 섹터 매핑을 **정밀 우선(precision-first)** 으로 적용해야 한다(shall): 명확한 업종명만 매핑하고, 모호한 업종명은 매핑하지 않아야 한다(unmapped). 매핑 실패는 곧 "섹터 없음"이며, 결코 캐치올 대체를 의미하지 않는다.
- **[Unwanted]** **If** 티커가 메타데이터에 없거나 그 업종명이 news 섹터 매핑에 없으면, **then** 시스템은 **섹터 매칭을 하지 않아야**(no match) 하며 **가짜 캐치올 매칭**(예 "stock_market")을 만들어서는 안 된다(shall not).
- **[Optional]** **Where** 향후 US 시장이 활성일 때, 시스템은 동일 로직으로 US 매핑(설정 항목만 추가)을 사용할 수 있어야 한다(shall). US 데이터소스 어댑터 자체는 범위 밖(Exclusions 참조).
- **[Ubiquitous]** 시스템은 하드코딩 맵의 3개 소비처(뉴스 컨텍스트 빌더·연관성 태거·리포터)를 단일 신규 해소 함수로 대체해야 한다(shall).

> 근거/reference (정규 요구사항 아님): 단일 진실원천 = DB `ticker_metadata`(`sector_loader`가 일간 갱신, SPEC-054) + `sector_taxonomy.yaml` 의 `news_sector_map` 블록 확장. 신규 해소 함수 후보 = `news/ticker_sector.py:resolve_ticker_sector`. 소비처 = `context_builder.py:43`(`TICKER_SECTOR_MAP`)·`relevance.py:41`·`reporter.py:315`. `active_market()`(env TRADING_MARKET) 로 시장 분기. **정밀 우선 매핑 표는 plan.md M1 참조**(실 `ticker_metadata` 13개 distinct 업종명 기준).

### REQ-060-2 — 기사 섹터 품질(비용 0): 기존 CLI 섹터 경로 경화 + 명시적 폴백 체인 (RC1·RC2)

- **[Ubiquitous]** 시스템은 유료 API 없이(strict_cost_zero) 콘텐츠 기반 섹터를 부여해야 한다(shall). 정본 경로 = 기존 analyze_news CLI 가 emit 하는 섹터. 본 REQ 는 신규 구축이 아니라 이 경로의 **경화·검증**이다.
- **[Event-Driven]** **When** CLI 분석 결과에 유효한 섹터(canonical 소속)가 있고 현재 값과 다르면, 시스템은 그 섹터로 기사 섹터를 교정해야 한다(shall).
- **[State-Driven]** **While** CLI 출력에 섹터가 없거나 무효일 때, 시스템은 키워드 분류기를 폴백으로 사용해야 한다(shall).
- **[Unwanted]** **If** 키워드 분류기도 확신하지 못하면, **then** 시스템은 피드 상속 섹터를 최후 수단(last-resort default)으로만 유지해야 한다(shall). 폴백 체인 순서는 명시적이어야 한다(CLI → 키워드 → 피드).
- **[Ubiquitous]** importer 는 CLI 출력 스키마를 검증해야 하며(shall), 스키마 드리프트(누락/무효 섹터) 시 크래시 없이 폴백 체인으로 강등해야 한다.

> 범위 정직성 (정규 요구사항 아님): REQ-060-2 의 폴백 체인은 **누락/무효** 섹터만 잡는다. 2026-07-03 지배적 실패 모드인 "**유효(canonical) 하지만 의미상 틀린**" 섹터(예 "피난처 된 금융주" → `energy_commodities`)는 폴백 체인이 통과시킨다 — 이 값들은 개별 기사 단위로는 되돌릴 수 없다(정본 CLI 판정을 무단 뒤집으면 새 오류를 부름). 따라서 이 실패 모드의 **알림 영향 억제**는 REQ-060-2 가 아니라 **REQ-060-4 의 코로보레이션 게이트(클러스터 수준 독립 키워드 확증)** 가 책임진다. REQ-060-2 는 기사 섹터를 완벽히 고치지 않으며, 그럴 필요도 없다.
>
> 근거/reference: CLI emit 경로 = `prompts.py:44-52`(sector 필드 요청) + `analyzer.py:_corrected_sector:81` + `import_host_results:890`. 키워드 분류기 = `sector_classifier.py:classify_sector`(SPEC-026 c3). 스키마 검증 = `analyzer.py:_validate_results:400`.

### REQ-060-3 — 클러스터 섹터: 다수결 (RC3)

- **[Event-Driven]** **When** 클러스터를 형성할 때, 시스템은 클러스터 섹터를 `group_articles[0]`이 아니라 **멤버 기사 섹터의 다수결**로 계산해야 한다(shall).
- **[Ubiquitous]** 다수결 가중은 **impact_score 우선, 동수 시 기사 수**로 하며, **최종 동점 시 최고 impact 기사의 섹터**를 tie-break 으로 채택해야 한다(shall). tie-break 규칙은 결정론적이어야 한다.
- **[State-Driven]** **While** 클러스터에 단일 기사만 있을 때, 그 기사 섹터가 곧 클러스터 섹터여야 한다(shall)(다수결의 자명한 경우).

### REQ-060-4 — 알림 게이팅: 티커 직접일치 또는 (quorum 섹터 + 키워드 코로보레이션) (RC5)

- **[Event-Driven]** **When** "포트폴리오 관련 고위험" 후보 클러스터를 평가할 때, 시스템은 다음 중 하나가 성립할 때만 발화해야 한다(shall):
  - (a) **티커 직접일치** — 알림 시점 DB 조회한 실보유 + 워치리스트 티커 각각의 **공식 회사명**(`ticker_metadata.name`, 예 `우리금융지주`·`한국전력`)이 클러스터 멤버 기사의 **제목**(`news_articles.title`) 또는 그 기사의 **분석 키워드**(`news_analysis.keywords` 배열 원소) 중 하나에 **정확 부분문자열**로 등장(이 경로는 섹터 판정과 무관). 자회사/브랜드 별칭은 매칭하지 않는다(정밀 우선), 또는
  - (b) **섹터 경로** — 클러스터 섹터가 보유 섹터와 일치하며 **AND** 그 클러스터 섹터가 **quorum**(멤버 기사의 >= 50% 가 동일 섹터에 동의) **AND** **키워드 코로보레이션**(아래 정의)을 모두 만족.
- **[Ubiquitous]** 티커 직접일치(a)의 매칭 술어는 **정확 부분문자열**이어야 하며(shall), 퍼지·자회사·브랜드 별칭 매칭을 해서는 안 된다(shall not). 즉 `ticker_metadata.name` 문자열 자체가 대상 텍스트의 부분문자열일 때만 일치한다(예 보유 316140 의 회사명 `우리금융지주` 는 제목 "우리은행 삼성월렛머니…" 의 부분문자열이 아니므로 일치하지 않는다). 이는 재현 결정론(2026-07-03 클러스터 A 발화 0)을 보장한다. 별칭 표(예 `우리은행`→우리금융지주)는 본 SPEC 범위 밖(Exclusions 참조).
- **[Unwanted]** **If** 대상 티커의 `ticker_metadata.name` 이 빈 문자열이거나 공백뿐이면, **then** 시스템은 그 티커를 직접일치 후보에서 **제외**해야 한다(shall not match). 근거: 빈 문자열은 모든 텍스트의 부분문자열이므로, 이 가드가 없으면 name 미채움 티커 1개가 모든 클러스터를 전수 발화시킨다. 라이브 실측(2026-07-03): `ticker_metadata` 54행 중 **44행이 name=''**(주문/보유 이력 있는 10종목만 채워짐). 따라서 직접일치 커버리지는 name 이 채워진 티커로 한정된다 — 현 실보유 015760(`한국전력`)·316140(`우리금융지주`)은 채워져 있어 영향 없음. name 전수 backfill 은 본 SPEC 범위 밖의 업스트림(메타데이터 적재) 개선 항목이다(Exclusions 참조).
- **[Ubiquitous]** 키워드 코로보레이션: 섹터 경로(b)의 승리 섹터 S 는, 클러스터 멤버 **제목**을 `sector_classifier` 키워드 세트로 채점했을 때 **score(S) >= 1 AND score(S) == max(전 섹터 득점)** 일 때에만 "확증됨(corroborated)" 으로 간주해야 한다(shall). 즉 S 는 최고 득점을 (단독 또는 공동으로) 반드시 달성해야 하며, S 를 포함하는 동점은 확증으로 계수한다. S 가 최고 득점 미만이거나 득점 0 이면 미확증이다(결정론적 동점 규칙 — D12).
- **[Unwanted]** **If** 승리 섹터 S 가 캐치올(`stock_market`·`macro_economy`)이면, **then** 시스템은 그 클러스터를 **섹터기반 알림 자격에서 명시적으로 제외**해야 한다(shall not 섹터 경로 발화). 캐치올은 코로보레이션 후보가 될 수 없으며(설령 `stock_market` 키워드 세트가 키워드 채점을 받더라도, 그리고 설령 캐치올이 argmax 를 차지하더라도) 자기 자신을 확증할 수 없다. 또한 REQ-060-1 의 정밀-우선 매핑은 어떤 업종명도 캐치올로 매핑하지 않으므로 캐치올 승리 섹터는 결코 보유 섹터와 일치하지 않는다. (캐치올이 argmax 를 차지해 특정 섹터의 확증을 부정하는 것은 억제-안전이므로 허용되나, 캐치올 자신의 확증만 금지된다.)
- **[Unwanted]** **If** 클러스터의 CLI/저장 섹터가 그 멤버 제목의 키워드 최고 득점 섹터와 **불일치**하면(예 저장=energy 인데 제목 키워드는 반도체/철강을 가리킴), **then** 시스템은 섹터 경로 발화를 해서는 안 된다(shall not). 티커 직접일치 경로는 영향받지 않는다.
- **[Unwanted]** **If** 클러스터 섹터가 quorum 미만(멤버 < 50% 동의)이거나 코로보레이션 미확증이거나 티커 직접일치가 없으면, **then** 시스템은 "포트폴리오 관련 고위험"을 발화하지 않아야 한다(shall not).
- **[Ubiquitous]** 실보유 + 워치리스트는 **알림 시점에 DB에서 조회**해야 하며(shall) 하드코딩·정적 목록을 사용해서는 안 된다.
- **[Unwanted]** **If** 실보유 + 워치리스트가 매핑된 섹터 0개로 해소되면(즉 보유 섹터 맵이 빔), **then** 시스템은 **섹터기반 고위험 알림을 비활성**해야 하며(shall not) 무게이트 "full coverage" 발화로 진입해서는 안 된다. 티커 직접일치 경로(REQ-060-4a, `ticker_metadata.name` 부분문자열)는 여전히 동작한다. (매핑 실패 후 조용히 전수 발화로 폴백하는 기존 동작을 명시적으로 금지 — D4.)
- **[Unwanted]** **If** 2026-07-03 라이브 오경보 2건(클러스터 A: 실측 `stock_market` x22 최다·`finance_banking` x1, 클러스터 B: 실측 `energy_commodities` x9(82%)·`defense_aerospace` x2)을 재현하면, **then** 신규 3중 게이트에서 두 건 모두 발화되지 않아야 한다(shall not). 클러스터 A 는 다수결 `stock_market`(비보유 캐치올) 불일치로, 클러스터 B 는 코로보레이션 미확증(제목 키워드 argmax ≠ energy)으로 각각 억제된다. (acceptance.md 시나리오 1 에서 단계별 walk-through 로 검증.)

> 근거/reference (정규 요구사항 아님): 발화 게이트 삽입 지점 = `relevance.py:tag_portfolio_relevance:75` 섹터 매칭 분기(108-112) 앞단, `_send_critical_alert:228` 호출부(128). 티커 직접일치(a)는 알림 시점 `article_ids` → `news_articles.title` + `news_analysis.keywords` join 과 `ticker_metadata.name` 부분문자열 비교(순수 계산). quorum·코로보레이션은 알림 시점 `article_ids` → `news_articles.sector`/`title` join + 순수 계산(스키마 불변). 코로보레이션 키워드 세트 = `sector_classifier._SECTOR_KEYWORDS` **재사용**(신규 키워드 세트 금지). **캐치올 주의**: `_SECTOR_KEYWORDS` 에서 `stock_market` 은 키워드 세트를 가지며(`코스피`·`코스닥`·`국민연금`·`증시`·`코스피지수`·`코스닥지수`, SPEC-026 c3 r2), `macro_economy` 는 아예 키가 없다 — 따라서 "캐치올은 키워드가 없어 자연히 미확증"이라는 서술은 사실이 아니며, 대신 **캐치올 제외를 명시적 정규 규칙으로 강제**한다(위 [Unwanted] 절). full_coverage 진입 지점 = `relevance.py:92,104-106`.

### REQ-060-5 — 호환성·무중단 (compat)

- **[Ubiquitous]** `story_clusters` 스키마 변경은 최소화해야 하며(shall), 가능하면 없어야 한다. quorum 신뢰도는 알림 시점에 `article_ids` → `news_articles.sector`에서 계산 가능(스키마 불변 선호). 컬럼이 불가피하면 **가산(additive) 마이그레이션만**.
- **[Ubiquitous]** SPEC-026 c3 테스트(`test_sector_classifier.py`·`test_sector_from_analysis.py`·`test_relevance.py`)는 **통과를 유지하거나 의식적으로 갱신**해야 한다(shall).
- **[Ubiquitous]** 텔레그램 알림 메시지 **포맷은 불변**이어야 한다(shall)("[NEWS ALERT] … (Impact 5/5, Sector: …) — 포트폴리오 관련 고위험 뉴스 감지").
- **[Ubiquitous]** paper/live 파이프라인은 중단 없이 동작해야 하며(shall), 모든 실행은 컨테이너 전용(`docker exec trading-app`)이어야 한다([[feedback_container_only_execution]]).
- **[Ubiquitous]** 오프라인 pytest 회귀 0, ruff clean 이어야 한다(shall).

## 제약 (Constraints)

- **strict_cost_zero**: 파이프라인 어디서도 유료 LLM/API 호출 금지. CLI `claude -p` 만 허용(SPEC-052/053). importer 는 유료 폴백을 유발해서는 안 됨.
- **하드코딩 금지**: 시장 종속 리터럴(업종명·섹터 키·거래소·통화 등) 코드 리터럴 금지. 시장별 외부설정(YAML) + `active_market()` 패턴([[feedback_no_hardcoding_multimarket]], 선례 `sector_taxonomy.yaml` commit 75e1b52).
- **서킷브레이커 규율**: 외부 엔드포인트(있다면) 실패 시 즉시 재시도 중단·지수백오프([[feedback_no_hammering_failing_endpoints]]). 단 본 SPEC은 DB·기존 캐시만 읽으므로 신규 외부 호출 없음.
- **시장중립 순수함수**: 다수결·quorum 로직은 DB 읽기 + 순수 계산(신규 외부 I/O 0).

## Exclusions (What NOT to Build)

- **`ticker_metadata.name` 전수 backfill (D9 빈 name 가드의 커버리지 한계)**: 라이브 54행 중 44행이 name='' 이라 티커 직접일치 커버리지가 10종목으로 제한되나, name 적재는 업스트림 메타데이터 로더(SPEC-054 계열)의 책임이므로 본 SPEC 에서 backfill 하지 않는다. 빈 name 은 직접일치 후보에서 제외(REQ-060-4 [Unwanted])로 안전성만 보장한다.

- **티커 별칭/자회사 표 (문서화된 향후 확장 — D9)**: 티커 직접일치(REQ-060-4a)는 `ticker_metadata.name` 공식 회사명의 **정확 부분문자열** 매칭만 수행한다. 자회사·브랜드·구명칭 별칭(예 `우리은행`→우리금융지주(316140), `KEPCO`→한국전력(015760), `삼성전자`의 사업부 브랜드 등)을 인식하는 별칭 매핑 표는 본 SPEC 에서 **구축하지 않는다**(정밀 우선 — 별칭 확장은 재현 결정론을 깨고 오경보 표면을 넓힘). 이는 클러스터 A 기사 661135 "우리은행 삼성월렛머니…"가 보유 316140(우리금융지주)에 티커 일치하지 **않도록** 하여 발화 0 을 결정론적으로 보장한다. 향후 별칭 표가 필요하면 별도 SPEC 으로, YAML 외부설정 + 리콜/정밀 트레이드오프 측정을 동반해 도입한다.
- **US 데이터소스 어댑터 자체**: US 매핑 설정(YAML 항목)은 준비하되, pykrx 대체 US 업종 데이터 어댑터는 구축하지 않는다. `active_market()`로 배선만 열어둔다.
- **재클러스터링 알고리즘 재설계 (수용 잔여 리스크 — D6)**: union-find 유사도/키워드 임계(`clustering.py:20-22`)·클러스터 형성 로직은 손대지 않는다. 섹터 결정만 다수결로 바꾼다. **명시적 잔여 리스크**: 08:15 클러스터 B 는 NH농협은행·AWS·포스코·SK하이닉스·철강 BSI·수소 강관·"피난처 된 금융주"를 한 클러스터로 **과잉병합**했다(금융+철강+반도체+클라우드가 키워드-오버랩 union-find 로 뭉침). 이 과잉병합은 본 SPEC이 **고치지 않는다** — "쓰레기 클러스터의 다수결도 여전히 쓰레기"일 수 있다. REQ-060-4 의 코로보레이션 게이트는 이 과잉병합의 **알림 영향만 완화**(승리 섹터가 제목 키워드로 확증되지 않으면 발화 억제)하며, 병합 자체를 되돌리지 않는다. 재클러스터링 임계 재설계는 별도 SPEC 으로 남긴다(사유: 클러스터링 로직 변경은 회귀 표면이 넓고 본 SPEC 의 오경보 억제 목표는 게이트만으로 달성 가능).
- **과거 `story_clusters` 행 백필/재라벨링**: 이미 저장된 오태깅 행을 소급 교정하지 않는다. 재클러스터링(약 3h 주기)이 자연 갱신한다.
- **기사 섹터 완전 교정**: REQ-060-2 는 누락/무효 섹터만 폴백으로 처리한다. "유효하지만 의미상 틀린" 섹터를 기사 단위로 강제 정정하는 로직(정본 CLI 판정 뒤집기)은 구축하지 않는다 — 알림 영향은 REQ-060-4 코로보레이션 게이트로 억제한다.
- **유료 API 뉴스 재분석**: 비용 0 제약상 과거 기사를 유료 LLM으로 재분석하지 않는다.
- **알림 임계·dedup 윈도우 변경**: `IMPACT_CRITICAL_THRESHOLD=5`·`_ALERT_DEDUP_WINDOW_HOURS=18`은 불변. 게이팅 조건(quorum/코로보레이션/티커)만 추가.

## 관련 SPEC

- SPEC-TRADING-026 (news intelligence c3/A2): 본 SPEC이 확장·경화하는 섹터 분류 토대.
- SPEC-TRADING-054 (`ticker_metadata`·`sector_loader`): 티커→업종명 진실원천 제공.
- SPEC-TRADING-053/052 (CLI 비용 0 가드): analyze_news CLI 경로 규율 상속.
- SPEC-TRADING-014/013 (news 모듈·relevance): 알림 발화 원본 모듈.
