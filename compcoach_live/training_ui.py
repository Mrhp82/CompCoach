"""Shared practice instructions and the instructor's mobile controls.

Practice uses the ordinary athlete, call, result, and coverage screens. This
module only starts the isolated exercise and explains its current step; it
never provides replacement buttons for a coach's real workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st

try:
    from compcoach_live import training
    from compcoach_live.coach_picker import canonical_coach_names
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - Streamlit script entry point
    import training  # type: ignore[no-redef]
    from coach_picker import canonical_coach_names
    from storage import CompCoachError


STATUS_LABELS = {
    "running": "Exercise running",
    "paused": "Exercise paused",
    "completed": "Exercise complete",
    "stopped": "Exercise ended",
}


def _unique_names(values: list[Any]) -> list[str]:
    return canonical_coach_names(values)


def render_training_start(
    db: Any,
    source_meet: dict[str, Any] | None,
    actor: str,
    open_callback: Callable[[dict[str, Any]], Any],
    link_callback: Callable[[dict[str, Any]], str | None] | None = None,
) -> dict[str, Any] | None:
    """Render an Admin-only entry point; return the new practice day if opened.

    The caller enforces authorization and chooses the outer Home/Setup
    container. ``open_callback`` navigates to the exercise's private Admin
    link. An optional ``link_callback`` may supply the public coach link.
    """

    st.markdown("#### 🧪 Practice exercise")
    st.caption(
        "Share one practice link. Each coach types their own name and starts "
        "a separate course, from pools to the final bout, even if joining later."
    )
    st.info(
        "Practice creates separate exercises with fictional athletes and virtual teammates. "
        "Your current competition, assignments, and results stay unchanged."
    )
    scope = str((source_meet or {}).get("id") or "home")
    with st.form(f"training_start_{scope}"):
        duration_days = st.radio(
            "Practice link stays open for",
            [7, 14],
            format_func=lambda days: f"{days} days",
            horizontal=True,
            key=f"training_duration_{scope}",
        )
        st.caption(
            "Coaches can practice whenever they want. The app plays the coordinator "
            "and other coaches; you do not need to supervise or advance the scenarios."
        )
        st.caption(
            "You do not need to prepare a coach list. Even coaches who enter the "
            "same name get separate practice progress and personal links."
        )
        submitted = st.form_submit_button("Activate autonomous practice", width="stretch")
    if not submitted:
        return None

    try:
        meet = training.start_training(
            db,
            source_meet_id=(source_meet or {}).get("id"),
            coach_names=[],
            actor=actor,
            duration_days=duration_days,
            open_entry=True,
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return None

    st.success("Practice is ready. Open Share to copy the coach practice link.")
    if link_callback:
        link = link_callback(meet)
        if link:
            st.link_button("Coach practice link", link, width="stretch")
    open_callback(meet)
    return meet


def _run_instructor_action(action: Callable[[], Any]) -> Any:
    try:
        return action()
    except (CompCoachError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return None


def _expiry_label(value: Any, meet: dict[str, Any]) -> str:
    """Show practice expiry in the competition's configured timezone."""

    try:
        expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(ZoneInfo(str(meet.get("timezone") or "America/Los_Angeles")))
        return expiry.strftime("%b %d, %Y · %I:%M %p").replace("· 0", "· ")
    except (ValueError, TypeError, KeyError):
        return str(value)


