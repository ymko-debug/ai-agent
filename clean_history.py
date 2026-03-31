import sqlite3
import os

db_path = r'c:\Users\rusla\OneDrive\Documents\Agent\assistant_memory.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    # Remove any recent assistant turns that involve the refusal text
    count = conn.execute("DELETE FROM conversations WHERE role = 'assistant' AND content LIKE '%cannot directly interact%'").rowcount
    conn.commit()
    conn.close()
    print(f"Removed {count} refusal turns from chat history.")
else:
    print("DB not found.")
