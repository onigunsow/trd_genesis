---
id: SPEC-TRADING-061
version: 0.1.0
status: draft
created: 2026-07-07
updated: 2026-07-07
author: oni
priority: high
issue_number: 0
labels: [news, intelligence, alignment, data-integrity, cost-zero, fail-closed]
---

# SPEC-TRADING-061 — 뉴스 분석 결과-기사 정렬 근원 수정 (position→ID 매핑)

작성: 2026-07-07 · 기준 origin/main: 2cbde00 · 범위: 뉴스 인텔리전스(LLM 분석 결과 → 기사 매핑 정합성), 시장중립·비용 0

## HISTORY

- 2026-07-07 v0.1.0 (draft): 최초 작성. 2026-07-07 라이브 DB 실측으로 `news_analysis`의 keywords/classification/sentiment/impact가 기사 간에 뒤섞여 저장됨을 확인. 근본원인 = LLM/파서가 기사 전송 순서와 다른 순서로 결과를 반환할 때 분석기가 결과를 **리스트 위치(position)** 로 기사에 매핑하기 때문. SPEC-060 commit 2cbde00(코로보레이션 채점에서 `news_analysis.keywords` 제외)은 증상 우회였음 — 본 SPEC은 근원을 고쳐 classification/sentiment/impact/keywords 전부가 올바른 기사에 정렬되도록 한다. CLI import 경로(`import_host_results`)와 Haiku 폴백 저장 경로(`_store_results`)의 위치 기반 매핑을 **ID 기반 매핑**으로 교체하고, ID 불일치 시 **fail-closed** 거부한다.

## 배경 / 사건 (2026-07-07 라이브, DB 실측 — `news_analysis` ⋈ `news_articles`)

일일 리포트(2026-07-07 16:02)가 macro/micro 종합 총평에서 **"제목↔키워드·전략 어긋남"** 을 스스로 보고하고 자기 분석을 "관찰 테마"로 강등했다. 원인을 라이브 DB에서 재도출(read-only)한 결과, `news_analysis`의 분석 필드가 엉뚱한 기사에 붙어 있었다.

| 기사 제목(`news_articles.title`) | 저장된 분석(`news_analysis`) | 정합성 |
|---|---|---|
| "무릎 골관절염 K-세포·유전자 치료제…" | keywords {삼성전자, 반도체, 차익실현} | 어긋남 (제약 기사에 반도체 키워드) |
| "코스피 7400선…삼전닉스 8% 급락" | keywords {스페이스X, 나스닥100, 패시브수급} | 어긋남 (국내 반도체 급락 기사에 미국 우주/지수 키워드) |

핵심 실측 사실:

1. **카운트는 일치, 순서만 뒤섞임(REORDER).** `audit_log`의 `NEWS_INTEL_IMPORT_OK` 는 **모든 배치**에서 `articles_imported == results_parsed`(예 97/97, 98/98)를 기록한다. 즉 이것은 개수 불일치가 아니라 **순서 뒤섞임**이며, **개수만 검사하는 가드로는 잡히지 않는다.**
2. **정렬이 깨지면 classification/impact/sentiment/keywords가 전부 동시에 오염된다.** 한 결과 객체 전체가 다른 기사에 붙으므로 4개 필드가 함께 어긋난다. keywords만의 문제가 아니다.
3. **CLI 경로가 활성 오염원이다.** 일일 리포트가 "cli-claude-max 32건"으로 보고하듯 정본 경로는 CLI import(`import_host_results`)다. Haiku 폴백(`_store_results`)도 동일 결함을 가진다.
4. **SPEC-060 commit 2cbde00 은 증상 우회였다.** 그 커밋은 코로보레이션 채점에서 `news_analysis.keywords`를 제외했다(`relevance.py` ~206/213/237 주석이 "keywords는 정렬이 어긋난다"고 명시). 이는 알림 오경보 표면 하나만 가렸을 뿐, classification/sentiment/impact/keywords의 근본 오염은 그대로다. 본 SPEC이 근원을 고치면 그 우회의 전제(keywords 신뢰 불가)가 해소된다.

## 근거 (코드 실측, 2026-07-07)

