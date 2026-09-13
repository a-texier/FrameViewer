"""Discovery and lazy invocation of optional file-format capabilities."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


_CAPABILITY_DIR = Path(__file__).resolve().parent


def _read_capability(path: Path) -> dict[str, Any] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "FEATURE_CAPABILITY"
            for target in targets
        ):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return None
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            return value
    return None


def available_features(kind: str | None = None) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for path in sorted(_CAPABILITY_DIR.glob("*.py")):
        capability = _read_capability(path)
        if capability is None or (kind and capability.get("kind") != kind):
            continue
        features.append({**capability, "module": path.stem})
    return features


def _load(capability: dict[str, Any]) -> ModuleType:
    return importlib.import_module(f"frameviewer.core.{capability['module']}")


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
