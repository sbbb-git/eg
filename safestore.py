"""safestore.py — écriture atomique de fichiers JSON.

Toutes les écritures de data.json passent par ici pour éviter les
corruptions en cas d'interruption (CI tuée, OOM, etc.). On écrit dans un
fichier temporaire dans le même répertoire puis on `os.replace` (atomique
sur le même volume POSIX/NTFS).
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile
from typing import Any


def write_json(path: str, data: Any, *, indent: int = 2, sort_keys: bool = False) -> str:
    """Écrit `data` en JSON de manière atomique. Retourne le chemin écrit."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def write_json_gz(path: str, data: Any, *, indent: int = 0) -> str:
    """Écrit `data` en JSON gzippé (archives > 30 jours)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory, suffix=".json.gz")
    os.close(fd)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent or None)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def read_json(path: str, default: Any = None) -> Any:
    """Lit un JSON (ou .json.gz). Retourne `default` si absent/illisible."""
    if not os.path.exists(path):
        return default
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


if __name__ == "__main__":
    # smoke test
    p = write_json("/tmp/_safestore_test.json", {"ok": True, "accents": "éàü"})
    assert read_json(p)["ok"] is True
    print("safestore OK ->", p)
