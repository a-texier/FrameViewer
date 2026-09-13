# -*- coding: utf-8 -*-
"""Manifest d'un plugin (modele v2) : decrit le contrat d'entree d'un plugin
-- son type (code/graphe), l'entree qui porte la frame courante, et la liste
des entrees fichiers.

Chaque entree est un NOM DE VARIABLE (identifiant Python, ex. "csv_plots").
Le viewer fournit au plugin, avant chaque rendu, le chemin du fichier depose
sous cette entree via un attribut du meme nom : self.<nom> = "<chemin>" (ou
None). On ne transmet que le CHEMIN : le plugin ouvre et reconnait le format
lui-meme (voir loader._bind_inputs et docs/plugins.md). Aucun type/format
n'est donc declare ici.

Un plugin peut ne PAS avoir de manifest.json : on infere alors un contrat par
defaut (graphe si graph.json present, sinon code ; aucune entree fichier) --
les plugins existants continuent de fonctionner sans modification.
"""
import json
import os

MANIFEST_FILE = "manifest.json"


def normalize(data, folder):
    """Complete/valide un dict manifest brut avec des defauts surs."""
    data = data or {}
    kind = data.get("kind")
    if kind not in ("code", "graph"):
        kind = "graph" if os.path.isfile(os.path.join(folder, "graph.json")) else "code"
    inputs = []
    seen = set()
    for it in data.get("inputs", []) or []:
        # accepte l'entree sous forme de chaine ("csv_plots") ou de dict
        # ({"name": "csv_plots"}) ; le champ "type" des anciens manifests est
        # ignore (on ne transmet que le chemin, cf. docstring du module).
        name = (it if isinstance(it, str) else str(it.get("name", ""))).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        inputs.append({"name": name})
    # mapping touche clavier -> element On/Off (voir loader._sync_element_keymap) :
    # {cle_element: caractere}. Auto-genere/synchronise depuis overlay_elements,
    # editable par l'utilisateur. Omis si vide (plugins sans elements).
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
    """Manifest normalise du plugin (infere si manifest.json absent)."""
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
    """-> (ok: bool, message: str). Contrat minimal : un nom, des entrees aux
    noms uniques et non vides."""
    if not manifest.get("name"):
        return False, "manifest sans nom de plugin"
    names = [i.get("name") for i in manifest.get("inputs", [])]
    if any(not n for n in names):
        return False, "une entree n'a pas de nom"
    if len(names) != len(set(names)):
        return False, "noms d'entrees en double"
    return True, ""
