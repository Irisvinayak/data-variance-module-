"""M-31: a mistyped numeric setting names the variable instead of "invalid literal for int()"."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.envutil import ConfigError, env_float, env_int

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_unset_and_blank_use_the_default(monkeypatch):
    monkeypatch.delenv("DV_T_X", raising=False)
    assert env_int("DV_T_X", 7) == 7 and env_int("DV_T_X", "7") == 7 and env_float("DV_T_X", 1.5) == 1.5
    monkeypatch.setenv("DV_T_X", "   ")
    assert env_int("DV_T_X", 7) == 7


def test_valid_values_are_parsed_and_trimmed(monkeypatch):
    monkeypatch.setenv("DV_T_X", " 42 ")
    assert env_int("DV_T_X", 1) == 42
    monkeypatch.setenv("DV_T_X", "2.5")
    assert env_float("DV_T_X", 1) == 2.5


@pytest.mark.parametrize("bad", ["5s", "abc", "1.5", "0x10", "--3"])
def test_a_bad_integer_names_the_variable(monkeypatch, bad):
    monkeypatch.setenv("DV_T_X", bad)
    with pytest.raises(ConfigError) as err:
        env_int("DV_T_X", 1)
    assert "DV_T_X" in str(err.value) and repr(bad) in str(err.value)
    assert isinstance(err.value, ValueError)                     # same exception family as before


def test_a_bad_float_names_the_variable(monkeypatch):
    monkeypatch.setenv("DV_T_X", "fast")
    with pytest.raises(ConfigError, match="DV_T_X"):
        env_float("DV_T_X", 1.0)


def _import_fails_with(module: str, var: str, value: str) -> str:
    env = {**os.environ, var: value, "PYTHONUTF8": "1", "DV_NLP_WARMUP": "false"}
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, env=env, cwd=ROOT, check=False,
    )
    assert proc.returncode != 0
    return proc.stderr


def test_importing_the_nlp_config_with_a_typo_names_the_setting():
    assert "DV_NLP_BM25_TOP_K" in _import_fails_with("backend.nlp.nlp_config", "DV_NLP_BM25_TOP_K", "ten")


def test_importing_the_settings_with_a_typo_names_the_setting():
    assert "DV_MAX_BODY_BYTES" in _import_fails_with("backend.config.settings", "DV_MAX_BODY_BYTES", "big")


def test_no_bare_numeric_conversion_of_an_environment_value_is_left():
    import re

    pattern = re.compile(r"\b(?:int|float)\(\s*(?:os\.getenv|os\.environ)")
    offenders = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "backend")):
        dirnames[:] = [d for d in dirnames if d not in (".venv", "__pycache__")]
        for name in filenames:
            if name.endswith(".py") and name != "envutil.py":      # its own docstring quotes the old pattern
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    if pattern.search(fh.read()):
                        offenders.append(os.path.relpath(path, ROOT))
    assert offenders == []
