"""County Watch, shared: keeps one copy of the workbook on OneDrive or SharePoint current, so everyone opens the
same live file in Excel (browser or desktop) instead of passing copies around.

Microsoft Graph's workbook API writes only the cells the bot owns: the grey Feed columns, each state's read and the
update stamp on the Dashboard, the Early Vote numbers, and /countyset entries in the Manual columns. What people type
(Manual columns, turnout factors, the yellow early-vote shares) is never overwritten, and is read back so the bot's
projections and alerts use it too.

One-time setup (README, "Shared live workbook"): register an app, put MS_CLIENT_ID and CLOUD_WORKBOOK in .env, then
    python cw_cloud.py login      sign in once; the bot refreshes the sign-in by itself after that
    python cw_cloud.py upload     put the current workbook on OneDrive at CLOUD_WORKBOOK
    python cw_cloud.py share      print an edit link to hand out (or share it from OneDrive yourself)
    python cw_cloud.py share --view   print a read-only link for people who should watch, not type
    python cw_cloud.py check      confirm the bot can read and write the shared copy
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

import cw_model as M
import cw_workbook as W

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
TOKEN_FILE = HERE / "countywatch" / "ms_token.json"
STATE_FILE = HERE / "countywatch" / "cloud_state.json"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.ReadWrite.All"]       # .All so a share link to someone else's file works too
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
log = logging.getLogger("countywatch")

_lock = threading.Lock()
_auth: dict = {}       # msal app + token cache, made once
_item: dict = {}       # the workbook's drive item, resolved once
_sent: dict = {}       # last payload written per region, so unchanged blocks aren't rewritten


class CloudError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("MS_CLIENT_ID") and os.getenv("CLOUD_WORKBOOK"))


def enabled() -> bool:
    """On once .env names the app and the workbook and someone has signed in."""
    return configured() and TOKEN_FILE.exists()


# ------------------------------------------------------------------ sign-in
def _app():
    if not _auth:
        import msal
        cache = msal.SerializableTokenCache()
        if TOKEN_FILE.exists():
            cache.deserialize(TOKEN_FILE.read_text(encoding="utf-8"))
        tenant = os.getenv("MS_TENANT") or "common"
        _auth["cache"] = cache
        _auth["app"] = msal.PublicClientApplication(os.environ["MS_CLIENT_ID"], token_cache=cache,
                                                    authority=f"https://login.microsoftonline.com/{tenant}")
    return _auth["app"], _auth["cache"]


def _save_cache(cache):
    if cache.has_state_changed:
        TOKEN_FILE.parent.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(cache.serialize(), encoding="utf-8")


def token() -> str:
    app, cache = _app()
    accounts = app.get_accounts()
    res = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    _save_cache(cache)
    if not res or "access_token" not in res:
        raise CloudError("the Microsoft sign-in has expired: run  python cw_cloud.py login  on the bot PC")
    return res["access_token"]


def login():
    app, cache = _app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise CloudError(flow.get("error_description") or str(flow))
    print(flow["message"])
    res = app.acquire_token_by_device_flow(flow)
    if "access_token" not in res:
        raise CloudError(res.get("error_description") or str(res))
    _save_cache(cache)
    print(f"Signed in as {res.get('id_token_claims', {}).get('preferred_username', '?')}. "
          f"The sign-in is saved in {TOKEN_FILE.relative_to(HERE)}; keep that file private.")


# ------------------------------------------------------------------ Graph
def _call(method: str, url: str, **kw) -> requests.Response:
    headers = kw.pop("headers", {})
    for attempt in range(4):
        r = requests.request(method, url if url.startswith("http") else GRAPH + url, timeout=60,
                             headers={"Authorization": f"Bearer {token()}", **headers}, **kw)
        if r.status_code in (429, 503, 504) and attempt < 3:
            time.sleep(min(int(r.headers.get("Retry-After", 2 ** attempt)), 30))
            continue
        if r.status_code >= 400:
            try:
                err = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                err = r.text[:200]
            raise CloudError(f"{r.status_code} from Microsoft: {err}")
        return r
    raise CloudError("Microsoft kept asking the bot to slow down")


def _ref() -> str:
    return os.environ["CLOUD_WORKBOOK"].strip()


def item() -> dict:
    """{'base': '/drives/../items/..', 'url': web link, 'name': file name} for CLOUD_WORKBOOK, which is either a share
    link or a path in the signed-in account's OneDrive."""
    if not _item:
        ref = _ref()
        if ref.startswith("http"):
            enc = base64.urlsafe_b64encode(ref.encode()).decode().rstrip("=")
            it = _call("GET", f"/shares/u!{enc}/driveItem").json()
        else:
            it = _call("GET", f"/me/drive/root:/{quote(ref.strip('/'))}").json()
        _item.update(base=f"/drives/{it['parentReference']['driveId']}/items/{it['id']}", url=it.get("webUrl"),
                     name=it.get("name"))
    return _item


