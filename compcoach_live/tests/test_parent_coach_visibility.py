"""Optional parent status is safe, persisted and isolated to the active day."""

from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.parent_access import (
    PARENT_VISIBILITY_DDL, get_or_create_parent_access, get_parent_coach_visibility,
    get_parent_context, parent_context_revision, report_parent_call,
    set_parent_coach_visibility,
)
from compcoach_live.storage import CompCoachDB, CompCoachError


@pytest.fixture
def family(tmp_path):
    url = os.environ.get("COMPCOACH_TEST_POSTGRES_URL")
    if url:
        from compcoach_live.postgres_storage import PostgresCompCoachDB
        schema = "compcoach_parent_visibility_" + uuid4().hex
        db = PostgresCompCoachDB(url, schema=schema,
                               pool_max_size=1 if os.environ.get("COMPCOACH_TEST_POSTGRES_PGLITE") == "1" else 4)
    else:
        db = CompCoachDB(tmp_path / "visibility.db")
    competition = db.create_competition("Example NAC")
    day = db.create_competition_day(
        competition["id"], "Saturday", "2026-10-10", ["Alex", "Jordan"], ["Casey"],
        first_event_name="Cadet ME", day_status="active",
    )
    event = db.list_meet_events(day["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "avery", "name": "STONE Avery",
                    "phase": "de", "pod": "B", "strip": "B1"}], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    access = get_or_create_parent_access(db, competition["id"], "Casey")
    try:
        yield db, competition, day, event, athlete, access
    finally:
        if url:
            with db._connection() as conn:
                conn.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
            db.close()


def context(family):
    db, competition, _, _, _, access = family
    return get_parent_context(db, competition["id"], access["token"])


def enable(family):
    db, competition, *_ = family
    return set_parent_coach_visibility(db, competition["id"], True, "Casey")


def coach(family, name="Alex"):
    return next(row for row in context(family)["coaches"] if row["coach_name"] == name)


def test_default_off_migrates_existing_parent_link_without_rotation(family):
    db, competition, _, _, _, access = family
    with db._connection() as conn:
        conn.execute("DROP TABLE parent_visibility_settings")
        conn.commit()
    # A cached handle from v0.11.0 still has the old ready marker.
    db._parent_access_schema_ready = True
    del db._parent_access_schema_version
    assert get_parent_coach_visibility(db, competition["id"]) is False
    assert context(family)["coaches"] == []
    assert not context(family)["show_coach_status"]
    assert get_or_create_parent_access(db, competition["id"], "Casey") == access


def test_visibility_persists_and_is_competition_scoped(family):
    db, competition, *_ = family
    other = db.create_competition("Other NAC")
    assert enable(family) is True
    assert get_parent_coach_visibility(db, competition["id"]) is True
    assert get_parent_coach_visibility(db, other["id"]) is False
    if isinstance(db, CompCoachDB) and not hasattr(db, "schema"):
        assert get_parent_coach_visibility(CompCoachDB(db.path), competition["id"]) is True
    assert set_parent_coach_visibility(db, competition["id"], False, "Casey") is False
    assert context(family)["coaches"] == []


@pytest.mark.parametrize("enabled,actor,error", [(1, "Casey", TypeError), (True, "", ValueError)])
def test_visibility_writer_validates_admin_input(family, enabled, actor, error):
    db, competition, *_ = family
    with pytest.raises(error):
        set_parent_coach_visibility(db, competition["id"], enabled, actor)
    with pytest.raises(CompCoachError, match="Competition not found"):
        set_parent_coach_visibility(db, "missing", True, "Casey")


def test_public_projection_has_no_private_fields_and_never_infers_imported_strip(family):
    db, _, day, *_ = family
    db.set_coach_availability(day["id"], "Alex", True, "Casey")
    enable(family)
    rows = context(family)["coaches"]
    assert [row["coach_name"] for row in rows] == ["Alex", "Jordan"]
    assert rows[0]["availability"] == "available"
    assert rows[1]["availability"] == "not_reported"
    assert all(not row["last_declared_strip"] for row in rows)
    assert all(set(row) == {"coach_name", "availability", "last_declared_strip", "updated_at"} for row in rows)
    encoded = json.dumps(rows)
    assert "Casey" not in encoded and "B1" not in encoded and "token" not in encoded


def test_last_declared_strip_survives_result_and_busy_overrides_legacy_available(family):
    db, _, day, event, athlete, _ = family
    enable(family)
    covered = db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="c3")
    with db._connection() as conn:
        conn.execute("UPDATE coach_availability SET is_available = 1 WHERE meet_id = ? AND coach_name = ?",
                     (day["id"], "Alex"))
        conn.commit()
    assert coach(family)["availability"] == "busy"
    assert coach(family)["last_declared_strip"] == "C3"
    assert coach(family)["updated_at"]
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex", expected_version=covered["version"])
    assert db.get_athlete(event["id"], athlete["id"])["live_location"] == ""
    assert coach(family)["availability"] == "available"
    assert coach(family)["last_declared_strip"] == "C3"