def _next_guide_instruction(db: Any, metadata: dict[str, Any], instruction: str) -> str:
    """A reviewed AFM pairing has one remaining button, not another review."""
    stage = int(metadata.get("stage_index", metadata.get("stage", 0)) or 0)
    state = metadata.get("state") or {}
    expected_pair = {str(state.get("pair_a_id") or ""), str(state.get("pair_b_id") or "")}
    if stage != 14 or "" in expected_pair or len(expected_pair) != 2:
        return instruction
    for key in st.session_state:
        if not str(key).startswith("live_create_pending_"):
            continue
        pending = st.session_state[key]
        if not isinstance(pending, dict) or {str(pending.get("a") or ""), str(pending.get("b") or "")} != expected_pair:
            continue
        event_id = str(key).removeprefix("live_create_pending_")
        athletes = [db.get_athlete(event_id, str(pending.get(which) or "")) for which in ("a", "b")]
        if any(
            row is None or row.get("active_state") != "active"
            or row.get("participation_status", "active") != "active"
            for row in athletes
        ):
            continue
        if [int(row.get("version") or 0) for row in athletes] != pending.get("versions"):
            continue
        return (
            f"In Team situation → AFM vs AFM, tap Confirm pairing for "
            f"{state.get('pair_a_name') or athletes[0]['name']} and {state.get('pair_b_name') or athletes[1]['name']}."
        )
    return instruction


def _render_persistent_guide(
    stage: int, stage_count: int, title: str, instruction: str, paused: bool, *,
    guide_view: str = "", target_id: str = "", target_name: str = "",
) -> None:
    """Keep the next action visible beneath Streamlit's header while scrolling.

    A scoped top inset reserves the guide's full height at the start of the
    page. Only the title may ellipsize; the action remains readable and can
    scroll within the guide if a custom instruction is unusually long.
    """
    paused_label = " · Paused" if paused else ""
    st.html(
        f"""<style>
        .stApp:has(.cc-practice-guide) {{ --cc-guide-height: 100px; }}
        .stApp:has(.cc-practice-guide) .block-container {{
          padding-top: calc(4rem + env(safe-area-inset-top, 0px) + var(--cc-guide-height) + .6rem) !important;
        }}
        .stApp:has(.cc-practice-guide) [data-testid="stMain"] {{
          scroll-padding-top: calc(4rem + env(safe-area-inset-top, 0px) + var(--cc-guide-height) + .6rem);
        }}
        .cc-practice-guide {{
          position: fixed; top: calc(3.75rem + env(safe-area-inset-top, 0px));
          left: 50%; transform: translateX(-50%); width: min(736px, calc(100vw - 24px));
          height: var(--cc-guide-height, 100px); box-sizing: border-box;
          z-index: 998; padding: 9px 12px; overflow-y: auto;
          border: 1px solid #b7c8e8; border-radius: 12px;
          background: #f2f6ff; color: #183153; box-shadow: 0 3px 12px #1831531f;
          font-family: inherit;
        }}
        .cc-practice-guide-top {{display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px;}}
        .cc-practice-guide-step {{font-size: 11px; font-weight: 700; white-space: nowrap;}}
        .cc-practice-guide-title {{font-size: 13px; font-weight: 750; overflow: hidden;
          white-space: nowrap; text-overflow: ellipsis; min-width: 0;}}
        .cc-practice-guide-action {{font-size: 14px; line-height: 1.3; font-weight: 600;
          overflow-wrap: anywhere;}}
        @media (max-width: 600px) {{
          .stApp:has(.cc-practice-guide) {{--cc-guide-height: 110px;}}
          .cc-practice-guide {{width: calc(100vw - 16px); padding: 8px 10px;}}
          .cc-practice-guide-action {{font-size: 13.5px; line-height: 1.3;}}
        }}
        </style>
        <div class="cc-practice-guide" role="note" aria-label="Training practice guide"
             data-stage-index="{stage}" data-guide-view="{escape(guide_view, quote=True)}"
             data-target-athlete="{escape(target_id, quote=True)}" data-target-name="{escape(target_name, quote=True)}">
          <div class="cc-practice-guide-top">
            <span class="cc-practice-guide-step">🧪 Step {min(stage + 1, stage_count)} of {stage_count}{paused_label}</span>
            <span class="cc-practice-guide-title" title="{escape(title, quote=True)}">{escape(title)}</span>
          </div>
          <div class="cc-practice-guide-action">{escape(instruction)}</div>
        </div>""",
    )


