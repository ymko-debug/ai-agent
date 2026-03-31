import sqlite3
import os

db_path = r'c:\Users\rusla\OneDrive\Documents\Agent\assistant_memory.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    count = conn.execute("DELETE FROM corememory WHERE key = 'web_form_registration_limitation'").rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {count} memory facts.")
else:
    print("DB not found.")
