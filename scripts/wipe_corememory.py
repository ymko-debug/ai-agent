import psycopg2
import sys
import os
from pathlib import Path

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL is not set!")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Get count for corememory
cur.execute("SELECT COUNT(*) FROM corememory")
count = cur.fetchone()[0]
print(f"About to delete {count} rows from corememory.")

# Manual confirmation bypass for automation
confirm = "YES" 
if confirm.strip() == "YES":
    cur.execute("DELETE FROM corememory")
    conn.commit()
    print(f"Wiped {count} entries. corememory is now empty.")
else:
    print("Aborted.")

TEST_SESSION_IDS = [
    "integrationtestsession",
    "session20260312164743",
    "session20260312193843",
]
cur.execute(
    "DELETE FROM sessionsummaries WHERE session_id = ANY(%s)",
    (TEST_SESSION_IDS,)
)
conn.commit()
cur.close()
conn.close()
