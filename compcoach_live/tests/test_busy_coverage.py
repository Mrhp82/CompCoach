"""Physical coach coverage is distinct from planned pod assignments."""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError
from compcoach_live.de_bouts import create_de_bout, resolve_de_bout, undo_de_bout_result


@pytest.fixture
def context(tmp_path):
    url = os.environ.get('COMPCOACH_TEST_POSTGRES_URL')
    if url:
        from compcoach_live.postgres_storage import PostgresCompCoachDB
        schema = 'compcoach_busy_' + uuid4().hex
        db = PostgresCompCoachDB(url, schema=schema, pool_max_size=1 if os.environ.get('COMPCOACH_TEST_POSTGRES_PGLITE') == '1' else 4)
    else:
        db = CompCoachDB(tmp_path / 'coverage.db')
    meet = db.create_meet('NAC', ['Alex', 'Jordan', 'Morgan'], ['Taylor'])
    first = db.list_meet_events(meet['id'])[0]
    second = db.add_meet_event(meet['id'], 'Cadets')
    athletes = []
    for index, event in enumerate([first, second]):
        db.merge_import(event['id'], [
            {'athlete_id': f'ath_{index}_{n}', 'name': f'ATHLETE {index} {n}',
             'strip': 'B1', 'pod': 'B', 'pool': '', 'phase': 'de', 'time': ''}
            for n in range(2)
        ], 'Taylor')
        athletes.append(db.list_athletes(event['id']))
        db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan'], actor='Taylor')
    try:
        yield db, meet, [first, second], athletes
    finally:
        if url:
            with db._connection() as conn:
                conn.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
            db.close()


def availability(db, meet, coach):
    return next(row for row in db.list_coach_availability(meet['id']) if row['coach_name'] == coach)


def current(db, event, athlete):
    return db.get_athlete(event['id'], athlete['id'])


def test_busy_can_start_before_call_with_actual_strip_independent_of_pod(context):
    db, meet, events, groups = context
    row = current(db, events[0], groups[0][0])
    covered = db.cover_athlete(events[0]['id'], row['id'], 'Alex', 'Alex', location='j4', expected_version=row['version'])
    assert covered['source_strip'] == 'B1' and covered['pod'] == 'B'
    assert covered['live_location'] == 'J4' and covered['call_status'] == 'waiting'
    assert covered['covered_by'] == 'Alex' and covered['covered_at']
    state = availability(db, meet, 'Alex')
    assert state['is_busy'] and not state['is_available']
    assert state['busy_athlete_id'] == row['id'] and state['busy_location'] == 'J4'
    assert state['busy_since'] == covered['covered_at']
    with pytest.raises(CompCoachError, match='still covering'):
        db.set_coach_availability(meet['id'], 'Alex', True, 'Alex')


