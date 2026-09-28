"""One YAML reader for the whole engine, and it is the fast one when it can be.

PyYAML's `safe_load` is pure Python. On a synthetic map of twenty-five thousand entities and
fifty thousand relations it took about ten seconds to read — most of the time a `simulate`
spent before it could answer anything. libyaml's `CSafeLoader` parses the same safe subset
in C, about two seconds on the same file, and PyYAML ships it wherever libyaml is present.
Where it is not, the pure-Python loader is used and the answer is the same, only slower.

Two differences, both at the edges. The C loader is stricter about some escapes (a
surrogate pair spelled out as `\\ud83d\\ude00` is rejected rather than joined). And its error
messages are terser, with no snippet or caret — so on a parse error the text is read again
by the pure-Python loader, whose message is the one a person can act on. That costs nothing
on a file that parses.
"""
from __future__ import annotations

from typing import Any

import yaml

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load(text: str) -> Any:
    """Parse YAML text with the safe loader — the C one when available."""
    try:
        return yaml.load(text, Loader=_Loader)  # _Loader is CSafeLoader or SafeLoader, never the full loader
    except yaml.YAMLError:
        if _Loader is yaml.SafeLoader:
            raise
        return yaml.load(text, Loader=yaml.SafeLoader)  # raises with the readable message (or loads)
