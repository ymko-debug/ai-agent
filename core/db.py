# db.py — RAW DATABASE LAYER
# ─────────────────────────────────────────────────────────────────
# This module owns all direct SQLite operations.
# No LLM calls. No prompt logic. No imports from memory.py.
# Anything that touches sqlite3.connect() lives here.
# ─────────────────────────────────────────────────────────────────

import sqlite3
from datetime import datetime
from .config import DB_PATH, DAILY_CALL_LIMIT


NS_USER     = "user"           # Facts about the real operator of this agent
NS_TASK     = "task"           # Project-scoped facts — always has expires_at
NS_SUBJECT  = "research"       # Facts about people/companies being researched — NEVER user
NS_AGENT    = "agent"          # Agent self-learnings and preferences

import logging
from typing import Optional
logger = logging.getLogger(__name__)

from datetime import timedelta


def _get_conn(readonly: bool = False) -> sqlite3.Connection:
    """Central connection factory — enforces WAL mode and busy_timeout."""
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn

def delete_core_memory(namespace: str, key: str) -> bool:
    """Delete a single fact by (namespace, key). Returns True if deleted."""
    conn = _get_conn()
    deleted = conn.execute(
        "DELETE FROM corememory WHERE namespace = ? AND key = ?",
        (namespace, key)
    ).rowcount
    conn.commit()
    conn.close()
    return bool(deleted)

