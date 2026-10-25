# Creado por Aldo Garcia.
"""Benchmark aislado: cola/API reales, SQLite temporal y modelo sintético de 20 ms.

No acepta destinos remotos, DSN, corpus o credenciales. NO certifica Ollama/GPU,
MySQL, Windows, calidad RAG ni capacidad productiva. Desde backend:
python -m scripts.benchmark_queue_isolated --output release/audit-2026-10-25/queue-benchmark.json
"""

import argparse
import json
import math
import multiprocessing
import os
import platform
import socket
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.agents import chat_queue, chat_service
from app.api.deps import get_db
from app.api.middleware import matrix_error_handler
from app.api.routes.chat import router
from app.auth.sessions import create_session
from app.common.errors import MatrixError
from app.common.ids import new_id
from app.common.logging import configure_logging
from app.config import Settings, get_settings
from app.database.models import Base, ChatOperation, User
from app.llm.request_control import check_inference_control
from app.security.admission import admission, snapshot
from app.security.rate_limit import get_rate_limiter


@compiles(MEDIUMTEXT, "sqlite")
def compile_mediumtext(_type, _compiler, **_kwargs):
    return "TEXT"


def pct(values, q):
    return round(sorted(values)[max(0, math.ceil(len(values) * q) - 1)] * 1000, 3) if values else None


