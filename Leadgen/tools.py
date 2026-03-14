
import json
from typing import List
import pandas as pd
from core.llm import get_llm_response
from leadgen.models import Lead, EmailOutreach


def extract_leads_from_text(raw_text: str, extra_instructions: str = "") -> List[Lead]:
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
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, _provider = get_llm_response(messages)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        raise ValueError(f"LLM did not return valid JSON: {e}
Raw: {text[:500]}")

    leads: List[Lead] = []
    for item in data:
        try:
            leads.append(Lead(**item))
        except Exception:
            continue
    return leads


def save_leads_to_spreadsheet(leads: List[Lead], path: str) -> None:
    if not leads:
        return
    df = pd.DataFrame([l.model_dump() for l in leads])
    df.to_excel(path, index=False)


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
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, _provider = get_llm_response(messages)
    try:
        data = json.loads(text)
        return EmailOutreach(**data)
    except Exception as e:
        raise ValueError(f"LLM did not return valid outreach JSON: {e}
Raw: {text[:500]}")
