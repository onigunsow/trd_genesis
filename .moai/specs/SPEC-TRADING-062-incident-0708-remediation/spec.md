# SPEC-TRADING-062 — 7/8 인시던트 원복: 회로차단 breach 분류 + 뉴스 정렬 content-anchor

- Status: draft
- Created: 2026-07-08
- Author: oni (session)
- Supersedes-scope: SPEC-TRADING-061 (뉴스 정렬 후속 — 제2 실패모드)

## 배경 (관측된 사실)

2026-07-08 라이브 로그 감사에서 두 결함이 확정됨.

### 결함 A — 회로차단기 과설계
`orchestrator.py:1388`, `1852`가 **모든** pre-order breach에 무조건 `circuit_breaker.trip`
(전체 halt)을 건다. 07:33 `avg_down`(물타기 방지 — 가드가 나쁜 매수를 이미 차단) 단일
breach로 하루 종일 halt됐고, `avg_down`은 auto_resume benign 목록에 없어 자동재개도 안 됨.

breach 성격은 둘로 갈린다:
- per-signal 자문 차단(그 주문만 거부하면 충분, 계좌 위험 없음): `avg_down`, `repeat_buy`,
  `per_ticker`, `total_invested`
- 계좌 전체 위험(halt 정당): `daily_loss`

### 결함 B — 뉴스 분석 정렬 스크램블 재발 (제2 실패모드)
호스트 CLI(`analyze_news.sh:62`)가 최대 100개 기사를 **단일 `claude -p` 호출**로 전송.
94~98개 대용량 배치에서 모델이 idx-내용 대응을 놓쳐, 완전한 idx 순열 {1..N}을 echo하되
내용은 다른 기사에 붙인다. SPEC-061의 `_align_results_to_articles`는 idx **집합 완전성**만
검증하므로 조용히 통과(IMPORT_OK) → 의료 기사에 시장 분석이 붙어 11:15 오경보 발생.
idx-집합 검사는 모델측 오라벨링을 구조적으로 탐지 불가.

## 요구사항 (EARS)

### 그룹 A — 회로차단 breach 분류
- REQ-062-A1: WHEN pre-order 한도검사가 실패하고 breach가 **계좌 전체 위험(daily_loss)을
  포함하지 않을** 때, THE 시스템 SHALL 해당 주문만 거부(reject)하고 회로차단을 트립하지
  않으며 사이클을 계속 진행한다.
- REQ-062-A2: WHEN breach가 계좌 전체 위험(daily_loss)을 포함할 때, THE 시스템 SHALL 기존대로
  회로차단을 트립한다(halt).
- REQ-062-A3: breach 분류는 시장 종속 하드코딩 없이 breach 접두 토큰(':' 앞)으로 판별하며,
  계좌-halt 토큰 집합을 단일 상수로 정의한다(US 시장 재사용 대비, 하드코딩 금지 원칙).
- REQ-062-A4: per-signal 거부 시에도 기존 텔레그램 "한도 위반 차단" 브리핑과 `LIMIT_BREACH`
  감사는 유지한다(관측성 불변).

### 그룹 B — 뉴스 content-anchor 검증
- REQ-062-B1: THE 분석 프롬프트 SHALL 각 결과가 idx 외에 기사 제목 앵커(`title_head`,
  제목 앞 12자 verbatim)를 echo하도록 지시한다.
- REQ-062-B2: WHEN idx 정렬 후 각 결과의 echo된 `title_head`가 매핑된 기사
  `article_ids[idx-1]`의 실제 제목 앞부분과 불일치하는 결과가 임계(기본 1건 초과)를
  넘으면, THE 시스템 SHALL 배치 전체를 fail-closed로 거부(0건 저장)하고
  `NEWS_INTEL_ALIGN_REJECT`에 `anchor_mismatch_count`를 기록한다.
- REQ-062-B3: `title_head` 누락(구버전 응답)은 존재할 때만 대조한다(하위호환). 배치 내
  과반이 앵커를 결여하면 관측 로그를 남긴다.
- REQ-062-B4: 앵커 대조는 순수 함수(DB/네트워크 없음)로 3개 저장 경로
  (`import_host_results`/`_store_results`/repair)에서 재사용 가능해야 한다.

## 실행 단계
- 1단계(본 SPEC, 오늘 밤): 그룹 A + 그룹 B — fail-safe 확보. 야간 배치가 스크램블돼도 거부.
- 2단계(후속 SPEC): 호스트 CLI 청킹(20개 단위) — throughput 복원. 프로토콜 변경이라 분리.

## 즉시 조치(완료)
- 7/8 claude-cli 분석 387행 무해화(impact 0·noise·keywords 비움), 감사 `NEWS_INTEL_NEUTRALIZE`.

## 인수 기준
- A: `avg_down` 단일 breach는 주문만 거부·halt 없음(테스트); `daily_loss`는 halt(테스트).
- B: 스크램블 배치(앵커 불일치)는 0건 저장+ALIGN_REJECT(테스트); 정상 배치는 전건 저장(테스트).
- 오프라인 테스트 스위트 회귀 0.

