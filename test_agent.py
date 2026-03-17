import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.agent import process_user_message

def test_hello():
    # Test with a simple greeting
    response = process_user_message(
        prompt="hello",
        session_id="test_session_001",
        use_search=False,
        provider_override=None    )
    print("Agent response:", response)

if __name__ == "__main__":
    test_hello()
