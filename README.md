# Local AI Assistant

A general-purpose AI assistant that runs on your machine, powered by the Claude API,
with web search via Tavily and automatic fallback to OpenRouter.

---

## What you need before starting

- Python 3.10 or newer  (check: `python --version`)
- A Claude API key from https://console.anthropic.com
- Optionally: an OpenRouter key and/or a Tavily key

---

## Setup — do this once

### Step 1 — Download / clone the project
Put all four files in one folder:
- app.py
- requirements.txt
- .env.example
- .gitignore

### Step 2 — Create a virtual environment
Open a terminal in the project folder and run:

```
python -m venv venv
```

Activate it:
- Windows:      `venv\Scripts\activate`
- Mac / Linux:  `source venv/bin/activate`

You will see `(venv)` in your terminal prompt.

### Step 3 — Install dependencies
```
pip install -r requirements.txt
```

### Step 4 — Add your API keys
```
cp .env.example .env
```
Open `.env` in any text editor and paste in your keys.
Minimum required: `ANTHROPIC_API_KEY`

### Step 5 — Run the assistant
```
streamlit run app.py
```

Your browser will open automatically at http://localhost:8501

---

## Daily use

Each time you want to use the assistant:

1. Open a terminal in the project folder
2. Run: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Mac/Linux)
3. Run: `streamlit run app.py`
4. Your browser opens the assistant

To stop it: press Ctrl+C in the terminal.

---

## Changing the LLM model

Open `app.py` and find the CONFIG section near the top.

To use a different Claude model, change:
```
CLAUDE_MODEL = "claude-sonnet-4-6"
```

Available Claude models (check https://docs.anthropic.com for the latest list):
- claude-opus-4-6        (most capable, higher cost)
- claude-sonnet-4-6      (default — best balance)
- claude-haiku-4-5-20251001  (fastest, lowest cost)

To change the OpenRouter fallback model, change:
```
OPENROUTER_MODEL = "openai/gpt-4o-mini"
```
See https://openrouter.ai/models for all available options.

---

## Where your data is stored

All conversations are saved to `assistant_memory.db` in the project folder.
This is a SQLite database file. It stays on your machine — nothing is sent to any
external service except the messages you type (which go to Claude/OpenRouter/Tavily).

To back up your history: copy `assistant_memory.db` to a safe location.
To wipe all history: delete `assistant_memory.db` and restart the app.

---

## Troubleshooting

**"ModuleNotFoundError"**
Run `pip install -r requirements.txt` again inside the activated venv.

**"Authentication error" for Claude**
Your ANTHROPIC_API_KEY in `.env` is wrong or missing. Double-check at https://console.anthropic.com

**Web search not working**
Check that TAVILY_API_KEY is in your `.env` file. The toggle in the sidebar must also be ON.

**Both providers failing**
Check your internet connection. If Claude is down, check https://status.anthropic.com

---

## Updating

To update all packages:
```
pip install -r requirements.txt --upgrade
```

To update only the Anthropic SDK:
```
pip install anthropic --upgrade
```
