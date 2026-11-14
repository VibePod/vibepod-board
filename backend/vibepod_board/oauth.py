"""OAuth sign-in for the MCP endpoint.

`BoardOAuthProvider` makes the board its own authorization server, so an MCP client needs
nothing but the URL: it registers itself, sends the admin to the consent page, and trades
the code it gets back for a token. That token is an ordinary project-scoped API token, so
this provider is also what verifies every bearer token on `/mcp`, however it was created.

FastMCP serves the protocol endpoints (`/authorize`, `/token`, `/register`, the
`/.well-known` documents) and checks redirect URIs, PKCE and code expiry; the methods here
only store and load. No refresh tokens are issued: a token is valid until it is revoked
under API Tokens.
"""

from collections.abc import Callable
from urllib.parse import urlparse

import anyio
from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlmodel import Session

from vibepod_board.db import get_engine
from vibepod_board.errors import BoardError
from vibepod_board.services import oauth, tokens

CONSENT_PATH = "/oauth/consent"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def is_loopback_redirect(uri: str) -> bool:
    """A redirect to the machine the browser runs on, which is where native clients listen."""
    parsed = urlparse(uri)
    return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS


async def _db[T](operation: Callable[[Session], T]) -> T:
    """Runs a blocking service call in a worker thread with its own session."""

    def run() -> T:
        with Session(get_engine(), expire_on_commit=False) as session:
            return operation(session)

    return await anyio.to_thread.run_sync(run)


class BoardOAuthProvider(OAuthProvider):
    def __init__(self, public_url: str) -> None:
        super().__init__(
            base_url=public_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

    @property
    def consent_url(self) -> str:
        return f"{str(self.base_url).rstrip('/')}{CONSENT_PATH}"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = await _db(lambda session: oauth.get_client(session, client_id))
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Anyone who reaches the board may register, so a client can only ever send the
        # browser back to the machine it runs on, never to another site.
        for uri in client_info.redirect_uris or []:
            if not is_loopback_redirect(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description="Only http redirect URIs on localhost, 127.0.0.1 or "
                    f"[::1] are accepted: {uri}",
                )
        info = client_info.model_dump(mode="json", exclude_none=True)
        await _db(lambda session: oauth.save_client(session, client_info.client_id, info))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Stores the request and sends the browser to the consent page, where the admin
        picks the projects."""
        request_id = await _db(
            lambda session: oauth.create_request(
                session,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                code_challenge=params.code_challenge,
                state=params.state,
                scopes=params.scopes or [],
                resource=params.resource,
            )
        )
        return f"{self.consent_url}?request={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await _db(lambda session: oauth.load_code(session, authorization_code))
        if row is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row.scopes,
            expires_at=row.expires_at.timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        try:
            created = await _db(lambda session: oauth.redeem_code(session, authorization_code.code))
        except BoardError as error:
            raise TokenError(error="invalid_grant", error_description=error.message) from error
        if created is None:
            raise TokenError(
                error="invalid_grant", error_description="authorization code does not exist"
            )
        return OAuthToken(
            access_token=created.token,
            token_type="Bearer",
            scope=" ".join(authorization_code.scopes) or None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        raise TokenError(error="invalid_grant", error_description="Refresh tokens are not issued")

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Accepts unrevoked board API tokens and carries their project scope as a claim."""
        authenticated = await _db(lambda session: tokens.authenticate_token(session, token))
        if authenticated is None:
            return None
        return AccessToken(
            token=token,
            client_id=authenticated.token_id,
            scopes=[],
            claims={"project_ids": authenticated.project_ids},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Not offered: the revocation endpoint is off, tokens are revoked under API Tokens."""
