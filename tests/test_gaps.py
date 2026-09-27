import pytest
import yaml

from jobsearch import config
from jobsearch.tailor import gaps
from jobsearch.tailor.coverage import keyword_coverage
from jobsearch.tailor.facts import load_catalogue
from jobsearch.tailor.tailor import Bullet, EntryChoice, SkillGroup, TailoredResume


@pytest.fixture
def additions_file(tmp_path, monkeypatch):
    path = tmp_path / "profile_additions.yaml"
    monkeypatch.setattr(config, "PROFILE_ADDITIONS", path)
    return path


def test_merge_additions_appends_without_duplicates():
    base = {"skills": {"cloud": ["Docker"]}, "experience": [{"id": "acme", "facts": ["a"]}], "projects": []}
    merged = config.merge_additions(base, {"skills": {"cloud": ["Docker", "Kubernetes"], "other": ["Go"]},
                                           "facts": {"acme": ["a", "b"]}})
    assert merged["skills"] == {"cloud": ["Docker", "Kubernetes"], "other": ["Go"]}
    assert merged["experience"][0]["facts"] == ["a", "b"]
    assert base["experience"][0]["facts"] == ["a"]  # base untouched


def test_add_to_profile_flows_into_catalogue(additions_file):
    assert config.add_to_profile("skill", "cloud_devops", "Kubernetes")
    assert not config.add_to_profile("skill", "cloud_devops", "kubernetes")  # duplicate, any case
    assert config.add_to_profile("fact", "acme", "Cut p95 latency from 4s to 1.5s by caching inventory API calls.")
    assert yaml.safe_load(additions_file.read_text(encoding="utf-8"))["skills"]["cloud_devops"] == ["Kubernetes"]
    cat = load_catalogue()
    assert "Kubernetes" in cat.skills["cloud_devops"]
    assert "caching inventory API calls" in list(cat.entries["acme"].facts.values())[-1]
    assert "kubernetes" in cat.vocabulary()  # so the verifier now accepts it as a skill


def test_addressable_gaps_filters_ad_meta_notes():
    coverage = [{"keyword": "latency", "status": "gap"}, {"keyword": "RAG", "status": "covered"},
                {"keyword": "Qdrant", "status": "in_profile_not_used"}]
    assessment = {"gaps": ["Description is a partial snippet", "No media domain experience", "Latency"]}
    assert gaps.addressable_gaps(coverage, assessment) == ["latency", "No media domain experience"]
    assert gaps.unused_keywords(coverage) == ["Qdrant"]


def test_apply_and_guidance(additions_file):
    cat = load_catalogue()
    t = {x.target: x for x in gaps.targets(cat)}
    added = gaps.apply_additions([(t["cloud_devops"], "", "Terraform"), (t["acme"], "", "latency")])
    assert added == ["Skill · cloud devops: Terraform"]  # a fact with no description is skipped
    g = gaps.build_guidance(["Qdrant"], added)
    assert "Qdrant" in g and "Terraform" in g


def test_slash_keywords_count_as_covered():
    cat = load_catalogue()
    r = TailoredResume(headline="AI Engineer", summary=Bullet(text="RAGAS evaluation of RAG", sources=[]),
                       skills=[SkillGroup(label="x", items=["RAG"])],
                       experience=[EntryChoice(ref="acme", bullets=[])], projects=[], cover_letter=[], tailoring_notes="")
    cov = keyword_coverage(r, cat, {"must_have_skills": ["Evaluation/quality measurement"], "ats_keywords": []})
    assert cov[0]["status"] == "covered"
