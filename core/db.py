# db.py — RAW DATABASE LAYER (PostgreSQL Migration)
# ─────────────────────────────────────────────────────────────────
# This module owns all direct PostgreSQL operations via Supabase.
# No LLM calls. No prompt logic.
# ─────────────────────────────────────────────────────────────────

import os
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Constants for namespaces
NS_USER     = "user"
NS_TASK     = "task"
NS_SUBJECT  = "research"
NS_AGENT    = "agent"

DAILY_CALL_LIMIT = int(os.getenv("DAILY_CALL_LIMIT", 200))
DATABASE_URL = os.getenv("DATABASE_URL")

# Connection Pool Initialization
_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            logger.error("DATABASE_URL environment variable is not set!")
            raise ValueError("DATABASE_URL is required for PostgreSQL migration")
        try:
            # SimpleConnectionPool for basic thread safety in FastAPI
            _pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")
            raise
    return _pool

class DbConn:
    """Context manager for pool-based connections."""
    def __init__(self):
        self.conn = None

    def __enter__(self):
        self.conn = _get_pool().getconn()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
            _get_pool().putconn(self.conn)

def init_db():
    with DbConn() as conn:
        with conn.cursor() as cur:
            # 1. corememory
            cur.execute("""
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
            
            # 2. conversations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            
            # 3. call_log
            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_log (
                    id SERIAL PRIMARY KEY,
                    date TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            
            # 4. search_cache
            cur.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    id SERIAL PRIMARY KEY,
                    query_key TEXT NOT NULL UNIQUE,
                    result TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            
            # 5. session_names
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_names (
                    session_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # 6. sessionsummaries
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessionsummaries (
                    session_id   TEXT PRIMARY KEY,
                    summary_text TEXT NOT NULL,
                    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 7. active_tasks
            cur.execute("""
                CREATE TABLE IF NOT EXISTS active_tasks (
                    session_id   TEXT PRIMARY KEY,
                    task_type    TEXT NOT NULL,
                    task_input   TEXT NOT NULL,
                    task_status  TEXT NOT NULL,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    logger.info("PostgreSQL database initialized successfully")

# ── Core Memory ──────────────────────────────────────────────────────────────

def delete_core_memory(namespace: str, key: str) -> bool:
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corememory WHERE namespace = %s AND key = %s",
                (namespace, key)
            )
            return cur.rowcount > 0

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
    if namespace == NS_AGENT or namespace == "agent":
        return False

    if namespace == NS_USER and (confidence < 0.85 or source == "agent_inferred"):
        return False

    expires_at = None
    if expires_days:
        expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    elif namespace == NS_TASK:
        expires_at = (datetime.now() + timedelta(days=7)).isoformat()

    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO corememory
                    (namespace, key, value, source, confidence, project_id, session_id, expires_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (namespace, key) DO UPDATE SET
                    value      = CASE WHEN EXCLUDED.confidence >= corememory.confidence
                                      THEN EXCLUDED.value ELSE corememory.value END,
                    confidence = GREATEST(EXCLUDED.confidence, corememory.confidence),
                    source     = EXCLUDED.source,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
            """, (namespace, key, value, source, confidence, project_id,
                  session_id, expires_at, datetime.now().isoformat()))
    return True

def get_core_memory(namespace: Optional[str] = None) -> list[dict]:
    with DbConn() as conn:
        with conn.cursor() as cur:
            if namespace:
                cur.execute(
                    "SELECT namespace, key, value, confidence, source, expires_at "
                    "FROM corememory WHERE namespace = %s ORDER BY key",
                    (namespace,)
                )
            else:
                cur.execute(
                    "SELECT namespace, key, value, confidence, source, expires_at "
                    "FROM corememory ORDER BY namespace, key"
                )
            rows = cur.fetchall()
            return [
                {"namespace": r[0], "key": r[1], "value": r[2],
                 "confidence": r[3], "source": r[4], "expires_at": r[5]}
                for r in rows
            ]

def purge_expired_memory():
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corememory WHERE expires_at IS NOT NULL AND expires_at < %s",
                (datetime.now().isoformat(),)
            )
            count = cur.rowcount
            if count:
                logger.info(f"Purged {count} expired memory entries.")

# ── Sessions ──────────────────────────────────────────────────────────────────

def get_session_summary(session_id: str) -> str:
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT summary_text FROM sessionsummaries WHERE session_id=%s", (session_id,))
            row = cur.fetchone()
            return row[0] if row else ""

def save_session_summary(session_id: str, summary_text: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sessionsummaries (session_id, summary_text, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (session_id) DO UPDATE SET 
                    summary_text = EXCLUDED.summary_text,
                    updated_at = CURRENT_TIMESTAMP
            """, (session_id, summary_text))

# ── Call logging ──────────────────────────────────────────────────────────────

def log_call(provider: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO call_log (date, provider, timestamp) VALUES (%s, %s, %s)",
                (datetime.now().strftime("%Y-%m-%d"), provider, datetime.now().isoformat()),
            )

def daily_call_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM call_log WHERE date=%s", (today,))
            row = cur.fetchone()
            return row[0] if row else 0

def is_over_daily_limit() -> bool:
    return daily_call_count() >= DAILY_CALL_LIMIT

# ── Conversation storage ──────────────────────────────────────────────────────

def save_message(session_id: str, role: str, content: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (session_id, role, content, timestamp) VALUES (%s, %s, %s, %s)",
                (session_id, role, content, datetime.now().isoformat()),
            )

def load_history(session_id: str, limit: int):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content FROM conversations WHERE session_id=%s ORDER BY id DESC LIMIT %s",
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [{"role": r, "content": c} for r, c in reversed(rows)]

def list_sessions(limit: int = 15):
    with DbConn() as conn:
        with conn.cursor() as cur:
            # PostgreSQL requires an aggregate or GROUP BY for complex DISTINCT ORDER BY
            cur.execute("""
                SELECT session_id FROM (
                    SELECT session_id, max(id) as max_id 
                    FROM conversations 
                    GROUP BY session_id
                ) AS sub
                ORDER BY max_id DESC 
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [r[0] for r in rows]

def delete_session(session_id: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE session_id=%s", (session_id,))
            cur.execute("DELETE FROM session_names WHERE session_id=%s", (session_id,))

# ── Session naming ────────────────────────────────────────────────────────────

def save_session_name(session_id: str, name: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO session_names (session_id, name, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET 
                    name = EXCLUDED.name, 
                    updated_at = EXCLUDED.updated_at
            """, (session_id, name, datetime.now().isoformat()))

def get_session_name(session_id: str) -> str | None:
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM session_names WHERE session_id=%s", (session_id,))
            row = cur.fetchone()
            return row[0] if row else None

def get_all_session_names(session_ids: list[str]) -> dict[str, str]:
    if not session_ids:
        return {}
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, name FROM session_names WHERE session_id = ANY(%s)",
                (session_ids,)
            )
            rows = cur.fetchall()
            return {r[0]: r[1] for r in rows}

