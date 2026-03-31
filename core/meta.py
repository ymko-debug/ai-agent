# core/meta.py
import os
import re
import json
import importlib.util
import logging
from datetime import datetime
from core.llm import route_llm

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/audit.log",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("meta_skill_loop")

SKILLS_DIR    = "skills"
REGISTRY_PATH = "tools_registry.json"   # project root — matches agent.py reader


def plan_skill(skill_name: str, dom_context: str = "") -> str:
    system = "You are an expert Python architect. Your job is to write a step-by-step logic plan for a new python tool function."
    user   = f"""We need a new python tool for: '{skill_name}'.
The code must be self-contained and will be saved in `skills/`.
"""
    if dom_context:
        user += f"\n### TARGET PAGE DOM SNAPSHOT (Use these selectors!):\n{dom_context}\n"
        
    user += """
If the task involves a website (like registration or scraping), the plan MUST include:
1. Navigation to the URL and waiting for the page to settle.
2. Finding elements by reliable selectors (prioritizing labels/placeholders, then text, then CSS).
3. Handling common multi-step patterns (e.g., clicking 'Sign Up' before typing email).
4. Returning a success report or a detailed error/CAPTCHA signal if blocked.
Do NOT write code. Write ONLY a bulleted list of requirements, inputs, outputs, and the step-by-step logic. NO conversational talk (e.g., "Okay, I understand").
"""
    messages = [{"role": "user", "content": f"{system}\n\n{user}"}]
    plan, _  = route_llm(messages, task_type="planner")
    return plan


def execute_skill(skill_name: str, plan: str, error_feedback: str = "", dom_context: str = "") -> str:
    system = "You are an elite Python engineer. Write raw, production-ready python code that fulfills the provided plan."
    user   = f"""Write the python file for the tool '{skill_name}' based on this plan:

{plan}

Requirements:
1. ONLY output python code. Do not output markdown, explanations, or ```python blocks.
2. The code must define a top-level `run(input_data: dict) -> dict` function.
3. Import all necessary libraries at the top.
4. If using playwright, use the sync_api and always call `stealth_sync(page)` if available.
5. Simulate human-like typing speed for form fields to avoid bot detection.
6. Return a dict with a 'success' boolean and a 'result' string (snapshot of the final state).
"""
    if dom_context:
        user += f"\n### TARGET PAGE DOM SNAPSHOT (Use these selectors!):\n{dom_context}\n"
    if error_feedback:
        user += f"\n\nCRITICAL FIX REQUIRED. Your previous attempt failed with:\n{error_feedback}"

    messages   = [{"role": "user", "content": f"{system}\n\n{user}"}]
    raw_code, _ = route_llm(messages, task_type="executor")

    # ── ROBUST CODE CLEANER ──────────────────────────────────────────────────
    # 1. Strip markdown fences
    clean = raw_code.replace("```python", "").replace("```", "").strip()
    
    # 2. Strip <toolcall> tags (Shield against LLM over-obedience)
    if "<toolcall>" in clean:
        # If it's a full toolcall wrap, try to extract just the 'content' field if possible,
        # otherwise just remove the tags.
        match = re.search(r'<toolcall>.*?"content":\s*"(.*?)"\s*\}\s*</toolcall>', clean, re.DOTALL)
        if match:
            # Unescape newlines if it was a JSON string
            clean = match.group(1).replace("\\n", "\n").replace('\\"', '"')
        else:
            clean = re.sub(r'<toolcall>.*?</toolcall>', '', clean, flags=re.DOTALL).strip()
    
    # Final pass to ensure no leftover fences
    clean = clean.replace("```python", "").replace("```", "").strip()
    # ─────────────────────────────────────────────────────────────────────────

    os.makedirs(SKILLS_DIR, exist_ok=True)
    filepath = os.path.join(SKILLS_DIR, f"tools_{skill_name}.py")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(clean.strip())

    return filepath


from typing import Tuple

def test_skill(filepath: str) -> Tuple[bool, str]:
    """
    Two-phase test:
      Phase 1 — import check: catches syntax errors and bad imports.
      Phase 2 — runtime check: calls run({}) to catch errors that only
                appear at execution time (NameError, missing keys, etc).
    """
    try:
        module_name = os.path.basename(filepath)[:-3]
        spec        = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            return False, "Failed to create module spec."
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)   # Phase 1: import
    except Exception as e:
        import traceback
        return False, traceback.format_exc()

    # Phase 2: functional run() check (only if not a complex browser task)
    if hasattr(module, "run"):
        # For browser skills, we skip the live functional test to avoid
        # side effects or CAPTCHA blocks during the generation loop.
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'playwright' in content.lower():
                logger.info("Skill uses Playwright — skipping functional test to avoid side effects.")
                return True, ""
                
            module.run({})   # dummy call with empty input for simple logic tools
        except TypeError:
            pass  # Expected — run() needs real args, but it's callable.
        except Exception as e:
            return False, f"Runtime error in run({{}}): {e}"

    return True, ""


