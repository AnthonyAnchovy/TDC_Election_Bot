"""The Decision Desk website: make predictions on a map, see the leaderboard, follow the night.

    python -m uvicorn web.app:app --port 8026        (from decision-desk/)

It shares the bot's database (desk.db) and its rules: picks.py for the game, scoring.py for points, the same
config.json. Whatever is saved here the bot sees at once, and the other way round. Nothing in this file talks to
Discord except signing in.

Signing in is Discord's: the site asks only who you are and whether you're in the server (scopes `identify` and
`guilds.members.read`), and keeps that in a signed cookie. Settings, all in .env beside the bot's:
  DESK_WEB_SECRET         any long random string; signs the cookie. Required.
  DISCORD_CLIENT_ID       the bot application's ID, from the Discord developer portal (OAuth2 page)
  DISCORD_CLIENT_SECRET   its client secret, same page
  DESK_WEB_URL            where the site is reached, e.g. https://desk.example.com. Add <that>/auth/callback as a
                          redirect in the developer portal.
  GUILD_ID                already there for the bot; only members of this server can sign in
  DESK_WEB_DEV=1          for trying it on your own machine: /login?as=123&name=Me signs in without Discord.
                          Only honored from localhost.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import clock
import competitive as CMP
import packs
import picks as P
import races as R
import scoring as S
import tenancy as T
from store import Store

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
load_dotenv(DESK / ".env")

DISCORD = "https://discord.com/api"
COOKIE, STATE_COOKIE, PACK_COOKIE, SERVER_COOKIE = "desk_session", "desk_oauth", "desk_pack", "desk_server"
SESSION_DAYS = 30

app = FastAPI(title="Decision Desk", docs_url=None, redoc_url=None, openapi_url=None)
T.configure(check_same_thread=False, timeout=10)
_store: Store | None = None     # a database handed in by a test, used for every pack and server
_scope: list = [None, None]     # the pack and server the request being answered is about


def store() -> Store:
    return _store or T.store_for(_scope[1], _scope[0])


def use_store(s: Store | None):
    """Tests hand in their own database."""
    global _store
    _store = s


def may_see(user: dict | None, server: str, pack_id: str) -> bool:
    """Whether someone can look at a server's desk: the home server (sign-in already checked they belong to it), or
    one whose bot has recorded them as a member."""
    if server == T.home():
        return True
    return bool(user) and str(user["uid"]) in T.store_for(server, pack_id).setting("names", {})


def chosen(request: Request) -> tuple[str, str]:
    """The data pack and the server this request is about: what the visitor picked, if they may, else the defaults."""
    listed = [p["id"] for p in packs.available()]
    pack_id = request.cookies.get(PACK_COOKIE)
    pack_id = pack_id if pack_id in listed else packs.default_id()
    server = request.cookies.get(SERVER_COOKIE) or T.home()
    if _store is not None or server not in packs.servers(pack_id) or not may_see(me(request), server, pack_id):
        server = T.home()
    return pack_id, server


class on:
    """`with on(request):` answers a request inside its data pack and server. Nothing in the block may await: the
    active pack is one per process."""

    def __init__(self, request: Request):
        self.pack_id, self.server = chosen(request)

    def __enter__(self):
        self.ctx = packs.using(self.pack_id)
        self.ctx.__enter__()
        _scope[:] = [self.pack_id, self.server]
        return self

    def __exit__(self, *exc):
        _scope[:] = [None, None]
        return self.ctx.__exit__(*exc)


# ------------------------------------------------------------------ the signed cookie
def _secret() -> bytes:
    secret = os.getenv("DESK_WEB_SECRET", "")
    if not secret:
        raise HTTPException(500, "DESK_WEB_SECRET isn't set in .env, so nobody can sign in.")
    return secret.encode()


def sign(data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    return body + "." + hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()


def unsign(token: str | None) -> dict | None:
    """The cookie's contents if we signed it and it hasn't run out, else None."""
    try:
        body, mac = (token or "").rsplit(".", 1)
        if not hmac.compare_digest(mac, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) > clock.time() else None
    except (ValueError, HTTPException):
        return None


def me(request: Request) -> dict | None:
    return unsign(request.cookies.get(COOKIE))


def need_me(request: Request) -> dict:
    user = me(request)
    if not user:
        raise HTTPException(401, "Sign in with Discord first.")
    return user


def _local(request: Request) -> bool:
    return (request.url.hostname or "") in ("localhost", "127.0.0.1", "::1", "testserver")


def _base(request: Request) -> str:
    return (os.getenv("DESK_WEB_URL") or str(request.base_url)).rstrip("/")


def _signed_in(request: Request, uid: int, name: str, avatar: str | None = None) -> RedirectResponse:
    names = dict(store().setting("names", {}))
    if names.get(str(uid)) != name:
        names[str(uid)] = name
        store().set_setting("names", names)
    out = RedirectResponse("/", 303)
    out.set_cookie(COOKIE, sign({"uid": str(uid), "name": name, "avatar": avatar,
                                 "exp": clock.time() + SESSION_DAYS * 86400}),
                   max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=not _local(request))
    out.delete_cookie(STATE_COOKIE)
    return out


# ------------------------------------------------------------------ signing in
@app.get("/login")
async def login(request: Request):
    if os.getenv("DESK_WEB_DEV") == "1" and _local(request) and request.query_params.get("as"):
        uid = request.query_params["as"]
        if not uid.isdigit():
            raise HTTPException(400, "as= has to be a number")
        return _signed_in(request, int(uid), request.query_params.get("name") or f"user {uid}")
    client = os.getenv("DISCORD_CLIENT_ID")
    if not client or not os.getenv("DISCORD_CLIENT_SECRET"):
        raise HTTPException(500, "DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET aren't set in .env.")
    state = secrets.token_urlsafe(24)
    url = "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": client, "response_type": "code", "scope": "identify guilds.members.read",
        "redirect_uri": _base(request) + "/auth/callback", "state": state, "prompt": "none"})
    out = RedirectResponse(url, 303)
    out.set_cookie(STATE_COOKIE, sign({"state": state, "exp": clock.time() + 600}), max_age=600, httponly=True,
                   samesite="lax", secure=not _local(request))
    return out


async def discord_identity(code: str, redirect_uri: str) -> dict:
    """Trade Discord's code for who this is: {"id", "name", "avatar", "member"}. member is False when GUILD_ID is
    set and they aren't in that server."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.post(f"{DISCORD}/oauth2/token", data={
                "client_id": os.getenv("DISCORD_CLIENT_ID"), "client_secret": os.getenv("DISCORD_CLIENT_SECRET"),
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}) as r:
            if r.status != 200:
                raise HTTPException(400, "Discord didn't accept that sign-in. Try again.")
            auth = {"Authorization": f"Bearer {(await r.json())['access_token']}"}
        async with s.get(f"{DISCORD}/users/@me", headers=auth) as r:
            user = await r.json()
        name, member, guild = user.get("global_name") or user.get("username"), True, os.getenv("GUILD_ID")
        if guild:
            async with s.get(f"{DISCORD}/users/@me/guilds/{guild}/member", headers=auth) as r:
                member = r.status == 200
                if member:
                    name = (await r.json()).get("nick") or name
    return {"id": int(user["id"]), "name": name, "avatar": user.get("avatar"), "member": member}


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    sent = unsign(request.cookies.get(STATE_COOKIE))
    if not code or not sent or not hmac.compare_digest(sent.get("state", ""), state):
        raise HTTPException(400, "That sign-in link has expired. Go back and sign in again.")
    who = await discord_identity(code, _base(request) + "/auth/callback")
    if not who["member"]:
        raise HTTPException(403, "This site is for members of the server. Ask for an invite, then sign in again.")
    return _signed_in(request, who["id"], who["name"], who["avatar"])


