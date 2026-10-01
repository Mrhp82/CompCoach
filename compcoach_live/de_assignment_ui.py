"""Mobile DE assignment controls: peer coach groups and optional multi-pod cover."""

from __future__ import annotations

from hashlib import sha1
from typing import Any, Callable

import streamlit as st

try:
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script
    from storage import CompCoachError


def _coaches(row: dict[str, Any], *, athlete: bool = False) -> list[str]:
    """Read the canonical list, including legacy assignments during migration."""
    field = "de_coaches" if athlete else "coaches"
    values = row.get(field)
    if values is None:
        values = [row.get("main_coach"), row.get("side_coach")]
    if not isinstance(values, (list, tuple)):
        values = []
    return list(dict.fromkeys(str(value).strip() for value in values if value and str(value).strip()))


def _revision(*values: object) -> str:
    return sha1(repr(values).encode("utf-8")).hexdigest()[:12]


def _writable(event: dict[str, Any], actor: str) -> bool:
    return event.get("status") == "open" and bool(str(actor or "").strip())


def _notice_key(event_id: object) -> str:
    return f"de_assignment_notice_{event_id}"


def _show_notice(event_id: object) -> None:
    notice = st.session_state.pop(_notice_key(event_id), None)
    if notice:
        kind, message = notice
        (st.error if kind == "error" else st.success)(message)


def _refresh(event_id: object, kind: str, message: str, *, clear_key: str = "") -> None:
    st.session_state[_notice_key(event_id)] = (kind, message)
    if clear_key:
        st.session_state[clear_key] = True
    st.rerun(scope="app")


def _queue_clear(key: str) -> None:
    st.session_state[key] = True


def _cancel_clear(key: str) -> None:
    st.session_state.pop(key, None)


def _valid_selection(key: str, options: list[str]) -> None:
    """Prune stale values before instantiating the widget in this run."""
    if key in st.session_state:
        st.session_state[key] = [value for value in st.session_state[key] if value in options]


def _coach_options(current: list[str], options: list[str]) -> list[str]:
    # Existing assignments remain editable even after a coach leaves today's roster.
    return list(dict.fromkeys([*options, *current]))


def _clear_confirmation(
    event: dict[str, Any], actor: str, *, scope: str, revision: str,
    label: str, on_confirm: Callable[[], Any],
) -> None:
    pending_key = f"de_assignment_clear_pending_{event['id']}_{scope}_{revision}"
    suffix = f"{event['id']}_{scope}_{revision}"
    if not st.session_state.get(pending_key):
        st.button(
            f"Remove coaches from {label}", key=f"de_assignment_clear_{suffix}",
            width="stretch", disabled=not _writable(event, actor),
            on_click=_queue_clear, args=(pending_key,),
        )
        return
    st.warning(f"Remove all assigned coaches from {label}?")
    confirm_column, cancel_column = st.columns(2)
    with confirm_column:
        confirmed = st.button(
            "Confirm removal", key=f"de_assignment_clear_confirm_{suffix}",
            width="stretch", disabled=not _writable(event, actor),
        )
    with cancel_column:
        st.button(
            "Cancel", key=f"de_assignment_clear_cancel_{suffix}", width="stretch",
            on_click=_cancel_clear, args=(pending_key,),
        )
    if confirmed:
        st.session_state.pop(pending_key, None)
        on_confirm()


