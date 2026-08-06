"""Jinja2 rendering for :mod:`eventkit.notify`, with adopter-overridable templates.

Loader precedence is adopter directory, then event-profile directory, then
eventkit's own shipped defaults — a :class:`jinja2.ChoiceLoader` tries each in
order and uses the first template found, so an adopter can override
``sync_failed.html.j2`` without forking this package, and an event profile can
override it for one event without touching the adopter's site-wide templates.

Autoescape is on for ``.html.j2`` templates and off for ``.subject.txt.j2`` /
``.txt.j2`` ones: the HTML body interpolates values like ``ctx["full_name"]``
that came from a webform, so a name containing ``<`` or ``&`` must not inject
markup into the rendered email — the same bug class the shipped UI kit's
``app.js`` has on the JS side (string-interpolated HTML with no escaping), fixed
here for outbound email instead of the browser. The subject line and the plain
-text alternative part are not HTML documents; escaping them would put literal
``&amp;`` in an email subject instead of ``&``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import EventKitError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jinja2 import Environment

__all__ = ["RenderedMessage", "Renderer", "TemplateMissingError"]

#: Shipped defaults. Extracted from ``ticketed/backend/notifications.py:43-96``
#: and de-CAARMSified: no conference name, no discount codes, no Princeton
#: theme — see ``examples/caarms-2026`` and the ``princeton-orfe`` theme for
#: branded content instead.
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"


class TemplateMissingError(EventKitError):
    """Neither an adopter, a profile, nor eventkit's defaults have this event's template."""


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    subject: str
    html: str
    text: str | None


class Renderer:
    """Renders one event's subject/HTML/text templates against a context dict.

    Each event needs ``<event>.subject.txt.j2`` and ``<event>.html.j2``.
    ``<event>.txt.j2`` is optional — a missing one leaves
    :attr:`RenderedMessage.text` as ``None``, which every shipped
    :class:`~eventkit.notify.Transport` treats as "HTML-only, no plaintext
    alternative part".
    """

    def __init__(
        self,
        *,
        adopter_dir: str | Path | None = None,
        profile_dir: str | Path | None = None,
    ) -> None:
        self._search_dirs = [
            str(d) for d in (adopter_dir, profile_dir, _DEFAULT_TEMPLATE_DIR) if d
        ]
        self._env: Environment | None = None

    def _environment(self) -> Environment:
        if self._env is None:
            from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

            self._env = Environment(
                loader=ChoiceLoader([FileSystemLoader(d) for d in self._search_dirs]),
                # Template names are "<event>.html.j2" / "<event>.subject.txt.j2" /
                # "<event>.txt.j2", so matching must be on the full suffix — a
                # bare "html" extension check would never match ".html.j2" and
                # autoescape would silently stay off for every template.
                autoescape=select_autoescape(enabled_extensions=("html.j2",), default=False),
            )
        return self._env

    def render(self, event: str, ctx: Mapping[str, Any]) -> RenderedMessage:
        from jinja2 import TemplateNotFound

        env = self._environment()

        try:
            subject_template = env.get_template(f"{event}.subject.txt.j2")
        except TemplateNotFound as exc:
            raise TemplateMissingError(
                f"no {event}.subject.txt.j2 template found in any of "
                f"{self._search_dirs!r}."
            ) from exc
        try:
            html_template = env.get_template(f"{event}.html.j2")
        except TemplateNotFound as exc:
            raise TemplateMissingError(
                f"no {event}.html.j2 template found in any of {self._search_dirs!r}."
            ) from exc

        subject = subject_template.render(ctx).strip()
        html = html_template.render(ctx)

        text: str | None = None
        try:
            text_template = env.get_template(f"{event}.txt.j2")
        except TemplateNotFound:
            text = None
        else:
            text = text_template.render(ctx)

        return RenderedMessage(subject=subject, html=html, text=text)
