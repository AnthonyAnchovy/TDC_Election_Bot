/* Decision Desk: predictions, the leaderboard and the night, for whichever data pack is chosen.
   Nothing here knows about a particular election: the page is drawn from the pack's sections, races and bodies.
   No build step; plain browser JavaScript. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const app = {
  data: null,          // /api/state
  picks: {},           // what's on screen
  seats: "",           // the tiebreaker, as typed
  saved: "",           // what the server has, to tell when there's something to save
  tab: null,
  route: "predict",
  nightTimer: null,
};

// ------------------------------------------------------------------ talking to the server
/* A static copy of the site (python -m web.export) has no server behind it: every answer is a file saved beside
   the page when the copy was made, and nothing can be saved. */
async function staticApi(path) {
  const name = path.replace(/^\/api\//, "").replace(/[^a-z0-9]+/gi, "-");
  const res = await fetch(`data/${name}.json`);
  if (!res.ok) throw new Error("This is a saved copy of the site, and that page wasn't saved with it.");
  return res.json();
}

async function api(path, opts = {}) {
  if (window.DESK_STATIC) return staticApi(path);
  const res = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", "X-Desk": "1", ...(opts.headers || {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `The server said ${res.status}`);
  return body;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

const snapshot = () => JSON.stringify([Object.keys(app.picks).sort().map((k) => [k, app.picks[k]]), String(app.seats ?? "")]);
const dirty = () => app.data?.me && snapshot() !== app.saved;
const canEdit = () => Boolean(app.data?.me && app.data.open);
const sections = () => app.data.races.sections;

// ------------------------------------------------------------------ small pieces
function raceById() {
  if (!raceById.map) {
    raceById.map = {};
    for (const sec of sections()) {
      for (const r of sec.races) raceById.map[r.id] = r;
      if (sec.control) raceById.map[sec.control.id] = sec.control;
    }
  }
  return raceById.map;
}

/* An option's name and colour, wherever it is asked for: the race's own option, else the pack's shared one. */
function option(id, key) {
  const own = raceById()[id]?.options.find((o) => o.party === key);
  const shared = app.data.races.options[key] || {};
  return { name: own?.name || shared.name || key, color: own?.color || shared.color || "#64748b", plural: shared.plural || own?.name || key };
}
const shared = (key) => app.data.races.options[key] || { name: key, plural: key, color: "#64748b" };

function surname(name) {
  const parts = name.split(" ").filter((p) => !/^(jr\.?|sr\.?|ii|iii)$/i.test(p));
  return parts[parts.length - 1] || name;
}

function metaLine(race) {
  const bits = [];
  if (race.weight > 1) bits.push(`${race.weight} votes`);
  if (race.holder) bits.push(`${shared(race.holder).name === race.holder ? option(race.id, race.holder).name : race.holder}-held`);
  if (race.rating) bits.push(race.rating);
  else if (race.safe) bits.push(`Safe ${race.safe}`);
  if (race.open) bits.push("open seat");
  else if (race.incumbent) bits.push(`inc. ${surname(race.incumbent)}`);
  if (race.pvi) bits.push(race.pvi);
  return bits.join(" · ");
}

function optionButtons(race, pick, readonly, mark) {
  const opts = race.options.map((o) => {
    const on = pick === o.party;
    const label = o.named || on ? esc(o.name) : `<span class="muted">${esc(shared(o.party).name)}</span>`;
    return `<button class="opt${on ? " on" : ""}" style="--c:${esc(o.color)}" data-race="${race.id}" data-party="${esc(o.party)}" ${readonly ? "disabled" : ""}
      aria-pressed="${on}"><span class="dot"></span>${label}${o.tag ? `<span class="p">${esc(o.tag)}</span>` : ""}</button>`;
  }).join("");
  const m = mark === true ? '<span class="mark-ok" title="Right">✓</span>' : mark === false ? '<span class="mark-no" title="Wrong">✗</span>' : "";
  return `<div class="opts">${opts}${m}</div>`;
}

function raceRow(race, picks, readonly, marks, title) {
  return `<div class="race" data-row="${race.id}">
    <div class="where">${esc(title || race.state_name || race.label)}<span class="meta">${esc(metaLine(race))}</span></div>
    ${optionButtons(race, picks[race.id], readonly, marks ? marks[race.id] : undefined)}</div>`;
}

function mapSvg(sec, picks, readonly) {
  const m = app.data.map, ids = new Set(sec.races.map((r) => r.id)), byId = raceById();
  const small = new Set(["DE", "RI", "CT", "NJ", "MD", "MA", "NH", "VT", "HI", "DC"]), used = new Set();
  let paths = "", labels = "";
  for (const [st, shape] of Object.entries(m.states)) {
    const id = `${st}-${sec.suffix}`, race = ids.has(id) ? byId[id] : null, pick = picks[id];
    if (pick) used.add(pick);
    paths += `<path d="${shape.d}" class="${race ? "race" : ""}" ${pick ? `style="fill:${esc(option(id, pick).color)}"` : ""} ${race ? `data-state="${id}"` : ""}>
      <title>${esc(race ? race.label + ": " + (pick ? option(id, pick).name : "not picked") : st + ": not in this section")}</title></path>`;
    if (race && !small.has(st)) labels += `<text x="${shape.cx}" y="${shape.cy}" class="${pick ? "on" : ""}">${st}</text>`;
  }
  const keys = [...new Set([...sec.races.flatMap((r) => r.options.map((o) => o.party))])].filter((k) => app.data.races.options[k]);
  return `<svg class="map${readonly ? " readonly" : ""}" viewBox="0 0 ${m.width} ${m.height}" role="img" aria-label="Map of ${esc(sec.name)} picks">${paths}${labels}</svg>
    <div class="legend">${keys.map((k) => `<span><i style="background:${esc(shared(k).color)}"></i>${esc(shared(k).name)}</span>`).join("")}
    <span><i style="background:var(--none)"></i>Not picked</span><span><i style="background:var(--off)"></i>Not in play</span></div>`;
}

/* What a section's picks add up to in its body (Senate seats, electoral votes), and who that gives it to. */
function bodyTally(sec, picks) {
  const body = app.data.races.bodies[sec.body];
  if (!body || !body.majority) return "";
  const n = { ...(body.holdover || {}) };
  let open = 0, openWeight = 0;
  for (const race of sec.races) {
    const p = picks[race.id];
    if (p) n[p] = (n[p] || 0) + race.weight; else { open++; openWeight += race.weight; }
  }
  const total = body.seats || Object.values(n).reduce((a, b) => a + b, 0) + openWeight;
  const keys = Object.keys(n).filter((k) => n[k]).sort((a, b) => n[b] - n[a]);
  let verdict = `${open} still to pick`;
  if (!open) {
    const win = keys.find((k) => n[k] >= (k === body.tie_goes_to ? body.majority - 1 : body.majority));
    verdict = win ? `${shared(win).plural} win the ${body.name}` : "Nobody reaches a majority";
    if (win && win === body.tie_goes_to && n[win] === body.majority - 1) verdict += " on the tie-break";
  }
  const left = keys[0], right = keys[1], rest = keys.slice(2);
  const span = (k) => `<span><span class="n" style="color:${esc(shared(k).color)}">${n[k]}</span> ${esc(shared(k).plural)}</span>`;
  const bar = (k) => `<span style="width:${(100 * n[k]) / total}%;background:${esc(shared(k).color)}"></span>`;
  return `<div class="tally"><div class="tally-line">${left ? span(left) : ""}${rest.map(span).join("")}${right ? span(right) : ""}</div>
    <div class="seatwrap"><div class="seats">${left ? bar(left) : ""}${rest.map(bar).join("")}<span class="gap"></span>${right ? bar(right) : ""}</div>
      <div class="mark" style="left:50%"></div>
      <div class="marklab">${body.majority} of ${total} for a majority · <b class="verdict">${esc(verdict)}</b></div></div></div>`;
}

function completion() {
  const p = app.picks, parts = [];
  for (const sec of sections()) {
    if (sec.control) parts.push({ key: sec.key + ":control", name: sec.control.label, done: p[sec.control.id] ? 1 : 0, total: 1, required: true });
    if (sec.races.length) parts.push({ key: sec.key, name: sec.name, done: sec.races.filter((r) => p[r.id]).length, total: sec.races.length, required: sec.required });
  }
  const tb = app.data.races.tiebreaker;
  if (tb) parts.push({ key: "tiebreak", name: "Tiebreaker", done: String(app.seats ?? "").trim() !== "" ? 1 : 0, total: 1, required: false });
  const done = parts.reduce((a, x) => a + x.done, 0), total = parts.reduce((a, x) => a + x.total, 0);
  return { parts, share: total ? done / total : 0 };
}

// ------------------------------------------------------------------ the picker
function countdown() {
  const end = new Date(app.data.deadline), ms = end - Date.now();
  const when = end.toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  if (!app.data.open) return `<div class="clock">Predictions are locked<b>closed ${esc(when)}</b></div>`;
  if (ms <= 0) return `<div class="clock">Your picks are unlocked for now<b>the deadline was ${esc(when)}</b></div>`;
  const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, mn = Math.floor(ms / 6e4) % 60;
  return `<div class="clock">Picks close ${esc(when)}<b>${d ? d + "d " : ""}${h}h ${mn}m left</b></div>`;
}

function tabBar() {
  const c = completion();
  const count = (sec) => {
    const mine = c.parts.filter((x) => x.key === sec.key || x.key === sec.key + ":control" || (sec.kind === "house" && x.key === "tiebreak"));
    return [mine.reduce((a, x) => a + x.done, 0), mine.reduce((a, x) => a + x.total, 0)];
  };
  const tabs = sections().map((sec) => {
    const [a, b] = count(sec);
    return `<button role="tab" data-tab="${sec.key}" class="${app.tab === sec.key ? "on" : ""}" aria-selected="${app.tab === sec.key}">${esc(sec.name)}<span class="count ${a === b ? "full" : ""}">${a}/${b}</span></button>`;
  });
  tabs.push(`<button role="tab" data-tab="review" class="${app.tab === "review" ? "on" : ""}" aria-selected="${app.tab === "review"}">Review</button>`);
  return `<div class="tabs" role="tablist">${tabs.join("")}</div>`;
}

function worth(sec) {
  const bits = [`Each pick is worth ${sec.points}.`];
  if (sec.implied) bits.push(`${sec.implied.name} is worth ${sec.implied.points} more, and it's whatever your picks here add up to.`);
  if (!sec.required) bits.unshift("Bonus.");
  return bits.join(" ");
}

function tiebreakField(ro) {
  const tb = app.data.races.tiebreaker;
  if (!tb) return "";
  return `<div class="field"><label for="seats">Tiebreaker: ${esc(tb.label)}</label>
    <input id="seats" type="number" inputmode="numeric" min="${tb.min ?? 0}" max="${tb.max ?? ""}" value="${esc(app.seats)}" ${ro ? "disabled" : ""}>
    <div class="hint">${esc(tb.hint || "If two people finish level on points, the closer guess wins.")}</div></div>`;
}

function tools(sec, ro, extra = "") {
  if (ro) return "";
  const safe = sec.races.some((r) => r.safe) ? `<button class="btn ghost small" data-fill="safe:${sec.key}">Fill the safe races</button>` : "";
  return `<div class="tools">${safe}${extra}<button class="btn ghost small" data-clear="${sec.key}">Clear this page</button></div>`;
}

function grouped(sec, ro, titleOf) {
  const groups = new Map();
  for (const r of sec.races) {
    const g = r.group || r.rating || (r.safe ? "Rated safe" : sec.races.some((x) => x.safe || x.rating) ? "In play" : "");
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(r);
  }
  const order = [...groups.keys()].sort((a, b) => (a === "Rated safe") - (b === "Rated safe"));
  return order.map((g) => {
    const list = groups.get(g);
    const head = g ? `<h3><span>${esc(g)}</span><span>${list.filter((r) => app.picks[r.id]).length}/${list.length}</span></h3>` : "";
    return `<div class="group">${head}${list.map((r) => raceRow(r, app.picks, ro, null, titleOf ? titleOf(r) : null)).join("")}</div>`;
  }).join("");
}

function mapSection(sec) {
  const ro = !canEdit();
  return `<div class="grid"><div class="stick">${bodyTally(sec, app.picks)}${mapSvg(sec, app.picks, ro)}
      <p class="muted" style="font-size:14px">Click a state to cycle through its candidates, or pick by name in the list. ${esc(worth(sec))}</p></div>
    <div>${tools(sec, ro)}${grouped(sec, ro)}</div></div>`;
}

function listSection(sec) {
  const ro = !canEdit(), last = sec.key === sections()[sections().length - 1].key && !sections().some((s) => s.kind === "house");
  return `<div class="grid"><div>
      <h2 style="font-size:28px">${esc(sec.name)}</h2><p class="muted">${esc(sec.blurb || worth(sec))}</p>
      ${grouped(sec, ro, (r) => r.label)}</div>
    <div>${bodyTally(sec, app.picks)}${last ? tiebreakField(ro) : ""}${tools(sec, ro)}</div></div>`;
}

function houseSection(sec) {
  const ro = !canEdit(), c = sec.control, pick = app.picks[c.id], pts = app.data.points;
  const body = app.data.races.bodies[sec.body || c.body] || {};
  const big = c.options.map((o) => `<button class="${pick === o.party ? "on" : ""}" style="--c:${esc(o.color)}" data-race="${c.id}" data-party="${esc(o.party)}"
      ${ro ? "disabled" : ""} aria-pressed="${pick === o.party}"><span class="kicker">${esc(shared(o.party).name)} majority</span><b>${esc(o.name)}</b>
      ${c.holder ? (o.party === c.holder ? "keep it" : "take it") : ""}</button>`).join("");
  const seats = sec.races.length ? `<h2 style="font-size:28px">The battleground seats</h2>
      <p class="muted">Bonus: ${sec.points} each, plus ${pts.flip_bonus} for every flip you call.</p>
      ${tools(sec, ro, sec.races.some((r) => r.favored) ? '<button class="btn ghost small" data-fill="ratings">Fill Tilt, Lean and Likely by the ratings</button>' : "")}
      ${grouped(sec, ro, (s) => s.id + " · " + s.label.replace(" District", ""))}`
    : '<p class="muted">This pack has no rated seats to pick yet.</p>';
  return `<div class="grid"><div>
      <h2 style="font-size:28px">${esc(c.label)}</h2>
      <p class="muted">Worth ${sec.control_points}.${body.majority ? ` ${body.majority} seats make a majority.` : ""}</p>
      <div class="bigpick">${big}</div>${tiebreakField(ro)}</div><div>${seats}</div></div>`;
}

function reviewTab() {
  const c = completion(), pts = app.data.points;
  const card = (x) => `<div class="stat ${x.done === x.total ? "done" : ""}"><div class="kicker">${esc(x.name)}</div>
      <div class="big">${x.done}<span class="muted" style="font-size:18px"> of ${x.total}</span></div>
      <div class="muted" style="font-size:13.5px">${x.key === "tiebreak" ? "Breaks ties." : x.required ? "Required." : "Bonus."}</div>
      <div class="bar"><span style="width:${x.total ? (100 * x.done) / x.total : 0}%"></span></div></div>`;
  const maps = sections().filter((s) => s.kind === "map").map((s) => `<div><p class="kicker" style="margin-top:14px">${esc(s.name)}</p>${bodyTally(s, app.picks)}${mapSvg(s, app.picks, true)}</div>`);
  return `<div class="cards">${c.parts.map(card).join("")}</div>
    ${maps.length ? `<div class="grid">${maps.join("")}</div>` : ""}
    <p class="muted">${pts.flip_bonus ? `A correct pick that flips a seat earns ${pts.flip_bonus} extra. ` : ""}Anything left blank scores nothing.
    ${app.data.hidden ? "Everyone's picks stay hidden until predictions close." : "Other people can see your picks as soon as you save."}</p>`;
}

// What a page's clock says about refreshing: a saved copy (web/export.py) doesn't change until it's exported again.
function refreshNote() {
  return window.DESK_STATIC ? "Saved copy" : "Updates every 20 seconds";
}

function predictView() {
  const d = app.data, pack = d.races.pack;
  const draft = pack.example ? '<div class="banner">This data pack is a template with placeholder names. It shows what a single contest looks like.</div>' : "";
  if (!d.me) {
    return `<section class="hero"><span class="kicker">The prediction game · ${esc(pack.name)}</span>
      <h1>Call every race before the polls close.</h1>
      <p>${esc(pack.summary)} Your picks are scored as the desk calls each race on the night.</p>
      ${window.DESK_STATIC ? `<p class="muted">This is a saved copy of the site, so picks can't be made here. Sign in on the live site.</p>`
        : `<a class="btn" href="/login">Sign in with Discord</a>`}
      ${d.dev ? ' <a class="btn ghost" href="/login?as=1&name=Dev%20user">Dev sign-in</a>' : ""}
      <p class="muted" style="font-size:14px;margin-top:14px">It asks Discord only who you are and whether you're in the server.</p></section>`;
  }
  if (!app.tab || (app.tab !== "review" && !sections().some((s) => s.key === app.tab))) app.tab = sections()[0]?.key || "review";
  const sec = sections().find((s) => s.key === app.tab);
  const body = !sec ? reviewTab() : sec.kind === "map" ? mapSection(sec) : sec.kind === "house" ? houseSection(sec) : listSection(sec);
  return `<div class="head"><div><span class="kicker">Your predictions · ${esc(pack.name)}</span><h1>${esc(d.me.name)}'s picks</h1></div>${countdown()}</div>
    ${draft}${d.open ? "" : '<div class="banner">Predictions are locked, so this is how your entry stands. The leaderboard shows how it is scoring.</div>'}
    ${tabBar()}<div id="tabbody">${body}</div>`;
}

function saveBar() {
  const bar = $("#savebar");
  if (app.route !== "predict" || !canEdit()) { bar.hidden = true; return; }
  const c = completion();
  const left = c.parts.filter((x) => x.done < x.total).map((x) => (x.total === 1 ? x.name.toLowerCase() : `${x.total - x.done} ${x.name}`));
  bar.hidden = false;
  bar.innerHTML = `<div class="in"><div class="meter"><div class="bar"><span style="width:${(100 * c.share).toFixed(1)}%"></span></div>
      <small><b>${Math.round(100 * c.share)}% filled in.</b> ${left.length ? "Still open: " + esc(left.join(", ")) + "." : "Every part is done."}</small></div>
    ${dirty() ? '<span class="dirty">Unsaved changes</span>' : '<span class="muted" style="font-size:14px">Saved</span>'}
    <button class="btn" id="save" ${dirty() ? "" : "disabled"}>Save predictions</button></div>`;
}

function setPick(id, party) {
  if (!canEdit()) return;
  if (app.picks[id] === party) delete app.picks[id]; else app.picks[id] = party;
  redraw();
}

function cyclePick(id) {
  const opts = raceById()[id].options.map((o) => o.party), cur = app.picks[id];
  const next = cur === undefined ? opts[0] : opts[opts.indexOf(cur) + 1];
  if (next) app.picks[id] = next; else delete app.picks[id];
  redraw();
}

async function save() {
  try {
    const entry = await api("/api/picks", { method: "PUT", body: JSON.stringify({ picks: app.picks, house_dem_seats: String(app.seats).trim() === "" ? null : app.seats }) });
    adopt(entry);
    toast("Saved. The board in Discord will show it.");
  } catch (err) { toast(err.message); }
  redraw();
}

function adopt(entry) {
  app.picks = { ...entry.picks };
  app.seats = entry.house_dem_seats ?? "";
  app.saved = snapshot();
}

// ------------------------------------------------------------------ leaderboards
const mark = (m) => (m === true ? '<span class="mark-ok">✓</span>' : m === false ? '<span class="mark-no">✗</span>' : '<span class="muted">…</span>');

function standings(rows, withServer) {
  const cats = app.data.races.categories, tb = app.data.races.tiebreaker;
  const cell = (r, c) => {
    const v = r.cats[c.key];
    if (c.ids) return v && v[3] ? mark(r.marks[c.ids[0]]) : '<span class="muted">–</span>';
    return v && v[3] ? `${v[0]}<span class="muted">/${v[0] + v[1]}</span>` : '<span class="muted">–</span>';
  };
  return `<div class="scroll"><table class="board"><thead><tr><th></th><th>Name</th>${withServer ? "<th>Server</th>" : ""}
      ${cats.map((c) => `<th>${esc(c.name)}</th>`).join("")}${app.data.points.flip_bonus ? "<th>Flips</th>" : ""}${tb ? "<th>Tiebreak</th>" : ""}<th>Points</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr ${withServer ? 'style="cursor:default"' : `data-entry="${esc(r.id)}"`}><td class="rank">${i + 1}</td><td class="name">${esc(r.name)}</td>
      ${withServer ? `<td style="text-align:left" class="muted">${esc(r.server)}</td>` : ""}${cats.map((c) => `<td>${cell(r, c)}</td>`).join("")}
      ${app.data.points.flip_bonus ? `<td>${r.flips}</td>` : ""}
      ${tb ? `<td>${r.guess == null ? '<span class="muted">–</span>' : r.guess + (r.miss == null ? "" : ` <span class="muted">(off ${r.miss})</span>`)}</td>` : ""}
      <td class="pts">${r.points}</td></tr>`).join("")}</tbody></table></div>`;
}

async function boardView() {
  const [b, g] = await Promise.all([api("/api/leaderboard"), api("/api/global").catch(() => null)]);
  const pack = app.data.races.pack;
  const global = !g || g.hidden ? "" : `<h2 style="font-size:28px;margin:36px 0 6px">The global board</h2>
    <p class="muted">${g.servers ? `Everyone from the ${g.servers} server${g.servers === 1 ? "" : "s"} that joined, scored on outlet calls and final results, not any one server's desk.` : "No server has joined the global board for this election yet."}
      ${g.joined ? "" : " This server hasn't joined: an admin can with <code>/globalboard on</code> in Discord."}</p>
    ${g.rows.length ? standings(g.rows, true) : ""}
    ${app.data.me && g.joined ? `<p><button class="btn ghost small" id="optout">${g.out ? "Put my entry back on the global board" : "Keep my entry off the global board"}</button></p>` : ""}`;
  if (!b.rows.length) return `<section class="hero"><span class="kicker">Leaderboard · ${esc(pack.name)}</span><h1>Nobody has saved predictions yet.</h1></section>${global}`;
  if (b.hidden) {
    return `<div class="head"><div><span class="kicker">Leaderboard · ${esc(pack.name)}</span><h1>Who's in so far</h1></div></div>
      <p class="muted">Picks are hidden until predictions close. This is how much of the game each person has filled in.</p>
      <div class="scroll"><table class="board"><thead><tr><th>Name</th><th>Still open</th><th>Filled in</th></tr></thead><tbody>
      ${b.rows.map((r) => `<tr style="cursor:default"><td class="name">${esc(r.name)}</td><td style="text-align:left" class="muted">${esc(r.missing.join(", ") || "nothing")}</td>
        <td class="pts">${Math.round(100 * r.share)}%</td></tr>`).join("")}</tbody></table></div>`;
  }
  return `<div class="head"><div><span class="kicker">Leaderboard · ${esc(pack.name)}</span><h1>The standings</h1></div>
      <div class="clock">${b.closed ? "Final" : `${b.open_races} races still to settle`}<b>${b.final_house_dem == null ? (app.data.races.tiebreaker ? "tiebreaker not settled" : "") : "tiebreaker answer: " + b.final_house_dem}</b></div></div>
    ${standings(b.rows, false)}
    <p class="muted" style="font-size:14px">Right picks out of races decided so far. Click a row to see that entry.</p><div id="entry"></div>${global}`;
}

async function showEntry(id) {
  const box = $("#entry");
  try {
    const e = await api(`/api/entry/${id}`);
    const all = sections().flatMap((s) => [...s.races, ...(s.control ? [s.control] : [])]);
    const wrong = all.filter((x) => e.marks[x.id] === false);
    const maps = sections().filter((s) => s.kind === "map").map((s) => `<div><p class="kicker">${esc(s.name)}</p>${bodyTally(s, e.picks)}${mapSvg(s, e.picks, true)}</div>`);
    const lists = sections().filter((s) => s.kind !== "map").flatMap((s) => [...(s.control ? [s.control] : []), ...(s.kind === "list" ? s.races : [])])
      .map((x) => raceRow(x, e.picks, true, e.marks, x.label)).join("");
    box.innerHTML = `<div class="entry"><span class="kicker">Entry</span><h2 style="font-size:32px;margin:6px 0 14px">${esc(e.name)}</h2>
      ${maps.length ? `<div class="grid">${maps.join("")}</div>` : ""}${lists}
      ${e.house_dem_seats == null ? "" : `<p><b>Tiebreaker:</b> ${e.house_dem_seats}</p>`}
      ${wrong.length ? `<p class="kicker" style="margin-top:18px">Missed so far</p>${wrong.map((x) => raceRow(x, e.picks, true, e.marks, x.label)).join("")}` : ""}</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ the night
