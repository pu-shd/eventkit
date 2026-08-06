"""Tests for eventkit.auth: the header matrix, dev-bypass hardening, redirect
vs 401 by path, and WS ticket expiry/tampering — the priorities PLAN.md's
testing appendix (E.1) calls out by name for this module."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient

from eventkit.auth import (
    AllowList,
    DeniedTheme,
    EasyAuth,
    Principal,
    WsTicketError,
    install,
    issue_ws_ticket,
    render_access_denied,
    verify_ws_ticket,
    ws_dependency,
)
from eventkit.errors import ConfigError

ADMIN = "admin@example.edu"
OUTSIDER = "outsider@example.edu"


def claims_header(*, name: str = "Admin Example") -> str:
    blob = {"auth_typ": "aad", "claims": [{"typ": "name", "val": name}]}
    return base64.b64encode(json.dumps(blob).encode()).decode()


def build_app(auth: EasyAuth) -> FastAPI:
    app = FastAPI()
    app.state.auth = auth
    install(app, auth)

    optional_dependency = auth.optional()

    @app.get("/")
    def page(principal: Principal = Depends(auth.require)) -> dict:
        return {"email": principal.email}

    @app.get("/api/data")
    def api(principal: Principal = Depends(auth.require)) -> dict:
        return {"email": principal.email}

    @app.get("/api/optional")
    def optional(principal: Principal | None = Depends(optional_dependency)) -> dict:
        return {"email": principal.email if principal else None}

    return app


# ---------------------------------------------------------------------------
# AllowList
# ---------------------------------------------------------------------------
class TestAllowList:
    def test_empty_denies_everyone(self) -> None:
        allow_list = AllowList([])
        assert allow_list.allows(ADMIN) is False
        assert not allow_list

    def test_exact_email_case_insensitive(self) -> None:
        allow_list = AllowList([ADMIN])
        assert allow_list.allows(ADMIN)
        assert allow_list.allows(ADMIN.upper())
        assert not allow_list.allows(OUTSIDER)

    def test_domain_suffix_rule(self) -> None:
        allow_list = AllowList(["@example.edu"])
        assert allow_list.allows("anyone@example.edu")
        assert not allow_list.allows("anyone@notexample.edu")
        assert not allow_list.allows("anyone@example.org")

    def test_parse_csv_strips_and_lowercases(self) -> None:
        allow_list = AllowList.parse(f" {ADMIN.upper()} , @example.org ")
        assert allow_list.allows(ADMIN)
        assert allow_list.allows("person@example.org")

    def test_none_email_never_allowed(self) -> None:
        assert AllowList([ADMIN]).allows(None) is False


# ---------------------------------------------------------------------------
# EasyAuth construction hardening
# ---------------------------------------------------------------------------
class TestEasyAuthConstruction:
    def test_dev_principal_refused_on_app_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBSITE_SITE_NAME", "my-app-service")
        with pytest.raises(ConfigError, match="WEBSITE_SITE_NAME"):
            EasyAuth(AllowList([ADMIN]), dev_principal=ADMIN)

    def test_dev_principal_fine_off_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
        EasyAuth(AllowList([ADMIN]), dev_principal=ADMIN)

    def test_no_dev_principal_never_raises_even_on_azure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBSITE_SITE_NAME", "my-app-service")
        EasyAuth(AllowList([ADMIN]))


# ---------------------------------------------------------------------------
# The header matrix + redirect vs 401 by path
# ---------------------------------------------------------------------------
class TestHeaderMatrix:
    @pytest.fixture
    def auth(self) -> EasyAuth:
        return EasyAuth(AllowList([ADMIN]))

    @pytest.fixture
    def client(self, auth: EasyAuth) -> TestClient:
        return TestClient(build_app(auth), follow_redirects=False)

    def test_no_headers_on_page_path_redirects_to_login(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["location"].startswith("/.auth/login/aad?post_login_redirect_uri=")

    def test_no_headers_on_api_path_is_401(self, client: TestClient) -> None:
        response = client.get("/api/data")
        assert response.status_code == 401

    def test_name_header_alone_is_not_enough(self, client: TestClient) -> None:
        """Today one spoofable header is the entire authentication. Not here."""
        response = client.get("/api/data", headers={"X-MS-CLIENT-PRINCIPAL-NAME": ADMIN})
        assert response.status_code == 401

    def test_malformed_claims_blob_is_denied(self, client: TestClient) -> None:
        response = client.get(
            "/api/data",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": ADMIN,
                "X-MS-CLIENT-PRINCIPAL": "not-valid-base64-json",
            },
        )
        assert response.status_code == 401

    def test_full_header_set_allowed(self, client: TestClient) -> None:
        response = client.get(
            "/api/data",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": ADMIN,
                "X-MS-CLIENT-PRINCIPAL": claims_header(),
                "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
            },
        )
        assert response.status_code == 200
        assert response.json()["email"] == ADMIN

    def test_full_header_set_not_on_allow_list_gets_themed_403(self, client: TestClient) -> None:
        response = client.get(
            "/api/data",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": OUTSIDER,
                "X-MS-CLIENT-PRINCIPAL": claims_header(),
            },
        )
        assert response.status_code == 403
        assert "Access denied" in response.text
        assert OUTSIDER in response.text

    def test_require_claims_header_false_accepts_name_alone(self) -> None:
        auth = EasyAuth(AllowList([ADMIN]), require_claims_header=False)
        client = TestClient(build_app(auth))
        response = client.get("/api/data", headers={"X-MS-CLIENT-PRINCIPAL-NAME": ADMIN})
        assert response.status_code == 200

    def test_optional_dependency_returns_none_rather_than_raising(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/optional")
        assert response.status_code == 200
        assert response.json() == {"email": None}

    def test_optional_dependency_respects_allow_list(self, client: TestClient) -> None:
        response = client.get(
            "/api/optional",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": OUTSIDER,
                "X-MS-CLIENT-PRINCIPAL": claims_header(),
            },
        )
        assert response.json() == {"email": None}


class TestDevBypass:
    def test_dev_bypass_used_when_headers_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
        auth = EasyAuth(AllowList([ADMIN]), dev_principal=ADMIN)
        client = TestClient(build_app(auth))
        response = client.get("/api/data")
        assert response.status_code == 200
        assert response.json()["email"] == ADMIN

    def test_real_headers_still_win_over_dev_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
        auth = EasyAuth(AllowList([ADMIN]), dev_principal=OUTSIDER)
        client = TestClient(build_app(auth))
        response = client.get(
            "/api/data",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": ADMIN,
                "X-MS-CLIENT-PRINCIPAL": claims_header(),
            },
        )
        assert response.json()["email"] == ADMIN


# ---------------------------------------------------------------------------
# WebSocket tickets: expiry and tampering
# ---------------------------------------------------------------------------
class TestWsTicket:
    def test_roundtrip(self) -> None:
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret="s3cr3t-value", scope="checkin")
        verified = verify_ws_ticket(ticket, secret="s3cr3t-value", scope="checkin")
        assert verified.email == ADMIN

    def test_tampered_signature_rejected(self) -> None:
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret="s3cr3t-value")
        last = ticket[-1]
        tampered = ticket[:-1] + ("x" if last != "x" else "y")
        with pytest.raises(WsTicketError):
            verify_ws_ticket(tampered, secret="s3cr3t-value")

    def test_wrong_secret_rejected(self) -> None:
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret="s3cr3t-value")
        with pytest.raises(WsTicketError):
            verify_ws_ticket(ticket, secret="a-different-secret")

    def test_expired_ticket_rejected(self) -> None:
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret="s3cr3t-value", ttl_s=-1)
        with pytest.raises(WsTicketError, match="expired"):
            verify_ws_ticket(ticket, secret="s3cr3t-value")

    def test_wrong_scope_rejected(self) -> None:
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret="s3cr3t-value", scope="checkin")
        with pytest.raises(WsTicketError, match="scope"):
            verify_ws_ticket(ticket, secret="s3cr3t-value", scope="admin")

    def test_malformed_ticket_rejected(self) -> None:
        with pytest.raises(WsTicketError):
            verify_ws_ticket("this-is-not-a-real-ticket", secret="s3cr3t-value")

    def test_empty_ticket_rejected(self) -> None:
        with pytest.raises(WsTicketError):
            verify_ws_ticket("", secret="s3cr3t-value")


# ---------------------------------------------------------------------------
# Themed access-denied page
# ---------------------------------------------------------------------------
class TestDeniedTheme:
    def test_from_profile_maps_branding(self, event_profile) -> None:
        theme = DeniedTheme.from_profile(event_profile)
        assert theme.app_title == event_profile.event.title
        assert theme.brand_color == event_profile.branding.brand_color
        assert theme.support_contact == event_profile.event.contact_email

    def test_render_autoescapes_untrusted_email(self) -> None:
        theme = DeniedTheme(app_title="Test App")
        html = render_access_denied('"><script>alert(1)</script>@example.edu', theme)
        assert "<script>alert(1)</script>" not in html
        assert "Test App" in html

    def test_render_includes_support_contact_when_set(self) -> None:
        theme = DeniedTheme(app_title="Test App", support_contact="help@example.edu")
        html = render_access_denied(ADMIN, theme)
        assert "help@example.edu" in html


# ---------------------------------------------------------------------------
# The shipped testing.plugin fixtures (principal, make_client, as_anonymous)
# ---------------------------------------------------------------------------
class TestPluginFixtures:
    def test_principal_fixture_is_allow_listed_by_default(self, principal: Principal) -> None:
        assert principal.email == "admin@example.edu"

    def test_make_client_overrides_the_required_dependency(self, make_client, principal) -> None:
        app = build_app(EasyAuth(AllowList([ADMIN])))
        client = make_client(app, principal=principal)
        response = client.get("/api/data")
        assert response.status_code == 200
        assert response.json()["email"] == principal.email

    def test_make_client_accepts_a_bare_email_string(self, make_client) -> None:
        app = build_app(EasyAuth(AllowList([OUTSIDER])))
        client = make_client(app, principal=OUTSIDER)
        response = client.get("/api/data")
        assert response.status_code == 200
        assert response.json()["email"] == OUTSIDER

    def test_as_anonymous_exercises_the_real_unauthenticated_path(self, as_anonymous) -> None:
        app = build_app(EasyAuth(AllowList([ADMIN])))
        client = as_anonymous(app)
        assert client.get("/api/data").status_code == 401


# ---------------------------------------------------------------------------
# Malformed claims blobs that decode but aren't shaped as expected
# ---------------------------------------------------------------------------
class TestClaimsBlobEdgeCases:
    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(build_app(EasyAuth(AllowList([ADMIN]))))

    def test_claims_blob_that_is_not_an_object_is_denied(self, client: TestClient) -> None:
        not_an_object = base64.b64encode(json.dumps([1, 2, 3]).encode()).decode()
        response = client.get(
            "/api/data",
            headers={"X-MS-CLIENT-PRINCIPAL-NAME": ADMIN, "X-MS-CLIENT-PRINCIPAL": not_an_object},
        )
        assert response.status_code == 401

    def test_claims_list_with_a_non_dict_item_is_tolerated(self, client: TestClient) -> None:
        blob = {"claims": ["not-a-dict", {"typ": "name", "val": "Admin Example"}]}
        header = base64.b64encode(json.dumps(blob).encode()).decode()
        response = client.get(
            "/api/data",
            headers={"X-MS-CLIENT-PRINCIPAL-NAME": ADMIN, "X-MS-CLIENT-PRINCIPAL": header},
        )
        assert response.status_code == 200
        assert response.json()["email"] == ADMIN


# ---------------------------------------------------------------------------
# Dependency caching (needed for dependency_overrides to match at all)
# ---------------------------------------------------------------------------
class TestDependencyCaching:
    def test_dependency_is_the_same_object_on_repeat_calls(self) -> None:
        auth = EasyAuth(AllowList([ADMIN]))
        assert auth.dependency() is auth.dependency()
        assert auth.require is auth.dependency()

    def test_optional_is_the_same_object_on_repeat_calls(self) -> None:
        auth = EasyAuth(AllowList([ADMIN]))
        assert auth.optional() is auth.optional()


# ---------------------------------------------------------------------------
# ws_dependency: the FastAPI WebSocket route wiring
# ---------------------------------------------------------------------------
class TestWsDependency:
    SECRET = "ws-secret-value"

    def _ws_app(self) -> FastAPI:
        app = FastAPI()
        auth = EasyAuth(AllowList([ADMIN]))
        dependency = ws_dependency(auth, secret=self.SECRET, scope="checkin")

        @app.websocket("/ws")
        async def endpoint(
            websocket: WebSocket, principal: Principal = Depends(dependency)
        ) -> None:
            await websocket.accept()
            await websocket.send_json({"email": principal.email})
            await websocket.close()

        return app

    def test_valid_ticket_connects(self) -> None:
        client = TestClient(self._ws_app())
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret=self.SECRET, scope="checkin")
        with client.websocket_connect(f"/ws?ticket={ticket}") as ws:
            assert ws.receive_json() == {"email": ADMIN}

    def test_tampered_ticket_is_rejected_at_connect(self) -> None:
        client = TestClient(self._ws_app())
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret=self.SECRET, scope="checkin")
        tampered = ticket[:-1] + ("x" if ticket[-1] != "x" else "y")
        with pytest.raises(Exception):  # noqa: B017 - starlette raises WebSocketDisconnect
            with client.websocket_connect(f"/ws?ticket={tampered}") as ws:
                ws.receive_json()

    def test_ticket_for_a_non_allow_listed_email_is_rejected_at_connect(self) -> None:
        client = TestClient(self._ws_app())
        ticket = issue_ws_ticket(Principal(email=OUTSIDER), secret=self.SECRET, scope="checkin")
        with pytest.raises(Exception):  # noqa: B017 - starlette raises WebSocketDisconnect
            with client.websocket_connect(f"/ws?ticket={ticket}") as ws:
                ws.receive_json()

    def test_malformed_expiry_field_is_rejected(self) -> None:
        payload = f"checkin|{ADMIN}|not-a-number"
        signature = hmac.new(self.SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        raw = f"{payload}|{signature}".encode()
        ticket = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        with pytest.raises(WsTicketError, match="malformed"):
            verify_ws_ticket(ticket, secret=self.SECRET, scope="checkin")
