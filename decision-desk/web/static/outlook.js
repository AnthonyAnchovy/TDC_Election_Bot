/* The results pages, drawn from /api/outlook (outlook.py):
     Results        for anyone: who is winning, how sure, in plain words
     Analyst desk   for people who read margins: ranges, the vote still out, swing, what to watch
     The model      how the forecast did on the replayed elections
   Uses app.js's helpers ($, esc, api, option, shared). Plain browser JavaScript, no build step. */
"use strict";

const ox = { open: null, sort: "close", last: null };

/* The outlook, or the last one this browser saw if the server can't be reached: a night's numbers shouldn't
   vanish because of one failed request. */
async function outlookData() {
  try {
    const o = await api("/api/outlook");
    try { localStorage.setItem("desk-outlook", JSON.stringify(o)); } catch (_e) { /* private window: fine */ }
    ox.last = o;
    return o;
  } catch (err) {
    let o = ox.last;
    if (!o) { try { o = JSON.parse(localStorage.getItem("desk-outlook")); } catch (_e) { o = null; } }
    if (!o) throw err;
    return { ...o, stale: true, stale_why: "This browser couldn't reach the site" };
  }
}

// ------------------------------------------------------------------ small pieces
const sideColor = (r, d) => (d ? option(r.id, r.d_key).color || "var(--d)" : shared("R").color || "var(--r)");
const lastName = (n) => surname(n || "");
const pts = (m, digits = 1) => (Math.abs(m) * 100).toFixed(digits);
const lean = (r, m, digits = 1) => (m == null ? "n/a" : Math.abs(m) < 0.0005 ? "Even" : `${esc(lastName(m > 0 ? r.d_name : r.r_name))} +${pts(m, digits)}`);
const dr = (m, digits = 1) => (m == null ? "n/a" : Math.abs(m) < 0.0005 ? "even" : `${m > 0 ? "D" : "R"}+${pts(m, digits)}`);
const CALL_AT = 0.99;     // the chance the desk's own bot calls at (autocaller.CALL_AT)
const pctTxt = (p) => (p == null ? "n/a" : p > 0.995 && p < 1 ? ">99%" : p < 0.005 && p > 0 ? "<1%" : `${Math.round(p * 100)}%`);
const votesTxt = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}K` : Math.round(v).toLocaleString());
const clockTxt = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const ageTxt = (s) => (s == null ? "never" : s < 90 ? `${Math.round(s)} sec ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} hr ago` : `${Math.round(s / 86400)} days ago`);

function staleBanner(o) {
  const bits = [];
  if (o.stale) bits.push(`<div class="banner"><b>Showing the last numbers received${o.at ? ` (${clockTxt(o.at)})` : ""}.</b> ${esc(o.stale_why || "")}. The page keeps trying.</div>`);
  return bits.join("");
}

/* Who is likelier to win, as one bar: the left side's share in its colour, the right side's in theirs. */
function chanceBar(r) {
  const p = r.win_d, d = Math.round(p * 100);
  const w = Math.max(2, Math.min(98, p * 100));
  return `<div class="chance" role="img" aria-label="${esc(r.d_name)} ${pctTxt(p)}, ${esc(r.r_name)} ${pctTxt(1 - p)}">
      <span style="width:${w}%;background:${esc(sideColor(r, true))}"></span><span style="width:${100 - w}%;background:${esc(sideColor(r, false))}"></span></div>
    <div class="chance-lab"><span><b>${pctTxt(p)}</b> ${esc(lastName(r.d_name))}</span><span>${esc(lastName(r.r_name))} <b>${pctTxt(1 - p)}</b></span></div>`;
}

/* A forecast on a fixed scale from R+30 to D+30: the 90% range as a band, the forecast as a dot. */
function strip(r, span = 0.30) {
  const x = (m) => 50 + 50 * Math.max(-1, Math.min(1, m / span));
  const lo = x(r.lo), hi = x(r.hi), at = x(r.margin);
  const col = sideColor(r, r.margin >= 0);
  return `<div class="strip" title="Forecast ${dr(r.margin)}; 90% range ${dr(r.lo)} to ${dr(r.hi)}">
    <i class="zero"></i><i class="band" style="left:${lo}%;width:${Math.max(hi - lo, 0.8)}%;background:${esc(col)}"></i>
    <i class="dot" style="left:${at}%;background:${esc(col)}"></i></div>`;
}

function seatChart(c) {
  const keys = Object.keys(c.dist).map(Number).sort((a, b) => a - b);
  if (keys.length < 2) return "";
  const max = Math.max(...keys.map((k) => c.dist[k]));
  const bars = keys.map((k) => `<div class="seatbar" title="${k} seats: ${pctTxt(c.dist[k])}">
      <i style="height:${Math.max(3, (100 * c.dist[k]) / max)}%;background:${k >= c.need ? "var(--d)" : "var(--r)"}"></i><small>${k}</small></div>`).join("");
  return `<div class="seatdist" role="img" aria-label="How many seats Democrats could end with">${bars}</div>
    <p class="muted small">How often each seat count comes up when the forecast is run 8,000 times. ${c.need} wins control.</p>`;
}

// ------------------------------------------------------------------ Results: for anyone
function raceCard(r) {
  const side = Math.max(r.win_d, 1 - r.win_d);
  const chip = r.called ? '<span class="tag ok">CALLED</span>' : r.stage === "prior" ? '<span class="tag">NO VOTES YET</span>'
    : side < 0.7 ? '<span class="tag flip">CLOSE</span>' : "";
  const counted = r.counted ? `${pctTxt(r.counted_share)} counted` : "polls not closed or no results yet";
  return `<article class="rcard">
    <div class="rcard-top"><h3>${esc(r.place)}</h3>${chip}</div>
    ${chanceBar(r)}
    <p>${esc(r.plain)}</p>
    <div class="muted small">${esc(counted)}${r.bellwether ? " · watched as an early sign of the national mood" : ""}</div>
  </article>`;
}

async function resultsView() {
  const o = await outlookData();
  if (!o.has_model) {
    return `<div class="hero"><span class="kicker">Results</span><h1>No forecast for this election</h1>
      <p>This election is run by hand: the desk's calls are on the <a href="#/night">Calls</a> page.</p></div>`;
  }
  const order = [...o.races].sort((a, b) => Math.abs(a.win_d - 0.5) - Math.abs(b.win_d - 0.5));
  const close = order.filter((r) => !r.called && Math.max(r.win_d, 1 - r.win_d) < 0.95);
  const rest = order.filter((r) => !close.includes(r));
  const hero = o.chambers.map((c) => `<section class="bigread">
      <span class="kicker">${esc(c.name)}</span>
      <h1>${esc(c.plain)}</h1>
      <div class="chance big" role="img" aria-label="Democrats ${pctTxt(c.chance_d)}"><span style="width:${Math.max(2, Math.min(98, c.chance_d * 100))}%;background:var(--d)"></span><span style="width:${Math.max(2, Math.min(98, 100 - c.chance_d * 100))}%;background:var(--r)"></span></div>
      <div class="chance-lab"><span><b>${pctTxt(c.chance_d)}</b> Democrats</span><span>Republicans <b>${pctTxt(1 - c.chance_d)}</b></span></div>
      <p class="lede">Most likely outcome: Democrats and allied independents hold <b>${c.median_d}</b> of ${c.seats} seats. It takes ${c.need} to control it.</p>
      ${seatChart(c)}</section>`).join("");
  const stage = { before: "Before the count", night: "Counting now", after: "The count is all but done" }[o.stage] || "";
  return `${staleBanner(o)}
    <div class="head"><div><span class="kicker">Results · ${esc(o.pack_name || "")}</span></div>
      <div class="clock">${esc(stage)}<b>as of ${clockTxt(o.at || Date.now() / 1000)}</b></div></div>
    ${hero}
    <details class="explain"><summary>How to read this page</summary>
      <p>The percentages are <b>chances of winning</b>, not vote shares. 70% means: in 10 nights that looked like this one, that side wins about 7. Early in the night chances can move a lot, because the first votes counted are rarely typical of the whole state.</p>
      <p>"Our estimate of the final result" is where we think a race will end once every vote is counted, not who is ahead in the votes counted so far. The two often differ for hours.</p>
      <p>None of this is official. Races are called by the group in Discord, and results are certified by each state weeks later.</p></details>
    ${close.length ? `<h2 class="sect">Still in doubt</h2><div class="rgrid">${close.map(raceCard).join("")}</div>` : ""}
    <h2 class="sect">${close.length ? "Not in much doubt" : "Every race"}</h2>
    <div class="rgrid">${rest.map(raceCard).join("")}</div>
    <p class="muted small">Want the margins, ranges and county detail? That's the <a href="#/desk">Analyst desk</a>. How good is the forecast? See <a href="#/model">The model</a>.</p>`;
}

