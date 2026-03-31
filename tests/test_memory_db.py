import pytest
import sqlite3
import os
import tempfile
from datetime import datetime, timedelta

# Use a temp DB for tests — never touch the real one
os.environ["DBPATH"] = tempfile.mktemp(suffix=".db")
from core.db import init_db, update_core_memory, get_core_memory, purge_expired_memory

@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    yield
    conn = sqlite3.connect(os.environ["DBPATH"])
    conn.execute("DELETE FROM corememory")
    conn.commit()
    conn.close()


class TestWriteGate:
    def test_user_high_confidence_stated_writes(self):
        result = update_core_memory(
            "user", "name", "Test Owner",
            source="user_stated", confidence=0.95
        )
        assert result is True
        rows = get_core_memory("user")
        assert any(r["key"] == "name" for r in rows)

    def test_user_agent_inferred_blocked(self):
        result = update_core_memory(
            "user", "name", "Guessed Name",
            source="agent_inferred", confidence=0.95
        )
        assert result is False
        assert get_core_memory("user") == []

    def test_user_low_confidence_blocked(self):
        result = update_core_memory(
            "user", "name", "Low Conf Name",
            source="user_stated", confidence=0.4
        )
        assert result is False

    def test_research_namespace_always_writes(self):
        result = update_core_memory(
            "research", "company", "Acme Corp",
            source="agent_inferred", confidence=0.3
        )
        assert result is True   # research has no confidence gate

    def test_task_namespace_gets_expiry(self):
        update_core_memory("task", "flight_date", "July 18", source="user_stated", confidence=0.9)
        rows = get_core_memory("task")
        assert rows[0]["expires_at"] is not None
        expires = datetime.fromisoformat(rows[0]["expires_at"])
        assert expires > datetime.now() + timedelta(days=6)   # ~7 days forward


class TestConflictResolution:
    def test_higher_confidence_overwrites_lower(self):
        update_core_memory("user", "city", "Seattle", source="user_stated", confidence=0.7)
        update_core_memory("user", "city", "Tacoma",  source="user_stated", confidence=0.9)
        rows = get_core_memory("user")
        assert rows[0]["value"] == "Tacoma"

    def test_lower_confidence_does_not_overwrite_higher(self):
        update_core_memory("user", "city", "Seattle", source="user_stated", confidence=0.9)
        update_core_memory("user", "city", "Tacoma",  source="user_stated", confidence=0.7)
        rows = get_core_memory("user")
        assert rows[0]["value"] == "Seattle"   # original preserved


class TestNamespaceIsolation:
    def test_same_key_different_namespace_coexists(self):
        update_core_memory("user",     "name", "Owner Name",  source="user_stated",  confidence=0.9)
        update_core_memory("research", "name", "Skyler Peake", source="web_scraped", confidence=0.8)
        user_rows     = get_core_memory("user")
        research_rows = get_core_memory("research")
        assert len(user_rows) == 1
        assert len(research_rows) == 1
        assert user_rows[0]["value"] != research_rows[0]["value"]


class TestTTLPurge:
    def test_expired_entries_purged(self):
        conn = sqlite3.connect(os.environ["DBPATH"])
        conn.execute("""
            INSERT INTO corememory (namespace, key, value, source, confidence, updated_at, expires_at)
            VALUES ('task', 'old_task', 'stale', 'user_stated', 0.9, datetime('now'), datetime('now', '-1 day'))
        """)
        conn.commit()
        conn.close()
        purge_expired_memory()
        assert get_core_memory("task") == []

    def test_non_expired_entries_survive_purge(self):
        update_core_memory("user", "name", "Owner", source="user_stated", confidence=0.9)
        purge_expired_memory()
        assert len(get_core_memory("user")) == 1
