"""Nightly delta event cleanup job.

REQ-DELTA-01-9: Delete merged events older than 7 days at 03:00 KST.
Integrates with APScheduler in the existing scheduler module.
"""

from __future__ import annotations

import logging

from trading.db.session import audit
from trading.jit.events import cleanup_old_deltas

LOG = logging.getLogger(__name__)


def run_nightly_cleanup() -> None:
    """Execute nightly delta cleanup. Called by APScheduler at 03:00 KST.

    REQ-DELTA-01-9: Events older than 7 days AND merged=true are deleted.
    Un-merged events are never deleted regardless of age.
    """
    LOG.info("Starting nightly delta cleanup")
    try:
        deleted = cleanup_old_deltas()
        audit(
            "DELTA_CLEANUP_COMPLETED",
            actor="jit_cleanup",
            details={"deleted_count": deleted},
        )
        LOG.info("Nightly cleanup completed: %d events deleted", deleted)
    except Exception:
        LOG.exception("Nightly delta cleanup failed")
        audit(
            "DELTA_CLEANUP_FAILED",
            actor="jit_cleanup",
            details={},
        )
