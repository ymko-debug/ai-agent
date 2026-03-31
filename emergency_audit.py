import sqlite3
import os

db = "assistant_memory.db"
if os.path.exists(db):
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    # Get last 2 messages (user command + agent response)
    cur.execute("SELECT role, content FROM conversations ORDER BY id DESC LIMIT 2")
    rows = cur.fetchall()
    for r in reversed(rows):
        print(f"--- {r[0].upper()} ---")
        print(r[1])
    conn.close()
else:
    print(f"{db} not found.")
