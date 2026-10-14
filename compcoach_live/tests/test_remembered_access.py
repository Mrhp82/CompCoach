"""Browser memory is navigation data, returned through the component protocol."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from urllib.parse import urlencode

import pytest

from compcoach_live import remembered_access as access

NOW = 1_800_000_000_000
NODE = shutil.which("node")
EVENT = {
    "id": "day-1", "admin_token": "admin-capability",
    "coordinator_token": "coordinator-capability", "coach_token": "coach-capability",
}


def query(role="coach", actor="Coach Jamie", **extra):
    return "?" + urlencode({"event": EVENT["id"], "token": EVENT[f"{role}_token"], "who": actor, **extra})


def record(**changes):
    return {
        "schema": access.SCHEMA_VERSION, "role": "coach", "event": EVENT["id"],
        "actor": "Coach Jamie", "url": query(), "expiresAt": NOW + 3600_000, **changes,
    }


def browser(html, *, location="https://example.test/coaching/", stored=None, session=None,
            fail_storage=False, repeat_config=None):
    """Execute the exact frontend with local storage and Streamlit protocol APIs."""
    if not NODE:
        pytest.skip("Node is needed for the component protocol fixture")
    fixture = {
        "script": re.search(r"<script>\s*(.*?)\s*</script>", html, re.S).group(1),
        "location": location, "stored": stored or {}, "session": session or {}, "now": NOW,
        "failStorage": fail_storage, "repeatConfig": repeat_config,
    }
    harness = r"""
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const saved = {...input.stored}; const session = {...input.session};
const redirects = []; const messages = []; const listeners = {};
const makeStorage = values => ({
  getItem: key => {if (input.failStorage) throw Error('blocked'); return values[key] ?? null;},
  setItem: (key, value) => {if (input.failStorage) throw Error('blocked'); values[key] = String(value);},
  removeItem: key => {if (input.failStorage) throw Error('blocked'); delete values[key];}
});
const parent = {
  location: {href: input.location, replace: value => redirects.push(value)},
  localStorage: makeStorage(saved), sessionStorage: makeStorage(session),
  postMessage: message => messages.push(message)
};
global.window = {parent, addEventListener: (type, handler) => {listeners[type] = handler;}};
Date.now = () => input.now;
eval(input.script);
if (input.repeatConfig) {
  for (let i=0; i<3; i++) listeners.message({source: parent, data: {
    type: 'streamlit:render', args: {config: input.repeatConfig}
  }});
}
process.stdout.write(JSON.stringify({saved, session, redirects, messages,
  values: messages.filter(message => message.type === 'streamlit:setComponentValue').map(message => message.value)
}));
"""
    completed = subprocess.run(
        [NODE, "-e", harness], input=json.dumps(fixture), text=True,
        capture_output=True, check=True, timeout=10,
    )
    return json.loads(completed.stdout)


@pytest.fixture
def emitted(monkeypatch):
    calls = []
    monkeypatch.setattr(access, "_component", lambda **kwargs: calls.append(kwargs))
    return calls


def html_for(call):
    config = dict(call["config"])
    action = config.pop("action")
    return access._access_html(action, config)


@pytest.mark.parametrize("role", sorted(access.STAFF_ROLES))
def test_verified_context_writes_expected_query_without_rerun(emitted, role):
    assert access.remember_staff_access(EVENT, role, "Coach Jamie", relative_url=query(role))
    assert emitted[0]["key"] == "compcoach_staff_return_remember"
    result = browser(html_for(emitted[0]))
    assert json.loads(result["saved"][access.STORAGE_KEY]) == {
        "schema": 1, "role": role, "event": EVENT["id"], "actor": "Coach Jamie",
        "url": query(role), "expiresAt": NOW + 30 * 24 * 60 * 60 * 1000,
    }
    assert result["values"] == []
    assert result["redirects"] == []


@pytest.mark.parametrize("url", [
    "https://example.test/?event=day-1&token=coach-capability&who=Coach+Jamie",
    "//other.test/?event=day-1&token=coach-capability&who=Coach+Jamie",
    "/?event=day-1&token=coach-capability&who=Coach+Jamie",
    query() + "#fragment", query() + "&event=day-2", query() + "&parent=1",
    query() + "&training=1", query() + "&home=1", query() + "&archive=1",
    query().replace("day-1", "day-2"), query().replace("coach-capability", "admin-capability"),
    query(actor="Other person"), query() + "&unknown=value", "?event=day-1&token=coach-capability",
    "?event=day-1&token=coach-capability&who=", query(actor="Coach\nJamie"), query() + "&malformed",
])
def test_unverified_context_and_foreign_routes_never_emit(emitted, url):
    assert not access.remember_staff_access(EVENT, "coach", "Coach Jamie", relative_url=url)
    assert not emitted


@pytest.mark.parametrize("role", ["parent", "spectator", "training", "", None, {}])
def test_nonstaff_roles_are_excluded(emitted, role):
    assert not access.remember_staff_access(EVENT, role, "Coach Jamie", relative_url=query())
    assert not emitted


def test_practice_never_overwrites_real_context(emitted):
    assert not access.remember_staff_access(EVENT, "coach", "Coach Jamie", relative_url=query(), is_training=True)
    assert not emitted


def test_admin_archive_query_is_supported(emitted):
    assert access.remember_staff_access(EVENT, "admin", "Coach Jamie", relative_url=query("admin", archive="1"))
    saved = json.loads(browser(html_for(emitted[0]))["saved"][access.STORAGE_KEY])
    assert saved["url"] == query("admin", archive="1")


def test_ttl_is_bounded_and_shorter_lifetime_supported(emitted):
    for ttl in (300, 7):
        access.remember_staff_access(EVENT, "coach", "Coach Jamie", relative_url=query(), ttl_days=ttl)
    for call, days in zip(emitted, (30, 7), strict=True):
        saved = json.loads(browser(html_for(call))["saved"][access.STORAGE_KEY])
        assert saved["expiresAt"] == NOW + days * 24 * 60 * 60 * 1000


@pytest.mark.parametrize("ttl", [0, -1, "30", True, None])
def test_invalid_ttl_never_emits(emitted, ttl):
    assert not access.remember_staff_access(EVENT, "coach", "Coach Jamie", relative_url=query(), ttl_days=ttl)
    assert not emitted


def test_names_cannot_escape_script_and_still_round_trip(emitted):
    name = 'Jamie </script><img src=x onerror="alert(1)"> & José'
    assert access.remember_staff_access(EVENT, "coach", name, relative_url=query(actor=name))
    html = html_for(emitted[0])
    assert "</script><img" not in html
    assert html.count("</script>") == 1
    assert json.loads(browser(html)["saved"][access.STORAGE_KEY])["actor"] == name


def test_landing_returns_record_through_component_without_navigation():
    saved = {access.STORAGE_KEY: json.dumps(record())}
    result = browser(access._access_html("restore"), stored=saved)
    assert result["values"] == [{"action": "restore", "record": record()}]
    assert result["redirects"] == []
    assert result["saved"] == saved
    assert any(message["type"] == "streamlit:componentReady" for message in result["messages"])


@pytest.mark.parametrize("location", [
    "https://example.test/coaching/?home=1", "https://example.test/coaching/?event=other-day",
    "https://example.test/coaching/?token=other-token", "https://example.test/coaching/?event=other&token=other",
])
def test_explicit_home_or_incoming_link_does_not_restore(location):
    saved = {access.STORAGE_KEY: json.dumps(record())}
    result = browser(access._access_html("restore"), location=location, stored=saved)
    assert result["values"] == [{"action": "restore", "record": None}]
    assert result["saved"] == saved
    assert result["redirects"] == []


def test_repeated_render_emits_one_stable_record():
    result = browser(
        access._access_html("restore"), stored={access.STORAGE_KEY: json.dumps(record())},
        repeat_config=access._config("restore"),
    )
    assert len(result["values"]) == 1
    assert result["redirects"] == []


INVALID_RECORDS = [
    {"schema": 9}, {"role": "parent"}, {"actor": "Other person"}, {"event": "other-day"},
    {"url": "https://other.test/?event=day-1&token=cap&who=Coach+Jamie"},
    {"url": "//other.test/?event=day-1&token=cap&who=Coach+Jamie"},
    {"url": "/another-app/?event=day-1&token=cap&who=Coach+Jamie"},
    {"url": query() + "&parent=1"}, {"url": query() + "&training=1"},
    {"url": query() + "&who=Coach+Jamie"}, {"url": query() + "#fragment"},
    {"url": query() + "&archive=1"}, {"expiresAt": NOW}, {"expiresAt": NOW - 1},
    {"expiresAt": "not a number"}, {"expiresAt": True},
    {"expiresAt": NOW + 31 * 24 * 60 * 60 * 1000},
]


@pytest.mark.parametrize("changes", INVALID_RECORDS)
def test_invalid_browser_record_discarded_without_navigation(changes):
    result = browser(access._access_html("restore"), stored={access.STORAGE_KEY: json.dumps(record(**changes))})
    assert result["values"] == [{"action": "restore", "record": None}]
    assert access.STORAGE_KEY not in result["saved"]
    assert result["redirects"] == []


def test_corrupt_storage_fails_quietly():
    result = browser(access._access_html("restore"), stored={access.STORAGE_KEY: "not json"})
    assert result["values"] == [{"action": "restore", "record": None}]
    assert access.STORAGE_KEY not in result["saved"]


def test_storage_restrictions_return_no_context():
    result = browser(access._access_html("restore"), fail_storage=True)
    assert result["values"] == [{"action": "restore", "record": None}]
    assert result["redirects"] == []


def test_forget_removes_only_staff_memory_without_return_value(emitted):
    access.clear_remembered_access()
    result = browser(
        html_for(emitted[0]), stored={access.STORAGE_KEY: json.dumps(record()), "unrelated": "keep"},
        session={access.STORAGE_KEY + ":restore-attempt": "attempt", "another": "keep"},
    )
    assert result["saved"] == {"unrelated": "keep"}
    assert result["session"] == {"another": "keep"}
    assert result["values"] == []
    assert result["redirects"] == []


def test_explicit_home_skips_component(emitted):
    assert access.restore_remembered_access(force_home=True) is None
    assert not emitted


def test_python_returns_candidate_for_server_authorization(monkeypatch):
    monkeypatch.setattr(access.time, "time", lambda: NOW / 1000)
    monkeypatch.setattr(access, "_component", lambda **kwargs: {"action": "restore", "record": record()})
    assert access.restore_remembered_access() == {
        "event": EVENT["id"], "token": EVENT["coach_token"], "who": "Coach Jamie",
        "role": "coach", "relative_url": query(),
    }


@pytest.mark.parametrize("changes", INVALID_RECORDS)
def test_python_revalidates_untrusted_component_record(monkeypatch, changes):
    monkeypatch.setattr(access.time, "time", lambda: NOW / 1000)
    monkeypatch.setattr(access, "_component", lambda **kwargs: {"action": "restore", "record": record(**changes)})
    assert access.restore_remembered_access() is None


@pytest.mark.parametrize("value", [None, True, {}, {"action": "remember", "record": record()},
    {"action": "restore", "record": None}, {"action": "restore", "record": "bad"}])
def test_invalid_component_payload_never_returns_candidate(monkeypatch, value):
    monkeypatch.setattr(access, "_component", lambda **kwargs: value)
    assert access.restore_remembered_access() is None


def test_static_frontend_never_attempts_top_navigation_or_parent_injection():
    source = (access._FRONTEND / "index.html").read_text()
    assert "location.replace" not in source
    assert "location.href =" not in source
    assert "parent.document" not in source
    assert "streamlit:setComponentValue" in source
