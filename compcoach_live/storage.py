"""Shared persistence for CompCoach Live.

The first deployable version uses SQLite in WAL mode.  Every Streamlit session
on the same deployment reads and writes the same database file, and the live
claim operation is protected by an immediate transaction so two coaches cannot
cover the same athlete at the same time.

The module deliberately exposes dictionaries rather than ORM objects.  This
keeps the UI small and leaves room for a future Supabase/PostgreSQL adapter
without changing the rest of the application.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

NO_CHANGE = object()


class CompCoachError(RuntimeError):
    """Base error shown by the UI as an operational message."""


class EventLockedError(CompCoachError):
    """Raised when someone tries to change a closed competition."""


class ConcurrentUpdateError(CompCoachError):
    """Raised when a newer action makes an undo unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


EVENT_MUTABLE_FIELDS = {
    "name",
    "timezone",
    "status",
    "active_coaches_json",
    "coordinators_json",
    "source_url",
}

ATHLETE_MUTABLE_FIELDS = {
    "name",
    "phase",
    "pool_no",
    "pod",
    "source_strip",
    "time_text",
    "main_coach",
    "side_coach",
    "assignment_override",
    "active_state",
    "call_status",
    "live_location",
    "reported_at",
    "reported_by",
    "covered_by",
    "covered_at",
    "pool_wins",
    "pool_losses",
    "pool_result_at",
    "pool_result_by",
    "de_wins",
    "last_de_result",
    "last_de_result_at",
    "last_de_result_by",
    "help_requested_by",
    "help_requested_at",
    "help_location",
    "help_acknowledged_by",
    "help_acknowledged_at",
}


