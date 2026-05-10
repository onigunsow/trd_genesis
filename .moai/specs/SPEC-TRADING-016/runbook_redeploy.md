# Runbook — `make redeploy`: 컨테이너 안전하게 다시 띄우기

> 대상: 박세훈 (CLI 초보자) · 마지막 업데이트: 2026-05-10

## 0. 왜 이게 필요한가? (한 문단)

지금까지 두 번의 zero-trade 사고는 **컨테이너가 옛 코드로 돌고 있었기 때문**에 발생했어. `git pull` 로 소스코드는 최신이 됐어도 도커 이미지 안의 `__pycache__` 와 Jinja 캐시가 살아남아서, 우리가 고친 픽스가 컨테이너 안에서는 반영이 안 됐던 거야. `make redeploy` 는 이걸 한 줄로 해결해 — **이미지를 처음부터 새로 빌드 + 컨테이너 강제 재생성 + 빌드 커밋 검증**까지 묶음 처리. 앞으로 코드 변경 후 배포는 무조건 이 명령 하나만 쓰면 돼.

---

## 1. 배포 전 체크리스트 (30초)

### 1.1 위치 확인

터미널에서 지금 어디에 있는지 먼저 확인해.

```bash
pwd
```

기대 출력:
```
/home/onigunsow/trading
```

만약 다른 곳이면 이동:
```bash
cd /home/onigunsow/trading
```

### 1.2 git 상태 확인

저장 안 된 코드 변경이 남아있지 않은지 본다.

```bash
git status
```

기대 출력 (둘 중 하나):
- `nothing to commit, working tree clean` — 깨끗함, 진행해도 OK.
- 변경 파일 목록이 보임 — 그러면 먼저 commit 하거나 stash 하고 와.

### 1.3 어느 커밋이 빌드될지 확인

```bash
git rev-parse HEAD
```

예시 출력:
```
2172cdf3a1b2c4d5e6f7g8h9i0j1k2l3m4n5o6p7
```

이 SHA 가 컨테이너 안 `/app/.build_commit` 에 박힌다. 컨테이너 부팅 시 healthcheck 가 이 값과 호스트의 `HOST_BUILD_COMMIT` 환경변수를 비교해서 **불일치 시 즉시 Telegram 알림 + 종료**.

---

## 2. 한 줄 배포

```bash
make redeploy
```

이 한 줄이 내부에서 하는 일:

1. `git rev-parse HEAD` 로 현재 커밋 SHA 추출.
2. `docker compose build --no-cache --build-arg BUILD_COMMIT=<sha> app` — 캐시 없이 깨끗하게 다시 빌드.
3. `docker compose up -d --force-recreate scheduler bot app` — 세 컨테이너 강제 재생성. (`postgres` 는 그대로.)
4. `docker compose ps` 로 상태 출력.

소요 시간: 약 2~4분 (네트워크/CPU 따라).

### 진행 중 무엇을 보게 될까

- `[+] Building ...` → 도커가 새 이미지 빌드 중. uv sync 두 번 돌아감.
- `[+] Running 3/3 ✔ Container trading-app Started ...` → 컨테이너 재기동 완료.
- 마지막에 `make ps` 와 동일한 표가 출력됨 — 모든 행이 `Up (healthy)` 또는 `Up (health: starting)` 이어야 정상.

---

## 3. 정상 부팅 확인

### 3.1 로그 보기

```bash
make logs
```

(이 명령은 Ctrl+C 로 직접 종료하기 전까지 계속 출력 따라감.)

찾아볼 핵심 라인:

| 라인 | 의미 |
|------|------|
| `[OK ] env        env loaded, mode=paper` | .env 정상 로드 |
| `[OK ] kis        KIS paper reachable (404)` | KIS 페이퍼 서버 도달 |
| `[OK ] telegram   telegram bot @your_bot` | 텔레그램 봇 연결 |
| `[OK ] db         postgres reachable` | DB 연결 |
| `[OK ] build      build commit verified: 2172cdf3` | **빌드 커밋 일치 — 핵심** |
| `scheduler started` 또는 `Scheduler entering main loop` | scheduler 정상 부팅 |

이 6줄이 다 보이면 성공.

### 3.2 Ctrl+C 로 로그 빠져나오기

```
Ctrl+C
```

(컨테이너는 백그라운드에서 계속 도는 중. `make logs` 끄는 것뿐.)

### 3.3 컨테이너 상태 표 확인

```bash
make ps
```

기대 결과: 4개 컨테이너 (postgres, app, bot, scheduler) 전부 `Up (healthy)`.

---

## 4. 문제 발생 시 (트러블슈팅)

