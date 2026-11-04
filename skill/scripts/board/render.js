// Render code of the dev-loop progress board. The board server page and the Obsidian dataviewjs note inline
// this file unchanged, so it is one plain script: no import, export or require.
// renderBoard(features, nowMs, opts) -> HTML string.
// opts: {theme?: "light"|"dark"|null, open?: Set<key>, here?: machine_id, me?: owner, newerSchema?: number}.
// Open keys: "<project>/<feature>" (a row), "project:<project>" (a project fold), "shipped:<project>" (its shipped fold).
function renderBoard(features, nowMs, opts) {
  opts = opts || {};
  const open = opts.open || new Set();
  const STAGES = ["Frame", "Spec", "Acceptance", "Plan", "Build", "Gate", "Ship"];

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const str = (v) => (v == null ? "" : String(v));
  const ms = (iso) => (iso ? Date.parse(str(iso)) : NaN);
  // Sort value of a time: an unreadable time sorts as the oldest.
  const at = (iso) => (isFinite(ms(iso)) ? ms(iso) : -Infinity);
  const dur = (d) => {
    if (!isFinite(d) || d < 0) return "unknown";
    const m = Math.floor(d / 60000);
    if (m < 1) return "under a minute";
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
    return h % 24 ? `${Math.floor(h / 24)} d ${h % 24} h` : `${Math.floor(h / 24)} d`;
  };
  const ago = (iso) => (isFinite(ms(iso)) ? `${dur(nowMs - ms(iso))} ago` : "never");
  const clock = (t) => {
    const d = new Date(t), hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === new Date(nowMs).toDateString() ? hm : `${d.toLocaleDateString([], { weekday: "short" })} ${hm}`;
  };
  const label = (n) => `${n}. ${STAGES[n - 1]}`;
  const sum = (a) => a.reduce((x, y) => x + y, 0);

  // A note from the synced vault can hold any JSON value in any key: coerce each one to the type the board needs.
  const norm = (f) => {
    const st = f.stage_times && typeof f.stage_times === "object" ? f.stage_times : {};
    const times = {};
    for (let n = 1; n <= 7; n++) if (isFinite(ms(st[n]))) times[n] = ms(st[n]);
    // #230: Title Case, as the feature folders. A title that is the slug shows its words.
    const title = str(f.title) && str(f.title) !== str(f.feature) ? str(f.title) : str(f.feature).replace(/-/g, " ");
    return {
      project: str(f.project), feature: str(f.feature), machine_id: str(f.machine_id),
      title: title.replace(/\S+/g, (w) => w.replace(/\p{L}/u, (c) => c.toUpperCase())) || "Untitled feature",
      shippedKept: Number.isInteger(f.shipped_kept) && f.shipped_kept >= 0 ? f.shipped_kept : null,
      owner: str(f.owner) || "Unknown", machine: str(f.machine) || "unknown machine",
      track: str(f.track), acceptance: str(f.acceptance), stage: Math.min(Math.max(Math.floor(Number(f.stage)) || 1, 1), 7), times,
      tasks: Array.isArray(f.tasks) ? f.tasks.filter((t) => t && typeof t === "object") : [],
      waiting: str(f.waiting), backfilled: f.backfilled === true,
      decisions: Array.isArray(f.decisions) ? f.decisions.filter((d) => typeof d === "string" && d) : [],
      shipped: str(f.shipped_at), updated: str(f.updated),
    };
  };

  // D22 merge. Per project and feature: shipped when any note is, at the earliest shipped_at. Else the newest
  // note per machine, then the one with the highest stage, then the newest. Each stage keeps its earliest time.
  const notes = (Array.isArray(features) ? features : []).filter((f) => f && typeof f === "object").map(norm);
  const byKey = new Map();
  for (const f of notes) {
    const k = JSON.stringify([f.project, f.feature]);
    byKey.set(k, [...(byKey.get(k) || []), f]);
  }
  const active = [], shipped = [];
  for (const fs of byKey.values()) {
    const times = {};
    for (const f of fs) for (const n in f.times) if (!(n in times) || f.times[n] < times[n]) times[n] = f.times[n];
    const shipAt = (f) => (isFinite(ms(f.shipped)) ? ms(f.shipped) : Infinity);
    const ships = fs.filter((f) => f.shipped).sort((a, b) => shipAt(a) - shipAt(b));
    if (ships.length) {
      shipped.push({ ...ships[0], times });
      continue;
    }
    const newest = new Map();
    for (const f of fs) if (!newest.has(f.machine_id) || at(f.updated) > at(newest.get(f.machine_id).updated)) newest.set(f.machine_id, f);
    const best = [...newest.values()].sort((a, b) => b.stage - a.stage || at(b.updated) - at(a.updated))[0];
    active.push({ ...best, times });
  }

  const model = (f) => {
    const done = f.tasks.filter((t) => t.state === "done");
    const doing = f.tasks.find((t) => t.state === "doing");
    const durs = done.map((t) => ms(t.done) - ms(t.started)).filter((d) => isFinite(d) && d >= 0);
    const mean = durs.length ? sum(durs) / durs.length : NaN;
    const running = doing && isFinite(ms(doing.started)) ? nowMs - ms(doing.started) : 0;
    // D28: Stage 5 only, from the done tasks.
    const left = f.stage === 5 && durs.length ? Math.max((f.tasks.length - done.length) * mean - running, 0) : NaN;
    // Spec `acceptance:` regression or none: no Stage 3.
    const list = f.track.toUpperCase() === "S" ? [1, 5, 6, 7]
      : ["regression", "none"].includes(f.acceptance) ? [1, 2, 4, 5, 6, 7] : [1, 2, 3, 4, 5, 6, 7];
    return { f, done, doing, mean, left, list, key: `${f.project}/${f.feature}`, idle: nowMs - ms(f.updated) > 15 * 60000 };
  };

  // Each reached stage of the track runs from its time to the next reached stage time, or to `end`. NaN: no time.
  const spans = (m, end) => {
    const reached = m.list.filter((n) => n <= m.f.stage);
    return reached.map((n, i) => {
      const next = reached.slice(i + 1).find((k) => k in m.f.times);
      return { n, d: (next ? m.f.times[next] : end) - m.f.times[n] };
    });
  };

  const icon = (state) =>
    state === "done"
      ? `<svg class="ic" viewBox="0 0 16 16" aria-label="done"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`
      : state === "doing"
        ? `<svg class="ic" viewBox="0 0 16 16" aria-label="in progress"><circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>`
        : `<svg class="ic" viewBox="0 0 16 16" aria-label="to do"><circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
  const stateOf = (t) => (t.state === "done" || t.state === "doing" ? t.state : "todo");

  // D27 stage bar. Next stages: a fixed 1/N of the bar each. Reached stages share the rest by their real time,
  // Stage 5 plus its time left while it runs; a stage with no time gets a minimum width. `cur`: the current
  // stage, 0 for a shipped feature. Every style value is a number.
  const bar = (m, end, cur) => {
    const next = m.list.filter((n) => n > m.f.stage);
    const slot = 100 / m.list.length, past = 100 - next.length * slot;
    const sp = spans(m, end).map((s) => ({ ...s, w: (s.d > 0 ? s.d : 0) + (s.n === 5 && isFinite(m.left) ? m.left : 0) }));
    const total = sum(sp.map((s) => s.w)), minw = Math.min(1.5, past / sp.length);
    const share = past - sp.filter((s) => !s.w).length * minw;
    const wc = (pct) => (pct >= 25 ? "w3" : pct >= 8 ? "w2" : "w1");
    const segs = sp.map((s) => {
      const pct = total ? (s.w ? (s.w / total) * share : minw) : past / sp.length;
      const now = s.n === cur;
      const time = isFinite(s.d) && s.d >= 0 ? `${dur(s.d)}${now ? " so far" : ""}${now && isFinite(m.left) ? `, about ${dur(m.left)} left` : ""}` : "";
      return { n: s.n, pct, now, time, wc: wc(pct), split: s.n === 5 && cur === 5 && m.f.tasks.length > 0 };
    });
    next.forEach((n) => segs.push({ n, pct: slot, fut: true, time: "not started", wc: "fut" }));
    const parts = m.f.tasks.map((t) => {
      const st = stateOf(t);
      const d = st === "done" ? ms(t.done) - ms(t.started) : st === "doing" ? nowMs - ms(t.started) : isFinite(m.mean) ? m.mean : 15 * 60000;
      return { st, n: t.n, title: str(t.title), d: isFinite(d) && d > 0 ? d : 60000 };
    });
    const inner = (x) =>
      x.split
        ? parts.map((p, i) => `<b data-task-segment="${i + 1}" class="${p.st}" style="flex:${p.d.toFixed(0)} 1 0" title="${esc(`${num(p.n)}${p.title}: ${p.st === "todo" ? `about ${dur(p.d)}, estimated` : dur(p.d)}`)}"></b>`).join("")
        : "";
    const width = (x) => `width:${x.pct.toFixed(3)}%`;
    return `<span class="tb" role="img" aria-label="${esc(segs.map((x) => `${label(x.n)} ${x.time}`).join(", "))}">
<span class="segs">${segs.map((x) => `<i data-stage-segment="${x.n}" class="${x.now ? "cur" : ""}${x.fut ? " fut" : ""}${x.split ? " split" : ""}" style="${width(x)}" title="${esc(`${label(x.n)}: ${x.time}`)}">${inner(x)}</i>`).join("")}</span>
<span class="lbls">${segs.map((x) => `<span class="${x.wc}${x.now ? " cur" : ""}" style="${width(x)}">${x.fut ? `<span class="nm">${label(x.n)}</span>` : `<span class="full"><strong>${label(x.n)}</strong><br>${x.time}</span><span class="short">${x.n}.</span>`}</span>`).join("")}</span></span>`;
  };

  const num = (n) => (typeof n === "number" && isFinite(n) ? `${n}. ` : "");
  const ledgerRow = (m) => {
    const f = m.f, live = !m.shipped, end = m.end;
    const first = Math.min(...Object.values(f.times));
    const total = isFinite(end - first) && end - first >= 0 ? `<strong>${dur(end - first)}</strong>` : "";
    const taskD = (t) => {
      const d = t.state === "done" ? ms(t.done) - ms(t.started) : t.state === "doing" ? end - ms(t.started) : NaN;
      return isFinite(d) && d >= 0 ? d : NaN;
    };
    const max = Math.max(0, ...f.tasks.map(taskD).filter(isFinite));
    const tasks = f.tasks.length
      ? `<ol class="lt">${f.tasks.map((t) => {
          const st = stateOf(t), d = taskD(t);
          const time = st === "todo" ? "to do" : isFinite(d) ? `${st === "doing" && live ? "running " : ""}${dur(d)}` : "";
          const w = st !== "todo" && isFinite(d) && max ? (d / max) * 100 : 0;
          return `<li class="${st}">${icon(st)}<span class="tn">${num(t.n)}</span><span class="tt">${esc(t.title)}</span><span class="lb"><b style="width:${+w.toFixed(1)}%"></b></span><span class="td">${time}</span></li>`;
        }).join("")}</ol>`
      : `<p class="na">${f.track.toUpperCase() === "S" ? "Track S: no plan, no tasks." : "No plan yet. Tasks appear at Stage 4."}</p>`;
    const sp = spans(m, end);
    const chips = sp.filter((s) => isFinite(s.d) && s.d >= 0)
      .map((s) => `<span class="chip${live && s.n === f.stage ? " now" : ""}">${label(s.n)} <strong>${dur(s.d)}</strong>${live && s.n === f.stage ? " so far" : ""}</span>`).join("")
      + (live && isFinite(m.left) ? `<span class="chip now">About <strong>${dur(m.left)}</strong> left</span>` : "");
    const decs = f.decisions.length ? `<h3>Decided without you</h3><div class="decs">${f.decisions.map((d) => `<p>${esc(d)}</p>`).join("")}</div>` : "";
    const tk = f.tasks.length ? `${m.done.length} of ${f.tasks.length} tasks` : "No plan";
    const inStage = (sp.find((s) => s.n === f.stage) || {}).d;
    const stage = live
      ? `<span class="stg"><strong>${label(f.stage)}</strong>${isFinite(inStage) ? ` ${dur(inStage)}` : ""}<span class="sub">${tk}${isFinite(m.left) ? `, <span data-time-left>About ${dur(m.left)} left</span>` : ""}</span></span>`
      : `<span class="stg"><strong class="shp">Shipped</strong><span class="sub">${tk}</span></span>`;
    const when = live
      ? `<span class="when${m.idle ? " stale" : ""}">${m.idle ? "Idle, " : ""}${ago(f.updated)}</span>`
      : `<span class="when">${isFinite(end) ? new Date(end).toISOString().slice(0, 10) : ""}</span>`;
    const you = live && f.waiting;
    return `<details class="lg${live ? " act" : ""}${you ? " you" : ""}" data-key="${esc(m.key)}"${open.has(m.key) ? " open" : ""}>
<summary><span class="t">${esc(f.title)}${you ? `<span class="flag">Needs you: ${esc(f.waiting)}</span>` : ""}</span><span class="trk"><span class="pill">Track ${esc(f.track || "?")}</span>${total}</span>${stage}<span class="who">${esc(f.owner)}, ${esc(f.machine)}${here && f.machine_id === here ? ' <span class="pill">this machine</span>' : ""}</span>${when}</summary>
<div class="lg-body">${bar(m, end, live ? f.stage : 0)}<h3>Time per stage</h3><div class="chips">${chips}</div>${f.backfilled ? `<p class="na">Times are estimated</p>` : ""}${tasks}${decs}</div>
</details>`;
  };

  const rows = active.map((f) => ({ ...model(f), shipped: false, end: nowMs }));
  const needs = rows.filter((m) => m.f.waiting);
  const lede = rows.length
    ? `${rows.length} ${rows.length === 1 ? "feature" : "features"} in progress. ${needs.length ? `${needs.length} ${needs.length === 1 ? "needs" : "need"} you.` : "Nothing needs you."}`
    : "No feature in progress.";
  const head = `<div class="hd"><p class="lede">${lede}</p><p class="stamp">Updated ${clock(nowMs)}</p></div>`;

  const shipRows = [];
  for (const p of new Set(shipped.map((f) => f.project))) {
    // #230: the shipped_kept of the project's most recently updated active note that has one, else of the newest shipped note that has one, else 5.
    const newest = notes.filter((f) => f.project === p && f.shippedKept !== null)
      .sort((a, b) => (a.shipped ? 1 : 0) - (b.shipped ? 1 : 0) || at(b.updated) - at(a.updated))[0];
    shipRows.push(...shipped.filter((f) => f.project === p).sort((a, b) => at(b.shipped) - at(a.shipped))
      .slice(0, newest ? newest.shippedKept : 5)
      .map((f) => ({ ...model({ ...f, stage: 7 }), shipped: true, end: ms(f.shipped) })));
  }
  const all = [...rows, ...shipRows];

  const here = str(opts.here);
  const hereRow = here ? all.find((m) => m.f.machine_id === here) : null;
  const me = hereRow ? hereRow.f.owner : str(opts.me);
  const rank = (m) => (here && m.f.machine_id === here ? 0 : me && m.f.owner === me ? 1 : 2);
  const order = (a, b) => rank(a) - rank(b) || (b.f.waiting ? 1 : 0) - (a.f.waiting ? 1 : 0) || at(b.f.updated) - at(a.f.updated);
  const byShip = (a, b) => rank(a) - rank(b) || at(b.f.shipped) - at(a.f.shipped);
  const projectRank = (p) => Math.min(...all.filter((m) => m.f.project === p).map(rank));
  const projects = [...new Set(all.map((m) => m.f.project))].sort((a, b) => projectRank(a) - projectRank(b) || a.localeCompare(b));
  const perStage = (list) => STAGES.map((_, i) => [i + 1, list.filter((m) => m.f.stage === i + 1).length]).filter(([, c]) => c);

  const fold = (key, cls, sum, inner) => `<details class="${cls}" data-key="${esc(key)}"${open.has(key) ? " open" : ""}><summary>${sum}</summary>${inner}</details>`;
  const ledger = (list) => `<div class="ledger">${list.map(ledgerRow).join("")}</div>`;
  const project = (p) => {
    const act = rows.filter((m) => m.f.project === p).sort(order), sh = shipRows.filter((m) => m.f.project === p).sort(byShip);
    const you = act.filter((m) => m.f.waiting).length;
    const stages = perStage(act).map(([n, c]) => `${c} in ${label(n)}`).join(", ");
    const meta = [act.length ? `${act.length} in progress (${stages})` : "nothing in progress", sh.length ? `${sh.length} recently shipped` : ""].filter(Boolean).join(", ");
    const head = `<span class="pn">${esc(p)}</span><span class="pc">${meta}</span>${you ? `<span class="pyou">${you} ${you === 1 ? "needs" : "need"} you</span>` : ""}`;
    return fold(`project:${p}`, "pg", head, (act.length ? `<h3 class="part">In progress</h3>${ledger(act)}` : "")
      + (sh.length ? fold(`shipped:${p}`, "shf", `Recently shipped <span class="pc">${sh.length}</span>`, ledger(sh)) : ""));
  };

  let body;
  if (!rows.length) {
    body = `<p class="empty">Start one with the dev-loop skill in any repo that has a dev-loop.json. It shows up here at its first stage.</p>`;
  } else {
    const decs = rows.filter((m) => m.f.decisions.length);
    const panel = needs.length || decs.length
      ? `<div class="you-panel"><h2>Needs you</h2><ul>${needs.map((m) => `<li><strong>${esc(m.f.title)}</strong>: ${esc(m.f.waiting)}.</li>`).join("")}${decs.map((m) => m.f.decisions.map((d) => `<li><strong>${esc(m.f.title)}</strong> went on with a default: ${esc(d)}</li>`).join("")).join("")}</ul></div>`
      : "";
    const counts = perStage(rows).map(([n, c]) => `<span><strong>${c}</strong> in ${label(n)}</span>`).join("");
    body = panel + `<p class="counts">${counts}</p>`;
  }
  body += projects.map(project).join("");

  const newer = Math.floor(Number(opts.newerSchema)) || 0;
  const newerLine = newer > 0 ? `<p class="newer">${newer} ${newer === 1 ? "note needs" : "notes need"} a newer dev-loop</p>` : "";

  // Theme tokens: system by default, Obsidian's body class wins inside Obsidian, data-theme wins over both.
  const themeCss = (light, dark) => {
    const d = (o) => Object.entries(o).map(([k, x]) => `--${k}:${x}`).join(";");
    return `.dlb{${d(light)};color-scheme:light}
@media (prefers-color-scheme: dark){.dlb:not([data-theme]){${d(dark)};color-scheme:dark}}
body.theme-light .dlb:not([data-theme]){${d(light)};color-scheme:light}
body.theme-dark .dlb:not([data-theme]){${d(dark)};color-scheme:dark}
.dlb[data-theme=light]{${d(light)};color-scheme:light}
.dlb[data-theme=dark]{${d(dark)};color-scheme:dark}`;
  };
  const css = `
.dlb{container-type:inline-size;background:var(--bg);color:var(--fg);padding:20px;border-radius:6px;-webkit-text-size-adjust:100%}
.dlb *{box-sizing:border-box}
.dlb p,.dlb h1,.dlb h2,.dlb h3,.dlb ol{margin:0}
.dlb .ic{width:16px;height:16px;flex:none}
.dlb .na,.dlb .empty{color:var(--muted)}
.dlb .hd{flex-wrap:wrap}
.dlb .empty{max-width:46ch;margin-top:8px}
.dlb summary{list-style:none;cursor:pointer}
.dlb summary::-webkit-details-marker{display:none}
.dlb summary:focus-visible{outline:2px solid var(--active);outline-offset:2px;border-radius:4px}
.dlb .decs p{border-inline-start:3px solid var(--you);padding:4px 10px;margin-top:6px}
${themeCss({ bg: "#ffffff", fg: "#1c2430", muted: "#5d6876", line: "#d5dbe2", surface: "#f3f5f8", active: "#0b6e79", done: "#2e7d4f", you: "#9b2f6a", idle: "#8a6a1f" },
    { bg: "#1a1f26", fg: "#e4e8ed", muted: "#9aa5b1", line: "#36404c", surface: "#212833", active: "#5cc0ca", done: "#64c38a", you: "#e584b6", idle: "#d6ad55" })}
.dlb{font:15px/1.45 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.dlb .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:14px}
.dlb .lede{font-size:20px;font-weight:600;letter-spacing:-.01em}
.dlb .stamp{color:var(--muted);font-size:13px;white-space:nowrap}
.dlb .t{font-weight:600;display:block;overflow-wrap:anywhere}
.dlb .flag{font-size:13px;color:var(--you);font-weight:600;display:block;margin-top:2px}
.dlb .stale{color:var(--idle)}
.dlb .tb{display:block}
.dlb .tb .segs{display:flex;gap:2px;height:8px}
.dlb .tb .segs i{background:var(--done);border-radius:1px;min-width:3px}
.dlb .tb .segs i.cur{background:var(--active)}
.dlb .tb .lbls{display:flex;gap:2px;margin-top:5px;font-size:12px;line-height:1.35;color:var(--muted)}
.dlb .tb .lbls > span{min-width:3px;overflow:hidden;white-space:nowrap}
.dlb .tb .lbls strong{font-weight:600;color:var(--fg)}
.dlb .tb .lbls .cur strong{color:var(--active)}
.dlb .tb .short{display:none}
.dlb .tb .w1 .full,.dlb .tb .w2 .full{display:none}
.dlb .tb .w1 .short,.dlb .tb .w2 .short{display:inline}
.dlb .tb .segs i.fut{background:var(--line)}
.dlb .tb .lbls .fut .nm{display:block;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
.dlb .tb .segs i.split{display:flex;gap:1px;background:none}
.dlb .tb .split b{display:block;min-width:2px;border-radius:1px;background:var(--active)}
.dlb .tb .split b.doing{opacity:.5}
.dlb .tb .split b.todo{background:none;box-shadow:inset 0 0 0 1px var(--active)}
.dlb .tb .segs i.split:not(.cur) b{background:var(--done);box-shadow:none;opacity:1}
.dlb .you-panel{border-inline-start:3px solid var(--you);background:var(--surface);padding:10px 14px;margin-bottom:14px}
.dlb .you-panel h2{font-size:14px;font-weight:600;color:var(--you);margin-bottom:4px}
.dlb .you-panel ul{margin:0;padding-left:18px;font-size:14px}
.dlb .you-panel li{padding:2px 0}
.dlb .counts{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:13px;color:var(--muted);margin:0 0 6px}
.dlb .counts strong{color:var(--fg);font-weight:600}
.dlb .newer{font-size:13px;color:var(--idle);margin-top:16px}
.dlb .pg{border-bottom:2px solid var(--fg);margin-top:18px}
.dlb .pg > summary{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;padding:8px 4px 10px}
.dlb .pg > summary::before{content:"\\25B8";font-size:18px;color:var(--muted);width:16px}
.dlb .pg[open] > summary::before{content:"\\25BE"}
.dlb .pg > summary:hover{background:var(--surface)}
.dlb .pg .pn{font-size:22px;font-weight:700;letter-spacing:-.01em}
.dlb .pc{font-size:13px;font-weight:500;color:var(--muted)}
.dlb .pyou{font-size:13px;font-weight:600;color:var(--you)}
.dlb .pg[open]{padding-bottom:12px}
.dlb .part{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:12px 0 4px}
.dlb .shf{margin-top:14px}
.dlb .shf > summary{display:block;font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:6px 0}
.dlb .shf > summary::before{content:"\\25B8  "}
.dlb .shf[open] > summary::before{content:"\\25BE  "}
.dlb .ledger{border-top:1px solid var(--line)}
.dlb .lg{border-bottom:1px solid var(--line)}
.dlb .lg > summary{display:grid;grid-template-columns:minmax(0,2fr) 200px minmax(0,1.4fr) minmax(0,1fr) 130px;gap:14px;align-items:center;padding:10px 12px}
.dlb .lg > summary:hover,.dlb .lg[open] > summary{background:var(--surface)}
.dlb .lg.you{box-shadow:inset 3px 0 0 var(--you)}
.dlb .trk{display:flex;align-items:center;gap:8px}
.dlb .trk strong{font-size:18px;font-weight:700;letter-spacing:-.01em}
.dlb .lg.act .trk strong{color:var(--active)}
.dlb .pill{font-size:11px;font-weight:600;letter-spacing:.02em;padding:1px 7px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--fg);white-space:nowrap}
.dlb .stg{font-size:13px}
.dlb .stg strong{font-weight:600;color:var(--active)}
.dlb .stg strong.shp{color:var(--done)}
.dlb .stg .sub{display:block;color:var(--muted);font-size:12px}
.dlb .lg .who,.dlb .lg .when{font-size:13px;color:var(--muted)}
.dlb .lg .when{text-align:right}
.dlb .lg .when.stale{color:var(--idle)}
.dlb .lg-body{padding:8px 12px 16px}
.dlb .lg-body h3{font-size:13px;font-weight:600;color:var(--muted);margin:12px 0 4px}
.dlb .chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.dlb .chip{font-size:12px;padding:2px 8px;border-radius:4px;background:var(--surface);color:var(--muted)}
.dlb .chip strong{color:var(--fg);font-weight:600}
.dlb .chip.now{background:none;box-shadow:inset 0 0 0 1px var(--active);color:var(--active)}
.dlb .chip.now strong{color:var(--active)}
.dlb .lt{list-style:none;padding:0;font-size:13px}
.dlb .lt li{display:grid;grid-template-columns:16px 28px minmax(0,1fr) minmax(80px,200px) 120px;gap:8px;align-items:center;padding:3px 0}
.dlb .lt .done .ic{color:var(--done)}
.dlb .lt .doing{font-weight:600}
.dlb .lt .doing .ic{color:var(--active)}
.dlb .lt .doing .lb b{background:var(--active)}
.dlb .lt .todo{color:var(--muted)}
.dlb .lt .todo .ic{color:var(--line)}
.dlb .lt .tn{color:var(--muted)}
.dlb .lt .lb{height:6px;background:var(--surface);border-radius:3px;overflow:hidden}
.dlb .lt .lb b{display:block;height:100%;background:var(--done)}
.dlb .lt .td{text-align:right;color:var(--muted)}
@container (max-width: 720px){
 .dlb .lg > summary{grid-template-columns:1fr auto;gap:6px 12px}
 .dlb .lg .stg,.dlb .lg .who{grid-column:1/-1}
 .dlb .lt li{grid-template-columns:16px 28px 1fr 90px}
 .dlb .lt .lb{display:none}
 .dlb .tb .w1 .short{visibility:hidden}
}`;
  const theme = opts.theme === "light" || opts.theme === "dark" ? ` data-theme="${opts.theme}"` : "";
  return `<style>${css}</style><div class="dlb"${theme}>${head}${body}${newerLine}</div>`;
}