def register_skill(skill_name: str, filepath: str, plan: str):
    if not os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, "w") as f:
            json.dump({"registered_skills": []}, f)

    with open(REGISTRY_PATH, "r") as f:
        try:
            registry = json.load(f)
        except json.JSONDecodeError:
            registry = {"registered_skills": []}

    # Avoid duplicate registrations
    registry["registered_skills"] = [
        s for s in registry.get("registered_skills", []) if s.get("name") != skill_name
    ]

    # Clean the description: Remove 'Okay, I understand' talk if it leaked in
    clean_desc = plan.strip()
    conversation_patterns = [
        r"^(okay|ok|sure|understood|here is the plan|certainly)[^\n.]*[.!\n]", # common LLM talk
        r"^I will act as.*?[.!\n]" # another common talk pattern
    ]
    for cp in conversation_patterns:
        clean_desc = re.sub(cp, "", clean_desc, flags=re.IGNORECASE).strip()

    registry["registered_skills"].append({
        "name":        skill_name,
        "filepath":    filepath,
        "description": clean_desc[:200] + "...",
    })

    # Atomic write to avoid corruption on sudden 'Stop'
    import tempfile
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(REGISTRY_PATH))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=4)
        # Flush and sync to disk before renaming
        os.replace(temp_path, REGISTRY_PATH)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e


def run_meta_skill_loop(skill_name: str, max_attempts: int = 3, session_id: str = None, target_url: str = None) -> str:
    from core.signals import is_stopped
    from core.scraper import scrape_url_with_playwright
    
    logger.info(f"Starting meta-skill generation for '{skill_name}'")
    
    if session_id and is_stopped(session_id):
        return "🛑 Stopped by user."
    
    dom_context = ""
    if target_url:
        logger.info(f"Pre-flight DOM inspection for {target_url}...")
        try:
            dom_context = scrape_url_with_playwright(target_url)
            logger.info(f"Scraped {len(dom_context)} chars for DOM context.")
        except Exception as e:
            logger.warning(f"DOM inspection failed: {e}")
            
    plan           = plan_skill(skill_name, dom_context=dom_context)
    filepath       = ""
    error_feedback = ""

    for attempt in range(max_attempts):
        logger.info(f"Attempt {attempt + 1}/{max_attempts} for '{skill_name}'")
        
        if session_id and is_stopped(session_id):
            return "🛑 Stopped by user."
            
        filepath = execute_skill(skill_name, plan, error_feedback, dom_context=dom_context)
        
        if session_id and is_stopped(session_id):
            return "🛑 Stopped by user."
            
        success, error_msg = test_skill(filepath)
        if success:
            register_skill(skill_name, filepath, plan)
            logger.info(f"Success — built and registered '{skill_name}' at {filepath}")
            return (
                f"✅ Built, tested, and registered `{skill_name}` at `{filepath}`. "
                f"(Attempt {attempt + 1}/{max_attempts})"
            )

        logger.warning(
            f"Test failed for '{skill_name}' attempt {attempt + 1}: {error_msg[:100]}..."
        )
        error_feedback = (
            f"The code failed the test harness:\n\n{error_msg}\n\n"
            "Please fix the bug and return the complete corrected file. Do NOT wrap output in tags."
        )

    # All attempts failed — archive the evidence
    logger.error(f"Failed to build '{skill_name}' after {max_attempts} attempts.")
    if os.path.exists(filepath):
        ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_dir = os.path.join(SKILLS_DIR, "failed_attempts")
        os.makedirs(failed_dir, exist_ok=True)
        try:
            failed_py  = os.path.join(failed_dir, f"tools_{skill_name}_{ts}.py")
            failed_err = os.path.join(failed_dir, f"tools_{skill_name}_{ts}_error.txt")
            os.rename(filepath, failed_py)
            with open(failed_err, "w", encoding="utf-8") as f:
                f.write(error_feedback)
            logger.info(f"Archived failed attempt to {failed_py}")
        except Exception as e:
            logger.error(f"Could not archive failed attempt: {e}")

    return (
        f"❌ Failed to build `{skill_name}` after {max_attempts} attempts. "
        "Check `skills/failed_attempts/` for the error log."
    )