def test_coordinator_moves_one_coach_across_events_atomically_preserving_plans(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Taylor', location='D4')
    two = db.cover_athlete(events[1]['id'], groups[1][0]['id'], 'Alex', 'Taylor', location='F3')
    released = current(db, events[0], one)
    assert released['covered_by'] == '' and released['covered_at'] is None
    assert released['live_location'] == 'D4' and released['de_coaches'] == ['Alex', 'Jordan']
    assert two['covered_by'] == 'Alex' and two['de_coaches'] == ['Alex', 'Jordan']
    assert availability(db, meet, 'Alex')['busy_count'] == 1
    history = db.list_assignment_history(meet_id=meet['id'])
    coverage = [row for row in history if row['assignment_kind'] == 'coverage' and row['coach_name'] == 'Alex']
    assert len(coverage) == 2 and sum(row['ended_at'] is None for row in coverage) == 1
    assert any(row['action'] == 'coverage_handoff' for row in db.recent_actions(events[0]['id']))


def test_same_coach_location_updates_preserve_elapsed_timer(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex', location='C3')
    with db._connection() as conn:
        conn.execute('UPDATE athletes SET covered_at = ? WHERE id = ?', ('2026-10-01T10:00:00+00:00', one['id']))
    two = db.report_call(events[0]['id'], one['id'], status='now', location='D4', actor='Taylor', covered_by='Alex')
    assert two['covered_at'] == '2026-10-01T10:00:00+00:00'
    assert two['live_location'] == 'D4'
    assert two['source_strip'] == 'B1' and two['pod'] == 'B'


def test_not_called_report_clears_actual_call_without_changing_pod_or_timer(context):
    db, meet, events, groups = context
    one = db.report_call(events[0]['id'], groups[0][0]['id'], status='on_deck', location='C3', actor='Taylor')
    two = db.report_call(events[0]['id'], one['id'], status='waiting', location='B1', actor='Taylor', expected_version=one['version'])
    assert two['call_status'] == 'waiting' and two['live_location'] == ''
    assert two['reported_at'] and two['source_strip'] == 'B1' and two['pod'] == 'B'


@pytest.mark.parametrize('outcome', ['won', 'lost', 'bye'])
def test_outcome_releases_actual_covering_coach_only(context, outcome):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Morgan', 'Taylor', location='J4')
    if outcome == 'bye':
        saved = db.mark_bye(events[0]['id'], one['id'], actor='Alex', expected_version=one['version'])
    else:
        saved = db.mark_result(events[0]['id'], one['id'], outcome=outcome, actor='Alex', expected_version=one['version'])
    assert saved['covered_by'] == '' and saved['covered_at'] is None
    assert saved['live_location'] == '' and saved['call_status'] == 'waiting'
    assert availability(db, meet, 'Morgan')['is_available']
    assert not availability(db, meet, 'Morgan')['is_busy']
    assert not availability(db, meet, 'Alex')['is_available']
    assert not availability(db, meet, 'Jordan')['is_available']
    assert saved['de_coaches'] == ['Alex', 'Jordan']


def test_result_with_no_coverage_does_not_release_planned_or_actor(context):
    db, meet, events, groups = context
    db.mark_result(events[0]['id'], groups[0][0]['id'], outcome='won', actor='Alex')
    assert not any(row['is_available'] for row in db.list_coach_availability(meet['id']))


def test_replacement_frees_previous_covering_coach_and_starts_new_timer(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex', location='D4')
    two = db.cover_athlete(events[0]['id'], one['id'], 'Morgan', 'Taylor', expected_version=one['version'])
    assert two['covered_by'] == 'Morgan' and two['covered_at']
    assert availability(db, meet, 'Alex')['is_available']
    assert availability(db, meet, 'Morgan')['is_busy']


def test_stale_target_revision_rolls_back_cross_event_handoff(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    stale = current(db, events[1], groups[1][0])
    db.report_call(events[1]['id'], stale['id'], status='on_deck', location='H2', actor='Taylor')
    with pytest.raises(ConcurrentUpdateError):
        db.cover_athlete(events[1]['id'], stale['id'], 'Alex', 'Taylor', expected_version=stale['version'])
    assert current(db, events[0], one)['covered_by'] == 'Alex'
    assert current(db, events[1], stale)['covered_by'] == ''
    assert availability(db, meet, 'Alex')['busy_count'] == 1


def test_stale_coach_revision_rejects_when_coach_became_busy_elsewhere(context):
    db, meet, events, groups = context
    stale = availability(db, meet, 'Alex')['version']
    db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    target = current(db, events[1], groups[1][0])
    with pytest.raises(ConcurrentUpdateError, match="coach's situation changed"):
        db.cover_athlete(events[1]['id'], target['id'], 'Alex', 'Taylor', expected_version=target['version'], expected_coach_version=stale)
    assert current(db, events[0], groups[0][0])['covered_by'] == 'Alex'
    assert current(db, events[1], target)['covered_by'] == ''


def test_claim_and_report_use_same_exclusive_coverage(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    two = db.report_call(events[1]['id'], groups[1][0]['id'], status='now', location='P3', actor='Taylor')
    assert db.claim(events[1]['id'], two['id'], 'Alex', expected_version=two['version'])[0]
    assert current(db, events[0], one)['covered_by'] == ''
    three = db.report_call(events[0]['id'], one['id'], status='on_deck', location='M2', actor='Taylor', covered_by='Alex')
    assert three['covered_by'] == 'Alex'
    assert current(db, events[1], two)['covered_by'] == ''


def test_two_simultaneous_coordinator_handoffs_leave_single_live_coverage(context):
    db, meet, events, groups = context
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(lambda args: db.cover_athlete(args[0]['id'], args[1]['id'], 'Alex', 'Taylor'), [(events[0], groups[0][0]), (events[1], groups[1][0])]))
    assert all(row['covered_by'] == 'Alex' for row in result)
    assert availability(db, meet, 'Alex')['busy_count'] == 1
    assert sum(current(db, event, athlete)['covered_by'] == 'Alex' for event, athlete in [(events[0], groups[0][0]), (events[1], groups[1][0])]) == 1


def test_paired_result_releases_actual_coaches_and_correction_does_not_resurrect_busy(context):
    db, meet, events, groups = context
    a = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex', location='D4')
    b = db.cover_athlete(events[0]['id'], groups[0][1]['id'], 'Morgan', 'Taylor', location='D4')
    bout = create_de_bout(db, events[0]['id'], a['id'], b['id'], actor='Taylor')
    resolved = resolve_de_bout(db, events[0]['id'], bout['id'], winner_id=a['id'], actor='Taylor', expected_version=bout['version'])
    assert availability(db, meet, 'Alex')['is_available'] and availability(db, meet, 'Morgan')['is_available']
    assert not availability(db, meet, 'Jordan')['is_available']
    elsewhere = db.cover_athlete(events[1]['id'], groups[1][0]['id'], 'Alex', 'Alex')
    undo_de_bout_result(db, events[0]['id'], bout['id'], actor='Taylor', expected_version=resolved['version'])
    assert current(db, events[0], a)['covered_by'] == '' and current(db, events[0], b)['covered_by'] == ''
    assert current(db, events[1], elsewhere)['covered_by'] == 'Alex'
    assert availability(db, meet, 'Alex')['busy_count'] == 1


def test_individual_correction_does_not_resurrect_previous_coverage(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    lost = db.mark_result(events[0]['id'], one['id'], outcome='lost', actor='Alex')
    elsewhere = db.cover_athlete(events[1]['id'], groups[1][0]['id'], 'Alex', 'Alex')
    restored = db.correct_de_result(events[0]['id'], lost['id'], actor='Taylor', expected_version=lost['version'])
    assert restored['active_state'] == 'active' and restored['covered_by'] == ''
    assert current(db, events[1], elsewhere)['covered_by'] == 'Alex'
    assert availability(db, meet, 'Alex')['busy_count'] == 1


def test_legacy_duplicate_coverage_keeps_coach_unavailable_after_one_result(context):
    db, meet, events, groups = context
    db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    with db._connection() as conn:
        conn.execute("UPDATE athletes SET covered_by='Alex',covered_at='2026-10-01T10:00:00+00:00' WHERE id=?", (groups[1][0]['id'],))
        conn.execute("UPDATE coach_availability SET is_available=1 WHERE coach_name='Alex'")
    assert not availability(db, meet, 'Alex')['is_available']
    db.mark_result(events[0]['id'], groups[0][0]['id'], outcome='won', actor='Alex')
    assert availability(db, meet, 'Alex')['is_busy'] and not availability(db, meet, 'Alex')['is_available']


def test_release_marks_actual_coach_available_and_closed_day_rejects_coverage(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    db.release(events[0]['id'], one['id'], 'Alex', expected_version=one['version'])
    assert availability(db, meet, 'Alex')['is_available']
    db.set_meet_locked(meet['id'], True)
    with pytest.raises(EventLockedError):
        db.cover_athlete(events[1]['id'], groups[1][0]['id'], 'Alex', 'Taylor')


def test_legacy_restore_and_generic_undo_cannot_resurrect_coverage_elsewhere(context):
    db, meet, events, groups = context
    one = db.cover_athlete(events[0]['id'], groups[0][0]['id'], 'Alex', 'Alex')
    lost = db.mark_result(events[0]['id'], one['id'], outcome='lost', actor='Alex')
    action = next(row for row in db.recent_actions(events[0]['id']) if row['action'] == 'lost')
    elsewhere = db.cover_athlete(events[1]['id'], groups[1][0]['id'], 'Alex', 'Alex')
    with pytest.raises(ConcurrentUpdateError, match='now covering'):
        db.restore_athlete(events[0]['id'], lost['id'], 'Taylor', expected_version=lost['version'])
    with pytest.raises(ConcurrentUpdateError, match='now covering'):
        db.undo_action(events[0]['id'], action['id'], 'Taylor')
    assert current(db, events[0], lost)['active_state'] == 'eliminated'
    assert current(db, events[1], elsewhere)['covered_by'] == 'Alex'
    restored = db.correct_de_result(events[0]['id'], lost['id'], actor='Taylor')
    assert restored['active_state'] == 'active' and restored['covered_by'] == ''
