# SPEC-TRADING-060 (compact) — 뉴스 섹터 분류·연관성 판정 개편

status: draft · v0.3.0 (plan-audit review-2 반영, D9–D14) · origin/main d50fe04 · 비용 0·시장중립·컨테이너 전용
labels: [news, sector, relevance, alerts, hardcoding, cost-zero]

## 사건 (2026-07-03 라이브 DB 실측)
"포트폴리오 관련 고위험" Impact 5/5 오경보 2건. **실측 멤버 섹터 분포**(허수아비 아님):
- 클러스터 A (01:15, 태깅 finance_banking): `stock_market` x22 최다·`semiconductor` x9·finance x1 (총 35). 발화 섹터는 첫 기사 상속.
- 클러스터 B (08:15, 태깅 defense_aerospace): `energy_commodities` x9(82%)·`defense_aerospace` x2·finance x0 (총 11). 대표기사 "피난처 된 금융주"가 DB엔 energy 로 저장(유효 canonical 하지만 의미상 틀림). NH농협은행+AWS+포스코+SK하이닉스+철강 과잉병합.
오래된 품질 결함(story_clusters 섹터 체계적 오태깅).

## 근본원인 (file:line)
- RC1 피드 상속(`sources.py:37`, `rss_fetcher.py:275`)
- RC2 보수적 재분류·캐치올 미교정(`sector_classifier.py:84-109`)
- RC3 클러스터 섹터 = 첫 기사(`clustering.py:193` `group_articles[0]["sector"]`)
- RC4 하드코딩 `TICKER_SECTOR_MAP`(**21종목**, `context_builder.py:43-75`, 실보유 015760/316140 미포함 → stock_market 폴백)
- RC5 알림 = 단일 섹터 문자열 매칭·quorum 없음 + 매핑 실패 시 full_coverage 무게이트 우회(`relevance.py:104-110`)
- RC6 실 업종명 리터럴 = 가운뎃점·단형(`금융`·`전기·가스`·`전기·전자`·`금속`…, 13개 distinct). 015760=`전기·가스`, 316140=`금융`(industry `기타금융`). 옛 리터럴로 seed 시 전 보유 미해소 → 악화.
- **이미 존재**: CLI 섹터 emit 경로(`prompts.py:44` + `analyzer.py:_corrected_sector`) → REQ-060-2는 경화이지 신규 아님.

## 요구사항 (EARS)
- **REQ-060-1** 하드코딩 철폐: 티커→news 섹터 = `ticker_metadata`(업종명) + 시장별 YAML **정밀-우선** 매핑(**명확 6개만 매핑, 모호 7개 = `None`**; `전기·전자` 는 반도체+이차전지+가전+방산+중전기 혼재이고 DB industry 세분 신호 없어 `None` — D13). 미매핑 → `None`(가짜 캐치올 금지). `TICKER_SECTOR_MAP`(fan_in 3) 제거.
- **REQ-060-2** 기사 섹터 비용 0: 기존 CLI `sector` 경로 경화. 폴백 체인 = CLI → 키워드분류기 → 피드(last-resort). **누락/무효만 처리**; "유효하지만 틀림"은 사정거리 밖(→ REQ-060-4 코로보레이션이 알림 영향 억제). 신규 프롬프트 필드 없음.
- **REQ-060-3** 클러스터 섹터 = 다수결(impact 가중 최빈 → 기사 수 → 최고 impact tie-break), 첫 기사 상속 폐기.
- **REQ-060-4** 알림 3중 게이트 = 티커 직접일치 OR (섹터일치 AND quorum >= 50% AND **키워드 코로보레이션**). **티커 직접일치(D9)** = 알림시점 DB 조회 live holdings+watchlist 각 티커의 `ticker_metadata.name`(회사명)이 멤버 `news_articles.title` 또는 `news_analysis.keywords`(배열)에 **정확 부분문자열**로 등장(섹터무관, 퍼지·자회사·별칭 금지 — `우리은행`≠우리금융지주; **빈 name('' — 라이브 44/54행)은 후보 제외**, 빈 문자열 전수매치 방지). 뉴스 모듈에 티커 추출기 부재 → 회사명 부분문자열로 정의. 코로보레이션 = 멤버 제목을 `sector_classifier._SECTOR_KEYWORDS` 재사용 채점, **score(S) >= 1 AND score(S) == max(전 섹터)** 여야 확증(S 포함 동점은 확증 — D12). **[D10] 캐치올(`stock_market`·`macro_economy`)은 섹터기반 알림 자격에서 명시적 제외**(코로보레이션 후보 불가·업종명→캐치올 매핑 불가; `stock_market` 은 키워드 세트가 존재하므로 "키워드 없음" 논리 금지). **[D4]** 매핑 섹터 0개 → 섹터기반 고위험 알림 비활성(full_coverage 무게이트 발화 금지, 티커직접만). 07-03 오경보 재현 → 발화 0(수용 시나리오, 실측 구성). **클러스터 B 는 코로보레이션이 유일한 차단 장치**(다수결+quorum+섹터일치만으로는 재발화 — 감사 D1; energy score 0 이라 동점 무관 미확증).
- **REQ-060-5** compat: story_clusters 스키마 불변(quorum·코로보레이션은 article_ids→news_articles.sector/title 계산), 마이그 불필요, SPEC-026 테스트 유지/갱신, 텔레그램 포맷 불변, 회귀 0, ruff clean.

