-- M1 migration — system_state table.
-- Implements REQ-MODE-01-8 (live_unlocked default false on first init).
--
-- This file is mounted to /docker-entrypoint-initdb.d in the postgres container,
-- so it runs automatically on the very first DB initialization (empty volume).

CREATE TABLE IF NOT EXISTS system_state (
    id              SMALLINT     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    live_unlocked   BOOLEAN      NOT NULL DEFAULT FALSE,
    halt_state      BOOLEAN      NOT NULL DEFAULT FALSE,
    silent_mode     BOOLEAN      NOT NULL DEFAULT FALSE,
    trading_mode    TEXT         NOT NULL DEFAULT 'paper'
                                  CHECK (trading_mode IN ('paper','live')),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by      TEXT         NOT NULL DEFAULT 'init'
);

INSERT INTO system_state (id, live_unlocked, halt_state, silent_mode, trading_mode, updated_by)
VALUES (1, FALSE, FALSE, FALSE, 'paper', 'init')
ON CONFLICT (id) DO NOTHING;

-- Generic audit log used across milestones. Detailed schema for orders/positions arrives in M2.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    event_type  TEXT         NOT NULL,
    actor       TEXT         NOT NULL,
    details     JSONB        NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_event_type_idx ON audit_log (event_type);

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SYSTEM_INIT', 'init', '{"milestone":"M1","note":"initial schema applied"}'::JSONB);

COMMENT ON TABLE system_state IS
    'Singleton system flags. Row id=1 only. Updated by /halt /resume / /verbose / live unlock.';
COMMENT ON COLUMN system_state.live_unlocked IS
    'REQ-FUTURE-08-2: must remain FALSE until manual unlock with audit_log entry.';