| 근본원인 | 사실 | file:line |
|---|---|---|
| RC1 위치 매핑 (CLI, 활성) | `import_host_results` 가 결과를 리스트 위치로 기사에 매핑: `for i, result in enumerate(results): aid = article_ids[i]`. 결과 순서가 기사 순서와 다르면 전 필드 오염. | `analyzer.py:872-875` |
| RC2 위치 매핑 (Haiku 폴백) | `_store_results` 가 동일 결함: `for i, result in enumerate(results): article = articles[i]`. | `analyzer.py:525-528` |
| RC3 파서 재정렬 가능 | `_parse_analysis_response` 의 폴백 전략들이 입력 배치와 **다른 순서/개수** 로 객체를 반환할 수 있음: `_extract_individual_objects`(정규식 `finditer`, 필터에 걸린 객체는 탈락→인덱스 이동)·`_try_recover_truncated_array`(잘린 꼬리 객체 탈락). `_validate_results` 는 `data[:expected_count]` 로 앞에서 자를 뿐 정렬을 보장하지 않음. | `analyzer.py:261-330`, `_try_recover_truncated_array:332`, `_extract_individual_objects:379`, `_validate_results:400` |
| RC4 프롬프트 계약 부재 | `build_analysis_prompt` 은 기사를 1-기반 인덱스 `[1] [2] …` 로 나열하지만, 시스템 프롬프트 `ARTICLE_ANALYSIS_SYSTEM` 의 출력 스키마(classification·impact_score·investment_implication·keywords·sentiment·sector)는 **각 결과에 기사 식별자를 되돌려 echo 하라고 요구하지 않는다.** 따라서 정렬은 오로지 순서 가정에만 의존한다. | `prompts.py:9`(system), `prompts.py:55-64`(스키마), `prompts.py:70-86`(build) |
| RC5 감사가 순서를 못 봄 | `NEWS_INTEL_IMPORT_OK` 는 `articles_imported`·`results_parsed`(둘 다 개수)만 기록 → REORDER는 감사에 안 보임. | `analyzer.py:905-908` |

### 이미 존재하는 자산 (재사용 대상 — 신규 구축 최소화)

| 자산 | 상태 | 위치 |
|---|---|---|
| 기대 기사 ID 집합 | pending 메타데이터가 `article_ids` 를 이미 보관(정본 순서·집합의 진실원천). ID 기반 매핑·검증의 기준 집합. | `analyzer.py:818-823` (`meta.get("article_ids")`) |
| ID→기사 조회 | `_fetch_articles_by_ids` 가 이미 ID로 기사 dict를 반환. echo된 ID의 존재/유효성 검증에 재사용. | `analyzer.py:913` |
| 결과 정규화 훅 | `_validate_results` 가 결과별 필드 정규화를 이미 수행 → 결과별 echo ID 추출·검증을 여기(또는 인접)에 배선. | `analyzer.py:400` |
| 감사 인프라 | `audit(...)` 이벤트 발행. 신규 `NEWS_INTEL_ALIGN_REJECT` 발행에 재사용. | `analyzer.py:849,905` |
| 비용 0 CLI 경로 | 호스트 cron `scripts/analyze_news.sh` `claude -p` → `import_host_results()`. **유일한 LLM 예산**(strict_cost_zero). 백필도 이 경로만 사용. | `scripts/analyze_news.sh`, `analyzer.py:800` |

## 목표

1. 프롬프트가 각 기사에 **안정적·명시적 식별자**를 부여하고, LLM이 각 결과 객체에서 그 식별자를 **그대로 echo** 하도록 계약을 강제한다.
2. CLI(`import_host_results`)와 Haiku(`_store_results`) 저장 경로가 결과를 **echo된 식별자로 매핑**한다 — 리스트 위치로 매핑하지 않는다.
3. 식별자 누락·불일치(missing/extra/duplicate) 시 **fail-closed**: 해당 결과를 저장하지 않고 감사 이벤트를 남긴다. 절대 어긋난 행을 저장하지 않는다.
4. 이미 오염된 `news_analysis` 행을 **정렬 재수리**하는 일회성·멱등 CLI-only 엔트리포인트를 제공한다(영향 범위 정의 포함).
5. 회귀 0·다운타임 0·비용 0. 2026-07-07 오염 재현 시 정렬이 올바르게 복구됨을 테스트로 증명(RED-우선).

## EARS 요구사항

### REQ-061-1 — 프롬프트 계약: 안정적 식별자 echo 강제 (RC4)

