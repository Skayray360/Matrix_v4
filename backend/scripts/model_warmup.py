# Creado por Aldo Garcia.
"""Plan de precalentamiento local; solo --run ejecuta sondas sinteticas.

Usa los modelos configurados y sus pins, sin descargar pesos ni abrir el corpus.
Puede ocupar RAM/VRAM y competir con otras aplicaciones: ejecutar en un periodo
sin carga. Conserva keep_alive; no garantiza residencia simultanea ni futura.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from typing import Any

from app.common.errors import ConfigurationError, EmbeddingDimensionMismatchError
from app.common.inference_errors import InferenceFailureError
from app.config import Settings, get_settings
from app.llm.provider import ModelClient, inference_deadline

CHAT_TOKEN_LIMIT = 32
CHAT_PROBE = "Prueba sintetica de disponibilidad. Responde solamente: listo."
EMBEDDING_PROBE = "Prueba sintetica de disponibilidad del modelo de embeddings."


def _targets(settings: Settings, target: str, profile: str) -> list[tuple[str, str, str]]:
    targets = []
    # Terminar con generacion favorece la siguiente pregunta si solo cabe un
    # modelo. Esto no acredita que el de embeddings siga cargado al terminar.
    if target in {"embedding", "both"}:
        targets.append(("embedding", settings.ollama_embedding_model, settings.llm_embedding_digest))
    if target in {"generation", "both"}:
        targets.append(("generation",
                        settings.ollama_deep_model if profile == "deep" else settings.ollama_fast_model,
                        settings.llm_deep_digest if profile == "deep" else settings.llm_fast_digest))
    return targets


def run_warmup(
    *, execute: bool = False, target: str = "both", profile: str = "fast", timeout_seconds: float = 60,
) -> dict[str, Any]:
    """Sin execute no crea clientes; con execute hay un solo POST por rol."""
    if (target not in {"generation", "embedding", "both"} or profile not in {"fast", "deep"}
            or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 300):
        raise ValueError("Invalid warmup bounds")
    report: dict[str, Any] = {
        "schema_version": 1, "observed_at_utc": datetime.now(UTC).isoformat(),
        "scope": "synthetic_local_model_warmup", "status": "planned", "executed": execute,
        "target": target, "profile": profile, "timeout_seconds": timeout_seconds,
        "max_generation_tokens": CHAT_TOKEN_LIMIT, "probes": [], "errors": [],
        "resident_models_verified": False, "corporate_rag_validated": False,
        "limitations": [
            "Only --run sends synthetic probes; no weights are downloaded and no corpus is opened.",
            "Loading consumes RAM/VRAM and can evict other models or contend with other applications.",
            "The CLI admission counter is process-local; schedule this outside application traffic.",
            "Configured keep_alive and context size are preserved; simultaneous residency is not guaranteed.",
            "A deadline stops the client wait; the runtime may still be completing a submitted request.",
            "No retries or automatic startup hook; no prompts, answers, vectors or endpoint URLs are retained.",
        ],
    }
    client = None
    started = time.perf_counter()
    try:
        settings = get_settings()
        targets = _targets(settings, target, profile)
        report["roles"] = [role for role, _, _ in targets]
        report["pins_configured"] = all(bool(pin) for _, _, pin in targets)
        if not execute:
            return report
        report["status"] = "blocked"
        providers = (settings.llm_provider, settings.llm_deep_provider, settings.llm_embedding_provider)
        if not settings.llm_local_only or any(provider != "ollama" for provider in providers):
            report["errors"].append("local_ollama_required")
            return report
        if not report["pins_configured"]:
            report["errors"].append("model_pins_required")
            return report
        # Una copia acota solo esta sonda; no cambia los defaults de Matrix.
        client = ModelClient(settings=settings.model_copy(update={"llm_completion_retries": 0}),
                             timeout=min(timeout_seconds, settings.ollama_timeout_seconds))
        with inference_deadline(absolute_deadline=time.monotonic() + timeout_seconds):
            # ModelClient valida allowlist/local-only y los digests del inventario
            # antes de generar, y vuelve a verificar el pin antes de cada POST.
            inventory = client.list_models()
            if any(model not in inventory.names for _, model, _ in targets):
                report["errors"].append("configured_model_unavailable")
                return report
            for role, model, _ in targets:
                probe: dict[str, Any] = {"role": role, "status": "running"}
                report["probes"].append(probe)
                probe_started = time.perf_counter()
                if role == "embedding":
                    vectors = client.embed([EMBEDDING_PROBE], model=model)
                    probe["dimension"] = len(vectors[0])
                else:
                    context = (settings.ollama_deep_num_ctx if profile == "deep"
                               else settings.ollama_fast_num_ctx)
                    result = client.chat(
                        model=model, execution_profile=profile, num_ctx=context,
                        max_tokens=CHAT_TOKEN_LIMIT, temperature=0, think=False,
                        messages=[{"role": "user", "content": CHAT_PROBE}],
                    )
                    probe.update(result.performance_metrics())
                probe.update(status="completed", elapsed_ms=round((time.perf_counter() - probe_started) * 1000, 3))
        report["status"] = "completed"
    except ConfigurationError:
        report["errors"].append("configuration_or_model_pin_rejected")
    except EmbeddingDimensionMismatchError:
        report["errors"].append("invalid_embedding")
    except InferenceFailureError as exc:
        report["errors"].append(exc.failure_kind.value)
    except Exception:
        report["errors"].append("adapter_or_configuration_failed")
    finally:
        if client is not None:
            client.close()
        if report["errors"]:
            report["status"] = "failed" if report["probes"] else "blocked"
            for probe in report["probes"]:
                if probe["status"] == "running":
                    probe.update(status="failed", failure_kind=report["errors"][-1])
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Autoriza carga e inferencia sintetica acotada.")
    parser.add_argument("--target", choices=("generation", "embedding", "both"), default="both")
    parser.add_argument("--profile", choices=("fast", "deep"), default="fast")
    parser.add_argument("--timeout-seconds", type=float, default=60,
                        help="Plazo total 1..300 s; no aumenta los existentes.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_seconds) or not 1 <= args.timeout_seconds <= 300:
        parser.error("--timeout-seconds debe ser finito y estar entre 1 y 300")
    report = run_warmup(execute=args.run, target=args.target, profile=args.profile,
                        timeout_seconds=args.timeout_seconds)
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
