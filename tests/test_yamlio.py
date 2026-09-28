"""The fast reader must give the same answer as the safe one, and stay safe."""
from __future__ import annotations

import pathlib

import pytest
import yaml

from orrery import yamlio

FIXTURES = sorted(pathlib.Path(__file__).resolve().parents[1].joinpath("fixtures").rglob("*.yaml"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_the_fast_reader_parses_every_fixture_exactly_as_safe_load(path):
    import json
    text = path.read_text(encoding="utf-8")
    # `==` would let True pass for 1; the JSON text compares types too
    assert json.dumps(yamlio.load(text), sort_keys=True, default=str) == json.dumps(yaml.safe_load(text), sort_keys=True, default=str)


def test_the_fast_path_is_actually_used_when_libyaml_is_there():
    if getattr(yaml, "__with_libyaml__", False):
        assert yamlio._Loader is yaml.CSafeLoader


def test_a_parse_error_carries_the_readable_message():
    with pytest.raises(yaml.YAMLError) as exc:
        yamlio.load("a: 1\n\tb: 2\n")
    assert "^" in str(exc.value)  # the pure loader shows the line and a caret; the C one does not


def test_it_refuses_python_object_tags_like_safe_load():
    with pytest.raises(yaml.YAMLError):
        yamlio.load("!!python/object/apply:os.system ['true']")


def test_without_libyaml_it_falls_back_to_the_pure_python_safe_loader(monkeypatch):
    import importlib

    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    mod = importlib.reload(yamlio)
    try:
        assert mod._Loader is yaml.SafeLoader
        assert mod.load("a: [1, 2]") == {"a": [1, 2]}
    finally:
        monkeypatch.undo()
        importlib.reload(yamlio)
