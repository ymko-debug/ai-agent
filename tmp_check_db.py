import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def check_schema():
    if not DATABASE_URL:
        print("DATABASE_URL not found in environment!")
        return
        
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    table_names = ['search_cache', 'corememory', 'conversations']
    for table in table_names:
        cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='{table}'")
        cols = cur.fetchall()
        print(f"{table} columns:", cols)
    
    conn.close()

if __name__ == "__main__":
    check_schema()
