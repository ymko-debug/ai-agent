"""
scripts/seed_q_bootstrap.py
Reads logs/llm_traces.jsonl and produces a seed file: data/q_bootstrap_seed.jsonl
Each line: {"session_id": ..., "task_type": ..., "outcome": ..., "key_facts": [...]}
This seed file is consumed by Phase 2A's outcome_tracker on first startup.
"""
import json
import re
from pathlib import Path
from datetime import datetime

TRACES_PATH = Path("logs/llm_traces.jsonl")    # Corrected path
OUTPUT_PATH = Path("data/q_bootstrap_seed.jsonl")
OUTPUT_PATH.parent.mkdir(exist_ok=True)

SUCCESS_SIGNALS  = ["here is", "found", "completed", "successfully", "resolved",
                    "booked", "here are", "done", "extracted", "saved"]
FAILURE_SIGNALS  = ["failed", "could not", "unable to", "blocked", "error",
                    "captcha", "access denied", "no results found"]
PARTIAL_SIGNALS  = ["partial", "some results", "limited", "however", "but"]

DELIVERY_SIGNALS = [
    r"^here (is|are)\b",           # "Here is the flight..."
    r"^\d+\.",                      # numbered list = structured result
    r"^-\s",                        # bullet list = structured result
    r"\bbelow\b.*:\s*\n",          # "results below:"
    r"\bextracted\b",
    r"\bsaved to\b",
    r"\bskill.*created\b",
]

TASK_TYPE_MAP = {
    ("flight", "travel", "book", "hotel"):          "travel",
    ("lead", "client", "sales", "contact"):          "sales",
    ("skill", "create", "python", "script"):         "skill_creation",
    ("memory", "namespace", "corememory", "phase"):  "system_dev",
    ("summarize", "summary", "report", "analyse"):   "analysis",
    ("research", "find info", "who is", "find "):   "research",
}

def detect_task(content: str) -> str:
    lower = content.lower()
    for keywords, task_type in TASK_TYPE_MAP.items():
        if any(kw in lower for kw in keywords):
            return task_type
    return "general"

def detect_outcome(final_answer: str) -> str:
    lower = final_answer.lower()
    f = sum(1 for s in FAILURE_SIGNALS if s in lower)
    s = sum(1 for s in SUCCESS_SIGNALS if s in lower)
    p = sum(1 for s in PARTIAL_SIGNALS if s in lower)

    # NEW: structured delivery = implicit success
    d = sum(1 for sig in DELIVERY_SIGNALS
            if re.search(sig, final_answer[:300], re.IGNORECASE | re.MULTILINE))

    if f > s + d:      return "FAILURE"
    if s > 0 or d >= 2: return "SUCCESS"   # delivery signals count as success
    if p > 0:          return "PARTIAL"
    return "PARTIAL"

seeds = []
if TRACES_PATH.exists():
    with TRACES_PATH.open() as f:
        for line in f:
            try:
                trace = json.loads(line)
            except json.JSONDecodeError:
                continue

            messages  = trace.get("messages", [])
            if not messages:
                continue

            user_msg  = next((m["content"] for m in messages if m["role"] == "user"), "")
            final_ans = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
            if not user_msg or not final_ans:
                continue

            seeds.append({
                "session_id":  trace.get("session_id", "unknown"),
                "task_type":   detect_task(user_msg),
                "outcome":     detect_outcome(final_ans),
                "user_prompt": user_msg[:200],
                "timestamp":   trace.get("timestamp", datetime.now().isoformat()),
            })
else:
    print(f"⚠  Trace file not found: {TRACES_PATH}")

with OUTPUT_PATH.open("w") as f:
    for s in seeds:
        f.write(json.dumps(s) + "\n")

# Summary
from collections import Counter
outcomes   = Counter(s["outcome"]   for s in seeds)
task_types = Counter(s["task_type"] for s in seeds)
print(f"✓ Seed file written: {OUTPUT_PATH}  ({len(seeds)} entries)")
print(f"  Outcomes:   {dict(outcomes)}")
print(f"  Task types: {dict(task_types)}")
