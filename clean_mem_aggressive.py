import sqlite3
import os

db_path = r'c:\Users\rusla\OneDrive\Documents\Agent\assistant_memory.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    # Wipe out ALL limitation-based 'agent inferred' facts
    count = conn.execute("DELETE FROM corememory WHERE key LIKE '%limitation%' OR value LIKE '%cannot%'").rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {count} negative memory facts.")
else:
    print("DB not found.")
