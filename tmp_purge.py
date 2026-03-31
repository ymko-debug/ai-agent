import sqlite3
conn = sqlite3.connect('assistant_memory.db')
cur = conn.cursor()
try:
    cur.execute("DELETE FROM corememory WHERE namespace = 'agent'")
    cur.execute("DELETE FROM corememory WHERE namespace = 'task' AND (value LIKE '%blocked%' OR value LIKE '%failed%' OR value LIKE '%timeout%' OR value LIKE '%cannot%')")
    conn.commit()
    print('Cleaned:', conn.total_changes, 'rows')
except Exception as e:
    print('Error:', e)
finally:
    conn.close()