- **[Ubiquitous]** 시스템은 분석 프롬프트에서 각 기사를 **안정적이고 명시적인 식별자**와 함께 제시해야 하며(shall), 그 식별자는 기사 DB id(또는 `article_ids` 에 결정론적으로 대응되는 1-기반 idx)여야 한다.
- **[Ubiquitous]** 시스템은 LLM에게 **각 결과 객체에 그 식별자를 그대로(verbatim) echo** 하도록 지시해야 한다(shall). 출력 스키마는 식별자 필드를 필수 필드로 포함해야 한다.
- **[Ubiquitous]** 식별자 필드명·의미는 배치 전송 순서와 무관해야 하며(shall), 정렬은 결과 순서 가정이 아니라 echo된 식별자로만 성립해야 한다.

> 근거/reference (정규 요구사항 아님): 프롬프트 빌더 = `prompts.py:build_analysis_prompt:70`(현재 `[i]` 인덱스만 표시). 출력 스키마 = `ARTICLE_ANALYSIS_SYSTEM`(`prompts.py:9`, 필드 목록 `prompts.py:59`). 식별자 선택 트레이드오프(DB id = 전역 명확하나 긴 정수라 전사 오류 위험 / 1-기반 idx = 짧아 echo 충실도 높으나 `article_ids` 순서 안정성에 의존)는 plan.md 에서 결정 — 어느 쪽이든 REQ-061-3 의 집합 검증이 안전망.

### REQ-061-2 — ID 기반 매핑: 위치 매핑 철폐 (RC1·RC2)

- **[Event-Driven]** **When** CLI 분석 결과를 import 할 때, 시스템은 각 결과를 **echo된 식별자**로 해당 기사에 매핑해야 하며(shall), 리스트 위치(`enumerate` 인덱스)로 매핑해서는 안 된다(shall not).
- **[Event-Driven]** **When** Haiku 폴백 결과를 저장할 때, 시스템은 동일하게 **echo된 식별자** 로 매핑해야 하며(shall) 위치로 매핑해서는 안 된다.
- **[Ubiquitous]** 두 경로(CLI·Haiku)는 **동일한 ID 기반 매핑·검증 로직을 공유**해야 한다(shall) — 두 곳에 중복 구현하지 않는다.

> 근거/reference: CLI 위치 매핑 = `analyzer.py:872-875`(`aid = article_ids[i]`). Haiku 위치 매핑 = `analyzer.py:525-528`(`article = articles[i]`). 공유 매핑 함수 후보 = 신규 `_align_results_by_id(results, article_ids) -> dict[int, dict]`(순수 계산). `_corrected_sector` 섹터 교정(analyzer.py:890/545)은 매핑된 (result, article) 쌍에 그대로 적용.

### REQ-061-3 — Fail-closed 정렬 검증 (RC3·RC5)

- **[Unwanted]** **If** 결과에 유효한 식별자가 없으면, **then** 시스템은 그 결과를 **저장하지 않아야** 한다(shall not store).
- **[Unwanted]** **If** 반환된 식별자 집합이 기대 `article_ids` 집합과 일치하지 않으면(missing / extra / duplicate id), **then** 시스템은 **불일치에 해당하는 결과들을 거부**해야 하며(짝지어지지 않은 것은 아무것도 저장하지 않음, shall not store misaligned rows), 감사 이벤트(`NEWS_INTEL_ALIGN_REJECT`)를 발행해야 한다(shall).
- **[Ubiquitous]** 시스템은 **정확히 매칭된 (result ↔ article) 쌍만 저장**해야 한다(shall). 부분 배치는 매칭된 부분만 저장하고 나머지는 거부한다(전량 폐기 아님 — 단, duplicate id 는 해당 id 를 모호로 간주해 거부).
- **[Ubiquitous]** 감사 이벤트는 순서 오류를 관측 가능하게 해야 한다(shall): 기대 개수·매칭 개수·거부 개수(및 원인: missing/extra/duplicate/no-id)를 기록. (기존 `NEWS_INTEL_IMPORT_OK` 의 개수 전용 기록을 보강.)

> 근거/reference: 파서 재정렬원 = `_parse_analysis_response:261`·`_extract_individual_objects:379`·`_try_recover_truncated_array:332`. 검증 배선 지점 = `_validate_results:400`(결과별 id 추출) + import 루프 앞단(집합 대조). 기대 집합의 진실원천 = pending 메타 `article_ids`(`analyzer.py:818-823`).

