# -*- coding: utf-8 -*-
"""Block-graph model for building shape plugins without writing Python.

The deliberately focused graph reads CSV values, combines or compares them,
and emits overlay shapes for the current frame. It is interpreted directly,
serialized as JSON, and has no Qt dependency.
"""


class NodeType:
    """Describe ports, editable parameters, and a pure evaluation function."""

    def __init__(self, key, label, category, inputs, outputs, evaluate,
                params=None, variadic_input=None):
        self.key = key
        self.label = label
        self.category = category
        self.inputs = list(inputs)          # [(name, type_hint)]
        self.outputs = list(outputs)        # [(name, type_hint)]
        self.evaluate = evaluate
        self.params = list(params or [])    # [(name, type_hint, default)]
        # Optional input port that accepts multiple incoming connections.
        self.variadic_input = variadic_input


REGISTRY = {}


def register(node_type):
    REGISTRY[node_type.key] = node_type
    return node_type


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_rgb(text):
    try:
        parts = [int(float(x)) for x in str(text).split(",")]
        if len(parts) == 3:
            return tuple(max(0, min(255, p)) for p in parts)
    except ValueError:
        pass
    return (255, 190, 0)


def vue_to_index(v):
    """Convert an active/view-N selector to a zero-based multi-view index."""
    if not v or v == "active":
        return None
    if v.startswith("vue"):
        try:
            return max(0, int(v[3:]) - 1)
        except ValueError:
            return None
    return None


register(NodeType(
    "csv_column", "Colonne CSV", "Source",
    inputs=[], outputs=[("valeur", "any")],
    params=[("colonne", "column", ""), ("type", "choice:nombre,texte", "nombre")],
    evaluate=lambda p, i, row: {
        "valeur": _to_float(row.get(p.get("colonne"))) if p.get("type", "nombre") == "nombre"
        else row.get(p.get("colonne"), "")},
))

register(NodeType(
    "constant", "Constante", "Source",
    inputs=[], outputs=[("valeur", "any")],
    params=[("valeur", "str", "0")],
    evaluate=lambda p, i, row: {"valeur": p.get("valeur", "")},
))

register(NodeType(
    # The reserved ``__frame__`` value lets graphs react to the current frame
    # without requiring a dedicated CSV column.
    "frame_index", "Numero de frame", "Source",
    inputs=[], outputs=[("valeur", "number")],
    evaluate=lambda p, i, row: {"valeur": _to_float(row.get("__frame__"), 0.0)},
))

register(NodeType(
    "compare", "Comparer", "Logique",
    inputs=[("a", "any"), ("b", "any")], outputs=[("vrai", "bool")],
    params=[("operateur", "choice:>,<,>=,<=,==", ">")],
    evaluate=lambda p, i, row: {"vrai": {
        ">": lambda a, b: _to_float(a) > _to_float(b),
        "<": lambda a, b: _to_float(a) < _to_float(b),
        ">=": lambda a, b: _to_float(a) >= _to_float(b),
        "<=": lambda a, b: _to_float(a) <= _to_float(b),
        "==": lambda a, b: _to_float(a) == _to_float(b),
    }.get(p.get("operateur", ">"), lambda a, b: True)(i.get("a"), i.get("b"))},
))

register(NodeType(
    "midpoint", "Milieu", "Calcul",
    inputs=[("a", "number"), ("b", "number")], outputs=[("milieu", "number")],
    evaluate=lambda p, i, row: {"milieu": (_to_float(i.get("a")) + _to_float(i.get("b"))) / 2.0},
))

register(NodeType(
    "math", "Operation (a op b)", "Calcul",
    inputs=[("a", "number"), ("b", "number")], outputs=[("resultat", "number")],
    params=[("operateur", "choice:+,-,*,/", "+")],
    evaluate=lambda p, i, row: {"resultat": {
        "+": lambda a, b: a + b,
        "-": lambda a, b: a - b,
        "*": lambda a, b: a * b,
        "/": lambda a, b: a / b if b else 0.0,
    }.get(p.get("operateur", "+"), lambda a, b: a)(
        _to_float(i.get("a")), _to_float(i.get("b")))},
))

register(NodeType(
    "color_rgb", "Couleur (R,G,B)", "Apparence",
    inputs=[("r", "number"), ("g", "number"), ("b", "number")],
    outputs=[("couleur", "color")],
    evaluate=lambda p, i, row: {"couleur": (
        int(_to_float(i.get("r"))), int(_to_float(i.get("g"))), int(_to_float(i.get("b"))))},
))

register(NodeType(
    "parse_color_csv", "Analyser couleur (\"R,G,B\")", "Apparence",
    inputs=[("texte", "any")], outputs=[("couleur", "color")],
    evaluate=lambda p, i, row: {"couleur": _parse_rgb(i.get("texte"))},
))

