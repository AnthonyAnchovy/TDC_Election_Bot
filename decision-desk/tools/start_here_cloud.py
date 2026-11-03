"""Bring the shared workbook's Start Here tab in line with cw_guide.py, without re-uploading the workbook.

A replace upload (cw_cloud.py upload --replace) wipes everything people typed into the shared copy. This edits
only the Start Here cells whose text changed, clears leftover rows at the bottom, and puts the sheet's
protection back. Nothing on any other tab is touched.

    python tools/start_here_cloud.py           show what would change (reads only)
    python tools/start_here_cloud.py --apply   make the changes

Limit: Microsoft's workbook API can't write rich text, so a changed bullet loses its bold lead-in (the words
before the colon). Unchanged rows keep their formatting.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cw_cloud as CC  # noqa: E402
import cw_guide as G  # noqa: E402

SHEET = "Start Here"
EXTRA = 6  # rows below the guide to check for leftovers from a longer old version


def plain(kind: str, text: str) -> str:
    """The cell's text exactly as cw_guide.guide_sheet writes it, minus formatting."""
    if kind == "h":
        return text.upper()
    if kind in ("b", "term"):
        lead, rest = text.split("|", 1)
        prefix = "•  " if kind == "b" and not lead[:2].rstrip(".").isdigit() else ""
        sep = " " if lead.endswith((".", "?", ":", "!")) else (". " if kind == "term" else ": ")
        return f"{prefix}{lead}{sep}{rest}"
    return "" if kind == "gap" else text


def height(kind: str, text: str) -> float:
    """Row height in points, same rule as guide_sheet."""
    rows = G._rows_needed
    if kind in ("b", "term"):
        lead, rest = text.split("|", 1)
        return 15 * rows(lead + rest, G.PER_LINE - 4) + 5
    if kind == "p":
        return 15 * rows(text) + 5
    if kind == "lede":
        return 17 * rows(text, 100) + 6
    if kind in ("ex", "note"):
        return 15 * rows(text, G.PER_LINE - 4) + 12
    return {"title": 40, "h": 26}.get(kind, 8)


def main(apply: bool):
    n = len(G.GUIDE)
    live = [r[0] if r else "" for r in CC.read(SHEET, f"B1:B{n + EXTRA}")]
    changes = []
    for i, (kind, text) in enumerate(G.GUIDE):
        want = plain(kind, text)
        if str(live[i] or "") != want:
            changes.append((i + 1, kind, text, str(live[i] or ""), want))
    leftovers = [i + 1 for i in range(n, len(live)) if str(live[i] or "").strip()]

    if not changes and not leftovers:
        print("The shared Start Here tab already matches. Nothing to do.")
        return
    for row, _k, _t, old, new in changes:
        print(f"Row {row}\n  now: {old[:150]}\n  new: {new[:150]}")
    for row in leftovers:
        print(f"Row {row}\n  now: {str(live[row - 1])[:150]}\n  new: (cleared)")
    if not apply:
        print(f"\n{len(changes)} row(s) to change, {len(leftovers)} to clear. Run again with --apply to do it.")
        return

    base = f"{CC.item()['base']}/workbook/worksheets/{CC.quote(SHEET)}"
    CC._call("POST", f"{base}/protection/unprotect", json={})
    try:
        for row, kind, text, _old, new in changes:
            CC.write(SHEET, f"B{row}", values=[[new]])
            if kind in ("b", "term"):
                CC._call("PATCH", CC._range(SHEET, f"B{row}") + "/format/font", json={"bold": False})
            CC._call("PATCH", CC._range(SHEET, f"B{row}") + "/format", json={"rowHeight": height(kind, text)})
        for row in leftovers:
            CC._call("POST", CC._range(SHEET, f"B{row}") + "/clear", json={"applyTo": "All"})
            CC._call("PATCH", CC._range(SHEET, f"B{row}") + "/format", json={"rowHeight": 8})
    finally:
        CC._call("POST", f"{base}/protection/protect", json={"options": {"allowAutoFilter": True}})
    print(f"Done: changed {len(changes)} row(s), cleared {len(leftovers)}. Start Here is protected again.")


if __name__ == "__main__":
    try:
        main("--apply" in sys.argv)
    except CC.CloudError as exc:
        sys.exit(f"Couldn't finish: {exc}")
