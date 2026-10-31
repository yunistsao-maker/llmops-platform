"""SQL for requests.requests / decisions / audit. Every function runs in the caller's transaction."""
from __future__ import annotations

import base64
import datetime as dt
import json
import uuid

from psycopg import sql
from psycopg.types.json import Jsonb

from .domain import REVIEW_GATES

COLUMNS = ("id", "created_at", "updated_at", "requester_sub", "requester_email", "team", "alias", "use_case",
           "models", "unsure", "data_class", "data_class_other", "cloud", "rpd", "tokens_in", "tokens_out",
           "target_date", "external", "taiwan_only", "note", "status", "gates", "key_alias", "secret_ref")
_WRITABLE = frozenset(COLUMNS) - {"id", "created_at", "updated_at"}
_SELECT = sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS)


class BadCursor(ValueError):
    pass


def _value(column: str, value):
    return Jsonb(value) if column == "gates" else value


def insert_request(conn, request_id: uuid.UUID, fields: dict) -> dict:
    cols = [c for c in fields if c in _WRITABLE]
    query = sql.SQL("INSERT INTO requests.requests (id, {cols}) VALUES (%s, {vals}) RETURNING {sel}").format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in cols), sel=_SELECT)
    return conn.execute(query, [request_id, *(_value(c, fields[c]) for c in cols)]).fetchone()


def get_request(conn, request_id: uuid.UUID, *, lock: bool = False) -> dict | None:
    query = sql.SQL("SELECT {sel} FROM requests.requests WHERE id = %s" + (" FOR UPDATE" if lock else "")).format(sel=_SELECT)
    return conn.execute(query, (request_id,)).fetchone()


def update_request(conn, request_id: uuid.UUID, **fields) -> dict:
    unknown = set(fields) - _WRITABLE
    if unknown:
        raise ValueError(f"not writable: {sorted(unknown)}")
    sets = [sql.SQL("{} = %s").format(sql.Identifier(c)) for c in fields] + [sql.SQL("updated_at = now()")]
    query = sql.SQL("UPDATE requests.requests SET {sets} WHERE id = %s RETURNING {sel}").format(
        sets=sql.SQL(", ").join(sets), sel=_SELECT)
    return conn.execute(query, [*(_value(c, v) for c, v in fields.items()), request_id]).fetchone()


def add_decision(conn, request_id, gate: str, decision: str, comment: str | None, actor_sub: str,
                 actor_email: str | None) -> dict:
    return conn.execute(
        "INSERT INTO requests.decisions (request_id, gate, decision, comment, actor_sub, actor_email) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at",
        (request_id, gate, decision, comment, actor_sub, actor_email)).fetchone()


def add_audit(conn, request_id, action: str, actor: str, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO requests.audit (request_id, action, actor, detail) VALUES (%s, %s, %s, %s)",
                 (request_id, action, actor, Jsonb(detail or {})))


def count_audit(conn, request_id, actions: tuple[str, ...]) -> int:
    return conn.execute("SELECT count(*) AS n FROM requests.audit WHERE request_id = %s AND action = ANY(%s)",
                        (request_id, list(actions))).fetchone()["n"]