def get_session_summary(session_id: str) -> str:
    """Retrieve the rolling summary for a session."""
    conn = _get_conn(readonly=True)
    row = conn.execute(
        "SELECT summary_text FROM sessionsummaries WHERE session_id=?",
        (session_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else ""

def save_session_summary(session_id: str, summary_text: str):
    """Upsert the session summary."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO sessionsummaries (session_id, summary_text)
        VALUES (?, ?)
        ON CONFLICT(session_id) DO UPDATE SET summary_text=excluded.summary_text
        """,
        (session_id, summary_text),
    )
    conn.commit()
    conn.close()

def update_core_memory(
    namespace: str,
    key: str,
    value: str,
    source: str = "agent_inferred",
    confidence: float = 0.5,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    expires_days: Optional[int] = None,
) -> bool:
    # PERMANENT SEAL — agent namespace is write-protected
    if namespace == NS_AGENT or namespace == "agent":
        logger.debug("Write blocked — agent namespace sealed. key=%s", key)
        return False

    """
    Write a fact to corememory. Returns False if blocked by confidence gate.
    HARD RULE: namespace 'user' requires confidence >= 0.85 and source != 'agent_inferred'.
    """
    if namespace == NS_USER and (confidence < 0.85 or source == "agent_inferred"):
        logger.warning(f"Blocked low-confidence user write: {key}={value} ({confidence})")
        return False

    expires_at = None
    if expires_days:
        expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    elif namespace == NS_TASK and expires_at is None:
        # Task facts always expire — default 7 days if not specified
        expires_at = (datetime.now() + timedelta(days=7)).isoformat()

    conn = _get_conn()
    conn.execute("""
        INSERT INTO corememory
            (namespace, key, value, source, confidence, project_id, session_id, expires_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(namespace, key) DO UPDATE SET
            value      = CASE WHEN excluded.confidence >= corememory.confidence
                              THEN excluded.value ELSE corememory.value END,
            confidence = MAX(excluded.confidence, corememory.confidence),
            source     = excluded.source,
            expires_at = excluded.expires_at,
            updated_at = excluded.updated_at
    """, (namespace, key, value, source, confidence, project_id,
          session_id, expires_at, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True


def get_core_memory(namespace: Optional[str] = None) -> list[dict]:
    """Retrieve memory entries, optionally filtered by namespace."""
    conn = _get_conn()
    if namespace:
        rows = conn.execute(
            "SELECT namespace, key, value, confidence, source, expires_at "
            "FROM corememory WHERE namespace = ? ORDER BY key",
            (namespace,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT namespace, key, value, confidence, source, expires_at "
            "FROM corememory ORDER BY namespace, key"
        ).fetchall()
    conn.close()
    return [
        {"namespace": r[0], "key": r[1], "value": r[2],
         "confidence": r[3], "source": r[4], "expires_at": r[5]}
        for r in rows
    ]


def purge_expired_memory():
    """Delete corememory entries whose expires_at has passed. Safe to call on every startup."""
    conn = _get_conn()
    deleted = conn.execute(
        "DELETE FROM corememory WHERE expires_at IS NOT NULL AND expires_at < ?",
        (datetime.now().isoformat(),)
    ).rowcount
    conn.commit()
    conn.close()
    if deleted:
        logger.info(f"Purged {deleted} expired memory entries.")

def init_db():
    conn = _get_conn()
    
    # WAL mode + busy_timeout set by _get_conn()
    # DROP line removed — table is permanent, schema is stable
    
    # Create powerful namespaced table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corememory (
            namespace      TEXT NOT NULL,
            key            TEXT NOT NULL,
            value          TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT 'agent_inferred',
            confidence     REAL NOT NULL DEFAULT 0.5,
            project_id     TEXT,
            session_id     TEXT,
            expires_at     TEXT,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (namespace, key)
        )
    """)
    
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            provider TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_key TEXT NOT NULL UNIQUE,
            result TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
        """
    )
    # Session names — short human-readable label derived from first user message
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_names (
            session_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    
    # Layer 2 rolling session summaries
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessionsummaries (
            session_id   TEXT PRIMARY KEY,
            summary_text TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # ── HITL: Active task persistence ──────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS active_tasks (
            session_id   TEXT PRIMARY KEY,
            task_type    TEXT NOT NULL,
            task_input   TEXT NOT NULL,  -- JSON string
            task_status  TEXT NOT NULL,  -- pending/running/done/failed
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


# ── Call logging ──────────────────────────────────────────────────────────────

def log_call(provider: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO call_log (date, provider, timestamp) VALUES (?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d"), provider, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def daily_call_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    conn  = _get_conn(readonly=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM call_log WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()
    return count


def is_over_daily_limit() -> bool:
    return daily_call_count() >= DAILY_CALL_LIMIT


# ── Conversation storage ──────────────────────────────────────────────────────

def save_message(session_id: str, role: str, content: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_history(session_id: str, limit: int):
    conn = _get_conn(readonly=True)
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def list_sessions(limit: int = 15):
    conn = _get_conn(readonly=True)
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM conversations ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def delete_session(session_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM conversations WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM session_names WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


def _delete_oldest_messages(session_id: str, count: int):
    conn = _get_conn()
    conn.execute(
        """
        DELETE FROM conversations WHERE id IN (
            SELECT id FROM conversations
            WHERE session_id=?
            ORDER BY id ASC
            LIMIT ?
        )
        """,
        (session_id, count),
    )
    conn.commit()
    conn.close()


# ── Session naming ────────────────────────────────────────────────────────────

def save_session_name(session_id: str, name: str):
    """Store a human-readable label for a session."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO session_names (session_id, name, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at
        """,
        (session_id, name, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_session_name(session_id: str) -> str | None:
    """Retrieve the human-readable label for a session, or None if not set."""
    conn = _get_conn(readonly=True)
    row  = conn.execute(
        "SELECT name FROM session_names WHERE session_id=?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_all_session_names(session_ids: list[str]) -> dict[str, str]:
    """Batch-fetch names for multiple sessions. Returns {session_id: name}."""
    if not session_ids:
        return {}
    conn  = _get_conn(readonly=True)
    placeholders = ",".join("?" * len(session_ids))
    rows  = conn.execute(
        f"SELECT session_id, name FROM session_names WHERE session_id IN ({placeholders})",
        session_ids,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ── Search / scrape cache ─────────────────────────────────────────────────────

def get_cached_search(query: str, ttl_hours: int = 1) -> str | None:
    key  = query.strip().lower()
    conn = _get_conn(readonly=True)
    row  = conn.execute(
        "SELECT result, cached_at FROM search_cache WHERE query_key=?", (key,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    result, cached_at_str = row
    try:
        cached_at = datetime.fromisoformat(cached_at_str)
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > ttl_hours:
            return None
    except ValueError:
        return None

    return result


def save_cached_search(query: str, result: str):
    key  = query.strip().lower()
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO search_cache (query_key, result, cached_at)
        VALUES (?, ?, ?)
        ON CONFLICT(query_key) DO UPDATE SET
            result    = excluded.result,
            cached_at = excluded.cached_at
        """,
        (key, result, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def purge_expired_cache(ttl_hours: int = 24):
    conn = _get_conn()
    conn.execute(
        "DELETE FROM search_cache WHERE (julianday('now') - julianday(cached_at)) * 24 > ?",
        (ttl_hours,),
    )
    conn.commit()
    conn.close()
# ── HITL: Task Management ──────────────────────────────────────────────────────

def save_active_task(session_id: str, task_type: str, task_input: dict):
    """Persist an active task state."""
    conn = _get_conn()
    import json
    conn.execute(
        """
        INSERT INTO active_tasks (session_id, task_type, task_input, task_status, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id) DO UPDATE SET
            task_type=excluded.task_type,
            task_input=excluded.task_input,
            task_status=excluded.task_status,
            updated_at=CURRENT_TIMESTAMP
        """,
        (session_id, task_type, json.dumps(task_input), "pending"),
    )
    conn.commit()
    conn.close()


def get_active_task(session_id: str) -> Optional[dict]:
    """Retrieve the current active task for a session."""
    conn = _get_conn(readonly=True)
    row = conn.execute(
        "SELECT task_type, task_input, task_status FROM active_tasks WHERE session_id=?",
        (session_id,),
    ).fetchone()
    conn.close()
    if row:
        import json
        return {
            "task_type":   row[0],
            "task_input":  json.loads(row[1]),
            "task_status": row[2],
        }
    return None


def clear_active_task(session_id: str):
    """Remove the active task entry."""
    conn = _get_conn()
    conn.execute("DELETE FROM active_tasks WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()