class CompCoachDB:
    def __init__(self, path: str | Path = "data/compcoach.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(
            self.path,
            timeout=12,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 12000")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'locked')),
                    active_coaches_json TEXT NOT NULL DEFAULT '[]',
                    coordinators_json TEXT NOT NULL DEFAULT '[]',
                    admin_token TEXT NOT NULL UNIQUE,
                    coordinator_token TEXT NOT NULL UNIQUE,
                    coach_token TEXT NOT NULL UNIQUE,
                    source_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'locked')),
                    active_coaches_json TEXT NOT NULL DEFAULT '[]',
                    coordinators_json TEXT NOT NULL DEFAULT '[]',
                    admin_token TEXT NOT NULL UNIQUE,
                    coordinator_token TEXT NOT NULL UNIQUE,
                    coach_token TEXT NOT NULL UNIQUE,
                    ended_at TEXT,
                    ended_by TEXT NOT NULL DEFAULT '',
                    prepared_from_meet_id TEXT REFERENCES meets(id) ON DELETE SET NULL,
                    competition_date TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meet_events (
                    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
                    sort_order INTEGER NOT NULL CHECK (sort_order BETWEEN 0 AND 3),
                    PRIMARY KEY(meet_id, event_id),
                    UNIQUE(meet_id, sort_order)
                );

                CREATE TABLE IF NOT EXISTS athletes (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    athlete_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'pools'
                        CHECK (phase IN ('pools', 'de')),
                    pool_no TEXT NOT NULL DEFAULT '',
                    pod TEXT NOT NULL DEFAULT '',
                    source_strip TEXT NOT NULL DEFAULT '',
                    time_text TEXT NOT NULL DEFAULT '',
                    main_coach TEXT NOT NULL DEFAULT '',
                    side_coach TEXT NOT NULL DEFAULT '',
                    assignment_override INTEGER NOT NULL DEFAULT 0,
                    active_state TEXT NOT NULL DEFAULT 'active'
                        CHECK (active_state IN ('active', 'eliminated')),
                    call_status TEXT NOT NULL DEFAULT 'waiting'
                        CHECK (call_status IN ('waiting', 'in_hole', 'on_deck', 'now')),
                    live_location TEXT NOT NULL DEFAULT '',
                    reported_at TEXT,
                    reported_by TEXT NOT NULL DEFAULT '',
                    covered_by TEXT NOT NULL DEFAULT '',
                    covered_at TEXT,
                    pool_wins INTEGER,
                    pool_losses INTEGER,
                    pool_result_at TEXT,
                    pool_result_by TEXT NOT NULL DEFAULT '',
                    de_wins INTEGER NOT NULL DEFAULT 0,
                    last_de_result TEXT NOT NULL DEFAULT '',
                    last_de_result_at TEXT,
                    last_de_result_by TEXT NOT NULL DEFAULT '',
                    help_requested_by TEXT NOT NULL DEFAULT '',
                    help_requested_at TEXT,
                    help_location TEXT NOT NULL DEFAULT '',
                    help_acknowledged_by TEXT NOT NULL DEFAULT '',
                    help_acknowledged_at TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(event_id, athlete_key)
                );

                CREATE TABLE IF NOT EXISTS pod_assignments (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    phase TEXT NOT NULL CHECK (phase IN ('pools', 'de')),
                    pod TEXT NOT NULL,
                    main_coach TEXT NOT NULL DEFAULT '',
                    side_coach TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, phase, pod)
                );

                CREATE TABLE IF NOT EXISTS phase_states (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    phase TEXT NOT NULL CHECK (phase IN ('pools', 'de')),
                    started INTEGER NOT NULL DEFAULT 0 CHECK (started IN (0, 1)),
                    changed_at TEXT,
                    changed_by TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(event_id, phase)
                );

                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    athlete_id TEXT REFERENCES athletes(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    previous_json TEXT,
                    new_json TEXT,
                    version_after INTEGER,
                    created_at TEXT NOT NULL,
                    undone_at TEXT,
                    undone_by TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_athletes_event
                    ON athletes(event_id, active_state, phase, pod);
                CREATE INDEX IF NOT EXISTS idx_actions_event
                    ON actions(event_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_meet_events_order
                    ON meet_events(meet_id, sort_order);
                """
            )
            # In-place migration for databases created by an earlier build.
            # SQLite does not support ADD COLUMN IF NOT EXISTS.
            athlete_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(athletes)").fetchall()
            }
            migrations = {
                "assignment_override": "INTEGER NOT NULL DEFAULT 0",
                "pool_wins": "INTEGER",
                "pool_losses": "INTEGER",
                "pool_result_at": "TEXT",
                "pool_result_by": "TEXT NOT NULL DEFAULT ''",
                "de_wins": "INTEGER NOT NULL DEFAULT 0",
                "last_de_result": "TEXT NOT NULL DEFAULT ''",
                "last_de_result_at": "TEXT",
                "last_de_result_by": "TEXT NOT NULL DEFAULT ''",
                "help_requested_by": "TEXT NOT NULL DEFAULT ''",
                "help_requested_at": "TEXT",
                "help_location": "TEXT NOT NULL DEFAULT ''",
                "help_acknowledged_by": "TEXT NOT NULL DEFAULT ''",
                "help_acknowledged_at": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in athlete_columns:
                    conn.execute(f"ALTER TABLE athletes ADD COLUMN {column} {definition}")
            phase_state_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(phase_states)").fetchall()
            }
            if "version" not in phase_state_columns:
                conn.execute(
                    "ALTER TABLE phase_states ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
                )
            meet_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(meets)").fetchall()
            }
            meet_migrations = {
                "ended_at": "TEXT",
                "ended_by": "TEXT NOT NULL DEFAULT ''",
                "prepared_from_meet_id": (
                    "TEXT REFERENCES meets(id) ON DELETE SET NULL"
                ),
                "competition_date": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in meet_migrations.items():
                if column not in meet_columns:
                    conn.execute(f"ALTER TABLE meets ADD COLUMN {column} {definition}")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_meets_prepared_from
                ON meets(prepared_from_meet_id)
                WHERE prepared_from_meet_id IS NOT NULL
                """
            )

            # Safe additive migration: every legacy competition becomes a
            # one-event meet. No athlete, assignment, phase or action row is
            # rewritten, so old links and IDs remain valid.
            conn.execute("BEGIN IMMEDIATE")
            try:
                legacy_events = conn.execute(
                    """
                    SELECT events.* FROM events
                    LEFT JOIN meet_events ON meet_events.event_id = events.id
                    WHERE meet_events.event_id IS NULL
                    ORDER BY events.created_at, events.id
                    """
                ).fetchall()
                for event in legacy_events:
                    meet_id = uuid4().hex
                    conn.execute(
                        """
                        INSERT INTO meets (
                            id, name, timezone, status, active_coaches_json,
                            coordinators_json, admin_token, coordinator_token,
                            coach_token, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meet_id,
                            event["name"],
                            event["timezone"],
                            event["status"],
                            event["active_coaches_json"],
                            event["coordinators_json"],
                            event["admin_token"],
                            event["coordinator_token"],
                            event["coach_token"],
                            event["created_at"],
                            event["updated_at"],
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO meet_events (meet_id, event_id, sort_order)
                        VALUES (?, ?, 0)
                        """,
                        (meet_id, event["id"]),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _event_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["active_coaches"] = _load(data.pop("active_coaches_json"), [])
        data["coordinators"] = _load(data.pop("coordinators_json"), [])
        return data

    @staticmethod
    def _meet_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["active_coaches"] = _load(data.pop("active_coaches_json"), [])
        data["coordinators"] = _load(data.pop("coordinators_json"), [])
        return data

    @staticmethod
    def _athlete_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _assert_open(self, conn: sqlite3.Connection, event_id: str) -> None:
        row = conn.execute(
            """
            SELECT events.status AS event_status, meets.status AS meet_status
            FROM events
            LEFT JOIN meet_events ON meet_events.event_id = events.id
            LEFT JOIN meets ON meets.id = meet_events.meet_id
            WHERE events.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise CompCoachError("Competition not found.")
        if row["event_status"] == "locked" or row["meet_status"] == "locked":
            raise EventLockedError("This competition is locked and is now read-only.")

    def _assert_meet_open(self, conn: sqlite3.Connection, meet_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
        if row is None:
            raise CompCoachError("Competition group not found.")
        if row["status"] == "locked":
            raise EventLockedError("This competition group is locked and is now read-only.")
        return row

    def _log_action(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: str,
        athlete_id: str | None,
        action: str,
        actor: str,
        previous: dict[str, Any] | None,
        new: dict[str, Any] | None,
        version_after: int | None,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO actions (
                event_id, athlete_id, action, actor, previous_json, new_json,
                version_after, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                athlete_id,
                action,
                actor or "Unknown",
                _dump(previous) if previous is not None else None,
                _dump(new) if new is not None else None,
                version_after,
                utc_now(),
            ),
        )
        return int(cursor.lastrowid)

    def create_event(
        self,
        name: str,
        active_coaches: Iterable[str],
        coordinators: Iterable[str] = ("Irina",),
        timezone_name: str = "America/Los_Angeles",
    ) -> dict[str, Any]:
        event_id = uuid4().hex
        now = utc_now()
        coaches = [str(x).strip() for x in active_coaches if str(x).strip()]
        coords = [str(x).strip() for x in coordinators if str(x).strip()]
        admin_token = _new_token()
        coordinator_token = _new_token()
        coach_token = _new_token()
        meet_id = uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO events (
                    id, name, timezone, active_coaches_json, coordinators_json,
                    admin_token, coordinator_token, coach_token, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    name.strip() or "AFM Competition",
                    timezone_name,
                    _dump(coaches),
                    _dump(coords),
                    admin_token,
                    coordinator_token,
                    coach_token,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO meets (
                    id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, created_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meet_id,
                    name.strip() or "AFM Competition",
                    timezone_name,
                    _dump(coaches),
                    _dump(coords),
                    admin_token,
                    coordinator_token,
                    coach_token,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO meet_events (meet_id, event_id, sort_order) VALUES (?, ?, 0)",
                (meet_id, event_id),
            )
            conn.commit()
        return self.get_event(event_id)  # type: ignore[return-value]

    def list_events(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT events.*, meet_events.meet_id, meet_events.sort_order
                FROM events
                LEFT JOIN meet_events ON meet_events.event_id = events.id
                ORDER BY events.created_at DESC
                """
            ).fetchall()
        return [self._event_dict(row) for row in rows if row is not None]  # type: ignore[misc]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT events.*, meet_events.meet_id, meet_events.sort_order
                FROM events
                LEFT JOIN meet_events ON meet_events.event_id = events.id
                WHERE events.id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._event_dict(row)

    def create_meet(
        self,
        name: str,
        active_coaches: Iterable[str],
        coordinators: Iterable[str] = ("Irina",),
        timezone_name: str = "America/Los_Angeles",
        *,
        first_event_name: str | None = "Main Event",
        competition_date: str = "",
    ) -> dict[str, Any]:
        """Create a shared competition group, optionally without a first event.

        Omitting ``first_event_name`` retains the legacy ``"Main Event"``
        default. Passing ``None`` explicitly creates an empty competition day;
        a blank string still falls back to ``"Main Event"``.
        """
        meet_id = uuid4().hex
        now = utc_now()
        meet_name = name.strip() or "AFM Competition"
        child_name = (
            None
            if first_event_name is None
            else str(first_event_name).strip() or "Main Event"
        )
        clean_date = str(competition_date or "").strip()
        if clean_date:
            try:
                date.fromisoformat(clean_date)
            except ValueError as exc:
                raise ValueError("Competition date must use YYYY-MM-DD.") from exc
        coaches = [str(x).strip() for x in active_coaches if str(x).strip()]
        coords = [str(x).strip() for x in coordinators if str(x).strip()]
        meet_tokens = (_new_token(), _new_token(), _new_token())
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO meets (
                    id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, competition_date, created_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meet_id,
                    meet_name,
                    timezone_name,
                    _dump(coaches),
                    _dump(coords),
                    *meet_tokens,
                    clean_date,
                    now,
                    now,
                ),
            )
            if child_name is not None:
                event_id = uuid4().hex
                child_tokens = (_new_token(), _new_token(), _new_token())
                conn.execute(
                    """
                    INSERT INTO events (
                        id, name, timezone, status, active_coaches_json,
                        coordinators_json, admin_token, coordinator_token,
                        coach_token, source_url, created_at, updated_at
                    ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        event_id,
                        child_name,
                        timezone_name,
                        _dump(coaches),
                        _dump(coords),
                        *child_tokens,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO meet_events (meet_id, event_id, sort_order)
                    VALUES (?, ?, 0)
                    """,
                    (meet_id, event_id),
                )
            conn.commit()
        return self.get_meet(meet_id)  # type: ignore[return-value]

    def list_meets(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events
                        WHERE meet_events.meet_id = meets.id) AS event_count
                FROM meets
                ORDER BY meets.created_at DESC, meets.id DESC
                """
            ).fetchall()
        return [self._meet_dict(row) for row in rows if row is not None]  # type: ignore[misc]

    def get_meet(self, meet_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events
                        WHERE meet_events.meet_id = meets.id) AS event_count
                FROM meets WHERE meets.id = ?
                """,
                (meet_id,),
            ).fetchone()
        return self._meet_dict(row)

    def get_prepared_successor(self, source_meet_id: str) -> dict[str, Any] | None:
        """Return the direct next-day meet prepared from ``source_meet_id``."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events
                        WHERE meet_events.meet_id = meets.id) AS event_count
                FROM meets
                WHERE meets.prepared_from_meet_id = ?
                LIMIT 1
                """,
                (source_meet_id,),
            ).fetchone()
        return self._meet_dict(row)

    def get_latest_prepared_successor(
        self, source_meet_id: str
    ) -> dict[str, Any] | None:
        """Return the furthest prepared successor, safely following its chain.

        ``None`` means the source has no prepared successor. A visited-ID guard
        makes the traversal terminate even if a database is externally damaged
        into containing a cycle.
        """
        visited = {source_meet_id}
        latest: dict[str, Any] | None = None
        current_id = source_meet_id
        with self._connection() as conn:
            while True:
                row = conn.execute(
                    """
                    SELECT meets.*,
                           (SELECT COUNT(*) FROM meet_events
                            WHERE meet_events.meet_id = meets.id) AS event_count
                    FROM meets
                    WHERE meets.prepared_from_meet_id = ?
                    LIMIT 1
                    """,
                    (current_id,),
                ).fetchone()
                if row is None or str(row["id"]) in visited:
                    break
                latest = self._meet_dict(row)
                current_id = str(row["id"])
                visited.add(current_id)
        return latest

    def get_meet_for_event(self, event_id: str) -> dict[str, Any] | None:
        """Resolve a legacy child-event URL to its shared meet."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events AS counted
                        WHERE counted.meet_id = meets.id) AS event_count
                FROM meets
                JOIN meet_events ON meet_events.meet_id = meets.id
                WHERE meet_events.event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._meet_dict(row)

    def authorize_meet(self, meet_id: str, token: str) -> str | None:
        """Authorize a meet token or any legacy token belonging to a child."""
        if not token:
            return None
        with self._connection() as conn:
            meet = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
            if meet is None:
                return None
            children = conn.execute(
                """
                SELECT events.admin_token, events.coordinator_token, events.coach_token
                FROM events
                JOIN meet_events ON meet_events.event_id = events.id
                WHERE meet_events.meet_id = ?
                """,
                (meet_id,),
            ).fetchall()
        for role in ("admin", "coordinator", "coach"):
            stored = str(meet[f"{role}_token"] or "")
            if stored and hmac.compare_digest(stored, token):
                return role
            for child in children:
                stored = str(child[f"{role}_token"] or "")
                if stored and hmac.compare_digest(stored, token):
                    return role
        return None

    def list_meet_events(self, meet_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if not conn.execute("SELECT 1 FROM meets WHERE id = ?", (meet_id,)).fetchone():
                raise CompCoachError("Competition group not found.")
            rows = conn.execute(
                """
                SELECT events.*, meet_events.meet_id, meet_events.sort_order,
                       (SELECT COUNT(*) FROM athletes
                        WHERE athletes.event_id = events.id) AS athlete_count
                FROM meet_events
                JOIN events ON events.id = meet_events.event_id
                WHERE meet_events.meet_id = ?
                ORDER BY meet_events.sort_order, events.created_at, events.id
                """,
                (meet_id,),
            ).fetchall()
        return [self._event_dict(row) for row in rows if row is not None]  # type: ignore[misc]

    def add_meet_event(
        self,
        meet_id: str,
        name: str,
        *,
        source_url: str = "",
    ) -> dict[str, Any]:
        """Add an isolated child event, serialized so four is a hard maximum."""
        event_id = uuid4().hex
        now = utc_now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = self._assert_meet_open(conn, meet_id)
            membership = conn.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(MAX(sort_order), -1) AS max_order
                FROM meet_events WHERE meet_id = ?
                """,
                (meet_id,),
            ).fetchone()
            if int(membership["count"]) >= 4:
                raise CompCoachError("A competition group can contain at most 4 events.")
            sort_order = int(membership["max_order"]) + 1
            clean_name = name.strip() or f"Event {sort_order + 1}"
            sibling_names = conn.execute(
                """
                SELECT events.name FROM events
                JOIN meet_events ON meet_events.event_id = events.id
                WHERE meet_events.meet_id = ?
                """,
                (meet_id,),
            ).fetchall()
            if any(
                str(row["name"]).strip().casefold() == clean_name.casefold()
                for row in sibling_names
            ):
                raise CompCoachError("Event names must be unique within a competition day.")
            conn.execute(
                """
                INSERT INTO events (
                    id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, source_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    clean_name,
                    meet["timezone"],
                    meet["status"],
                    meet["active_coaches_json"],
                    meet["coordinators_json"],
                    _new_token(),
                    _new_token(),
                    _new_token(),
                    source_url.strip(),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO meet_events (meet_id, event_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (meet_id, event_id, sort_order),
            )
            conn.execute("UPDATE meets SET updated_at = ? WHERE id = ?", (now, meet_id))
            conn.commit()
        return self.get_event(event_id)  # type: ignore[return-value]

    def rename_meet_event(self, meet_id: str, event_id: str, name: str) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Event name is required.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_meet_open(conn, meet_id)
            if not conn.execute(
                "SELECT 1 FROM meet_events WHERE meet_id = ? AND event_id = ?",
                (meet_id, event_id),
            ).fetchone():
                raise CompCoachError("Event does not belong to this competition group.")
            sibling_names = conn.execute(
                """
                SELECT events.name FROM events
                JOIN meet_events ON meet_events.event_id = events.id
                WHERE meet_events.meet_id = ? AND events.id != ?
                """,
                (meet_id, event_id),
            ).fetchall()
            if any(
                str(row["name"]).strip().casefold() == clean_name.casefold()
                for row in sibling_names
            ):
                raise CompCoachError("Event names must be unique within a competition day.")
            now = utc_now()
            conn.execute(
                "UPDATE events SET name = ?, updated_at = ? WHERE id = ?",
                (clean_name, now, event_id),
            )
            conn.execute("UPDATE meets SET updated_at = ? WHERE id = ?", (now, meet_id))
            conn.commit()
        return self.get_event(event_id)  # type: ignore[return-value]

    def reorder_meet_events(self, meet_id: str, event_ids: Iterable[str]) -> None:
        ordered = list(dict.fromkeys(str(value) for value in event_ids))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_meet_open(conn, meet_id)
            current = [
                str(row["event_id"])
                for row in conn.execute(
                    """
                    SELECT event_id FROM meet_events
                    WHERE meet_id = ? ORDER BY sort_order
                    """,
                    (meet_id,),
                ).fetchall()
            ]
            if len(ordered) != len(current) or set(ordered) != set(current):
                raise ValueError("Event order must contain every event exactly once.")
            # Reinsert the tiny membership list atomically; this avoids
            # transient collisions in UNIQUE(meet_id, sort_order), including
            # when all four legal positions are occupied.
            conn.execute("DELETE FROM meet_events WHERE meet_id = ?", (meet_id,))
            for index, event_id in enumerate(ordered):
                conn.execute(
                    """
                    INSERT INTO meet_events (meet_id, event_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (meet_id, event_id, index),
                )
            conn.execute("UPDATE meets SET updated_at = ? WHERE id = ?", (utc_now(), meet_id))
            conn.commit()

    def delete_meet_event(self, meet_id: str, event_id: str) -> None:
        """Delete a child with no athletes, including the meet's last child.

        Athlete rows are the sole meaningful-data guard. Other event-scoped
        state is intentionally removed by the event's cascading delete.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_meet_open(conn, meet_id)
            membership = conn.execute(
                "SELECT sort_order FROM meet_events WHERE meet_id = ? AND event_id = ?",
                (meet_id, event_id),
            ).fetchone()
            if membership is None:
                raise CompCoachError("Event does not belong to this competition group.")
            has_athletes = conn.execute(
                "SELECT 1 FROM athletes WHERE event_id = ? LIMIT 1", (event_id,)
            ).fetchone()
            if has_athletes:
                raise CompCoachError("Only an empty event can be deleted.")
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            remaining = conn.execute(
                """
                SELECT event_id FROM meet_events
                WHERE meet_id = ? ORDER BY sort_order
                """,
                (meet_id,),
            ).fetchall()
            conn.execute("DELETE FROM meet_events WHERE meet_id = ?", (meet_id,))
            for index, row in enumerate(remaining):
                conn.execute(
                    """
                    INSERT INTO meet_events (meet_id, event_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (meet_id, row["event_id"], index),
                )
            conn.execute("UPDATE meets SET updated_at = ? WHERE id = ?", (utc_now(), meet_id))
            conn.commit()

    def update_meet_staff(
        self,
        meet_id: str,
        *,
        name: str,
        active_coaches: Iterable[str],
        coordinators: Iterable[str],
        timezone_name: str,
    ) -> dict[str, Any]:
        coaches = [str(x).strip() for x in active_coaches if str(x).strip()]
        coords = [str(x).strip() for x in coordinators if str(x).strip()]
        now = utc_now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = self._assert_meet_open(conn, meet_id)
            meet_name = name.strip() or "AFM Competition"
            conn.execute(
                """
                UPDATE meets SET name = ?, active_coaches_json = ?,
                    coordinators_json = ?, timezone = ?, updated_at = ?
                WHERE id = ?
                """,
                (meet_name, _dump(coaches), _dump(coords), timezone_name, now, meet_id),
            )
            conn.execute(
                """
                UPDATE events SET active_coaches_json = ?, coordinators_json = ?,
                    timezone = ?, status = ?, updated_at = ?
                WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)
                """,
                (
                    _dump(coaches),
                    _dump(coords),
                    timezone_name,
                    meet["status"],
                    now,
                    meet_id,
                ),
            )
            conn.commit()
        return self.get_meet(meet_id)  # type: ignore[return-value]

    def set_meet_locked(self, meet_id: str, locked: bool) -> None:
        status = "locked" if locked else "open"
        now = utc_now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = conn.execute(
                "SELECT ended_at FROM meets WHERE id = ?", (meet_id,)
            ).fetchone()
            if meet is None:
                raise CompCoachError("Competition group not found.")
            if not locked and meet["ended_at"]:
                raise CompCoachError("An ended competition day cannot be reopened.")
            conn.execute(
                "UPDATE meets SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, meet_id),
            )
            conn.execute(
                """
                UPDATE events SET status = ?, updated_at = ?
                WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)
                """,
                (status, now, meet_id),
            )
            conn.commit()

    def finish_meet(self, meet_id: str, actor: str) -> dict[str, Any]:
        """End a competition day and make every child event read-only."""
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("Actor is required.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
            if meet is None:
                raise CompCoachError("Competition group not found.")
            if meet["ended_at"]:
                # Idempotent retry: retain the original audit metadata while
                # repairing a status drift, if one ever occurred.
                conn.execute("UPDATE meets SET status = 'locked' WHERE id = ?", (meet_id,))
                conn.execute(
                    """
                    UPDATE events SET status = 'locked'
                    WHERE id IN (
                        SELECT event_id FROM meet_events WHERE meet_id = ?
                    )
                    """,
                    (meet_id,),
                )
                conn.commit()
                return self.get_meet(meet_id)  # type: ignore[return-value]

            now = utc_now()
            conn.execute(
                """
                UPDATE meets SET status = 'locked', ended_at = ?, ended_by = ?,
                    updated_at = ? WHERE id = ?
                """,
                (now, clean_actor, now, meet_id),
            )
            conn.execute(
                """
                UPDATE events SET status = 'locked', updated_at = ?
                WHERE id IN (
                    SELECT event_id FROM meet_events WHERE meet_id = ?
                )
                """,
                (now, meet_id),
            )
            conn.commit()
        return self.get_meet(meet_id)  # type: ignore[return-value]

    def prepare_next_day(
        self,
        source_meet_id: str,
        next_name: str,
        competition_date: str,
        event_names: Iterable[str],
        actor: str,
    ) -> dict[str, Any]:
        """Close one day and atomically create one clean successor meet."""
        clean_name = str(next_name or "").strip()
        clean_actor = str(actor or "").strip()
        clean_date = str(competition_date or "").strip()
        if not clean_name:
            raise ValueError("Next-day competition name is required.")
        if not clean_actor:
            raise ValueError("Actor is required.")
        try:
            date.fromisoformat(clean_date)
        except ValueError as exc:
            raise ValueError("Competition date must use YYYY-MM-DD.") from exc
        raw_names = [event_names] if isinstance(event_names, str) else list(event_names)
        names = [str(value or "").strip() for value in raw_names]
        if len(names) > 4:
            raise ValueError("Prepare next day accepts at most 4 events.")
        if any(not name for name in names):
            raise ValueError("Event names cannot be empty.")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Event names must be unique.")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                "SELECT * FROM meets WHERE id = ?", (source_meet_id,)
            ).fetchone()
            if source is None:
                raise CompCoachError("Competition group not found.")

            existing = conn.execute(
                "SELECT id FROM meets WHERE prepared_from_meet_id = ?",
                (source_meet_id,),
            ).fetchone()
            now = utc_now()
            if source["ended_at"]:
                conn.execute(
                    "UPDATE meets SET status = 'locked' WHERE id = ?",
                    (source_meet_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE meets SET status = 'locked', ended_at = ?, ended_by = ?,
                        updated_at = ? WHERE id = ?
                    """,
                    (now, clean_actor, now, source_meet_id),
                )
            conn.execute(
                """
                UPDATE events SET status = 'locked', updated_at = ?
                WHERE id IN (
                    SELECT event_id FROM meet_events WHERE meet_id = ?
                )
                """,
                (now, source_meet_id),
            )
            if existing:
                conn.commit()
                return self.get_meet(existing["id"])  # type: ignore[return-value]

            next_meet_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO meets (
                    id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, ended_at, ended_by, prepared_from_meet_id,
                    competition_date, created_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, NULL, '', ?, ?, ?, ?)
                """,
                (
                    next_meet_id,
                    clean_name,
                    source["timezone"],
                    source["active_coaches_json"],
                    source["coordinators_json"],
                    _new_token(),
                    _new_token(),
                    _new_token(),
                    source_meet_id,
                    clean_date,
                    now,
                    now,
                ),
            )
            for sort_order, event_name in enumerate(names):
                event_id = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO events (
                        id, name, timezone, status, active_coaches_json,
                        coordinators_json, admin_token, coordinator_token,
                        coach_token, source_url, created_at, updated_at
                    ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        event_id,
                        event_name,
                        source["timezone"],
                        source["active_coaches_json"],
                        source["coordinators_json"],
                        _new_token(),
                        _new_token(),
                        _new_token(),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO meet_events (meet_id, event_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (next_meet_id, event_id, sort_order),
                )
            conn.commit()
        return self.get_meet(next_meet_id)  # type: ignore[return-value]

    def rotate_meet_token(self, meet_id: str, role: str) -> str:
        if role not in {"admin", "coordinator", "coach"}:
            raise ValueError("Unknown role.")
        token = _new_token()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_meet_open(conn, meet_id)
            now = utc_now()
            conn.execute(
                f"UPDATE meets SET {role}_token = ?, updated_at = ? WHERE id = ?",
                (token, now, meet_id),
            )
            children = conn.execute(
                "SELECT event_id FROM meet_events WHERE meet_id = ?", (meet_id,)
            ).fetchall()
            for child in children:
                # Child tokens remain unique for old per-event links, but all
                # previous links for this role are invalidated together.
                conn.execute(
                    f"UPDATE events SET {role}_token = ?, updated_at = ? WHERE id = ?",
                    (_new_token(), now, child["event_id"]),
                )
            conn.commit()
        return token

    def authorize(self, event_id: str, token: str) -> str | None:
        event = self.get_event(event_id)
        if not event or not token:
            return None
        for role in ("admin", "coordinator", "coach"):
            stored = str(event.get(f"{role}_token") or "")
            if stored and hmac.compare_digest(stored, token):
                return role
        return None

    def update_event_staff(
        self,
        event_id: str,
        *,
        name: str,
        active_coaches: Iterable[str],
        coordinators: Iterable[str],
        timezone_name: str,
    ) -> dict[str, Any]:
        now = utc_now()
        coaches = [str(x).strip() for x in active_coaches if str(x).strip()]
        coords = [str(x).strip() for x in coordinators if str(x).strip()]
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            membership = conn.execute(
                "SELECT meet_id FROM meet_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            conn.execute(
                """
                UPDATE events
                SET name = ?, active_coaches_json = ?, coordinators_json = ?,
                    timezone = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name.strip() or "AFM Competition",
                    _dump(coaches),
                    _dump(coords),
                    timezone_name,
                    now,
                    event_id,
                ),
            )
            if membership:
                meet_id = membership["meet_id"]
                member_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM meet_events WHERE meet_id = ?",
                    (meet_id,),
                ).fetchone()["count"]
                conn.execute(
                    """
                    UPDATE meets SET active_coaches_json = ?, coordinators_json = ?,
                        timezone = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        _dump(coaches),
                        _dump(coords),
                        timezone_name,
                        now,
                        meet_id,
                    ),
                )
                if int(member_count) == 1:
                    conn.execute(
                        "UPDATE meets SET name = ? WHERE id = ?",
                        (name.strip() or "AFM Competition", meet_id),
                    )
                conn.execute(
                    """
                    UPDATE events SET active_coaches_json = ?, coordinators_json = ?,
                        timezone = ?, updated_at = ?
                    WHERE id IN (
                        SELECT event_id FROM meet_events WHERE meet_id = ?
                    )
                    """,
                    (
                        _dump(coaches),
                        _dump(coords),
                        timezone_name,
                        now,
                        meet_id,
                    ),
                )
            conn.commit()
        return self.get_event(event_id)  # type: ignore[return-value]

    def update_event_source_url(self, event_id: str, url: str) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            conn.execute(
                "UPDATE events SET source_url = ?, updated_at = ? WHERE id = ?",
                (url.strip(), utc_now(), event_id),
            )
            conn.commit()

    def set_event_locked(self, event_id: str, locked: bool) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
                raise CompCoachError("Competition not found.")
            status = "locked" if locked else "open"
            now = utc_now()
            membership = conn.execute(
                """
                SELECT meet_events.meet_id, meets.ended_at
                FROM meet_events
                JOIN meets ON meets.id = meet_events.meet_id
                WHERE meet_events.event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if membership:
                meet_id = membership["meet_id"]
                if not locked and membership["ended_at"]:
                    raise CompCoachError("An ended competition day cannot be reopened.")
                conn.execute(
                    "UPDATE meets SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, meet_id),
                )
                conn.execute(
                    """
                    UPDATE events SET status = ?, updated_at = ?
                    WHERE id IN (
                        SELECT event_id FROM meet_events WHERE meet_id = ?
                    )
                    """,
                    (status, now, meet_id),
                )
            else:
                conn.execute(
                    "UPDATE events SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, event_id),
                )
            conn.commit()

    def get_phase_states(self, event_id: str) -> dict[str, dict[str, Any]]:
        """Return both phase signals, defaulting missing rows to not started."""
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM events WHERE id = ?", (event_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            rows = conn.execute(
                """
                SELECT phase, started, changed_at, changed_by, version
                FROM phase_states
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchall()

        states = {
            phase: {
                "phase": phase,
                "started": False,
                "changed_at": None,
                "changed_by": "",
                "version": 0,
            }
            for phase in ("pools", "de")
        }
        for row in rows:
            phase = str(row["phase"])
            states[phase] = {
                "phase": phase,
                "started": bool(row["started"]),
                "changed_at": row["changed_at"],
                "changed_by": row["changed_by"],
                "version": int(row["version"]),
            }
        return states

    def set_phase_started(
        self,
        event_id: str,
        phase: str,
        started: bool,
        actor: str,
        *,
        expected_started: bool | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Set a phase's shared start signal with an optional stale-state guard."""
        phase = str(phase).strip().lower()
        actor = str(actor or "").strip()
        if phase not in {"pools", "de"}:
            raise ValueError("Phase must be pools or de.")
        if not isinstance(started, bool):
            raise TypeError("Started must be true or false.")
        if expected_started is not None and not isinstance(expected_started, bool):
            raise TypeError("Expected started must be true, false, or omitted.")
        if expected_version is not None and (
            isinstance(expected_version, bool) or not isinstance(expected_version, int)
        ):
            raise TypeError("Expected version must be a whole number or omitted.")
        if expected_version is not None and expected_version < 0:
            raise ValueError("Expected version cannot be negative.")
        if not actor:
            raise ValueError("Actor is required.")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                """
                SELECT phase, started, changed_at, changed_by, version
                FROM phase_states
                WHERE event_id = ? AND phase = ?
                """,
                (event_id, phase),
            ).fetchone()
            previous = (
                {
                    "phase": phase,
                    "started": bool(row["started"]),
                    "changed_at": row["changed_at"],
                    "changed_by": row["changed_by"],
                    "version": int(row["version"]),
                }
                if row is not None
                else {
                    "phase": phase,
                    "started": False,
                    "changed_at": None,
                    "changed_by": "",
                    "version": 0,
                }
            )
            if (
                expected_version is not None
                and previous["version"] != expected_version
            ):
                raise ConcurrentUpdateError(
                    "This phase changed on another phone. The board has been refreshed; "
                    "please try again."
                )
            if (
                expected_started is not None
                and previous["started"] is not expected_started
            ):
                raise ConcurrentUpdateError(
                    "This phase changed on another phone. The board has been refreshed; "
                    "please try again."
                )
            if previous["started"] is started:
                conn.commit()
                return previous

            now = utc_now()
            new_version = int(previous["version"]) + 1
            conn.execute(
                """
                INSERT INTO phase_states (
                    event_id, phase, started, changed_at, changed_by, version
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, phase) DO UPDATE SET
                    started = excluded.started,
                    changed_at = excluded.changed_at,
                    changed_by = excluded.changed_by,
                    version = excluded.version
                """,
                (event_id, phase, int(started), now, actor, new_version),
            )
            new = {
                "phase": phase,
                "started": started,
                "changed_at": now,
                "changed_by": actor,
                "version": new_version,
            }
            self._log_action(
                conn,
                event_id=event_id,
                athlete_id=None,
                action="phase_started" if started else "phase_reset",
                actor=actor,
                previous=previous,
                new=new,
                version_after=new_version,
            )
            conn.execute(
                "UPDATE events SET updated_at = ? WHERE id = ?", (now, event_id)
            )
            conn.commit()
        return new

    def rotate_token(self, event_id: str, role: str) -> str:
        if role not in {"admin", "coordinator", "coach"}:
            raise ValueError("Unknown role.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            membership = conn.execute(
                "SELECT meet_id FROM meet_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            now = utc_now()
            if membership:
                meet_id = membership["meet_id"]
                conn.execute(
                    f"UPDATE meets SET {role}_token = ?, updated_at = ? WHERE id = ?",
                    (_new_token(), now, meet_id),
                )
                children = conn.execute(
                    "SELECT event_id FROM meet_events WHERE meet_id = ?", (meet_id,)
                ).fetchall()
                token = ""
                for child in children:
                    child_token = _new_token()
                    conn.execute(
                        f"UPDATE events SET {role}_token = ?, updated_at = ? WHERE id = ?",
                        (child_token, now, child["event_id"]),
                    )
                    if child["event_id"] == event_id:
                        token = child_token
            else:
                token = _new_token()
                cursor = conn.execute(
                    f"UPDATE events SET {role}_token = ?, updated_at = ? WHERE id = ?",
                    (token, now, event_id),
                )
                if cursor.rowcount != 1:
                    raise CompCoachError("Competition not found.")
            conn.commit()
        return token

    def list_athletes(self, event_id: str, phase: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM athletes WHERE event_id = ?"
        params: list[Any] = [event_id]
        if phase in {"pools", "de"}:
            sql += " AND phase = ?"
            params.append(phase)
        sql += " ORDER BY active_state, pod, source_strip, pool_no, name"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_athlete(self, event_id: str, athlete_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
        return self._athlete_dict(row)

    @staticmethod
    def _de_advancing_keys(
        records: Iterable[dict[str, Any]],
        *,
        allow_empty: bool = False,
    ) -> set[str]:
        """Return the valid athlete keys from a DE-only import.

        Missing pool athletes can be inferred only from a complete direct-
        elimination list.  Rejecting mixed-phase input here keeps that
        inference opt-in and unambiguous even if a future caller bypasses the
        current import preview UI.
        """

        keys: set[str] = set()
        for record in records:
            athlete_key = str(record.get("athlete_id") or "").strip()
            name = str(record.get("name") or "").strip()
            if not athlete_key or not name:
                continue
            if str(record.get("phase") or "pools").strip().lower() != "de":
                raise ValueError(
                    "A complete Direct Elimination confirmation can contain only DE rows."
                )
            keys.add(athlete_key)
        if not keys and not allow_empty:
            raise ValueError(
                "A complete Direct Elimination confirmation requires at least one valid DE row."
            )
        return keys

    @staticmethod
    def _de_non_advancer_rows(
        conn: sqlite3.Connection,
        event_id: str,
        advancing_keys: set[str],
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM athletes
            WHERE event_id = ? AND phase = 'pools' AND active_state = 'active'
            ORDER BY name COLLATE NOCASE, id
            """,
            (event_id,),
        ).fetchall()
        return [dict(row) for row in rows if row["athlete_key"] not in advancing_keys]

    def preview_de_non_advancers(
        self,
        event_id: str,
        records: Iterable[dict[str, Any]],
        *,
        allow_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """List active Pools athletes absent from a proposed complete DE list.

        This is a read-only preview for the explicit whole-list confirmation
        shown by the UI.  ``merge_import`` recalculates and validates the same
        set inside its write transaction before changing anything.
        """

        advancing_keys = self._de_advancing_keys(
            list(records), allow_empty=allow_empty
        )
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM events WHERE id = ?", (event_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            return self._de_non_advancer_rows(conn, event_id, advancing_keys)

    def merge_import(
        self,
        event_id: str,
        records: Iterable[dict[str, Any]],
        actor: str,
        *,
        confirmed_non_advancers: Mapping[str, int] | None = None,
    ) -> dict[str, int]:
        stats = {"added": 0, "updated": 0, "unchanged": 0, "moved_out": 0}
        record_list = list(records)
        if not record_list and confirmed_non_advancers is None:
            return stats

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            non_advancers: list[dict[str, Any]] = []
            if confirmed_non_advancers is not None:
                advancing_keys = self._de_advancing_keys(
                    record_list, allow_empty=True
                )
                non_advancers = self._de_non_advancer_rows(
                    conn, event_id, advancing_keys
                )
                current_snapshot = {
                    str(athlete["id"]): int(athlete["version"])
                    for athlete in non_advancers
                }
                try:
                    confirmed_snapshot = {
                        str(athlete_id): int(version)
                        for athlete_id, version in confirmed_non_advancers.items()
                    }
                except (AttributeError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "The Direct Elimination confirmation is invalid."
                    ) from exc
                if confirmed_snapshot != current_snapshot:
                    raise ConcurrentUpdateError(
                        "The Pools athlete list changed on another phone. "
                        "Review the complete Direct Elimination list and confirm it again."
                    )
            now = utc_now()
            for record in record_list:
                athlete_key = str(record.get("athlete_id") or "").strip()
                name = str(record.get("name") or "").strip()
                phase = str(record.get("phase") or "pools")
                if not athlete_key or not name or phase not in {"pools", "de"}:
                    continue
                current_row = conn.execute(
                    "SELECT * FROM athletes WHERE event_id = ? AND athlete_key = ?",
                    (event_id, athlete_key),
                ).fetchone()
                current = dict(current_row) if current_row else None

                incoming_strip = str(record.get("strip") or "").strip()
                incoming_pod = str(record.get("pod") or "").strip().upper()
                incoming_pool = str(record.get("pool") or "").strip()
                incoming_time = str(record.get("time") or "").strip()

                assignment = None
                if incoming_pod:
                    assignment = conn.execute(
                        """
                        SELECT main_coach, side_coach FROM pod_assignments
                        WHERE event_id = ? AND phase = ? AND pod = ?
                        """,
                        (event_id, phase, incoming_pod),
                    ).fetchone()

                if current is None:
                    athlete_id = uuid4().hex
                    main_coach = assignment["main_coach"] if assignment else ""
                    side_coach = assignment["side_coach"] if assignment else ""
                    conn.execute(
                        """
                        INSERT INTO athletes (
                            id, event_id, athlete_key, name, phase, pool_no, pod,
                            source_strip, time_text, main_coach, side_coach,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            athlete_id,
                            event_id,
                            athlete_key,
                            name,
                            phase,
                            incoming_pool,
                            incoming_pod,
                            incoming_strip,
                            incoming_time,
                            main_coach,
                            side_coach,
                            now,
                            now,
                        ),
                    )
                    new = dict(
                        conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
                    )
                    self._log_action(
                        conn,
                        event_id=event_id,
                        athlete_id=athlete_id,
                        action="import_added",
                        actor=actor,
                        previous=None,
                        new=new,
                        version_after=0,
                    )
                    stats["added"] += 1
                    continue

                phase_changed = current["phase"] != phase
                changes = {
                    "name": name,
                    "phase": phase,
                    "pool_no": incoming_pool or current["pool_no"],
                    "pod": incoming_pod or current["pod"],
                    "source_strip": incoming_strip or current["source_strip"],
                    "time_text": incoming_time or current["time_text"],
                }
                if phase_changed:
                    changes.update(
                        {
                            # Blank values in a new phase are meaningful: do
                            # not leak a pool number/strip into the DE board.
                            "pool_no": incoming_pool,
                            "pod": incoming_pod,
                            "source_strip": incoming_strip,
                            "time_text": incoming_time,
                            "main_coach": "",
                            "side_coach": "",
                            "assignment_override": 0,
                            "active_state": "active",
                            "call_status": "waiting",
                            "live_location": "",
                            "reported_at": None,
                            "reported_by": "",
                            "covered_by": "",
                            "covered_at": None,
                            "de_wins": 0,
                            "last_de_result": "",
                            "last_de_result_at": None,
                            "last_de_result_by": "",
                            "help_requested_by": "",
                            "help_requested_at": None,
                            "help_location": "",
                            "help_acknowledged_by": "",
                            "help_acknowledged_at": None,
                        }
                    )
                    if phase == "pools":
                        changes.update(
                            {
                                "pool_wins": None,
                                "pool_losses": None,
                                "pool_result_at": None,
                                "pool_result_by": "",
                            }
                        )
                pod_changed = bool(incoming_pod and incoming_pod != current["pod"])
                assignment_is_managed = phase_changed or not current.get(
                    "assignment_override", 0
                )
                if assignment_is_managed:
                    if assignment:
                        changes["main_coach"] = assignment["main_coach"]
                        changes["side_coach"] = assignment["side_coach"]
                    elif phase_changed or pod_changed:
                        # Do not retain coaches inherited from the previous pod.
                        changes["main_coach"] = ""
                        changes["side_coach"] = ""

                changed = any(current.get(key) != value for key, value in changes.items())
                if not changed:
                    stats["unchanged"] += 1
                    continue
                new_version = int(current["version"]) + 1
                set_clause = ", ".join(f"{key} = ?" for key in changes)
                conn.execute(
                    f"UPDATE athletes SET {set_clause}, version = ?, updated_at = ? WHERE id = ?",
                    [*changes.values(), new_version, now, current["id"]],
                )
                new = dict(
                    conn.execute("SELECT * FROM athletes WHERE id = ?", (current["id"],)).fetchone()
                )
                self._log_action(
                    conn,
                    event_id=event_id,
                    athlete_id=current["id"],
                    action="import_updated",
                    actor=actor,
                    previous=current,
                    new=new,
                    version_after=new_version,
                )
                stats["updated"] += 1

            for athlete in non_advancers:
                self._update_athlete(
                    conn,
                    event_id=event_id,
                    athlete_id=athlete["id"],
                    changes={
                        "active_state": "eliminated",
                        "call_status": "waiting",
                        "live_location": "",
                        "reported_at": None,
                        "reported_by": "",
                        "covered_by": "",
                        "covered_at": None,
                        "help_requested_by": "",
                        "help_requested_at": None,
                        "help_location": "",
                        "help_acknowledged_by": "",
                        "help_acknowledged_at": None,
                    },
                    action="de_import_not_advanced",
                    actor=actor,
                    expected_version=int(athlete["version"]),
                    require_active=True,
                )
                stats["moved_out"] += 1
            conn.execute("UPDATE events SET updated_at = ? WHERE id = ?", (now, event_id))
            conn.commit()
        return stats

    def _update_athlete(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: str,
        athlete_id: str,
        changes: dict[str, Any],
        action: str,
        actor: str,
        expected_version: int | None = None,
        require_active: bool = False,
    ) -> dict[str, Any]:
        current_row = conn.execute(
            "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
            (event_id, athlete_id),
        ).fetchone()
        if current_row is None:
            raise CompCoachError("Athlete not found.")
        current = dict(current_row)
        if expected_version is not None and int(current["version"]) != int(expected_version):
            raise ConcurrentUpdateError(
                "This athlete changed on another phone. The board has been refreshed; please try again."
            )
        if require_active and current["active_state"] != "active":
            raise ConcurrentUpdateError(
                f"{current['name']} is no longer active. The update was not saved."
            )
        safe_changes = {key: value for key, value in changes.items() if key in ATHLETE_MUTABLE_FIELDS}
        if not safe_changes:
            return current
        new_version = int(current["version"]) + 1
        set_clause = ", ".join(f"{key} = ?" for key in safe_changes)
        conn.execute(
            f"UPDATE athletes SET {set_clause}, version = ?, updated_at = ? WHERE id = ?",
            [*safe_changes.values(), new_version, utc_now(), athlete_id],
        )
        new = dict(conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone())
        self._log_action(
            conn,
            event_id=event_id,
            athlete_id=athlete_id,
            action=action,
            actor=actor,
            previous=current,
            new=new,
            version_after=new_version,
        )
        return new

    def assign_athletes(
        self,
        event_id: str,
        athlete_ids: Iterable[str],
        *,
        main_coach: object = NO_CHANGE,
        side_coach: object = NO_CHANGE,
        actor: str,
    ) -> int:
        ids = list(dict.fromkeys(athlete_ids))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            count = 0
            for athlete_id in ids:
                changes: dict[str, Any] = {}
                if main_coach is not NO_CHANGE:
                    changes["main_coach"] = str(main_coach or "")
                if side_coach is not NO_CHANGE:
                    changes["side_coach"] = str(side_coach or "")
                if changes:
                    # A person-specific choice must survive later pod imports.
                    changes["assignment_override"] = 1
                    self._update_athlete(
                        conn,
                        event_id=event_id,
                        athlete_id=athlete_id,
                        changes=changes,
                        action="assignment",
                        actor=actor,
                    )
                    count += 1
            conn.commit()
        return count

    def assign_pod(
        self,
        event_id: str,
        *,
        phase: str,
        pod: str,
        main_coach: str,
        side_coach: str,
        actor: str,
    ) -> int:
        pod = pod.strip().upper()
        if phase not in {"pools", "de"} or not pod:
            raise ValueError("Phase and pod are required.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            now = utc_now()
            conn.execute(
                """
                INSERT INTO pod_assignments (
                    event_id, phase, pod, main_coach, side_coach, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, phase, pod) DO UPDATE SET
                    main_coach = excluded.main_coach,
                    side_coach = excluded.side_coach,
                    updated_at = excluded.updated_at
                """,
                (event_id, phase, pod, main_coach, side_coach, now),
            )
            rows = conn.execute(
                """
                SELECT id FROM athletes
                WHERE event_id = ? AND phase = ? AND pod = ? AND assignment_override = 0
                """,
                (event_id, phase, pod),
            ).fetchall()
            for row in rows:
                self._update_athlete(
                    conn,
                    event_id=event_id,
                    athlete_id=row["id"],
                    changes={"main_coach": main_coach, "side_coach": side_coach},
                    action="pod_assignment",
                    actor=actor,
                )
            conn.commit()
        return len(rows)

    def report_call(
        self,
        event_id: str,
        athlete_id: str,
        *,
        status: str,
        location: str,
        actor: str,
        covered_by: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if status not in {"in_hole", "on_deck", "now"}:
            raise ValueError("Unknown live status.")
        now = utc_now()
        changes: dict[str, Any] = {
            "call_status": status,
            "live_location": location.strip().upper(),
            "reported_at": now,
            "reported_by": actor,
        }
        if covered_by is not None:
            changes["covered_by"] = covered_by
            changes["covered_at"] = now if covered_by else None
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            help_state = conn.execute(
                "SELECT help_requested_at FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if help_state and help_state["help_requested_at"] and location.strip():
                changes["help_location"] = location.strip().upper()
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes=changes,
                action="live_update",
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def claim(self, event_id: str, athlete_id: str, coach: str) -> tuple[bool, str]:
        coach = coach.strip()
        if not coach:
            return False, "Choose your name first."
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False, "Athlete not found."
            current = dict(row)
            if current["active_state"] != "active":
                conn.rollback()
                return False, f"{current['name']} is no longer active."
            if current["call_status"] == "waiting":
                conn.rollback()
                return False, "There is no active call to cover."
            if current["covered_by"]:
                conn.rollback()
                return False, f"Already covered by {current['covered_by']}."
            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE athletes
                SET covered_by = ?, covered_at = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND event_id = ? AND covered_by = '' AND active_state = 'active'
                """,
                (coach, now, now, athlete_id, event_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                latest = conn.execute("SELECT covered_by FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
                who = latest["covered_by"] if latest else "another coach"
                return False, f"Already covered by {who}."
            new = dict(conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone())
            self._log_action(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                action="claim",
                actor=coach,
                previous=current,
                new=new,
                version_after=new["version"],
            )
            conn.commit()
        return True, f"You are covering {new['name']}."

    def release(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={"covered_by": "", "covered_at": None},
                action="release",
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def set_pool_result(
        self,
        event_id: str,
        athlete_id: str,
        *,
        wins: int,
        losses: int,
        actor: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if isinstance(wins, bool) or isinstance(losses, bool):
            raise TypeError("Wins and losses must be whole numbers.")
        wins = int(wins)
        losses = int(losses)
        if wins < 0 or losses < 0 or wins > 6 or losses > 6:
            raise ValueError("Wins and losses must be between 0 and 6.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            current = conn.execute(
                "SELECT phase FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if current is None:
                raise CompCoachError("Athlete not found.")
            if current["phase"] != "pools":
                raise CompCoachError("Pool results are available only during Pools.")
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "pool_wins": wins,
                    "pool_losses": losses,
                    "pool_result_at": utc_now(),
                    "pool_result_by": actor,
                },
                action="pool_result",
                actor=actor,
                expected_version=expected_version,
            )
            conn.commit()
        return result

    def request_help(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        location: str = "",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if row is None:
                raise CompCoachError("Athlete not found.")
            current = dict(row)
            if current.get("help_requested_at"):
                if current.get("help_requested_by") == actor:
                    conn.commit()
                    return current
                raise CompCoachError(
                    f"Help was already requested by {current.get('help_requested_by') or 'another coach'}."
                )
            if expected_version is not None and int(current["version"]) != int(expected_version):
                raise ConcurrentUpdateError(
                    "This athlete changed on another phone. The board has been refreshed; please try again."
                )
            if current["active_state"] != "active":
                raise CompCoachError("Help cannot be requested for an athlete who is Out.")
            snapshot = (
                location.strip().upper()
                or current.get("live_location")
                or current.get("source_strip")
                or (f"Pod {current.get('pod')}" if current.get("pod") else "Location TBD")
            )
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "help_requested_by": actor,
                    "help_requested_at": utc_now(),
                    "help_location": snapshot,
                    "help_acknowledged_by": "",
                    "help_acknowledged_at": None,
                },
                action="help_request",
                actor=actor,
                expected_version=current["version"],
                require_active=True,
            )
            conn.commit()
        return result

    def acknowledge_help(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if row is None:
                raise CompCoachError("Athlete not found.")
            current = dict(row)
            if not current.get("help_requested_at"):
                raise ConcurrentUpdateError("This help request has already been resolved.")
            if current.get("help_requested_by") == actor:
                raise CompCoachError("You created this help request.")
            if current.get("help_acknowledged_by"):
                if current["help_acknowledged_by"] == actor:
                    conn.commit()
                    return current
                raise ConcurrentUpdateError(
                    f"{current['help_acknowledged_by']} is already responding."
                )
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "help_acknowledged_by": actor,
                    "help_acknowledged_at": utc_now(),
                },
                action="help_acknowledged",
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def clear_help(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        reason: str = "resolved",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if reason not in {"resolved", "cancelled"}:
            raise ValueError("Unknown help-request resolution.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if row is None:
                raise CompCoachError("Athlete not found.")
            current = dict(row)
            if not current.get("help_requested_at"):
                conn.commit()
                return current
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "help_requested_by": "",
                    "help_requested_at": None,
                    "help_location": "",
                    "help_acknowledged_by": "",
                    "help_acknowledged_at": None,
                },
                action=f"help_{reason}",
                actor=actor,
                expected_version=expected_version,
            )
            conn.commit()
        return result

    def mark_result(
        self,
        event_id: str,
        athlete_id: str,
        *,
        outcome: str,
        actor: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"won", "lost"}:
            raise ValueError("Outcome must be won or lost.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            current = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if current is None:
                raise CompCoachError("Athlete not found.")
            if current["phase"] != "de":
                raise CompCoachError("Won/Lost is available only during direct elimination.")
            if current["active_state"] != "active":
                raise ConcurrentUpdateError(
                    "This athlete is already Out. The board has been refreshed."
                )
            now = utc_now()
            changes = {
                "active_state": "eliminated" if outcome == "lost" else "active",
                "call_status": "waiting",
                "live_location": "",
                "reported_at": None,
                "reported_by": "",
                "covered_by": "",
                "covered_at": None,
                "de_wins": int(current["de_wins"] or 0) + (1 if outcome == "won" else 0),
                "last_de_result": outcome,
                "last_de_result_at": now,
                "last_de_result_by": actor,
                "help_requested_by": "",
                "help_requested_at": None,
                "help_location": "",
                "help_acknowledged_by": "",
                "help_acknowledged_at": None,
            }
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes=changes,
                action=outcome,
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def mark_out(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Move a pools athlete out without inventing a DE bout result."""

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "active_state": "eliminated",
                    "call_status": "waiting",
                    "live_location": "",
                    "reported_at": None,
                    "reported_by": "",
                    "covered_by": "",
                    "covered_at": None,
                    "help_requested_by": "",
                    "help_requested_at": None,
                    "help_location": "",
                    "help_acknowledged_by": "",
                    "help_acknowledged_at": None,
                },
                action="out",
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def clear_call(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Cancel obsolete live information and return the athlete to Waiting."""

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "call_status": "waiting",
                    "live_location": "",
                    "reported_at": None,
                    "reported_by": "",
                    "covered_by": "",
                    "covered_at": None,
                    "help_requested_by": "",
                    "help_requested_at": None,
                    "help_location": "",
                    "help_acknowledged_by": "",
                    "help_acknowledged_at": None,
                },
                action="clear_call",
                actor=actor,
                expected_version=expected_version,
                require_active=True,
            )
            conn.commit()
        return result

    def restore_athlete(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
                (event_id, athlete_id),
            ).fetchone()
            if row is None:
                raise CompCoachError("Athlete not found.")
            current = dict(row)
            source_action = conn.execute(
                """
                SELECT previous_json FROM actions
                WHERE event_id = ? AND athlete_id = ?
                  AND action IN ('lost', 'out', 'de_import_not_advanced')
                  AND undone_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (event_id, athlete_id),
            ).fetchone()
            previous = _load(source_action["previous_json"], {}) if source_action else {}
            fallback = {
                "active_state": "active",
                "call_status": "waiting",
                "live_location": "",
                "reported_at": None,
                "reported_by": "",
                "covered_by": "",
                "covered_at": None,
                "help_requested_by": "",
                "help_requested_at": None,
                "help_location": "",
                "help_acknowledged_by": "",
                "help_acknowledged_at": None,
            }
            restore_fields = {
                "active_state",
                "call_status",
                "live_location",
                "reported_at",
                "reported_by",
                "covered_by",
                "covered_at",
                "de_wins",
                "last_de_result",
                "last_de_result_at",
                "last_de_result_by",
                "help_requested_by",
                "help_requested_at",
                "help_location",
                "help_acknowledged_by",
                "help_acknowledged_at",
            }
            changes = {
                field: previous.get(field, fallback.get(field, current.get(field)))
                for field in restore_fields
            }
            changes["active_state"] = "active"
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes=changes,
                action="restore",
                actor=actor,
                expected_version=expected_version,
            )
            conn.commit()
        return result

    def recent_actions(self, event_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT actions.*, athletes.name AS athlete_name
                FROM actions
                LEFT JOIN athletes ON athletes.id = actions.athlete_id
                WHERE actions.event_id = ?
                ORDER BY actions.id DESC LIMIT ?
                """,
                (event_id, max(1, min(int(limit), 200))),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["previous"] = _load(data.pop("previous_json"), None)
            data["new"] = _load(data.pop("new_json"), None)
            result.append(data)
        return result

    def undo_action(self, event_id: str, action_id: int, actor: str) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            action_row = conn.execute(
                """
                SELECT * FROM actions
                WHERE event_id = ? AND id = ? AND undone_at IS NULL
                """,
                (event_id, action_id),
            ).fetchone()
            if action_row is None:
                raise CompCoachError("This action is no longer available for Undo.")
            action = dict(action_row)
            previous = _load(action["previous_json"], None)
            if not previous or not action["athlete_id"]:
                raise CompCoachError("This import action cannot be undone here.")
            current_row = conn.execute(
                "SELECT * FROM athletes WHERE id = ? AND event_id = ?",
                (action["athlete_id"], event_id),
            ).fetchone()
            if current_row is None:
                raise CompCoachError("Athlete not found.")
            current = dict(current_row)
            if int(current["version"]) != int(action["version_after"]):
                raise ConcurrentUpdateError(
                    "A newer update exists. Undo was blocked to avoid overwriting it."
                )
            # Older action rows may predate newly added athlete columns.
            restored = {
                field: previous[field] if field in previous else current.get(field)
                for field in ATHLETE_MUTABLE_FIELDS
            }
            new_version = int(current["version"]) + 1
            set_clause = ", ".join(f"{key} = ?" for key in restored)
            now = utc_now()
            conn.execute(
                f"UPDATE athletes SET {set_clause}, version = ?, updated_at = ? WHERE id = ?",
                [*restored.values(), new_version, now, current["id"]],
            )
            new = dict(conn.execute("SELECT * FROM athletes WHERE id = ?", (current["id"],)).fetchone())
            conn.execute(
                "UPDATE actions SET undone_at = ?, undone_by = ? WHERE id = ?",
                (now, actor, action_id),
            )
            self._log_action(
                conn,
                event_id=event_id,
                athlete_id=current["id"],
                action="undo",
                actor=actor,
                previous=current,
                new=new,
                version_after=new_version,
            )
            conn.commit()
        return new
