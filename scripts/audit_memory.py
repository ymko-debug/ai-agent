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
cur.execute("SELECT key, value, updated_at FROM corememory ORDER BY updated_at")
rows = cur.fetchall()
cur.close()
conn.close()

with open("scripts/memory_audit_post_wipe.txt", "w", encoding="utf-8") as f:
    f.write(f"Total entries: {len(rows)}\n\n")
    for key, value, updated_at in rows:
        f.write(f"  [{updated_at[:10]}]  {key:40s} = {value}\n")
print(f"Audit saved to scripts/memory_audit_post_wipe.txt")
