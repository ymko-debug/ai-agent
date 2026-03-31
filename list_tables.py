import sqlite3
db_path = r'c:\Users\rusla\OneDrive\Documents\Agent\assistant_memory.db'
conn = sqlite3.connect(db_path)
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [r[0] for r in rows])
conn.close()
