"""Focused mobile action flows for same-club bouts, without app navigation."""

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.de_bouts import create_de_bout, list_de_bouts
from compcoach_live.storage import CompCoachDB


def _seed(tmp_path):
    path = tmp_path / "bout_ui.db"
    db = CompCoachDB(path)
    event = db.create_event("Cadet Epee",["Alex","Taylor"],["Jordan"])
    db.merge_import(event["id"],[{"athlete_id":f"ath_{name}","name":name,"phase":"de","pod":"P","strip":"P","pool":"","time":""} for name in ("Casey","Riley")],"Jordan")
    a,b = sorted(db.list_athletes(event["id"]),key=lambda row:row["name"])
    return path,db,event,a,b


def _app(path,event,actor="Alex",*,corrections=False):
    source = f'''
from compcoach_live.storage import CompCoachDB
from compcoach_live.de_bout_controls import render_de_bouts, render_athlete_bout_badge, render_de_bout_corrections
db = CompCoachDB({str(path)!r})
event = db.get_event({event['id']!r})
{('render_de_bout_corrections' if corrections else 'render_de_bouts')}(db,event,{actor!r},key_prefix='test')
for athlete in db.list_athletes(event['id']):
    render_athlete_bout_badge(db,event['id'],athlete)
'''
    return AppTest.from_string(source,default_timeout=10).run()


def _button(app,label):
    buttons = [button for button in app.button if button.label == label]
    assert len(buttons) == 1
    return buttons[0]


def test_pair_creation_confirmed_result_immediate_and_undo_in_corrections(tmp_path):
    path,db,event,a,b = _seed(tmp_path)
    app = _app(path,event)
    app.selectbox(key=f"test_first_{event['id']}").select(a["id"])
    app.selectbox(key=f"test_second_{event['id']}").select(b["id"])
    app.text_input(key=f"test_round_{event['id']}").set_value("T64")
    _button(app,"Review pairing").click().run()
    assert not app.exception
    assert not list_de_bouts(db,event["id"])
    _button(app,"Confirm pairing").click().run()
    assert not app.exception
    assert len(list_de_bouts(db,event["id"])) == 1
    assert sum("cc-afm-bout-badge" in markdown.value for markdown in app.markdown) == 2
    _button(app,"Casey wins").click().run()
    assert not app.exception
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 1
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "eliminated"
    assert not any(button.label in {"Confirm winner", "Undo both results"} for button in app.button)
    app = _app(path,event,corrections=True)
    _button(app,"Undo both results").click().run()
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "eliminated"
    _button(app,"Confirm undo both").click().run()
    assert not app.exception
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 0
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "active"


def test_cancel_removal_and_restore_leave_athletes_unchanged(tmp_path):
    path,db,event,a,b = _seed(tmp_path)
    create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan")
    app = _app(path,event)
    _button(app,"Remove pairing").click().run()
    _button(app,"Cancel").click().run()
    assert not app.exception
    assert not any(button.label == "Confirm removal" for button in app.button)
    _button(app,"Remove pairing").click().run()
    _button(app,"Confirm removal").click().run()
    assert not app.exception
    assert not list_de_bouts(db,event["id"])
    assert not any(button.label == "Restore pairing" for button in app.button)
    app = _app(path,event,corrections=True)
    _button(app,"Restore pairing").click().run()
    assert not app.exception
    assert list_de_bouts(db,event["id"])[0]["status"] == "pending"
    assert [db.get_athlete(event["id"],row["id"])["version"] for row in (a,b)] == [a["version"],b["version"]]


def test_one_tap_result_uses_rendered_snapshot_and_rejects_other_phone_update(tmp_path):
    path,db,event,a,b = _seed(tmp_path)
    create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan")
    app = _app(path,event)
    db.report_call(event["id"],b["id"],status="now",location="P3",actor="Jordan")
    _button(app,"Casey wins").click().run()
    assert not app.exception
    assert any("changed on another phone" in item.value for item in app.error)
    assert not any(button.label == "Confirm winner" for button in app.button)
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 0


@pytest.mark.parametrize("mode",["anonymous","closed"])
def test_read_only_board_keeps_pair_and_badges_without_actions(tmp_path,mode):
    path,db,event,a,b = _seed(tmp_path)
    create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan",round_label="T32")
    if mode == "closed":
        db.set_event_locked(event["id"],True)
    app = _app(path,event,actor="" if mode == "anonymous" else "Alex")
    assert not app.exception
    assert not app.button and not app.selectbox
    badges = [markdown.value for markdown in app.markdown if "cc-afm-bout-badge" in markdown.value]
    assert len(badges) == 2 and all("T32" in badge for badge in badges)


def test_second_phone_cannot_save_opposite_winner_after_bout_resolved(tmp_path):
    path,db,event,a,b = _seed(tmp_path)
    create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan")
    first, second = _app(path,event), _app(path,event,actor="Taylor")
    _button(first,"Casey wins").click().run()
    _button(second,"Riley wins").click().run()
    assert not second.exception
    assert any("changed on another phone" in item.value for item in second.error)
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 1
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "eliminated"


@pytest.mark.parametrize("actor",["Alex","Taylor","Jordan"])
def test_one_tap_paired_result_available_to_all_staff(tmp_path,actor):
    path,db,event,a,b = _seed(tmp_path)
    create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan")
    app = _app(path,event,actor=actor)
    _button(app,"Riley wins").click().run()
    assert not app.exception
    assert db.get_athlete(event["id"],b["id"])["de_wins"] == 1
    assert db.get_athlete(event["id"],a["id"])["active_state"] == "eliminated"
    assert not any(button.label == "Confirm winner" for button in app.button)