## 수용 기준 (요약)
1. 07-03 오경보 2건 재현(실측 구성) → 발화 0 (HARD 게이트, repro-first). 클러스터 B 코로보레이션 게이트 on/off 대조로 D1 증명.
2. 정당 연관 뉴스(316140 언급 or 금융 quorum+코로보레이션) → 정상 발화, 포맷 불변.
3. 미매핑/모호 업종명 → `None`·크래시 없음·가짜 매칭 없음.
4. 015760(`전기·가스`)→energy_commodities / 316140(`금융`)→finance_banking (DB 유도, 하드코딩 철폐 실증).
5. 다수결·tie-break 결정론.
6. CLI sector 누락 → 폴백 체인·유료 폴백 0.
- 엣지: **매핑 섹터 0개 → 섹터 발화 비활성·티커직접만(D4)** / **캐치올 섹터 → 섹터 자격 명시 제외(D10, stock_market 키워드 존재하므로 명시 가드)** / 단일 멤버 quorum 100% / article_ids 없음 → 섹터 발화 억제.

## 영향 파일
- [MODIFY] `data/sector_taxonomy.yaml`(news_sector_map, 실 업종명 seed), `data/sector_taxonomy.py`(news_sector())
- [NEW] `news/ticker_sector.py`(resolve_ticker_sector, @MX:ANCHOR)
- [MODIFY] `news/context_builder.py`·`news/intelligence/relevance.py`(quorum·코로보레이션·티커직접·full_coverage 비활성, @MX:WARN)·`reporter.py`·`clustering.py`(_majority_sector)·`analyzer.py`(폴백체인)
- [NEW 테스트] test_false_alert_repro_2026_07_03 / test_cluster_majority_sector / test_ticker_sector_resolution / test_alert_gate
- [MODIFY 테스트] test_relevance.py(하드코딩 제거 반영)
- 마이그레이션: 불필요(story_clusters 불변)
- [NEW] `tests/news/intelligence/test_ticker_direct_match.py` (D9 회사명 부분문자열·빈/공백 name 가드)

## Exclusions (What NOT to Build)
- **티커 별칭/자회사 표(D9, 향후 확장)**: 티커 직접일치는 `ticker_metadata.name` 정확 부분문자열만. `우리은행`→우리금융지주 같은 별칭 매핑 표는 미구축(정밀 우선, 재현 결정론 보존). 별도 SPEC.
- US 데이터소스 어댑터 자체(YAML 설정만 준비, 어댑터 미구축).
- **재클러스터링 알고리즘 재설계 (수용 잔여 리스크·D6)**: 과잉병합(금융+철강+AWS 한 클러스터)은 본 SPEC이 고치지 않음. 코로보레이션은 **알림 영향만 완화**, 병합 자체 미교정. 별도 SPEC.
- 기사 섹터 완전 교정("유효하지만 틀림" 기사 단위 정정 금지, 알림 영향은 코로보레이션으로 억제).
- 과거 story_clusters 백필/재라벨링.
- 유료 API 뉴스 재분석.
- 알림 임계(Impact 5)·dedup 윈도우(18h) 변경.
