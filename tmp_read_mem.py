import sqlite3
import os

db_path = r'c:\Users\rusla\OneDrive\Documents\Agent\assistant_memory.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT namespace, key, value FROM corememory").fetchall()
    for row in rows:
        print(f"[{row['namespace']}] {row['key']}: {row['value']}")
    conn.close()
else:
    print("DB not found.")