### 4.1 Telegram 으로 `[시스템 에러 · BOOT · ...]` 알림이 옴

#### 메시지: `BUILD MISMATCH: container=abc12345 host=def67890`

원인: `HOST_BUILD_COMMIT` 환경변수가 컨테이너에 들어간 빌드 SHA 와 다름. 이건 `make redeploy` 가 아니라 그냥 `docker compose up -d` 만 했을 때 발생.

해결:
```bash
make redeploy
```
(다시 한 번 처음부터 깨끗하게 돌려.)

#### 메시지: `HOST_BUILD_COMMIT not set; container commit=abc12345` (warn)

원인: 정보성 경고. 환경변수가 안 박힌 채로 `docker compose up` 실행됨. 컨테이너는 그래도 부팅됨.

해결: 운영 환경에서는 항상 `make redeploy` 사용.

### 4.2 컨테이너가 `Restarting` 상태로 계속 재시작

```bash
docker compose logs scheduler --tail 50
```

로 마지막 50줄을 보고 어디서 깨졌는지 확인. 흔한 원인:
- `.env` 누락 → `.env.example` 보고 다시 만들기.
- DB 마이그레이션 실패 → `docker compose logs postgres --tail 50` 로 확인.

### 4.3 `make redeploy` 자체가 실패함 (예: 디스크 부족)

```bash
docker system prune -a   # 안 쓰는 이미지/컨테이너 정리. 주의: 빌드 캐시 다 날아감.
make redeploy
```

---

## 5. 일상 운영 명령

| 명령 | 용도 |
|------|------|
| `make redeploy` | 코드 변경 후 배포 — **유일한 배포 진입점** |
| `make logs` | scheduler 로그 따라가기 (Ctrl+C 종료) |
| `make ps` | 모든 컨테이너 상태 한번에 보기 |
| `make stop` | scheduler/bot/app 멈추기 (postgres 는 유지) |
| `make help` | 위 명령 요약 보기 |

---

## 6. tmux 활용 팁 — 학습 기회

배포 + 모니터링을 한 화면에서 동시에 보고 싶을 때.

### 6.1 새 tmux 세션 시작

```bash
tmux new -s trading
```

이제 너는 `trading` 이라는 이름의 세션 안에 있어. 터미널을 닫아도 세션은 살아있어.

### 6.2 화면 좌우 분할

키보드: `Ctrl+a` 누르고 떼고, 그 다음 `|` (파이프, Shift+\\).

(만약 `Ctrl+a` 가 안 먹으면 `Ctrl+b` 로 시도. tmux 기본 prefix 는 `Ctrl+b`. `~/.tmux.conf` 에서 설정 가능.)

이제 화면이 좌우 두 패인으로 나뉨.

### 6.3 좌우 이동

`Ctrl+a` (또는 `Ctrl+b`) → `←` 또는 `→`.

### 6.4 추천 배치

- **왼쪽 패인**: `cd /home/onigunsow/trading && make redeploy` — 배포 명령.
- **오른쪽 패인**: `cd /home/onigunsow/trading && make logs` — 실시간 로그 모니터.

이러면 왼쪽에서 `make redeploy` 끝나는 동시에 오른쪽에서 부팅 로그가 뜨는 걸 동시에 볼 수 있어.

### 6.5 세션 떠나기 (detach) — 컨테이너는 계속 돔

`Ctrl+a` (또는 `Ctrl+b`) → `d`.

터미널이 일반 셸로 돌아옴. tmux 세션은 백그라운드에 살아있음.

### 6.6 다시 들어가기 (attach)

```bash
tmux attach -t trading
```

다시 두 패인 그대로 보임.

### 6.7 세션 완전히 끄기

각 패인에서 `exit` 입력 또는 `Ctrl+a x` (kill pane).

---

## 7. 한 줄 요약 (외워둘 것)

> 코드를 바꿨으면 무조건 `make redeploy`. 절대 `docker compose up -d` 만 쓰지 말 것. 배포 후엔 `make logs` 로 `[OK ] build  build commit verified` 한 줄 확인.

---

## 부록 A. 완전 처음부터 (cold start) — 시스템 재부팅 직후

```bash
cd /home/onigunsow/trading
docker compose up -d postgres   # DB 먼저 띄움
sleep 10                         # postgres healthy 될 때까지 잠시 대기
make redeploy                    # 나머지 다 띄움
```

## 부록 B. 검증 쿼리 (배포가 진짜 반영됐는지 확인)

```bash
docker compose exec app cat /app/.build_commit
```

출력값이 `git rev-parse HEAD` 와 같으면 OK.
