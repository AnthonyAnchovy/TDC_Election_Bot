"""Build district_shapes.json: every 2026 House district as simplified lon/lat rings, for the call card's
state cut-out. Run it again only if a map changes. The bot itself needs nothing beyond the JSON.

    pip install pyshp shapely pyproj
    python tools/build_districts.py <cache dir>

Put these downloads in <cache dir> first (all public; government data, no license restrictions):

  Every state whose 2024 lines carry over into 2026 (41 states), Census 119th Congress boundaries:
    cb_2024_us_cd119_500k.zip     https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip

  The nine states with new lines for 2026 (confirmed by comparing Census's 119th and 120th Congress block
  equivalency files block by block: these nine changed, nothing else did):
    TX  planc2333.zip              https://data.capitol.texas.gov/dataset/planc2333  (PLANC2333, Aug 2025)
    CA  AB604 shapefile (zip)      https://selc.senate.ca.gov/media/603              (Prop 50 / AB 604)
    FL  EOGPCRP2026.zip            https://www.flsenate.gov/Session/Redistricting/Congressional (HB 1-D, May 2026)
    NC  NC_2025.geojson            NC OneMap "NCGA Congress 2025" FeatureServer      (SL 2025-95)
    TN  TN_2026.geojson            tnmap.tn.gov LEGISLATIVE_DISTRICTS/MapServer/2    (May 2026 lines)
    UT  UT_2026.geojson            UGRC political_us_congress_districts_2026_to_2032 (court "Map 1")
    LA  Shapefile - Act_2_(2026_RS).zip  https://redist.legis.la.gov/2026_Files/2026CONGRESSACT2 (SB 121 / Act 2)
    OH, AL: no official polygons published, so they are dissolved from Census 2020 blocks using Census's
        120th Congress block equivalency file:
      cd120.zip                    https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2027/120-congressional-district-befs/cd120.zip
      tl_2020_39_tabblock20.zip    https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_39_tabblock20.zip
      tl_2020_01_tabblock20.zip    https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_01_tabblock20.zip
GeoJSON layers are fetched with  <layer>/query?where=1%3D1&outFields=*&outSR=4326&f=geojson
"""
from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import races as R  # noqa: E402

FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "12": "FL",
        "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA",
        "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
        "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK",
        "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT",
        "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY"}
STATE_FIPS = {v: k for k, v in FIPS.items()}

# state -> (file in the cache, kind, district field)
OVERRIDES = {
    "TX": ("planc2333.zip", "shp_zip", "District"),
    "CA": ("CA_ab604_shp.zip", "shp_zip", "DISTRICT"),
    "FL": ("EOGPCRP2026.zip", "shp_zip", "DISTRICT"),
    "LA": ("Shapefile - Act_2_(2026_RS).zip", "shp_zip", "DISTRICT_I"),
    "NC": ("NC_2025.geojson", "geojson", "District"),
    "TN": ("TN_2026.geojson", "geojson", "DISTRICT"),
    "UT": ("UT_2026.geojson", "geojson", "DISTRICT"),
    "OH": ("tl_2020_39_tabblock20.zip", "blocks", None),
    "AL": ("tl_2020_01_tabblock20.zip", "blocks", None),
}


def rid(state: str, district) -> str | None:
    n = int(str(district).strip())
    if R.HOUSE_SEATS[state] == 1:
        return f"{state}-AL"
    rid_ = f"{state}-{n:02d}"
    return rid_ if rid_ in R.RACES else None


def read_shp_zip(path: Path):
    """Yield (record dict, shapely geometry in lon/lat) from a zipped shapefile, reprojecting if needed."""
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if not n.startswith("__MACOSX")]
    base = next(n for n in names if n.lower().endswith(".shp"))[:-4]
    shp = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), shx=io.BytesIO(z.read(base + ".shx")),
                           dbf=io.BytesIO(z.read(base + ".dbf")))
    prj = next((n for n in names if n.lower() == base.lower() + ".prj"), None)
    to_lonlat = None
    if prj:
        crs = CRS.from_wkt(z.read(prj).decode("utf-8", "replace"))
        if not crs.is_geographic:
            to_lonlat = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True).transform
    fields = [f[0] for f in shp.fields[1:]]
    for sr in shp.iterShapeRecords():
        geom = shape(sr.shape.__geo_interface__)
        if to_lonlat:
            geom = transform(to_lonlat, geom)
        yield dict(zip(fields, sr.record)), geom


def read_geojson(path: Path):
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        yield f["properties"], shape(f["geometry"])


def dissolve_blocks(path: Path, state: str, bef: dict[str, str]) -> dict[str, shapely.Geometry]:
    """Union a state's 2020 blocks into districts using the 120th Congress block equivalency file."""
    parts: dict[str, list] = defaultdict(list)
    for rec, geom in read_shp_zip(path):
        cd = bef.get(rec["GEOID20"])
        if cd:
            parts[cd].append(geom)
    out = {}
    for cd, geoms in parts.items():
        r = rid(state, cd)
        if r:
            out[r] = shapely.coverage_union_all(geoms).buffer(0)
            print(f"  {r}: {len(geoms):,} blocks")
    return out


def load_bef(cache: Path, states: set[str]) -> dict[str, str]:
    fips = {STATE_FIPS[s] for s in states}
    out = {}
    with zipfile.ZipFile(cache / "cd120.zip") as z, z.open("NationalCD120.txt") as f:
        rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        next(rd)
        for row in rd:
            if row[1] in fips:
                out[row[0]] = row[-1]
    return out


def rings(geom: shapely.Geometry) -> list[list[list[float]]]:
    """Simplify relative to the district's size (so city districts keep their detail) and keep outer rings."""
    minx, miny, maxx, maxy = geom.bounds
    tol = min(0.02, max(0.0003, 0.003 * ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5))
    g = geom.simplify(tol, preserve_topology=True)
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g] if g.geom_type == "Polygon" else []
    biggest = max((p.area for p in polys), default=0)
    out = []
    for p in polys:
        if p.area < biggest * 0.0005:          # drop slivers and tiny islets that would not show at card size
            continue
        out.append([[round(x, 4), round(y, 4)] for x, y in p.exterior.coords])
    return out


def main(cache: Path):
    shapes: dict[str, shapely.Geometry] = {}
    print("Census 119th Congress (states with unchanged lines)")
    for rec, geom in read_shp_zip(cache / "cb_2024_us_cd119_500k.zip"):
        st = FIPS.get(rec["STATEFP"])
        if st and st not in OVERRIDES:
            r = rid(st, rec["CD119FP"])
            if r:
                shapes[r] = geom
    bef = load_bef(cache, {s for s, (_, kind, _) in OVERRIDES.items() if kind == "blocks"})
    for st, (name, kind, field) in OVERRIDES.items():
        print(f"{st}: {name}")
        if kind == "blocks":
            shapes.update(dissolve_blocks(cache / name, st, bef))
            continue
        reader = read_shp_zip if kind == "shp_zip" else read_geojson
        got = {}
        for rec, geom in reader(cache / name):
            r = rid(st, rec[field])
            if r:
                got[r] = got[r].union(geom) if r in got else geom
        shapes.update(got)

    missing = [r for r, v in R.RACES.items() if v["type"] == "house" and r not in shapes]
    if missing:
        raise SystemExit(f"No shape for {len(missing)} districts: {', '.join(missing[:20])}")
    out = {r: rings(g.buffer(0)) for r, g in sorted(shapes.items())}
    dest = HERE / "district_shapes.json"
    dest.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(out)} districts to {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
