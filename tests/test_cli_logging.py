"""`_cmd_run` console-logging configuration tests.

The run loop must emit a visible operator narrative to stdout. ``_cmd_run`` is the
single place that configures the root logger (INFO by default, DEBUG under ``-v``),
without clobbering an already-configured logger.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from magic_agent import cli
from magic_agent.cli import build_parser


@pytest.fixture
def _patched_run(monkeypatch, tmp_path):
    """Stub build_app + run_live so _cmd_run exercises only the logging config."""
    monkeypatch.setattr("magic_agent.app.build_app", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(
        "magic_agent.live.run_live", lambda app, *, clock, max_iters: 0
    )

    def _args(**overrides):
        base = dict(executor="paper", max_iters=1, root_dir=tmp_path)
        base.update(overrides)
        return SimpleNamespace(**base)

    return _args


def _reset_root_logging():
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)


def test_verbose_flag_parses():
    p = build_parser()
    args = p.parse_args(["run", "-v"])
    assert args.verbose is True
    args2 = p.parse_args(["run"])
    assert args2.verbose is False


def test_cmd_run_configures_console_logging_at_info(_patched_run):
    _reset_root_logging()
    cli._cmd_run(_patched_run(verbose=False))
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert root.handlers, "console handler should be configured"


def test_cmd_run_verbose_sets_debug(_patched_run):
    _reset_root_logging()
    cli._cmd_run(_patched_run(verbose=True))
    assert logging.getLogger().level == logging.DEBUG


def test_cmd_run_does_not_double_configure(_patched_run):
    _reset_root_logging()
    # Pre-configure with a sentinel handler at WARNING. _cmd_run must not stack a
    # second basicConfig handler on top (basicConfig is a no-op when handlers exist).
    sentinel = logging.StreamHandler()
    logging.getLogger().addHandler(sentinel)
    before = list(logging.getLogger().handlers)
    cli._cmd_run(_patched_run(verbose=False))
    after = logging.getLogger().handlers
    assert sentinel in after
    # No new handler added by basicConfig (it short-circuits when handlers exist).
    assert len(after) == len(before)
    _reset_root_logging()


def test_cmd_run_tolerates_missing_verbose_attr(_patched_run):
    # Some call-sites (e.g. the twak-mode CLI test) build args WITHOUT a verbose
    # attribute; _cmd_run must default it via getattr and not raise.
    _reset_root_logging()
    args = SimpleNamespace(executor="paper", max_iters=1)
    cli._cmd_run(args)  # must not raise (no verbose / root_dir attrs)
    _reset_root_logging()
