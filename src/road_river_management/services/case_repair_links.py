"""Link administrative requests to existing repairs, or create repairs from requests.

All writes use a SAVEPOINT. Callers must commit the enclosing transaction.
Authorization belongs to the API/controller; this service validates IDs and DB relations.
"""

import sqlite3
from collections.abc import Callable
from uuid import uuid4

REPAIR_TYPES = frozenset({"PLANNED", "EMERGENCY", "PREVENTIVE", "OTHER"})


def _id(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _actor(con: sqlite3.Connection, user_id: int) -> int:
    uid = _id(user_id, "user_id")
    exists = con.execute(
        "SELECT 1 FROM users WHERE user_id=? AND is_active=1 AND is_locked=0",
        (uid,),
    ).fetchone()
    if exists is None:
        raise ValueError("Active user not found")
    return uid


def _case(con: sqlite3.Connection, case_id: int) -> int:
    cid = _id(case_id, "case_id")
    if con.execute("SELECT 1 FROM administrative_cases WHERE case_id=?", (cid,)).fetchone() is None:
        raise ValueError("Case not found")
    return cid


def _repair_facility(con: sqlite3.Connection, repair_id: int) -> int:
    rid = _id(repair_id, "repair_id")
    row = con.execute(
        "SELECT facility_id FROM facility_repairs WHERE repair_id=?", (rid,)
    ).fetchone()
    if row is None:
        raise ValueError("Repair not found")
    return row[0]


def _atomic(con: sqlite3.Connection, action: Callable[[], int]) -> int:
    con.execute("SAVEPOINT case_repair_operation")
    try:
        result = action()
        con.execute("RELEASE SAVEPOINT case_repair_operation")
        return result
    except Exception:
        con.execute("ROLLBACK TO SAVEPOINT case_repair_operation")
        con.execute("RELEASE SAVEPOINT case_repair_operation")
        raise


def _link(con: sqlite3.Connection, cid: int, rid: int, uid: int, note: str | None) -> int:
    facility_id = _repair_facility(con, rid)
    # Carry the existing repair's facility into the case, without retyping it.
    con.execute(
        """INSERT OR IGNORE INTO administrative_case_facilities(case_id, facility_id)
           VALUES (?, ?)""",
        (cid, facility_id),
    )
    cursor = con.execute(
        """INSERT INTO case_repair_links(case_id, repair_id, linked_by_user_id, link_note)
           VALUES (?, ?, ?, ?)""",
        (cid, rid, uid, note),
    )
    link_id = cursor.lastrowid
    con.execute(
        """INSERT INTO case_repair_link_events
           (case_repair_link_id, event_type_code, actor_user_id, event_note)
           VALUES (?, 'LINKED', ?, ?)""",
        (link_id, uid, note),
    )
    return link_id


def link_repair_to_case(
    con: sqlite3.Connection,
    *,
    case_id: int,
    repair_id: int,
    actor_user_id: int,
    note: str | None = None,
) -> int:
    """Connect an existing repair to a request; auto-link its facility to the case.

    One active link per case/repair pair is allowed. Multiple different links
    are supported. Duplicate active links raise sqlite3.IntegrityError.
    """
    cid = _case(con, case_id)
    rid = _id(repair_id, "repair_id")
    uid = _actor(con, actor_user_id)
    if note is not None and not isinstance(note, str):
        raise ValueError("note must be text")
    return _atomic(con, lambda: _link(con, cid, rid, uid, note))


def create_repair_for_case(
    con: sqlite3.Connection,
    *,
    case_id: int,
    facility_id: int,
    repair_name: str,
    repair_type: str,
    actor_user_id: int,
    repair_description: str | None = None,
) -> dict:
    """Create exactly one repair and connect it to the case in one transaction.

    Reuses the selected facility; the case-to-facility relation is added if
    necessary. Dates, costs, and construction records are edited separately.
    """
    cid = _case(con, case_id)
    fid = _id(facility_id, "facility_id")
    uid = _actor(con, actor_user_id)
    if not isinstance(repair_name, str) or not repair_name.strip():
        raise ValueError("repair_name is required")
    if repair_type not in REPAIR_TYPES:
        raise ValueError("Invalid repair_type")
    if repair_description is not None and not isinstance(repair_description, str):
        raise ValueError("repair_description must be text")
    if con.execute(
        "SELECT 1 FROM managed_facilities WHERE facility_id=?", (fid,)
    ).fetchone() is None:
        raise ValueError("Facility not found")

    def perform() -> int:
        cur = con.execute(
            """INSERT INTO facility_repairs
               (repair_uuid, facility_id, repair_name, repair_type,
                repair_description, responsible_user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), fid, repair_name.strip(), repair_type, repair_description, uid),
        )
        rid = cur.lastrowid
        _link(con, cid, rid, uid, "Created from administrative case")
        return rid

    repair_id = _atomic(con, perform)
    return {"case_id": cid, "repair_id": repair_id, "facility_id": fid}


def unlink_repair_from_case(
    con: sqlite3.Connection,
    *,
    case_id: int,
    repair_id: int,
    actor_user_id: int,
    reason: str,
) -> int:
    """End a link while retaining the original record and append-only history."""
    cid = _id(case_id, "case_id")
    rid = _id(repair_id, "repair_id")
    uid = _actor(con, actor_user_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Unlink reason is required")
    row = con.execute(
        """SELECT case_repair_link_id FROM case_repair_links
           WHERE case_id=? AND repair_id=? AND unlinked_at IS NULL""",
        (cid, rid),
    ).fetchone()
    if row is None:
        raise ValueError("Active link not found")
    link_id = row[0]

    def perform() -> int:
        con.execute(
            """UPDATE case_repair_links
               SET unlinked_at=CURRENT_TIMESTAMP, unlinked_by_user_id=?, unlink_reason=?
               WHERE case_repair_link_id=? AND unlinked_at IS NULL""",
            (uid, reason.strip(), link_id),
        )
        con.execute(
            """INSERT INTO case_repair_link_events
               (case_repair_link_id, event_type_code, actor_user_id, event_note)
               VALUES (?, 'UNLINKED', ?, ?)""",
            (link_id, uid, reason.strip()),
        )
        return link_id

    return _atomic(con, perform)


def list_case_repairs(con: sqlite3.Connection, case_id: int) -> list[dict]:
    """Show repairs from a selected case, without duplicating repair records."""
    cid = _case(con, case_id)
    cur = con.execute(
        """SELECT l.case_repair_link_id, r.repair_id, r.facility_id,
                  f.facility_name, r.repair_name, r.repair_type,
                  r.started_on, r.completed_on, r.actual_cost_yen,
                  l.linked_at
           FROM case_repair_links AS l
           JOIN facility_repairs AS r ON r.repair_id=l.repair_id
           JOIN managed_facilities AS f ON f.facility_id=r.facility_id
           WHERE l.case_id=? AND l.unlinked_at IS NULL
           ORDER BY l.case_repair_link_id DESC""",
        (cid,),
    )
    keys = [col[0] for col in cur.description]
    return [dict(zip(keys, row, strict=True)) for row in cur]


def list_repair_cases(con: sqlite3.Connection, repair_id: int) -> list[dict]:
    """Show the requests connected to one repair."""
    rid = _id(repair_id, "repair_id")
    _repair_facility(con, rid)
    cur = con.execute(
        """SELECT l.case_repair_link_id, c.case_id, c.title, c.case_type_code,
                  c.status_code, c.received_on, l.linked_at
           FROM case_repair_links AS l
           JOIN administrative_cases AS c ON c.case_id=l.case_id
           WHERE l.repair_id=? AND l.unlinked_at IS NULL
           ORDER BY l.case_repair_link_id DESC""",
        (rid,),
    )
    keys = [col[0] for col in cur.description]
    return [dict(zip(keys, row, strict=True)) for row in cur]
