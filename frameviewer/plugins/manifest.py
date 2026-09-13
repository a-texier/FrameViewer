# -*- coding: utf-8 -*-
"""Normalize the version-two plugin manifest and its input contract.

Each input name is a valid Python identifier. Before rendering, the loader
assigns its selected path to an attribute with the same name. Only paths are
passed; each plugin owns parsing and format validation.

Packages without ``manifest.json`` receive a backward-compatible inferred
contract based on their contents.
"""
import json
import os

MANIFEST_FILE = "manifest.json"


def normalize(data, folder):
    """Validate and complete a raw manifest with safe defaults."""
    data = data or {}
    kind = data.get("kind")
    if kind not in ("code", "graph"):
        kind = "graph" if os.path.isfile(os.path.join(folder, "graph.json")) else "code"
    inputs = []
    seen = set()
    for it in data.get("inputs", []) or []:
        # Accept either a string or a mapping. Legacy type metadata is ignored
        # because the contract transports paths only.
        name = (it if isinstance(it, str) else str(it.get("name", ""))).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        inputs.append({"name": name})
    # Keyboard mapping for overlay-element toggles. It is synchronized from
    # overlay_elements, remains user-editable, and is omitted when empty.
    elem_keys = {}
    raw_keys = data.get("element_keys")
    if isinstance(raw_keys, dict):
        for k, v in raw_keys.items():
            if isinstance(k, str) and k:
                elem_keys[k] = (str(v)[:8] if v else "m")
    out = {
        "name": (data.get("name") or os.path.basename(folder)).strip(),
        "kind": kind,
        "frame_input": (data.get("frame_input") or "Current Frame").strip(),
        "inputs": inputs,
    }
    if elem_keys:
        out["element_keys"] = elem_keys
    return out


def load(folder):
    """Load and normalize a manifest, inferring it when absent."""
    path = os.path.join(folder, MANIFEST_FILE)
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            data = {}
    return normalize(data, folder)


def save(folder, manifest):
    with open(os.path.join(folder, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(normalize(manifest, folder), f, indent=1, ensure_ascii=False)


def validate(manifest):
    """Validate the minimal contract and return ``(ok, message)``."""
    if not manifest.get("name"):
        return False, "manifest sans nom de plugin"
    names = [i.get("name") for i in manifest.get("inputs", [])]
    if any(not n for n in names):
        return False, "une entree n'a pas de nom"
    if len(names) != len(set(names)):
        return False, "noms d'entrees en double"
    return True, ""