## Stage 2 — 호스트 CLI 청킹 (2026-07-09, throughput 복원)

### 배경 (관측된 사실)
2026-07-09 라이브 로그 감사: 그룹 A/B(fail-safe)가 배포된 뒤, 94~100개 기사
단일배치 5사이클이 전부 거부됨 — 배치 2건은 `anchor_mismatch_count` 100/100,
96/96(완전한 idx 순열이되 내용 전체 스크램블), 배치 3건은 idx 1~5개 누락.
결과: 하루 종일 분석 저장 0건(fail-closed는 정상 작동했으나 throughput 사망).
원인: `analyze_news.sh:62`가 최대 100개 기사를 **단일** `claude -p` 호출로
전송 — 배치가 클수록 모델이 idx-내용 대응을 놓친다. 해법: 배치를 작은
청크(20개 단위)로 나눠 모델이 정렬을 유지할 수 있는 크기로 호출한다.

### 요구사항 (EARS)
- REQ-062-C1: `export_pending_for_host` SHALL 실제(비noise) 기사를 최대
  `HOST_CHUNK_SIZE = 20`(모듈 상수, 시장 중립) 단위로 나누어 청크별로 하나의
  대기 파일을 `data/pending_chunks/`(예: `chunk_00.json` — `{chunk_id, prompt,
  article_ids, exported_at}`) 아래 쓴다. 각 프롬프트는 `build_analysis_prompt`
  로 그 청크의 기사만을 대상으로(로컬 `[1..n]` 라벨) 생성한다. 또한
  `data/pending_metadata.json` `{chunks: [{chunk_id, article_ids}], exported_at,
  count}`를 쓰며, 쓰기 전 `data/pending_chunks/`의 잔여 파일과 잔여 청크
  결과를 정리한다.
- REQ-062-C2: `scripts/analyze_news.sh` SHALL `data/pending_chunks/chunk_*.json`
  을 순서대로 순회하며, 각각에 대해 프롬프트를 추출해 `claude -p --tools ""`
  를 한 번 호출하고, `data/analysis_chunks/result_<동일 id>.json`에 쓰며,
  비어있지 않은 성공 시 그 청크의 대기 파일을 제거한다. 실패/빈 응답 청크는
  대기 파일을 유지(다음 슬롯 재시도)하며 루프는 나머지 청크로 계속
  진행한다. 청크별 로그 라인을 남긴다. 기존 CLI 경로 해소·flock·무유료API
  보장은 그대로 유지한다.
- REQ-062-C3: `import_host_results` SHALL 사용 가능한 각
  `data/analysis_chunks/result_*.json`을 메타데이터의 해당 청크
  article_ids에 대해 독립적으로 처리한다(파싱 -> 검증 -> idx정렬 ->
  content-anchor검증 -> 저장), fail-closed 거부는 그 청크에만 적용된다(다른
  청크는 계속 저장). 감사: 청크별 `NEWS_INTEL_ALIGN_REJECT`에 `chunk_id`를
  추가하고, 최종 집계 `NEWS_INTEL_IMPORT_OK`에 `{chunks_ok, chunks_rejected,
  articles_imported, articles_rejected}` 상세를 남긴다. 소비된 결과 파일은
  성공/거부 무관하게 처리 후 삭제한다.
- REQ-062-C4: 전환기 1사이클 하위호환 — 레거시 `data/analysis_results.json`
  + 구버전 메타데이터(최상위 article_ids 리스트)가 남아있으면 기존
  단일배치 경로로 1회 import한 뒤 청크 파일 처리로 진행한다. 신규 export는
  더 이상 레거시 단일 프롬프트 형식을 쓰지 않는다.
- REQ-062-C5: 모든 청킹 헬퍼는 순수/시장중립(하드코딩된 시장 값 없음)이며,
  기존 `_parse_analysis_response`/`_align_results_to_articles`/
  `_verify_content_anchor`를 청크별로 그대로 재사용한다.

### 인수 기준 (Stage 2)
- export: 45개 기사 -> 3개 청크(20/20/5), 청크별 로컬 `[1..n]` 라벨, 잔여
  청크/결과 파일 정리(테스트).
- import: 청크 하나 스크램블 -> 그 청크만 0건 거부(`chunk_id` 포함
  ALIGN_REJECT), 나머지 청크는 전건 저장, 집계 IMPORT_OK 정확(테스트).
- import: 청크 결과 파일 누락(호스트 미처리) -> 다른 청크는 정상 import,
  누락 청크는 메타데이터에 남아 재시도 대상(테스트).
- import: 레거시 형식 잔여 -> 기존 경로로 1회 흡수(테스트).
- `bash -n scripts/analyze_news.sh` 통과, 오프라인 테스트 스위트 회귀 0.
