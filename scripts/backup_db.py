"""
scripts/backup_db.py
Run before any schema migration or Phase 2 work.
Creates a timestamped copy of the SQLite DB in backups/.
"""
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH     = Path("assistant_memory.db")
BACKUP_DIR  = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = BACKUP_DIR / f"assistant_memory_{timestamp}.db"

# Verify DB is readable before copying
# Use the table names exactly as defined in core/db.py
conn = sqlite3.connect(DB_PATH)
counts = {
    "corememory":       conn.execute("SELECT COUNT(*) FROM corememory").fetchone()[0],
    "conversations":    conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
    "sessionsummaries": conn.execute("SELECT COUNT(*) FROM sessionsummaries").fetchone()[0],
    "call_log":         conn.execute("SELECT COUNT(*) FROM call_log").fetchone()[0],
    "search_cache":     conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0],
}
conn.close()

shutil.copy2(DB_PATH, backup_path)
print(f"✓ Backup created: {backup_path}")
print(f"  Row counts:")
for table, count in counts.items():
    print(f"    {table:20s}: {count:,}")
