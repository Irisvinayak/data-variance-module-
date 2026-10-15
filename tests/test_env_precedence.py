"""M-08: which value wins - the operating system, the root .env, or the per-instance overlay.

The loader is called with a plain dict as the environment, so the real environment and the real
.env are never touched. Which servers should set DV_DOTENV_OVERRIDE=false is an audit of each
server's own variables, recorded in docs/ISSUES_STATUS.md.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.config.settings import load_environment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def files(tmp_path):
    root_env = write(tmp_path / ".env", "A=from_dotenv\nB=from_dotenv\nVERSION=5.5\nONLY_DOTENV=1\nEMPTY=\n")
    overlay = write(tmp_path / ".env.60", "VERSION=6.0\nDV_SERVER_PORT=8003\nA=from_overlay\n")
    return root_env, overlay


# ── default: override=True (what every existing server relies on) ────────────

def test_by_default_dotenv_beats_the_operating_system(files):
    root_env, _ = files
    env = {"A": "from_os", "UNRELATED": "x"}
    load_environment(root_env, None, True, env)
    assert env["A"] == "from_dotenv" and env["UNRELATED"] == "x" and env["ONLY_DOTENV"] == "1"


def test_by_default_the_overlay_beats_dotenv_and_the_os(files):
    root_env, overlay = files
    env = {"VERSION": "9.9"}
    load_environment(root_env, overlay, True, env)
    assert env["VERSION"] == "6.0" and env["A"] == "from_overlay" and env["B"] == "from_dotenv"
    assert env["DV_SERVER_PORT"] == "8003"


# ── DV_DOTENV_OVERRIDE=false: real variables win ─────────────────────────────

def test_with_override_off_an_os_variable_beats_dotenv(files):
    root_env, _ = files
    env = {"A": "from_os"}
    load_environment(root_env, None, False, env)
    assert env["A"] == "from_os"                               # the operator's value stands
    assert env["B"] == "from_dotenv" and env["ONLY_DOTENV"] == "1"     # everything else still comes from .env


def test_with_override_off_the_overlay_still_beats_dotenv(files):
    """The side-by-side deployment depends on this: the overlay's VERSION must replace the root .env's."""
    root_env, overlay = files
    env = {}
    load_environment(root_env, overlay, False, env)
    assert env["VERSION"] == "6.0" and env["A"] == "from_overlay" and env["B"] == "from_dotenv"


def test_with_override_off_an_os_variable_also_beats_the_overlay(files):
    root_env, overlay = files
    env = {"VERSION": "5.5", "DV_SERVER_PORT": "9000"}
    load_environment(root_env, overlay, False, env)
    assert env["VERSION"] == "5.5" and env["DV_SERVER_PORT"] == "9000"
    assert env["A"] == "from_overlay"                          # not an OS variable: the overlay may set it


def test_the_os_snapshot_can_be_given_explicitly(files):
    root_env, _ = files
    env = {"A": "set_later_by_something_else"}
    load_environment(root_env, None, False, env, os_keys=set())       # nothing counts as an OS variable
    assert env["A"] == "from_dotenv"


# ── edge cases ───────────────────────────────────────────────────────────────

def test_missing_files_are_ignored(tmp_path):
    env = {"A": "x"}
    load_environment(str(tmp_path / "nope.env"), str(tmp_path / "nope2.env"), True, env)
    assert env == {"A": "x"}


def test_a_key_without_a_value_does_not_erase_the_os_value(tmp_path):
    path = write(tmp_path / ".env", "NOVALUE\nA=1\n")
    env = {"NOVALUE": "keep"}
    load_environment(path, None, True, env)
    assert env["NOVALUE"] == "keep" and env["A"] == "1"


def test_an_explicitly_empty_value_is_applied_like_load_dotenv_does(files):
    root_env, _ = files
    env = {"EMPTY": "was_set"}
    load_environment(root_env, None, True, env)
    assert env["EMPTY"] == ""


def test_the_real_environment_is_not_touched_by_these_tests(files):
    root_env, _ = files
    before = dict(os.environ)
    load_environment(root_env, None, True, {})
    assert dict(os.environ) == before


# ── the real module, in a clean process ──────────────────────────────────────

def _probe(extra_env):
    # This test process already loaded the repository .env into os.environ, so VERSION would look
    # like an operating-system variable to the child. Start the child without it.
    env = {k: v for k, v in os.environ.items() if k not in ("DV_DOTENV_OVERRIDE", "DV_ENV_FILE", "VERSION")}
    env.update(extra_env, PYTHONUTF8="1")
    code = "import os, backend.config.settings as s; print(s.DOTENV_OVERRIDE, os.environ.get('VERSION'))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    return out.stdout.strip()


def test_the_default_is_unchanged_in_the_real_module():
    assert _probe({"VERSION": "6.0"}) == "True 5.5"            # the repository's .env still wins


def test_off_makes_the_os_win_in_the_real_module():
    assert _probe({"VERSION": "6.0", "DV_DOTENV_OVERRIDE": "false"}) == "False 6.0"


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", " false "])
def test_every_spelling_of_false_turns_it_off(value):
    assert _probe({"DV_DOTENV_OVERRIDE": value}).startswith("False")


@pytest.mark.parametrize("value", ["true", "1", "yes", "anything-else", ""])
def test_anything_else_keeps_the_default(value):
    assert _probe({"DV_DOTENV_OVERRIDE": value}).startswith("True")


def test_the_real_overlay_mechanism_still_works_with_override_off(tmp_path):
    overlay = write(tmp_path / ".env.test", "VERSION=6.0\n")
    assert _probe({"DV_ENV_FILE": overlay, "DV_DOTENV_OVERRIDE": "false"}) == "False 6.0"
    assert _probe({"DV_ENV_FILE": overlay}) == "True 6.0"
