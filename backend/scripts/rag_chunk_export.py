# Creado por Aldo Garcia.
"""Exportacion local explicita para revisar chunks; no es una API de autenticacion.

El operador ya debe controlar la instalacion y su cuenta de sistema operativo.
La cuenta SQL elegida solo acota la revision: diagnostics.read y ACL vigente,
incluido el propietario real de la conversacion. No consulta modelos ni escribe
SQL/Qdrant. En modo embedded requiere detener Matrix antes de abrir el almacen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import time
import warnings
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from qdrant_client import QdrantClient, models
from sqlalchemy import select

from app.authorization.context import PERM_DIAGNOSTICS_READ
from app.authorization.policy import get_policy_engine
from app.config import QdrantMode
from app.database.models import Conversation, Document, User

MAX_CHUNKS = 2000
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
SNAPSHOT_SECONDS = 15
EXPORT_FIELDS = [
    "document_id", "generation", "index_fingerprint", "scope", "category",
    "owner_user_id", "conversation_id", "chunk_index", "text", "section",
    "page_or_sheet", "extraction_incomplete",
]


class ExportDenied(ValueError):
    """Cuenta/documento no autorizado o no publicado; mensaje deliberadamente comun."""


class ExportPayloadMismatch(ValueError):
    """La respuesta vectorial no cumple el manifiesto autorizado."""


class ExportStateChanged(ValueError):
    """La publicacion o su autorizacion cambiaron durante la lectura."""


class ExportLimitExceeded(ValueError):
    """El contenido supera el presupuesto explicito del exportador."""


@dataclass(frozen=True)
class DocumentScope:
    document_id: str
    scope: str
    category: str | None
    owner_user_id: str | None
    conversation_id: str | None
    generation: str
    index_fingerprint: str
    chunk_count: int
    filename: str


def authorized_document(db, *, username: str, document_id: str, conversation_id: str | None) -> DocumentScope:
    """SQL aplica ACL antes de abrir Qdrant, sin excepciones para administradores."""
    user = db.execute(select(User).where(User.username == username, User.is_active.is_(True))).scalar_one_or_none()
    if user is None:
        raise ExportDenied("Exportacion no autorizada o documento no disponible.")
    policy = get_policy_engine()
    context = policy.build_context(db, user=user, session_id=str(uuid4()), request_id=str(uuid4()))
    if not context.has_permission(PERM_DIAGNOSTICS_READ):
        raise ExportDenied("Exportacion no autorizada o documento no disponible.")
    query = select(Document).where(
        Document.id == document_id, Document.deleted_at.is_(None), Document.status == "indexed",
        Document.active_generation.is_not(None), Document.index_fingerprint.is_not(None),
    )
    if conversation_id is None:
        query = query.where(
            Document.scope == "corporate", Document.category.in_(policy.effective_categories(context)),
        )
    else:
        query = query.join(Conversation, Conversation.id == Document.conversation_id).where(
            Document.scope == "conversation", Document.owner_user_id == context.user_id,
            Document.conversation_id == conversation_id, Conversation.user_id == context.user_id,
            Conversation.deleted_at.is_(None), Conversation.purged_at.is_(None),
        )
    document = db.execute(query).scalar_one_or_none()
    if document is None or not document.active_generation or not document.index_fingerprint or document.chunk_count < 1:
        raise ExportDenied("Exportacion no autorizada o documento no disponible.")
    return DocumentScope(
        document.id, document.scope, document.category, document.owner_user_id, document.conversation_id,
        document.active_generation, document.index_fingerprint, document.chunk_count, document.filename,
    )


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


class SnapshotClient:
    """Qdrant puede normalizar su copia temporal, nunca el indice del operador."""

    def __init__(self, client, temporary):
        self.client = client
        self.temporary = temporary

    def scroll(self, **kwargs):
        return self.client.scroll(**kwargs)

    def close(self):
        try:
            self.client.close()
        finally:
            self.temporary.cleanup()


def _snapshot_collection(path: Path, collection: str, destination: Path) -> None:
    """Formato embedded fijado en qdrant-client 1.20; copia acotada bajo su lock."""
    import portalocker

    if not collection or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                             for character in collection):
        raise ValueError("Nombre de coleccion no compatible con exportacion local.")
    source = path / "collection" / collection / "storage.sqlite"
    for required in (path / ".lock", path / "meta.json", source):
        if not required.is_file() or any(_linked(part) for part in (required, *required.parents)):
            raise ValueError("Se requiere almacenamiento existente sin enlaces.")
    # r+b exige lock existente: no lo crea, trunca ni modifica.
    with (path / ".lock").open("r+b") as lock:
        portalocker.lock(lock, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
        try:
            if source.stat().st_size > MAX_SNAPSHOT_BYTES or (path / "meta.json").stat().st_size > 1024 * 1024:
                raise ExportLimitExceeded("El indice supera el limite de copia temporal.")
            if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-journal")):
                raise ValueError("El almacen requiere cierre limpio antes de exportar.")
            metadata = json.loads((path / "meta.json").read_text(encoding="utf-8"))
            # No copiar aliases, configuracion ni datos de otras colecciones.
            selected = {"collections": {collection: metadata["collections"][collection]}, "aliases": {}}
            if collection in metadata.get("text_indexes", {}):
                selected["text_indexes"] = {collection: metadata["text_indexes"][collection]}
            target = destination / "collection" / collection / "storage.sqlite"
            (destination / "collection").mkdir(mode=0o700)
            target.parent.mkdir(mode=0o700)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            deadline = time.monotonic() + SNAPSHOT_SECONDS
            # immutable impide crear sidecars incluso durante la lectura. Con
            # lock exclusivo y sin WAL/journal pendientes la copia es estable.
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)) as original:
                page_size = original.execute("PRAGMA page_size").fetchone()[0]

                def progress(_status, _remaining, total):
                    if total * page_size > MAX_SNAPSHOT_BYTES or time.monotonic() > deadline:
                        raise ExportLimitExceeded("Se excedio el presupuesto de copia temporal.")

                with closing(sqlite3.connect(target)) as copied:
                    original.backup(copied, pages=128, progress=progress, sleep=0.1)
            with os.fdopen(os.open(destination / "meta.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                           "w", encoding="utf-8") as output:
                json.dump(selected, output)
        finally:
            portalocker.unlock(lock)


def open_existing_store(settings, *, collection: str):
    """Nunca crea almacenamiento ni colecciones, ni evade el lock del backend."""
    if settings.qdrant_mode is QdrantMode.SERVER:
        return QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=30, trust_env=False, check_compatibility=False,
        )
    path = settings.qdrant_storage_path.absolute()
    temporary = tempfile.TemporaryDirectory(prefix="matrix-chunk-review-")
    try:
        destination = Path(temporary.name)
        if os.name == "nt":
            _windows_private_file(destination)
        _snapshot_collection(path, collection, destination)
        # El SDK puede limpiar duplicados al abrir embedded. Solo abre la copia.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            client = QdrantClient(path=str(destination))
        return SnapshotClient(client, temporary)
    except BaseException:
        temporary.cleanup()
        raise


def read_chunks(client, *, collection: str, document: DocumentScope, max_chunks: int) -> dict:
    """Scroll acotado, sin embeddings, dentro de una unica publicacion y su ACL."""
    if not 1 <= max_chunks <= MAX_CHUNKS:
        raise ValueError("Limite de chunks invalido.")
    expected = {
        "document_id": document.document_id, "scope": document.scope,
        "generation": document.generation, "index_fingerprint": document.index_fingerprint,
    }
    if document.scope == "corporate":
        expected["category"] = document.category
    else:
        expected.update(owner_user_id=document.owner_user_id, conversation_id=document.conversation_id)
    selector = models.Filter(must=[
        models.FieldCondition(key=key, match=models.MatchValue(value=value)) for key, value in expected.items()
    ])
    chunks, indices, offsets = [], set(), set()
    offset, text_bytes = None, 0
    while True:
        points, following = client.scroll(
            collection_name=collection, scroll_filter=selector, offset=offset,
            limit=min(128, max_chunks - len(chunks)), with_vectors=False,
            with_payload=models.PayloadSelectorInclude(include=EXPORT_FIELDS),
        )
        if len(points) > min(128, max_chunks - len(chunks)):
            raise ExportPayloadMismatch("Lote vectorial invalido.")
        for point in points:
            payload = point.payload or {}
            index = payload.get("chunk_index")
            text = payload.get("text")
            if (any(payload.get(key) != value for key, value in expected.items())
                    or type(index) is not int or not 0 <= index < document.chunk_count or index in indices
                    or not isinstance(text, str)):
                raise ExportPayloadMismatch("Chunk fuera del manifiesto autorizado.")
            text_bytes += len(text.encode("utf-8"))
            if text_bytes > MAX_TEXT_BYTES:
                raise ExportLimitExceeded("El texto excede el limite de exportacion.")
            record = {"chunk_index": index, "text": text}
            for key in ("section", "page_or_sheet", "extraction_incomplete"):
                value = payload.get(key)
                if value is not None:
                    if key in {"section", "page_or_sheet"}:
                        valid = isinstance(value, str) and len(value) <= 2048
                    else:
                        valid = type(value) is bool
                    if not valid:
                        raise ExportPayloadMismatch("Metadatos de chunk invalidos.")
                    record[key] = value
            chunks.append(record)
            indices.add(index)
        if following is None or len(chunks) == max_chunks:
            break
        if not points or str(following) in offsets:
            raise ExportPayloadMismatch("Paginacion vectorial invalida.")
        offsets.add(str(following))
        offset = following
    # No disfrazar un indice incompleto de documento completo.
    complete = following is None and len(chunks) == document.chunk_count
    return {
        "format_version": 1, "purpose": "private_chunk_review", "document": asdict(document),
        "complete": complete, "truncated": following is not None,
        "exported_chunks": len(chunks), "chunks": sorted(chunks, key=lambda row: row["chunk_index"]),
    }


def _windows_private_file(path: Path) -> None:
    """Protege el archivo aun vacio: chmod(0600) no protege una DACL de Windows."""
    powershell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # pwsh7 -> Python -> powershell5 hereda modulos de otra edicion. Dejar que
    # powershell.exe construya sus rutas nativas, sin modificar el entorno padre.
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    environment["MATRIX_REVIEW_FILE"] = str(path)
    security_type = "DirectorySecurity" if path.is_dir() else "FileSecurity"
    inheritance = "'ContainerInherit,ObjectInherit','None'," if path.is_dir() else ""
    script = (
        "$ErrorActionPreference='Stop';"
        "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;"
        f"$acl=New-Object System.Security.AccessControl.{security_type};"
        "$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false);"
        "$rule=[System.Security.AccessControl.FileSystemAccessRule]::new"
        f"($sid,'FullControl',{inheritance}'Allow');$acl.AddAccessRule($rule);"
        "Set-Acl -LiteralPath $env:MATRIX_REVIEW_FILE -AclObject $acl;"
        "$check=Get-Acl -LiteralPath $env:MATRIX_REVIEW_FILE;"
        "if(-not $check.AreAccessRulesProtected){throw 'private ACL required'};"
        "$rules=$check.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]);"
        "if($rules.Count -ne 1){throw 'unexpected ACL count'};"
        "foreach($r in $rules){if($r.IdentityReference -ne $sid -or $r.AccessControlType -ne 'Allow' "
        "-or $r.FileSystemRights -ne 'FullControl'){throw 'unexpected ACL principal'}}"
    )
    subprocess.run(  # noqa: S603 -- ejecutable del sistema y script constante, nunca contenido documental.
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment, check=True, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@contextmanager
def private_output(root: Path, destination: Path):
    """Destino nuevo en var/, excluido del repositorio, sin enlaces ni truncado."""
    root = root.resolve()
    path = destination if destination.is_absolute() else root / destination
    # No resolver antes de verificar: resolve() esconderia enlaces y componentes '..'.
    try:
        relative = path.relative_to(root / "var")
    except ValueError:
        raise ValueError("El destino debe estar dentro de var/.") from None
    if not relative.parts or ".." in relative.parts or path.suffix.lower() != ".json":
        raise ValueError("Se requiere un archivo JSON nuevo dentro de var/.")
    if any(part != part.rstrip(" .") or PureWindowsPath(part).is_reserved()
           or any(character in part for character in ':*?"<>|') for part in relative.parts):
        raise ValueError("El destino contiene un nombre no portable o stream alterno.")
    for directory in (root, *(root / "var" / Path(*relative.parts[:i]) for i in range(len(relative.parts)))):
        if not directory.exists():
            directory.mkdir(mode=0o700)
        if _linked(directory) or not directory.is_dir():
            raise ValueError("El destino contiene un enlace o directorio invalido.")
        if os.name != "nt" and directory.stat().st_mode & 0o022:
            raise ValueError("El directorio de exportacion permite escritura a terceros.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    created = os.fstat(descriptor)
    succeeded = False
    try:
        if os.name == "nt":
            _windows_private_file(path)
        elif stat.S_IMODE(created.st_mode) != 0o600:
            raise ValueError("No fue posible asegurar permisos privados.")
        if _linked(path) or path.stat().st_ino != created.st_ino:
            raise ValueError("El archivo de exportacion cambio durante su apertura.")
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            yield output
            output.flush()
            os.fsync(output.fileno())
        succeeded = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not succeeded and not _linked(path) and path.exists() and path.stat().st_ino == created.st_ino:
            path.unlink()


def export_document(db, *, settings, root: Path, destination: Path, username: str,
                    document_id: str, conversation_id: str | None = None, max_chunks: int = 128,
                    client_factory=open_existing_store) -> dict:
    """Salida publica solo agregada; texto e identidades exclusivamente al archivo privado."""
    document = authorized_document(db, username=username, document_id=document_id, conversation_id=conversation_id)
    collection = settings.rag_collection_corporate if document.scope == "corporate" else settings.rag_collection_private
    # Reservar destino antes de abrir Qdrant: una colision no lee ni trunca datos.
    with private_output(root, destination) as output:
        client = client_factory(settings, collection=collection)
        try:
            payload = read_chunks(client, collection=collection, document=document, max_chunks=max_chunks)
        finally:
            client.close()
        # Nueva transaccion para observar revocacion, desactivacion o republicacion.
        db.rollback()
        db.expire_all()
        if authorized_document(db, username=username, document_id=document_id,
                               conversation_id=conversation_id) != document:
            raise ExportStateChanged("El documento cambio durante la exportacion.")
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return {"exported_chunks": payload["exported_chunks"], "complete": payload["complete"],
            "truncated": payload["truncated"]}