def count_open(conn, requester_sub: str) -> int:
    """Open requests of one user. Takes a per-user transaction lock first, so concurrent submissions by the same
    user are counted one after another and MAX_OPEN_REQUESTS_PER_USER holds exactly."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('request-api:open:' || %s))", (requester_sub,))
    return conn.execute("SELECT count(*) AS n FROM requests.requests WHERE requester_sub = %s "
                        "AND status IN ('in_review', 'changes_requested')", (requester_sub,)).fetchone()["n"]


def decisions(conn, request_id) -> list[dict]:
    return conn.execute("SELECT id, gate, decision, comment, actor_email, actor_sub, created_at FROM requests.decisions "
                        "WHERE request_id = %s ORDER BY created_at, id", (request_id,)).fetchall()


def history(conn, request_id, limit: int = 200) -> list[dict]:
    return conn.execute("SELECT action, actor, detail, created_at FROM requests.audit WHERE request_id = %s "
                        "ORDER BY created_at, id LIMIT %s", (request_id, limit)).fetchall()


def latest_audit(conn, request_id, action: str) -> dict | None:
    return conn.execute("SELECT detail, created_at FROM requests.audit WHERE request_id = %s AND action = %s "
                        "ORDER BY created_at DESC, id DESC LIMIT 1", (request_id, action)).fetchone()


# -- subscriptions ("notify me when this model can be requested") ------------------------------------------
class SubscriptionLimit(Exception):
    pass


def list_subscriptions(conn, subscriber_sub: str) -> list[dict]:
    return conn.execute("SELECT model_id, created_at FROM requests.subscriptions WHERE subscriber_sub = %s "
                        "ORDER BY model_id", (subscriber_sub,)).fetchall()


def subscribe(conn, subscriber_sub: str, email: str | None, model_id: str, cap: int) -> tuple[dict, bool]:
    """Idempotent: returns (row, created). Concurrent calls of one user are serialised by a transaction lock,
    so the cap holds exactly and two PUTs of the same model never collide on the primary key."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('request-api:subscriptions:' || %s))", (subscriber_sub,))
    row = conn.execute("SELECT model_id, created_at, subscriber_email FROM requests.subscriptions "
                       "WHERE subscriber_sub = %s AND model_id = %s", (subscriber_sub, model_id)).fetchone()
    if row:
        if email and row["subscriber_email"] != email:     # keep the address current (email claims can change)
            conn.execute("UPDATE requests.subscriptions SET subscriber_email = %s WHERE subscriber_sub = %s "
                         "AND model_id = %s", (email, subscriber_sub, model_id))
        return row, False
    if cap > 0:
        count = conn.execute("SELECT count(*) AS n FROM requests.subscriptions WHERE subscriber_sub = %s",
                             (subscriber_sub,)).fetchone()["n"]
        if count >= cap:
            raise SubscriptionLimit(cap)
    row = conn.execute("INSERT INTO requests.subscriptions (subscriber_sub, subscriber_email, model_id) "
                       "VALUES (%s, %s, %s) RETURNING model_id, created_at", (subscriber_sub, email, model_id)).fetchone()
    return row, True


def unsubscribe(conn, subscriber_sub: str, model_id: str) -> bool:
    return conn.execute("DELETE FROM requests.subscriptions WHERE subscriber_sub = %s AND model_id = %s",
                        (subscriber_sub, model_id)).rowcount > 0


# -- listing ----------------------------------------------------------------------------------------------
def encode_cursor(row: dict) -> str:
    raw = json.dumps([row["created_at"].isoformat(), str(row["id"])]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created, rid = json.loads(raw)
        return dt.datetime.fromisoformat(created), uuid.UUID(rid)
    except (ValueError, TypeError) as exc:
        raise BadCursor("invalid cursor") from exc


def _pending(gate: str) -> sql.Composable:
    return sql.SQL("gates @> {}::jsonb").format(sql.Literal(json.dumps([{"gate": gate, "status": "pending"}])))


def next_gate_filter(gate: str) -> sql.Composable:
    """Requests whose next decision is `gate` (G2/G8: that gate pending and every earlier review gate done)."""
    if gate == "G9":
        return sql.SQL("status = 'trial'")
    parts = [sql.SQL("status = 'in_review'"), _pending(gate)]
    for earlier in REVIEW_GATES[:REVIEW_GATES.index(gate)]:
        parts.append(sql.SQL("NOT ") + _pending(earlier))
    return sql.SQL(" AND ").join(parts)


def list_requests(conn, *, requester_sub: str | None = None, statuses: list[str] | None = None,
                  next_gate: str | None = None, limit: int = 50, cursor: str | None = None) -> tuple[list[dict], str | None]:
    where: list[sql.Composable] = []
    params: list = []
    if requester_sub is not None:
        where.append(sql.SQL("requester_sub = %s"))
        params.append(requester_sub)
    if statuses:
        where.append(sql.SQL("status = ANY(%s)"))
        params.append(list(statuses))
    if next_gate:
        where.append(sql.SQL("(") + next_gate_filter(next_gate) + sql.SQL(")"))
    if cursor:
        created, rid = decode_cursor(cursor)
        where.append(sql.SQL("(created_at, id) < (%s, %s)"))
        params.extend([created, rid])
    query = sql.SQL("SELECT {sel} FROM requests.requests {where} ORDER BY created_at DESC, id DESC LIMIT %s").format(
        sel=_SELECT, where=(sql.SQL("WHERE ") + sql.SQL(" AND ").join(where)) if where else sql.SQL(""))
    rows = conn.execute(query, [*params, limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if more and rows else None)
