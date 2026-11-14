"""Build a data pack's shapes.json from a city's ward boundaries (GeoJSON), for its map and its call cards.

    python tools/build_ward_shapes.py <wards.geojson> --place CHI --pack 2027-chicago-mayor [--field ward]

Chicago's come from the City of Chicago data portal, "Boundaries - Wards (2023-)" (dataset p293-wvbd), the map in
force from May 2023, so for the 2027 municipal election:
    https://data.cityofchicago.org/resource/p293-wvbd.geojson?$limit=100

What it writes (lon/lat rings, the same form as state_shapes.json):
    {"_note": ..., "places": {"CHI": {"parts_name": "ward", "state": "IL", "outline": [rings], "parts": {"1": [rings]}}}}

"state" is the U.S. state the place is in, so a call card's locator lights it up.

The outline is the city's edge, worked out from the wards: an edge two wards share appears in both, in opposite
directions, so dropping every such pair leaves the boundary. It's done before simplifying, so the outline has no
slivers between wards. Each ring is then simplified (Douglas-Peucker) and rounded to about 10 m.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROUND = 4               # decimal places of a degree: about 10 m
WARD_TOLERANCE = 0.0002
OUTLINE_TOLERANCE = 0.0003


def _dp(points: list, tol: float) -> list:
    """Douglas-Peucker on an open polyline."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5
    best, at = -1.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm if norm else ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        if d > best:
            best, at = d, i
    if best <= tol:
        return [points[0], points[-1]]
    return _dp(points[:at + 1], tol)[:-1] + _dp(points[at:], tol)


def simplify(ring: list, tol: float) -> list | None:
    """A closed ring, simplified and rounded; None if it collapses to almost nothing."""
    pts = [(round(x, 7), round(y, 7)) for x, y in ring]
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    out = _dp(pts[:far + 1], tol)[:-1] + _dp(pts[far:] + [pts[0]], tol)[:-1]
    out = [[round(x, ROUND), round(y, ROUND)] for x, y in out]
    out = [p for i, p in enumerate(out) if p != out[i - 1]]
    return out if len(out) >= 3 else None


def outline(rings: list[list]) -> list[list]:
    """The edge of a set of rings that tile an area: every edge whose reverse is also there is shared, so it goes."""
    edges: dict[tuple, int] = {}
    for ring in rings:
        pts = [(round(x, 7), round(y, 7)) for x, y in ring]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        for a, b in zip(pts, pts[1:]):
            if a != b:
                edges[(a, b)] = edges.get((a, b), 0) + 1
    left = {}
    for (a, b), n in edges.items():
        if (b, a) not in edges:
            left.setdefault(a, []).append(b)
    out = []
    while left:
        start = next(iter(left))
        ring, at = [start], start
        while at in left:
            nxt = left[at].pop()
            if not left[at]:
                del left[at]
            ring.append(nxt)
            at = nxt
            if at == start:
                break
        if len(ring) > 3:
            out.append(ring)
    return out


def build(geojson: dict, field: str) -> dict:
    parts, raw = {}, []
    for f in geojson["features"]:
        key = str(int(float(f["properties"][field])))
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        rings = [r for poly in polys for r in poly]          # holes too: a mask fills them, which a ward never has
        raw += [r for poly in polys for r in poly[:1]]
        parts[key] = [s for s in (simplify(r, WARD_TOLERANCE) for r in rings) if s]
    edge = [s for s in (simplify(r, OUTLINE_TOLERANCE) for r in outline(raw)) if s]
    return {"parts": dict(sorted(parts.items(), key=lambda kv: int(kv[0]))), "outline": edge}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("geojson")
    ap.add_argument("--place", required=True, help="the pack's jurisdiction code, e.g. CHI")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--field", default="ward")
    ap.add_argument("--state", help="the U.S. state the place is in, e.g. IL")
    ap.add_argument("--source", default="City of Chicago data portal, Boundaries - Wards (2023-), dataset p293-wvbd")
    a = ap.parse_args()
    data = json.loads(Path(a.geojson).read_text(encoding="utf-8"))
    built = build(data, a.field)
    out_path = HERE / "packs" / a.pack / "shapes.json"
    try:
        shapes = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shapes = {}
    shapes["_note"] = ("Outlines for places that aren't U.S. states, as lon/lat rings, built by tools/build_ward_shapes.py. "
                       "outline is the place's edge (call cards); parts are its subdivisions (the ward map).")
    shapes.setdefault("places", {})[a.place] = {"parts_name": a.field, "source": a.source,
                                                 **({"state": a.state} if a.state else {}), **built}
    out_path.write_text(json.dumps(shapes, separators=(",", ":")) + "\n", encoding="utf-8")
    n = sum(len(r) for rings in built["parts"].values() for r in rings)
    print(f"{out_path.relative_to(HERE)}: {len(built['parts'])} {a.field}s, {n} points; outline {len(built['outline'])} "
          f"ring(s), {sum(len(r) for r in built['outline'])} points; {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