register(NodeType(
    "color_const", "Couleur fixe", "Apparence",
    inputs=[], outputs=[("couleur", "color")],
    params=[("rgb", "str", "255,190,0")],
    evaluate=lambda p, i, row: {"couleur": _parse_rgb(p.get("rgb"))},
))

register(NodeType(
    "rect_shape", "Forme : Rectangle", "Forme",
    inputs=[("x1", "number"), ("y1", "number"), ("x2", "number"), ("y2", "number"),
           ("couleur", "color"), ("epaisseur", "number"), ("condition", "bool")],
    outputs=[("forme", "shape")],
    evaluate=lambda p, i, row: {"forme": None} if i.get("condition") is False else {"forme": {
        "type": "ligne_brisee_fermee",
        "points": [(_to_float(i.get("x1")), _to_float(i.get("y1"))),
                  (_to_float(i.get("x2")), _to_float(i.get("y1"))),
                  (_to_float(i.get("x2")), _to_float(i.get("y2"))),
                  (_to_float(i.get("x1")), _to_float(i.get("y2")))],
        "color": i.get("couleur") or (255, 190, 0),
        "thickness": int(_to_float(i.get("epaisseur"), 2)) or 2,
    }},
))

register(NodeType(
    "ellipse_shape", "Forme : Ellipse", "Forme",
    inputs=[("cx", "number"), ("cy", "number"), ("rayon_x", "number"), ("rayon_y", "number"),
           ("couleur", "color"), ("epaisseur", "number"), ("condition", "bool")],
    outputs=[("forme", "shape")],
    evaluate=lambda p, i, row: {"forme": None} if i.get("condition") is False else {"forme": {
        "type": "ellipse",
        "points": [(_to_float(i.get("cx")), _to_float(i.get("cy")))],
        "a": _to_float(i.get("rayon_x"), 5) or 5,
        "b": _to_float(i.get("rayon_y"), 5) or 5,
        "color": i.get("couleur") or (255, 190, 0),
        "thickness": int(_to_float(i.get("epaisseur"), 1)) or 1,
    }},
))

register(NodeType(
    "point_shape", "Forme : Point / Croix", "Forme",
    inputs=[("x", "number"), ("y", "number"),
           ("couleur", "color"), ("epaisseur", "number"), ("condition", "bool")],
    outputs=[("forme", "shape")],
    params=[("style", "choice:points,croix", "points")],
    evaluate=lambda p, i, row: {"forme": None} if i.get("condition") is False else {"forme": {
        "type": p.get("style", "points"),
        "points": [(_to_float(i.get("x")), _to_float(i.get("y")))],
        "color": i.get("couleur") or (255, 190, 0),
        "thickness": int(_to_float(i.get("epaisseur"), 1)) or 1,
    }},
))

register(NodeType(
    # Segment connecting two points inside one view.
    "line_shape", "Forme : Segment (2 points)", "Forme",
    inputs=[("x1", "number"), ("y1", "number"), ("x2", "number"), ("y2", "number"),
           ("couleur", "color"), ("epaisseur", "number"), ("condition", "bool")],
    outputs=[("forme", "shape")],
    evaluate=lambda p, i, row: {"forme": None} if i.get("condition") is False else {"forme": {
        "type": "ligne_brisee",
        "points": [(_to_float(i.get("x1")), _to_float(i.get("y1"))),
                  (_to_float(i.get("x2")), _to_float(i.get("y2")))],
        "color": i.get("couleur") or (255, 190, 0),
        "thickness": int(_to_float(i.get("epaisseur"), 1)) or 1,
    }},
))

register(NodeType(
    # Cross-view segment rendered by the multi-view overlay layer. ``va`` and
    # ``vb`` are zero-based view indices.
    "interview_segment", "Segment inter-vues", "Inter-vues",
    inputs=[("xa", "number"), ("ya", "number"), ("xb", "number"), ("yb", "number"),
           ("couleur", "color"), ("epaisseur", "number"), ("condition", "bool")],
    outputs=[("forme", "shape")],
    params=[("vue_a", "choice:vue1,vue2,vue3,vue4", "vue1"),
           ("vue_b", "choice:vue1,vue2,vue3,vue4", "vue2")],
    evaluate=lambda p, i, row: {"forme": None} if i.get("condition") is False else {"forme": {
        "type": "segment_inter_vues",
        "va": vue_to_index(p.get("vue_a", "vue1")) or 0,
        "pa": (_to_float(i.get("xa")), _to_float(i.get("ya"))),
        "vb": vue_to_index(p.get("vue_b", "vue2")) or 0,
        "pb": (_to_float(i.get("xb")), _to_float(i.get("yb"))),
        "color": i.get("couleur") or (0, 255, 0),
        "thickness": int(_to_float(i.get("epaisseur"), 1)) or 1,
    }},
))

