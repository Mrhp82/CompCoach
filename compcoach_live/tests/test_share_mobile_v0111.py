"""Admin sharing keeps the common parent link visible and its opt-in persistent."""

from urllib.parse import parse_qs, urlsplit

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.parent_access import get_parent_coach_visibility
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import APP_PATH, nav_named, open_board


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(tmp_path / "share-mobile.db"))
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach.example/app")
    for name in ("COMPCOACH_DATABASE_URL", "COMPCOACH_REQUIRE_CLOUD"):
        monkeypatch.delenv(name, raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(tmp_path / "share-mobile.db")
    st.cache_resource.clear()


def _share(database, *, who=True):
    day = database.create_meet("October NAC", ["Carmine", "Sam"], ["Irina"], first_event_name="Cadet ME")
    child = database.list_meet_events(day["id"])[0]
    database.merge_import(child["id"], parse_pasted_table(
        "Name\tStrip #\tPool #\nEXAMPLE Avery\tB1\t1"
    ).records, "Carmine")
    app = open_board(day["id"], day["admin_token"], "Carmine" if who else None)
    if not who:
        next(button for button in app.button if button.label == "Carmine").click().run()
    nav_named(app, "admin_nav_").set_value("Share").run()
    assert not app.exception
    return day, app


def _parent_url(app):
    return next(code.value for code in app.code if "?competition=" in code.value)


def _switch(app):
    return next(toggle for toggle in app.toggle if toggle.label == "Show coach status to parents")


@pytest.mark.parametrize("who", [True, False])
def test_parent_link_is_first_for_url_and_selected_admin_identity(database, who):
    day, app = _share(database, who=who)
    url = _parent_url(app)
    assert app.code[0].value == url
    assert url.startswith("https://compcoach.example/app/")
    query = parse_qs(urlsplit(url).query)
    assert query["competition"] == [day["competition_id"]]
    assert set(query) == {"competition", "parent"}
    assert not _switch(app).value
    assert not get_parent_coach_visibility(database, day["competition_id"])
    next(radio for radio in app.radio if radio.label == "Events to include").set_value("Choose events").run()
    next(select for select in app.multiselect if select.label == "Include these events").set_value([]).run()
    assert app.code[0].value == url
    assert not any(code.value.startswith("🏆") for code in app.code)


def test_admin_switch_persists_and_parent_view_follows_both_changes(database):
    day, app = _share(database)
    url = _parent_url(app)
    _switch(app).set_value(True).run()
    assert not app.exception and _switch(app).value
    assert get_parent_coach_visibility(CompCoachDB(database.path), day["competition_id"])
    parent = AppTest.from_file(APP_PATH, default_timeout=15)
    parent.query_params.update({key: values[0] for key, values in parse_qs(urlsplit(url).query).items()})
    parent.run()
    assert not parent.exception
    assert any("Coach availability" in item.value for item in parent.markdown)
    assert not parent.toggle
    assert not any("admin_nav_" in str(item.key) for item in parent.get("button_group"))
    _switch(app).set_value(False).run()
    assert not app.exception and not get_parent_coach_visibility(database, day["competition_id"])
    parent.run()
    assert not parent.exception
    assert not any("Coach availability" in item.value for item in parent.markdown)
    assert _parent_url(app) == url


def test_second_competition_starts_with_an_independent_default_off(database):
    day, app = _share(database)
    _switch(app).set_value(True).run()
    second, app = _share(database)
    assert second["competition_id"] != day["competition_id"]
    assert not _switch(app).value
