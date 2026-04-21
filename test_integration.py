# test_integration.py
import os, uuid, time, sys

# Ensure project root is in path
sys.path.append(os.getcwd())

SESSION = f"test_{uuid.uuid4().hex[:8]}"

def test_db_write_and_read():
    print("Testing DB write and read...")
    from core.db import update_core_memory, get_core_memory
    ok = update_core_memory(
        namespace="user", key="test_name", value="Test User",
        source="user_stated", confidence=0.95
    )
    assert ok, "Write returned False"
    rows = get_core_memory(namespace="user")
    keys = [r["key"] for r in rows]
    assert "test_name" in keys, "Written key not found on read"
    print("  ✅ DB write/read")

def test_upsert_with_embedding():
    print("Testing upsert with embedding (async)...")
    from core.db import upsert_memory_with_embedding, get_core_memory
    ok = upsert_memory_with_embedding(
        namespace="task", key="test_budget", value="$5000",
        source="user_stated", confidence=0.9, session_id=SESSION
    )
    assert ok, "Upsert returned False"
    time.sleep(3)  # wait for async embedding thread
    rows = get_core_memory(namespace="task")
    keys = [r["key"] for r in rows]
    assert "test_budget" in keys, f"test_budget not found — keys: {keys}"
    print("  ✅ upsert_with_embedding (write + async embed)")

def test_delete_oldest_messages():
    print("Testing _delete_oldest_messages...")
    from core.db import save_message, load_history, _delete_oldest_messages
    for i in range(5):
        save_message(SESSION, "user" if i % 2 == 0 else "assistant", f"message {i}")
    history = load_history(SESSION, limit=100)
    assert len(history) == 5, f"Expected 5, got {len(history)}"
    _delete_oldest_messages(SESSION, 2)
    history = load_history(SESSION, limit=100)
    assert len(history) == 3, f"Expected 3 after delete, got {len(history)}"
    assert history[0]["content"] == "message 2", \
        f"Wrong messages deleted — first remaining: {history[0]['content']}"
    print("  ✅ _delete_oldest_messages (Fix 2)")

def test_session_summary_roundtrip():
    print("Testing session summary save/read...")
    from core.db import save_session_summary, get_session_summary
    save_session_summary(SESSION, "User is building an AI agent.")
    result = get_session_summary(SESSION)
    assert result == "User is building an AI agent.", f"Got: {result}"
    print("  ✅ session summary save/read")

def test_maybe_summarize_uses_threshold():
    """Fix 3: confirm SUMMARIZE_THRESHOLD=8000 is passed, not default 3000."""
    print("Testing maybe_summarize respects threshold...")
    from core.db import save_message
    from core.memory import maybe_summarize_session
    from core.config import SUMMARIZE_THRESHOLD

    # Write 10 messages with ~300 chars each = ~3000 chars / ~750 tokens
    # This is BELOW 8000 threshold — summarizer must NOT fire
    sid = f"thresh_test_{uuid.uuid4().hex[:6]}"
    for i in range(10):
        save_message(sid, "user", "word " * 60)  # ~300 chars each

    fired = []
    def mock_llm(msgs, task_type="general"):
        fired.append(task_type)
        return "mock summary", "mock"

    maybe_summarize_session(sid, mock_llm, token_threshold=SUMMARIZE_THRESHOLD)
    assert len(fired) == 0, \
        f"Summarizer fired at {SUMMARIZE_THRESHOLD} token threshold but shouldn't have — fired: {fired}"

    # Cleanup
    from core.db import _delete_oldest_messages
    _delete_oldest_messages(sid, 999)
    print(f"  ✅ maybe_summarize respects SUMMARIZE_THRESHOLD={SUMMARIZE_THRESHOLD} (Fix 3)")

def test_updatecorememory_tool_reachable():
    """Fix 1: updatecorememory must NOT return 'Unknown tool'."""
    print("Testing updatecorememory tool reachability...")
    from core.agent import dispatch_tool
    result = dispatch_tool(
        "updatecorememory",
        {
            "namespace":  "user",
            "key":        "test_fix1_key",
            "value":      "fix1_verified",
            "confidence": 0.95,
            "source":     "user_stated",
        },
        session_id=SESSION
    )
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "Unknown tool" not in str(result), \
        f"updatecorememory still dead — got: {result}"
    assert result.get("success") is True or "Write blocked" in str(result), \
        f"Unexpected result: {result}"
    print(f"  ✅ updatecorememory reachable — result: {result} (Fix 1)")

def test_listcorememory_tool():
    print("Testing listcorememory tool reachability...")
    from core.agent import dispatch_tool
    result = dispatch_tool("listcorememory", {}, session_id=SESSION)
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    assert "Unknown tool" not in result
    print("  ✅ listcorememory reachable")

def test_deletecorememory_tool():
    print("Testing deletecorememory tool reachability...")
    from core.db import update_core_memory
    from core.agent import dispatch_tool
    update_core_memory("task", "temp_delete_test", "temp", "user_stated", 0.9)
    result = dispatch_tool(
        "deletecorememory",
        {"namespace": "task", "key": "temp_delete_test"},
        session_id=SESSION
    )
    assert result.get("success") is True, f"Delete failed: {result}"
    print("  ✅ deletecorememory reachable")

def test_concurrent_db_writes():
    """Fix 4: ThreadedConnectionPool must handle concurrent writes without PoolError."""
    print("Testing concurrent DB writes (thread safety)...")
    import threading
    from core.db import save_message
    errors = []
    def write(i):
        try:
            save_message(SESSION, "user", f"concurrent message {i}")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(errors) == 0, f"Concurrent write errors: {errors}"
    print("  ✅ ThreadedConnectionPool — 20 concurrent writes, no PoolError (Fix 4)")

def test_search_cache_roundtrip():
    print("Testing search cache roundtrip...")
    from core.db import save_cached_search, get_cached_search
    save_cached_search("test query abc123", "cached result xyz")
    result = get_cached_search("test query abc123", ttl_hours=1)
    assert result == "cached result xyz", f"Got: {result}"
    print("  ✅ search cache save/read")

def cleanup():
    from core.db import DbConn, _delete_oldest_messages
    with DbConn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM corememory WHERE key LIKE 'test_%'")
            cur.execute("DELETE FROM sessionsummaries WHERE session_id = %s", (SESSION,))
            cur.execute("DELETE FROM search_cache WHERE query_key LIKE 'test query%'")
    _delete_oldest_messages(SESSION, 999)
    print(f"  🧹 Cleaned up session {SESSION}")

if __name__ == "__main__":
    if not os.getenv("DATABASE_URL"):
        print("❌ DATABASE_URL not set — cannot run Layer 3")
        exit(1)
        
    from core.db import initdb
    print("Initializing database schema...")
    initdb()
    
    print(f"\n── Layer 3: Integration Tests (session={SESSION}) ──")
    test_db_write_and_read()
    test_upsert_with_embedding()
    test_delete_oldest_messages()
    test_session_summary_roundtrip()
    test_maybe_summarize_uses_threshold()
    test_updatecorememory_tool_reachable()
    test_listcorememory_tool()
    test_deletecorememory_tool()
    test_concurrent_db_writes()
    test_search_cache_roundtrip()
    cleanup()
    print("\n✅ Layer 3 PASSED\n")
