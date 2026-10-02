"""Result styling keeps the native controls and their existing form contract."""

from pathlib import Path
import re

import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.result_styles import RESULT_STYLES


def _result_controls():
    import streamlit as st
    from compcoach_live.result_styles import apply_result_styles

    apply_result_styles()
    # An unrelated control must remain a normal native segmented control.
    st.segmented_control("View", ["My Group", "Team situation"], key="view_test")
    with st.form("pool_result_athlete_4"):
        wins = st.segmented_control(
            "Wins", list(range(7)), default=3, required=True,
            selection_mode="single", key="pool_wins_athlete_4", width="stretch",
        )
        losses = st.segmented_control(
            "Losses", list(range(7)), default=3, required=True,
            selection_mode="single", key="pool_losses_athlete_4", width="stretch",
        )
        if st.form_submit_button("Save pool result"):
            st.session_state["saved_pool_result"] = (wins, losses)
    with st.container(key="cc_result_actions_athlete"):
        left, right = st.columns(2)
        left.button("Won", key="won_athlete")
        right.button("Lost", key="lost_athlete")
    st.button("Won", key="current_won_athlete", type="primary")
    st.button("Lost", key="current_lost_athlete")
    with st.container(key="cc_call_actions_athlete"):
        for column, label in zip(st.columns(3), ("In the hole", "On deck", "Now")):
            column.button(label, key=f"call_{label}")


def test_native_form_has_one_seven_option_selector_per_result():
    app = AppTest.from_function(_result_controls).run()
    assert not app.exception
    groups = {item.label: item for item in app.get("button_group")}
    assert set(groups) == {"View", "Wins", "Losses"}
    for label, key in (("Wins", "pool_wins_athlete_4"), ("Losses", "pool_losses_athlete_4")):
        group = groups[label]
        assert group.key == key
        assert group.options == [str(number) for number in range(7)]
        assert group.value == 3
        assert group.proto.form_id == "pool_result_athlete_4"
        assert group._is_single_select
        assert group.proto.required
    result_buttons = [
        item for item in app.button
        if (item.key or "").startswith(("won_", "lost_", "current_won_", "current_lost_"))
    ]
    assert [(item.label, item.key) for item in result_buttons] == [
        ("Won", "won_athlete"), ("Lost", "lost_athlete"),
        ("Won", "current_won_athlete"), ("Lost", "current_lost_athlete"),
    ]


def test_selecting_six_or_zero_still_submits_a_single_native_result():
    app = AppTest.from_function(_result_controls).run()
    groups = {item.label: item for item in app.get("button_group")}
    groups["Wins"].set_value(6)
    groups["Losses"].set_value(0)
    assert "saved_pool_result" not in app.session_state
    next(item for item in app.button if item.label == "Save pool result").click().run()
    assert not app.exception
    assert app.session_state["saved_pool_result"] == (6, 0)
    groups = {item.label: item for item in app.get("button_group")}
    assert groups["Wins"].value == 6 and groups["Losses"].value == 0


def test_styles_render_as_native_html_and_do_not_add_another_widget():
    app = AppTest.from_function(_result_controls).run()
    assert not app.exception
    html = app.get("html")
    assert len(html) == 1
    assert html[0].proto.body.strip().startswith("<style>")
    assert html[0].proto.body.strip().endswith("</style>")
    assert not app.code
    assert not app.get("component_instance")
    # No global button-group selector: this leaves the navigation unchanged.
    assert "st-key-view_test" not in RESULT_STYLES
    assert 'grid-template-columns: repeat(4, minmax(0, 1fr))' in RESULT_STYLES
    assert 'st-key-cc_result_actions_' in RESULT_STYLES
    assert 'st-key-cc_call_actions_' in RESULT_STYLES


def test_compact_row_hooks_wrap_native_equal_width_columns():
    app = AppTest.from_function(_result_controls).run()
    wrappers = {
        item.proto.id.rsplit("-", 1)[-1]: item
        for item in app.get("flex_container") if item.proto.id
    }
    for key, count in (("cc_result_actions_athlete", 2), ("cc_call_actions_athlete", 3)):
        wrapper = wrappers[key]
        row, = wrapper.children.values()
        assert row.proto.flex_container.direction == row.proto.flex_container.HORIZONTAL
        columns = list(row.children.values())
        assert len(columns) == count
        assert all(column.type == "column" for column in columns)
        assert all(column.proto.weight == 1 / count for column in columns)


def test_result_selectors_match_the_installed_native_frontend_contract():
    """Verify the actual DOM hooks rather than assuming radio input markup."""
    frontend = Path(st.__file__).parent / "static" / "static" / "js"
    button_group = next(frontend.glob("ButtonGroup.*.js")).read_text()
    styled = "\n".join(path.read_text() for path in frontend.glob("styled-components.*.js"))
    button = next(frontend.glob("Button.*.js")).read_text()
    index = next(frontend.glob("index.*.js")).read_text()

    assert '"data-testid":`stButtonGroup`' in button_group
    assert '"data-variant"' in button_group and '`segmented_control`' in button_group
    assert 'role:' in styled and '`radiogroup`' in styled
    assert '`aria-checked`' in styled
    assert '"data-testid":`stButton`' in button
    # Native keyed controls really have the st-key class on their outer node.
    assert re.search(r'className:.*?`stElementContainer`.*?Sx\(Cx\(', index)
    assert '`st-key-`' in index
    assert '"data-testid":`stColumn`' in index
    assert 'HORIZONTAL?`stHorizontalBlock`:`stVerticalBlock`' in index
    assert '"data-testid":_x(t)' in index