# ── Search / scrape cache ─────────────────────────────────────────────────────

def get_cached_search(query: str, ttl_hours: int = 1) -> str | None:
    key = query.strip().lower()
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT result, cached_at FROM search_cache WHERE query_key=%s", (key,))
            row = cur.fetchone()
            if not row:
                return None
            result, cached_at_str = row
            try:
                cached_at = datetime.fromisoformat(cached_at_str)
                if datetime.now() - cached_at > timedelta(hours=ttl_hours):
                    return None
                return result
            except:
                return None

def save_cached_search(query: str, result: str):
    key = query.strip().lower()
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_cache (query_key, result, cached_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (query_key) DO UPDATE SET
                    result = EXCLUDED.result,
                    cached_at = EXCLUDED.cached_at
            """, (key, result, datetime.now().isoformat()))

def purge_expired_cache(ttl_hours: int = 24):
    with DbConn() as conn:
        with conn.cursor() as cur:
            # Postgres interval math
            cur.execute(
                "DELETE FROM search_cache WHERE CAST(cached_at AS TIMESTAMP) < NOW() - INTERVAL '1 hour' * %s",
                (ttl_hours,)
            )

# ── HITL: Task Management ──────────────────────────────────────────────────────

def save_active_task(session_id: str, task_type: str, task_input: dict):
    import json
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO active_tasks (session_id, task_type, task_input, task_status, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (session_id) DO UPDATE SET
                    task_type = EXCLUDED.task_type,
                    task_input = EXCLUDED.task_input,
                    task_status = EXCLUDED.task_status,
                    updated_at = CURRENT_TIMESTAMP
            """, (session_id, task_type, json.dumps(task_input), "pending"))

def get_active_task(session_id: str) -> Optional[dict]:
    import json
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_type, task_input, task_status FROM active_tasks WHERE session_id=%s", (session_id,))
            row = cur.fetchone()
            if row:
                return {
                    "task_type":   row[0],
                    "task_input":  json.loads(row[1]),
                    "task_status": row[2],
                }
            return None

def clear_active_task(session_id: str):
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM active_tasks WHERE session_id=%s", (session_id,))
