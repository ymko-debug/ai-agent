import logging
import json
import sqlite3
import os
from core.db import init_db, save_active_task, get_active_task, clear_active_task
from core.agent import process_user_message

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)

def test_hitl_resumption():
    session_id = "test_session_hitl"
    
    # 1. Init DB and clear any old state
    init_db()
    clear_active_task(session_id)
    
    # 2. Save a dummy task
    task_type = "register_on_website"
    task_input = {"url": "https://example.com/register", "email": "test@example.com"}
    save_active_task(session_id, task_type, task_input)
    
    print("--- Task saved to DB ---")
    active = get_active_task(session_id)
    print(f"Active task found: {active}")
    
    # 3. Simulate user saying 'yes' or 'continue'
    # We expect process_user_message to catch the active task and try to re-run it.
    # Note: This will attempt to call _execute_skill which might fail if skill isn't functional, 
    # but we want to see if the ROUTING logic hits the resumption path.
    print("\n--- Simulating 'continue' command ---")
    response = process_user_message("yes", session_id)
    
    print("\n--- Agent Response ---")
    print(json.dumps(response, indent=2))
    
    # 4. Check if task was cleared (if it 'succeeded' in dummy mode, or if we just want to verify logic)
    # Since _execute_skill will actually run the skill, we can see if it outputs "HITL Resume" provider.
    if response.get("provider") == "HITL Resume":
        print("\n✅ SUCCESS: Agent correctly identified and resumed the active task.")
    else:
        print("\n❌ FAILURE: Agent did not use the HITL Resume provider.")

if __name__ == "__main__":
    # Ensure we are in the right directory
    if os.path.exists("assistant_memory.db"):
        test_hitl_resumption()
    else:
        print("Database not found. Please run from the project root.")
