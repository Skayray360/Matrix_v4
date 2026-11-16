# Creado por Aldo Garcia.
"""Cambios de identidad concurrentes en MySQL 8.4 descartable, sin modelos.

Usa cuentas nuevas de cada prueba y transacciones REPEATABLE READ. La ausencia
de servicio o una base no autorizada falla: no hay omisiones en el job dirigido.
"""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.auth import local_accounts, local_provider, sessions
from app.auth.local_accounts import LOCAL_ADMIN_ROLE, LOCAL_READER_ROLE, create_local_account, update_local_account
from app.authorization import policy
from app.common.errors import ForbiddenError, UnauthorizedError, ValidationFailedError
from app.common.ids import new_id, utcnow_naive
from app.config import AuthProvider, get_settings
from app.database.models import LocalCredential, Role, SessionRecord, User, UserRole
from scripts.test_environment import require_disposable_database

pytestmark = pytest.mark.integration


@pytest.fixture
def identities_mysql(monkeypatch):
    require_disposable_database()
    settings = get_settings()
    url = make_url(settings.database_url.get_secret_value())
    assert url.drivername == "mysql+pymysql" and url.host in {"127.0.0.1", "localhost", "::1"}
    engine = create_engine(url, isolation_level="REPEATABLE READ", hide_parameters=True,
                           connect_args={"connect_timeout": 5, "read_timeout": 15, "write_timeout": 15})

    @event.listens_for(engine, "connect")
    def bounded_lock_wait(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")

    factory = sessionmaker(engine, expire_on_commit=False)
    configured = settings.model_copy(update={"auth_provider": AuthProvider.LOCAL,
                                             "local_test_auth_enabled": False,
                                             "local_test_seed_users_enabled": False})
    for module in (local_accounts, local_provider, sessions, policy):
        monkeypatch.setattr(module, "get_settings", lambda: configured)
    ids, contexts, passwords, usernames = [], [], [], []
    try:
        with factory.begin() as db:
            assert str(db.scalar(text("SELECT VERSION()"))).startswith("8.4.")
            assert db.scalar(text("SELECT @@transaction_isolation")) == "REPEATABLE-READ"
            existing = db.execute(select(User.id).join(UserRole).join(Role).where(
                User.auth_source == "local", User.is_active.is_(True), Role.name == LOCAL_ADMIN_ROLE,
            )).first()
            assert existing is None, "Use una base desechable sin administradores locales ajenos a esta suite."
            for number in range(2):
                username, password = f"Lifecycle{number}_{new_id()}", secrets.token_urlsafe(24)
                user = create_local_account(db, username=username, display_name=username,
                                             password=password, role_name=LOCAL_ADMIN_ROLE)
                ids.append(user.id)
                usernames.append(username)
                passwords.append(password)
                issued = sessions.create_session(db, user=user, auth_source="local")
                contexts.append(policy.get_policy_engine().build_context(
                    db, user=user, session_id=issued.session_id, request_id="synthetic-lifecycle",
                ))
        yield SimpleNamespace(factory=factory, ids=ids, contexts=contexts, passwords=passwords, usernames=usernames)
    finally:
        if ids:
            with factory.begin() as db:
                db.execute(delete(User).where(User.id.in_(ids)))
        engine.dispose()


@pytest.mark.parametrize("operation", ["deactivate", "demote"])
@pytest.mark.parametrize("target_kind", ["self", "other"])
def test_concurrent_admin_changes_keep_one_admin_and_revalidate_old_snapshots(identities_mysql, operation, target_kind):
    environment = identities_mysql
    barrier = Barrier(2)

    def mutate(index):
        try:
            with environment.factory.begin() as db:
                # Ambos establecen un snapshot que contiene los dos admins
                # antes de competir por el mutex y los permisos vigentes.
                assert len(db.execute(select(User.id).where(User.id.in_(environment.ids),
                                      User.is_active.is_(True))).scalars().all()) == 2
                barrier.wait(timeout=10)
                target = index if target_kind == "self" else 1 - index
                update_local_account(db, user_id=environment.ids[target], actor=environment.contexts[index],
                                     is_active=False if operation == "deactivate" else None,
                                     role_name=LOCAL_READER_ROLE if operation == "demote" else None)
            return "applied"
        except (ForbiddenError, UnauthorizedError, ValidationFailedError):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, index) for index in (0, 1)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == ["applied", "rejected"]
    with environment.factory() as db:
        admins = db.execute(select(User.id).join(UserRole).join(Role).where(
            User.id.in_(environment.ids), User.is_active.is_(True), Role.name == LOCAL_ADMIN_ROLE,
        )).scalars().all()
        assert len(admins) == 1
        changed = next(user_id for user_id in environment.ids if user_id not in admins)
        assert all(row.revoked_at is not None for row in db.execute(
            select(SessionRecord).where(SessionRecord.user_id == changed),
        ).scalars())


def test_mysql_collation_alias_cannot_bypass_ascii_login_boundary(identities_mysql):
    environment = identities_mysql
    alias = environment.usernames[0].replace("Lifecycle", "Lífecyclé")
    with environment.factory.begin() as db:
        # La columna real demuestra el alias que una comparacion Python sola
        # no detectaria. El proveedor debe rechazarlo antes de buscar en SQL.
        assert db.execute(select(User.id).where(User.username == alias)).scalar_one() == environment.ids[0]
        with pytest.raises(UnauthorizedError):
            local_provider.LocalIdentityProvider().authenticate(db, username=alias, password=environment.passwords[0])


def test_create_rechecks_permissions_after_an_old_repeatable_read_snapshot(identities_mysql):
    environment = identities_mysql
    with environment.factory.begin() as stale:
        # La consulta establece un snapshot previo a la retirada de permisos.
        assert stale.get(User, environment.ids[0]).is_active
        with environment.factory.begin() as other:
            update_local_account(other, user_id=environment.ids[0], role_name=LOCAL_READER_ROLE,
                                 actor=environment.contexts[1])
        with pytest.raises(ForbiddenError):
            create_local_account(stale, username=f"Late_{new_id()}", display_name="Late synthetic account",
                                 password=secrets.token_urlsafe(24), role_name=LOCAL_ADMIN_ROLE,
                                 actor=environment.contexts[0])


def test_failed_attempts_persist_but_cannot_lock_out_valid_password(identities_mysql):
    environment = identities_mysql
    for _ in range(5):
        # La excepcion se consume dentro de begin: el contador debe confirmarse
        # entre intentos, como hace la frontera HTTP, no desaparecer por rollback.
        with environment.factory.begin() as db, pytest.raises(UnauthorizedError):
            local_provider.LocalIdentityProvider().authenticate(
                db, username=environment.usernames[0], password=secrets.token_urlsafe(24),
            )
    with environment.factory.begin() as db:
        credential = db.get(LocalCredential, environment.ids[0])
        assert credential.failed_attempts == 5
        credential.locked_until = utcnow_naive() + timedelta(minutes=5)
    with environment.factory.begin() as db:
        identity = local_provider.LocalIdentityProvider().authenticate(
            db, username=environment.usernames[0], password=environment.passwords[0],
        )
        assert identity.subject_id == environment.ids[0]
    with environment.factory() as db:
        credential = db.get(LocalCredential, environment.ids[0])
        assert credential.failed_attempts == 0 and credential.locked_until is None
