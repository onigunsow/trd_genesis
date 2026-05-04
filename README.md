# trading — Korean Stock 5-Persona AI Trading System

박세훈 개인 자본 1,000만원 모의 자동 매매 시스템. M1~M5 (모의 한정). M6 실거래는 별도 SPEC.

자세한 SPEC: `.moai/specs/SPEC-TRADING-001/spec.md`
프로젝트 컨텍스트: `.moai/project/{product,structure,tech}.md`

## 빠른 시작

```bash
# 0. 시크릿 준비 (이미 박세훈 님이 .env 작성·검증 완료)
ls -la .env  # perm 600 확인

# 1. 빌드 + 기동
docker compose up -d --build

# 2. 헬스체크
docker compose exec app python -m trading.healthcheck

# 3. 백업 1회
./backup.sh
```

## 주요 명령어

| 명령 | 용도 |
|---|---|
| `docker compose ps` | 컨테이너 상태 |
| `docker compose logs -f app` | app 로그 |
| `docker compose logs -f postgres` | postgres 로그 |
| `docker compose exec app python -m trading.healthcheck` | env / KIS / Telegram / DB 점검 |
| `docker compose exec app trading <subcommand>` | CLI (M2~M5에서 추가) |
| `docker compose exec postgres psql -U trading -d trading` | DB 직접 접근 |
| `./backup.sh` | 백업 (Postgres + .env + compose) |
| `BACKUP_KEEP=7 ./backup.sh` | retention 7개로 일시 변경 |
| `docker compose down` | 중지 (데이터 보존) |
| `docker compose down -v` | 완전 제거 (볼륨까지) — 위험 |

## 보안 원칙

- `.env` perm 600, git 절대 X (`.gitignore` 박혀 있음)
- `TRADING_MODE=paper` 가 기본. `live` 는 `live_unlocked=true` (DB) 없이는 차단됨
- 컨테이너 user 1000:1000 (비-root)
- Postgres 호스트 포트 미노출, 컨테이너 네트워크 내부에서만
- KIS 호출 IP 제한은 M6 진입 전 KIS Developers 포털에서 설정

## 트러블슈팅

**`docker compose up -d` 가 빌드 단계에서 실패**
→ `pyproject.toml` 문법, uv lockfile 동기화 문제일 가능성. 컨테이너 안에서 `uv sync` 직접 실행 시 에러 메시지 확인.

**healthcheck 실패**
→ `.env` 시크릿 누락 또는 권한 문제. `python -m trading.healthcheck` 로 어떤 단계에서 실패하는지 확인.

**Postgres healthy 인데 app 컨테이너가 unhealthy**
→ `docker compose logs app` 로 import 오류 확인. 보통 의존성 누락.

## 마일스톤 진행 상태

- [x] M1 — 인프라 (이 README가 작성된 시점)
- [ ] M2 — KIS API + DB 스키마 v1
- [ ] M3 — 데이터 어댑터 + 룰 기반 백테스트
- [ ] M4 — 5-페르소나 + 텔레그램 시계열 브리핑 + 모의 자동 매매
- [ ] M5 — 위험관리 + 관측성 + 3주 모의 운영
- [ ] M6 — 실거래 (본 SPEC 범위 외, 별도 SPEC 필요)
