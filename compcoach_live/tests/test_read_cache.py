"""Render reads are reused without carrying stale data into another request."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from compcoach_live.read_cache import RenderReads


class Database:
    backend = "test"

    def __init__(self):
        self.calls = Counter()
        self.value = 0
        self.lock = Lock()

    def get_meet(self, meet_id, *, role="coach", active=True):
        with self.lock:
            self.calls["get_meet"] += 1
        return {"id": meet_id, "value": self.value, "coaches": ["Alex"]}

    def list_athletes(self, meet_id):
        self.calls["list_athletes"] += 1
        return [{"id": "athlete", "version": self.value}]

    def get_meet_revision(self, meet_id):
        self.calls["get_meet_revision"] += 1
        return str(self.value)

    def authorize_meet(self, meet_id, token):
        self.calls["authorize_meet"] += 1
        return "coach" if token == "current" else None

    def update(self, value, *, fail=False):
        self.value = value
        if fail:
            raise RuntimeError("A request failed")


def test_repeated_reads_share_one_request_and_return_independent_dictionaries():
    raw = Database()
    db = RenderReads(raw)
    with db.snapshot():
        first = db.get_meet("day", role="coach", active=True)
        first["coaches"].append("Local annotation")
        second = db.get_meet("day", active=True, role="coach")
        assert second["coaches"] == ["Alex"]
        assert raw.calls["get_meet"] == 1
        assert db.list_athletes("day") == db.list_athletes("day")
        assert raw.calls["list_athletes"] == 1


def test_nested_ui_snapshot_reuses_outer_request_then_discards_it():
    raw = Database()
    db = RenderReads(raw)
    with db.snapshot():
        assert db.get_meet("day")["value"] == 0
        with db.snapshot():
            assert db.get_meet("day")["value"] == 0
        assert raw.calls["get_meet"] == 1
    raw.value = 1
    with db.snapshot():
        assert db.get_meet("day")["value"] == 1
    assert raw.calls["get_meet"] == 2


@pytest.mark.parametrize("fail", [False, True])
def test_writes_and_failed_writes_discard_cached_read_state(fail):
    raw = Database()
    db = RenderReads(raw)
    with db.snapshot():
        assert db.get_meet("day")["value"] == 0
        if fail:
            with pytest.raises(RuntimeError):
                db.update(1, fail=True)
        else:
            db.update(1)
        assert db.get_meet("day")["value"] == 1
        assert raw.calls["get_meet"] == 2


def test_revision_and_authorization_reach_current_storage_on_every_check():
    raw = Database()
    db = RenderReads(raw)
    with db.snapshot():
        db.get_meet("day")
        assert db.get_meet_revision("day") == "0"
        raw.value = 1
        assert db.get_meet_revision("day") == "1"
        assert db.authorize_meet("day", "current") == "coach"
        assert db.authorize_meet("day", "obsolete") is None
        db.get_meet("day")
    assert raw.calls["get_meet_revision"] == 2
    assert raw.calls["authorize_meet"] == 2
    assert raw.calls["get_meet"] == 1


def test_reads_without_render_snapshot_are_never_retained():
    raw = Database()
    db = RenderReads(raw)
    assert db.get_meet("day")["value"] == 0
    raw.value = 1
    assert db.get_meet("day")["value"] == 1
    assert raw.calls["get_meet"] == 2


def test_two_concurrent_coach_requests_have_separate_read_caches():
    raw = Database()
    db = RenderReads(raw)
    barrier = Barrier(2)

    def render():
        with db.snapshot():
            first = db.get_meet("day")
            barrier.wait(timeout=5)
            second = db.get_meet("day")
        return first, second

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: render(), range(2)))
    assert raw.calls["get_meet"] == 2
    assert all(first == second for first, second in results)


def test_explicit_invalidation_refreshes_private_training_writer_changes():
    raw = Database()
    db = RenderReads(raw)
    with db.snapshot():
        assert db.list_athletes("day")[0]["version"] == 0
        # The training engine writes through the private connection facade.
        raw.value = 1
        db.invalidate()
        assert db.list_athletes("day")[0]["version"] == 1
    assert raw.calls["list_athletes"] == 2
