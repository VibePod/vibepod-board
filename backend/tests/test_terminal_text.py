"""Terminal output is stored as plain text. The fixtures are shared with vibepod-cli, which
cleans agent errors the same way before it reports them."""

import json
from pathlib import Path

import pytest

from vibepod_board.terminal_text import strip_terminal_codes

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "terminal_text.json").read_text())


@pytest.mark.parametrize("case", FIXTURES, ids=[case["name"] for case in FIXTURES])
def test_strips_terminal_codes(case: dict[str, str]) -> None:
    assert strip_terminal_codes(case["input"]) == case["expected"]


def test_stripping_twice_changes_nothing() -> None:
    for case in FIXTURES:
        assert strip_terminal_codes(case["expected"]) == case["expected"]
