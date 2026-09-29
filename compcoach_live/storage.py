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


def _normalize_name(value: str) -> str:
    """Return the stable key used by the persistent coach directory."""

    return " ".join(str(value or "").split()).casefold()


def _validated_date(value: str, *, label: str, allow_blank: bool = True) -> str:
    clean = str(value or "").strip()
    if not clean and allow_blank:
        return ""
    try:
        date.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from exc
    return clean


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
    "participation_status",
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

                CREATE TABLE IF NOT EXISTS competitions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                    logo_path TEXT NOT NULL DEFAULT '',
                    strip_map_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meets (
                    id TEXT PRIMARY KEY,
                    competition_id TEXT REFERENCES competitions(id) ON DELETE SET NULL,
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
                    day_status TEXT NOT NULL DEFAULT 'active'
                        CHECK (day_status IN ('scheduled', 'active', 'closed')),
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
                    participation_status TEXT NOT NULL DEFAULT 'active'
                        CHECK (participation_status IN ('active', 'absent', 'withdrawn')),
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

                CREATE TABLE IF NOT EXISTS pool_waves (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    wave_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 0
                        CHECK (is_active IN (0, 1)),
                    is_visible INTEGER NOT NULL DEFAULT 0
                        CHECK (is_visible IN (0, 1)),
                    activated_at TEXT,
                    activated_by TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, wave_key)
                );

                CREATE TABLE IF NOT EXISTS coach_availability (
                    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
                    coach_name TEXT NOT NULL,
                    is_available INTEGER NOT NULL DEFAULT 0
                        CHECK (is_available IN (0, 1)),
                    available_since TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(meet_id, coach_name)
                );

                CREATE TABLE IF NOT EXISTS coaches (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    is_active INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS competition_coaches (
                    competition_id TEXT NOT NULL
                        REFERENCES competitions(id) ON DELETE CASCADE,
                    coach_id TEXT NOT NULL REFERENCES coaches(id) ON DELETE RESTRICT,
                    role TEXT NOT NULL DEFAULT 'coach'
                        CHECK (role IN ('coach', 'coordinator', 'admin')),
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(competition_id, coach_id, role)
                );

                CREATE TABLE IF NOT EXISTS day_coach_presence (
                    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
                    coach_id TEXT NOT NULL REFERENCES coaches(id) ON DELETE RESTRICT,
                    presence_status TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK (presence_status IN ('scheduled', 'present', 'absent')),
                    home_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(meet_id, coach_id)
                );

                CREATE TABLE IF NOT EXISTS coach_assignment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    competition_id TEXT REFERENCES competitions(id) ON DELETE SET NULL,
                    meet_id TEXT REFERENCES meets(id) ON DELETE SET NULL,
                    event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
                    athlete_id TEXT REFERENCES athletes(id) ON DELETE SET NULL,
                    coach_id TEXT REFERENCES coaches(id) ON DELETE SET NULL,
                    coach_name TEXT NOT NULL,
                    assignment_kind TEXT NOT NULL
                        CHECK (assignment_kind IN ('main', 'side', 'coverage')),
                    target_type TEXT NOT NULL
                        CHECK (target_type IN ('athlete', 'pod')),
                    phase TEXT NOT NULL DEFAULT '',
                    pod TEXT NOT NULL DEFAULT '',
                    home_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
                    is_cross_event INTEGER NOT NULL DEFAULT 0
                        CHECK (is_cross_event IN (0, 1)),
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    assigned_by TEXT NOT NULL DEFAULT '',
                    ended_by TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT ''
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
                CREATE INDEX IF NOT EXISTS idx_coach_availability_meet
                    ON coach_availability(meet_id, is_available);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_pool_wave
                    ON pool_waves(event_id) WHERE is_active = 1;
                CREATE INDEX IF NOT EXISTS idx_pool_waves_order
                    ON pool_waves(event_id, sort_order, wave_key);
                CREATE INDEX IF NOT EXISTS idx_competition_coaches
                    ON competition_coaches(competition_id, role);
                CREATE INDEX IF NOT EXISTS idx_day_coach_presence
                    ON day_coach_presence(meet_id, presence_status);
                CREATE INDEX IF NOT EXISTS idx_assignment_history_athlete
                    ON coach_assignment_history(athlete_id, started_at, id);
                CREATE INDEX IF NOT EXISTS idx_assignment_history_coach
                    ON coach_assignment_history(coach_id, coach_name, started_at, id);
                CREATE INDEX IF NOT EXISTS idx_assignment_history_open
                    ON coach_assignment_history(event_id, target_type, assignment_kind)
                    WHERE ended_at IS NULL;
                """
            )
            # In-place migration for databases created by an earlier build.
            # SQLite does not support ADD COLUMN IF NOT EXISTS.
            athlete_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(athletes)").fetchall()
            }
            migrations = {
                "assignment_override": "INTEGER NOT NULL DEFAULT 0",
                "participation_status": "TEXT NOT NULL DEFAULT 'active'",
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
                "competition_id": (
                    "TEXT REFERENCES competitions(id) ON DELETE SET NULL"
                ),
                "day_status": "TEXT NOT NULL DEFAULT 'active'",
            }
            for column, definition in meet_migrations.items():
                if column not in meet_columns:
                    conn.execute(f"ALTER TABLE meets ADD COLUMN {column} {definition}")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_meets_competition_day
                ON meets(competition_id, competition_date)
                """
            )
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

                # Promote every legacy meet to a day under a durable parent
                # competition. Prepared day chains remain grouped together.
                meet_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM meets ORDER BY created_at, id"
                    ).fetchall()
                ]
                by_id = {str(row["id"]): row for row in meet_rows}

                def root_meet_id(meet: dict[str, Any]) -> str:
                    current = meet
                    visited: set[str] = set()
                    while current.get("prepared_from_meet_id"):
                        current_id = str(current["id"])
                        if current_id in visited:
                            break
                        visited.add(current_id)
                        parent = by_id.get(str(current["prepared_from_meet_id"]))
                        if parent is None:
                            break
                        current = parent
                    return str(current["id"])

                grouped: dict[str, list[dict[str, Any]]] = {}
                for meet in meet_rows:
                    grouped.setdefault(root_meet_id(meet), []).append(meet)

                for root_id, days in grouped.items():
                    competition_id = next(
                        (
                            str(day["competition_id"])
                            for day in days
                            if day.get("competition_id")
                        ),
                        "",
                    )
                    if not competition_id:
                        root = by_id[root_id]
                        competition_id = uuid4().hex
                        dated_days = sorted(
                            str(day.get("competition_date") or "")
                            for day in days
                            if str(day.get("competition_date") or "")
                        )
                        conn.execute(
                            """
                            INSERT INTO competitions (
                                id, name, location, start_date, end_date,
                                timezone, logo_path, strip_map_path,
                                created_at, updated_at
                            ) VALUES (?, ?, '', ?, ?, ?, '', '', ?, ?)
                            """,
                            (
                                competition_id,
                                root["name"],
                                dated_days[0] if dated_days else "",
                                dated_days[-1] if dated_days else "",
                                root["timezone"],
                                root["created_at"],
                                max(str(day["updated_at"]) for day in days),
                            ),
                        )
                    conn.execute(
                        """
                        UPDATE meets SET competition_id = ?
                        WHERE id IN ({}) AND competition_id IS NULL
                        """.format(",".join("?" for _ in days)),
                        (competition_id, *(str(day["id"]) for day in days)),
                    )

                if "day_status" not in meet_columns:
                    conn.execute(
                        """
                        UPDATE meets
                        SET day_status = CASE
                            WHEN status = 'locked' OR ended_at IS NOT NULL
                                THEN 'closed'
                            ELSE 'active'
                        END
                        """
                    )

                # A competition has one operational day. If a legacy database
                # drifted into multiple open days, retain the newest as active
                # and keep earlier open days editable as scheduled.
                competition_rows = conn.execute(
                    "SELECT id FROM competitions"
                ).fetchall()
                for competition in competition_rows:
                    active_days = conn.execute(
                        """
                        SELECT id FROM meets
                        WHERE competition_id = ? AND day_status = 'active'
                        ORDER BY competition_date DESC, created_at DESC, id DESC
                        """,
                        (competition["id"],),
                    ).fetchall()
                    for extra in active_days[1:]:
                        conn.execute(
                            "UPDATE meets SET day_status = 'scheduled' WHERE id = ?",
                            (extra["id"],),
                        )

                # Populate the durable coach directory and retain the legacy
                # JSON lists as a compatibility projection for the current UI.
                for meet in conn.execute("SELECT * FROM meets").fetchall():
                    competition_id = str(meet["competition_id"] or "")
                    coach_names = [
                        str(value).strip()
                        for value in _load(meet["active_coaches_json"], [])
                        if str(value).strip()
                    ]
                    coordinator_names = {
                        str(value).strip()
                        for value in _load(meet["coordinators_json"], [])
                        if str(value).strip()
                    }
                    for coach_name in dict.fromkeys(
                        [*coach_names, *coordinator_names]
                    ):
                        normalized = _normalize_name(coach_name)
                        coach = conn.execute(
                            "SELECT id FROM coaches WHERE normalized_name = ?",
                            (normalized,),
                        ).fetchone()
                        if coach is None:
                            coach_id = uuid4().hex
                            conn.execute(
                                """
                                INSERT INTO coaches (
                                    id, name, normalized_name, is_active,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, 1, ?, ?)
                                """,
                                (
                                    coach_id,
                                    coach_name,
                                    normalized,
                                    meet["created_at"],
                                    meet["updated_at"],
                                ),
                            )
                        else:
                            coach_id = str(coach["id"])
                        roles = ["coach"]
                        if coach_name in coordinator_names:
                            roles.append("coordinator")
                        for role in roles:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO competition_coaches (
                                    competition_id, coach_id, role, added_at
                                ) VALUES (?, ?, ?, ?)
                                """,
                                (
                                    competition_id,
                                    coach_id,
                                    role,
                                    meet["created_at"],
                                ),
                            )
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO day_coach_presence (
                                meet_id, coach_id, presence_status,
                                home_event_id, updated_at, updated_by
                            ) VALUES (?, ?, 'present', NULL, ?, 'Migration')
                            """,
                            (meet["id"], coach_id, meet["updated_at"]),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_day_per_competition
                ON meets(competition_id) WHERE day_status = 'active'
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                for athlete_row in conn.execute(
                    "SELECT * FROM athletes ORDER BY created_at, id"
                ).fetchall():
                    if conn.execute(
                        """
                        SELECT 1 FROM coach_assignment_history
                        WHERE athlete_id = ? LIMIT 1
                        """,
                        (athlete_row["id"],),
                    ).fetchone():
                        continue
                    self._sync_athlete_assignment_history(
                        conn,
                        current=None,
                        new=dict(athlete_row),
                        actor="Migration",
                        source="legacy_snapshot",
                    )
                for pod_row in conn.execute(
                    "SELECT * FROM pod_assignments ORDER BY event_id, phase, pod"
                ).fetchall():
                    if conn.execute(
                        """
                        SELECT 1 FROM coach_assignment_history
                        WHERE event_id = ? AND target_type = 'pod'
                          AND phase = ? AND pod = ? LIMIT 1
                        """,
                        (pod_row["event_id"], pod_row["phase"], pod_row["pod"]),
                    ).fetchone():
                        continue
                    for kind in ("main", "side"):
                        self._sync_assignment_slot(
                            conn,
                            event_id=str(pod_row["event_id"]),
                            athlete_id=None,
                            target_type="pod",
                            assignment_kind=kind,
                            coach_name=str(pod_row[f"{kind}_coach"] or ""),
                            phase=str(pod_row["phase"]),
                            pod=str(pod_row["pod"]),
                            actor="Migration",
                            source="legacy_snapshot",
                        )
                conn.execute(
                    """
                    UPDATE coach_assignment_history
                    SET ended_at = COALESCE(
                            (SELECT meets.ended_at FROM meets
                             WHERE meets.id = coach_assignment_history.meet_id),
                            (SELECT meets.updated_at FROM meets
                             WHERE meets.id = coach_assignment_history.meet_id)
                        ),
                        ended_by = 'Migration'
                    WHERE ended_at IS NULL AND meet_id IN (
                        SELECT id FROM meets WHERE day_status = 'closed'
                    )
                    """
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
    def _competition_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _coach_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        if "is_active" in data:
            data["is_active"] = bool(data["is_active"])
        if "is_present" in data:
            data["is_present"] = bool(data["is_present"])
        if "is_cross_event" in data:
            data["is_cross_event"] = bool(data["is_cross_event"])
        return data

    @staticmethod
    def _athlete_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _ensure_coach_record(
        conn: sqlite3.Connection,
        name: str,
        *,
        now: str | None = None,
    ) -> sqlite3.Row:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("Coach name is required.")
        normalized = _normalize_name(clean_name)
        row = conn.execute(
            "SELECT * FROM coaches WHERE normalized_name = ?", (normalized,)
        ).fetchone()
        if row is not None:
            return row
        timestamp = now or utc_now()
        coach_id = uuid4().hex
        conn.execute(
            """
            INSERT INTO coaches (
                id, name, normalized_name, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            """,
            (coach_id, clean_name, normalized, timestamp, timestamp),
        )
        return conn.execute(
            "SELECT * FROM coaches WHERE id = ?", (coach_id,)
        ).fetchone()

    @classmethod
    def _sync_staff_records(
        cls,
        conn: sqlite3.Connection,
        *,
        meet_id: str,
        competition_id: str,
        coaches: Iterable[str],
        coordinators: Iterable[str],
        actor: str,
        replace_day: bool = False,
    ) -> None:
        now = utc_now()
        clean_coaches = list(
            dict.fromkeys(
                " ".join(str(name or "").split())
                for name in coaches
                if str(name or "").strip()
            )
        )
        clean_coordinators = list(
            dict.fromkeys(
                " ".join(str(name or "").split())
                for name in coordinators
                if str(name or "").strip()
            )
        )
        coach_ids: list[str] = []
        for name in dict.fromkeys([*clean_coaches, *clean_coordinators]):
            coach = cls._ensure_coach_record(conn, name, now=now)
            coach_id = str(coach["id"])
            coach_ids.append(coach_id)
            roles = ["coach"]
            if name in clean_coordinators:
                roles.append("coordinator")
            for role in roles:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO competition_coaches (
                        competition_id, coach_id, role, added_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (competition_id, coach_id, role, now),
                )
            conn.execute(
                """
                INSERT INTO day_coach_presence (
                    meet_id, coach_id, presence_status, home_event_id,
                    updated_at, updated_by
                ) VALUES (?, ?, 'present', NULL, ?, ?)
                ON CONFLICT(meet_id, coach_id) DO UPDATE SET
                    presence_status = CASE
                        WHEN day_coach_presence.presence_status = 'absent'
                            THEN 'absent'
                        ELSE 'present'
                    END,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (meet_id, coach_id, now, actor or "System"),
            )
        if replace_day:
            if coach_ids:
                placeholders = ",".join("?" for _ in coach_ids)
                conn.execute(
                    f"""
                    DELETE FROM day_coach_presence
                    WHERE meet_id = ? AND coach_id NOT IN ({placeholders})
                    """,
                    (meet_id, *coach_ids),
                )
            else:
                conn.execute(
                    "DELETE FROM day_coach_presence WHERE meet_id = ?", (meet_id,)
                )

    @classmethod
    def _assignment_context(
        cls,
        conn: sqlite3.Connection,
        event_id: str,
        coach_name: str,
        *,
        now: str,
    ) -> tuple[str, str, str, str | None, int]:
        membership = conn.execute(
            """
            SELECT meets.id AS meet_id, meets.competition_id
            FROM meet_events
            JOIN meets ON meets.id = meet_events.meet_id
            WHERE meet_events.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if membership is None:
            raise CompCoachError("Competition group not found.")
        coach = cls._ensure_coach_record(conn, coach_name, now=now)
        coach_id = str(coach["id"])
        competition_id = str(membership["competition_id"] or "")
        if competition_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO competition_coaches (
                    competition_id, coach_id, role, added_at
                ) VALUES (?, ?, 'coach', ?)
                """,
                (competition_id, coach_id, now),
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO day_coach_presence (
                meet_id, coach_id, presence_status, home_event_id,
                updated_at, updated_by
            ) VALUES (?, ?, 'present', NULL, ?, 'Assignment')
            """,
            (membership["meet_id"], coach_id, now),
        )
        presence = conn.execute(
            """
            SELECT home_event_id FROM day_coach_presence
            WHERE meet_id = ? AND coach_id = ?
            """,
            (membership["meet_id"], coach_id),
        ).fetchone()
        home_event_id = (
            str(presence["home_event_id"])
            if presence is not None and presence["home_event_id"]
            else None
        )
        is_cross_event = int(bool(home_event_id and home_event_id != event_id))
        return (
            coach_id,
            str(membership["meet_id"]),
            competition_id,
            home_event_id,
            is_cross_event,
        )

    @classmethod
    def _sync_assignment_slot(
        cls,
        conn: sqlite3.Connection,
        *,
        event_id: str,
        athlete_id: str | None,
        target_type: str,
        assignment_kind: str,
        coach_name: str,
        phase: str,
        pod: str,
        actor: str,
        source: str,
    ) -> None:
        """Close/open one assignment interval without deleting history."""

        now = utc_now()
        if target_type == "athlete":
            open_rows = conn.execute(
                """
                SELECT * FROM coach_assignment_history
                WHERE event_id = ? AND athlete_id = ?
                  AND target_type = 'athlete' AND assignment_kind = ?
                  AND ended_at IS NULL
                ORDER BY id
                """,
                (event_id, athlete_id, assignment_kind),
            ).fetchall()
        else:
            open_rows = conn.execute(
                """
                SELECT * FROM coach_assignment_history
                WHERE event_id = ? AND target_type = 'pod'
                  AND phase = ? AND pod = ? AND assignment_kind = ?
                  AND ended_at IS NULL
                ORDER BY id
                """,
                (event_id, phase, pod, assignment_kind),
            ).fetchall()

        clean_name = " ".join(str(coach_name or "").split())
        unchanged = len(open_rows) == 1 and (
            _normalize_name(str(open_rows[0]["coach_name"]))
            == _normalize_name(clean_name)
            and str(open_rows[0]["phase"] or "") == phase
            and str(open_rows[0]["pod"] or "") == pod
        )
        if unchanged:
            return
        if open_rows:
            conn.executemany(
                """
                UPDATE coach_assignment_history
                SET ended_at = ?, ended_by = ?
                WHERE id = ? AND ended_at IS NULL
                """,
                [(now, actor or "System", row["id"]) for row in open_rows],
            )
        if not clean_name:
            return

        coach_id, meet_id, competition_id, home_event_id, is_cross_event = (
            cls._assignment_context(
                conn, event_id, clean_name, now=now
            )
        )
        conn.execute(
            """
            INSERT INTO coach_assignment_history (
                competition_id, meet_id, event_id, athlete_id, coach_id,
                coach_name, assignment_kind, target_type, phase, pod,
                home_event_id, is_cross_event, started_at, assigned_by, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                competition_id or None,
                meet_id,
                event_id,
                athlete_id,
                coach_id,
                clean_name,
                assignment_kind,
                target_type,
                phase,
                pod,
                home_event_id,
                is_cross_event,
                now,
                actor or "System",
                source,
            ),
        )

    @classmethod
    def _sync_athlete_assignment_history(
        cls,
        conn: sqlite3.Connection,
        *,
        current: Mapping[str, Any] | None,
        new: Mapping[str, Any],
        actor: str,
        source: str,
    ) -> None:
        event_id = str(new["event_id"])
        athlete_id = str(new["id"])
        phase = str(new.get("phase") or "")
        pod = str(new.get("pod") or "")
        for field, kind in (
            ("main_coach", "main"),
            ("side_coach", "side"),
            ("covered_by", "coverage"),
        ):
            old_value = str((current or {}).get(field) or "")
            new_value = str(new.get(field) or "")
            context_changed = bool(current) and (
                str(current.get("phase") or "") != phase
                or str(current.get("pod") or "") != pod
            )
            if old_value == new_value and not context_changed:
                continue
            cls._sync_assignment_slot(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                target_type="athlete",
                assignment_kind=kind,
                coach_name=new_value,
                phase=phase,
                pod=pod,
                actor=actor,
                source=source,
            )

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

    @staticmethod
    def _meet_for_event(
        conn: sqlite3.Connection, event_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT meets.*
            FROM meets
            JOIN meet_events ON meet_events.meet_id = meets.id
            WHERE meet_events.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise CompCoachError("Competition group not found.")
        return row

    @staticmethod
    def _clear_available_coaches(
        conn: sqlite3.Connection,
        meet_id: str,
        coaches: Iterable[str],
        actor: str,
    ) -> None:
        """Mark explicitly available coaches engaged inside the caller's transaction."""

        names = list(
            dict.fromkeys(
                str(coach or "").strip()
                for coach in coaches
                if str(coach or "").strip()
            )
        )
        meet = conn.execute(
            "SELECT active_coaches_json FROM meets WHERE id = ?", (meet_id,)
        ).fetchone()
        if meet is None:
            raise CompCoachError("Competition group not found.")
        active_coaches = {
            str(coach).strip()
            for coach in _load(meet["active_coaches_json"], [])
            if str(coach).strip()
        }
        names = [coach for coach in names if coach in active_coaches]
        if not names:
            return
        now = utc_now()
        clean_actor = str(actor or "System").strip() or "System"
        for coach in names:
            # Advance the version even when no row exists or the coach was
            # already engaged.  This invalidates an "I'm available" tap from
            # a phone that loaded before the new assignment was made.
            conn.execute(
                """
                INSERT INTO coach_availability (
                    meet_id, coach_name, is_available, available_since,
                    updated_at, updated_by, version
                ) VALUES (?, ?, 0, NULL, ?, ?, 1)
                ON CONFLICT(meet_id, coach_name) DO UPDATE SET
                    is_available = 0,
                    available_since = NULL,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    version = coach_availability.version + 1
                """,
                (meet_id, coach, now, clean_actor),
            )

    @staticmethod
    def _delete_removed_coach_availability(
        conn: sqlite3.Connection,
        meet_id: str,
        active_coaches: Iterable[str],
    ) -> None:
        names = list(
            dict.fromkeys(
                str(coach or "").strip()
                for coach in active_coaches
                if str(coach or "").strip()
            )
        )
        if not names:
            conn.execute(
                "DELETE FROM coach_availability WHERE meet_id = ?", (meet_id,)
            )
            return
        placeholders = ", ".join("?" for _ in names)
        conn.execute(
            f"""
            DELETE FROM coach_availability
            WHERE meet_id = ? AND coach_name NOT IN ({placeholders})
            """,
            (meet_id, *names),
        )

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

    def create_competition(
        self,
        name: str,
        *,
        location: str = "",
        start_date: str = "",
        end_date: str = "",
        timezone_name: str = "America/Los_Angeles",
        logo_path: str = "",
        strip_map_path: str = "",
    ) -> dict[str, Any]:
        """Create a season-level competition parent without creating a day."""

        clean_name = str(name or "").strip() or "AFM Competition"
        clean_start = _validated_date(start_date, label="Start date")
        clean_end = _validated_date(end_date, label="End date")
        if clean_start and clean_end and clean_end < clean_start:
            raise ValueError("End date cannot be before start date.")
        competition_id = uuid4().hex
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO competitions (
                    id, name, location, start_date, end_date, timezone,
                    logo_path, strip_map_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    competition_id,
                    clean_name,
                    str(location or "").strip(),
                    clean_start,
                    clean_end,
                    str(timezone_name or "").strip() or "America/Los_Angeles",
                    str(logo_path or "").strip(),
                    str(strip_map_path or "").strip(),
                    now,
                    now,
                ),
            )
        return self.get_competition(competition_id)  # type: ignore[return-value]

    def list_competitions(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT competitions.*,
                       (SELECT COUNT(*) FROM meets
                        WHERE meets.competition_id = competitions.id) AS day_count
                FROM competitions
                ORDER BY start_date DESC, created_at DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_competition(self, competition_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT competitions.*,
                       (SELECT COUNT(*) FROM meets
                        WHERE meets.competition_id = competitions.id) AS day_count
                FROM competitions WHERE id = ?
                """,
                (competition_id,),
            ).fetchone()
        return self._competition_dict(row)

    def update_competition(
        self,
        competition_id: str,
        *,
        name: str,
        location: str = "",
        start_date: str = "",
        end_date: str = "",
        timezone_name: str = "America/Los_Angeles",
        logo_path: str = "",
        strip_map_path: str = "",
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip() or "AFM Competition"
        clean_start = _validated_date(start_date, label="Start date")
        clean_end = _validated_date(end_date, label="End date")
        if clean_start and clean_end and clean_end < clean_start:
            raise ValueError("End date cannot be before start date.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            now = utc_now()
            timezone_value = (
                str(timezone_name or "").strip() or "America/Los_Angeles"
            )
            conn.execute(
                """
                UPDATE competitions SET
                    name = ?, location = ?, start_date = ?, end_date = ?,
                    timezone = ?, logo_path = ?, strip_map_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_name,
                    str(location or "").strip(),
                    clean_start,
                    clean_end,
                    timezone_value,
                    str(logo_path or "").strip(),
                    str(strip_map_path or "").strip(),
                    now,
                    competition_id,
                ),
            )
            conn.execute(
                "UPDATE meets SET timezone = ?, updated_at = ? WHERE competition_id = ?",
                (timezone_value, now, competition_id),
            )
            conn.execute(
                """
                UPDATE events SET timezone = ?, updated_at = ?
                WHERE id IN (
                    SELECT meet_events.event_id FROM meet_events
                    JOIN meets ON meets.id = meet_events.meet_id
                    WHERE meets.competition_id = ?
                )
                """,
                (timezone_value, now, competition_id),
            )
            conn.commit()
        return self.get_competition(competition_id)  # type: ignore[return-value]

    def list_competition_days(self, competition_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            rows = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events
                        WHERE meet_events.meet_id = meets.id) AS event_count
                FROM meets WHERE competition_id = ?
                ORDER BY competition_date, created_at, id
                """,
                (competition_id,),
            ).fetchall()
        return [self._meet_dict(row) for row in rows if row is not None]  # type: ignore[misc]

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
        competition_id = uuid4().hex
        competition_name = name.strip() or "AFM Competition"
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO competitions (
                    id, name, location, start_date, end_date, timezone,
                    logo_path, strip_map_path, created_at, updated_at
                ) VALUES (?, ?, '', '', '', ?, '', '', ?, ?)
                """,
                (competition_id, competition_name, timezone_name, now, now),
            )
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
                    id, competition_id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, day_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    meet_id,
                    competition_id,
                    competition_name,
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
            self._sync_staff_records(
                conn,
                meet_id=meet_id,
                competition_id=competition_id,
                coaches=coaches,
                coordinators=coords,
                actor="Create event",
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
        competition_id: str | None = None,
        day_status: str = "active",
        location: str = "",
        logo_path: str = "",
        strip_map_path: str = "",
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
        clean_date = _validated_date(competition_date, label="Competition date")
        clean_day_status = str(day_status or "").strip().lower()
        if clean_day_status not in {"scheduled", "active", "closed"}:
            raise ValueError("Day status must be scheduled, active, or closed.")
        if clean_day_status == "closed":
            raise ValueError("A new competition day cannot start closed.")
        coaches = [str(x).strip() for x in active_coaches if str(x).strip()]
        coords = [str(x).strip() for x in coordinators if str(x).strip()]
        meet_tokens = (_new_token(), _new_token(), _new_token())
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            resolved_competition_id = str(competition_id or "").strip()
            if resolved_competition_id:
                parent = conn.execute(
                    "SELECT * FROM competitions WHERE id = ?",
                    (resolved_competition_id,),
                ).fetchone()
                if parent is None:
                    raise CompCoachError("Competition not found.")
                if clean_day_status == "active" and conn.execute(
                    """
                    SELECT 1 FROM meets
                    WHERE competition_id = ? AND day_status = 'active'
                    """,
                    (resolved_competition_id,),
                ).fetchone():
                    raise CompCoachError(
                        "This competition already has an active day. "
                        "Create the new day as scheduled."
                    )
                timezone_value = str(parent["timezone"] or timezone_name)
            else:
                resolved_competition_id = uuid4().hex
                timezone_value = timezone_name
                conn.execute(
                    """
                    INSERT INTO competitions (
                        id, name, location, start_date, end_date, timezone,
                        logo_path, strip_map_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_competition_id,
                        meet_name,
                        str(location or "").strip(),
                        clean_date,
                        clean_date,
                        timezone_value,
                        str(logo_path or "").strip(),
                        str(strip_map_path or "").strip(),
                        now,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO meets (
                    id, competition_id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, competition_date, day_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meet_id,
                    resolved_competition_id,
                    meet_name,
                    timezone_value,
                    _dump(coaches),
                    _dump(coords),
                    *meet_tokens,
                    clean_date,
                    clean_day_status,
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
                        timezone_value,
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
            if competition_id and clean_date:
                start_date = str(parent["start_date"] or "")
                end_date = str(parent["end_date"] or "")
                expanded_start = min(
                    value for value in (start_date, clean_date) if value
                )
                expanded_end = max(
                    value for value in (end_date, clean_date) if value
                )
                conn.execute(
                    """
                    UPDATE competitions
                    SET start_date = ?, end_date = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (expanded_start, expanded_end, now, resolved_competition_id),
                )
            self._sync_staff_records(
                conn,
                meet_id=meet_id,
                competition_id=resolved_competition_id,
                coaches=coaches,
                coordinators=coords,
                actor="Create day",
            )
            conn.commit()
        return self.get_meet(meet_id)  # type: ignore[return-value]

    def create_competition_day(
        self,
        competition_id: str,
        name: str,
        competition_date: str,
        active_coaches: Iterable[str],
        coordinators: Iterable[str] = ("Irina",),
        *,
        first_event_name: str | None = None,
        day_status: str = "scheduled",
    ) -> dict[str, Any]:
        parent = self.get_competition(competition_id)
        if parent is None:
            raise CompCoachError("Competition not found.")
        return self.create_meet(
            name,
            active_coaches,
            coordinators,
            str(parent["timezone"]),
            first_event_name=first_event_name,
            competition_date=competition_date,
            competition_id=competition_id,
            day_status=day_status,
        )

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
            self._delete_removed_coach_availability(conn, meet_id, coaches)
            self._sync_staff_records(
                conn,
                meet_id=meet_id,
                competition_id=str(meet["competition_id"] or ""),
                coaches=coaches,
                coordinators=coords,
                actor="Staff update",
                replace_day=True,
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

    def set_day_status(
        self,
        meet_id: str,
        day_status: str,
        actor: str,
    ) -> dict[str, Any]:
        """Schedule, activate, or close one competition day atomically."""

        clean_status = str(day_status or "").strip().lower()
        clean_actor = str(actor or "").strip()
        if clean_status not in {"scheduled", "active", "closed"}:
            raise ValueError("Day status must be scheduled, active, or closed.")
        if not clean_actor:
            raise ValueError("Actor is required.")
        if clean_status == "closed":
            return self.finish_meet(meet_id, clean_actor)

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = conn.execute(
                "SELECT * FROM meets WHERE id = ?", (meet_id,)
            ).fetchone()
            if meet is None:
                raise CompCoachError("Competition group not found.")
            if meet["ended_at"] or meet["day_status"] == "closed":
                raise CompCoachError("A closed competition day cannot be reactivated.")
            now = utc_now()
            if clean_status == "active":
                conn.execute(
                    """
                    UPDATE meets SET day_status = 'scheduled', updated_at = ?
                    WHERE competition_id = ? AND day_status = 'active' AND id != ?
                    """,
                    (now, meet["competition_id"], meet_id),
                )
            conn.execute(
                """
                UPDATE meets SET day_status = ?, status = 'open', updated_at = ?
                WHERE id = ?
                """,
                (clean_status, now, meet_id),
            )
            conn.execute(
                """
                UPDATE events SET status = 'open', updated_at = ?
                WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)
                """,
                (now, meet_id),
            )
            conn.commit()
        return self.get_meet(meet_id)  # type: ignore[return-value]

    def get_active_competition_day(
        self, competition_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT meets.*,
                       (SELECT COUNT(*) FROM meet_events
                        WHERE meet_events.meet_id = meets.id) AS event_count
                FROM meets
                WHERE competition_id = ? AND day_status = 'active'
                LIMIT 1
                """,
                (competition_id,),
            ).fetchone()
        return self._meet_dict(row)

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
                conn.execute(
                    "UPDATE meets SET status = 'locked', day_status = 'closed' WHERE id = ?",
                    (meet_id,),
                )
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
                    day_status = 'closed', updated_at = ? WHERE id = ?
                """,
                (now, clean_actor, now, meet_id),
            )
            conn.execute(
                """
                UPDATE coach_assignment_history
                SET ended_at = ?, ended_by = ?
                WHERE meet_id = ? AND ended_at IS NULL
                """,
                (now, clean_actor, meet_id),
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
                    """
                    UPDATE meets SET status = 'locked', day_status = 'closed'
                    WHERE id = ?
                    """,
                    (source_meet_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE meets SET status = 'locked', ended_at = ?, ended_by = ?,
                        day_status = 'closed', updated_at = ? WHERE id = ?
                    """,
                    (now, clean_actor, now, source_meet_id),
                )
            conn.execute(
                """
                UPDATE coach_assignment_history
                SET ended_at = ?, ended_by = ?
                WHERE meet_id = ? AND ended_at IS NULL
                """,
                (now, clean_actor, source_meet_id),
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
                    id, competition_id, name, timezone, status, active_coaches_json,
                    coordinators_json, admin_token, coordinator_token,
                    coach_token, ended_at, ended_by, prepared_from_meet_id,
                    competition_date, day_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, NULL, '', ?, ?, 'active', ?, ?)
                """,
                (
                    next_meet_id,
                    source["competition_id"],
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
            self._sync_staff_records(
                conn,
                meet_id=next_meet_id,
                competition_id=str(source["competition_id"] or ""),
                coaches=_load(source["active_coaches_json"], []),
                coordinators=_load(source["coordinators_json"], []),
                actor=clean_actor,
            )
            parent = conn.execute(
                "SELECT start_date, end_date FROM competitions WHERE id = ?",
                (source["competition_id"],),
            ).fetchone()
            if parent is not None:
                dates = [
                    value
                    for value in (
                        str(parent["start_date"] or ""),
                        str(parent["end_date"] or ""),
                        clean_date,
                    )
                    if value
                ]
                conn.execute(
                    """
                    UPDATE competitions SET start_date = ?, end_date = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (min(dates), max(dates), now, source["competition_id"]),
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
                self._delete_removed_coach_availability(conn, meet_id, coaches)
                competition = conn.execute(
                    "SELECT competition_id FROM meets WHERE id = ?", (meet_id,)
                ).fetchone()
                self._sync_staff_records(
                    conn,
                    meet_id=meet_id,
                    competition_id=str(competition["competition_id"] or ""),
                    coaches=coaches,
                    coordinators=coords,
                    actor="Staff update",
                    replace_day=True,
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

    @staticmethod
    def _pool_wave_sort_key(label: str) -> tuple[int, int, str]:
        clean = " ".join(str(label or "").split())
        for pattern in ("%I:%M %p", "%I %p", "%H:%M", "%H%M"):
            try:
                parsed = datetime.strptime(clean.upper(), pattern).replace(
                    tzinfo=timezone.utc
                )
                return (0, parsed.hour * 60 + parsed.minute, clean.casefold())
            except ValueError:
                continue
        return (1, 0, clean.casefold())

    @classmethod
    def _sync_pool_waves(
        cls,
        conn: sqlite3.Connection,
        event_id: str,
        records: Iterable[Mapping[str, Any]],
    ) -> None:
        labels = list(
            dict.fromkeys(
                " ".join(str(record.get("time") or "").split())
                for record in records
                if str(record.get("phase") or "pools").strip().lower() == "pools"
                and str(record.get("time") or "").strip()
            )
        )
        if not labels:
            return
        now = utc_now()
        for label in labels:
            conn.execute(
                """
                INSERT OR IGNORE INTO pool_waves (
                    event_id, wave_key, label, sort_order, is_active,
                    is_visible, activated_at, activated_by, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 0, 0, 0, NULL, '', 0, ?, ?)
                """,
                (event_id, _normalize_name(label), label, now, now),
            )

        rows = conn.execute(
            "SELECT * FROM pool_waves WHERE event_id = ?", (event_id,)
        ).fetchall()
        ordered = sorted(rows, key=lambda row: cls._pool_wave_sort_key(row["label"]))
        for index, row in enumerate(ordered):
            conn.execute(
                """
                UPDATE pool_waves SET sort_order = ?, updated_at = ?
                WHERE event_id = ? AND wave_key = ?
                """,
                (index, now, event_id, row["wave_key"]),
            )

        manually_selected = any(
            int(row["version"]) > 0 and str(row["activated_by"] or "") != "Import"
            for row in rows
        )
        if not manually_selected and ordered:
            first_key = str(ordered[0]["wave_key"])
            conn.execute(
                """
                UPDATE pool_waves SET is_active = 0, is_visible = 0,
                    activated_at = NULL, activated_by = '', updated_at = ?
                WHERE event_id = ?
                """,
                (now, event_id),
            )
            conn.execute(
                """
                UPDATE pool_waves SET is_active = 1, is_visible = 1,
                    activated_at = COALESCE(activated_at, ?),
                    activated_by = 'Import', updated_at = ?
                WHERE event_id = ? AND wave_key = ?
                """,
                (now, now, event_id, first_key),
            )

    def list_pool_waves(self, event_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM events WHERE id = ?", (event_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            rows = conn.execute(
                """
                SELECT * FROM pool_waves
                WHERE event_id = ? ORDER BY sort_order, wave_key
                """,
                (event_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "is_active": bool(row["is_active"]),
                "is_visible": bool(row["is_visible"]),
            }
            for row in rows
        ]

    def set_pool_wave(
        self,
        event_id: str,
        wave_key: str,
        actor: str,
        *,
        active: bool | None = None,
        visible: bool | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("Actor is required.")
        if active is not None and not isinstance(active, bool):
            raise TypeError("Active must be true, false, or omitted.")
        if visible is not None and not isinstance(visible, bool):
            raise TypeError("Visible must be true, false, or omitted.")
        if active is None and visible is None:
            raise ValueError("Choose an active or visible wave change.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            row = conn.execute(
                """
                SELECT * FROM pool_waves
                WHERE event_id = ? AND wave_key = ?
                """,
                (event_id, wave_key),
            ).fetchone()
            if row is None:
                raise CompCoachError("Pool wave not found.")
            if expected_version is not None and int(row["version"]) != int(
                expected_version
            ):
                raise ConcurrentUpdateError(
                    "This pool wave changed on another phone. Refresh and try again."
                )
            now = utc_now()
            if active is True:
                # Every row advances so a stale activation snapshot for any
                # other wave is rejected after this switch.
                conn.execute(
                    """
                    UPDATE pool_waves SET is_active = 0, is_visible = 0,
                        version = version + 1, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (now, event_id),
                )
                conn.execute(
                    """
                    UPDATE pool_waves SET is_active = 1, is_visible = 1,
                        activated_at = ?, activated_by = ?, updated_at = ?
                    WHERE event_id = ? AND wave_key = ?
                    """,
                    (now, clean_actor, now, event_id, wave_key),
                )
            else:
                new_active = bool(row["is_active"]) if active is None else active
                new_visible = (
                    bool(row["is_visible"]) if visible is None else visible
                )
                conn.execute(
                    """
                    UPDATE pool_waves SET is_active = ?, is_visible = ?,
                        activated_at = CASE WHEN ? THEN COALESCE(activated_at, ?)
                                            ELSE activated_at END,
                        activated_by = CASE WHEN ? THEN ? ELSE activated_by END,
                        version = version + 1, updated_at = ?
                    WHERE event_id = ? AND wave_key = ?
                    """,
                    (
                        int(new_active),
                        int(new_visible),
                        int(new_active),
                        now,
                        int(new_active),
                        clean_actor,
                        now,
                        event_id,
                        wave_key,
                    ),
                )
            conn.commit()
        return next(
            wave for wave in self.list_pool_waves(event_id) if wave["wave_key"] == wave_key
        )

    def activate_pool_wave(
        self,
        event_id: str,
        wave_key: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self.set_pool_wave(
            event_id,
            wave_key,
            actor,
            active=True,
            expected_version=expected_version,
        )

    def create_coach(self, name: str, *, active: bool = True) -> dict[str, Any]:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("Coach name is required.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM coaches WHERE normalized_name = ?",
                (_normalize_name(clean_name),),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE coaches SET is_active = ?, updated_at = ? WHERE id = ?",
                    (int(active), utc_now(), existing["id"]),
                )
                coach_id = str(existing["id"])
            else:
                coach = self._ensure_coach_record(conn, clean_name)
                coach_id = str(coach["id"])
                if not active:
                    conn.execute(
                        "UPDATE coaches SET is_active = 0 WHERE id = ?", (coach_id,)
                    )
            conn.commit()
        return self.get_coach(coach_id)  # type: ignore[return-value]

    def get_coach(self, coach_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM coaches WHERE id = ?", (coach_id,)
            ).fetchone()
        return self._coach_dict(row)

    def list_coaches(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM coaches"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name COLLATE NOCASE, id"
        with self._connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._coach_dict(row) for row in rows if row is not None]  # type: ignore[misc]

    def update_coach(
        self,
        coach_id: str,
        *,
        name: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM coaches WHERE id = ?", (coach_id,)
                ).fetchone()
                if row is None:
                    raise CompCoachError("Coach not found.")
                old_name = str(row["name"])
                clean_name = old_name
                if name is not None:
                    clean_name = " ".join(str(name or "").split())
                    if not clean_name:
                        raise ValueError("Coach name is required.")
                active_value = (
                    bool(row["is_active"]) if active is None else bool(active)
                )
                conn.execute(
                    """
                    UPDATE coaches SET name = ?, normalized_name = ?,
                        is_active = ?, updated_at = ? WHERE id = ?
                    """,
                    (
                        clean_name,
                        _normalize_name(clean_name),
                        int(active_value),
                        utc_now(),
                        coach_id,
                    ),
                )
                if clean_name != old_name:
                    self._rename_current_coach_references(
                        conn,
                        coach_id=coach_id,
                        old_name=old_name,
                        new_name=clean_name,
                    )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise CompCoachError("A coach with this name already exists.") from exc
            except Exception:
                conn.rollback()
                raise
        return self.get_coach(coach_id)  # type: ignore[return-value]

    @staticmethod
    def _renamed_coach_list(
        raw_value: str | None,
        *,
        old_name: str,
        new_name: str,
    ) -> list[str]:
        """Replace one directory name in a legacy staff projection.

        Staff lists pre-date the durable coach directory and therefore store
        display names rather than coach IDs.  Normalized comparison catches
        harmless case/spacing drift, while normalized de-duplication prevents
        a rename from leaving two visually identical options in the UI.
        """

        old_key = _normalize_name(old_name)
        result: list[str] = []
        seen: set[str] = set()
        for value in _load(raw_value, []):
            candidate = " ".join(str(value or "").split())
            if not candidate:
                continue
            if _normalize_name(candidate) == old_key:
                candidate = new_name
            key = _normalize_name(candidate)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    @staticmethod
    def _staff_list_with_name(
        raw_value: str | None,
        *,
        coach_name: str,
        include: bool,
    ) -> list[str]:
        """Return a normalized staff projection with one canonical name toggled."""

        coach_key = _normalize_name(coach_name)
        result: list[str] = []
        seen: set[str] = set()
        for value in _load(raw_value, []):
            candidate = " ".join(str(value or "").split())
            if not candidate or _normalize_name(candidate) == coach_key:
                continue
            key = _normalize_name(candidate)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        if include:
            result.append(coach_name)
        return result

    @classmethod
    def _rename_current_coach_references(
        cls,
        conn: sqlite3.Connection,
        *,
        coach_id: str,
        old_name: str,
        new_name: str,
    ) -> None:
        """Rename denormalized *current* references without rewriting audit data.

        The coach directory and assignment history use a stable coach ID, but
        the original operational model stores display names in several current
        state columns.  Updating them in the same transaction prevents a rename
        from splitting one person into two identities in the live UI.
        """

        old_key = _normalize_name(old_name)
        new_key = _normalize_name(new_name)
        now = utc_now()

        # A day-presence record is the durable registration for one coach on a
        # competition day.  Keep both meet and child-event JSON projections in
        # sync, including coordinator-only registrations.
        registered_meet_ids = [
            str(row["meet_id"])
            for row in conn.execute(
                """
                SELECT meet_id FROM day_coach_presence
                WHERE coach_id = ?
                ORDER BY meet_id
                """,
                (coach_id,),
            ).fetchall()
        ]
        for meet_id in registered_meet_ids:
            meet = conn.execute(
                """
                SELECT active_coaches_json, coordinators_json
                FROM meets WHERE id = ?
                """,
                (meet_id,),
            ).fetchone()
            if meet is not None:
                active_coaches = cls._renamed_coach_list(
                    meet["active_coaches_json"],
                    old_name=old_name,
                    new_name=new_name,
                )
                coordinators = cls._renamed_coach_list(
                    meet["coordinators_json"],
                    old_name=old_name,
                    new_name=new_name,
                )
                conn.execute(
                    """
                    UPDATE meets
                    SET active_coaches_json = ?, coordinators_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (_dump(active_coaches), _dump(coordinators), now, meet_id),
                )

            child_events = conn.execute(
                """
                SELECT events.id, events.active_coaches_json,
                       events.coordinators_json
                FROM events
                JOIN meet_events ON meet_events.event_id = events.id
                WHERE meet_events.meet_id = ?
                """,
                (meet_id,),
            ).fetchall()
            for event in child_events:
                active_coaches = cls._renamed_coach_list(
                    event["active_coaches_json"],
                    old_name=old_name,
                    new_name=new_name,
                )
                coordinators = cls._renamed_coach_list(
                    event["coordinators_json"],
                    old_name=old_name,
                    new_name=new_name,
                )
                conn.execute(
                    """
                    UPDATE events
                    SET active_coaches_json = ?, coordinators_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        _dump(active_coaches),
                        _dump(coordinators),
                        now,
                        event["id"],
                    ),
                )

        # These athlete columns are live operational state.  Reporter/result
        # fields, actions, and action JSON are deliberately not included: they
        # are historical audit evidence and retain the display name used then.
        athlete_fields = (
            "main_coach",
            "side_coach",
            "covered_by",
            "help_requested_by",
            "help_acknowledged_by",
        )
        athletes = conn.execute(
            f"SELECT id, {', '.join(athlete_fields)} FROM athletes"
        ).fetchall()
        for athlete in athletes:
            changed_fields = [
                field
                for field in athlete_fields
                if _normalize_name(str(athlete[field] or "")) == old_key
            ]
            if not changed_fields:
                continue
            set_clause = ", ".join(f"{field} = ?" for field in changed_fields)
            conn.execute(
                f"""
                UPDATE athletes SET {set_clause}, version = version + 1,
                    updated_at = ? WHERE id = ?
                """,
                (*([new_name] * len(changed_fields)), now, athlete["id"]),
            )

        pod_fields = ("main_coach", "side_coach")
        assignments = conn.execute(
            """
            SELECT event_id, phase, pod, main_coach, side_coach
            FROM pod_assignments
            """
        ).fetchall()
        for assignment in assignments:
            changed_fields = [
                field
                for field in pod_fields
                if _normalize_name(str(assignment[field] or "")) == old_key
            ]
            if not changed_fields:
                continue
            set_clause = ", ".join(f"{field} = ?" for field in changed_fields)
            conn.execute(
                f"""
                UPDATE pod_assignments SET {set_clause}
                WHERE event_id = ? AND phase = ? AND pod = ?
                """,
                (
                    *([new_name] * len(changed_fields)),
                    assignment["event_id"],
                    assignment["phase"],
                    assignment["pod"],
                ),
            )

        # Open intervals describe the current assignment, so their display
        # label follows the canonical name while the stable ID and interval
        # boundaries remain untouched.  Closed intervals stay historical.
        conn.execute(
            """
            UPDATE coach_assignment_history SET coach_name = ?
            WHERE coach_id = ? AND ended_at IS NULL
            """,
            (new_name, coach_id),
        )

        # coach_name is part of this table's primary key.  Reinsert under the
        # new key rather than recreating availability: state, timestamps and
        # the CAS version are preserved exactly.  A stale target-name row is
        # consolidated so the board cannot expose duplicate identities.
        availability_rows = conn.execute(
            "SELECT * FROM coach_availability ORDER BY meet_id, coach_name"
        ).fetchall()
        source_by_meet: dict[str, list[sqlite3.Row]] = {}
        for availability in availability_rows:
            if _normalize_name(str(availability["coach_name"])) == old_key:
                source_by_meet.setdefault(str(availability["meet_id"]), []).append(
                    availability
                )
        for meet_id, sources in source_by_meet.items():
            source = max(
                sources,
                key=lambda item: (
                    int(item["version"]),
                    str(item["updated_at"] or ""),
                ),
            )
            conflicting_names = [
                str(item["coach_name"])
                for item in availability_rows
                if str(item["meet_id"]) == meet_id
                and _normalize_name(str(item["coach_name"])) in {old_key, new_key}
            ]
            conn.executemany(
                """
                DELETE FROM coach_availability
                WHERE meet_id = ? AND coach_name = ?
                """,
                [(meet_id, conflict_name) for conflict_name in conflicting_names],
            )
            conn.execute(
                """
                INSERT INTO coach_availability (
                    meet_id, coach_name, is_available, available_since,
                    updated_at, updated_by, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meet_id,
                    new_name,
                    source["is_available"],
                    source["available_since"],
                    source["updated_at"],
                    source["updated_by"],
                    source["version"],
                ),
            )

    def set_competition_coach_roles(
        self,
        competition_id: str,
        coach_id: str,
        roles: Iterable[str],
    ) -> dict[str, Any]:
        clean_roles = list(
            dict.fromkeys(str(role or "").strip().lower() for role in roles)
        )
        if not clean_roles or any(
            role not in {"coach", "coordinator", "admin"} for role in clean_roles
        ):
            raise ValueError("Roles must contain coach, coordinator, or admin.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            if not conn.execute(
                "SELECT 1 FROM coaches WHERE id = ?", (coach_id,)
            ).fetchone():
                raise CompCoachError("Coach not found.")
            conn.execute(
                """
                DELETE FROM competition_coaches
                WHERE competition_id = ? AND coach_id = ?
                """,
                (competition_id, coach_id),
            )
            now = utc_now()
            conn.executemany(
                """
                INSERT INTO competition_coaches (
                    competition_id, coach_id, role, added_at
                ) VALUES (?, ?, ?, ?)
                """,
                [(competition_id, coach_id, role, now) for role in clean_roles],
            )

            # Coordinator links are day-operational.  A competition role makes
            # the person eligible, while day presence decides whether their
            # name is exposed for that day.  Keep legacy meet/event projections
            # aligned without changing the independently managed coach list.
            coordinator_enabled = "coordinator" in clean_roles
            coach = conn.execute(
                "SELECT name FROM coaches WHERE id = ?", (coach_id,)
            ).fetchone()
            day_rows = conn.execute(
                """
                SELECT meets.id, meets.coordinators_json,
                       day_coach_presence.presence_status
                FROM meets
                JOIN day_coach_presence
                  ON day_coach_presence.meet_id = meets.id
                WHERE meets.competition_id = ?
                  AND day_coach_presence.coach_id = ?
                """,
                (competition_id, coach_id),
            ).fetchall()
            for day in day_rows:
                coordinators = self._staff_list_with_name(
                    day["coordinators_json"],
                    coach_name=str(coach["name"]),
                    include=(
                        coordinator_enabled
                        and str(day["presence_status"]) == "present"
                    ),
                )
                conn.execute(
                    """
                    UPDATE meets SET coordinators_json = ? WHERE id = ?
                    """,
                    (_dump(coordinators), day["id"]),
                )
                conn.execute(
                    """
                    UPDATE events SET coordinators_json = ?
                    WHERE id IN (
                        SELECT event_id FROM meet_events WHERE meet_id = ?
                    )
                    """,
                    (_dump(coordinators), day["id"]),
                )
            conn.commit()
        return next(
            row
            for row in self.list_competition_coaches(competition_id)
            if row["coach_id"] == coach_id
        )

    def list_competition_coaches(
        self, competition_id: str
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone():
                raise CompCoachError("Competition not found.")
            rows = conn.execute(
                """
                SELECT coaches.*, competition_coaches.role,
                       competition_coaches.added_at
                FROM competition_coaches
                JOIN coaches ON coaches.id = competition_coaches.coach_id
                WHERE competition_coaches.competition_id = ?
                ORDER BY coaches.name COLLATE NOCASE, competition_coaches.role
                """,
                (competition_id,),
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            coach_id = str(row["id"])
            if coach_id not in result:
                result[coach_id] = {
                    "competition_id": competition_id,
                    "coach_id": coach_id,
                    "name": row["name"],
                    "is_active": bool(row["is_active"]),
                    "roles": [],
                    "added_at": row["added_at"],
                }
            result[coach_id]["roles"].append(str(row["role"]))
        return list(result.values())

    def set_day_coach_presence(
        self,
        meet_id: str,
        coach_id: str,
        present: bool | None = None,
        actor: str = "",
        *,
        presence_status: str | None = None,
        home_event_id: object = NO_CHANGE,
    ) -> dict[str, Any]:
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("Actor is required.")
        if presence_status is None:
            if not isinstance(present, bool):
                raise TypeError("Present must be true or false.")
            status = "present" if present else "absent"
        else:
            status = str(presence_status or "").strip().lower()
            if status not in {"scheduled", "present", "absent"}:
                raise ValueError("Presence status must be scheduled, present, or absent.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = self._assert_meet_open(conn, meet_id)
            coach = conn.execute(
                "SELECT * FROM coaches WHERE id = ?", (coach_id,)
            ).fetchone()
            if coach is None:
                raise CompCoachError("Coach not found.")
            existing = conn.execute(
                """
                SELECT * FROM day_coach_presence
                WHERE meet_id = ? AND coach_id = ?
                """,
                (meet_id, coach_id),
            ).fetchone()
            selected_home = existing["home_event_id"] if existing else None
            if home_event_id is not NO_CHANGE:
                selected_home = str(home_event_id or "").strip() or None
                if selected_home and not conn.execute(
                    """
                    SELECT 1 FROM meet_events
                    WHERE meet_id = ? AND event_id = ?
                    """,
                    (meet_id, selected_home),
                ).fetchone():
                    raise CompCoachError(
                        "The home event must belong to this competition day."
                    )
            now = utc_now()
            conn.execute(
                """
                INSERT INTO day_coach_presence (
                    meet_id, coach_id, presence_status, home_event_id,
                    updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(meet_id, coach_id) DO UPDATE SET
                    presence_status = excluded.presence_status,
                    home_event_id = excluded.home_event_id,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (meet_id, coach_id, status, selected_home, now, clean_actor),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO competition_coaches (
                    competition_id, coach_id, role, added_at
                ) VALUES (?, ?, 'coach', ?)
                """,
                (meet["competition_id"], coach_id, now),
            )

            active_names = [
                str(value).strip()
                for value in _load(meet["active_coaches_json"], [])
                if str(value).strip()
            ]
            coach_name = str(coach["name"])
            active_names = self._staff_list_with_name(
                _dump(active_names),
                coach_name=coach_name,
                include=status == "present",
            )
            coordinator_role = conn.execute(
                """
                SELECT 1 FROM competition_coaches
                WHERE competition_id = ? AND coach_id = ?
                  AND role = 'coordinator'
                """,
                (meet["competition_id"], coach_id),
            ).fetchone()
            coordinator_names = self._staff_list_with_name(
                meet["coordinators_json"],
                coach_name=coach_name,
                include=status == "present" and coordinator_role is not None,
            )
            conn.execute(
                """
                UPDATE meets SET active_coaches_json = ?, coordinators_json = ?,
                    updated_at = ? WHERE id = ?
                """,
                (_dump(active_names), _dump(coordinator_names), now, meet_id),
            )
            conn.execute(
                """
                UPDATE events SET active_coaches_json = ?, coordinators_json = ?,
                    updated_at = ?
                WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)
                """,
                (
                    _dump(active_names),
                    _dump(coordinator_names),
                    now,
                    meet_id,
                ),
            )
            conn.commit()
        return next(
            row for row in self.list_day_coaches(meet_id) if row["coach_id"] == coach_id
        )

    def list_day_coaches(
        self,
        meet_id: str,
        *,
        present_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM meets WHERE id = ?", (meet_id,)
            ).fetchone():
                raise CompCoachError("Competition group not found.")
            sql = """
                SELECT coaches.id AS coach_id, coaches.name, coaches.is_active,
                       day_coach_presence.presence_status,
                       day_coach_presence.home_event_id,
                       day_coach_presence.updated_at,
                       day_coach_presence.updated_by,
                       events.name AS home_event_name,
                       (SELECT COUNT(*) FROM coach_assignment_history AS history
                        WHERE history.meet_id = day_coach_presence.meet_id
                          AND history.coach_id = day_coach_presence.coach_id
                          AND history.ended_at IS NULL) AS assignment_count
                FROM day_coach_presence
                JOIN coaches ON coaches.id = day_coach_presence.coach_id
                LEFT JOIN events ON events.id = day_coach_presence.home_event_id
                WHERE day_coach_presence.meet_id = ?
            """
            params: list[Any] = [meet_id]
            if present_only:
                sql += " AND day_coach_presence.presence_status = 'present'"
            sql += " ORDER BY assignment_count > 0, coaches.name COLLATE NOCASE"
            rows = conn.execute(sql, params).fetchall()
            role_rows = conn.execute(
                """
                SELECT competition_coaches.coach_id, competition_coaches.role
                FROM competition_coaches
                JOIN meets ON meets.competition_id = competition_coaches.competition_id
                WHERE meets.id = ?
                ORDER BY competition_coaches.role
                """,
                (meet_id,),
            ).fetchall()
        roles_by_coach: dict[str, list[str]] = {}
        for role in role_rows:
            roles_by_coach.setdefault(str(role["coach_id"]), []).append(
                str(role["role"])
            )
        return [
            {
                **dict(row),
                "is_active": bool(row["is_active"]),
                "is_present": row["presence_status"] == "present",
                "is_used": int(row["assignment_count"]) > 0,
                "assignment_count": int(row["assignment_count"]),
                "roles": roles_by_coach.get(str(row["coach_id"]), []),
            }
            for row in rows
        ]

    def list_assignment_history(
        self,
        *,
        competition_id: str | None = None,
        meet_id: str | None = None,
        event_id: str | None = None,
        athlete_id: str | None = None,
        coach_id: str | None = None,
        include_closed: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("competition_id", competition_id),
            ("meet_id", meet_id),
            ("event_id", event_id),
            ("athlete_id", athlete_id),
            ("coach_id", coach_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if not include_closed:
            clauses.append("ended_at IS NULL")
        sql = "SELECT * FROM coach_assignment_history"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at, id"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {**dict(row), "is_cross_event": bool(row["is_cross_event"])}
            for row in rows
        ]

    def list_coach_availability(self, meet_id: str) -> list[dict[str, Any]]:
        """Return explicit availability and computed workload for active coaches."""

        with self._connection() as conn:
            meet = conn.execute(
                "SELECT * FROM meets WHERE id = ?", (meet_id,)
            ).fetchone()
            if meet is None:
                raise CompCoachError("Competition group not found.")
            coaches = [
                str(coach).strip()
                for coach in _load(meet["active_coaches_json"], [])
                if str(coach).strip()
            ]
            stored_rows = conn.execute(
                """
                SELECT * FROM coach_availability
                WHERE meet_id = ?
                """,
                (meet_id,),
            ).fetchall()
            stored = {str(row["coach_name"]): dict(row) for row in stored_rows}
            athlete_rows = conn.execute(
                """
                SELECT athletes.*
                FROM athletes
                JOIN meet_events ON meet_events.event_id = athletes.event_id
                WHERE meet_events.meet_id = ?
                """,
                (meet_id,),
            ).fetchall()
            athletes = [dict(row) for row in athlete_rows]

        result: list[dict[str, Any]] = []
        for coach in coaches:
            assigned_ids: set[str] = set()
            temporary_ids: set[str] = set()
            unfinished_ids: set[str] = set()
            for athlete in athletes:
                athlete_id = str(athlete["id"])
                planned = coach in {
                    athlete.get("main_coach"),
                    athlete.get("side_coach"),
                }
                active = (
                    athlete.get("active_state") == "active"
                    and athlete.get("participation_status", "active") == "active"
                )
                unresolved_help = bool(athlete.get("help_requested_at"))
                temporary = active and (
                    athlete.get("covered_by") == coach
                    or (
                        unresolved_help
                        and coach
                        in {
                            athlete.get("help_requested_by"),
                            athlete.get("help_acknowledged_by"),
                        }
                    )
                )
                if planned:
                    assigned_ids.add(athlete_id)
                elif temporary:
                    temporary_ids.add(athlete_id)

                if temporary:
                    unfinished_ids.add(athlete_id)
                if not planned or not active:
                    continue
                if athlete.get("phase") == "de":
                    unfinished_ids.add(athlete_id)
                    continue
                pool_complete = (
                    athlete.get("pool_wins") is not None
                    and athlete.get("pool_losses") is not None
                )
                if (
                    not pool_complete
                    or athlete.get("call_status") != "waiting"
                    or unresolved_help
                ):
                    unfinished_ids.add(athlete_id)

            row = stored.get(coach)
            is_available = bool(row["is_available"]) if row else False
            assigned_count = len(assigned_ids)
            unfinished_count = len(unfinished_ids)
            result.append(
                {
                    "meet_id": meet_id,
                    "coach_name": coach,
                    "is_available": is_available,
                    "available_since": row["available_since"] if row else None,
                    "updated_at": row["updated_at"] if row else None,
                    "updated_by": row["updated_by"] if row else "",
                    "version": int(row["version"]) if row else 0,
                    "assigned_count": assigned_count,
                    "temporary_count": len(temporary_ids),
                    "unfinished_count": unfinished_count,
                    "suggested_available": bool(
                        assigned_count and unfinished_count == 0
                    ),
                }
            )
        return result

    def set_coach_availability(
        self,
        meet_id: str,
        coach: str,
        available: bool,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Persist one coach's meet-wide availability with an optional CAS guard."""

        coach = str(coach or "").strip()
        actor = str(actor or "").strip()
        if not coach:
            raise ValueError("Coach is required.")
        if not actor:
            raise ValueError("Actor is required.")
        if not isinstance(available, bool):
            raise TypeError("Available must be true or false.")
        if expected_version is not None and (
            isinstance(expected_version, bool) or not isinstance(expected_version, int)
        ):
            raise TypeError("Expected version must be a whole number or omitted.")
        if expected_version is not None and expected_version < 0:
            raise ValueError("Expected version cannot be negative.")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meet = self._assert_meet_open(conn, meet_id)
            active_coaches = {
                str(name).strip()
                for name in _load(meet["active_coaches_json"], [])
                if str(name).strip()
            }
            if coach not in active_coaches:
                raise CompCoachError(f"{coach} is not an active coach for this competition.")
            row = conn.execute(
                """
                SELECT * FROM coach_availability
                WHERE meet_id = ? AND coach_name = ?
                """,
                (meet_id, coach),
            ).fetchone()
            current_version = int(row["version"]) if row else 0
            current_available = bool(row["is_available"]) if row else False
            if expected_version is not None and current_version != expected_version:
                raise ConcurrentUpdateError(
                    "This coach's availability changed on another phone. "
                    "The board has been refreshed; please try again."
                )
            if current_available is available:
                conn.commit()
                return {
                    "meet_id": meet_id,
                    "coach_name": coach,
                    "is_available": current_available,
                    "available_since": row["available_since"] if row else None,
                    "updated_at": row["updated_at"] if row else None,
                    "updated_by": row["updated_by"] if row else "",
                    "version": current_version,
                }

            now = utc_now()
            new_version = current_version + 1
            available_since = now if available else None
            conn.execute(
                """
                INSERT INTO coach_availability (
                    meet_id, coach_name, is_available, available_since,
                    updated_at, updated_by, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meet_id, coach_name) DO UPDATE SET
                    is_available = excluded.is_available,
                    available_since = excluded.available_since,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    version = excluded.version
                """,
                (
                    meet_id,
                    coach,
                    int(available),
                    available_since,
                    now,
                    actor,
                    new_version,
                ),
            )
            conn.execute(
                "UPDATE meets SET updated_at = ? WHERE id = ?", (now, meet_id)
            )
            conn.commit()
        return {
            "meet_id": meet_id,
            "coach_name": coach,
            "is_available": available,
            "available_since": available_since,
            "updated_at": now,
            "updated_by": actor,
            "version": new_version,
        }

    def list_pod_assignments(
        self, event_id: str, phase: str | None = None
    ) -> list[dict[str, Any]]:
        if phase is not None and phase not in {"pools", "de"}:
            raise ValueError("Phase must be pools, de, or omitted.")
        sql = "SELECT * FROM pod_assignments WHERE event_id = ?"
        params: list[Any] = [event_id]
        if phase is not None:
            sql += " AND phase = ?"
            params.append(phase)
        sql += " ORDER BY phase, pod"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def deploy_available_coach_to_pod(
        self,
        event_id: str,
        *,
        pod: str,
        coach: str,
        actor: str,
        expected_availability_version: int,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Atomically consume availability and add a DE Side/support coach."""

        pod = str(pod or "").strip().upper()
        coach = str(coach or "").strip()
        actor = str(actor or "").strip()
        if not pod:
            raise ValueError("Pod is required.")
        if not coach:
            raise ValueError("Coach is required.")
        if not actor:
            raise ValueError("Actor is required.")
        if isinstance(expected_availability_version, bool) or not isinstance(
            expected_availability_version, int
        ):
            raise TypeError("Expected availability version must be a whole number.")
        if expected_availability_version < 0:
            raise ValueError("Expected availability version cannot be negative.")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_open(conn, event_id)
            meet = self._meet_for_event(conn, event_id)
            active_coaches = {
                str(name).strip()
                for name in _load(meet["active_coaches_json"], [])
                if str(name).strip()
            }
            if coach not in active_coaches:
                raise CompCoachError(f"{coach} is not an active coach for this competition.")

            availability = conn.execute(
                """
                SELECT * FROM coach_availability
                WHERE meet_id = ? AND coach_name = ?
                """,
                (meet["id"], coach),
            ).fetchone()
            current_version = int(availability["version"]) if availability else 0
            if current_version != expected_availability_version:
                raise ConcurrentUpdateError(
                    "This coach's availability changed on another phone. "
                    "Review the situation and try again."
                )
            if availability is None or not bool(availability["is_available"]):
                raise ConcurrentUpdateError(
                    f"{coach} is no longer marked available."
                )

            assignment = conn.execute(
                """
                SELECT * FROM pod_assignments
                WHERE event_id = ? AND phase = 'de' AND pod = ?
                """,
                (event_id, pod),
            ).fetchone()
            previous_main = str(assignment["main_coach"] or "") if assignment else ""
            previous_side = str(assignment["side_coach"] or "") if assignment else ""
            if previous_main == coach:
                raise CompCoachError(f"{coach} is already the Main coach for Pod {pod}.")
            if previous_side and previous_side != coach and not replace_existing:
                raise CompCoachError(
                    f"Pod {pod} already has Side coach {previous_side}. "
                    "Confirm replacement before deploying another coach."
                )

            rows = conn.execute(
                """
                SELECT * FROM athletes
                WHERE event_id = ? AND phase = 'de' AND pod = ?
                  AND active_state = 'active' AND participation_status = 'active'
                ORDER BY id
                """,
                (event_id, pod),
            ).fetchall()
            if not rows:
                raise CompCoachError(
                    f"Pod {pod} has no active Direct Elimination athletes."
                )

            now = utc_now()
            conn.execute(
                """
                INSERT INTO pod_assignments (
                    event_id, phase, pod, main_coach, side_coach, updated_at
                ) VALUES (?, 'de', ?, ?, ?, ?)
                ON CONFLICT(event_id, phase, pod) DO UPDATE SET
                    side_coach = excluded.side_coach,
                    updated_at = excluded.updated_at
                """,
                (event_id, pod, previous_main, coach, now),
            )
            self._sync_assignment_slot(
                conn,
                event_id=event_id,
                athlete_id=None,
                target_type="pod",
                assignment_kind="side",
                coach_name=coach,
                phase="de",
                pod=pod,
                actor=actor,
                source="deploy",
            )
            updated = 0
            exceptions_kept = 0
            for stored_athlete in rows:
                athlete = dict(stored_athlete)
                if bool(athlete.get("assignment_override")):
                    exceptions_kept += 1
                    continue
                if athlete.get("side_coach") == coach:
                    continue
                self._update_athlete(
                    conn,
                    event_id=event_id,
                    athlete_id=athlete["id"],
                    changes={"side_coach": coach},
                    action="pod_assignment",
                    actor=actor,
                    expected_version=int(athlete["version"]),
                    require_active=True,
                )
                updated += 1

            availability_cursor = conn.execute(
                """
                UPDATE coach_availability
                SET is_available = 0, available_since = NULL, updated_at = ?,
                    updated_by = ?, version = version + 1
                WHERE meet_id = ? AND coach_name = ? AND is_available = 1
                  AND version = ?
                """,
                (
                    now,
                    actor,
                    meet["id"],
                    coach,
                    expected_availability_version,
                ),
            )
            if availability_cursor.rowcount != 1:
                raise ConcurrentUpdateError(
                    "This coach's availability changed on another phone. "
                    "The deployment was not saved."
                )
            conn.execute(
                "UPDATE events SET updated_at = ? WHERE id = ?", (now, event_id)
            )
            conn.commit()

        return {
            "event_id": event_id,
            "phase": "de",
            "pod": pod,
            "coach": coach,
            "main_coach": previous_main,
            "side_coach": coach,
            "previous_side": previous_side,
            "updated": updated,
            "exceptions_kept": exceptions_kept,
            "availability_version": expected_availability_version + 1,
        }

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
              AND participation_status = 'active'
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
            self._sync_pool_waves(conn, event_id, record_list)
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
            inherited_coaches: set[str] = set()
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
                    self._sync_athlete_assignment_history(
                        conn,
                        current=None,
                        new=new,
                        actor=actor,
                        source="import_added",
                    )
                    if assignment:
                        inherited_coaches.update(
                            str(assignment[field] or "").strip()
                            for field in ("main_coach", "side_coach")
                            if str(assignment[field] or "").strip()
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
                resulting_active = (
                    changes.get("active_state", current.get("active_state")) == "active"
                )
                inherited_assignment_changed = assignment_is_managed and (
                    phase_changed
                    or pod_changed
                    or any(
                        changes.get(field, current.get(field)) != current.get(field)
                        for field in ("main_coach", "side_coach")
                    )
                )
                if resulting_active and inherited_assignment_changed:
                    inherited_coaches.update(
                        str(changes.get(field, current.get(field)) or "").strip()
                        for field in ("main_coach", "side_coach")
                        if str(changes.get(field, current.get(field)) or "").strip()
                    )
                new_version = int(current["version"]) + 1
                set_clause = ", ".join(f"{key} = ?" for key in changes)
                conn.execute(
                    f"UPDATE athletes SET {set_clause}, version = ?, updated_at = ? WHERE id = ?",
                    [*changes.values(), new_version, now, current["id"]],
                )
                new = dict(
                    conn.execute("SELECT * FROM athletes WHERE id = ?", (current["id"],)).fetchone()
                )
                self._sync_athlete_assignment_history(
                    conn,
                    current=current,
                    new=new,
                    actor=actor,
                    source="import_updated",
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
            if inherited_coaches:
                meet = self._meet_for_event(conn, event_id)
                self._clear_available_coaches(
                    conn, meet["id"], inherited_coaches, actor
                )
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
        if require_active and (
            current["active_state"] != "active"
            or current.get("participation_status", "active") != "active"
        ):
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
        self._sync_athlete_assignment_history(
            conn,
            current=current,
            new=new,
            actor=actor,
            source=action,
        )
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
            if count:
                assigned_coaches = []
                if main_coach is not NO_CHANGE and str(main_coach or "").strip():
                    assigned_coaches.append(str(main_coach).strip())
                if side_coach is not NO_CHANGE and str(side_coach or "").strip():
                    assigned_coaches.append(str(side_coach).strip())
                if assigned_coaches:
                    meet = self._meet_for_event(conn, event_id)
                    self._clear_available_coaches(
                        conn, meet["id"], assigned_coaches, actor
                    )
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
            previous_assignment = conn.execute(
                """
                SELECT * FROM pod_assignments
                WHERE event_id = ? AND phase = ? AND pod = ?
                """,
                (event_id, phase, pod),
            ).fetchone()
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
            for kind, coach_name in (
                ("main", main_coach),
                ("side", side_coach),
            ):
                previous_name = (
                    str(previous_assignment[f"{kind}_coach"] or "")
                    if previous_assignment is not None
                    else ""
                )
                if previous_name != str(coach_name or ""):
                    self._sync_assignment_slot(
                        conn,
                        event_id=event_id,
                        athlete_id=None,
                        target_type="pod",
                        assignment_kind=kind,
                        coach_name=str(coach_name or ""),
                        phase=phase,
                        pod=pod,
                        actor=actor,
                        source="assign_pod",
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
            assigned_coaches = [
                coach for coach in (main_coach, side_coach) if str(coach).strip()
            ]
            if assigned_coaches:
                meet = self._meet_for_event(conn, event_id)
                self._clear_available_coaches(
                    conn, meet["id"], assigned_coaches, actor
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
            if covered_by:
                meet = self._meet_for_event(conn, event_id)
                self._clear_available_coaches(
                    conn, meet["id"], [covered_by], actor
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
            if (
                current["active_state"] != "active"
                or current.get("participation_status", "active") != "active"
            ):
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
                WHERE id = ? AND event_id = ? AND covered_by = ''
                  AND active_state = 'active' AND participation_status = 'active'
                """,
                (coach, now, now, athlete_id, event_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                latest = conn.execute("SELECT covered_by FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
                who = latest["covered_by"] if latest else "another coach"
                return False, f"Already covered by {who}."
            new = dict(conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone())
            self._sync_athlete_assignment_history(
                conn,
                current=current,
                new=new,
                actor=coach,
                source="claim",
            )
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
            meet = self._meet_for_event(conn, event_id)
            self._clear_available_coaches(conn, meet["id"], [coach], coach)
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
            if (
                current["active_state"] != "active"
                or current.get("participation_status", "active") != "active"
            ):
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
            meet = self._meet_for_event(conn, event_id)
            self._clear_available_coaches(conn, meet["id"], [actor], actor)
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
            meet = self._meet_for_event(conn, event_id)
            self._clear_available_coaches(conn, meet["id"], [actor], actor)
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

    def set_athlete_participation(
        self,
        event_id: str,
        athlete_id: str,
        status: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Set attendance independently from competitive elimination state."""

        clean_status = str(status or "").strip().lower()
        if clean_status not in {"active", "absent", "withdrawn"}:
            raise ValueError("Participation status must be active, absent, or withdrawn.")
        if clean_status == "active":
            return self.restore_athlete_participation(
                event_id,
                athlete_id,
                actor,
                expected_version=expected_version,
            )
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
            if expected_version is not None and int(current["version"]) != int(
                expected_version
            ):
                raise ConcurrentUpdateError(
                    "This athlete changed on another phone. The board has been refreshed; "
                    "please try again."
                )
            if current.get("participation_status", "active") == clean_status:
                conn.commit()
                return current
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes={
                    "participation_status": clean_status,
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
                action=f"participation_{clean_status}",
                actor=actor,
                expected_version=expected_version,
            )
            conn.commit()
        return result

    def mark_absent(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self.set_athlete_participation(
            event_id,
            athlete_id,
            "absent",
            actor,
            expected_version=expected_version,
        )

    def mark_withdrawn(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self.set_athlete_participation(
            event_id,
            athlete_id,
            "withdrawn",
            actor,
            expected_version=expected_version,
        )

    def restore_athlete_participation(
        self,
        event_id: str,
        athlete_id: str,
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Reverse the latest absence/withdrawal only when no newer edit exists."""

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
            if expected_version is not None and int(current["version"]) != int(
                expected_version
            ):
                raise ConcurrentUpdateError(
                    "This athlete changed on another phone. The board has been refreshed; "
                    "please try again."
                )
            if current.get("participation_status", "active") == "active":
                conn.commit()
                return current
            source = conn.execute(
                """
                SELECT * FROM actions
                WHERE event_id = ? AND athlete_id = ?
                  AND action IN ('participation_absent', 'participation_withdrawn')
                  AND version_after = ? AND undone_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (event_id, athlete_id, current["version"]),
            ).fetchone()
            if source is None:
                raise ConcurrentUpdateError(
                    "A newer update exists. Restore was blocked to avoid reviving stale "
                    "live information."
                )
            previous = _load(source["previous_json"], {})
            live_fields = {
                "call_status",
                "live_location",
                "reported_at",
                "reported_by",
                "covered_by",
                "covered_at",
                "help_requested_by",
                "help_requested_at",
                "help_location",
                "help_acknowledged_by",
                "help_acknowledged_at",
            }
            changes = {
                field: previous.get(field, current.get(field)) for field in live_fields
            }
            changes["participation_status"] = "active"
            if current["active_state"] != "active":
                changes.update(
                    {
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
                )
            result = self._update_athlete(
                conn,
                event_id=event_id,
                athlete_id=athlete_id,
                changes=changes,
                action="participation_restore",
                actor=actor,
                expected_version=current["version"],
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
            current = dict(current)
            if current["phase"] != "de":
                raise CompCoachError("Won/Lost is available only during direct elimination.")
            if (
                current["active_state"] != "active"
                or current.get("participation_status", "active") != "active"
            ):
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
            restored_coaches = [
                result.get("main_coach"),
                result.get("side_coach"),
                result.get("covered_by"),
                result.get("help_requested_by"),
                result.get("help_acknowledged_by"),
            ]
            meet = self._meet_for_event(conn, event_id)
            self._clear_available_coaches(
                conn, meet["id"], restored_coaches, actor
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
            self._sync_athlete_assignment_history(
                conn,
                current=current,
                new=new,
                actor=actor,
                source="undo",
            )
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