def _range(sheet: str, address: str) -> str:
    return f"{item()['base']}/workbook/worksheets/{quote(sheet)}/range(address='{address}')"


def read(sheet: str, address: str) -> list[list]:
    return _call("GET", _range(sheet, address) + "?$select=values").json()["values"]


def _cell(v):
    """A value Graph accepts. None stays None, which Graph reads as 'leave this cell alone'."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def write(sheet: str, address: str, key: str | None = None, **body) -> bool:
    """PATCH a range with values / formulas / numberFormat. Skipped when `key` last wrote the same thing."""
    body = {k: [[_cell(v) for v in row] for row in rows] for k, rows in body.items()}
    sig = json.dumps([address, body], sort_keys=True)
    if key and _sent.get(key) == sig:
        return False
    _call("PATCH", _range(sheet, address), json=body)
    if key:
        _sent[key] = sig
    return True


# ------------------------------------------------------------------ the County Watch regions
def _fips(v) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _same(a, b) -> bool:
    norm = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else ("" if v is None else str(v))
    return norm(a) == norm(b)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _early_block(uf, nc):
    """The Early Vote sheet as the local build draws it: formulas and number formats, with the yellow input cells
    left as None so people's edits to them stay."""
    ws = Workbook().active
    W._early_sheet(ws, uf, nc)
    forms, fmts = [], []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        mine = [c.fill != W.YELLOW for c in row]
        forms.append([("" if c.value is None else c.value) if m else None for c, m in zip(row, mine)])
        fmts.append([c.number_format if m else None for c, m in zip(row, mine)])
    return f"A1:{get_column_letter(ws.max_column)}{ws.max_row}", forms, fmts


def sync(*, live: dict, reads: dict, stamp: str, uf: dict | None, nc: dict | None, entries: dict) -> dict:
    """Push the bot's cells to the shared workbook and read people's inputs back.
    entries: /countyset entries {fips: {md, mr, mo, mc}}. Returns inputs shaped like cw_workbook.read_inputs()."""
    with _lock:
        n = len(W.county_order())
        CL, last = W.CL, n + 1
        fips = [_fips(r[0]) for r in read("Counties", f"{CL['fips']}2:{CL['fips']}{last}")]
        manual = read("Counties", f"{CL['md']}2:{CL['mc']}{last}")
        # Columns are found by their headers, so the bot keeps working if the shared copy still has an older layout
        end = get_column_letter(len(W.DCOLS))
        head = read("Dashboard", f"A{W.DASH_ROW0 - 1}:{end}{W.DASH_ROW0 - 1}")[0]
        dash = read("Dashboard", f"A{W.DASH_ROW0}:{end}{W.DASH_ROW0 + len(M.STATES) - 1}")
        fac = head.index(W.FACTOR_HEADER) if W.FACTOR_HEADER in head else None

        # grey Feed columns
        by_fips = {str(f): row for rows in live.values() for f, row in rows.items()}
        feed = [[by_fips[f].get(k) for k in ("D", "R", "O", "pct_in")] if f in by_fips else [""] * 4 for f in fips]
        feed = [["" if v is None else v for v in row] for row in feed]
        write("Counties", f"{CL['fd']}2:{CL['fp']}{last}", "feed", values=feed)

        # /countyset entries go into the Manual columns when they change. A cleared entry is blanked only if the
        # cells still hold what the bot put there, so a number someone typed over it stays.
        state = _load_state()
        pushed = state.setdefault("pushed", {})
        row_of = {f: i for i, f in enumerate(fips)}
        for f, m in entries.items():
            vals = [m.get(k) if m.get(k) is not None else "" for k in ("md", "mr", "mo", "mc")]
            if f in row_of and pushed.get(f) != vals:
                r = row_of[f] + 2
                write("Counties", f"{CL['md']}{r}:{CL['mc']}{r}", values=[vals])
                manual[row_of[f]] = vals
                pushed[f] = vals
        for f in [f for f in pushed if f not in entries]:
            if f in row_of and all(_same(a, b) for a, b in zip(manual[row_of[f]], pushed[f])):
                r = row_of[f] + 2
                write("Counties", f"{CL['md']}{r}:{CL['mc']}{r}", values=[[""] * 4])
                manual[row_of[f]] = [""] * 4
            pushed.pop(f)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

        # Dashboard: each state's read (None leaves a state without one alone), and the stamp
        if W.OLD_READ_HEADER in head:
            letter = get_column_letter(head.index(W.OLD_READ_HEADER) + 1)
            col = [["\n".join(s for s in reads[row[0]] if s) if row[0] in reads else None] for row in dash]
            write("Dashboard", f"{letter}{W.DASH_ROW0}:{letter}{W.DASH_ROW0 + len(M.STATES) - 1}", "reads", values=col)
        else:
            for st, secs in reads.items():
                for j, (a, text) in enumerate(zip(W.take_cells(st), secs)):
                    write("Dashboard", a, f"take-{st}-{j}", values=[[text]])
        write("Dashboard", "A3", values=[[f"Last bot update: {stamp}"]])

        # Early Vote: rewritten only when the numbers behind it change
        address, forms, fmts = _early_block(uf, nc)
        write("Early Vote", address, "early", formulas=forms, numberFormat=fmts)

    keep: dict = {"factor": {}}
    for f, row in zip(fips, manual):
        m = {k: v for k, v in zip(("md", "mr", "mo", "mc"), row) if v not in (None, "")}
        if m:
            keep[f] = m
    for row in dash:
        if fac is not None and row[0] in M.STATES and isinstance(row[fac], (int, float)) and not isinstance(row[fac], bool):
            keep["factor"][row[0]] = row[fac]
    return keep


