"""Readable, touch-friendly result controls without replacing native widgets.

The keys are part of the rendering contract: ``pool_wins_``/``pool_losses_``
identify the two pool selectors, and ``won_``/``lost_`` (including their
``current_`` variants) identify the one-tap DE result buttons. Streamlit puts
these keys on their element containers, so navigation controls are unaffected.
"""

import streamlit as st


RESULT_STYLES = """
<style>
/* One native seven-option control, arranged as 0–3 / 4–6 at equal widths. */
.stElementContainer:is([class*="st-key-pool_wins_"], [class*="st-key-pool_losses_"])
    [data-testid="stButtonGroup"] [role="radiogroup"] {
    display: grid !important;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 6px !important;
    width: 100%;
}
.stElementContainer:is([class*="st-key-pool_wins_"], [class*="st-key-pool_losses_"])
    button[data-variant="segmented_control"] {
    flex: none !important;
    width: 100%;
    min-width: 0;
    min-height: 46px;
    margin: 0 !important;
    padding: .3rem .25rem !important;
    border-radius: 10px !important;
    font-weight: 700;
}

/* Pool labels and choices retain their normal selection/keyboard semantics. */
.stElementContainer[class*="st-key-pool_wins_"] {
    --cc-result-ink: #12683b;
    --cc-result-border: #6aa782;
    --cc-result-soft: #edf8f1;
    --cc-result-selected: #147443;
}
.stElementContainer[class*="st-key-pool_losses_"] {
    --cc-result-ink: #a92332;
    --cc-result-border: #d88d97;
    --cc-result-soft: #fff0f2;
    --cc-result-selected: #b32737;
}
.stElementContainer:is([class*="st-key-pool_wins_"], [class*="st-key-pool_losses_"])
    [data-testid="stWidgetLabel"] p {
    color: var(--cc-result-ink) !important;
    font-weight: 700;
}
.stElementContainer:is([class*="st-key-pool_wins_"], [class*="st-key-pool_losses_"])
    button[data-variant="segmented_control"]:not([disabled]) {
    color: var(--cc-result-ink) !important;
    background-color: var(--cc-result-soft) !important;
    border-color: var(--cc-result-border) !important;
}
.stElementContainer:is([class*="st-key-pool_wins_"], [class*="st-key-pool_losses_"])
    button[data-variant="segmented_control"][aria-checked="true"]:not([disabled]) {
    color: #ffffff !important;
    background-color: var(--cc-result-selected) !important;
    border-color: var(--cc-result-selected) !important;
}

/* Both the current-bout shortcut and the athlete card show the same colors. */
.stElementContainer:is([class*="st-key-won_"], [class*="st-key-current_won_"])
    [data-testid="stButton"] button:not([disabled]) {
    color: #12683b !important;
    background-color: #edf8f1 !important;
    border-color: #6aa782 !important;
}
.stElementContainer:is([class*="st-key-lost_"], [class*="st-key-current_lost_"])
    [data-testid="stButton"] button:not([disabled]) {
    color: #a92332 !important;
    background-color: #fff0f2 !important;
    border-color: #d88d97 !important;
}
.stElementContainer:is([class*="st-key-won_"], [class*="st-key-current_won_"])
    [data-testid="stButton"] button:not([disabled]):hover {
    background-color: #ddf0e4 !important;
}
.stElementContainer:is([class*="st-key-lost_"], [class*="st-key-current_lost_"])
    [data-testid="stButton"] button:not([disabled]):hover {
    background-color: #ffe1e5 !important;
}

/* Compact native call/result rows stay side by side on phones. */
@media (max-width: 640px) {
    :is([class*="st-key-cc_result_actions_"], [class*="st-key-cc_call_actions_"])
        [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
        gap: .5rem !important;
    }
    :is([class*="st-key-cc_result_actions_"], [class*="st-key-cc_call_actions_"])
        [data-testid="stHorizontalBlock"]
        > [data-testid="stColumn"] {
        flex: 1 1 0 !important;
        min-width: 0 !important;
    }
}
</style>
"""


def apply_result_styles() -> None:
    """Install styles after the app's general CSS; add no controls or state.

    ``st.html`` renders a style-only payload without a Markdown code block or
    empty content row. Labels, widget keys and form submission stay native.
    """
    st.html(RESULT_STYLES)
