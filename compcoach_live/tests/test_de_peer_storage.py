"""DE groups keep every equal coach through edits, imports and history."""
import json
import sqlite3

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError, EventLockedError, assigned_coaches


def record(key, pod='B', phase='de'):
    return {'athlete_id': key, 'name': f'ATHLETE {key}', 'pod': pod,
            'strip': f'{pod}1', 'pool': '1' if phase == 'pools' else '',
            'time': '', 'phase': phase}


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / 'peer.db')
    meet = db.create_meet('NAC', ['Alex', 'Jordan', 'Taylor', 'Morgan'], ['Casey'])
    event = db.list_meet_events(meet['id'])[0]
    db.merge_import(event['id'], [record(str(i), pod) for i, pod in enumerate('BCDE')], 'Casey')
    return db, meet, event


def test_equal_three_coaches_cover_four_pods_and_full_workload(day):
    db, meet, event = day
    db.set_coach_availability(meet['id'], 'Taylor', True, 'Taylor')
    assert db.assign_de_pods(event['id'], pods=list('BCDE'), coaches=['Alex', 'Jordan', 'Taylor'], actor='Casey') == 4
    for athlete in db.list_athletes(event['id']):
        assert athlete['de_coaches'] == ['Alex', 'Jordan', 'Taylor']
        assert assigned_coaches(athlete) == ['Alex', 'Jordan', 'Taylor']
        assert athlete['main_coach'] == 'Alex'
        assert athlete['side_coach'] == 'Jordan'
    assert all(pod['coaches'] == ['Alex', 'Jordan', 'Taylor'] for pod in db.list_pod_assignments(event['id'], 'de'))
    taylor = next(row for row in db.list_coach_availability(meet['id']) if row['coach_name'] == 'Taylor')
    assert (taylor['assigned_count'], taylor['unfinished_count'], taylor['is_available']) == (4, 4, False)
    history = db.list_assignment_history(event_id=event['id'], include_closed=False)
    assert len(history) == 24
    assert {row['assignment_kind'] for row in history} == {'de_coach'}