### REQ-061-4 — 오염 행 정렬 재수리 (일회성·멱등·CLI-only)

- **[Event-Driven]** **When** 운영자가 재수리 엔트리포인트를 실행하면, 시스템은 영향 구간의 `news_analysis` 행을 **CLI 경로로 재분석**하고 **ID 기반 정렬**로 덮어써야 한다(shall).
- **[Ubiquitous]** 재수리는 **멱등**해야 하며(shall) 재실행이 안전해야 한다(같은 구간 재실행 시 동일 결과·중복 부작용 없음).
- **[Ubiquitous]** 재수리는 **비용 0(CLI-only)** 이어야 하며(shall) 유료 Anthropic API를 호출해서는 안 된다(shall not). 재분석은 기존 host CLI 배치 큐(pending → `claude -p` → import)를 재사용한다.
- **[Ubiquitous]** 영향 구간은 **결정론적으로 정의**되어야 한다(shall): 운영자 지정 `--since DATE`(기본값 = 정렬 오류 최초 관측일 등 문서화된 경계) 이후 발행 기사의 `news_analysis` 행을 대상으로 하며, 대상 선정 규칙(예 `model_used='claude-cli'` 한정 또는 전체)을 plan.md 에서 확정한다.
- **[Unwanted]** **If** 재분석 결과가 REQ-061-3 의 fail-closed 검증을 통과하지 못하면, **then** 시스템은 해당 행을 **덮어쓰지 않아야** 한다(shall not overwrite) — 오염 행을 다른 오염으로 대체하지 않는다.

> 근거/reference: 배치 export = `export_pending_for_host`(`analyzer.py:762`, `article_ids` 메타 기록). import = `import_host_results:800`. 덮어쓰기는 현재 `INSERT … ON CONFLICT (article_id) DO NOTHING`(analyzer.py:869) 이므로 재수리는 UPSERT(`DO UPDATE`) 또는 대상 행 선삭제 후 재삽입이 필요 — 스키마/SQL 변경 시 REQ-061-6 통합테스트 게이트 적용.

### REQ-061-5 — 테스트 (RED 우선)

- **[Ubiquitous]** 시스템은 **재정렬된 LLM 응답이 더 이상 필드를 뒤섞지 않음**을 증명하는 재현 테스트를 가져야 한다(shall): 기사 순서와 다른 순서로 결과를 반환해도 ID 기반 매핑이 올바른 정렬을 산출.
- **[Ubiquitous]** 시스템은 **id/개수 불일치(missing/extra/duplicate/no-id)가 fail-closed 거부**를 유발함을 증명하는 테스트를 가져야 한다(shall): 어긋난 행이 저장되지 않고 `NEWS_INTEL_ALIGN_REJECT` 가 발행됨.
- **[Ubiquitous]** 시스템은 **올바른 순서 응답이 여전히 정상 저장**됨을 증명하는 회귀 테스트를 가져야 한다(shall).
- **[Ubiquitous]** RED 테스트는 수정 전 현재 코드에서 **실제로 실패**해야 하며(shall), GREEN 후 통과해야 한다.

> 근거/reference: 재현 테스트는 순수 파서/매핑 함수 단위로 작성 가능(외부 I/O 0). SQL/마이그레이션이 바뀌면 REQ-061-6 의 통합테스트가 추가 게이트.

### REQ-061-6 — 호환성·비용·실행 규율 (compat)

- **[Ubiquitous]** strict_cost_zero 를 준수해야 하며(shall), 분석·재수리 어디서도 유료 Anthropic API를 호출해서는 안 된다(CLI `claude -p` 만 허용, SPEC-052/053).
- **[Ubiquitous]** 모든 실행은 컨테이너 전용(`docker exec trading-app`)이어야 한다(shall)([[feedback_container_only_execution]]). 호스트 직접 실행 금지.
- **[Ubiquitous]** 시장 종속 리터럴을 코드에 하드코딩해서는 안 된다(shall not)([[feedback_no_hardcoding_multimarket]]). 식별자·정렬 로직은 시장 중립 순수 계산이어야 한다(US 시장 재사용 대비).
- **[Ubiquitous]** SQL/스키마/마이그레이션 변경 시(예 REQ-061-4 의 UPSERT) 배포 전 **통합테스트**(`pytest tests/integration/ -m integration`, `trading_test` DB)를 실행해야 한다(shall) — mock 테스트는 DB 형상 버그에 거짓그린을 준다([[reference_integration_tests]]).
- **[Unwanted]** **If** 구(舊) 프롬프트로 생성된(식별자 echo 없는) 결과가 유입되면, **then** 시스템은 **fail-closed** 로 거부하고 감사·경고를 남겨야 한다(shall) — 위치 폴백으로 조용히 저장해서는 안 된다(권고: fail-closed, plan.md 에서 확정).
- **[Ubiquitous]** 오프라인 pytest 회귀 0, ruff clean 이어야 한다(shall).

