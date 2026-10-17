import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROW = re.compile(r"^\|[^|]*\|\s*([^|]+?)\s*\|\s*`([^`:]+):(\w+)`\s*\|$", re.M)


def spec_rows():
    return ROW.findall((ROOT / "spec.md").read_text())


def code_value(path: str, name: str) -> float:
    m = re.search(rf"\b{name}\s*=\s*(-?[\d.\s*]+?)f?\s*[;,\n]", (ROOT / path).read_text())
    assert m, f"{name} not found in {path}"
    return eval(m.group(1), {"__builtins__": {}})


def test_spec_has_checked_rows():
    assert len(spec_rows()) > 20


@pytest.mark.parametrize("value,path,name", spec_rows())
def test_spec_matches_code(value, path, name):
    assert code_value(path, name) == float(value)
