// County Watch grab: run on a state's official results page in your own browser (as a bookmark).
// It reads the Senate race by county from the same data the page itself loads, and saves
// countywatch-XX.json to your Downloads folder, which the bot picks up within seconds.
// One click = one download. Works on: Ohio (liveresults.ohiosos.gov), Texas (results.texas-election.com),
// Kansas (ent.sos.ks.gov).
(async () => {
  const H = location.hostname, C = {};
  let st = '', note = '';
  const side = p => /^(D|DEM)/i.test(p || '') ? 'D' : /^(R|REP)/i.test(p || '') ? 'R' : 'O';
  const add = (k, s, v) => { C[k] = C[k] || { D: 0, R: 0, O: 0, pct_in: null }; C[k][s] += (+v || 0); };
  const num = s => +String(s || '').replace(/[^0-9.]/g, '');
  const J = async u => { const r = await fetch(u, { cache: 'no-store' }); if (!r.ok) throw new Error(u + ' ' + r.status); return r.json(); };
  try {
    if (H.includes('ohiosos.gov')) {
      st = 'OH';
      const q = '/Api/v1/Contest/GetContestDetailsObject?contestName=' + encodeURIComponent('U.S. Senator');
      const s = await J(q);
      const j = await J(q + '&contestObjectId=' + s.contestList[0].objectId + '&resultsLevel=County');
      for (const c of j.contestList) {
        for (const x of c.candidatesRunning) add(c.name, side(x.candidateParty), x.vCount);
        C[c.name].pct_in = c.totalPrecincts ? c.reportedPrecincts / c.totalPrecincts : null;
      }
    } else if (H.includes('texas-election.com')) {
      st = 'TX';
      const v = (await J('/static/data/Version.json')).___versionNo;
      const info = (await J('/static/data/ElectionConstants_' + v + '.json')).currentelectionInfo;
      if (!/2026/.test(info.N) || info.ET !== 'G') throw new Error('the site is still showing "' + info.N + '", not the 2026 general');
      note = info.N;
      const ev = (await J('/static/data/election/' + info.ID + '/Version.json')).___versionNo;
      const j = await J('/static/data/election/' + info.ID + '/' + ev + '/County.json');
      for (const [f, c] of Object.entries(j)) {
        const r = Object.values(c.Races || {}).find(r => /^U\.\s?S\.\s?SENATOR/i.test(r.ON || ''));
        if (!r) continue;
        for (const x of Object.values(r.C)) add(f, side(x.P), x.V);
        C[f].pct_in = r.TPR && r.PR ? r.PR / r.TPR : null;
      }
    } else if (H.includes('ent.sos.ks.gov')) {
      st = 'KS';
      const names = {};
      for (const o of document.querySelectorAll('select option'))
        if (/^[a-z][a-z_-]*\.html$/.test(o.value) && !/kssos_ent/.test(o.value)) names[o.value] = o.textContent.trim();
      const pages = Object.keys(names);
      if (pages.length < 100) throw new Error('open a Kansas county page first (the county list has ' + pages.length + ' entries)');
      const one = async p => {
        const h = await (await fetch('/' + p, { cache: 'no-store' })).text();
        const d = new DOMParser().parseFromString(h, 'text/html');
        const r = [...d.querySelectorAll('.race')].find(r => /United States Senate/.test(r.querySelector('h3')?.textContent || ''));
        if (!r) return;
        const name = names[p];
        const sub = r.querySelector('.race-subheading')?.textContent || '';
        const m = sub.match(/Reporting:\s*([\d,]+)\s*of\s*([\d,]+)/);
        for (const row of r.querySelectorAll('.race-candidate-list .row')) {
          const ps = [...row.querySelectorAll('p')].map(x => x.textContent.trim());
          if (ps.length >= 2 && /^[A-Z]{1,3}-/.test(ps[0])) add(name, side(ps[0].split('-')[0]), num(ps[1]));
        }
        if (C[name] && m && num(m[2])) C[name].pct_in = num(m[1]) / num(m[2]);
      };
      for (let i = 0; i < pages.length; i += 6) await Promise.all(pages.slice(i, i + 6).map(one));
    } else {
      alert('County Watch: open this on the Ohio, Texas or Kansas results site.');
      return;
    }
    const out = { countywatch: 1, state: st, source: H, note, grabbed: new Date().toISOString(), counties: C };
    if (window.__cwDry) return out;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
    a.download = 'countywatch-' + st + '.json';
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) {
    if (window.__cwDry) throw e;
    alert('County Watch grab failed: ' + e.message);
  }
})();