register(NodeType(
    "text_label", "Texte (sur une forme)", "Forme",
    inputs=[("forme", "shape"), ("texte", "any")],
    outputs=[("forme", "shape")],
    params=[("decalage_x", "number", "6"), ("decalage_y", "number", "-6"),
           ("taille", "number", "12")],
    evaluate=lambda p, i, row: {"forme": (
        {**i["forme"], "text": {"label": str(i.get("texte", "")),
                                "x": _to_float(p.get("decalage_x"), 6),
                                "y": _to_float(p.get("decalage_y"), -6),
                                "size": _to_float(p.get("taille"), 12)}}
        if i.get("forme") else None)},
))

register(NodeType(
    "output_overlays", "Sortie : Calques", "Sortie",
    inputs=[("forme", "shape")], outputs=[],
    params=[("vue", "choice:active,vue1,vue2,vue3,vue4", "active")],
    evaluate=lambda p, i, row: {},
    variadic_input="forme",
))


class Graph:
    """`nodes` : {id: {"type": key, "params": {...}, "pos": [x,y]}}.
    `edges` : [(src_id, src_port, dst_id, dst_port), ...]."""

    def __init__(self, nodes=None, edges=None, csv_path=None, frame_column=None):
        self.nodes = dict(nodes or {})
        self.edges = list(edges or [])
        self.csv_path = csv_path
        # CSV column used to group rows before evaluating one row at a time.
        self.frame_column = frame_column

    def to_dict(self):
        return {"nodes": self.nodes, "edges": self.edges,
                "csv_path": self.csv_path, "frame_column": self.frame_column}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("nodes"), d.get("edges"), d.get("csv_path"), d.get("frame_column"))

    def _incoming(self, node_id):
        """Return incoming source ports grouped by destination port."""
        out = {}
        for src_id, src_port, dst_id, dst_port in self.edges:
            if dst_id == node_id:
                out.setdefault(dst_port, []).append((src_id, src_port))
        return out

    def _topo_order(self):
        """Return dependency-first evaluation order and detect cycles."""
        deps = {nid: set() for nid in self.nodes}
        for src_id, _sp, dst_id, _dp in self.edges:
            if dst_id in deps and src_id in self.nodes:
                deps[dst_id].add(src_id)
        order = []
        visited, visiting = set(), set()

        def visit(nid):
            if nid in visited:
                return
            if nid in visiting:
                raise ValueError(f"cycle detecte impliquant le bloc {nid}")
            visiting.add(nid)
            for d in deps.get(nid, ()):
                visit(d)
            visiting.discard(nid)
            visited.add(nid)
            order.append(nid)

        for nid in self.nodes:
            visit(nid)
        return order

    def evaluate(self, row):
        """Evaluate the complete graph for one CSV row and return its shapes."""
        order = self._topo_order()
        results = {}
        shapes = []
        for nid in order:
            node = self.nodes[nid]
            nt = REGISTRY.get(node["type"])
            if nt is None:
                continue
            incoming = self._incoming(nid)
            inputs = {}
            for port_name, _t in nt.inputs:
                srcs = incoming.get(port_name, [])
                if nt.variadic_input == port_name:
                    continue   # Collected separately below.
                if srcs:
                    src_id, src_port = srcs[0]
                    inputs[port_name] = results.get(src_id, {}).get(src_port)
            out = nt.evaluate(node.get("params", {}), inputs, row)
            results[nid] = out
            if nt.variadic_input:
                # Output nodes stamp ordinary shapes with a target view. A
                # cross-view shape already carries both indices and is kept.
                vidx = vue_to_index(node.get("params", {}).get("vue", "active"))
                for src_id, src_port in incoming.get(nt.variadic_input, []):
                    val = results.get(src_id, {}).get(src_port)
                    if not val:
                        continue
                    if vidx is not None and val.get("type") != "segment_inter_vues":
                        val = {**val, "view": vidx}
                    shapes.append(val)
        return shapes


def evaluate_graph_for_frame(graph, rows, frame_idx=None):
    """Evaluate all rows for one frame and combine their shapes.

    A copied row receives the reserved current-frame value. Graphs with only
    non-CSV sources are evaluated once against a virtual row when no data row
    exists.
    """
    out = []
    if not rows:
        if frame_idx is not None and _graph_has_non_csv_source(graph):
            out.extend(graph.evaluate({"__frame__": frame_idx}))
        return out
    for row in rows:
        r = dict(row)
        r["__frame__"] = frame_idx
        out.extend(graph.evaluate(r))
    return out


def _graph_has_non_csv_source(graph):
    """Return whether the graph has a source independent of CSV rows."""
    return any(n.get("type") == "frame_index" for n in graph.nodes.values())
