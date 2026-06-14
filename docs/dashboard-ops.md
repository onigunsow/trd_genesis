# SPEC-TRADING-047 운영 절차서 — 대시보드 설치 가이드

## 이 문서의 목적

트레이딩 모니터링 대시보드를 **Tailscale VPN 전용**으로 안전하게 올리는 단계별 절차.
CLI 초심자 기준으로 작성했습니다. 공개 인터넷 노출 없이, 집 밖에서도 폰/PC로 볼 수 있게 됩니다.

---

## 1단계: DB 읽기 전용 역할 비밀번호 설정 (migration 032 적용 후)

migration 032 가 dashboard_ro 역할을 생성하지만 비밀번호는 임시값입니다.
운영 비밀번호로 바꿔야 합니다.

```bash
# 트레이딩 호스트에서 실행 (postgres 컨테이너 안의 psql)
docker exec -it trading-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "ALTER ROLE dashboard_ro PASSWORD '여기에_강력한_비밀번호_입력';"
```

---

## 2단계: .env 파일에 대시보드 변수 추가

`.env` 파일을 열어 아래 세 줄을 추가합니다.

```bash
# 텍스트 편집기로 .env 열기 (nano 사용 예시)
nano .env
```

파일 맨 아래에 추가:

```
# SPEC-TRADING-047 대시보드 서비스 변수
DASHBOARD_DB_USER=dashboard_ro
DASHBOARD_DB_PASSWORD=여기에_위에서_설정한_비밀번호
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=여기에_Grafana_관리자_비밀번호
```

저장: `Ctrl+O` → Enter → `Ctrl+X`

---

## 3단계: .env 파일 권한 강화 (AC-9 보안 요구사항)

```bash
chmod 600 .env
ls -l .env
# 출력 예: -rw------- 1 onigunsow onigunsow 543 Jun 14 10:00 .env
```

권한이 `-rw-------` 이어야 합니다. 다른 사람이 읽지 못하게 막는 것입니다.

---

## 4단계: migration 032 적용

```bash
docker exec trading-app trading migrate
```

완료 메시지에 `032_dashboard_readonly_role` 이 나오면 성공입니다.

---

## 5단계: Tailscale 설치 (호스트)

> Tailscale은 VPN 터널을 만들어 집 밖에서도 사설 IP로 접근하게 해줍니다.
> 공유기 포트포워딩 없이 안전하게 접근할 수 있습니다.

```bash
# Tailscale 설치 스크립트 (Ubuntu/Debian)
curl -fsSL https://tailscale.com/install.sh | sh

# Tailscale 시작 및 로그인 (브라우저가 열립니다)
sudo tailscale up
```

로그인 후 `tailscale ip -4` 명령으로 이 기기의 tailnet IP 확인:

```bash
tailscale ip -4
# 예: 100.64.x.y
```

이 IP를 메모해 두세요. 집 밖에서 접속할 때 사용합니다.

---

## 6단계: compose.yaml 포트 바인딩 확인

`compose.yaml` 의 `dashboard-api` 와 `grafana` 서비스의 `ports` 항목을 확인합니다.

기본값은 `127.0.0.1` (로컬호스트 전용) 입니다. 집 밖 접근을 위해 Tailscale IP 로 바꿉니다:

```yaml
# dashboard-api
ports:
  - "100.64.x.y:8080:8080"   # ← Tailscale IP 로 교체

# grafana
ports:
  - "100.64.x.y:3000:3000"   # ← Tailscale IP 로 교체
```

또는 LAN 내 접근만 필요하면 LAN IP (예: `192.168.1.x`) 를 사용해도 됩니다.

**절대 `0.0.0.0:포트:포트` 형식으로 쓰지 마세요.** 공개 인터넷에 노출됩니다.

---

## 7단계: 서비스 시작

```bash
# 새 서비스만 시작 (기존 app/bot/scheduler는 재시작 안 함)
docker compose up -d dashboard-api grafana
```

상태 확인:

```bash
docker compose ps
```

`dashboard-api` 와 `grafana` 가 `Up (healthy)` 이면 성공입니다.

---

## 8단계: 접속 확인

**같은 tailnet 에 연결된 기기에서:**

- 결정 피드 페이지: `http://100.64.x.y:8080`
- Grafana: `http://100.64.x.y:3000` (admin / 설정한 비밀번호)

**핸드폰에서 접속하려면:**

1. 핸드폰에 Tailscale 앱 설치 (App Store / Play Store)
2. 같은 계정으로 로그인
3. 위 주소로 접속

---

## 9단계: 외부 도달 불가 확인 (AC-8 검증)

Tailscale 끈 상태의 외부 기기에서 (또는 모바일 데이터로 Tailscale OFF):

```bash
curl -v --connect-timeout 5 http://공인IP:8080/health
# 예상: 연결 거부 또는 타임아웃
```

같은 tailnet 내에서는 정상 응답:

```bash
curl http://100.64.x.y:8080/health
# 예상: {"status":"ok"}
```

---

## 정기 보안 유지 절차

### .env 자격증명 주기적 교체 (권장: 3개월마다)

```bash
# 1. 새 비밀번호 생성
openssl rand -hex 32

# 2. DB 역할 비밀번호 변경
docker exec -it trading-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "ALTER ROLE dashboard_ro PASSWORD '새_비밀번호';"

# 3. .env 파일 업데이트
nano .env   # DASHBOARD_DB_PASSWORD, GRAFANA_ADMIN_PASSWORD 교체

# 4. .env 권한 재확인
chmod 600 .env

# 5. 서비스 재시작
docker compose restart dashboard-api grafana
```

### Grafana 업데이트

```bash
docker compose pull grafana
docker compose up -d grafana
```

---

## 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| Grafana 데이터 없음 | dashboard_ro 비밀번호 불일치 | 4단계 재확인, 서비스 재시작 |
| /api/status 503 | DB 연결 실패 | `docker compose logs dashboard-api` 확인 |
| Tailscale 밖에서 접속됨 | ports 가 0.0.0.0 | compose.yaml 6단계 재확인 |
| Grafana 로그인 불가 | GRAFANA_ADMIN_PASSWORD 미설정 | .env 확인 후 `docker compose restart grafana` |

---

## 주의사항

- 이 대시보드는 **읽기 전용**입니다. halt/resume 등 제어는 기존 Telegram/CLI 만 사용합니다.
- Grafana 익명 접근(`GF_AUTH_ANONYMOUS_ENABLED`)은 `false` 로 고정되어 있습니다.
- 보안 우선순위: **Tailscale VPN > 포트 바인딩 제한 > 읽기 전용 DB 역할**

---

*SPEC-TRADING-047 REQ-047-15, REQ-047-16 충족 문서.*
*작성일: 2026-06-14*
