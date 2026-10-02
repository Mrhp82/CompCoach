"""Lightweight, app-wide refresh and autonomous-practice decisions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _moment(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def training_visible_revision(metadata: dict | None) -> str:
    """Ignore observation bookkeeping when nothing on the course changed."""
    if not metadata:
        return ""
    fields = (
        "meet_id", "kind", "status", "expired", "expires_at", "stage_index",
        "stage_count", "stage_title", "instruction", "guide_instruction", "hint", "progress",
        "scenario_message", "last_feedback", "completed_runs", "last_completed_at",
    )
    visible = {key: metadata.get(key) for key in fields}
    if metadata.get("is_hub"):
        # Admin's progress overview is visible data. Individual coach telemetry
        # must not redraw the ordinary learner page on every observation.
        visible["participants"] = metadata.get("participants", [])
    encoded = json.dumps(visible, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def training_hub_status(db: Any, metadata: dict | None) -> str | None:
    """Check access termination without loading every learner's course."""
    if not metadata:
        return None
    if metadata.get("is_hub"):
        return str(metadata.get("status") or "")
    hub_id = metadata.get("hub_meet_id")
    if not hub_id:
        return None
    with db._connection() as conn:
        row = conn.execute(
            "SELECT status FROM training_sessions WHERE meet_id = ?", (hub_id,)
        ).fetchone()
    return str(row["status"]) if row else "stopped"


def training_needs_tick(
    metadata: dict | None,
    actor: str = "",
    view: str = "",
    now: datetime | None = None,
    *,
    board_changed: bool = False,
    hub_status: str | None = None,
) -> bool:
    """Run the writer only for an action, new observation or due scenario."""
    if not metadata or metadata.get("status") in {"completed", "stopped"}:
        return False
    now = now or datetime.now(timezone.utc)
    if not now.tzinfo:
        now = now.replace(tzinfo=timezone.utc)
    state = metadata.get("state") or {}
    expires = _moment(metadata.get("expires_at") or state.get("expires_at"))
    if metadata.get("expired") or (expires and expires <= now):
        return True
    if metadata.get("is_hub"):
        return False
    if hub_status is not None and hub_status not in {"running", "paused"}:
        return True
    if board_changed:
        return True
    if metadata.get("status") == "running" and hub_status != "paused":
        for event in state.get("pending_events", []):
            due = _moment(event.get("due_at"))
            if due is not None and due <= now:
                return True
    learner = metadata.get("learner") or state.get("learner")
    if actor and actor == learner and view in {"My Group", "Live", "Activity"}:
        person = state.get("participants", {}).get(actor, {})
        stage = int(metadata.get("stage_index", metadata.get("stage", 0)))
        return (
            stage not in person.get("stages_seen", [])
            or view not in person.get("stage_views", {}).get(str(stage), [])
        )
    return False
