# tests/jobpulse/conftest.py

import pytest
from unittest.mock import AsyncMock

from jobpulse.form_models import (
    PageSnapshot, FieldInfo, ButtonInfo, VerificationWall,
)
from jobpulse.perplexity import CompanyResearch


@pytest.fixture
def mock_ext_bridge():
    """Minimal AsyncMock driver for orchestrator tests that mock everything else."""
    return AsyncMock()


@pytest.fixture
def sample_snapshot():
    """A typical Greenhouse application page snapshot."""
    return PageSnapshot(
        url="https://boards.greenhouse.io/acme/jobs/123",
        title="Apply - ML Engineer at Acme",
        fields=[
            FieldInfo(selector="#first_name", input_type="text", label="First Name", required=True),
            FieldInfo(selector="#last_name", input_type="text", label="Last Name", required=True),
            FieldInfo(selector="#email", input_type="email", label="Email", required=True),
            FieldInfo(selector="#phone", input_type="tel", label="Phone"),
            FieldInfo(selector="#resume", input_type="file", label="Resume/CV"),
        ],
        buttons=[
            ButtonInfo(selector="button[type=submit]", text="Submit Application", type="submit", enabled=True),
        ],
        verification_wall=None,
        page_text_preview="Apply for ML Engineer at Acme Corp",
        has_file_inputs=True,
        iframe_count=0,
        timestamp=1712150400000,
    )


@pytest.fixture
def sample_company_research():
    """A typical Perplexity company research result."""
    return CompanyResearch(
        company="Acme AI",
        description="AI startup building NLP tools for enterprise",
        industry="Technology",
        size="startup",
        employee_count=50,
        tech_stack=["Python", "PyTorch", "FastAPI", "AWS"],
        recent_news=["Raised Series A"],
        red_flags=[],
        culture="Remote-first, active blog",
    )
