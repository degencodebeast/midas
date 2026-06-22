import pathlib
import tomllib


REQUIRED_SCANNER_SHA = "5f92552e8fdd688808e2709eefc176ab681b7f4f"
REQUIRED_SCANNER_URL = "https://github.com/degencodebeast/trading-scanner"


def test_scanner_source_is_vps_resolvable_git_pin():
    pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    source = pyproject["tool"]["uv"]["sources"]["magic-scanner"]

    assert source["git"] == REQUIRED_SCANNER_URL
    assert source["rev"] == REQUIRED_SCANNER_SHA
    assert not source["git"].startswith("file:")


def test_uv_lock_uses_same_deployable_scanner_pin():
    text = pathlib.Path("uv.lock").read_text()

    assert REQUIRED_SCANNER_URL in text
    assert REQUIRED_SCANNER_SHA in text
    assert "file:///Users/degencodebeast/Projects/personal/trading/trading-scanner" not in text
