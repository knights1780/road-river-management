
"""Construction status transition policies."""

NORMAL_TRANSITIONS = {
    "PLANNED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"PROCUREMENT", "CONTRACTED", "CANCELLED"},
    "PROCUREMENT": {"PREPARING", "CONTRACTED", "CANCELLED"},
    "CONTRACTED": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"ON_HOLD", "COMPLETED", "CANCELLED"},
    "ON_HOLD": {"IN_PROGRESS", "CANCELLED"},
    "COMPLETED": {"INSPECTED"},
    "INSPECTED": {"SETTLED"},
    "SETTLED": set(),
    "CANCELLED": set(),
}

EXCEPTION_TRANSITIONS = {
    ("COMPLETED", "IN_PROGRESS"),
    ("INSPECTED", "IN_PROGRESS"),
    ("SETTLED", "COMPLETED"),
    ("CANCELLED", "PLANNED"),
}


def validate_construction_transition(
    old_status: str,
    new_status: str,
    *,
    change_reason: str | None = None,
    confirmed: bool = False,
) -> bool:
    """Validate a construction status transition.

    Returns True for a confirmed exceptional transition.
    Returns False for a normal transition or no status change.
    """

    if old_status == new_status:
        return False

    reason = (change_reason or "").strip()

    if not reason:
        raise ValueError("Status change requires a reason")

    if new_status in NORMAL_TRANSITIONS.get(old_status, set()):
        return False

    if (old_status, new_status) in EXCEPTION_TRANSITIONS:
        if not confirmed:
            raise ValueError(
                "Exceptional status transition requires confirmation"
            )
        return True

    raise ValueError(
        "Invalid construction transition: "
        f"{old_status} -> {new_status}"
    )
