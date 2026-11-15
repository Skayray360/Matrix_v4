# Creado por Aldo Garcia.
"""La identidad federada exige el mismo transporte seguro al publicar en red."""

import pytest
from pydantic import ValidationError

from app.config import Settings

pytestmark = [pytest.mark.unit, pytest.mark.security]


def federated(provider, **updates):
    values = {
        "_env_file": None, "app_env": "development", "auth_provider": provider,
        "app_secret_key": "0" * 64, "local_test_auth_enabled": False, "local_test_seed_users_enabled": False,
        # secrets-scan: allow (DSN sintetico, no apunta a una cuenta real)
        "database_url": "mysql+pymysql://matrixrh:synthetic-password@127.0.0.1/matrix_rh",
        "entra_tenant_id": "synthetic-tenant", "entra_client_id": "synthetic-client",
        "entra_redirect_uri": "https://matrix.example.test/api/v1/auth/callback",
        "oidc_issuer": "https://identity.example.test", "oidc_authorization_endpoint": "https://identity.example.test/auth",
        "oidc_token_endpoint": "https://identity.example.test/token", "oidc_jwks_uri": "https://identity.example.test/jwks",
        "oidc_client_id": "synthetic-client", "oidc_client_secret": "synthetic-client-secret",
        "oidc_redirect_uri": "https://matrix.example.test/api/v1/auth/callback",
    }
    return Settings(**(values | updates))


@pytest.mark.parametrize("provider", ["entra", "oidc"])
@pytest.mark.parametrize("app_host", ["127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize("base_url,secure", [
    ("http://matrix.example.test", True),
    ("https://matrix.example.test", False),
    ("http://matrix.example.test", False),
])
def test_public_federated_access_requires_https_and_secure_cookie(provider, app_host, base_url, secure):
    with pytest.raises(ValidationError, match="HTTPS y SESSION_COOKIE_SECURE"):
        federated(provider, app_host=app_host, app_base_url=base_url, session_cookie_secure=secure)


@pytest.mark.parametrize("provider", ["entra", "oidc"])
def test_direct_network_listener_cannot_hide_behind_loopback_base_url(provider):
    with pytest.raises(ValidationError, match="HTTPS y SESSION_COOKIE_SECURE"):
        federated(provider, app_host="0.0.0.0", app_base_url="http://127.0.0.1:8000", session_cookie_secure=False)


@pytest.mark.parametrize("provider", ["entra", "oidc"])
@pytest.mark.parametrize("app_host", ["127.0.0.1", "0.0.0.0"])
def test_valid_https_proxy_or_network_listener_remains_supported(provider, app_host):
    settings = federated(
        provider, app_host=app_host, app_base_url="https://matrix.example.test", session_cookie_secure=True,
    )
    assert settings.session_cookie_secure


@pytest.mark.parametrize("provider", ["entra", "oidc"])
def test_loopback_development_remains_supported(provider):
    assert federated(provider, app_host="127.0.0.1", app_base_url="http://127.0.0.1:8000").auth_provider == provider
