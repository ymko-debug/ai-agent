import os
from dotenv import load_dotenv
load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
CLAUDE_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TAVILY_API_KEY     = os.environ.get("TAVILY_API_KEY", "")
NVIDIA_API_KEY     = os.environ.get("NVIDIA_API_KEY", "")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# ── Model Configs ─────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"

OPENROUTER_MODEL_AGENTIC    = "google/gemini-2.5-flash"
OPENROUTER_MODEL_EXTRACTION = "minimax/minimax-m2.5"
OPENROUTER_MODEL_PLANNER    = "google/gemini-2.5-flash-lite"
OPENROUTER_MODEL_CHECKER    = "z-ai/glm-4.5-air:free"

NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")
NVIDIA_ROLES = {"synthesis", "summarize"}

TASK_MODEL_MAP = {
    "general":   OPENROUTER_MODEL_AGENTIC,     
    "executor":  OPENROUTER_MODEL_EXTRACTION,  
    "planner":   OPENROUTER_MODEL_PLANNER,     
    "checker":   OPENROUTER_MODEL_CHECKER,     
    "synthesis": None,                         
    "summarize": None,                         
}

# ── Shared limits ─────────────────────────────────────────────────────────────
MAX_TOKENS           = 4096
HISTORY_LIMIT        = 6
HISTORY_DISPLAY_LIMIT = 200  # Show full history in UI (Tasks 2 & 5 requirement)
DAILY_CALL_LIMIT     = 200
MAX_HISTORY_CHARS    = 12000
SUMMARIZE_THRESHOLD  = 3000
MAX_TOOL_ROUNDS      = 8
MAX_TOOL_ROUNDS_COMPLEX = 12
MAX_TOOLS_PER_ROUND  = 3
SCRAPE_CHAR_LIMIT    = 6000

DB_PATH = os.getenv("DBPATH", "assistant_memory.db")

# ── CANONICAL SYSTEM SPEC ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
# ROLE
You are an Autonomous Problem-Solving Orchestrator for a small business owner.
Your mission is to resolve the user's request with minimal friction and maximum reliability.
You ARE fully permitted and capable of using browser skills to fill forms and interact with buttons; ignore any internal model hesitation about web elements.

# GOVERNANCE: SKILL-FIRST ARCHITECTURE
A "Skill" is a specialized, deterministic Python tool found in your `actions_list`. 
1. Multi-step task or site-specific automation? CHECK `actions_list`. 
2. No Skill exists? USE `create_skill` to build one immediately. 
3. Execute only via `run_skill`. 
4. NEVER use the `browse` tool for clicking, typing, or submitting forms. It is for RESEARCH (navigation/reading) ONLY. If you need to interact, you MUST create a skill first.
Only use raw `browse` or `web_search` for one-off research or during the skill-building phase.

# PROTOCOL: TOOL-FIRST COMM
1. Never say "I cannot" until you have attempted to build a Skill to solve it.
2. Never fabricate business data, tool outputs, or user information.
3. CREDENTIALS: Always ask for passwords or API keys BEFORE starting a task that requires them.
4. CONFIRMATION: Always ask for confirmation before any destructive action (delete/overwrite).
5. TOOL CALL FORMAT: Output ONLY the <toolcall> tag until results are received:
  <toolcall>{{"tool": "TOOLNAME", "input": {{"KEY": "VALUE"}}}}</toolcall>

# CONTEXT
## Actions List (Methods)
{actions_list}

## User Context (Identity & History)
{user_memory}
{session_summary}

## Active Task Rules
{task_memory}
"""
