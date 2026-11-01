"""The reference bundle is the contract: it must validate, and break loudly."""

from __future__ import annotations

from pathlib import Path

from app.validate import validate_bundle

REFERENCE = Path(__file__).resolve().parents[1] / "app" / "reference"

README = (
    "# Example scraper\n\n"
    "Scrapes the public example listing page and maps records into the canonical "
    "schema. Run `bash runner.sh`; proxies come from `proxies.json`, and the delay "
    "is configurable through environment variables. HTML fallback is documented.\n"
)


def good_bundle() -> dict[str, str]:
    return {
        "scraper.py": (REFERENCE / "scraper.py").read_text(encoding="utf-8"),
        "mapper.py": (REFERENCE / "mapper.py").read_text(encoding="utf-8"),
        "runner.sh": (REFERENCE / "runner.sh").read_text(encoding="utf-8"),
        "README.md": README,
        "requirements.txt": (REFERENCE / "requirements.txt").read_text(encoding="utf-8"),
    }


def test_reference_bundle_passes() -> None:
    assert validate_bundle(good_bundle()) == []


def test_forbidden_import_is_rejected() -> None:
    bundle = good_bundle()
    bundle["scraper.py"] = "import subprocess\n" + bundle["scraper.py"]
    errors = validate_bundle(bundle)
    assert any("forbidden import" in error for error in errors)


def test_hardcoded_credentials_are_rejected() -> None:
    bundle = good_bundle()
    bundle["mapper.py"] = (
        'MONGO = "mongodb://admin:secret@db.example:27017"\n' + bundle["mapper.py"]
    )
    errors = validate_bundle(bundle)
    assert any("credentials" in error for error in errors)


def test_missing_schema_field_is_rejected() -> None:
    bundle = good_bundle()
    bundle["mapper.py"] = bundle["mapper.py"].replace('    "title",\n', "", 1)
    errors = validate_bundle(bundle)
    assert any("CANONICAL_KEYS" in error and "title" in error for error in errors)


def test_missing_contract_function_is_rejected() -> None:
    bundle = good_bundle()
    bundle["scraper.py"] = bundle["scraper.py"].replace(
        "def fetch_page(", "def not_fetch_page(", 1
    )
    errors = validate_bundle(bundle)
    assert any("fetch_page" in error for error in errors)


def test_syntax_error_is_reported() -> None:
    bundle = good_bundle()
    bundle["mapper.py"] = "def map_listing(:\n    pass\n"
    errors = validate_bundle(bundle)
    assert any("syntax error" in error for error in errors)


def test_missing_requirements_is_rejected() -> None:
    bundle = good_bundle()
    del bundle["requirements.txt"]
    errors = validate_bundle(bundle)
    assert any("requirements.txt" in error for error in errors)


def test_requirements_must_list_the_imports() -> None:
    bundle = good_bundle()
    bundle["requirements.txt"] = "beautifulsoup4>=4.12\n"
    errors = validate_bundle(bundle)
    assert any("requirements.txt" in error and "requests" in error for error in errors)
