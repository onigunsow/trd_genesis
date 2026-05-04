"""Shared helpers for context builders.

- atomic_write: write to .tmp then rename — no partial files visible to readers.
- contexts_dir: project_root/data/contexts/.
- guarded_build: wrap a builder; on exception preserve previous .md + system_error.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from trading.alerts.telegram import system_error
from trading.config import project_root
from trading.db.session import audit

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def contexts_dir() -> Path:
    d = project_root() / "data" / "contexts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write(path: Path, content: str) -> None:
    """Write to .tmp + rename. POSIX rename is atomic within the same filesystem."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def guarded_build(name: str, builder: Callable[[], str], target: Path) -> Path | None:
    """Run builder; on success atomic-write target. On failure keep previous file
    + telegram system_error + audit_log.

    Returns target path on success, None on failure.
    """
    try:
        content = builder()
        if not content or not content.strip():
            raise RuntimeError(f"{name} builder returned empty content")
        atomic_write(target, content)
        audit("CONTEXT_BUILD_OK", actor="cron", details={
            "name": name, "path": str(target), "bytes": len(content),
        })
        LOG.info("context built: %s (%d bytes)", target, len(content))
        return target
    except Exception as e:  # noqa: BLE001
        LOG.exception("context build failed: %s", name)
        try:
            system_error(f"context.{name}", e, context=str(target))
        except Exception:  # noqa: BLE001
            LOG.exception("system_error fallback failed (%s)", name)
        return None
