#!/usr/bin/env python3
"""Crea .env demo privado una sola vez; nunca imprime ni reemplaza secretos."""
import argparse
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys

import operations as op
from first_deploy_demo import REMOTES, DEMO_DB, DEMO_STORAGE, safe_identifier

ROOT = Path(__file__).resolve().parents[1]


def verify_repo(root):
    def git(*args):
        return subprocess.check_output(["git", "-c", "safe.directory=" + str(root), "-C", str(root), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    op.require(git("rev-parse", "--show-toplevel") == str(root), "Raíz Git incorrecta")
    op.require(git("remote", "get-url", "origin") in REMOTES, "No es el repo deploy oficial")


def create_env(root=ROOT):
    op.require(platform.system() == "Linux", "Crear el entorno solo en Debian/Linux")
    safe_identifier(str(root), "ruta del repo")
    verify_repo(root)
    path = root / ".env"
    op.require(not path.exists() and not path.is_symlink(), ".env ya existe; no se reemplazará")
    values = {}
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    values.update(INSTALL_MODE="demo", POSTGRES_DB="correspondencia_demo",
                  POSTGRES_USER="correspondencia_app", POSTGRES_VOLUME=DEMO_DB,
                  STORAGE_VOLUME=DEMO_STORAGE)
    for key in ("POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET"):
        values[key] = secrets.token_hex(32)
    op.validate_env(values, root)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("# PRIVADO. Generado para Correspondencia DEMO. No versionar.\n")
        for key, value in values.items():
            f.write(key + "=" + value + "\n")
    print("Creado .env privado (600), modo demo, con secretos aleatorios. No se creó ningún usuario.")
    return path


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    create_env()


if __name__ == "__main__":
    try:
        main()
    except (op.Refusal, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("DETENIDO: " + str(exc), file=sys.stderr)
        sys.exit(1)