def test_add_preserves_independent_groups_and_explicit_exceptions(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan'], actor='Casey')
    db.assign_de_pods(event['id'], pods=['C'], coaches=['Morgan'], actor='Casey')
    athlete = next(a for a in db.list_athletes(event['id']) if a['pod'] == 'B')
    db.assign_de_athletes(event['id'], [athlete['id']], coaches=['Taylor', 'Morgan', 'Alex'], actor='Casey')
    db.assign_de_pods(event['id'], pods=['B', 'C'], coaches=['Taylor', 'taylor'], actor='Casey', mode='add')
    groups = {pod['pod']: pod['coaches'] for pod in db.list_pod_assignments(event['id'])}
    assert groups == {'B': ['Alex', 'Jordan', 'Taylor'], 'C': ['Morgan', 'Taylor']}
    assert db.get_athlete(event['id'], athlete['id'])['de_coaches'] == ['Taylor', 'Morgan', 'Alex']
    db.merge_import(event['id'], [record('new', 'B')], 'Casey')
    new = next(a for a in db.list_athletes(event['id']) if a['athlete_key'] == 'new')
    assert new['de_coaches'] == ['Alex', 'Jordan', 'Taylor']


def test_peer_import_changes_pod_and_phase_without_leaking_previous_group(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan', 'Taylor'], actor='Casey')
    db.assign_de_pods(event['id'], pods=['C'], coaches=['Morgan'], actor='Casey')
    db.merge_import(event['id'], [record('0', 'C')], 'Casey')
    athlete = next(a for a in db.list_athletes(event['id']) if a['athlete_key'] == '0')
    assert athlete['de_coaches'] == ['Morgan']
    db.merge_import(event['id'], [record('0', 'F')], 'Casey')
    assert db.get_athlete(event['id'], athlete['id'])['de_coaches'] == []
    db.merge_import(event['id'], [record('pool-athlete', 'B', 'pools')], 'Casey')
    pool = next(a for a in db.list_athletes(event['id']) if a['athlete_key'] == 'pool-athlete')
    db.assign_athletes(event['id'], [pool['id']], main_coach='Morgan', side_coach='Alex', actor='Casey')
    assert db.get_athlete(event['id'], pool['id'])['de_coaches'] == []
    db.merge_import(event['id'], [record('pool-athlete', 'B')], 'Casey')
    assert db.get_athlete(event['id'], pool['id'])['de_coaches'] == ['Alex', 'Jordan', 'Taylor']


def test_unchanged_peer_keeps_history_interval_and_removed_peer_is_closed(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan'], actor='Casey')
    before = db.list_assignment_history(event_id=event['id'], include_closed=False)
    alex_ids = {row['id'] for row in before if row['coach_name'] == 'Alex'}
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Taylor'], actor='Casey', mode='add')
    after = db.list_assignment_history(event_id=event['id'], include_closed=False)
    assert alex_ids == {row['id'] for row in after if row['coach_name'] == 'Alex'}
    assert len([row for row in after if row['coach_name'] == 'Taylor']) == 2
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Taylor'], actor='Casey')
    all_rows = db.list_assignment_history(event_id=event['id'])
    assert all(row['ended_at'] for row in all_rows if row['coach_name'] in {'Alex', 'Jordan'})
    assert len(db.list_assignment_history(event_id=event['id'], include_closed=False)) == 2


def test_third_peer_rename_updates_groups_and_stable_history(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan', 'Taylor'], actor='Casey')
    coach = next(coach for coach in db.list_coaches() if coach['name'] == 'Taylor')
    before = db.list_assignment_history(coach_id=coach['id'])
    db.update_coach(coach['id'], name='Terry')
    assert db.list_pod_assignments(event['id'])[0]['coaches'] == ['Alex', 'Jordan', 'Terry']
    assert next(a for a in db.list_athletes(event['id']) if a['pod'] == 'B')['de_coaches'] == ['Alex', 'Jordan', 'Terry']
    after = db.list_assignment_history(coach_id=coach['id'])
    assert [row['id'] for row in before] == [row['id'] for row in after]
    assert {row['coach_name'] for row in after} == {'Terry'}


def test_de_exception_undo_preserves_every_peer(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan', 'Taylor'], actor='Casey')
    athlete = next(a for a in db.list_athletes(event['id']) if a['pod'] == 'B')
    db.assign_de_athletes(event['id'], [athlete['id']], coaches=['Morgan'], actor='Casey')
    action = db.recent_actions(event['id'])[0]
    undone = db.undo_action(event['id'], action['id'], 'Casey')
    assert undone['de_coaches'] == ['Alex', 'Jordan', 'Taylor']
    assert undone['assignment_override'] == 0
    assert {row['coach_name'] for row in db.list_assignment_history(athlete_id=athlete['id'], include_closed=False)} == {'Alex', 'Jordan', 'Taylor'}


def test_same_coach_can_cover_other_event_and_groups_have_no_cross_event_lock(day):
    db, meet, event = day
    other = db.add_meet_event(meet['id'], 'Other event')
    db.merge_import(other['id'], [record('other', 'F')], 'Casey')
    db.assign_de_pods(event['id'], pods=list('BCDE'), coaches=['Taylor'], actor='Casey')
    db.assign_de_pods(other['id'], pods=['F'], coaches=['Taylor'], actor='Casey')
    assert db.list_athletes(other['id'])[0]['de_coaches'] == ['Taylor']
    taylor = next(row for row in db.list_coach_availability(meet['id']) if row['coach_name'] == 'Taylor')
    assert taylor['assigned_count'] == 5


@pytest.mark.parametrize('pods', [[], list('BCDEF')])
def test_selected_pod_limit_is_atomic(day, pods):
    db, _, event = day
    with pytest.raises(ValueError, match='one and four'):
        db.assign_de_pods(event['id'], pods=pods, coaches=['Alex'], actor='Casey')
    assert db.list_pod_assignments(event['id']) == []
    assert db.list_assignment_history(event_id=event['id']) == []


def test_clear_group_stays_empty_after_restart(day):
    db, _, event = day
    db.assign_de_pods(event['id'], pods=['B'], coaches=['Alex', 'Jordan', 'Taylor'], actor='Casey')
    db.assign_de_pods(event['id'], pods=['B'], coaches=[], actor='Casey')
    restarted = CompCoachDB(db.path)
    assert restarted.list_pod_assignments(event['id'])[0]['coaches'] == []
    athlete = next(a for a in restarted.list_athletes(event['id']) if a['pod'] == 'B')
    assert athlete['de_coaches'] == []
    assert athlete['main_coach'] == athlete['side_coach'] == ''


def test_legacy_slots_and_history_upgrade_idempotently_without_new_ids(tmp_path):
    db = CompCoachDB(tmp_path / 'legacy-peer.db')
    event = db.create_event('Legacy', ['Alex', 'Jordan'], ['Casey'])
    db.merge_import(event['id'], [record('old')], 'Casey')
    db.assign_pod(event['id'], phase='de', pod='B', main_coach='Alex', side_coach='Jordan', actor='Casey')
    athlete = db.list_athletes(event['id'])[0]
    history_before = db.list_assignment_history(event_id=event['id'])
    with sqlite3.connect(db.path) as conn:
        conn.execute('ALTER TABLE athletes DROP COLUMN de_coaches_json')
        conn.execute('ALTER TABLE pod_assignments DROP COLUMN coaches_json')
        conn.execute("UPDATE coach_assignment_history SET assignment_kind = CASE WHEN coach_name = 'Alex' THEN 'main' ELSE 'side' END")
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='coach_assignment_history'").fetchone()[0]
        ddl = ddl.replace("'coverage', 'de_coach'", "'coverage'")
        conn.execute('ALTER TABLE coach_assignment_history RENAME TO old_coach_history')
        conn.execute(ddl)
        conn.execute('INSERT INTO coach_assignment_history SELECT * FROM old_coach_history')
        conn.execute('DROP TABLE old_coach_history')
    upgraded = CompCoachDB(db.path)
    assert upgraded.get_athlete(event['id'], athlete['id'])['de_coaches'] == ['Alex', 'Jordan']
    assert upgraded.list_pod_assignments(event['id'])[0]['coaches'] == ['Alex', 'Jordan']
    after = upgraded.list_assignment_history(event_id=event['id'])
    assert [(r['id'], r['started_at'], r['ended_at']) for r in after] == [(r['id'], r['started_at'], r['ended_at']) for r in history_before]
    assert {r['assignment_kind'] for r in after} == {'de_coach'}
    restarted = CompCoachDB(db.path)
    assert restarted.list_assignment_history(event_id=event['id']) == after
    assert restarted.get_athlete(event['id'], athlete['id'])['id'] == athlete['id']


def test_two_byes_and_two_real_wins_are_four_advances(day):
    db, _, event = day
    athlete = db.list_athletes(event['id'])[0]
    for _ in range(2):
        athlete = db.mark_bye(event['id'], athlete['id'], actor='Casey', expected_version=athlete['version'], expected_bout_id='')
    for _ in range(2):
        athlete = db.mark_result(event['id'], athlete['id'], outcome='won', actor='Casey', expected_version=athlete['version'])
    assert (athlete['de_byes'], athlete['de_wins'], athlete['de_rounds_passed']) == (2, 2, 4)
    assert athlete['active_state'] == 'active'
    assert CompCoachDB(db.path).get_athlete(event['id'], athlete['id'])['de_rounds_passed'] == 4


def test_latest_bye_undo_restores_counter_and_saved_live_state(day):
    db, _, event = day
    athlete = db.list_athletes(event['id'])[0]
    called = db.report_call(event['id'], athlete['id'], status='on_deck', location='B3', actor='Casey')
    bye = db.mark_bye(event['id'], athlete['id'], actor='Casey', expected_version=called['version'])
    assert (bye['de_byes'], bye['de_wins'], bye['last_de_result'], bye['call_status']) == (1, 0, 'bye', 'waiting')
    undone = db.undo_last_bye(event['id'], athlete['id'], 'Casey', expected_version=bye['version'])
    assert (undone['de_byes'], undone['de_rounds_passed'], undone['last_de_result']) == (0, 0, '')
    assert (undone['call_status'], undone['live_location']) == ('on_deck', 'B3')
    with pytest.raises(CompCoachError, match='no longer a BYE'):
        db.undo_last_bye(event['id'], athlete['id'], 'Casey')


def test_bye_stale_write_and_undo_never_replace_new_updates(day):
    from compcoach_live.storage import ConcurrentUpdateError
    db, _, event = day
    athlete = db.list_athletes(event['id'])[0]
    first = db.mark_bye(event['id'], athlete['id'], actor='Casey', expected_version=athlete['version'])
    with pytest.raises(ConcurrentUpdateError):
        db.mark_bye(event['id'], athlete['id'], actor='Casey', expected_version=athlete['version'])
    db.report_call(event['id'], athlete['id'], status='now', location='B4', actor='Casey')
    with pytest.raises(ConcurrentUpdateError):
        db.undo_last_bye(event['id'], athlete['id'], 'Casey', expected_version=first['version'])
    with pytest.raises(ConcurrentUpdateError, match='newer update'):
        db.undo_last_bye(event['id'], athlete['id'], 'Casey')
    current = db.get_athlete(event['id'], athlete['id'])
    assert (current['de_byes'], current['live_location']) == (1, 'B4')


def test_bye_and_undo_reject_pending_afm_pair(day):
    from compcoach_live.de_bouts import create_de_bout
    db, _, event = day
    a, b = db.list_athletes(event['id'])[:2]
    bye = db.mark_bye(event['id'], a['id'], actor='Casey')
    pair = create_de_bout(db, event['id'], a['id'], b['id'], actor='Casey')
    with pytest.raises(CompCoachError, match='pairing changed'):
        db.mark_bye(event['id'], a['id'], actor='Casey', expected_bout_id='')
    with pytest.raises(CompCoachError, match='pending AFM bout'):
        db.mark_bye(event['id'], a['id'], actor='Casey', expected_bout_id=pair['id'])
    with pytest.raises(CompCoachError, match='changed on another phone'):
        db.undo_last_bye(event['id'], a['id'], 'Casey', expected_version=bye['version'])
    with pytest.raises(CompCoachError, match='both athletes'):
        db.correct_de_result(event['id'], a['id'], 'Casey', expected_version=db.get_athlete(event['id'], a['id'])['version'])
    assert db.get_athlete(event['id'], a['id'])['de_byes'] == 1
    assert db.get_athlete(event['id'], b['id'])['de_byes'] == 0


def test_bye_rejects_pools_out_and_closed_competitions(day):
    db, _, event = day
    athlete = db.list_athletes(event['id'])[0]
    db.merge_import(event['id'], [record('pool', 'F', 'pools')], 'Casey')
    pool = next(a for a in db.list_athletes(event['id']) if a['athlete_key'] == 'pool')
    with pytest.raises(CompCoachError, match='only during direct elimination'):
        db.mark_bye(event['id'], pool['id'], actor='Casey')
    db.mark_result(event['id'], athlete['id'], outcome='lost', actor='Casey')
    with pytest.raises(CompCoachError, match='no longer active'):
        db.mark_bye(event['id'], athlete['id'], actor='Casey')
    other = next(a for a in db.list_athletes(event['id']) if a['phase'] == 'de' and a['active_state'] == 'active')
    db.set_meet_locked(db.get_meet_for_event(event['id'])['id'], True)
    with pytest.raises(EventLockedError):
        db.mark_bye(event['id'], other['id'], actor='Casey')


def test_bye_additive_migration_defaults_old_rows_to_zero_and_phase_reset(day):
    db, _, event = day
    athlete = db.list_athletes(event['id'])[0]
    won = db.mark_result(event['id'], athlete['id'], outcome='won', actor='Casey')
    with sqlite3.connect(db.path) as conn:
        conn.execute('ALTER TABLE athletes DROP COLUMN de_byes')
    upgraded = CompCoachDB(db.path)
    migrated = upgraded.get_athlete(event['id'], athlete['id'])
    assert (migrated['de_byes'], migrated['de_wins'], migrated['de_rounds_passed']) == (0, 1, 1)
    upgraded.mark_bye(event['id'], athlete['id'], actor='Casey')
    upgraded.merge_import(event['id'], [record(migrated['athlete_key'], 'B', 'pools')], 'Casey')
    reset = upgraded.get_athlete(event['id'], athlete['id'])
    assert (reset['de_byes'], reset['de_wins'], reset['de_rounds_passed']) == (0, 0, 0)