def _render_hint(metadata: dict[str, Any], meet: dict[str, Any], status: str, instruction: str) -> None:
    with st.expander("Need a hint?"):
        st.caption(STATUS_LABELS.get(status, status.title()))
        if instruction:
            st.markdown(instruction)
        if metadata.get("expires_at"):
            st.caption(f"Practice access ends {_expiry_label(metadata['expires_at'], meet)}")
        if metadata.get("scenario_message"):
            st.info(str(metadata["scenario_message"]))
        if metadata.get("last_feedback"):
            st.caption(f"✓ {metadata['last_feedback']}")
        if metadata.get("hint"):
            st.markdown("**Hint**")
            st.markdown(str(metadata["hint"]))


def render_training_panel(
    db: Any,
    meet: dict[str, Any],
    metadata: dict[str, Any],
    role: str,
    actor: str,
    open_callback: Callable[[dict[str, Any]], Any] | None = None,
    home_callback: Callable[[], Any] | None = None,
    current_view: str | None = None,
    navigate_callback: Callable[[str], Any] | None = None,
) -> None:
    """Show the personal guided step without requiring an instructor."""

    status = str(metadata.get("status") or "running")
    is_hub = bool(metadata.get("is_hub") or metadata.get("kind") == "hub")
    meet_id = str(metadata.get("meet_id") or meet["id"])
    stage = int(metadata.get("stage_index", metadata.get("stage", 0)) or 0)
    stage_count = max(int(metadata.get("stage_count") or 1), 1)
    stage_title = str(metadata.get("stage_title") or "Practice")
    actor_tasks = metadata.get("actor_tasks") or {}
    instruction = str(actor_tasks.get(actor) or metadata.get("instruction") or "")
    feedback = str(metadata.get("last_feedback") or "")
    progress = max(0.0, min(float(metadata.get("progress") or 0), 1.0))
    persistent_guide = (
        role == "coach" and not is_hub and status in {"running", "paused"} and not metadata.get("expired")
    )
    if persistent_guide:
        guide_view = str(metadata.get("guide_view") or "")
        guide_instruction = _next_guide_instruction(
            db, metadata, str(metadata.get("guide_instruction") or instruction),
        )
        _render_persistent_guide(
            stage, stage_count, stage_title,
            guide_instruction, status == "paused",
            guide_view=guide_view,
            target_id=str(metadata.get("guide_target_id") or ""),
            target_name=str(metadata.get("guide_target_name") or ""),
        )
        # A coach can follow an instruction from either screen. Offer a
        # deliberate shortcut only when its target screen differs; it never
        # performs the athlete action or advances the exercise itself.
        if (
            navigate_callback is not None
            and current_view is not None
            and guide_view in {"My Group", "Live"}
            and guide_view != current_view
        ):
            label = "Open Team situation" if guide_view == "Live" else "Open My Group"
            st.button(
                label, key=f"training_task_view_{meet_id}_{stage}_{guide_view}",
                width="stretch", on_click=navigate_callback, args=(guide_view,),
            )
        _render_hint(metadata, meet, status, instruction)

    with (nullcontext() if persistent_guide else st.container(border=True)):
        if persistent_guide:
            pass
        else:
            st.markdown("**🧪 TRAINING · Practice only**")
        if is_hub or status in {"completed", "stopped"} or metadata.get("expired"):
            st.caption(STATUS_LABELS.get(status, status.title()))
            if metadata.get("expires_at"):
                st.caption(f"Practice access ends {_expiry_label(metadata['expires_at'], meet)}")
        if status == "stopped" or metadata.get("expired"):
            st.info("This exercise has ended. The practice link cannot change a real competition.")
        elif status == "completed":
            st.success("Exercise complete! You have practiced the full competition workflow.")
            learner = str(metadata.get("learner") or actor)
            participant = next(
                (person for person in metadata.get("participants") or [] if person.get("name") == learner), {}
            )
            skills = _unique_names(list(participant.get("milestones") or []))
            if skills:
                st.caption(f"Skills practiced: {' · '.join(skills)}")
            if feedback:
                st.caption(feedback)
        elif is_hub:
            st.success("Autonomous practice is open. Coaches can join whenever they are ready.")
            st.markdown(
                "Open **Share** and send the **coach practice link**. Each coach "
                "types their own name and starts a separate full course; the app "
                "plays the coordinator and other coaches."
            )
            st.caption("You can leave the app. Coach progress is saved automatically.")
        elif not persistent_guide:
            st.markdown(f"**Step {min(stage + 1, stage_count)} of {stage_count} · {stage_title}**")
            st.progress(progress)
            if instruction:
                st.markdown(instruction)
            if metadata.get("show_scenario") and metadata.get("scenario_message"):
                st.caption(str(metadata["scenario_message"]))
            if status == "paused":
                st.warning("Automatic practice scenarios are paused.")
            _render_hint(metadata, meet, status, instruction)

    if role != "admin":
        if role == "coach" and not is_hub and status in {"running", "paused"} and not metadata.get("expired"):
            with st.expander("Practice options"):
                st.caption(
                    "For an incorrect DE result, use Correct DE results. "
                    "You can also restart your own course; other coaches keep their progress."
                )
                if st.button("Restart my practice", key=f"training_restart_{meet_id}", width="stretch"):
                    fresh = _run_instructor_action(
                        lambda: training.restart_training(db, meet_id, actor)
                    )
                    if fresh is not None:
                        if open_callback:
                            open_callback(fresh)
                        st.rerun()
        if not is_hub and status == "completed" and not metadata.get("expired"):
            if st.button("Practice again", key=f"training_again_{meet_id}", width="stretch"):
                fresh = _run_instructor_action(
                    lambda: training.restart_training(db, meet_id, actor)
                )
                if fresh is not None:
                    if open_callback:
                        open_callback(fresh)
                    st.rerun()
        return

    with st.expander("Practice management"):
        st.caption(
            "Coaches complete the course independently. No instructor needs to "
            "stay online, pause the exercise, or advance its scenarios."
        )
        if status not in {"stopped", "completed"} and not metadata.get("expired"):
            if st.button("End practice access", key=f"training_end_{meet_id}", width="stretch"):
                result = _run_instructor_action(
                    lambda: training.stop_training(db, meet_id, actor)
                )
                if result is not None:
                    if home_callback:
                        home_callback()
                    st.rerun()
        elif home_callback:
            if st.button("Return Home", key=f"training_home_{meet_id}", width="stretch"):
                home_callback()

        st.caption("End practice access affects practice only; real competitions stay unchanged.")

        participants = list(metadata.get("participants") or [])
        if participants:
            with st.expander("Participation"):
                for participant in participants:
                    name = str(participant.get("name") or "Coach")
                    milestones = participant.get("milestones") or []
                    milestone_count = len(milestones) if isinstance(milestones, (list, dict)) else int(milestones)
                    views = participant.get("views") or []
                    viewed = ", ".join(str(view) for view in views) if isinstance(views, list) else str(views)
                    participant_status = str(participant.get("status") or "")
                    if participant_status == "completed":
                        state = "✅ Course complete"
                    elif participant_status in {"running", "paused"}:
                        participant_stage = int(participant.get("stage_index", participant.get("stage", 0)) or 0)
                        state = f"Step {participant_stage + 1} · {participant.get('stage_title') or 'Practicing'}"
                    elif participant_status in {"stopped", "expired"}:
                        state = "Practice ended"
                    else:
                        state = f"{milestone_count} completed action(s)" if viewed or milestone_count else "Not started yet"
                    st.markdown(f"**{name}** · {state}")
                    if int(participant.get("completed_runs") or 0):
                        st.caption(f"Completed practice runs: {int(participant['completed_runs'])}")
                    if viewed:
                        st.caption(f"Opened: {viewed.replace('Live', 'Team situation')}")
