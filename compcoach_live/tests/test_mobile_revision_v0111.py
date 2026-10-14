"""Mobile monitoring stays compact while the existing operations remain usable."""

from compcoach_live.tests.test_app_smoke import keyed, nav_named, open_board
from compcoach_live.tests.test_mobile_group_flow import group
from compcoach_live.tests.test_team_grouping_app import grouped_scenario, _team


def visible_nodes(block):
    """Model collapsed expanders as the one tappable row a phone displays."""
    for node in block.children.values():
        yield node
        if hasattr(node, "children") and not (
            node.type == "expander" and not node.proto.expanded
        ):
            yield from visible_nodes(node)


def test_my_group_shows_current_bout_and_active_athletes_before_staff_panels(group):
    _database, meet, _events, athletes = group
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    nodes = list(visible_nodes(app.main))
    current = next(index for index, node in enumerate(nodes)
                   if node.type == "markdown" and "cc-current-bout" in node.value)
    first_card = next(index for index, node in enumerate(nodes)
                      if node.type == "markdown" and "class='cc-athlete'" in node.value)
    staff = next(index for index, node in enumerate(nodes)
                 if node.type == "expander" and node.label.startswith("Availability & staff activity"))
    events = next(index for index, node in enumerate(nodes)
                  if node.type == "expander" and node.label == "Events & available coaches")
    assert current < first_card < staff < events
    assert athletes["EXAMPLE Now"]["name"] in nodes[first_card].value
    assert "cc-strip" in nodes[first_card].value and "Strip G4" in nodes[first_card].value
    assert not any(node.type == "button" and node.label == "I’m available to help" for node in nodes[:first_card])
    assert not app.exception


def test_team_situation_is_tappable_list_with_operations_hidden_until_expanded(grouped_scenario):
    _database, meet, _events, athletes = grouped_scenario
    app = _team(meet, view="Needs Coach")
    nodes = list(visible_nodes(app.main))
    rows = [(index, node) for index, node in enumerate(nodes)
            if node.type == "expander" and "**EXAMPLE" in node.label]
    assert len(rows) == 2
    assert all(not row.proto.expanded for _, row in rows)
    assert any("EXAMPLE Called" in row.label and "Strip M4" in row.label and "Now" in row.label for _, row in rows)
    assert not any(node.type == "button" and node.label in {"Won", "Lost"} for node in nodes)
    coaches = next(index for index, node in enumerate(nodes)
                   if node.type == "expander" and node.label == "Coaches")
    assert max(index for index, _ in rows) < coaches
    called = athletes["EXAMPLE Called"]
    keyed(app.button, f"won_{called['id']}").click().run()
    assert not app.exception
    assert _database.get_athlete(called["event_id"], called["id"])["de_wins"] == 1


def test_event_name_opens_live_details_without_losing_personal_view(group):
    _database, meet, _events, athletes = group
    athlete = athletes["EXAMPLE Now"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    link = keyed(app.button, f"event_details_personal_card_{athlete['id']}_")
    assert link.label == "Cadet Epee"
    link.click().run()
    assert any(item.value == "Cadet Epee" for item in app.subheader)
    assert any(item.value == "**Assignments and live calls**" for item in app.markdown)
    assert not any((button.key or "").startswith("event_detail_phase_") for button in app.button)
    assert nav_named(app, "coach_nav_").value == "My Group"
    assert not app.exception


def test_event_details_phase_action_is_scoped_to_selected_event(group):
    database, meet, events, athletes = group
    athlete = athletes["EXAMPLE Now"]
    app = open_board(meet["id"], meet["coordinator_token"], "Irina")
    keyed(app.button, f"event_details_live_card_{athlete['id']}_").click().run()
    keyed(app.button, f"event_detail_phase_{athlete['event_id']}_de_").click().run()
    assert database.get_phase_states(athlete["event_id"])["de"]["started"]
    other = next(child for child in events if child["id"] != athlete["event_id"])
    assert not database.get_phase_states(other["id"])["de"]["started"]
    assert nav_named(app, "coordinator_nav_").value == "Live"
    keys = [button.key for button in app.button if button.key]
    assert len(keys) == len(set(keys))
    assert not app.exception
