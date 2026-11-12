// DDHQ grab: click as a bookmark in your own browser. It reads which House seats Decision Desk HQ has called, from the
// balance of power page, and saves ddhq-calls.json to your Downloads folder, which the bot picks up within seconds.
// One click = one read of that page. A called seat has a check mark, and its card's background is the winner's color.
(async () => {
  const HOST = 'votes.decisiondeskhq.com', PAGE = '/balance-of-power/2026/house';
  const COLORS = { 'rgb(0, 89, 194)': 'D', 'rgb(215, 25, 19)': 'R' };
  if (location.hostname !== HOST) {
    alert('DDHQ grab: opening DDHQ. Click the bookmark again once the page has loaded.');
    location.href = 'https://' + HOST + PAGE;
    return;
  }
  try {
    const r = await fetch(PAGE, { cache: 'no-store' });
    if (!r.ok) throw new Error('DDHQ answered ' + r.status);
    const d = new DOMParser().parseFromString(await r.text(), 'text/html');
    const all = new Map(), calls = new Map();
    for (const a of d.querySelectorAll('a[data-bop-target-id]')) {
      const seat = (a.querySelector('p')?.textContent || '').trim();
      all.set(seat, true);
      if (!a.querySelector('.lucide-check') || calls.has(seat)) continue;
      const color = a.querySelector('.absolute.inset-0')?.style.backgroundColor || '';
      calls.set(seat, { seat, party: COLORS[color] || '?', color, url: a.getAttribute('href') });
    }
    const won = d.body.textContent.replace(/\s+/g, ' ').match(/(\d+) ?won ?(\d+) ?won/);
    if (all.size < 400 || !won) throw new Error("the page doesn't look the way it did (" + all.size + ' seats), so nothing was saved');
    const list = [...calls.values()];
    const out = { ddhqcalls: 1, page: PAGE, seats: all.size, tally: { D: +won[1], R: +won[2] }, grabbed: new Date().toISOString(), calls: list };
    if (window.__cwDry) return out;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
    a.download = 'ddhq-calls.json';
    document.body.appendChild(a); a.click(); a.remove();
    const note = document.createElement('div');
    note.textContent = 'Saved ' + list.length + ' DDHQ House calls (D ' + won[1] + ', R ' + won[2] + '). The bot loads them within 10 seconds.';
    note.style.cssText = 'position:fixed;top:16px;right:16px;z-index:99999;padding:10px 14px;background:#111;color:#fff;font:14px sans-serif;border-radius:6px';
    document.body.appendChild(note);
    setTimeout(() => note.remove(), 5000);
  } catch (e) {
    if (window.__cwDry) throw e;
    alert('DDHQ grab failed: ' + e.message);
  }
})();
