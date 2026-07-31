"""Test fixtures shipped with eventkit.

Registered as a pytest plugin through the ``pytest11`` entry point, so an
application repository's whole ``conftest.py`` can be::

    pytest_plugins = ["eventkit.testing.plugin"]

The golden Drupal payloads live in ``fixtures/drupal/`` and ship inside the
wheel, so all five applications assert against the same inputs instead of each
inventing its own — which is how three parsers drifted into disagreeing about
composite emails and ``select_other``.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DRUPAL_FIXTURE_DIR = FIXTURE_DIR / "drupal"

__all__ = ["DRUPAL_FIXTURE_DIR", "FIXTURE_DIR"]
