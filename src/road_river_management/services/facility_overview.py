"""Read-only facility tree, map summary and progressive detail data.

No image or repair-to-case relation is invented when not stored in the schema.
"""

import sqlite3

DETAIL_TABLES = {
    "ROAD": "road_details",
    "BRIDGE": "bridge_details",
    "CLASS_A_RIVER": "river_details",
    "CLASS_B_RIVER": "river_details",
    "QUASI_RIVER": "river_details",
    "ORDINARY_RIVER": "river_details",
    "IRRIGATION_CANAL": "river_details",
    "CITY_PARK": "park_details",
    "DRAINAGE_STATION": "agricultural_facility_details",
    "PUMP_STATION": "agricultural_facility_details",
    "WATER_GATE": "agricultural_facility_details",
    "SLUICE": "agricultural_facility_details",
}


def _id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("facility_id must be a positive integer")
    return value


def _dict_rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = con.execute(sql, params)
    names = [x[0] for x in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _facility(con: sqlite3.Connection, facility_id: int) -> dict:
    rows = _dict_rows(con, """
        SELECT f.facility_id, f.facility_code, f.facility_name, f.description,
               f.installed_on, f.abolished_on, f.facility_status_id,
               f.responsible_department_id, t.facility_type_id, t.type_code,
               t.type_name, c.facility_category_id, c.category_code,
               c.category_name
        FROM managed_facilities AS f
        JOIN facility_types AS t ON t.facility_type_id=f.facility_type_id
        JOIN facility_categories AS c ON c.facility_category_id=t.facility_category_id
        WHERE f.facility_id=?
        """, (_id(facility_id),))
    if not rows:
        raise LookupError("Facility not found")
    return rows[0]


def list_facility_tree(con: sqlite3.Connection) -> dict:
    """Return categories > types > facilities > recursively nested components."""
    categories = _dict_rows(con, """
        SELECT facility_category_id, category_code, category_name
        FROM facility_categories ORDER BY sort_order, facility_category_id""")
    types = _dict_rows(con, """
        SELECT facility_type_id, facility_category_id, type_code, type_name
        FROM facility_types ORDER BY sort_order, facility_type_id""")
    facilities = _dict_rows(con, """
        SELECT facility_id, facility_type_id, facility_code, facility_name
        FROM managed_facilities ORDER BY facility_name, facility_id""")
    components = _dict_rows(con, """
        SELECT component_id, facility_id, parent_component_id,
               component_name, component_type, is_active
        FROM facility_components ORDER BY component_id""")

    type_by_id = {}
    category_by_id = {}
    facility_by_id = {}
    for cat in categories:
        cat["types"] = []
        category_by_id[cat["facility_category_id"]] = cat
    for item in types:
        item["facilities"] = []
        type_by_id[item["facility_type_id"]] = item
        category_by_id[item["facility_category_id"]]["types"].append(item)
    for facility in facilities:
        facility["components"] = []
        facility_by_id[facility["facility_id"]] = facility
        type_by_id[facility["facility_type_id"]]["facilities"].append(facility)

    components_by_id = {c["component_id"]: c for c in components}
    children: dict[int, list[dict]] = {}
    for comp in components:
        children.setdefault(comp["parent_component_id"], []).append(comp)

    # Build each tree from the facility roots, tracking visited nodes so a
    # corrupted parent chain cannot recursively hang a read-only API.
    def nest(comp: dict, seen: frozenset[int]) -> dict:
        cid = comp["component_id"]
        if cid in seen:
            return {**comp, "children": [], "hierarchy_error": "cycle"}
        path = seen | {cid}
        return {**comp, "children": [
            nest(child, path) for child in children.get(cid, [])
            if child["facility_id"] == comp["facility_id"]
        ]}

    for comp in components:
        parent = components_by_id.get(comp["parent_component_id"])
        if parent is None or parent["facility_id"] != comp["facility_id"]:
            facility = facility_by_id.get(comp["facility_id"])
            if facility is not None:
                facility["components"].append(nest(comp, frozenset()))

    return {"categories": categories}


def get_facility_summary(con: sqlite3.Connection, facility_id: int) -> dict:
    """Minimal map popup: title, counts, last inspection, recent repair.

    Photos require a separately reviewed image/attachment schema; return no
    image URL or filesystem path before one has been integrated.
    """
    facility = _facility(con, facility_id)
    fid = facility["facility_id"]
    repair_count = con.execute(
        "SELECT COUNT(*) FROM facility_repairs WHERE facility_id=?", (fid,)
    ).fetchone()[0]
    recent = _dict_rows(con, """
        SELECT repair_id, repair_name, completed_on, started_on
        FROM facility_repairs WHERE facility_id=?
        ORDER BY COALESCE(completed_on, started_on) DESC, repair_id DESC LIMIT 1
        """, (fid,))
    inspection = con.execute(
        "SELECT MAX(inspection_date) FROM facility_inspections WHERE facility_id=?",
        (fid,),
    ).fetchone()[0]
    case_counts = con.execute("""
        SELECT COUNT(*), COALESCE(SUM(CASE WHEN a.status_code <> 'CLOSED'
            THEN 1 ELSE 0 END), 0)
        FROM administrative_case_facilities AS cf
        JOIN administrative_cases AS a ON a.case_id=cf.case_id
        WHERE cf.facility_id=?""", (fid,)).fetchone()
    return {
        "facility": facility,
        "photo": None,
        "photo_status": "NOT_INTEGRATED",
        "repair_count": repair_count,
        "recent_repair": recent[0] if recent else None,
        "last_inspection_on": inspection,
        "related_case_count": case_counts[0],
        "active_case_count": case_counts[1],
    }


def get_facility_detail(con: sqlite3.Connection, facility_id: int) -> dict:
    """Detailed sections loaded only after the user opens the detail view."""
    facility = _facility(con, facility_id)
    fid = facility["facility_id"]
    detail_table = DETAIL_TABLES.get(facility["type_code"])
    # Table names are chosen exclusively from this hardcoded allow-list.
    attributes = _dict_rows(
        con, f"SELECT * FROM {detail_table} WHERE facility_id=?", (fid,)
    ) if detail_table else []
    components = _dict_rows(con, """
        SELECT component_id, parent_component_id, component_code,
               component_name, component_type, manufacturer, model_number,
               serial_number, installed_on, abolished_on, is_active, remarks
        FROM facility_components WHERE facility_id=? ORDER BY component_id""", (fid,))
    repairs = _dict_rows(con, """
        SELECT repair_id, repair_name, repair_type, started_on, completed_on,
               repair_description, actual_cost_yen, completion_result
        FROM facility_repairs WHERE facility_id=?
        ORDER BY COALESCE(completed_on, started_on) DESC, repair_id DESC""", (fid,))
    inspections = _dict_rows(con, """
        SELECT inspection_id, inspection_date, inspection_name, inspection_type,
               inspection_method, inspection_result, condition_rating,
               next_inspection_due_date
        FROM facility_inspections WHERE facility_id=?
        ORDER BY inspection_date DESC, inspection_id DESC""", (fid,))
    cases = _dict_rows(con, """
        SELECT a.case_id, a.title, a.case_type_code, a.status_code,
               a.received_on, a.importance_code
        FROM administrative_case_facilities AS cf
        JOIN administrative_cases AS a ON a.case_id=cf.case_id
        WHERE cf.facility_id=? ORDER BY a.received_on DESC, a.case_id DESC""", (fid,))
    maintenance = _dict_rows(con, """
        SELECT h.component_maintenance_id, h.component_id, h.maintenance_date,
               h.maintenance_type, h.maintenance_name, h.maintenance_description,
               h.cost_yen, h.result_description
        FROM component_maintenance_history AS h
        JOIN facility_components AS c ON c.component_id=h.component_id
        WHERE c.facility_id=?
        ORDER BY h.maintenance_date DESC, h.component_maintenance_id DESC""", (fid,))
    return {
        "facility": facility,
        "attributes": attributes[0] if attributes else None,
        "components": components,
        "repairs": repairs,
        "inspections": inspections,
        "cases": cases,
        "component_maintenance": maintenance,
        "repair_case_links_supported": False,
        "photo_status": "NOT_INTEGRATED",
    }
