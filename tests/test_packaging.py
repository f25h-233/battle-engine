"""M4 packaging: wheel must ship web templates/static + battle CLI entry."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_build_system_setuptools():
    py = _pyproject()
    assert py["build-system"]["build-backend"] == "setuptools.build_meta"
    assert any("setuptools" in r for r in py["build-system"]["requires"])


def test_version_0_4_0():
    assert _pyproject()["project"]["version"] == "0.4.0"


def test_console_script_entry():
    scripts = _pyproject()["project"]["scripts"]
    assert scripts["battle"] == "battle.cli:main"


def test_web_package_data_shipped():
    pd = _pyproject()["tool"]["setuptools"]["package-data"]
    assert "templates/*.html" in pd["battle.web"]
    assert "static/*.js" in pd["battle.web"] and "static/*.css" in pd["battle.web"]