# ------------------------------------------------------------------ setup commands
def upload(path: Path, replace: bool = False):
    ref = _ref()
    if ref.startswith("http"):
        raise CloudError("CLOUD_WORKBOOK is a link to a file that already exists, so there's nothing to upload. "
                         "Set it to a OneDrive path (e.g. County Watch/County Watch 2026.xlsx) to upload a new copy.")
    how = "replace" if replace else "fail"
    try:
        it = _call("PUT", f"/me/drive/root:/{quote(ref.strip('/'))}:/content?@microsoft.graph.conflictBehavior={how}",
                   data=path.read_bytes(), headers={"Content-Type": XLSX}).json()
    except CloudError as exc:
        if str(exc).startswith("409 "):
            raise CloudError(f"{ref} is already on OneDrive. Run  python cw_cloud.py upload --replace  to overwrite "
                             "it (anything people typed into that copy is lost).") from exc
        raise
    _item.clear()
    print(f"Uploaded to {it.get('webUrl')}")


def share(view: bool = False):
    """A link to the workbook: anyone with it, falling back to people in your organization where the tenant blocks
    that. view=True makes a read-only link (Microsoft's 'view' link), for people who should watch but not type."""
    base = item()["base"]
    kind = "view" if view else "edit"
    for scope in ("anonymous", "organization"):
        try:
            link = _call("POST", f"{base}/createLink", json={"type": kind, "scope": scope}).json()["link"]["webUrl"]
        except CloudError as exc:
            last = exc
            continue
        who = "Anyone with this link" if scope == "anonymous" else "Anyone in your organization with this link"
        what = "open and watch the workbook (read-only: they can't change anything)" if view else "open and edit the workbook"
        print(f"{who} can {what}:\n{link}")
        return
    raise last


def check():
    it = item()
    print(f"Found {it['name']}: {it['url']}")
    title = read("Dashboard", "A1")[0][0]
    n = len(read("Counties", f"{W.CL['fips']}2:{W.CL['fips']}{len(W.county_order()) + 1}"))
    print(f"Read the Dashboard ('{title}') and {n} county rows.")
    write("Dashboard", "A3", values=[[f"Last bot update: connection check {time.strftime('%b %d %I:%M %p')}"]])
    print("Wrote the update stamp on the Dashboard (cell A3). The bot can keep this workbook current.")


def main(argv):
    what = argv[1] if len(argv) > 1 else ""
    if not os.getenv("MS_CLIENT_ID"):
        sys.exit("Put MS_CLIENT_ID in .env first (README: Shared live workbook).")
    if what != "login" and not os.getenv("CLOUD_WORKBOOK"):
        sys.exit("Put CLOUD_WORKBOOK in .env first: a OneDrive path or a share link to the workbook.")
    try:
        if what == "login":
            login()
        elif what == "upload":
            upload(HERE / "County Watch 2026.xlsx", replace="--replace" in argv)
        elif what == "share":
            share(view="--view" in argv)
        elif what == "check":
            check()
        else:
            sys.exit(__doc__)
    except CloudError as exc:
        sys.exit(f"Couldn't finish: {exc}")


if __name__ == "__main__":
    main(sys.argv)
