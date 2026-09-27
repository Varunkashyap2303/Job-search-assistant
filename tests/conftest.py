"""Every test runs against a throwaway config folder built from tests/fixtures (a fictional persona),
never the real user's config/ files, so the suite behaves the same on any machine."""

import shutil
from pathlib import Path

import pytest

from jobsearch import config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    shutil.copytree(config.ROOT / "config" / "examples", cfg / "examples")
    shutil.copyfile(FIXTURES / "profile.yaml", cfg / "profile.yaml")
    shutil.copyfile(FIXTURES / "preferences.yaml", cfg / "preferences.yaml")
    shutil.copyfile(cfg / "examples" / "watchlist.yaml", cfg / "watchlist.yaml")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "EXAMPLES_DIR", cfg / "examples")
    monkeypatch.setattr(config, "PROFILE_ADDITIONS", cfg / "profile_additions.yaml")
    return cfg
