"""Remember verified staff navigation without storing the Admin PIN.

The caller must authorize the role capability and actor before remembering.
Restoration only returns a candidate link: the caller must reauthorize its token
and actor before changing the app's query parameters. A small declared Streamlit
component returns browser data through the ordinary component API; sandboxed
iframes never navigate or inject code into the parent page.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import streamlit.components.v1 as components


STORAGE_KEY = "compcoach.staff-return.v1"
SCHEMA_VERSION = 1
MAX_TTL_DAYS = 30
STAFF_ROLES = frozenset({"admin", "coordinator", "coach"})
_ALLOWED_QUERY_KEYS = frozenset({"event", "token", "who", "archive"})
_FRONTEND = Path(__file__).parent / "frontend" / "remembered_access"
_component = components.declare_component("compcoach_staff_return", path=str(_FRONTEND))


def _json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _plain_value(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= 4096
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _parse_query(relative_url: Any, role: Any) -> dict[str, str] | None:
    if not isinstance(role, str) or role not in STAFF_ROLES:
        return None
    if not isinstance(relative_url, str) or not relative_url.startswith("?"):
        return None
    if len(relative_url) > 16384 or any(ord(char) < 32 or ord(char) == 127 for char in relative_url):
        return None
    try:
        parsed = urlsplit(relative_url)
        if parsed.scheme or parsed.netloc or parsed.path or parsed.fragment:
            return None
        values = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None
    if set(values) - _ALLOWED_QUERY_KEYS or any(len(items) != 1 for items in values.values()):
        return None
    query = {key: items[0] for key, items in values.items()}
    if any(not _plain_value(query.get(key)) for key in ("event", "token", "who")):
        return None
    if "archive" in query and (query["archive"] != "1" or role != "admin"):
        return None
    return query


def _verified_query(event: Mapping[str, Any], role: str, actor: str, relative_url: str) -> str | None:
    query = _parse_query(relative_url, role)
    if query is None or not _plain_value(actor):
        return None
    if (
        query["event"] != str(event.get("id") or "")
        or query["token"] != str(event.get(f"{role}_token") or "")
        or query["who"] != actor
    ):
        return None
    return "?" + urlencode(query)


def _config(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "action": action, "storageKey": STORAGE_KEY, "schema": SCHEMA_VERSION,
        "maxTtlMs": MAX_TTL_DAYS * 24 * 60 * 60 * 1000, **(payload or {}),
    }


def _access_html(action: str, payload: dict[str, Any] | None = None) -> str:
    """Run the exact declared-component frontend with a test render argument."""

    return (_FRONTEND / "index.html").read_text().replace(
        "const testConfig = null;", "const testConfig = " + _json_for_script(_config(action, payload)) + ";", 1,
    )


def _restored_context(value: Any) -> dict[str, str] | None:
    """Validate browser data without granting the role claimed by the browser."""

    if not isinstance(value, dict) or value.get("action") != "restore":
        return None
    record = value.get("record")
    if not isinstance(record, dict) or record.get("schema") != SCHEMA_VERSION:
        return None
    expiry = record.get("expiresAt")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        return None
    remaining = expiry - time.time() * 1000
    if not 0 < remaining <= MAX_TTL_DAYS * 24 * 60 * 60 * 1000:
        return None
    query = _parse_query(record.get("url"), record.get("role"))
    if query is None or query["event"] != record.get("event") or query["who"] != record.get("actor"):
        return None
    return {**query, "role": record["role"], "relative_url": "?" + urlencode(query)}


def remember_staff_access(
    event: Mapping[str, Any], role: str, actor: str, *, relative_url: str,
    is_training: bool = False, ttl_days: int = MAX_TTL_DAYS,
) -> bool:
    """Remember after server capability authorization and actor validation.

    The URL is query-only and must match meet ID, role token and selected actor.
    Pass ``is_training=True`` for hubs and personal courses. Returns whether the
    component was emitted, not whether this browser permits local storage.
    Remember/clear actions do not emit component values or trigger rerun loops.
    """

    if is_training or not isinstance(event, Mapping):
        return False
    relative = _verified_query(event, role, actor, relative_url)
    if relative is None or isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days <= 0:
        return False
    _component(config=_config("remember", {
        "event": str(event["id"]), "role": role, "actor": actor, "url": relative,
        "ttlMs": min(ttl_days, MAX_TTL_DAYS) * 24 * 60 * 60 * 1000,
    }), default=None, key="compcoach_staff_return_remember")
    return True


def restore_remembered_access(*, force_home: bool = False) -> dict[str, str] | None:
    """Return a remembered candidate, then let the caller reauthorize it.

    Result keys: ``event``, ``token``, ``who``, ``role``, ``relative_url`` (and
    optional admin ``archive``). Do not trust the claimed role: authorize the
    token against the database, compare its role, and verify allowed actors.
    Never grant permissions directly from this browser-controlled record.
    ``force_home=True`` and explicit ``?home=1`` preserve deliberate Home access.
    The browser component also skips incoming event/token links.
    """

    if force_home:
        return None
    result = _component(config=_config("restore"), default=None, key="compcoach_staff_return_restore")
    return _restored_context(result)


def clear_remembered_access() -> None:
    """Forget this device; render normally and skip remembering on the same run.

    Do not immediately interrupt the component render with ``st.rerun``. The
    current server authorization is unchanged; this only removes browser data.
    """

    _component(config=_config("clear"), default=None, key="compcoach_staff_return_clear")