function bodyCard(b) {
  const keys = Object.keys(b.counts).sort((x, y) => b.counts[y] - b.counts[x]);
  const left = keys[0], right = keys[1], rest = keys.slice(2);
  const pct = (k) => (100 * b.counts[k]) / b.total;
  const state = b.control ? `<span style="color:${esc(shared(b.control).color)}">${esc(shared(b.control).plural)} win, called</span>`
    : b.clinched ? `<span style="color:${esc(shared(b.clinched).color)}">${esc(shared(b.clinched).plural)} have a majority on the desk's calls</span>` : "Not settled";
  const n = (k) => `<span><span class="n" style="color:${esc(shared(k).color)}">${b.counts[k]}</span> ${esc(k)}</span>`;
  const bar = (k) => `<span style="width:${pct(k)}%;background:${esc(shared(k).color)}"></span>`;
  return `<div class="chamber"><span class="kicker">${esc(b.name)}</span><h2>${state}</h2>
    <div class="tally-line">${left ? n(left) : ""}${rest.map(n).join("")}<span class="muted">${b.uncalled} uncalled</span>${right ? n(right) : ""}</div>
    <div class="seatwrap"><div class="seats">${left ? bar(left) : ""}${rest.map(bar).join("")}<span class="gap"></span>${right ? bar(right) : ""}</div>
      <div class="mark" style="left:50%"></div><div class="marklab">${b.majority} for a majority</div></div>
    ${b.path ? `<p class="muted" style="margin:10px 0 0;font-size:14px">${esc(b.path)}</p>` : ""}</div>`;
}

