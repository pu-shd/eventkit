"""Real-world :class:`~eventkit.notify.Transport` implementations.

Each of these needs an SDK :mod:`eventkit.notify` itself does not depend on
(``smtplib`` is stdlib, but ``resend`` and ``azure-communication-email`` are
extras), so each is its own module and :func:`eventkit.notify.transport_from_settings`
imports it lazily, only for the transport actually selected. ``LogTransport``
and ``MemoryTransport`` need no SDK and live in :mod:`eventkit.notify` itself.
"""

from __future__ import annotations