def render_de_pod_assignments(
    db: Any, event: dict[str, Any], actor: str, visible: list[dict[str, Any]],
    coach_options: list[str], used_coaches: set[str], *,
    natural_sort_key: Callable[[object], Any], coach_format: Callable[[str], str],
) -> None:
    """Assign a peer group to one pod, or add one coach to 1–4 selected pods."""
    del used_coaches  # Labels and ordering are provided by the parent page.
    event_id = event["id"]
    _show_notice(event_id)
    pods = sorted({str(row.get("pod") or "").strip() for row in visible} - {""}, key=natural_sort_key)
    writable = _writable(event, actor)
    with st.expander("Assign DE pods", expanded=True):
        st.caption("All coaches assigned to a pod have the same role.")
        if not pods:
            st.caption("No pod can be derived yet. Import a list containing values such as M1 or P3.")
            return
        mode = st.radio(
            "Assignment", ["Coaches → pod", "Coach → pods"],
            key=f"de_assignment_mode_{event_id}", horizontal=True,
        )
        if mode == "Coach → pods":
            st.caption("Add the same coach to 1–4 pods. Existing coach groups are kept.")
            if not coach_options:
                st.info("Add a present coach to today's roster first.")
                return
            selection_key = f"de_assignment_multi_pods_{event_id}"
            clear_key = f"{selection_key}_clear"
            if st.session_state.pop(clear_key, False):
                st.session_state[selection_key] = []
            _valid_selection(selection_key, pods)
            with st.form(f"de_assignment_multi_form_{event_id}"):
                coach = st.selectbox(
                    "Coach", coach_options, format_func=coach_format,
                    key=f"de_assignment_multi_coach_{event_id}",
                )
                selected_pods = st.multiselect(
                    "Pods · choose up to 4", pods, max_selections=4, key=selection_key,
                )
                submitted = st.form_submit_button(
                    "Add coach to selected pods", type="primary", width="stretch",
                    disabled=not writable,
                )
            if submitted:
                if not selected_pods:
                    st.error("Choose at least one pod.")
                else:
                    try:
                        db.assign_de_pods(event_id, pods=selected_pods, coaches=[coach], actor=actor, mode="add")
                    except (CompCoachError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        _refresh(event_id, "success", f"{coach} added to pod{'s' if len(selected_pods) != 1 else ''} {', '.join(selected_pods)}.", clear_key=clear_key)
            return

        pod_key = f"de_assignment_pod_{event_id}"
        if st.session_state.get(pod_key) not in pods:
            st.session_state.pop(pod_key, None)
        pod = st.selectbox("Pod", pods, key=pod_key)
        assignment = next(
            (row for row in db.list_pod_assignments(event_id, phase="de") if str(row.get("pod") or "") == pod),
            {},
        )
        current = _coaches(assignment)
        revision = _revision(pod, current, assignment.get("version"), assignment.get("updated_at"))
        options = _coach_options(current, coach_options)
        coaches_key = f"de_assignment_pod_coaches_{event_id}_{pod}_{revision}"
        _valid_selection(coaches_key, options)
        with st.form(f"de_assignment_pod_form_{event_id}_{pod}_{revision}"):
            selected_coaches = st.multiselect(
                "Coaches", options, default=current, format_func=coach_format,
                key=coaches_key, disabled=not writable,
            )
            submitted = st.form_submit_button(
                "Save pod coaches", type="primary", width="stretch", disabled=not writable,
            )
        if submitted:
            if not selected_coaches:
                st.error("Select at least one coach. Use the removal button below to clear an assignment.")
            else:
                try:
                    db.assign_de_pods(event_id, pods=[pod], coaches=selected_coaches, actor=actor, mode="replace")
                except (CompCoachError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    _refresh(event_id, "success", f"Pod {pod}: {len(selected_coaches)} coach{'es' if len(selected_coaches) != 1 else ''} assigned.")
        if current:
            def clear_pod() -> None:
                try:
                    db.assign_de_pods(event_id, pods=[pod], coaches=[], actor=actor, mode="replace")
                except (CompCoachError, ValueError) as exc:
                    _refresh(event_id, "error", str(exc))
                else:
                    _refresh(event_id, "success", f"Coach assignment removed from pod {pod}.")

            _clear_confirmation(
                event, actor, scope=f"pod_{pod}", revision=revision,
                label=f"pod {pod}", on_confirm=clear_pod,
            )


def render_de_individual_assignments(
    db: Any, event: dict[str, Any], actor: str, visible: list[dict[str, Any]],
    coach_options: list[str], used_coaches: set[str], *,
    natural_sort_key: Callable[[object], Any], coach_format: Callable[[str], str],
) -> None:
    """Optional DE athlete overrides, using the same peer group model."""
    del used_coaches
    event_id = event["id"]
    ordered = sorted(visible, key=lambda row: (
        bool(_coaches(row, athlete=True)),
        natural_sort_key(row.get("source_strip") or row.get("pod") or ""),
        natural_sort_key(row.get("name") or ""),
    ))
    by_id = {str(row["id"]): row for row in ordered}
    selection_key = f"de_assignment_athletes_{event_id}"
    clear_key = f"{selection_key}_clear"
    if st.session_state.pop(clear_key, False):
        st.session_state[selection_key] = []
    _valid_selection(selection_key, list(by_id))

    def athlete_label(athlete_id: str) -> str:
        row = by_id[athlete_id]
        coaches = _coaches(row, athlete=True)
        details = [str(row.get("name") or "Athlete")]
        if row.get("pod"):
            details.append(f"Pod {row['pod']}")
        if coaches:
            details.append(" / ".join(coaches))
        return ("✓ " if coaches else "") + " · ".join(details)

    with st.expander("Assign selected DE athletes", expanded=False):
        st.caption("Optional individual assignment. Unassigned athletes are first; pod coach groups stay unchanged.")
        selected = st.multiselect("Athletes", list(by_id), format_func=athlete_label, key=selection_key)
        current_groups = [_coaches(by_id[athlete_id], athlete=True) for athlete_id in selected]
        current = current_groups[0] if current_groups and all(group == current_groups[0] for group in current_groups) else []
        revision = _revision([(athlete_id, by_id[athlete_id].get("version"), _coaches(by_id[athlete_id], athlete=True)) for athlete_id in selected])
        options = _coach_options(current, coach_options)
        coaches_key = f"de_assignment_individual_coaches_{event_id}_{revision}"
        _valid_selection(coaches_key, options)
        selected_coaches = st.multiselect(
            "Coaches for selected athletes", options, default=current,
            format_func=coach_format, key=coaches_key,
            disabled=not selected or not _writable(event, actor),
        )
        if st.button(
            f"Apply to {len(selected)} athlete{'s' if len(selected) != 1 else ''}",
            key=f"de_assignment_individual_apply_{event_id}", type="primary", width="stretch",
            disabled=not selected or not selected_coaches or not _writable(event, actor),
        ):
            try:
                db.assign_de_athletes(event_id, selected, coaches=selected_coaches, actor=actor)
            except (CompCoachError, ValueError) as exc:
                st.error(str(exc))
            else:
                _refresh(event_id, "success", f"{len(selected)} individual assignment{'s' if len(selected) != 1 else ''} saved.", clear_key=clear_key)
        if selected and any(current_groups):
            def clear_individual() -> None:
                try:
                    db.assign_de_athletes(event_id, selected, coaches=[], actor=actor)
                except (CompCoachError, ValueError) as exc:
                    _refresh(event_id, "error", str(exc))
                else:
                    _refresh(event_id, "success", f"Coaches removed from {len(selected)} selected athlete{'s' if len(selected) != 1 else ''}.", clear_key=clear_key)

            _clear_confirmation(
                event, actor, scope="athletes", revision=revision,
                label=f"{len(selected)} selected athlete{'s' if len(selected) != 1 else ''}",
                on_confirm=clear_individual,
            )
