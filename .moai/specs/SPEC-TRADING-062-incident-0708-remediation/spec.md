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