@app.post("/logout")
async def logout():
    out = JSONResponse({"ok": True})
    out.delete_cookie(COOKIE)
    return out


# ------------------------------------------------------------------ the map
_map: dict | None = None


def us_map() -> dict:
    """Every state as an SVG path in one 960x600 frame: the lower 48, with Alaska and Hawaii set in at bottom left."""
    global _map
    if _map is not None:
        return _map
    shapes = json.loads((DESK / "state_shapes.json").read_text(encoding="utf-8"))

    def project(rings, lat0):
        k = math.cos(math.radians(lat0))
        return [[((x - 360 if x > 0 else x) * k, -y) for x, y in ring] for ring in rings if len(ring) > 2]

    def fit(groups: dict, box):
        """Scale the projected rings of several states together into box = (x, y, w, h), keeping their shape."""
        xs = [p[0] for rings in groups.values() for ring in rings for p in ring]
        ys = [p[1] for rings in groups.values() for ring in rings for p in ring]
        sc = min(box[2] / (max(xs) - min(xs)), box[3] / (max(ys) - min(ys)))
        ox = box[0] + (box[2] - (max(xs) - min(xs)) * sc) / 2
        oy = box[1] + (box[3] - (max(ys) - min(ys)) * sc) / 2
        out = {}
        for st, rings in groups.items():
            pts = [[(ox + (x - min(xs)) * sc, oy + (y - min(ys)) * sc) for x, y in ring] for ring in rings]
            d = " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in ring) + " Z" for ring in pts)
            big = max(pts, key=len)
            out[st] = {"d": d, "cx": round(sum(p[0] for p in big) / len(big), 1),
                       "cy": round(sum(p[1] for p in big) / len(big), 1)}
        return out

    lower = {st: project(r, 38.5) for st, r in shapes.items() if st not in ("AK", "HI") and not st.startswith("_")}
    states = fit(lower, (10, 10, 940, 500))
    states.update(fit({"AK": project(shapes["AK"], 63)}, (10, 455, 190, 135)))
    states.update(fit({"HI": project(shapes["HI"], 20.5)}, (215, 520, 110, 70)))
    _map = {"width": 960, "height": 600, "states": states}
    return _map


