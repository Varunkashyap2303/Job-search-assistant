"""A brand-new user, from an empty config folder to a ready-to-run setup, driven through the real dashboard."""

import shutil

import pytest
import yaml
from sqlmodel import SQLModel, create_engine
from streamlit.testing.v1 import AppTest

from jobsearch import config, db, llm, onboarding

RESUME = """Jane Citizen - Data Analyst - jane@example.com - 0400 000 000 - Brisbane, QLD
Experience: Data Analyst, Acme Health (Feb 2023 - present)
- Built the weekly revenue dashboard in Power BI used by 40 sales staff.
- Cut monthly reporting time from 3 days to 4 hours by automating SQL extracts.
Education: Bachelor of Commerce, University of Queensland, 2019-2022""" + " filler" * 40


@pytest.fixture
def new_user(tmp_path, monkeypatch):
    cfg = tmp_path / "new_user_config"
    shutil.copytree(config.ROOT / "config" / "examples", cfg / "examples")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "EXAMPLES_DIR", cfg / "examples")
    monkeypatch.setattr(config, "PROFILE_ADDITIONS", cfg / "profile_additions.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)

    def fake_parse(*, output_format, **kw):
        if output_format is onboarding.ProfileIn:
            return onboarding.ProfileIn(
                name="Jane Citizen", headline="Data Analyst", email="jane@example.com", phone="0400 000 000",
                location="Brisbane, QLD", linkedin="", github="", portfolio="", work_rights_line="", summary="Data analyst.",
                skills=[onboarding.SkillGroupIn(group="Analytics", items=["SQL", "Power BI"])],
                experience=[onboarding.ExperienceIn(title="Data Analyst", company="Acme Health", location="Brisbane, QLD",
                                                    start="2023-02", end="present", project="", stack=["Power BI", "SQL"],
                                                    facts=["Built the weekly revenue dashboard in Power BI used by 40 sales staff.",
                                                           "Cut monthly reporting time from 3 days to 4 hours by automating SQL extracts."])],
                projects=[], education=[onboarding.EducationIn(degree="Bachelor of Commerce", institution="University of Queensland",
                                                               location="Brisbane", dates="2019 to 2022")],
                certifications=[], awards=[])
        return onboarding.SearchSettings(
            target_roles=["Data Analyst", "Business Intelligence Analyst"], avoid_roles="sales roles",
            queries=["data analyst", "power bi analyst"], title_keywords=["data analyst", "BI", "analytics"],
            title_exclude_keywords=["sales"], preferred_locations=["Brisbane"], allow_remote=True,
            intel_topic="health tech", intel_keywords=["health tech", "digital health"])
    monkeypatch.setattr(llm, "parse", fake_parse)
    return cfg


def test_new_user_can_set_up_from_the_dashboard(new_user):
    # Overview: setup checklist shown, run buttons disabled
    app = AppTest.from_file(str(config.ROOT / "dashboard" / "app.py"), default_timeout=90).run()
    assert not app.exception
    assert any("get you set up" in m.value for m in app.markdown)
    assert [b.disabled for b in app.sidebar.button if b.label == "Run scout now"] == [True]
    assert (new_user / "profile.yaml").exists() and (new_user / "preferences.yaml").exists()

    # 1. My profile: resume -> profile
    at = AppTest.from_function(_profile_page, default_timeout=90).run()
    at.text_area(key="resume_paste").input(RESUME).run()
    [b for b in at.button if b.label.startswith("Read my resume")][0].click().run()
    assert not at.exception
    [b for b in at.button if b.label == "Save to my profile"][0].click().run()
    prof = yaml.safe_load((new_user / "profile.yaml").read_text(encoding="utf-8"))
    assert prof["identity"]["name"] == "Jane Citizen"
    assert prof["experience"][0]["facts"][1].startswith("Cut monthly reporting time")
    assert prof["application"]["first_name"] == "Jane" and prof["work_rights"]["status"] == "citizen"

    # 2. Job search: description -> search settings
    at = AppTest.from_function(_search_page, default_timeout=90).run()
    at.text_area(key="looking_for").input("Data analyst jobs in Brisbane, Power BI heavy, no sales.").run()
    [b for b in at.button if b.label.startswith("Suggest search settings")][0].click().run()
    [b for b in at.button if b.label == "Save search settings"][0].click().run()
    prefs = yaml.safe_load((new_user / "preferences.yaml").read_text(encoding="utf-8"))
    assert prefs["search"]["target_roles"] == ["Data Analyst", "Business Intelligence Analyst"]
    assert r"\bdata\s+analyst\b" in prefs["search"]["title_include"]
    assert prefs["search"]["locations"]["preferred"] == ["Brisbane"]
    assert prefs["intel"]["topic"] == "health tech"

    # only the API key is left
    app.run()
    checklist = " ".join(m.value for m in app.markdown)
    assert checklist.count(":material/check_circle:") == 2


def _profile_page():
    from jobsearch.ui import profile_tab

    profile_tab.render()


def _search_page():
    from jobsearch.ui import search_tab

    search_tab.render()


def test_persona_follows_the_new_users_work_rights(new_user):
    from jobsearch import persona
    assert persona.work_rights_status() == "citizen"
    assert persona.hard_exclude_patterns() == []          # citizens can hold clearances
    prof = config.profile()
    prof["work_rights"].update(status="visa", visa="Subclass 482", can_hold_clearance=False)
    config.save_profile(prof)
    assert "Subclass 482" in persona.work_rights_text()
    assert any("clearance" in p for p in persona.hard_exclude_patterns())


def test_keyword_to_regex():
    assert onboarding.keyword_to_regex("data analyst") == r"\bdata\s+analyst\b"
    assert onboarding.keyword_to_regex(r"\bml\b|machine learning") == r"\bml\b|machine learning"
