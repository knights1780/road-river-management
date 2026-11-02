"""Administrative case workflow. Local SQLite, no web or cloud dependencies.

Only application service entry points are validated; direct SQL writers must be
restricted by the deployment's database access policy.
"""

import sqlite3
from datetime import date
from uuid import uuid4

CASE_TYPES = frozenset({'CONSULTATION', 'COMPLAINT_REQUEST', 'CIVIL_WORKS_REQUEST'})
IMPORTANCE = frozenset({'CRITICAL', 'HIGH', 'NORMAL', 'LOW'})
STATUSES = frozenset({'OPEN', 'IN_PROGRESS', 'ON_HOLD', 'CLOSED'})
RESPONSE_TYPES = frozenset({'CONTACT', 'SITE_VISIT', 'INVESTIGATION', 'REPAIR', 'NOTICE', 'OTHER'})


def _date(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be an ISO date')
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{name} must be an ISO date') from exc
    if not 1900 <= result.year <= 2200 or result.isoformat() != value:
        raise ValueError(f'{name} must be YYYY-MM-DD (1900-2200)')
    return value


def _optional_date(value: str | None, name: str) -> str | None:
    return None if value is None else _date(value, name)


def _id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} is required')
    return value.strip()


def _choice(value: str, choices: frozenset[str], name: str) -> str:
    if value not in choices:
        raise ValueError(f'invalid {name}')
    return value


def _case(connection: sqlite3.Connection, case_id: int) -> dict:
    cursor = connection.execute('SELECT * FROM administrative_cases WHERE case_id = ?',
                                (_id(case_id, 'case_id'),))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('Case not found')
    return dict(zip((x[0] for x in cursor.description), row, strict=True))


def get_case(connection: sqlite3.Connection, case_id: int) -> dict:
    """Read one case with immutable response and event history."""
    result = _case(connection, case_id)
    result['responses'] = [
        dict(zip((x[0] for x in cursor.description), row, strict=True))
        for cursor in [connection.execute(
            'SELECT * FROM administrative_case_responses WHERE case_id=? ORDER BY response_id',
            (case_id,))]
        for row in cursor.fetchall()
    ]
    result['events'] = [
        dict(zip((x[0] for x in cursor.description), row, strict=True))
        for cursor in [connection.execute(
            'SELECT * FROM administrative_case_events WHERE case_id=? ORDER BY event_id',
            (case_id,))]
        for row in cursor.fetchall()
    ]
    result['facility_ids'] = [r[0] for r in connection.execute(
        '''SELECT facility_id FROM administrative_case_facilities
           WHERE case_id=? ORDER BY facility_id''',
        (case_id,))]
    return result