async function nightView() {
  const n = await api("/api/night");
  const today = new Date().toDateString();
  const time = (t) => {
    const d = new Date(t * 1000), clock = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === today ? clock : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + clock;
  };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const feed = n.recent.map((x) => `<li><time>${time(x.t)}</time><span>${x.action === "uncall"
      ? `Call retracted in <b>${esc(x.label)}</b>` : `<b style="color:${esc(option(x.race, x.party).color)}">${esc(cap(x.name))}</b> wins <b>${esc(x.label)}</b>`}</span>
      <span>${x.action === "uncall" ? '<span class="tag out">RETRACTED</span>' : x.flip ? '<span class="tag flip">FLIP</span>' : ""}
      ${x.rated ? `<span class="tag">${esc(x.rated)}</span>` : ""}</span></li>`).join("");
  const cards = n.bodies.filter((b) => b.majority);
  const other = n.bodies.filter((b) => !b.majority).map((b) => `${esc(b.name)}: ${Object.entries(b.counts).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")} · ${b.uncalled} uncalled`);
  return `<div class="head"><div><span class="kicker">The night · ${esc(app.data.races.pack.name)}</span><h1>Where the desk stands</h1></div>
      <div class="clock">${refreshNote()}<b>as of ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</b></div></div>
    ${cards.length ? `<div class="two">${cards.map(bodyCard).join("")}</div>` : ""}
    <h2 style="font-size:26px;margin:28px 0 12px">Latest calls</h2>
    ${feed ? `<ul class="feed">${feed}</ul>` : '<p class="muted">Nothing has been called yet. Calls appear here as the desk makes them in Discord.</p>'}
    <p class="muted" style="font-size:14px">${other.join(" ")} Calls are the desk's, made by vote in Discord. They are not official results.</p>`;
}

