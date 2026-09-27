from datetime import datetime, timedelta, timezone

import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import TypeAdapter

from jobsearch.config import preferences, strip_todos
from jobsearch.scout.dedupe import dedupe_key, is_same_job, norm_company
from jobsearch.scout.filters import classify_location, prefilter, title_passes, work_rights_blocker
from jobsearch.scout.models import RawPosting
from jobsearch.scout.scorer import JobAssessment


@pytest.fixture
def prefs():
    return preferences()


@pytest.mark.parametrize("location, expected", [
    ("Melbourne, VIC", ("Melbourne", "preferred")),
    ("Sydney, Australia HQ; US - Remote", ("Sydney", "preferred")),
    ("Brisbane", ("Brisbane", "acceptable")),
    ("Remote (AU)", ("Remote (AU)", "remote_au")),
    ("Remote (Australia); Perth", ("Remote (AU)", "remote_au")),
    ("Australia", ("Australia", "acceptable")),
    ("Melbourne, FL, United States", None),
    ("Perth, Scotland", None),
    ("Seattle, WA", None),
    ("London, UK", None),
    ("Singapore; Shenton Way, Singapore", None),
])
def test_classify_location(prefs, location, expected):
    assert classify_location(location, prefs) == expected


@pytest.mark.parametrize("title, ai_native, ok", [
    ("AI Engineer", False, True),
    ("Senior Machine Learning Engineer", False, True),
    ("LLM Engineer", False, True),
    ("Software Engineer, Agents", False, True),
    ("Graduate Software Engineer", True, True),
    ("Graduate Software Engineer", False, False),
    ("Staff ML Engineer", False, False),
    ("Engineering Manager, AI", False, False),
    ("AI Account Executive", False, False),
    ("Senior Product Manager - AI", False, False),
    ("Real Estate Agent", False, False),
    ("Forward Deployed Engineer", False, True),
    ("AI Solutions Architect", False, True),
    ("Solutions Architect", True, True),
    ("Senior Data Scientist", False, False),
])
def test_title_passes(prefs, title, ai_native, ok):
    assert title_passes(title, ai_native, prefs)[0] is ok


def test_work_rights_blocker(prefs):
    assert work_rights_blocker("Must hold an NV1 clearance", prefs)
    assert work_rights_blocker("ability to obtain a Baseline clearance", prefs)
    assert not work_rights_blocker("Full working rights in Australia required", prefs)


def test_prefilter_age(prefs):
    old = RawPosting(source="lever", external_id="1", company="X", title="AI Engineer", location="Melbourne",
                     url="", posted_at=datetime.now(timezone.utc) - timedelta(days=45))
    assert prefilter(old, prefs) == (None, "older than max_age_days")


def test_dedupe():
    assert norm_company("Canva Pty Ltd") == norm_company("Canva")
    assert dedupe_key("Xero Limited", "Sr. ML Engineer", "Melbourne") == dedupe_key("Xero", "Senior ML Engineer", "melbourne")
    assert is_same_job("Senior AI Engineer (Hybrid)", "Sydney", "Senior AI Engineer", "Sydney")
    assert not is_same_job("Senior AI Engineer", "Sydney", "Senior AI Engineer", "Melbourne")
    assert not is_same_job("AI Engineer", "Sydney", "AI Product Manager", "Sydney")


def test_assessment_schema_is_structured_output_compatible():
    schema = transform_schema(TypeAdapter(JobAssessment).json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(JobAssessment.model_fields)


def test_profile_clean_drops_todos():
    assert strip_todos({"a": "TODO", "b": 1, "todo": "x", "c": [{"todo": "y", "d": 2}]}) == {"b": 1, "c": [{"d": 2}]}
