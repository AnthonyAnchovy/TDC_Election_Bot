"""Save the website as plain files that any static host can serve, with no server behind them.

    python -m web.export                 writes decision-desk/site/
    python -m web.export --out D:/site --every 60      rewrite the numbers every 60 seconds until stopped

Why: the live site runs on the PC that runs the bot, so when that PC sleeps, updates or loses its connection, the
site goes with it. A static copy doesn't. Point any file host at the folder (GitHub Pages, Cloudflare Pages,
Netlify, an S3 bucket) and the results pages, the analyst desk, the leaderboard and the calls stay up for everyone,
showing the numbers as of the last export. With --every, the bot's PC only has to push files; visitors never touch
it. Signing in and saving picks need the live site, so the copy is read-only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from web import app as WEB

HERE = Path(__file__).resolve().parent
PAGES = ("state", "outlook", "model", "night", "leaderboard", "global")


def export(out: Path, client: TestClient | None = None) -> list[str]:
    """Write the site to `out`. Returns the data files it couldn't write, with why."""
    client = client or TestClient(WEB.app)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    missed = []
    for name in PAGES:
        r = client.get(f"/api/{name}")
        if r.status_code != 200:
            missed.append(f"{name}: HTTP {r.status_code}")      # keep the copy from the last export, if there is one
            continue
        tmp = out / "data" / f"{name}.json.tmp"
        tmp.write_text(json.dumps(r.json()), encoding="utf-8")
        tmp.replace(out / "data" / f"{name}.json")
    shutil.copytree(HERE / "static", out / "static", dirs_exist_ok=True)
    shutil.copytree(WEB.DESK / "assets" / "fonts", out / "fonts", dirs_exist_ok=True)
    css = (out / "static" / "app.css").read_text(encoding="utf-8").replace('url("/fonts/', 'url("../fonts/')
    (out / "static" / "app.css").write_text(css, encoding="utf-8")
    stamp = int(time.time())
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    html = html.replace(".js\"", f".js?v={stamp}\"").replace(".css\"", f".css?v={stamp}\"")
    html = html.replace("<script src=", f"<script>window.DESK_STATIC = {stamp};</script>\n<script src=", 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    return missed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(WEB.DESK / "site"))
    ap.add_argument("--every", type=int, default=0, help="seconds between rewrites; 0 writes once")
    a = ap.parse_args()
    client = TestClient(WEB.app)
    while True:
        missed = export(Path(a.out), client)
        print(time.strftime("%H:%M:%S"), f"wrote {a.out}" + (f" (couldn't refresh: {'; '.join(missed)})" if missed else ""), flush=True)
        if not a.every:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