def test_parent_call_and_planned_takeover_do_not_declare_coach_location(family):
    db, competition, day, event, athlete, access = family
    enable(family)
    db.set_coach_availability(day["id"], "Alex", True, "Alex")
    promised = db.take_over_athlete(event["id"], athlete["id"], "Alex", "Alex")
    report_parent_call(db, competition["id"], access["token"], event["id"], athlete["id"],
                       status="on_deck", location="P9", expected_version=promised["version"],
                       expected_day_id=day["id"], expected_phase="de")
    assert coach(family)["availability"] == "committed"
    assert coach(family)["last_declared_strip"] == ""
    covered = db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    before = coach(family)
    report_parent_call(db, competition["id"], access["token"], event["id"], athlete["id"],
                       status="now", location="Q5", expected_version=covered["version"],
                       expected_day_id=day["id"], expected_phase="de")
    assert coach(family) == before


def test_staff_location_update_tracks_last_declaration_and_revision(family):
    db, _, _, event, athlete, _ = family
    enable(family)
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    before = parent_context_revision(context(family), athlete["id"])
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Casey")
    assert coach(family)["last_declared_strip"] == "D4"
    assert parent_context_revision(context(family), athlete["id"]) != before


def test_hidden_coach_changes_do_not_refresh_parent_but_toggle_does(family):
    db, _, day, _, athlete, _ = family
    before = parent_context_revision(context(family), athlete["id"])
    db.set_coach_availability(day["id"], "Alex", True, "Alex")
    with db._connection() as conn:
        conn.execute("UPDATE meets SET updated_at = ? WHERE id = ?",
                     ("2026-10-10T15:15:15+00:00", day["id"]))
        conn.commit()
    assert parent_context_revision(context(family), athlete["id"]) == before
    enable(family)
    visible = parent_context_revision(context(family), athlete["id"])
    assert visible != before
    db.set_coach_availability(day["id"], "Alex", False, "Alex")
    assert parent_context_revision(context(family), athlete["id"]) != visible


def test_no_active_day_and_new_day_never_expose_old_coach_status(family):
    db, competition, day, event, athlete, _ = family
    enable(family)
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    db.finish_meet(day["id"], "Casey")
    assert context(family)["coaches"] == [] and not context(family)["show_coach_status"]
    db.create_competition_day(competition["id"], "Sunday", "2026-10-11", ["Jordan"], ["Casey"],
                             first_event_name="Junior ME", day_status="active")
    assert context(family)["show_coach_status"]
    assert context(family)["coaches"] == [{"coach_name": "Jordan", "availability": "not_reported",
                                          "last_declared_strip": "", "updated_at": None}]


def test_invalid_link_and_closed_competition_redact_enabled_status(family):
    db, competition, _, _, _, _ = family
    enable(family)
    assert get_parent_context(db, competition["id"], "bad")["coaches"] == []
    with db._connection() as conn:
        conn.execute("UPDATE competitions SET status = 'closed' WHERE id = ?", (competition["id"],))
        conn.commit()
    assert context(family)["coaches"] == [] and not context(family)["show_coach_status"]


def test_visibility_ddl_uses_existing_postgres_adapter():
    from compcoach_live.postgres_storage import _translate_sql
    translated, returns_id = _translate_sql(PARENT_VISIBILITY_DDL)
    assert "CREATE TABLE IF NOT EXISTS parent_visibility_settings" in translated
    assert "show_coach_status INTEGER NOT NULL DEFAULT 0" in translated
    assert not returns_id


def test_parent_portal_coach_list_is_optional_and_preserves_reporting(family):
    db, competition, day, event, athlete, access = family
    if hasattr(db, "schema"):
        pytest.skip("Streamlit fixture opens the local SQLite file; domain tests cover PostgreSQL.")
    source = f'''from compcoach_live.storage import CompCoachDB
from compcoach_live.parent_portal import render_parent_portal
db = CompCoachDB({str(db.path)!r})
render_parent_portal(db, {competition['id']!r}, {access['token']!r})
'''
    app = AppTest.from_string(source, default_timeout=10).run()
    assert not app.exception
    assert not any("Coach availability" in entry.value for entry in app.markdown)
    enable(family)
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    app.run()
    rendered = " ".join(entry.value for entry in app.markdown)
    assert "Coach availability" in rendered and "Alex" in rendered and "Busy" in rendered
    assert "Last declared strip C3" in rendered and "Updated just now" in rendered
    app.selectbox[0].set_value(athlete["id"]).run()
    assert not app.exception
    assert [entry.label for entry in app.button] == ["Fencing now", "On deck", "In the hole"]
    set_parent_coach_visibility(db, competition["id"], False, "Casey")
    app.run()
    assert not any("Coach availability" in entry.value for entry in app.markdown)


def test_elapsed_labels_are_stable_time_strings():
    from compcoach_live.parent_portal import _elapsed_label
    assert _elapsed_label(None) == "No update reported"
    assert _elapsed_label("invalid") == "Update time unavailable"
    now = datetime.now(timezone.utc)
    assert _elapsed_label((now - timedelta(minutes=7)).isoformat()) == "Updated 7 min ago"
    assert _elapsed_label((now - timedelta(hours=2, minutes=12)).isoformat()) == "Updated 2h 12m ago"
