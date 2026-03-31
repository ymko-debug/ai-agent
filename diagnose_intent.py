from core.search import detect_browser_intent
import re

prompt1 = "register me here https://www.saatchiart.com/whysell using email ruslanazazymko@gmail.com"
intent = detect_browser_intent(prompt1)
print(f"Prompt: {prompt1}")
print(f"Intent Action: {intent.get('action')}")
print(f"Intent URL: {intent.get('url')}")

# Double check the regex in search.py
text = prompt1.strip().lower()
form_signals = [r'\bregister\b', r'\bsign\s*up\b', r'\bcreate\s+account\b', r'\bjoin\b', r'\blogin\b', r'\bsign\s*in\b']
matched = any(re.search(s, text) for s in form_signals)
print(f"Regex Match (Direct): {matched}")
