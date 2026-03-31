import sqlite3
import sys
from pathlib import Path

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
rows = conn.execute("SELECT key, value, updated_at FROM core_memory ORDER BY updated_at").fetchall()
conn.close()

with open("scripts/memory_audit_post_wipe.txt", "w", encoding="utf-8") as f:
    f.write(f"Total entries: {len(rows)}\n\n")
    for key, value, updated_at in rows:
        f.write(f"  [{updated_at[:10]}]  {key:40s} = {value}\n")
print(f"Audit saved to scripts/memory_audit_post_wipe.txt")
