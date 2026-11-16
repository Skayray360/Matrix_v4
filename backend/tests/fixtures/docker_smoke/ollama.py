# Creado por Aldo Garcia.
"""Doble HTTP exclusivo de CI: comprueba despliegue, no calidad ni pesos reales."""

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = tuple(dict.fromkeys(os.environ[key] for key in (
    "OLLAMA_FAST_MODEL", "OLLAMA_DEEP_MODEL", "OLLAMA_EMBEDDING_MODEL",
)))
DIMENSION = int(os.environ["OLLAMA_EMBEDDING_DIMENSION"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        # No registrar cuerpos, cabeceras ni credenciales de solicitudes.
        return

    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - contrato de BaseHTTPRequestHandler.
        if self.path != "/api/tags":
            self.respond(404, {"error": "fixture endpoint unavailable"})
            return
        self.respond(200, {"models": [{
            "name": name,
            "digest": hashlib.sha256(("matrix-ci-synthetic:" + name).encode()).hexdigest(),
        } for name in MODELS]})

    def do_POST(self):  # noqa: N802 - contrato de BaseHTTPRequestHandler.
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1_048_576:
            self.respond(413, {"error": "fixture body limit"})
            return
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            self.respond(400, {"error": "fixture invalid json"})
            return
        if not isinstance(data, dict) or data.get("model") not in MODELS:
            self.respond(404, {"error": "fixture model unavailable"})
        elif self.path == "/api/show":
            self.respond(200, {"capabilities": ["completion", "thinking"],
                               "model_info": {"synthetic.context_length": 131072}})
        elif self.path == "/api/embed" and data["model"] == os.environ["OLLAMA_EMBEDDING_MODEL"]:
            texts = data.get("input", [])
            if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
                self.respond(400, {"error": "fixture invalid embedding input"})
                return
            self.respond(200, {"embeddings": [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]})
        else:
            # El smoke no hace chat: una llamada inesperada falla visiblemente.
            self.respond(404, {"error": "fixture endpoint unavailable"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 11434), Handler).serve_forever()  # noqa: S104 - red interna efimera CI.
