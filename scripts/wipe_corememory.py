import sqlite3
import sys
from pathlib import Path

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
count = conn.execute("SELECT COUNT(*) FROM core_memory").fetchone()[0]
print(f"About to delete {count} rows from core_memory.")
confirm = "YES" # Automated 
if confirm.strip() == "YES":
    conn.execute("DELETE FROM core_memory")
    conn.commit()
    print(f"Wiped {count} entries. core_memory is now empty.")
else:
    print("Aborted.")

TEST_SESSION_IDS = [
    "integrationtestsession",
    "session20260312164743",  # early "prove yourself" test
    "session20260312193843",  # AnyDesk test
]
conn.executemany(
    "DELETE FROM session_summaries WHERE session_id = ?",
    [(sid,) for sid in TEST_SESSION_IDS]
)
conn.commit()
conn.close()
