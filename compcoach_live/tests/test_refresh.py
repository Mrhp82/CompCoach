"""Refresh decisions follow visible changes and due simulation work."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from compcoach_live.refresh import training_needs_tick, training_visible_revision
from compcoach_live.storage import CompCoachError


NOW = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)


def metadata(*, stage=8):
    person = {
        "first_seen": (NOW - timedelta(minutes=2)).isoformat(),
        "views": ["My Group"], "milestones": ["Physical coverage"],
        "stages_seen": [stage], "stage_views": {str(stage): ["My Group"]},
    }
    return {
        "meet_id": "practice", "kind": "run", "is_hub": False,
        "status": "running", "expired": False, "version": 12,
        "stage": stage, "stage_index": stage, "stage_count": 18,
        "stage_title": "Busy coach: a colleague may help",
        "instruction": "Watch the other athlete while covering your current bout.",
        "hint": "Request help when needed.", "progress": stage / 17,
        "scenario_message": "The other athlete was called on J2.",
        "last_feedback": "Completed: actual strip and physical coverage.",
        "learner": "Alex", "participants": [{"name": "Alex", **deepcopy(person)}],
        "expires_at": (NOW + timedelta(days=7)).isoformat(),
        "updated_at": NOW.isoformat(),
        "state": {
            "kind": "run", "learner": "Alex", "coaches": ["Alex", "Robin (virtual)"],
            "expires_at": (NOW + timedelta(days=7)).isoformat(),
            "participants": {"Alex": person}, "pending_events": [],
            "entered_action_id": 20, "variant": 0,
        },
    }


def test_observation_telemetry_alone_does_not_refresh_the_course_panel():
    before = metadata()
    after = deepcopy(before)
    after["version"] += 1
    after["updated_at"] = (NOW + timedelta(seconds=5)).isoformat()
    after["state"]["participants"]["Alex"]["milestones"].append("Call reported")
    after["state"]["participants"]["Alex"]["views"].append("Live")
    after["participants"][0]["milestones"].append("Call reported")
    after["participants"][0]["views"].append("Live")
    after["state"]["entered_action_id"] += 1
    assert training_visible_revision(before) == training_visible_revision(after)


@pytest.mark.parametrize("changes", [
    {"stage": 9, "stage_index": 9, "stage_title": "A sudden call: take over"},
    {"instruction": "Take over the newly called athlete."},
    {"scenario_message": "A colleague has arrived and is covering the other athlete."},
    {"last_feedback": "Your call was saved."},
    {"status": "completed"},
    {"expired": True, "status": "stopped"},
])
def test_changes_to_visible_guidance_refresh_the_course_panel(changes):
    before = metadata()
    after = deepcopy(before)
    after.update(changes)
    assert training_visible_revision(before) != training_visible_revision(after)


def test_first_or_new_view_requires_observation_but_idle_same_view_does_not():
    settled = metadata(stage=1)
    assert not training_needs_tick(settled, "Alex", "My Group", NOW)
    assert training_needs_tick(settled, "Alex", "Live", NOW)
    first = deepcopy(settled)
    first["state"]["participants"] = {}
    first["participants"] = []
    assert training_needs_tick(first, "Alex", "My Group", NOW)


def test_new_stage_is_observed_once_even_if_navigation_stays_the_same():
    changed = metadata(stage=8)
    changed["stage"] = changed["stage_index"] = 9
    assert training_needs_tick(changed, "Alex", "My Group", NOW)
    changed["state"]["participants"]["Alex"]["stage_views"]["9"] = ["My Group"]
    changed["state"]["participants"]["Alex"]["stages_seen"].append(9)
    assert not training_needs_tick(changed, "Alex", "My Group", NOW)


def test_future_virtual_event_waits_until_due_then_runs_without_coach_input():
    active = metadata()
    active["state"]["pending_events"] = [{
        "id": "coverage", "kind": "other_coverage", "athlete_id": "secondary",
        "due_at": (NOW + timedelta(seconds=15)).isoformat(),
    }]
    assert not training_needs_tick(active, "Alex", "My Group", NOW)
    assert not training_needs_tick(active, "Alex", "My Group", NOW + timedelta(seconds=14))
    assert training_needs_tick(active, "Alex", "My Group", NOW + timedelta(seconds=15))


def test_access_expiry_is_checked_even_when_the_coach_has_not_tapped():
    active = metadata()
    active["state"]["expires_at"] = active["expires_at"] = (NOW + timedelta(seconds=10)).isoformat()
    assert not training_needs_tick(active, "Alex", "My Group", NOW)
    assert training_needs_tick(active, "Alex", "My Group", NOW + timedelta(seconds=10))


@pytest.mark.parametrize("status", ["completed", "stopped"])
def test_finished_course_does_not_run_the_engine_on_every_idle_check(status):
    finished = metadata()
    finished["status"] = status
    assert not training_needs_tick(finished, "Alex", "My Group", NOW)


def test_real_coach_action_triggers_processing_without_waiting_for_next_timer():
    settled = metadata()
    assert training_needs_tick(settled, "Alex", "My Group", NOW, board_changed=True)


def test_hub_stopped_on_another_device_ends_a_still_running_personal_course():
    settled = metadata()
    assert training_needs_tick(settled, "Alex", "My Group", NOW, hub_status="stopped")


def test_paused_hub_does_not_fire_a_due_virtual_event():
    active = metadata()
    active["state"]["pending_events"] = [{
        "id": "call", "kind": "sudden_call", "due_at": NOW.isoformat(),
    }]
    assert not training_needs_tick(active, "Alex", "My Group", NOW, hub_status="paused")
    assert training_needs_tick(active, "Alex", "My Group", NOW, hub_status="running")


def test_hub_overview_changes_when_a_learner_completes_a_step():
    before = metadata()
    before["is_hub"] = True
    before["kind"] = "hub"
    before["participants"] = [{"name": "Alex", "stage_index": 8, "status": "running"}]
    after = deepcopy(before)
    after["participants"][0]["stage_index"] = 9
    assert training_visible_revision(before) != training_visible_revision(after)


def test_hub_does_not_process_learner_steps_but_still_expires_access():
    hub = metadata()
    hub["is_hub"] = True
    hub["kind"] = "hub"
    assert not training_needs_tick(hub, "Alex", "Live", NOW)
    hub["expires_at"] = hub["state"]["expires_at"] = NOW.isoformat()
    assert training_needs_tick(hub, "Alex", "Live", NOW)


class HeartbeatDB:
    def __init__(self):
        self.revision = "unchanged"
        self.reads = 0
        self.invalidations = 0
        self.error = None

    def get_meet_revision(self, _meet_id):
        self.reads += 1
        if self.error:
            raise self.error
        return self.revision

    def invalidate(self):
        self.invalidations += 1


@pytest.fixture
def heartbeat():
    """Run the actual callback without starting timers or rendering an app.

    Extracting this function preserves its real control flow. The small fake
    Streamlit surface records redraws/toasts and makes accidental IO visible.
    """
    tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8"))
    names = {"live_refresh_fragment", "current_training_view"}
    selected = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name in names]
    assert len(selected) == len(names)
    for function in selected:
        function.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    redraws, toasts, ticks, metadata_reads = [], [], [], []
    database = HeartbeatDB()
    active = metadata()
    clock = [NOW.timestamp()]
    streamlit = SimpleNamespace(
        session_state={}, rerun=lambda **kwargs: redraws.append(kwargs),
        toast=lambda message: toasts.append(message),
    )

    def read_metadata(_database, _meet_id):
        metadata_reads.append(1)
        return deepcopy(active)

    def tick(_database, _meet_id, **kwargs):
        ticks.append(kwargs)
        return deepcopy(active)

    namespace = {
        "st": streamlit, "db": database,
        "get_training": read_metadata, "training_hub_status": lambda *_args: "running",
        "training_needs_tick": lambda value, actor, view, **kwargs: training_needs_tick(value, actor, view, NOW, **kwargs),
        "training_visible_revision": training_visible_revision,
        "tick_training": tick, "CompCoachError": CompCoachError,
        "time": SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]),
    }
    exec(compile(module, "app-heartbeat", "exec"), namespace)

    def call(*, practice=False, clock_bucket=None):
        return namespace["live_refresh_fragment"](
            "practice", "coach", "Alex", "unchanged",
            training_visible_revision(metadata()), practice, clock_bucket, 1,
        )

    return SimpleNamespace(
        call=call, namespace=namespace, db=database, metadata=active,
        streamlit=streamlit, redraws=redraws, toasts=toasts, ticks=ticks,
        metadata_reads=metadata_reads, clock=clock,
    )


@pytest.mark.parametrize("practice", [False, True])
def test_initial_heartbeat_only_arms_itself_without_duplicate_database_work(heartbeat, practice):
    heartbeat.call(practice=practice)
    assert heartbeat.db.reads == 0
    assert not heartbeat.metadata_reads
    assert not heartbeat.ticks
    assert not heartbeat.redraws


def test_idle_real_board_has_one_revision_read_and_no_redraw_per_check(heartbeat):
    heartbeat.call()
    for _ in range(8):
        heartbeat.call()
    assert heartbeat.db.reads == 8
    assert not heartbeat.ticks
    assert not heartbeat.redraws


def test_idle_training_neither_writes_nor_redraws_the_workflow(heartbeat):
    heartbeat.call(practice=True)
    for _ in range(8):
        heartbeat.call(practice=True)
    assert heartbeat.db.reads == len(heartbeat.metadata_reads) == 8
    assert heartbeat.db.invalidations == 0
    assert not heartbeat.ticks
    assert not heartbeat.redraws


def test_changed_real_board_refreshes_other_coaches_on_the_next_check(heartbeat):
    heartbeat.call()
    heartbeat.db.revision = "new help or call or result"
    heartbeat.call()
    assert heartbeat.redraws == [{"scope": "app"}]
    assert not heartbeat.ticks


def test_due_virtual_event_is_processed_and_refreshes_its_new_situation(heartbeat):
    heartbeat.call(practice=True)
    heartbeat.metadata["state"]["pending_events"] = [{
        "kind": "other_coverage", "due_at": (NOW - timedelta(seconds=1)).isoformat(),
    }]

    def respond(_db, _meet_id, **kwargs):
        heartbeat.ticks.append(kwargs)
        heartbeat.db.revision = "colleague covered athlete"
        heartbeat.metadata["scenario_message"] = "A virtual colleague has arrived on J2."
        heartbeat.metadata["state"]["pending_events"] = []
        return deepcopy(heartbeat.metadata)

    heartbeat.namespace["tick_training"] = respond
    heartbeat.call(practice=True)
    assert len(heartbeat.ticks) == heartbeat.db.invalidations == 1
    assert heartbeat.db.reads == 2
    assert heartbeat.redraws == [{"scope": "app"}]


def test_new_view_observation_only_does_not_force_full_app_refresh(heartbeat):
    heartbeat.call(practice=True)
    heartbeat.streamlit.session_state["coach_nav_practice"] = "Live"

    def observed(_db, _meet_id, **kwargs):
        heartbeat.ticks.append(kwargs)
        heartbeat.metadata["version"] += 1
        heartbeat.metadata["state"]["participants"]["Alex"]["stage_views"]["8"].append("Live")
        return deepcopy(heartbeat.metadata)

    heartbeat.namespace["tick_training"] = observed
    heartbeat.call(practice=True)
    assert heartbeat.ticks == [{"actor": "Alex", "view": "Live"}]
    assert not heartbeat.redraws
    heartbeat.call(practice=True)
    assert len(heartbeat.ticks) == 1
    assert not heartbeat.redraws


def test_temporary_connection_error_keeps_board_and_retries_quietly(heartbeat):
    heartbeat.call()
    heartbeat.db.error = CompCoachError("Database temporarily unavailable")
    heartbeat.call()
    heartbeat.call()
    assert not heartbeat.redraws
    assert len(heartbeat.toasts) == 1
    heartbeat.db.error = None
    heartbeat.call()
    assert heartbeat.db.reads == 3
    assert not heartbeat.redraws


def test_elapsed_timer_updates_once_per_minute_without_a_five_second_redraw(heartbeat):
    minute = int(heartbeat.clock[0] // 60)
    heartbeat.call(clock_bucket=minute)
    heartbeat.call(clock_bucket=minute)
    assert not heartbeat.redraws
    heartbeat.clock[0] += 60
    heartbeat.call(clock_bucket=minute)
    assert heartbeat.redraws == [{"scope": "app"}]
