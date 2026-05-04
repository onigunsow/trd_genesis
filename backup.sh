#!/usr/bin/env bash
# Snapshot Postgres + .env + compose.yaml + src/ into backups/<timestamp>/
# Retention: keeps the most recent $BACKUP_KEEP snapshots (default 30).
# Pattern matches ~/n8n/backup.sh.
set -euo pipefail

cd "$(dirname "$0")"

set -a
# shellcheck disable=SC1091
source .env
set +a

TS="$(date +%Y%m%d-%H%M%S)"
OUT="backups/${TS}"
mkdir -p "$OUT"

echo "[1/4] Dumping Postgres ($POSTGRES_DB)..."
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | gzip > "$OUT/postgres.sql.gz"

echo "[2/4] Copying config + SPEC + project docs..."
# Secrets and infra config
cp .env "$OUT/.env"
cp .env.example "$OUT/.env.example" 2>/dev/null || true
cp .gitignore "$OUT/.gitignore" 2>/dev/null || true
cp compose.yaml "$OUT/compose.yaml"
cp Dockerfile "$OUT/Dockerfile"
cp pyproject.toml "$OUT/pyproject.toml"
[ -f uv.lock ] && cp uv.lock "$OUT/uv.lock"
cp README.md "$OUT/README.md" 2>/dev/null || true
cp backup.sh "$OUT/backup.sh"

# SPEC + project context (essential for rebuild from scratch)
if [ -d .moai ]; then
  tar czf "$OUT/moai.tar.gz" .moai 2>/dev/null \
    || echo "  (moai/ archive skipped)"
fi

echo "[3/5] Verifying backup integrity (REQ-OPS-05-21)..."
# 1) postgres dump must be non-empty + parsable schema
DUMP="$OUT/postgres.sql.gz"
if [ ! -s "$DUMP" ]; then
  echo "  FAIL postgres.sql.gz is empty or missing" >&2
  exit 2
fi
# Schema-only restore validation: gunzip into a transient stream and look for table headers.
TABLE_COUNT=$(gunzip -c "$DUMP" | grep -cE "^CREATE TABLE")
EXPECTED_TABLES=14   # M1~M5 + 정밀화 누적 (system_state, audit_log, orders, positions, ohlcv, macro_indicators, disclosures, fundamentals, flows, persona_runs, persona_decisions, risk_reviews, daily_reports, schema_migrations, retrospectives, portfolio_adjustments, benchmark_runs)
if [ "$TABLE_COUNT" -lt 10 ]; then
  echo "  FAIL CREATE TABLE statements found: $TABLE_COUNT (expected ≥10)" >&2
  exit 2
fi
# 2) .env must exist with perm 600
if [ ! -f "$OUT/.env" ]; then
  echo "  FAIL .env missing in backup" >&2
  exit 2
fi
ENV_PERM=$(stat -c "%a" "$OUT/.env" 2>/dev/null)
if [ "$ENV_PERM" != "600" ]; then
  echo "  WARN .env permission $ENV_PERM (expected 600); restoring..."
  chmod 600 "$OUT/.env"
fi
# 3) moai.tar.gz must list ≥3 files (.moai/specs/*, project/*)
if [ -f "$OUT/moai.tar.gz" ]; then
  MOAI_FILES=$(tar tzf "$OUT/moai.tar.gz" 2>/dev/null | wc -l)
  if [ "$MOAI_FILES" -lt 3 ]; then
    echo "  WARN moai.tar.gz contains only $MOAI_FILES entries"
  fi
fi
echo "  OK postgres tables=${TABLE_COUNT}, .env perm=${ENV_PERM}, moai entries=${MOAI_FILES:-0}"

echo "[4/5] Pruning old backups (keep ${BACKUP_KEEP:-30})..."
KEEP=${BACKUP_KEEP:-30}
# shellcheck disable=SC2012
ls -1dt backups/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -rf

echo "[5/5] Done."
ls -la "$OUT"
echo ""
echo "Backup size: $(du -sh "$OUT" | cut -f1)"
