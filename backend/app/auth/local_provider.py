# Creado por Aldo Garcia.
"""Identidades locales reales y de prueba, separadas por proveedor.

El proveedor conserva los cambios en la transaccion recibida. La frontera HTTP
confirma los intentos fallidos antes de rechazar la peticion; el rollback normal
de una respuesta 401 no puede borrar el contador de proteccion de la cuenta.
"""

from __future__ import annotations

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.auth.local_accounts import validate_username
from app.auth.passwords import dummy_verify, verify_password
from app.auth.provider import (
    IdentityProvider,
    IntegrationStatus,
    LoginChallenge,
    NormalizedIdentity,
)
from app.common.errors import ConfigurationError, UnauthorizedError, ValidationFailedError
from app.common.ids import utcnow_naive
from app.common.logging import get_logger
from app.config import AppEnv, AuthProvider, get_settings
from app.database.models import LocalCredential, User

logger = get_logger(__name__)

#: Contador acotado de fallos; nunca impide verificar una contrasena correcta.
MAX_FAILED_ATTEMPTS = 5


class LocalIdentityProvider(IdentityProvider):
    """Autentica contra ``users`` + ``local_credentials``."""

    name = "local"

    def _allowed(self) -> bool:
        settings = get_settings()
        return settings.auth_provider is AuthProvider.LOCAL and settings.is_local_auth_allowed

    def __init__(self) -> None:
        if not self._allowed():
            raise ConfigurationError(
                f"{type(self).__name__} no puede instanciarse en este entorno."
            )

    def start_login(self, session: Session, *, redirect_after: str | None = None) -> LoginChallenge:
        """El proveedor local no redirige: la UI muestra un formulario propio."""
        return LoginChallenge(authorization_url="/login", state="")

    def authenticate(self, session: Session, **kwargs: object) -> NormalizedIdentity:
        """Verifica usuario y contrasena.

        El mensaje de error es identico para "usuario inexistente" y "contrasena
        incorrecta" (gate E2E 33): la respuesta no debe permitir enumerar cuentas.
        """
        username = str(kwargs.get("username") or "").strip()
        password = str(kwargs.get("password") or "")

        if not username or not password:
            raise UnauthorizedError("Usuario o contrasena incorrectos.")

        # Las altas locales solo admiten ASCII. MySQL unicode_ci tambien
        # compararia variantes acentuadas/compatibles con una cuenta ASCII,
        # creando alias que el backoff casefold no puede agrupar. Rechazarlas
        # antes de SQL conserva el contrato de alta y el error generico.
        try:
            validate_username(username)
        except ValidationFailedError:
            dummy_verify()
            raise UnauthorizedError("Usuario o contrasena incorrectos.") from None

        # Orden de bloqueo compartido con cambios de contrasena: usuario,
        # credencial y finalmente sesiones. La emision de sesion pertenece a
        # esta misma transaccion, evitando autenticar con un hash ya sustituido.
        user = session.execute(
            select(User).where(User.username == username).with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if (user is None or not user.is_active or user.auth_source != self.name
                or (self.name == "local" and user.is_synthetic_test)):
            # Se consume tiempo equivalente para no filtrar la existencia.
            dummy_verify()
            logger.warning("auth.local.login_failed", extra={"auth_reason": "unknown_user"})
            raise UnauthorizedError("Usuario o contrasena incorrectos.")

        credential = session.execute(
            select(LocalCredential).where(LocalCredential.user_id == user.id).with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if credential is None:
            dummy_verify()
            logger.warning("auth.local.login_failed", extra={"auth_reason": "no_local_credential"})
            raise UnauthorizedError("Usuario o contrasena incorrectos.")

        now = utcnow_naive()
        if not verify_password(credential.password_hash_argon2id, password):
            # Contador SQL atomico y acotado para auditoria. Un atacante no
            # puede usarlo para bloquear al titular, ni mediante locked_until
            # heredados de una version anterior. El backoff HTTP se aplica
            # DESPUES del commit, sin mantener esta transaccion durante la espera.
            reaches_limit = LocalCredential.failed_attempts >= MAX_FAILED_ATTEMPTS - 1
            session.execute(
                update(LocalCredential)
                .where(
                    LocalCredential.user_id == user.id,
                    LocalCredential.password_hash_argon2id == credential.password_hash_argon2id,
                )
                .values(
                    locked_until=None,
                    failed_attempts=case((reaches_limit, MAX_FAILED_ATTEMPTS),
                                         else_=LocalCredential.failed_attempts + 1),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.expire(credential)
            logger.warning(
                "auth.local.login_failed",
                extra={"auth_reason": "bad_password", "user_opaque_id": user.id},
            )
            raise UnauthorizedError("Usuario o contrasena incorrectos.")

        # Confirmar contra el hash que acabamos de verificar. Ademas del bloqueo
        # MySQL, el CAS impide emitir una sesion si otro cambio de contrasena
        # gano la carrera entre la lectura y esta escritura.
        accepted = session.execute(
            update(LocalCredential).where(
                LocalCredential.user_id == user.id,
                LocalCredential.password_hash_argon2id == credential.password_hash_argon2id,
            ).values(failed_attempts=0, locked_until=None, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        session.expire(credential)
        if accepted.rowcount != 1:
            raise UnauthorizedError("Usuario o contrasena incorrectos.")

        logger.info("auth.local.login_ok", extra={"user_opaque_id": user.id})
        return NormalizedIdentity(
            subject_id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            auth_source=self.name,
            groups=(),
            roles=(),
        )

    def logout_url(self) -> str | None:
        return None

    def status(self) -> IntegrationStatus:
        return (
            IntegrationStatus.CONNECTED_AND_VALIDATED
            if self._allowed()
            else IntegrationStatus.DISABLED
        )


class LocalTestIdentityProvider(LocalIdentityProvider):
    """Proveedor sintetico conservado exclusivamente para development/test."""

    name = "local_test"

    def _allowed(self) -> bool:
        settings = get_settings()
        return (
            settings.auth_provider is AuthProvider.LOCAL_TEST
            and settings.app_env in (AppEnv.DEVELOPMENT, AppEnv.TEST)
            and settings.is_local_auth_allowed
        )
