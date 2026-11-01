"""Model output parsing and prompt assembly."""

from __future__ import annotations

import pytest

from app.generator import GenerationError, parse_files
from app.prompt import user_prompt


def model_reply() -> str:
    return """Here is the bundle.

### FILE: scraper.py
```python
print("scraper")
```

### FILE: mapper.py
```python
print("mapper")
```

### FILE: runner.sh
```bash
python3 scraper.py
python3 mapper.py
```

### FILE: README.md
```markdown
# Example
```

### NOTES
- json api preferred
"""


def test_parse_files_returns_all_bundles() -> None:
    files = parse_files(model_reply())
    assert set(files) == {"scraper.py", "mapper.py", "runner.sh", "README.md"}
    assert files["scraper.py"].strip() == 'print("scraper")'
    assert files["runner.sh"].startswith("python3 scraper.py")


def test_missing_file_raises() -> None:
    reply = model_reply().replace("### FILE: mapper.py", "### FILE: other.py")
    with pytest.raises(GenerationError):
        parse_files(reply)


def test_reply_without_markers_raises() -> None:
    with pytest.raises(GenerationError):
        parse_files("just some prose without file sections")


def test_prompt_contains_schema_probe_and_country() -> None:
    probe = {
        "final_url": "https://example.com/list",
        "status": 200,
        "api_candidates": [{"url": "https://example.com/api/items"}],
        "skeleton": "<html><body><div class='card'>x</div></body></html>",
    }
    prompt = user_prompt(probe, {"country": "italy", "site_name": "example.com"})
    assert "reference scraper" in prompt.lower()
    assert "canonical schema" in prompt.lower()
    assert "'italy'" in prompt
    assert "example.com/api/items" in prompt
    assert "CANONICAL_KEYS" in prompt