# ------------------------------------------------------------------ the game
def results() -> dict[str, str]:
    """The same rule as the bot's results(): finals, then pro calls, then the desk, gated on poll closings."""
    full = store().all_full()
    return S.truth({rid: c["party"] for rid, c in full.items()}, store().procalls(),
                   P.config().get("score_against", "pros_then_desk"),
                   called_at={rid: c["called_at"] for rid, c in full.items()},
                   finals=store().setting("final_results", {}), gate=not os.getenv("DESK_FAKE_NOW"))


def display_name(uid: int) -> str:
    return store().setting("names", {}).get(str(uid)) or f"user {str(uid)[-4:]}"


def entry_of(uid: int) -> dict:
    picks, meta = store().user_predictions(uid)
    seats = (meta or {}).get("house_dem_seats")
    return {"picks": picks, "house_dem_seats": seats, "saved_at": (meta or {}).get("saved_at"),
            "completion": S.completion(picks, {"house_dem_seats": seats})}


def board_rows(board: list[dict], name=None) -> list[dict]:
    return [{"name": (name or (lambda r: display_name(r["user_id"])))(r), "id": str(r["user_id"]), "points": r["points"],
             "flips": r["flips"], "cats": {k: [c["right"], c["wrong"], c["pending"], c["picked"]] for k, c in r["cats"].items()},
             "guess": r["guess"], "miss": r["miss"], "marks": r["marks"], "server": r.get("server")} for r in board]


@app.get("/api/state")
async def state(request: Request):
    """Everything the page needs to draw itself."""
    user = me(request)
    with on(request) as at:
        cfg = P.config()
        servers = [sv for sv in packs.servers(at.pack_id) if may_see(user, sv, at.pack_id)]
        out = {"me": user and {"id": user["uid"], "name": user["name"], "avatar": user.get("avatar")},
               "pack": at.pack_id, "packs": packs.available(), "server": at.server,
               "servers": [{"id": sv, "name": T.store_for(sv, at.pack_id).setting("server_name") or
                            ("This server" if sv == T.home() else f"Server {sv[-4:]}")} for sv in servers],
               "global": {"joined": T.joined(at.server, at.pack_id) if _store is None else False,
                          "out": bool(user) and _store is None and T.opted_out(at.server, int(user["uid"]), at.pack_id)},
               "deadline": P.deadline(cfg).isoformat(), "now": clock.time(), "hidden": P.hidden(cfg),
               "open": P.is_open(store(), int(user["uid"]), cfg) if user else clock.time() < P.deadline(cfg).timestamp(),
               "points": P.points(cfg), "races": P.catalogue(), "map": us_map(),
               "fills": {"safe": P.safe_fill(), "ratings": P.ratings_fill()},
               "dev": os.getenv("DESK_WEB_DEV") == "1" and _local(request)}
        if user:
            out["entry"] = entry_of(int(user["uid"]))
            names = store().setting("names", {})
            if user["uid"] not in names:        # each desk keeps its own names: learn this one the first time it's opened
                store().set_setting("names", {**names, user["uid"]: user["name"]})
    return out


@app.post("/api/choose")
async def choose(request: Request):
    """Switch the data pack, the server, or both. What can't be chosen is ignored, and the reply says where you are."""
    body = await request.json()
    out = JSONResponse({"ok": True})
    if body.get("pack") in [p["id"] for p in packs.available()]:
        out.set_cookie(PACK_COOKIE, body["pack"], max_age=365 * 86400, samesite="lax")
    if body.get("server"):
        out.set_cookie(SERVER_COOKIE, str(body["server"]), max_age=365 * 86400, samesite="lax")
    return out


@app.put("/api/picks")
async def save(request: Request):
    user = need_me(request)
    if request.headers.get("x-desk") != "1":
        raise HTTPException(400, "Missing the X-Desk header.")
    uid = int(user["uid"])
    body = await request.json()
    with on(request):
        if not P.is_open(store(), uid):
            raise HTTPException(403, "Predictions are locked.")
        picks, seats, problems = P.validate(body.get("picks"), body.get("house_dem_seats"))
        if problems:
            raise HTTPException(422, "; ".join(problems[:5]))
        first = not store().user_predictions(uid)[1]
        store().save_predictions(uid, picks, seats)
        # the bot reads this list and tells the board, so a save here is announced like a save in Discord
        store().set_setting("web_saves", store().setting("web_saves", []) + [{"uid": uid, "first": first, "t": clock.time()}])
        return entry_of(uid)


