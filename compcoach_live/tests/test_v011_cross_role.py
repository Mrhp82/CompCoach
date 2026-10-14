"""One competition is shared across parent, coach and Admin operational views.

These integration scenarios use real persistence and independent page sessions;
they exercise the boundaries between the new feature modules, rather than
repeating each module's isolated unit tests.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.parent_access import get_or_create_parent_access, get_parent_context, report_parent_call
from compcoach_live import postgres_storage as pg
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.pool_results import pool_has_results, pool_is_complete
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError
from compcoach_live.tests.test_app_smoke import markdown_values, nav_named, open_board, query_value


APP_PATH = str(Path(__file__).parents[1] / "app.py")


@pytest.fixture
def competition(tmp_path, monkeypatch):
    path = tmp_path / "cross-role.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach.example")
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "test-only")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    db = CompCoachDB(path)
    day = db.create_meet(
        "Autumn Challenge", ["Alex", "Taylor"], ["Jordan"],
        first_event_name="Morning Cadet", competition_date="2026-10-09",
    )
    morning = db.list_meet_events(day["id"])[0]
    db.add_meet_event(day["id"], "Afternoon Junior")
    afternoon = db.list_meet_events(day["id"])[1]
    for event, text in (
        (morning, "Name\tStrip #\tPool #\nSTONE Avery\tA1\t1\nREED Nora\tA2\t2"),
        (afternoon, "Name\tStrip #\nPARK Eli\tB1"),
    ):
        db.merge_import(event["id"], parse_pasted_table(text).records, "Alex")
    morning_rows = db.list_athletes(morning["id"])
    db.assign_athletes(morning["id"], [r["id"] for r in morning_rows], main_coach="Alex", actor="Alex")
    db.assign_de_pods(afternoon["id"], pods=["B"], coaches=["Taylor"], actor="Alex")
    access = get_or_create_parent_access(db, day["competition_id"], "Alex")
    yield db, day, morning, afternoon, access
    st.cache_resource.clear()


def _parent_report(db, day, access, athlete, status="on_deck", strip="C3"):
    return report_parent_call(
        db, day["competition_id"], access["token"], athlete["event_id"], athlete["id"],
        status=status, location=strip, expected_version=int(athlete["version"]),
        expected_day_id=day["id"], expected_phase=athlete["phase"],
    )


def _context(db, day, access):
    return get_parent_context(db, day["competition_id"], access["token"])


def _parent_page(day, access):
    app = AppTest.from_file(APP_PATH, default_timeout=20)
    app.query_params.update(competition=day["competition_id"], parent=access["token"])
    return app.run()


def test_parent_de_call_enriches_coach_view_without_reassigning_or_claiming(competition):
    db, day, _, afternoon, access = competition
    athlete = db.list_athletes(afternoon["id"])[0]
    db.set_coach_availability(day["id"], "Taylor", True, "Taylor")
    before_available = db.list_coach_availability(day["id"])
    saved = _parent_report(db, day, access, athlete, "now", "D4")
    assert saved["call_status"] == "now" and saved["live_location"] == "D4"
    assert saved["reported_by"] == "Parent" and saved["reported_at"]
    for field in ("de_coaches", "main_coach", "side_coach", "covered_by", "covered_at", "takeover_coach", "de_wins", "de_byes", "active_state"):
        assert saved[field] == athlete[field]
    assert db.list_coach_availability(day["id"]) == before_available
    coach = open_board(day["id"], day["coach_token"], "Taylor")
    assert not coach.exception
    markup = "\n".join(markdown_values(coach))
    assert "PARK Eli" in markup and "D4" in markup
    assert any("Reported by Parent" in item.value for item in coach.caption)


def test_common_parent_page_has_only_call_and_strip_controls(competition):
    db, day, morning, afternoon, access = competition
    app = _parent_page(day, access)
    assert not app.exception
    assert any(item.value == "Parent updates" or "Parent updates" in item.value for item in app.markdown)
    assert not app.get("button_group")
    assert not any(widget.label in {"Who are you?", "Coach", "Admin PIN", "Wins", "Losses"} for widget in app.selectbox)
    assert not any(widget.label == "Admin PIN" for widget in app.text_input)
    athlete = db.list_athletes(afternoon["id"])[0]
    app.selectbox[0].select(athlete["id"]).run()
    assert not app.exception
    labels = {button.label for button in app.button}
    assert labels == {"Fencing now", "On deck", "In the hole"}
    forbidden = {"Won", "Lost", "I’m available to help", "I'll cover this bout", "I’ll take over", "Save pool result", "Mark absent", "Prepare next day"}
    assert not labels & forbidden
    app.text_input[0].set_value("G7")
    next(button for button in app.button if button.label == "On deck").click().run()
    assert not app.exception
    saved = db.get_athlete(afternoon["id"], athlete["id"])
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "G7"
    assert any("Coaches can now see your update" in item.value for item in app.success)
    assert {button.label for button in app.button} == {"Fencing now", "On deck", "In the hole", "Save strip"}
    public = _context(db, day, access)
    assert {row["event_id"] for row in public["athletes"]} == {morning["id"], afternoon["id"]}
    assert "token" not in str(public)
    assert public["show_coach_status"] is False and public["coaches"] == []
    for row in public["athletes"]:
        assert not {"main_coach", "side_coach", "de_coaches", "covered_by"} & row.keys()


def test_forced_pool_closure_removes_parent_calls_and_preserves_known_results(competition):
    db, day, morning, afternoon, access = competition
    first, pending = db.list_athletes(morning["id"])
    db.set_pool_result(morning["id"], first["id"], wins=4, losses=2, actor="Alex")
    stale = db.get_athlete(morning["id"], pending["id"])
    closure = db.close_pools(morning["id"], "Alex")
    assert closure["closed"] and closure["completed_count"] == 1
    complete = db.list_athletes(morning["id"])
    assert all(pool_is_complete(row) for row in complete)
    known = db.get_athlete(morning["id"], first["id"])
    unknown = db.get_athlete(morning["id"], pending["id"])
    assert pool_has_results(known) and (known["pool_wins"], known["pool_losses"]) == (4, 2)
    assert not pool_has_results(unknown) and unknown["pool_wins"] is None and unknown["pool_losses"] is None
    assert {row["event_id"] for row in _context(db, day, access)["athletes"]} == {afternoon["id"]}
    with pytest.raises(CompCoachError):
        _parent_report(db, day, access, stale, "now", "A2")
    # Explicitly reopening one athlete restores the phase consistently for
    # the parent portal; the old version still cannot revive a stale call.
    reopened = db.reopen_pool(morning["id"], pending["id"], "Alex")
    assert not pool_is_complete(reopened)
    assert not db.get_phase_states(morning["id"])["pools"]["closed"]
    assert any(row["id"] == pending["id"] for row in _context(db, day, access)["athletes"])
    assert _parent_report(db, day, access, reopened)["call_status"] == "on_deck"


def test_closing_day_keeps_one_parent_link_but_blocks_all_old_writes(competition):
    db, day, morning, _, access = competition
    athlete = db.list_athletes(morning["id"])[0]
    _parent_report(db, day, access, athlete)
    history_before = db.list_athletes(morning["id"])
    db.finish_meet(day["id"], "Alex")
    context = _context(db, day, access)
    assert context["authorized"] and context["reason"] == "no_active_day"
    assert context["athletes"] == [] and context["day"] is None
    assert get_or_create_parent_access(db, day["competition_id"], "Jordan")["token"] == access["token"]
    with pytest.raises(CompCoachError):
        _parent_report(db, day, access, db.get_athlete(morning["id"], athlete["id"]))
    with pytest.raises(EventLockedError):
        db.report_call(morning["id"], athlete["id"], status="now", location="H1", actor="Alex")
    with pytest.raises(EventLockedError):
        db.set_pool_result(morning["id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert db.list_athletes(morning["id"]) == history_before


def test_forced_pool_close_then_de_import_resumes_parent_reporting_in_new_phase(competition):
    db, day, morning, _, access = competition
    pool_roster = db.list_athletes(morning["id"])
    stale = pool_roster[0]
    db.close_pools(morning["id"], "Alex")
    db.merge_import(
        morning["id"],
        parse_pasted_table("Name\tStrip #\nSTONE Avery\tP1\nREED Nora\tQ1").records,
        "Alex",
    )
    direct = db.list_athletes(morning["id"])
    assert {row["id"] for row in direct} == {row["id"] for row in pool_roster}
    assert all(row["phase"] == "de" for row in direct)
    assert all(row["pool_results_unknown"] and row["pool_wins"] is None for row in direct)
    assert db.get_phase_states(morning["id"])["pools"]["closed"]
    # The completed Pools marker cannot block the newly imported DE calls.
    eligible_ids = {row["id"] for row in _context(db, day, access)["athletes"]}
    assert {row["id"] for row in direct} <= eligible_ids
    with pytest.raises(ConcurrentUpdateError):
        _parent_report(db, day, access, stale)
    current = db.get_athlete(morning["id"], stale["id"])
    assert _parent_report(db, day, access, current, "in_hole", "R4")["live_location"] == "R4"


def test_prepared_day_reuses_scheduled_roster_and_same_links_follow_activation(competition):
    db, day, _, _, access = competition
    next_day = db.create_competition_day(
        day["competition_id"], "Saturday", "2026-10-10", ["Alex", "Taylor"], ["Jordan"],
        first_event_name="Junior Women", day_status="scheduled",
    )
    next_event = db.list_meet_events(next_day["id"])[0]
    db.merge_import(next_event["id"], parse_pasted_table("Name\tStrip #\nLANE Riley\tP1").records, "Alex")
    db.assign_de_pods(next_event["id"], pods=["P"], coaches=["Taylor"], actor="Alex")
    next_roster_before = db.list_athletes(next_event["id"])
    assert all(row["name"] != "LANE Riley" for row in _context(db, day, access)["athletes"])
    prepared = db.prepare_next_day(
        day["id"], "A different draft name", "2026-10-10", ["Ignored draft event"], "Alex", activate=False,
    )
    assert prepared["id"] == next_day["id"] and prepared["name"] == "Saturday"
    assert db.list_athletes(next_event["id"]) == next_roster_before
    assert db.get_active_competition_day(day["competition_id"]) is None
    assert _context(db, day, access)["athletes"] == []
    old_coach = open_board(day["id"], day["coach_token"], "Taylor")
    assert not old_coach.exception
    assert any("No active competition" in sub.value for sub in old_coach.subheader)
    assert not any("LANE Riley" in text for text in markdown_values(old_coach))
    db.set_day_status(next_day["id"], "active", "Alex", require_closed_previous=True)
    old_coach.run()
    assert not old_coach.exception
    assert query_value(old_coach, "event") == next_day["id"] and query_value(old_coach, "who") == "Taylor"
    assert "LANE Riley" in "\n".join(markdown_values(old_coach))
    context = _context(db, day, access)
    assert context["day"]["id"] == next_day["id"]
    assert [row["name"] for row in context["athletes"]] == ["LANE Riley"]
    assert get_or_create_parent_access(db, day["competition_id"], "Alex")["token"] == access["token"]


def test_parent_stale_day_bout_and_event_cannot_override_new_day(competition):
    db, day, morning, _, access = competition
    old_athlete = db.list_athletes(morning["id"])[0]
    tomorrow = db.prepare_next_day(day["id"], "Saturday", "2026-10-10", ["Junior Epee"], "Alex", activate=False)
    event = db.list_meet_events(tomorrow["id"])[0]
    db.merge_import(event["id"], parse_pasted_table("Name\tStrip #\nSTONE Avery\tK1").records, "Alex")
    current = db.list_athletes(event["id"])[0]
    db.set_day_status(tomorrow["id"], "active", "Alex", require_closed_previous=True)
    with pytest.raises(ConcurrentUpdateError):
        _parent_report(db, day, access, old_athlete)
    # Same competition capability does not let a form on the new day post to
    # yesterday's event, even if athlete names are identical.
    with pytest.raises(ConcurrentUpdateError):
        _parent_report(db, tomorrow, access, old_athlete)
    assert db.get_athlete(event["id"], current["id"]) == current


def test_competition_finish_closes_parent_page_and_all_future_duties(competition):
    db, day, _, afternoon, access = competition
    athlete = db.list_athletes(afternoon["id"])[0]
    db.create_competition_day(day["competition_id"], "Saturday", "2026-10-10", ["Alex"], first_event_name="Junior Epee")
    db.finish_competition(day["competition_id"], "Alex")
    context = _context(db, day, access)
    assert context["authorized"] and context["reason"] == "competition_closed"
    assert context["athletes"] == [] and context["day"] is None
    with pytest.raises(EventLockedError):
        _parent_report(db, day, access, athlete)
    with pytest.raises(EventLockedError):
        db.report_call(afternoon["id"], athlete["id"], status="now", location="G8", actor="Taylor")
    assert all(item["day_status"] == "closed" for item in db.list_competition_days(day["competition_id"]))
    parent_page = _parent_page(day, access)
    assert not parent_page.exception
    assert any("competition has ended" in item.value for item in parent_page.info)
    assert not parent_page.button and not parent_page.selectbox


def test_whatsapp_selected_event_does_not_limit_common_parent_roster(competition):
    db, day, morning, afternoon, access = competition
    admin = open_board(day["id"], day["admin_token"], "Alex")
    nav_named(admin, "admin_nav_").set_value("Share").run()
    assert not admin.exception
    scope = next(widget for widget in admin.radio if widget.label == "Events to include")
    scope.set_value("Choose events").run()
    selection = next(widget for widget in admin.multiselect if widget.label == "Include these events")
    selection.set_value([morning["id"]]).run()
    assert not admin.exception
    message = next(widget.value for widget in admin.code if "MORNING CADET" in widget.value)
    assert "MORNING CADET" in message and "STONE Avery" in message
    assert "AFTERNOON JUNIOR" not in message and "PARK Eli" not in message
    assert {row["event_id"] for row in _context(db, day, access)["athletes"]} == {morning["id"], afternoon["id"]}
    selection = next(widget for widget in admin.multiselect if widget.label == "Include these events")
    selection.set_value([]).run()
    assert not admin.exception
    assert not any("MORNING CADET" in widget.value or "AFTERNOON JUNIOR" in widget.value for widget in admin.code)
    assert get_or_create_parent_access(db, day["competition_id"], "Alex")["token"] == access["token"]


def test_parent_pool_closure_and_scheduled_reuse_through_postgres_sql_adapter(competition, monkeypatch):
    """Production SQL adapter with a local transport; this is not a PG server."""
    local, day, morning, afternoon, access = competition
    statuses = SimpleNamespace(IDLE="idle", INTRANS="intrans")
    monkeypatch.setattr(pg, "TransactionStatus", statuses, raising=False)
    monkeypatch.setattr(pg, "psycopg", SimpleNamespace(IntegrityError=type("PGConstraintError", (Exception,), {})))
    translated = []

    class Raw:
        def __init__(self, connection):
            self.connection = connection
            self.info = SimpleNamespace(transaction_status=statuses.IDLE)
            self.closed = False

        def execute(self, sql, parameters=None, **kwargs):
            translated.append((sql, parameters))
            if "pg_advisory_xact_lock" in sql:
                return SimpleNamespace(rowcount=0, fetchone=lambda: None, fetchall=lambda: [])
            if sql == "BEGIN":
                self.info.transaction_status = statuses.INTRANS
            return self.connection.execute(sql.replace("%s", "?").replace("%%", "%"), parameters or ())

        def commit(self):
            self.connection.commit()
            self.info.transaction_status = statuses.IDLE

        def rollback(self):
            self.connection.rollback()
            self.info.transaction_status = statuses.IDLE

    class FacadeDB(CompCoachDB):
        @contextmanager
        def _connection(self):
            with sqlite3.connect(self.path) as connection:
                connection.row_factory = lambda cursor, values: {column[0]: value for column, value in zip(cursor.description, values)}
                yield pg._Connection(Raw(connection), "compcoach", 1)

    db = object.__new__(FacadeDB)
    db.path = local.path
    future = db.create_competition_day(day["competition_id"], "Saturday", "2026-10-10", ["Alex", "Taylor"], ["Jordan"], first_event_name="Junior Epee")
    assert _context(db, day, access)["day"]["id"] == day["id"]
    athlete = db.list_athletes(afternoon["id"])[0]
    assert _parent_report(db, day, access, athlete)["reported_by"] == "Parent"
    closed = db.close_pools(morning["id"], "Alex")
    assert closed["closed"] and closed["completed_count"] == 2
    assert db.get_phase_states(morning["id"])["pools"]["closed"]
    assert all(row["event_id"] != morning["id"] for row in _context(db, day, access)["athletes"])
    reopened = db.reopen_pools(morning["id"], "Alex")
    assert not reopened["closed"]
    # Reopening the phase retains the athlete completions until a deliberate
    # per-athlete correction, across the same SQL adapter used in deployment.
    assert all(pool_is_complete(row) for row in db.list_athletes(morning["id"]))
    prepared = db.prepare_next_day(day["id"], "Unused name", "2026-10-10", ["Unused event"], "Alex", activate=False)
    assert prepared["id"] == future["id"] and prepared["day_status"] == "scheduled"
    assert _context(db, day, access)["reason"] == "no_active_day"
    assert get_or_create_parent_access(db, day["competition_id"], "Alex")["token"] == access["token"]
    assert any("pg_advisory_xact_lock" in sql for sql, _ in translated)
    assert any(sql.startswith("UPDATE phase_states") or sql.startswith("INSERT INTO phase_states") for sql, _ in translated)
