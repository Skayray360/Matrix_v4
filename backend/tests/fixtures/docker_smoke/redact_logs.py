# Creado por Aldo Garcia.
"""Logs acotados de CI; oculta secretos efimeros y campos de autenticacion."""

import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[4]


def redact(text: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]").replace(quote(secret, safe=""), "[REDACTED]")
    text = re.sub(r'(?i)("(?:password|token|csrf_token|session_token|authorization|cookie|set-cookie)"\s*:\s*)"[^"\n]*"',
                  r'\1"[REDACTED]"', text)
    text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[REDACTED]', text)
    # Acota tambien lineas aisladas, por si un tercero registra un cuerpo grande.
    return "\n".join(line[:2000] for line in text.splitlines()[-400:])


def main():
    secrets = set()
    env_file = ROOT / "backend/config/docker.env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and re.search(r"SECRET|PASSWORD|TOKEN|API_KEY|DATABASE_URL", key):
                secrets.add(value)
    directory = ROOT / "backend/config/secrets/docker"
    if directory.is_dir():
        for path in directory.glob("*.txt"):
            if path.is_file() and not path.is_symlink():
                secrets.add(path.read_text().strip())
    print(redact(sys.stdin.read(2_097_152), secrets))


if __name__ == "__main__":
    main()
