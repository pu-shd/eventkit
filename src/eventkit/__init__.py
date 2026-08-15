"""eventkit — reusable core for the Sherrerd event-management stack.

Five applications sit on this library: ``ticket-reconciler``, ``lodging-planner``,
``nametag-press``, ``link-forge`` and ``poster-gallery``. The library exists
because those five were originally two, and the two shared roughly ten thousand
lines of near-duplicated logic — three Drupal payload parsers that disagreed with
each other, six copies of one affiliation rule, two hand-rolled backup formats,
and eighteen imperative authorization checks any one of which could be forgotten.

Import-weight contract, enforced by ``tests/unit/test_import_weight.py``:

* ``eventkit.identity``, ``eventkit.drupal``, ``eventkit.eventprofile``,
  ``eventkit.errors`` and ``eventkit.logging`` import with **only** pydantic,
  PyYAML and the standard library.
* ``eventkit.eventbrite.models`` and ``eventkit.eventbrite.aggregate`` likewise.
* FastAPI, SQLAlchemy and httpx are needed only by modules that genuinely serve
  HTTP, touch a database, or make outbound calls, and are imported lazily inside
  the functions that need them.

That is what keeps ``link-forge`` (stateless, no database) and ``nametag-press``
(no outbound HTTP) light.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