// ------------------------------------------------------------------ Analyst desk
/* What deserves attention right now, worked out from the numbers. Most useful first. */
function watchList(o) {
  const items = [];
  const live = o.races.filter((r) => r.stage === "live" && !r.called);
  for (const r of live) {
    const side = Math.max(r.win_d, 1 - r.win_d);
    if (r.raw_m != null && r.counted && (r.raw_m > 0) !== (r.margin > 0) && Math.abs(r.margin) > 0.003) {
      const top = r.left[0];
      items.push([3, `<b>${esc(r.place)}:</b> ${lean(r, r.raw_m)} in the count, but the forecast is ${lean(r, r.margin)} (${pctTxt(side)}). The vote still out decides it${top ? `, most of all ${esc(top.name)} (${votesTxt(top.left)} left, expected ${dr(top.left_margin)})` : ""}.`]);
    } else if (side < 0.9) {
      const need = r.need_share && r.need_share <= 1 ? ` ${esc(lastName(r.trailer === "D" ? r.d_name : r.r_name))} needs ${pctTxt(r.need_share)} of the ${votesTxt(r.remaining)} still out and is on pace for ${pctTxt(r.pace_share)}.` : "";
      items.push([2, `<b>${esc(r.place)}</b> is live at ${lean(r, r.margin)}, range ${dr(r.lo)} to ${dr(r.hi)}.${need}`]);
    }
    if (r.poll != null && r.data_weight > 0.6 && Math.abs(r.margin - r.poll) > 0.05) {
      items.push([1, `<b>${esc(r.place)}</b> is running ${pts(r.margin - r.poll)} points ${r.margin > r.poll ? "better for the Democratic side" : "better for Republicans"} than its final poll (${dr(r.poll)}).`]);
    }
    if (r.classic_m != null && Math.abs(r.classic_m - r.margin) > 0.025) {
      items.push([1, `<b>${esc(r.place)}:</b> the forecast (${dr(r.margin)}) and the classic county projection (${dr(r.classic_m)}) disagree by ${pts(r.classic_m - r.margin)} points, usually a sign that the first votes counted lean one way${r.early_lean != null && Math.abs(r.early_lean) > 0.03 ? ` (early counts are running ${dr(r.early_lean)} against final ones)` : ""}.`]);
    }
  }
  const callable = live.filter((r) => Math.max(r.win_d, 1 - r.win_d) >= CALL_AT && r.data_weight >= 0.5);
  if (callable.length) items.push([4, `<b>Ready to call on the forecast's rule</b> (99% or better, results leading): ${callable.map((r) => `${esc(r.place)} for ${esc(lastName(r.margin > 0 ? r.d_name : r.r_name))}`).join(", ")}.`]);
  const waiting = o.races.filter((r) => r.stage === "prior" && !r.called);
  if (waiting.length && o.national && o.national.counties) {
    items.push([1, `<b>Still waiting on ${waiting.length} state${waiting.length === 1 ? "" : "s"}:</b> ${waiting.map((r) => `${esc(r.state)} ${dr(r.margin)}`).join(", ")}. Those numbers are each state's poll and baseline moved by tonight's swing elsewhere (${dr(o.national.national_swing)}) and the polls' miss so far (${dr(o.national.poll_miss)}).`]);
  }
  const f = o.feeds || {};
  const bad = Object.entries(f.errors || {}).filter(([, why]) => !/waiting for polls/.test(why));
  if (bad.length) items.push([5, `<b>Feed trouble:</b> ${bad.map(([st, why]) => `${esc(st)} (${esc(String(why).slice(0, 70))})`).join("; ")}. Numbers for those states are the last good pull; enter counties by hand with <code>/countyset</code> if it lasts.`]);
  const onBackup = f.on_backup || [];
  if (onBackup.length) items.push([3, `<b>Reading a backup source:</b> ${onBackup.map((b) => `${esc(b.state)} is on ${esc(b.using)} (${esc(b.first)} ${b.first_error ? "isn't answering" : b.first_changed_age == null ? "has nothing newer" : `last changed ${ageTxt(b.first_changed_age)}`})`).join("; ")}. The bot goes back by itself when the first source moves again.`]);
  const held = Object.entries(f.held || {});
  if (held.length) items.push([3, `<b>Numbers held back by the intake check:</b> ${held.map(([st, why]) => `${esc(st)}: ${esc(String(why).slice(0, 160))}`).join("; ")}. A held number goes through if it's still there three pulls running.`]);
  if (!items.length) items.push([0, o.stage === "after" ? "Nothing is in doubt: every race is called or past the point where the outstanding vote could change it." : "Nothing needs attention yet."]);
  return items.sort((a, b) => b[0] - a[0]).map((x) => `<li>${x[1]}</li>`).join("");
}

