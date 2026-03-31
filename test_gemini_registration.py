import asyncio
import os
import logging
from skills.register_on_website import run

# Setup logging
logging.basicConfig(level=logging.INFO)

def test_gemini_registration_skill():
    print("--- Starting Gemini 2.5 Flash Verification test ---")
    
    # Test data - using a simple URL that doesn't require real registration for the check
    input_data = {
        "url": "https://www.wikipedia.org/",
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "password": "Password123!"
    }
    
    print(f"Target URL: {input_data['url']}")
    print("Executing skill...")
    
    # Run the skill synchronously (it uses asyncio.run internally)
    result = run(input_data)
    
    print("\n--- Skill Result ---")
    import json
    print(json.dumps(result, indent=2))
    
    if result.get("success") or "Step 1" in str(result.get("result", "")):
        print("\n✅ SUCCESS: Gemini 2.5 Flash initialized and navigated successfully.")
    elif "no attribute 'provider'" in str(result.get("error", "")):
        print("\n❌ FAILURE: Attribute error 'provider' still persists.")
    else:
        print(f"\n⚠️ UNCERTAIN: Skill returned result but status is unclear. Error: {result.get('error')}")

if __name__ == "__main__":
    test_gemini_registration_skill()
