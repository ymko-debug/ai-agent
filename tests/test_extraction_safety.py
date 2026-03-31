import pytest
import json
from unittest.mock import patch, MagicMock
from core.memory import extract_core_facts, EXTRACTION_PROMPT


# Two real-world adversarial inputs that caused the original contamination
RESEARCH_QUERY = (
    "find everything about Skyler Peake of Peake Management LLC in Tacoma",
    "Skyler Peake is the owner of Peake Management LLC, registered in Tacoma WA."
)

IDENTITY_STATEMENT = (
    "my name is [owner] and I run this agent",
    "Got it! I'll remember your name and that you run this agent."
)


def get_namespaces_from_extraction(prompt, answer, mock_response):
    """Helper: run extraction with a mocked LLM and collect namespace outputs."""
    mock_llm = MagicMock(return_value=(mock_response, "mock_provider"))
    result = extract_core_facts(prompt, answer, mock_llm)
    namespaces = []
    for line in result.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                fact = json.loads(line)
                namespaces.append(fact.get("namespace"))
            except json.JSONDecodeError:
                pass
    return namespaces


class TestResearchDoesNotPollutesUserNamespace:
    def test_research_query_outputs_no_user_namespace(self):
        """
        LLM must never emit namespace='user' when the prompt is a research query.
        We test this with a realistic mock LLM response that correctly uses 'research'.
        """
        good_response = '\n'.join([
            '{"namespace": "research", "key": "skyler_peake_company", "value": "Peake Management LLC", "confidence": 0.9, "source": "web_scraped", "expires_days": 7}',
            '{"namespace": "research", "key": "skyler_peake_location", "value": "Tacoma WA", "confidence": 0.85, "source": "web_scraped", "expires_days": 7}',
        ])
        namespaces = get_namespaces_from_extraction(
            RESEARCH_QUERY[0], RESEARCH_QUERY[1], good_response
        )
        assert "user" not in namespaces, \
            f"Research query leaked into user namespace: {namespaces}"
        assert all(ns == "research" for ns in namespaces)


class TestExplicitIdentityWritesToUserNamespace:
    def test_identity_statement_writes_to_user_namespace(self):
        good_response = '{"namespace": "user", "key": "owner_name", "value": "[owner]", "confidence": 0.95, "source": "user_stated", "expires_days": null}'
        namespaces = get_namespaces_from_extraction(
            IDENTITY_STATEMENT[0], IDENTITY_STATEMENT[1], good_response
        )
        assert "user" in namespaces
        assert "research" not in namespaces

    def test_identity_confidence_is_high(self):
        good_response = '{"namespace": "user", "key": "owner_name", "value": "[owner]", "confidence": 0.95, "source": "user_stated", "expires_days": null}'
        mock_llm = MagicMock(return_value=(good_response, "mock"))
        result = extract_core_facts(IDENTITY_STATEMENT[0], IDENTITY_STATEMENT[1], mock_llm)
        for line in result.strip().splitlines():
            if line.strip().startswith("{"):
                fact = json.loads(line.strip())
                if fact.get("namespace") == "user":
                    assert fact["confidence"] >= 0.85, \
                        f"User namespace write has confidence {fact['confidence']} < 0.85"