function trendChart(r) {
  const p = r.trend || [];
  if (p.length < 3) return '<p class="muted small">The night\'s chart appears once this race has a few readings.</p>';
  const W = 640, H = 200, L = 46, R = 70, T = 12, B = 24;
  const t0 = p[0][0], t1 = p[p.length - 1][0], span = Math.max(t1 - t0, 60);
  const top = Math.max(5, ...p.map((q) => Math.abs(q[1]))) * 1.15;
  const x = (t) => L + ((W - L - R) * (t - t0)) / span, y = (m) => T + ((H - T - B) * (top - m)) / (2 * top);
  const step = top <= 8 ? 2 : top <= 20 ? 5 : 10;
  let grid = "";
  for (let g = -Math.floor(top / step) * step; g <= top; g += step) {
    grid += `<line x1="${L}" x2="${W - R}" y1="${y(g)}" y2="${y(g)}" class="${g === 0 ? "zero" : "grid"}"/><text x="${L - 6}" y="${y(g)}" class="ax" text-anchor="end" dominant-baseline="central">${g === 0 ? "Even" : `${g > 0 ? "D" : "R"}+${Math.abs(g)}`}</text>`;
  }
  const line = p.map((q, i) => `${i ? "L" : "M"}${x(q[0]).toFixed(1)},${y(q[1]).toFixed(1)}`).join(" ");
  const end = p[p.length - 1];
  const hit = p.map((q, i) => { const a = i ? (x(p[i - 1][0]) + x(q[0])) / 2 : L, b = i < p.length - 1 ? (x(q[0]) + x(p[i + 1][0])) / 2 : W - R;
    return `<rect x="${a.toFixed(1)}" y="${T}" width="${Math.max(b - a, 1).toFixed(1)}" height="${H - T - B}" class="hit"><title>${clockTxt(q[0])}: ${dr(q[1] / 100)}, ${votesTxt(q[2])} votes counted${q[4] != null ? `, ${pctTxt(q[4])} chance` : ""}</title></rect>`; }).join("");
  return `<svg class="trend" viewBox="0 0 ${W} ${H}" role="img" aria-label="Projected margin through the night">${grid}
    <path d="${line}" class="line"/><circle cx="${x(end[0])}" cy="${y(end[1])}" r="4" class="pt"/>
    <text x="${x(end[0]) + 8}" y="${y(end[1])}" class="lab" dominant-baseline="central">${dr(end[1] / 100)}</text>
    <text x="${L}" y="${H - 6}" class="ax">${clockTxt(t0)}</text><text x="${W - R}" y="${H - 6}" class="ax" text-anchor="end">${clockTxt(t1)}</text>${hit}</svg>
    <p class="muted small">The margin the desk's field showed at each moment of the night. Hover for the time and votes counted.</p>`;
}

