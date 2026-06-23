"""Phase 8 — append-only raw-event audit journal in Cassandra.

The cassandra-driver is **synchronous**; every blocking call is wrapped in
``asyncio.to_thread`` (CLAUDE.md rule). The audit log is best-effort: callers
(the webhook background task) catch failures and log ERROR — a Cassandra outage
must never block the pipeline. Inserts use bound parameters only; raw bodies are
never interpolated into the CQL string.
"""

import asyncio
import logging
import uuid
from datetime import datetime

from cassandra.cluster import Cluster, Session

from backend.config import settings

logger = logging.getLogger(__name__)

_session: Session | None = None

# Keyspace/table names come from config (not untrusted input), so identifier
# substitution via .format is safe here. Row VALUES are always bound params.
_CREATE_KEYSPACE = (
    "CREATE KEYSPACE IF NOT EXISTS {ks} "
    "WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}}"
)
_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS {ks}.events_by_day ("
    "day date, received_at timestamp, event_id uuid, "
    "source text, event_type text, raw_body text, "
    "PRIMARY KEY ((day), received_at, event_id))"
)
_INSERT = (
    "INSERT INTO {ks}.events_by_day "
    "(day, received_at, event_id, source, event_type, raw_body) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


def _connect() -> Session:
    cluster = Cluster(contact_points=settings.CASSANDRA_HOSTS, port=settings.CASSANDRA_PORT)
    return cluster.connect()


def _get_session() -> Session:
    global _session
    if _session is None:
        _session = _connect()
    return _session


def _init_sync() -> None:
    session = _get_session()
    ks = settings.CASSANDRA_KEYSPACE
    session.execute(_CREATE_KEYSPACE.format(ks=ks))
    session.execute(_CREATE_TABLE.format(ks=ks))


async def init_audit_log() -> None:
    """Create the keyspace + table if absent. Called once at startup."""
    await asyncio.to_thread(_init_sync)


def _append_sync(
    event_id: uuid.UUID,
    source: str,
    event_type: str,
    raw_body: str,
    received_at: datetime,
) -> None:
    session = _get_session()
    session.execute(
        _INSERT.format(ks=settings.CASSANDRA_KEYSPACE),
        (received_at.date(), received_at, event_id, source, event_type, raw_body),
    )


async def append_event(
    *,
    event_id: uuid.UUID,
    source: str,
    event_type: str,
    raw_body: str,
    received_at: datetime,
) -> None:
    """Append one inbound raw event to the audit journal (bound params only)."""
    await asyncio.to_thread(
        _append_sync, event_id, source, event_type, raw_body, received_at
    )