// ------------------------------------------------------------------ drawing and events
function redraw() {
  const main = $("#app"), showing = app.route;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.route === app.route));
  clearInterval(app.nightTimer);
  saveBar();
  if (showing === "predict") {        // drawn at once, so a click's result is on screen before the next click
    main.innerHTML = predictView();
    return;
  }
  const load = async () => {
    try {
      const views = { board: boardView, night: nightView, results: resultsView, desk: deskView, model: modelView };
      const html = await views[showing]();
      if (app.route === showing) main.innerHTML = html;      // they may have moved on while it loaded
    } catch (err) { if (app.route === showing) main.innerHTML = `<div class="banner">${esc(err.message)}</div>`; }
  };
  load();
  if (["night", "results", "desk"].includes(showing)) app.nightTimer = setInterval(load, 20000);
}

function who() {
  const d = app.data, me = d.me, box = $("#who");
  const packs = d.packs.length > 1 ? `<select id="pack" aria-label="Election">${d.packs.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === d.pack ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>` : "";
  const servers = d.servers.length > 1 ? `<select id="server" aria-label="Server">${d.servers.map((s) =>
    `<option value="${esc(s.id)}" ${s.id === d.server ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>` : "";
  if (window.DESK_STATIC) {       // a saved copy: nobody can sign in, and it says how old it is
    box.innerHTML = `<span class="muted">Saved copy · ${new Date(window.DESK_STATIC * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>`;
    return;
  }
  if (!me) { box.innerHTML = `${packs}<a class="btn small" href="/login">Sign in</a>`; return; }
  const img = me.avatar ? `<img alt="" src="https://cdn.discordapp.com/avatars/${esc(me.id)}/${esc(me.avatar)}.png?size=64">` : "";
  box.innerHTML = `${packs}${servers}${img}<span>${esc(me.name)}</span><button class="btn ghost small" id="logout">Sign out</button>`;
}

function route() {
  const r = location.hash.replace("#/", "");
  // before picks lock the game is the front page; once the night starts, the results are
  const front = app.data.has_model && app.data.now > Date.parse(app.data.deadline) / 1000 ? "results" : "predict";
  app.route = ["predict", "board", "night", "results", "desk", "model"].includes(r) ? r : front;
  redraw();
}

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-tab],[data-party],[data-state],[data-fill],[data-clear],[data-entry],#save,#logout,#optout");
  if (!el) return;
  if (el.dataset.tab) { app.tab = el.dataset.tab; redraw(); window.scrollTo({ top: 0 }); }
  else if (el.dataset.party) setPick(el.dataset.race, el.dataset.party);
  else if (el.dataset.state) { if (canEdit() && !el.closest(".readonly")) cyclePick(el.dataset.state); }
  else if (el.dataset.fill) {
    const [what, key] = el.dataset.fill.split(":");
    const sec = key && sections().find((s) => s.key === key);
    const ids = sec ? new Set(sec.races.map((r) => r.id)) : null;
    let n = 0;
    for (const [id, party] of Object.entries(app.data.fills[what])) {
      if (!app.picks[id] && (!ids || ids.has(id))) { app.picks[id] = party; n++; }
    }
    toast(n ? `Filled ${n} race${n === 1 ? "" : "s"}. Your own picks were left alone.` : "Nothing to fill: those are all picked.");
    redraw();
  } else if (el.dataset.clear) {
    for (const r of sections().find((s) => s.key === el.dataset.clear).races) delete app.picks[r.id];
    redraw();
  } else if (el.dataset.entry) showEntry(el.dataset.entry);
  else if (el.id === "save") save();
  else if (el.id === "optout") {
    const out = !el.textContent.startsWith("Put");
    await api("/api/global/me", { method: "POST", body: JSON.stringify({ out }) });
    toast(out ? "Your entry is off the global board." : "Your entry is back on the global board.");
    redraw();
  } else if (el.id === "logout") { await api("/logout", { method: "POST" }); location.reload(); }
});

document.addEventListener("change", async (ev) => {
  if (ev.target.id !== "pack" && ev.target.id !== "server") return;
  if (dirty() && !confirm("You have unsaved picks. Switch anyway?")) { who(); return; }
  await api("/api/choose", { method: "POST", body: JSON.stringify({ [ev.target.id]: ev.target.value }) });
  app.saved = snapshot();             // nothing to warn about on the way out
  location.reload();
});

document.addEventListener("input", (ev) => {
  if (ev.target.id === "seats") { app.seats = ev.target.value; saveBar(); }
});

// a state on the map and its row in the list light up together
document.addEventListener("mouseover", (ev) => {
  const el = ev.target.closest("[data-state],[data-row]");
  document.querySelectorAll(".hot").forEach((x) => x.classList.remove("hot"));
  if (!el) return;
  const id = el.dataset.state || el.dataset.row;
  document.querySelector(`[data-state="${id}"]`)?.classList.add("hot");
  document.querySelector(`[data-row="${id}"]`)?.classList.add("hot");
});

window.addEventListener("beforeunload", (ev) => { if (dirty()) { ev.preventDefault(); ev.returnValue = ""; } });
window.addEventListener("hashchange", route);

(async function start() {
  try {
    app.data = await api("/api/state");
    document.title = `Decision Desk · ${app.data.races.pack.name}`;
    if (app.data.entry) adopt(app.data.entry);
    if (app.data.me && app.data.open && !app.data.entry.saved_at) {
      Object.assign(app.picks, app.data.fills.safe);       // a first visit starts with the safe races filled, like /predict
    }
    who();
    route();
    setInterval(() => { if (app.route === "predict" && app.data.me) { const c = $(".clock"); if (c) c.outerHTML = countdown(); } }, 30000);
  } catch (err) {
    $("#app").innerHTML = `<div class="banner">Couldn't load the desk: ${esc(err.message)}</div>`;
  }
})();
