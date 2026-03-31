
import json
import logging
from typing import List, Tuple
import pandas as pd
from core.llm import route_llm
from leadgen.models import Lead, EmailOutreach

# Hook into the same audit log
logger = logging.getLogger("leadgen_tools")


def extract_leads_from_text(raw_text: str, extra_instructions: str = "", max_retries: int = 2) -> Tuple[List[Lead], str]:
    logger.info(f"Extracting leads from text block of length {len(raw_text)}")
    system = (
        "You extract structured B2B leads from messy text. "
        "Only output valid JSON matching the given schema."
    )
    user = f"""Extract all business leads from the following text.
Return a JSON array of objects with keys: name, title, company, website, phone, email, linkedin, source_url.

Extra instructions: {extra_instructions}

Text:
{raw_text}
"""
    messages = [{"role": "user", "content": f"{system}\n\n{user}"}]

    last_error = ""
    for attempt in range(max_retries + 1):
        if last_error:
            messages.append({"role": "user", "content": f"Your previous output failed validation with this error:\n{last_error}\n\nPlease fix the JSON and try again. ONLY return valid JSON."})
            
        text, _provider = route_llm(messages, task_type="executor")
        
        # Strip markdown formatting if present
        clean_text = text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("\n", 1)[-1]
        if clean_text.endswith("```"):
            clean_text = clean_text.rsplit("\n", 1)[0]
        clean_text = clean_text.strip("`").strip()

        try:
            data = json.loads(clean_text)
            if isinstance(data, dict):
                data = [data]
        except Exception as e:
            logger.warning(f"JSON Decode Error on attempt {attempt + 1}. Retrying...")
            last_error = f"JSON Decode Error: {e}\nRaw Output: {text[:500]}"
            messages.append({"role": "assistant", "content": text})
            continue

        leads: List[Lead] = []
        validation_failed = False
        for item in data:
            try:
                leads.append(Lead(**item))
            except Exception as e:
                # Accumulate the first failing item's error for the next retry prompt
                logger.warning(f"Pydantic Validation Error on attempt {attempt + 1}: {e}. Retrying...")
                last_error = f"Pydantic Validation Error: {e} | Data string that failed: {item}"
                validation_failed = True
                break
                
        if validation_failed:
            messages.append({"role": "assistant", "content": text})
            continue
            
        logger.info(f"Successfully extracted {len(leads)} valid leads via {_provider}")
        return leads, _provider

    logger.error(f"Failed to extract leads after {max_retries + 1} attempts.")
    return [], "Tools"


def save_leads_to_spreadsheet(leads: List[Lead], path: str) -> None:
    if not leads:
        logger.warning(f"Received empty lead list, skipping save to {path}")
        return
    df = pd.DataFrame([l.model_dump() for l in leads])
    df.to_excel(path, index=False)
    logger.info(f"Saved {len(leads)} leads to excel spreadsheet at {path}")


def draft_outreach_for_lead(lead: Lead, offer_description: str) -> EmailOutreach:
    system = "You write short, friendly, personalized cold emails."
    user = f"""Write a concise cold email to this lead.
Lead:
Name: {lead.name}
Title: {lead.title}
Company: {lead.company}
Website: {lead.website}

Offer:
{offer_description}

Return JSON with keys: subject, body.
"""
    messages = [{"role": "user", "content": f"{system}\n\n{user}"}]
    text, _provider = route_llm(messages, task_type="executor")
    clean_text = text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.split("\n", 1)[-1]
    if clean_text.endswith("```"):
        clean_text = clean_text.rsplit("\n", 1)[0]
    clean_text = clean_text.strip("`").strip()

    try:
        data = json.loads(clean_text)
        return EmailOutreach(**data)
    except Exception as e:
        raise ValueError(f"LLM did not return valid outreach JSON: {e}\nRaw: {text[:500]}")