function raceDetail(r) {
  const types = r.types.filter((t) => t.counties).map((t) => `<tr><td>${esc(t.type)}</td><td>${pctTxt(t.share)}</td><td>${t.complete}/${t.counties}</td>
      <td>${dr(t.breakeven)}</td><td>${t.swing == null ? '<span class="muted">none in</span>' : dr(t.swing)}</td><td>${t.t_index == null ? "" : Math.round(t.t_index * 100)}</td></tr>`).join("");
  const left = r.left.map((c) => `<tr><td>${esc(c.name)}</td><td>${votesTxt(c.left)}</td><td>${pctTxt(c.share)}</td><td>${dr(c.left_margin)}</td><td>${c.margin == null ? '<span class="muted">nothing yet</span>' : dr(c.margin)}</td></tr>`).join("");
  const keys = r.keys.map((k) => { const m = k.final ?? k.margin; const vs = m == null ? null : m - k.breakeven;
    return `<tr title="${esc(k.why || "")}"><td>${esc(k.name)}</td><td>${pctTxt(k.share)}</td><td>${dr(k.breakeven)}</td><td>${k.margin == null ? '<span class="muted">nothing yet</span>' : dr(k.margin)}</td>
      <td>${k.margin == null ? "" : pctTxt(k.in)}</td><td>${vs == null ? "" : `<b>${dr(vs)}</b>`}</td></tr>`; }).join("");
  const tiltNames = { black: "Black share", hisp: "Hispanic share", ba: "college degrees", logpop: "county size", m24: "Democratic lean last time", split: "lean last time" };
  const tilt = r.tilts ? Object.entries(r.tilts)      // a pack can choose its own features: one without a name here shows as it's written
    .map(([k, v]) => [tiltNames[k] || esc(k), v]).filter(([, v]) => Math.abs(v) >= 0.004).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .map(([name, v]) => `${name} ${dr(v)}`).join(" · ") : "";
  const internals = [
    r.prior_m != null ? `Start (before results): ${dr(r.prior_m)} ± ${pts(1.645 * (r.prior_sd || 0))}${r.poll != null ? `, from the final poll ${dr(r.poll)}` : ""} and the swing in other states` : "",
    `Results carry ${pctTxt(r.data_weight)} of the forecast; the start carries the rest`,
    r.swing != null ? `Swing from the baseline: ${dr(r.swing)} (needs ${dr(r.needed_swing)} for a tie)` : "",
    r.early_lean != null && r.stage === "live" ? `Counties still counting are running ${dr(r.early_lean)} against where finished ones ended (the lean of early votes)` : "",
    tilt ? `Where the swing is bigger, per typical difference between counties: ${tilt}` : "",
    r.classic_m != null ? `Classic county projection: ${dr(r.classic_m)}` : "",
    r.turnout_vs_22 ? `Turnout in finished counties: ${pctTxt(r.turnout_vs_22)} of 2022` : "",
    `${r.finished} of ${r.counties} counties finished`,
  ].filter(Boolean).map((x) => `<li>${x}</li>`).join("");
  return `<tr class="detail"><td colspan="9"><div class="detail-in">
      <div><h4>The night in this race</h4>${trendChart(r)}
        <h4>Inside the forecast</h4><ul class="plainlist">${internals}</ul></div>
      <div><h4>Where the vote still out is</h4>${left ? `<table class="mini"><thead><tr><th>County</th><th>Votes left</th><th>Of what's left</th><th>Expected</th><th>So far</th></tr></thead><tbody>${left}</tbody></table>` : '<p class="muted small">Nothing of any size is left.</p>'}
        <h4>Key counties against the tie line</h4>${keys ? `<table class="mini"><thead><tr><th>County</th><th>Of state</th><th>Tie line</th><th>Now</th><th>In</th><th>Ahead by</th></tr></thead><tbody>${keys}</tbody></table>
        <p class="muted small">Tie line: the margin this county gives in a tied state. "Ahead by" is the Democratic side's margin over it (its expected final margin once the county is counting). Hover a row for why the county matters.</p>` : ""}
        <h4>By kind of county</h4><table class="mini"><thead><tr><th>Kind</th><th>Of vote</th><th>Done</th><th>Tie line</th><th>Swing</th><th>Turnout index</th></tr></thead><tbody>${types}</tbody></table></div>
    </div></td></tr>`;
}

async function deskView() {
  const o = await outlookData();
  if (!o.has_model) return `<div class="hero"><span class="kicker">Analyst desk</span><h1>No forecast for this election</h1><p>This pack has no County Watch model.</p></div>`;
  const sorts = { close: (a, b) => Math.abs(a.win_d - 0.5) - Math.abs(b.win_d - 0.5), margin: (a, b) => b.margin - a.margin,
    left: (a, b) => b.remaining - a.remaining, state: (a, b) => a.place.localeCompare(b.place) };
  const races = [...o.races].sort(sorts[ox.sort] || sorts.close);
  const rows = races.map((r) => {
    const side = Math.max(r.win_d, 1 - r.win_d), lead = r.margin >= 0;
    const need = r.trailer && r.need_share != null ? (r.need_share > 1 ? "out of reach" : `${pctTxt(r.need_share)} / ${pctTxt(r.pace_share)}`) : "";
    const status = r.called ? `<span class="tag ok">CALLED ${esc(r.called)}</span>` : r.stage === "prior" ? '<span class="tag">PRIOR</span>' : side >= CALL_AT && r.data_weight >= 0.5 ? '<span class="tag flip">CALLABLE</span>' : '<span class="tag">LIVE</span>';
    return `<tr class="rrow${ox.open === r.id ? " open" : ""}" data-ox-open="${esc(r.id)}" tabindex="0">
      <td><b>${esc(r.place)}</b><span class="meta">${esc(lastName(r.d_name))} v ${esc(lastName(r.r_name))}</span></td>
      <td class="num"><i class="sw" style="background:${esc(sideColor(r, lead))}"></i>${lean(r, r.margin)}</td>
      <td class="wide">${strip(r)}<span class="meta">${dr(r.lo)} to ${dr(r.hi)}</span></td>
      <td class="num">${pctTxt(side)}</td>
      <td class="num">${r.counted ? pctTxt(r.counted_share) : "0%"}<span class="meta">${r.counted ? dr(r.raw_m) + " so far" : ""}</span></td>
      <td class="num">${r.remaining > 0.5 ? votesTxt(r.remaining) : "0"}<span class="meta">${need}</span></td>
      <td class="num">${r.swing == null ? "" : dr(r.swing)}</td>
      <td class="num">${r.poll == null ? '<span class="muted">none</span>' : dr(r.poll)}<span class="meta">${r.poll != null && r.stage === "live" ? `miss ${dr(r.margin - r.poll)}` : ""}</span></td>
      <td>${status}</td></tr>${ox.open === r.id ? raceDetail(r) : ""}`;
  }).join("");
  const c = o.chambers[0], n = o.national || {}, f = o.feeds || {};
  const bad = Object.entries(f.errors || {}).filter(([, why]) => !/waiting for polls/.test(why)).length;
  const tiles = [
    c ? ["Senate control", pctTxt(Math.max(c.chance_d, 1 - c.chance_d)), `${c.chance_d >= 0.5 ? "Democrats" : "Republicans"} · median ${c.median_d} D seats, need ${c.need}`] : null,
    ["Swing from 2024", n.swing == null ? "n/a" : dr(n.swing), n.counties ? `${n.counties.toLocaleString()} finished counties in ${n.states} states` : "no county has finished"],
    ["Polls' shared miss", n.poll_miss == null ? "n/a" : dr(n.poll_miss), "applied to states still waiting"],
    ["Turnout", n.turnout ? `${pctTxt(n.turnout)}` : "n/a", "of 2022, in finished counties"],
    ["Feeds", bad ? `${bad} failing` : (f.age || 0) > 30 * 60 ? "Quiet" : "All answering", `last pull ${ageTxt(f.age)}${(f.on_backup || []).length ? ` · ${f.on_backup.length} on a backup source` : ""}${Object.keys(f.held || {}).length ? ` · ${Object.keys(f.held).length} with numbers held` : ""}`],
  ].filter(Boolean).map(([k, v, s]) => `<div class="stat"><span class="kicker">${esc(k)}</span><div class="big">${esc(v)}</div><div class="muted small">${esc(s)}</div></div>`).join("");
  const byType = (n.by_type || []).map((t) => `<tr><td>${esc(t.type)}</td><td>${dr(t.swing)}</td><td>${t.counties}</td><td>${t.turnout ? pctTxt(t.turnout) : ""}</td></tr>`).join("");
  const early = (o.early || []).slice(0, 14).map((e) => `<tr><td>${esc(e.state)}</td><td>${votesTxt(e.votes)}</td><td>${pctTxt(e.share22)}</td><td>${e.reg_gap == null ? "" : dr(e.reg_gap)}</td></tr>`).join("");
  const th = (key, label) => `<th><button class="sortb${ox.sort === key ? " on" : ""}" data-ox-sort="${key}">${label}</button></th>`;
  return `${staleBanner(o)}
    <div class="head"><div><span class="kicker">Analyst desk · ${esc(o.pack_name || "")}</span><h1>Where every race is heading</h1></div>
      <div class="clock">${refreshNote()}<b>as of ${clockTxt(o.at || Date.now() / 1000)}</b></div></div>
    <div class="cards tiles">${tiles}</div>
    <section class="watch"><h2 class="sect">What to watch</h2><ul class="plainlist">${watchList(o)}</ul></section>
    <h2 class="sect">Races</h2>
    <div class="scroll"><table class="board races"><thead><tr>${th("state", "Race")}${th("margin", "Forecast")}<th>90% range, R+30 to D+30</th>${th("close", "Chance")}<th>Counted</th>${th("left", "Votes left")}<th>Swing</th><th>Final poll</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="muted small">Click a race for its counties. Forecast: the estimated final two-party margin. Chance: of the side shown winning. Votes left: the model's estimate, with the share the trailing side needs / is on pace for. Swing: from the 2024 baseline at midterm turnout.</p>
    ${sourceTable(f)}
    <div class="two" style="margin-top:28px">
      <div><h2 class="sect">Swing by kind of county, all states</h2>${byType ? `<table class="mini"><thead><tr><th>Kind</th><th>Swing</th><th>Finished</th><th>Turnout v 2022</th></tr></thead><tbody>${byType}</tbody></table>` : '<p class="muted">No county has finished yet.</p>'}</div>
      <div><h2 class="sect">Early vote</h2>${early ? `<table class="mini"><thead><tr><th>State</th><th>Voted early</th><th>Of 2022 turnout</th><th>Party gap</th></tr></thead><tbody>${early}</tbody></table>` : '<p class="muted">No early vote on file.</p>'}</div>
    </div>`;
}

function sourceTable(f) {
  const states = Object.entries(f.sources || {});
  if (!states.length) return "";
  const when = (a) => (a == null ? "not yet" : ageTxt(a));
  const rows = states.map(([st, list]) => list.map((s, i) => `<tr><td>${i ? "" : esc(st)}</td><td>${s.order}. ${esc(s.name)}${s.in_use ? " <b>(in use)</b>" : ""}</td><td>${when(s.answered_age)}</td><td>${when(s.changed_age)}</td><td>${s.error ? esc(String(s.error).slice(0, 80)) : ""}</td></tr>`).join("")).join("");
  return `<details style="margin-top:28px"><summary><b>Where each state's numbers are coming from</b></summary>
    <div class="scroll"><table class="mini wide"><thead><tr><th>State</th><th>Source, in the order it's trusted</th><th>Last answered</th><th>Last changed</th><th>Trouble</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="muted small">The bot reads the first source that has votes. It moves down the list when that one is blocked, down, empty, or hasn't changed in 10 minutes while a later one is still counting.</p></details>`;
}

// ------------------------------------------------------------------ The model
async function modelView() {
  const m = await api("/api/model");
  const b = m.backtest;
  const tbl = (rows) => `<div class="scroll"><table class="mini wide"><thead><tr><th>Vote counted</th><th>Miss, before</th><th>Miss, now</th><th>In 90% range</th><th>Wrong side, before</th><th>Wrong side, now</th><th>Range width</th></tr></thead><tbody>${
    rows.map((r) => `<tr><td>${esc(r.bin)}</td><td>${r.old_miss.toFixed(1)} pts</td><td><b>${r.new_miss.toFixed(1)} pts</b></td><td>${pctTxt(r.new_in)}</td><td>${(100 * r.old_wrong).toFixed(1)}%</td><td><b>${(100 * r.new_wrong).toFixed(1)}%</b></td><td>${r.width.toFixed(0)} pts</td></tr>`).join("")}</tbody></table></div>`;
  const calls = (c) => `The forecast's calling rule called ${c.called} of ${c.races} races, ${c.wrong} wrong; half came with under ${pctTxt(c.median_share)} of the vote counted. Of ${c.close_races} races that ended inside 3 points it called ${c.close_called} and left the rest alone.`;
  const body = !b ? '<div class="banner">The backtest hasn\'t been run on this copy. Run <code>python backtest.py --json web/static/backtest.json</code>.</div>' : `
    <h2 class="sect">All ${b.years.length} elections together</h2>${tbl(b.all)}<p>${calls(b.calls.all)}</p>
    ${b.years.map((y) => `<h2 class="sect">${y}${y === 2026 ? " (the bot's own night)" : ""}</h2>${tbl(b.by_year[y])}<p class="muted">${calls(b.calls[y])}</p>`).join("")}
    <p class="muted small">${b.nights} simulated nights per election. "Before" is the county projection the bot ran on election night 2026; "now" is the forecast. Miss is the average distance from the final margin. Wrong side is how often the wrong candidate was shown ahead.</p>`;
  return `<div class="hero"><span class="kicker">The model</span><h1>How the forecast works, and how it has done</h1>
      <p>It estimates where each race will finish, not who is ahead right now, and says how sure it is.</p></div>
    <div class="prose">
      <h2 class="sect">In plain words</h2>
      <ol>
        <li><b>It starts from a guess.</b> Before votes arrive, each state starts at its final poll and its last result, adjusted as other states report: if the polls are missing everywhere tonight, a state still waiting is moved too.</li>
        <li><b>It learns from finished counties.</b> A county that has finished counting shows how far that kind of place moved since last time. Counties are compared by who lives there (race, education, size, how they voted before), across every state at once.</li>
        <li><b>It distrusts early counts.</b> The first votes out of a county are usually one voting method, and lean one way. A half-counted county only moves the forecast as far as it has earned.</li>
        <li><b>It fills in the rest.</b> Every county still counting gets an estimated final result, and they are added up into a statewide margin with a range around it.</li>
        <li><b>It turns the range into a chance.</b> If the whole range sits on one side, that side is very likely to win. The desk's own bot calls a race when that chance reaches 99% and results, not the starting guess, are doing the work.</li>
      </ol>
      <h2 class="sect">How it was tested</h2>
      <p>Five elections were replayed county by county: 2018 and 2020 from the 2016 results, 2022 and 2024 from the 2020 results, and 2026 from the county results the bot was holding when the count ended. Nobody recorded which county reported when, so the order is simulated: small counties first, early votes leaning one way, some states without precinct counts. All five have shaped the model, so none is an untouched test.</p>
      <p>One check was kept clean: how wide the ranges are. Fitted on 2018 to 2024 alone, the ranges would have been narrower. On 2026, which that fit never saw, they held the result 90% of the time as they should, but missed by a wide margin twice as often as they should, and made one wrong call in 20 replays of the night. The ranges in use are wider: on 2026 they made no wrong call, and in the four other years they cost about one call in 500.</p>
      ${body}
      <h2 class="sect">What it can't do yet</h2>
      <ul>
        <li>It covers the Senate races County Watch models. House seats and governors still run on ratings and the desk's calls. A version for races known only by their totals was tested and didn't beat the simpler rule the House board uses.</li>
        <li>The replay tests the arithmetic, not the feeds. A real night adds stale files, corrections and sites that block the bot. From now on the bot saves every feed response, so the next election can be replayed exactly.</li>
        <li>Alaska is projected by voting method, as before, with the starting guess blended in.</li>
        <li>Five elections is a small sample. The ranges are deliberately wide early in the night.</li>
      </ul>
    </div>`;
}

document.addEventListener("click", (ev) => {
  const sort = ev.target.closest("[data-ox-sort]");
  if (sort) { ox.sort = sort.dataset.oxSort; redraw(); return; }
  const row = ev.target.closest("[data-ox-open]");
  if (row && !ev.target.closest("a,button")) { ox.open = ox.open === row.dataset.oxOpen ? null : row.dataset.oxOpen; redraw(); }
});
document.addEventListener("keydown", (ev) => {
  const row = ev.target.closest?.("[data-ox-open]");
  if (row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); ox.open = ox.open === row.dataset.oxOpen ? null : row.dataset.oxOpen; redraw(); }
});
