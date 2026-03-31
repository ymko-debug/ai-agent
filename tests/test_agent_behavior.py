import os
import sys
import pytest

# Add current directory to path so we can import core modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.agent import process_user_message

# Configuration for dry-run testing
TEST_SESSION_ID = "test-harness-session"

@pytest.mark.parametrize("prompt,expected_intent,disallowed_intent", [
    (
        "Register me at saatchiart.com using email ruslanazazymko@gmail.com",
        ["added skill", "run_skill", "ask for password", "create_skill"], # OHT Pattern
        ["searched", "web_search", "navigate"] # Forbidden "Reactive" patterns
    ),
    (
        "Find plumbers in Tacoma",
        ["web_search"], # Research task
        ["create_skill"] # Overkill for simple search
    ),
    (
        "What is 2+2?",
        [], # Direct knowledge
        ["web_search", "browse"]
    )
])
def test_intent_routing(prompt, expected_intent, disallowed_intent):
    """
    Checks if the orchestrator routes the request to the correct high-level tool.
    In the "Skill-First" architecture, registration MUST trigger a browser action
    followed by a skill-building intent, or a direct skill run.
    """
    # Use the process_user_message response to see what 'search_label' or 'provider' it returned
    # This assumes we have the 'simple' fixes applied first.
    result = process_user_message(prompt, session_id=TEST_SESSION_ID)
    
    label = result.get("search_label", "").lower()
    answer = result.get("answer", "").lower()
    
    # Check if any expected intents are present in the trace
    found_expected = any(exp in label or exp in answer for exp in expected_intent) if expected_intent else (not label)
    
    # Check if any disallowed intents "leaked" through
    found_disallowed = any(dis in label or dis in answer for dis in disallowed_intent)
    
    assert found_expected, f"Failed case: '{prompt}'. Expected one of {expected_intent}, but got label='{label}'"
    assert not found_disallowed, f"Failed case: '{prompt}'. Disallowed intent '{disallowed_intent}' was triggered."

if __name__ == "__main__":
    # If run directly, perform a quick manual check
    prompts = [
        "Register me at saatchiart.com email ruslanazazymko@gmail.com",
        "Find plumbers in Tacoma",
        "What is 2+2?"
    ]
    for p in prompts:
        res = process_user_message(p, TEST_SESSION_ID)
        print(f"\nPrompt: {p}")
        print(f"Search Label: {res.get('search_label')}")
        print(f"Provider: {res.get('provider')}")