@app.get("/api/leaderboard")
async def leaderboard(request: Request):
    with on(request):
        cfg = P.config()
        entries = {uid: store().user_predictions(uid) for uid in store().predictors()}
        if P.hidden(cfg):
            rows = [{"name": display_name(uid), "id": str(uid), "share": S.completion(p, m)["share"],
                     "missing": [S.PART_NAMES[x] for x in S.completion(p, m)["missing"]]} for uid, (p, m) in entries.items()]
            return {"hidden": True, "rows": sorted(rows, key=lambda r: -r["share"])}
        final = store().setting("final_house_dem")
        res = results()
        left = S.open_items(res, final)
        return {"hidden": False, "rows": board_rows(S.leaderboard(entries, res, P.points(cfg), final)),
                "final_house_dem": final, "closed": left["closed"],
                "open_races": sum(len(left[k]) for k in ("control", "senate", "governor", "battleground", "other"))}


@app.get("/api/global")
async def global_board(request: Request):
    """The scoreboard across every server that has joined it, scored on shared results."""
    user = me(request)
    with on(request) as at:
        if P.hidden():
            return {"hidden": True, "rows": [], "servers": len(T.joined_servers(at.pack_id))}
        rows = T.global_board(at.pack_id, P.points(), gate=not os.getenv("DESK_FAKE_NOW"))
        names = {sv: T.store_for(sv, at.pack_id).setting("server_name") or f"Server {sv[-4:]}" for sv in T.joined_servers(at.pack_id)}
        out = board_rows(rows, name=lambda r: r["name"])
        for r in out:
            r["server"] = names.get(r["server"], r["server"])
        return {"hidden": False, "rows": out, "servers": len(names), "joined": T.joined(at.server, at.pack_id),
                "out": bool(user) and T.opted_out(at.server, int(user["uid"]), at.pack_id)}


@app.post("/api/global/me")
async def global_me(request: Request):
    """Keep your own entry off the global board, or put it back."""
    user = need_me(request)
    body = await request.json()
    with on(request) as at:
        T.set_opted_out(at.server, int(user["uid"]), bool(body.get("out")), at.pack_id)
    return {"out": bool(body.get("out"))}


@app.get("/api/entry/{uid}")
async def entry(uid: int, request: Request):
    user = me(request)
    with on(request):
        if P.hidden() and not (user and int(user["uid"]) == uid):
            raise HTTPException(403, "Picks are hidden until predictions close.")
        if uid not in store().predictors():
            raise HTTPException(404, "No predictions saved under that name.")
        out = entry_of(uid)
        out["name"] = display_name(uid)
        out["marks"] = S.grade(out["picks"], results(), P.points())["marks"]
        return out


@app.get("/api/night")
async def night(request: Request):
    """Where the desk stands: what each body adds up to, the path to each majority, and the latest calls."""
    with on(request):
        calls, cands = store().all(), P.candidates()
        recent = []
        for ts, action, rid, party in store().recent(40):
            if rid not in R.RACES:
                continue
            recent.append({"t": ts, "action": action, "race": rid, "label": R.RACES[rid]["label"], "party": party,
                           "name": P.name_of(rid, party, cands) if party else None,
                           "flip": bool(party and S.holder(rid) and S.holder(rid) != party),
                           "rated": CMP.RATING_OF.get(rid)})
        bodies = []
        for key, b in R.BODIES.items():
            counts = S.chamber_counts(key, calls)
            uncalled = counts.pop("uncalled")
            total = b.get("seats") or sum(counts.values()) + uncalled
            path = S.path_text(key, calls).replace("**", "")
            bodies.append({"key": key, "name": b.get("name", key), "counts": {k: v for k, v in counts.items() if v},
                           "uncalled": uncalled, "majority": b.get("majority"), "total": total,
                           "clinched": S.clinched(key, calls), "control": calls.get(b.get("control") or ""), "path": path})
        return {"now": clock.time(), "calls": calls, "bodies": bodies, "recent": recent,
                "senate": next((b for b in bodies if b["key"] == "senate"), None),
                "house": next((b for b in bodies if b["key"] == "house"), None)}


# ------------------------------------------------------------------ pages and files
@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/")
async def index():
    """The page, with its script and stylesheet addressed by when they last changed, so an update is picked up at
    once and an unchanged file stays cached."""
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "app.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((HERE / 'static' / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/fonts", StaticFiles(directory=DESK / "assets" / "fonts"), name="fonts")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