def create_case(
    connection: sqlite3.Connection, *, case_type_code: str, title: str,
    received_on: str, created_by_user_id: int, description: str = '',
    due_on: str | None = None, importance_code: str = 'NORMAL',
    responsible_department_id: int | None = None,
    responsible_user_id: int | None = None,
    case_subtype_code: str | None = None,
    facility_ids: tuple[int, ...] = (),
) -> int:
    """Create case and CREATION event atomically. Returns case ID."""
    kind = _choice(case_type_code, CASE_TYPES, 'case_type_code')
    name = _text(title, 'title')
    received = _date(received_on, 'received_on')
    due = _optional_date(due_on, 'due_on')
    importance = _choice(importance_code, IMPORTANCE, 'importance_code')
    actor = _id(created_by_user_id, 'created_by_user_id')
    if not isinstance(description, str):
        raise TypeError('description must be text')
    for value, key in ((responsible_department_id, 'department_id'),
                       (responsible_user_id, 'user_id')):
        if value is not None:
            _id(value, key)
    if case_subtype_code is not None:
        _text(case_subtype_code, 'case_subtype_code')
    ids = [_id(f, 'facility_id') for f in facility_ids]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate facility_id')
    connection.execute('SAVEPOINT case_create')
    try:
        cursor = connection.execute(
            '''INSERT INTO administrative_cases
            (case_uuid, case_type_code, case_subtype_code, title, description,
             received_on, due_on, importance_code, responsible_department_id,
             responsible_user_id, created_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (str(uuid4()), kind, case_subtype_code, name, description, received,
             due, importance, responsible_department_id, responsible_user_id, actor),
        )
        case_id = cursor.lastrowid
        connection.executemany(
            'INSERT INTO administrative_case_facilities(case_id,facility_id) VALUES (?,?)',
            [(case_id, fid) for fid in ids],
        )
        connection.execute(
            '''INSERT INTO administrative_case_events
               (case_id,event_type_code,new_value,changed_by_user_id)
               VALUES (?,'CREATED',?,?)''', (case_id, kind, actor),
        )
        connection.execute('RELEASE SAVEPOINT case_create')
        return case_id
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT case_create')
        connection.execute('RELEASE SAVEPOINT case_create')
        raise


def add_response(
    connection: sqlite3.Connection, case_id: int, *, response_on: str,
    response_type_code: str, content: str, recorded_by_user_id: int,
    next_action: str | None = None,
) -> int:
    _case(connection, case_id)
    on = _date(response_on, 'response_on')
    kind = _choice(response_type_code, RESPONSE_TYPES, 'response_type_code')
    body = _text(content, 'content')
    actor = _id(recorded_by_user_id, 'recorded_by_user_id')
    if next_action is not None and not isinstance(next_action, str):
        raise ValueError('next_action must be text')
    cursor = connection.execute(
        '''INSERT INTO administrative_case_responses
        (case_id,response_on,response_type_code,content,next_action,recorded_by_user_id)
        VALUES (?,?,?,?,?,?)''', (case_id, on, kind, body, next_action, actor),
    )
    return cursor.lastrowid


def change_case_status(
    connection: sqlite3.Connection, case_id: int, *, status_code: str,
    changed_by_user_id: int, changed_on: str,
) -> None:
    current = _case(connection, case_id)
    status = _choice(status_code, STATUSES, 'status_code')
    actor = _id(changed_by_user_id, 'changed_by_user_id')
    changed = _date(changed_on, 'changed_on')
    if status == current['status_code']:
        raise ValueError('status has not changed')
    if current['status_code'] == 'CLOSED' and status != 'IN_PROGRESS':
        raise ValueError('closed case can only reopen IN_PROGRESS')
    # A completed case disappears from the active map and followup queue.
    connection.execute('SAVEPOINT case_status')
    try:
        connection.execute(
            '''UPDATE administrative_cases
            SET status_code=?, closed_on=?,
                followup_required=CASE WHEN ?='CLOSED' THEN 0 ELSE followup_required END,
                followup_on=CASE WHEN ?='CLOSED' THEN NULL ELSE followup_on END,
                followup_reason=CASE WHEN ?='CLOSED' THEN NULL ELSE followup_reason END,
                updated_at=CURRENT_TIMESTAMP WHERE case_id=?''',
            (status, changed if status == 'CLOSED' else None, status, status, status, case_id),
        )
        connection.execute(
            '''INSERT INTO administrative_case_events
            (case_id,event_type_code,previous_value,new_value,changed_by_user_id)
            VALUES (?,'STATUS_CHANGED',?,?,?)''',
            (case_id, current['status_code'], status, actor),
        )
        connection.execute('RELEASE SAVEPOINT case_status')
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT case_status')
        connection.execute('RELEASE SAVEPOINT case_status')
        raise


def set_followup(
    connection: sqlite3.Connection, case_id: int, *,
    changed_by_user_id: int, followup_on: str | None = None,
    followup_reason: str | None = None,
) -> None:
    current = _case(connection, case_id)
    actor = _id(changed_by_user_id, 'changed_by_user_id')
    if current['status_code'] == 'CLOSED':
        raise ValueError('closed case cannot have a followup')
    enabled = followup_on is not None
    on = _optional_date(followup_on, 'followup_on')
    if enabled:
        reason = _text(followup_reason, 'followup_reason')
    else:
        if followup_reason is not None:
            raise ValueError('followup_reason requires followup_on')
        reason = None
    connection.execute('SAVEPOINT case_followup')
    try:
        connection.execute(
            '''UPDATE administrative_cases
               SET followup_required=?,followup_on=?,followup_reason=?,
                   updated_at=CURRENT_TIMESTAMP WHERE case_id=?''',
            (int(enabled), on, reason, case_id),
        )
        connection.execute(
            '''INSERT INTO administrative_case_events
            (case_id,event_type_code,previous_value,new_value,changed_by_user_id)
            VALUES (?,'FOLLOWUP_CHANGED',?,?,?)''',
            (case_id, current['followup_on'], on, actor),
        )
        connection.execute('RELEASE SAVEPOINT case_followup')
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT case_followup')
        connection.execute('RELEASE SAVEPOINT case_followup')
        raise


def list_active_cases(
    connection: sqlite3.Connection, *, as_of: str,
    responsible_department_id: int | None = None,
) -> dict:
    """Read-only active queue; closed cases excluded without deletion."""
    day = _date(as_of, 'as_of')
    if responsible_department_id is not None:
        _id(responsible_department_id, 'responsible_department_id')
    cursor = connection.execute(
        '''SELECT case_id,case_type_code,title,importance_code,status_code,
                  received_on,due_on,followup_required,followup_on,followup_reason,
                  responsible_department_id,responsible_user_id
           FROM administrative_cases
           WHERE status_code <> 'CLOSED'
             AND (? IS NULL OR responsible_department_id=?)
           ORDER BY CASE importance_code WHEN 'CRITICAL' THEN 0
                      WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END,
                    due_on IS NULL, due_on, case_id''',
        (responsible_department_id, responsible_department_id),
    )
    names = [c[0] for c in cursor.description]
    cases = [dict(zip(names, row, strict=True)) for row in cursor]
    for item in cases:
        item['overdue'] = item['due_on'] is not None and item['due_on'] < day
        item['followup_due'] = bool(item['followup_required'] and item['followup_on'] <= day)
    return {
        'as_of': day,
        'active_count': len(cases),
        'overdue_count': sum(item['overdue'] for item in cases),
        'followup_due_count': sum(item['followup_due'] for item in cases),
        'cases': cases,
    }