def run_scenario(concurrency, repetition, *, samples=32, queue_limit=50, overload=False):
    configure_logging("CRITICAL")
    # Sin .env ni ajustes/secretos heredados del equipo.
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None, app_env="test")
    get_settings.cache_clear()
    start_times = {}
    completion_times = {}
    completion_events = {}
    guard = Lock()
    release = Event()
    if not overload:
        release.set()
    with tempfile.TemporaryDirectory(prefix="matrix-queue-synthetic-") as folder:
        engine = create_engine(
            f"sqlite:///{folder}/queue.db",
            connect_args={"check_same_thread": False, "timeout": 5},
            pool_size=8,
            max_overflow=0,
        )

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _record):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")

        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        manager = chat_queue.ChatQueue()
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.add_exception_handler(MatrixError, matrix_error_handler)

        def isolated_db():
            with factory() as db:
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

        app.dependency_overrides[get_db] = isolated_db

        def model(db, *, ctx, conversation, message):
            db.commit()
            with admission("inference", settings.inference_max_inflight):
                while not release.wait(0.01):
                    check_inference_control()
                time.sleep(0.02)
            return SimpleNamespace(
                conversation_id=conversation.id,
                message_id=new_id(),
                answer="Respuesta sintética.",
                public_sources=lambda: [],
                intent="general",
                grounded=False,
                answer_basis="general",
                latency_ms=20,
            )

        original_execution = chat_queue.execute_chat

        def measured_execution(*args, **kwargs):
            state = "failed"
            try:
                response = original_execution(*args, **kwargs)
                state = "completed"
                return response
            finally:
                with guard:
                    operation_id = kwargs["operation_id"]
                    completion_times[operation_id] = (time.monotonic(), state)
                    completion_events.setdefault(operation_id, Event()).set()

        clients = []
        with ExitStack() as stack:
            stack.enter_context(patch("app.config.settings.Settings", return_value=settings))
            stack.enter_context(patch.object(socket.socket, "connect", side_effect=RuntimeError("Network forbidden")))
            for name, value in [
                ("chat_max_inflight", 2),
                ("inference_max_inflight", 2),
                ("chat_queue_max_size", queue_limit),
                ("chat_queue_timeout_seconds", 20),
                ("llm_request_deadline_seconds", 20),
            ]:
                stack.enter_context(patch.object(settings, name, value))
            stack.enter_context(patch.object(chat_queue, "get_sessionmaker", lambda: factory))
            stack.enter_context(patch.object(chat_service, "get_sessionmaker", lambda: factory))
            stack.enter_context(
                patch.object(chat_service, "get_orchestrator", lambda: SimpleNamespace(handle_chat=model))
            )
            stack.enter_context(patch.object(chat_queue, "_queue", manager))
            stack.enter_context(patch.object(chat_queue, "execute_chat", measured_execution))
            for i in range(samples):
                with factory() as db:
                    user = User(
                        id=new_id(),
                        username=f"synthetic-{i}",
                        display_name="Synthetic benchmark",
                        auth_source="local_test",
                        is_active=True,
                        is_synthetic_test=True,
                    )
                    db.add(user)
                    issued = create_session(db, user=user, auth_source="local_test")
                    db.commit()
                client = stack.enter_context(TestClient(app, raise_server_exceptions=False))
                client.cookies.set(settings.session_cookie_name, issued.session_token)
                client.headers["X-CSRF-Token"] = issued.csrf_token
                clients.append(client)
            get_rate_limiter().reset()
            started = time.monotonic()
            results = []
            operation_ids = []

            def send(i):
                began = time.monotonic()
                response = clients[i].post(
                    "/api/chat/submit",
                    json={"message": "Hola sintético", "client_request_id": f"synthetic-request-{repetition}-{i}"},
                )
                latency = time.monotonic() - began
                data = response.json()
                if response.status_code == 202:
                    with guard:
                        start_times[data["operation_id"]] = began
                        operation_ids.append(data["operation_id"])
                    if not overload:
                        # Un usuario espera su turno. El observador no hace polling SQL.
                        with guard:
                            finished = completion_events.setdefault(data["operation_id"], Event())
                        finished.wait(timeout=max(0, started + 25 - time.monotonic()))
                return {
                    "status": response.status_code,
                    "latency": latency,
                    "queued": data.get("status") == "queued",
                    "notice": bool(data.get("notice")),
                }

            try:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = [pool.submit(send, i) for i in range(samples)]
                    for future in futures:
                        results.append(future.result(timeout=30))
                release.set()
                deadline = started + 30
                for operation_id in operation_ids:
                    with guard:
                        finished = completion_events.setdefault(operation_id, Event())
                    finished.wait(timeout=max(0, deadline - time.monotonic()))
                with factory() as db:
                    rows = db.scalars(select(ChatOperation).where(ChatOperation.id.in_(operation_ids))).all()
                    for row in rows:
                        if row.status in ("failed", "cancelled", "expired"):
                            completion_times.setdefault(row.id, (time.monotonic(), row.status))
                completed = [
                    when - start_times[key] for key, (when, state) in completion_times.items() if state == "completed"
                ]
                status_counts = {
                    str(code): sum(x["status"] == code for x in results)
                    for code in sorted({x["status"] for x in results})
                }
                final_states = {
                    state: sum(s == state for _, s in completion_times.values())
                    for _, state in completion_times.values()
                }
                result = {
                    "scenario": "bounded_overload" if overload else "normal",
                    "concurrency": concurrency,
                    "repetition": repetition,
                    "samples": samples,
                    "queue_limit": queue_limit,
                    "http_statuses": status_counts,
                    "terminal_states": final_states,
                    "unfinished": len(operation_ids) - len(completion_times),
                    "queued_acceptances": sum(x["queued"] for x in results),
                    "notices": sum(x["notice"] for x in results),
                    "submit_p50_ms": pct([x["latency"] for x in results], 0.5),
                    "submit_p95_ms": pct([x["latency"] for x in results], 0.95),
                    "submit_p99_ms": pct([x["latency"] for x in results], 0.99),
                    "complete_p50_ms": pct(completed, 0.5),
                    "complete_p95_ms": pct(completed, 0.95),
                    "complete_p99_ms": pct(completed, 0.99),
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            finally:
                release.set()
                manager.stop()
                get_rate_limiter().reset()
            result["remaining_admission_slots"] = snapshot()
            result["passed"] = (
                not snapshot()
                and result["unfinished"] == 0
                and result["http_statuses"] == ({"202": 4, "503": 47} if overload else {"202": samples})
                and result["terminal_states"] == {"completed": 4 if overload else samples}
            )
        engine.dispose()
    return result


def child_scenario(connection, concurrency, repetition, overload):
    try:
        connection.send(
            run_scenario(
                concurrency,
                repetition,
                samples=max(32, concurrency),
                queue_limit=2 if overload else 50,
                overload=overload,
            )
        )
    except Exception as error:
        connection.send({"passed": False, "error_type": type(error).__name__})
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    all_results = []
    context = multiprocessing.get_context("spawn")
    for conc, overload in [(1, False), (4, False), (8, False), (51, False), (51, True)]:
        for repeat in (1, 2, 3):
            # Aislar cada escenario y abortarlo a los 30 s, incluyendo setup/cleanup.
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=child_scenario, args=(child, conc, repeat, overload))
            process.start()
            child.close()
            process.join(timeout=30)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=3)
                result = {"passed": False, "error_type": "ScenarioDeadlineExceeded"}
            else:
                result = parent.recv() if parent.poll() else {"passed": False, "error_type": "NoScenarioResult"}
            parent.close()
            result.update(concurrency=conc, repetition=repeat)
            all_results.append(result)
            print(json.dumps(result), flush=True)
            if not result["passed"]:
                break
        if not all_results[-1]["passed"]:
            break
    passed = len(all_results) == 15 and all(row["passed"] for row in all_results)
    output = {
        "benchmark": "Actual Matrix queue/API; synthetic orchestrator; SQLite WAL",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "inference_slots": 2,
        "mock_inference_ms": 20,
        "scenario_hard_deadline_s": 30,
        "http_transport": "ASGI in memory; real cookies, CSRF, RBAC, SQL queue and publication",
        "completion_clock": "after execute_chat commits; excludes browser polling delay",
        "no_external_network": True,
        "warmup": "No model warmup; fresh synthetic scenario includes first-worker start",
        "gpu_model_latency_measured": False,
        "capacity_certified": False,
        "limitations": [
            "Not MySQL/Windows performance",
            "No Ollama, GPU, vector search or real corpus",
            "No browser polling overhead",
            "Shared CI hardware; no production SLO claim",
        ],
        "passed": passed,
        "results": all_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
