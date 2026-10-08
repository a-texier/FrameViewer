"""Discovery and lazy invocation of optional file-format capabilities."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


_CAPABILITY_DIR = Path(__file__).resolve().parent
_PACKAGE = __name__.rsplit(".", 1)[0]  # "frameviewer.core"
_SELF_MODULE = Path(__file__).stem


def _candidate_module_names() -> list[str]:
    """Sous-modules de frameviewer.core susceptibles de porter une capacite.

    On combine deux sources pour marcher aussi bien depuis les sources (fichiers
    .py sur disque) que dans un exe PyInstaller gele : dans un onefile il n'y a
    AUCUN .py sur disque (le code vit dans l'archive PYZ), donc un simple
    glob("*.py") renvoie vide et les capacites deviennent invisibles.
    iter_modules liste les sous-modules reellement embarques ; le glob sert de
    repli quand on tourne depuis les sources."""
    names: set[str] = set()
    try:
        package = importlib.import_module(_PACKAGE)
        for info in pkgutil.iter_modules(getattr(package, "__path__", [])):
            names.add(info.name)
    except Exception:
        pass
    try:
        for path in _CAPABILITY_DIR.glob("*.py"):
            if path.stem != "__init__":
                names.add(path.stem)
    except OSError:
        pass
    return sorted(names)


def available_features(kind: str | None = None) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for module_name in _candidate_module_names():
        if module_name == _SELF_MODULE:
            continue
        try:
            module = importlib.import_module(f"{_PACKAGE}.{module_name}")
        except (Exception, SystemExit):
            # Un module non lie a une capacite peut echouer ou meme appeler
            # sys.exit a l'import (ex. utilitaire prevu comme script) : on ne
            # laisse jamais ca tuer la decouverte.
            continue
        capability = getattr(module, "FEATURE_CAPABILITY", None)
        if not (isinstance(capability, dict) and isinstance(capability.get("id"), str)):
            continue
        if kind and capability.get("kind") != kind:
            continue
        features.append({**capability, "module": module_name})
    return features


def _load(capability: dict[str, Any]) -> ModuleType:
    return importlib.import_module(f"{_PACKAGE}.{capability['module']}")


def feature_for_path(path: str, kind: str | None = None) -> dict[str, Any] | None:
    suffix = Path(path).suffix.lower()
    for capability in available_features(kind):
        extensions = {str(ext).lower() for ext in capability.get("extensions", [])}
        if suffix in extensions:
            return capability
    return None


def supports_path(path: str, kind: str | None = None) -> bool:
    return feature_for_path(path, kind) is not None


def extensions_for_kind(kind: str) -> tuple[str, ...]:
    values: list[str] = []
    for capability in available_features(kind):
        values.extend(str(ext).lower() for ext in capability.get("extensions", []))
    return tuple(dict.fromkeys(values))


def operation_for_kind(kind: str, operation: str) -> Callable[..., Any] | None:
    for capability in available_features(kind):
        handler = getattr(_load(capability), "FEATURE_OPERATIONS", {}).get(operation)
        if callable(handler):
            return handler
    return None


def operation_for_path(
    path: str,
    operation: str,
    *,
    kind: str | None = None,
) -> Callable[..., Any] | None:
    capability = feature_for_path(path, kind)
    if capability is None:
        return None
    handler = getattr(_load(capability), "FEATURE_OPERATIONS", {}).get(operation)
    return handler if callable(handler) else None


def invoke_for_path(
    path: str,
    operation: str,
    *args: Any,
    kind: str | None = None,
    **kwargs: Any,
) -> Any:
    handler = operation_for_path(path, operation, kind=kind)
    if handler is None:
        raise ValueError(f"Aucune capacité disponible pour {Path(path).suffix or path}")
    return handler(*args, **kwargs)