## 제약 (Constraints)

- **strict_cost_zero**: 파이프라인·재수리 어디서도 유료 LLM/API 호출 금지. CLI `claude -p` 만 허용. importer/backfill 은 유료 폴백을 유발해서는 안 됨.
- **컨테이너 전용 실행**: 모든 코드 실행은 `docker exec trading-app …`. 호스트는 DB·KRX 자격증명 미보유([[feedback_container_only_execution]]).
- **하드코딩 금지·멀티마켓**: 정렬/매핑은 순수 계산이며 시장 종속 리터럴 없음. US 시장에서 동일 로직 재사용 가능해야 함([[feedback_no_hardcoding_multimarket]]).
- **통합테스트 게이트**: SQL/마이그레이션 변경은 `trading_test` DB 통합테스트 선행([[reference_integration_tests]]).
- **fail-closed 우선**: 애매하면 저장 거부. 오염 행을 만들 바에 빈 분석이 낫다(알림·리포트가 어긋난 근거로 오도되지 않도록).

## Exclusions (What NOT to Build)

- **뉴스 분석 스키마 재설계**: `news_analysis` 컬럼(summary_2line·impact_score·keywords·sentiment·classification…)을 재설계하지 않는다. REQ-061-4 의 덮어쓰기에 필요한 최소 SQL 변경(예 UPSERT)만 허용.
- **LLM 프롬프트 전면 개편**: 식별자 echo 계약 추가 외에 분석 기준(classification 정의·impact 척도·sector 규칙)은 변경하지 않는다. SPEC-060 의 sector 경로는 그대로 상속.
- **파서 재작성**: `_parse_analysis_response`·`_extract_individual_objects`·`_try_recover_truncated_array` 의 견고성 전략은 유지한다. 본 SPEC은 파서가 **재정렬할 수 있음을 전제**하고 매핑을 ID 기반으로 바꿔 방어한다(파서를 "순서 보존"으로 고치려 하지 않는다 — 그 가정 자체가 깨지기 쉬움).
- **SPEC-060 코로보레이션 재개편**: 2cbde00 이 keywords를 코로보레이션에서 제외한 것을 본 SPEC이 자동 원복하지 않는다. keywords 정렬이 신뢰 가능해진 뒤 코로보레이션에 keywords를 되살릴지는 별도 판단(후속) — 본 SPEC 범위는 저장 정렬 근원 수정까지.
- **과거 전 구간 소급 재분석**: REQ-061-4 는 `--since` 로 **경계된** 구간만 재수리한다. 전체 히스토리 재분석은 비용/시간 규율상 하지 않는다.
- **알림 게이트/임계 변경**: SPEC-060 의 3중 게이트·Impact 임계·dedup 윈도우는 불변. 본 SPEC은 게이트에 **더 정확한 입력**을 공급할 뿐 게이트 로직을 바꾸지 않는다.
- **뉴스 클러스터링 로직**: `story_clusters` 형성·다수결(SPEC-060)은 손대지 않는다.

## 관련 SPEC

- SPEC-TRADING-060 (news sector·relevance): keywords 오정렬을 코로보레이션에서 제외한 증상 우회(2cbde00)의 근원을 본 SPEC이 수정. 게이트에 정확한 입력 공급.
- SPEC-TRADING-026 (news intelligence c3/A2): sector emit·교정 경로 토대. 본 SPEC의 ID 매핑이 `_corrected_sector` 를 올바른 (result, article) 쌍에 적용하도록 보장.
- SPEC-TRADING-053/052 (CLI 비용 0 가드): analyze_news CLI 경로·strict_cost_zero 규율 상속. 재수리도 CLI-only.
- SPEC-TRADING-014/013 (news 모듈): 뉴스 분석·저장 원본 모듈.
