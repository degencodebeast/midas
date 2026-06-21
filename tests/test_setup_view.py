# tests/test_setup_view.py
"""setup_view no longer fabricates fixed-percentage stop/target levels.

The scanner is the sole authority for stop and campaign-DOL levels
(consumed via ``authorized_setup_from_scan``); MIDAS must never derive
levels from a fixed buffer percentage or a fixed risk-reward multiple.
"""
import importlib

import magic_agent.setup_view as setup_view


def test_setup_view_is_importable():
    # cli.py and the rest of the agent layer must still be able to import this module.
    importlib.reload(setup_view)


def test_no_fabricated_fixed_percentage_level_paths():
    # The fixed-percentage stop / fixed-RR target fabrication must be gone.
    assert not hasattr(setup_view, "from_scan_result"), (
        "from_scan_result fabricated fixed-percentage stop/target levels and must be removed"
    )
