"""Small helpers for assignment receipts, separate from physical bout coverage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def assignment_is_actionable(athlete: Mapping[str, Any]) -> bool:
    """An absent, eliminated or completed Pools athlete needs no new receipt."""

    return bool(
        athlete.get("active_state", "active") == "active"
        and athlete.get("participation_status", "active") == "active"
        and not (
            athlete.get("phase") == "pools"
            and athlete.get("pool_result_at")
        )
    )


def assignment_notice_details(notice: Mapping[str, Any]) -> str:
    """Keep the urgent location distinct from the DE pod used for planning."""

    parts = [str(notice.get("event_name") or "")]
    phase = notice.get("phase")
    if phase == "de":
        parts.append(f"Pod {notice.get('pod') or 'TBD'}")
        parts.append(f"Actual strip {notice.get('live_location') or 'TBD'}")
    else:
        parts.append(f"Pool {notice.get('pool_no') or 'TBD'}")
        parts.append(f"Strip {notice.get('source_strip') or 'TBD'}")
        if notice.get("time_text"):
            parts.append(f"Time {notice['time_text']}")
    return " · ".join(part for part in parts if part)
