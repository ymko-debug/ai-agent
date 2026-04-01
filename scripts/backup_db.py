"""
scripts/backup_db.py
Creates a backup count of PostgreSQL tables.
Note: Full backups should be managed via Supabase/PostgreSQL provider.
"""
import psycopg2
import sys
import os
from pathlib import Path
from datetime import datetime

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL is not set!")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

tables = [
    "corememory",
    "conversations",
    "sessionsummaries",
    "call_log",
    "search_cache",
    "active_tasks",
    "session_names"
]

counts = {}
for table in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    except Exception as e:
        counts[table] = f"Error: {e}"
        conn.rollback()

cur.close()
conn.close()

print(f"✓ PostgreSQL Database Status ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}):")
print(f"  Row counts:")
for table, count in counts.items():
    print(f"    {table:20s}: {count}")
