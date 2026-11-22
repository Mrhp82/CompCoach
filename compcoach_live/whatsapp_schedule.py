"""Plain-text assignment lists that a coach can scan in WhatsApp."""

from __future__ import annotations

from dataclasses import dataclass


MAIN = "🔹"
SIDE = "🔸"
LEGEND = f"{MAIN} *Main coach* · {SIDE} Side coach"


@dataclass
class PodAthlete:
    name: str
    coaches: tuple[str, ...]
    progress: str = ""


@dataclass
class EventPlan:
    title: str
    pools: list[dict]
    pods: dict[str, list[PodAthlete]]
    pairings: list[str]


def _pool_location(row: dict) -> str:
    return row["source_strip"] or (f"Pod {row['pod']}" if row["pod"] else "TBD")


def _pool_line(row: dict, *, side: bool) -> str:
    where = _pool_location(row)
    clock = f"{row['time_text']} · " if row["time_text"] else ""
    if side:
        peer = row["main_coach"]
        note = f" [M: *{peer[:3].upper()}*]" if peer else ""
    else:
        peer = row["side_coach"]
        note = f" [S: {peer[:3].upper()}]" if peer and peer != row["main_coach"] else ""
    detail = f"Pool {row['pool_no']}" if row["pool_no"] else (f"Pod {row['pod']}" if row["pod"] else "")
    suffix = f" · {detail}" if detail and detail not in where else ""
    return f"{SIDE if side else MAIN} {clock}{where}: {row['name']}{note}{suffix}"


def _single_event_pods(plan: EventPlan) -> list[str]:
    lines = []
    for pod, athletes in plan.pods.items():
        coaches = sorted({c for row in athletes for c in row.coaches})
        mixed = len({frozenset(row.coaches) for row in athletes}) > 1
        lines.extend([f"◆ *POD {pod}*", "Coaches: " + (" / ".join(coaches) or "Unassigned")])
        for row in athletes:
            peers = " · Coaches: " + (" / ".join(row.coaches) or "Unassigned") if mixed else ""
            lines.append(f"• {row.name}{peers}{row.progress}")
        lines.append("")
    return lines


def _needs_coach(names, roster: set[str]) -> bool:
    assigned = {name for name in names if name}
    return not assigned or not assigned.issubset(roster)


def schedule_lines(plans: list[EventPlan], active_coaches: list[str]) -> list[str]:
    """Keep each coach's selected events contiguous; put shared notices last."""
    if not plans:
        return []
    multi = len(plans) > 1
    has_pools = any(p.pools for p in plans)
    has_de = any(p.pods for p in plans)
    coaches = list(dict.fromkeys(active_coaches))
    roster = set(coaches)

    def event_label(plan: EventPlan, phase: str = "") -> str:
        suffix = f" · {phase}" if phase and has_pools and has_de else ""
        return f"_{plan.title}{suffix}_"

    lines = [] if multi else [f"📍 *{plans[0].title}*", ""]
    if has_pools:
        if not multi or not has_de:
            lines.extend(["*POOLS*", ""])
        lines.extend([LEGEND, ""])
    elif multi and has_de:
        lines.extend(["*DIRECT ELIMINATION*", ""])

    for coach in coaches:
        block = []
        has_main = False
        for plan in plans:
            mains = [row for row in plan.pools if row["main_coach"] == coach]
            sides = [row for row in plan.pools if row["side_coach"] == coach and row["main_coach"] != coach]
            if mains or sides:
                has_main = has_main or bool(mains)
                if multi:
                    block.append(event_label(plan, "Pools"))
                block.extend(_pool_line(row, side=False) for row in mains)
                block.extend(_pool_line(row, side=True) for row in sides)
            if multi:
                de_lines = []
                for pod, athletes in plan.pods.items():
                    for row in athletes:
                        if coach not in row.coaches:
                            continue
                        peers = " / ".join(c for c in row.coaches if c != coach)
                        note = f" · also assigned: {peers}" if peers else ""
                        de_lines.append(f"◆ Pod {pod}: {row.name}{note}{row.progress}")
                if de_lines:
                    block.extend([event_label(plan, "DE"), *de_lines])
        if block:
            heading = f"*{coach.upper()}*" if has_main else coach.upper()
            lines.extend([f"🤺 {heading}", *block, ""])

    if not multi and has_de:
        lines.extend(["*DIRECT ELIMINATION*", "", *_single_event_pods(plans[0])])

    needs, pairings, empty = [], [], []
    for plan in plans:
        if not plan.pools and not plan.pods:
            empty.append((f"{event_label(plan)} · " if multi else "") + "No active athletes yet.")
        for phase, rows in (
            ("Pools", [
                f"• {row['time_text'] + ' · ' if row['time_text'] else ''}{_pool_location(row)} · {row['name']}"
                for row in plan.pools
                if _needs_coach((row["main_coach"], row["side_coach"]), roster)
            ]),
            ("DE", [
                f"• Pod {pod} · {row.name}{row.progress}"
                for pod, athletes in plan.pods.items() for row in athletes
                if _needs_coach(row.coaches, roster)
            ]),
        ):
            if rows:
                if multi:
                    needs.append(event_label(plan, phase))
                elif has_pools and has_de:
                    needs.append(f"_{phase}_")
                needs.extend(rows)
        if plan.pairings:
            if multi:
                pairings.append(event_label(plan))
            pairings.extend(plan.pairings)
    if needs:
        lines.extend(["⚠️ *NEEDS COACH / REASSIGNMENT*", *needs, ""])
    if pairings:
        lines.extend([*pairings, ""])
    if empty:
        lines.extend([*empty, ""])
    return lines
