"""Plain text from terminal output. Runners send what their agent printed, which can carry
colours, cursor moves and window titles; the board keeps only the visible text."""

import re

# Strings that run until a terminator: OSC (window titles, hyperlinks), DCS, SOS, PM and APC.
# They end with BEL or ST (ESC \ or its 8-bit form); one cut off at the end runs to the end.
_STRING = r"(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)"
# CSI: colours (SGR), cursor moves, erasing, modes such as ESC [ ? 25 h.
_CSI = r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
# Any other escape: charset selection (ESC ( B), keypad modes (ESC =), ESC 7 and the like.
_ESCAPE = r"\x1b[ -/]*[0-~]"
# What is left of control characters, keeping newline and tab: C0, DEL and C1. A lone ESC
# whose sequence was cut off goes with them.
_CONTROL = r"[\x00-\x08\x0b-\x1f\x7f-\x9f]"

_TERMINAL_CODES = re.compile("|".join((_STRING, _CSI, _ESCAPE, _CONTROL)))


def strip_terminal_codes(text: str) -> str:
    """The text without terminal escape sequences and control characters, except newline and
    tab. A carriage return is dropped too, so CRLF line ends become LF."""
    return _TERMINAL_CODES.sub("", text)
