// Render code of the dev-loop progress board. The board server page and the Obsidian dataviewjs note inline
// this file unchanged, so it is one plain script: no import, export or require.
// renderBoard(features, nowMs, opts) -> HTML string.
// opts: {theme?: "light"|"dark"|null, open?: Set<key>, here?: machine_id, me?: owner, newerSchema?: number}.
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
    const m = Math.round(d / 60000);
    if (m < 1) return "under a minute";
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  };
  const ago = (iso) => (isFinite(ms(iso)) ? (nowMs - ms(iso) < 60000 ? "just now" : `${dur(nowMs - ms(iso))} ago`) : "never");
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
    return {
      project: str(f.project), feature: str(f.feature), machine_id: str(f.machine_id),
      title: str(f.title) || str(f.feature) || "Untitled feature",
      owner: str(f.owner) || "Unknown", machine: str(f.machine) || "unknown machine",
      track: str(f.track), stage: Math.min(Math.max(Math.floor(Number(f.stage)) || 1, 1), 7), times,
      tasks: Array.isArray(f.tasks) ? f.tasks.filter((t) => t && typeof t === "object") : [],
      waiting: str(f.waiting),
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
    const durs = done.map((t) => ms(t.done) - ms(t.started)).filter((d) => isFinite(d) && d > 0);
    const mean = durs.length ? sum(durs) / durs.length : NaN;
    const running = doing && isFinite(ms(doing.started)) ? nowMs - ms(doing.started) : 0;
    // D28: Stage 5 only, from the done tasks.
    const left = f.stage === 5 && durs.length ? Math.max((f.tasks.length - done.length) * mean - running, 0) : NaN;
    const list = f.track.toUpperCase() === "S" ? [1, 5, 6, 7] : [1, 2, 3, 4, 5, 6, 7];
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
      const time = `${dur(s.d)}${now ? " so far" : ""}${now && isFinite(m.left) ? `, about ${dur(m.left)} left` : ""}`;
      return { n: s.n, pct, now, time, wc: wc(pct), split: s.n === 5 && m.f.tasks.length > 0 };
    });
    next.forEach((n) => segs.push({ n, pct: slot, fut: true, time: "not started", wc: "fut" }));
    const parts = m.f.tasks.map((t) => {
      const st = stateOf(t);
      const d = st === "done" ? ms(t.done) - ms(t.started) : st === "doing" ? nowMs - ms(t.started) : isFinite(m.mean) ? m.mean : 15 * 60000;
      return { st, title: str(t.title), d: isFinite(d) && d > 0 ? d : 60000 };
    });
    const inner = (x) =>
      x.split
        ? parts.map((p, i) => `<b data-task-segment="${i + 1}" class="${p.st}" style="flex:${p.d.toFixed(0)} 1 0" title="${esc(`${p.title}: ${p.st === "todo" ? `about ${dur(p.d)}, estimated` : dur(p.d)}`)}"></b>`).join("")
        : "";
    const width = (x) => `width:${x.pct.toFixed(3)}%`;
    return `<span class="tb" role="img" aria-label="${esc(segs.map((x) => `${label(x.n)} ${x.time}`).join(", "))}">
<span class="segs">${segs.map((x) => `<i data-stage-segment="${x.n}" class="${x.now ? "cur" : ""}${x.fut ? " fut" : ""}${x.split ? " split" : ""}" style="${width(x)}" title="${esc(`${label(x.n)}: ${x.time}`)}">${inner(x)}</i>`).join("")}</span>
<span class="lbls">${segs.map((x) => `<span class="${x.wc}${x.now ? " cur" : ""}" style="${width(x)}">${x.fut ? `<span class="nm">${label(x.n)}</span>` : `<span class="full"><strong>${label(x.n)}</strong><br>${x.time}</span><span class="short">${x.n}.</span>`}</span>`).join("")}</span></span>`;
  };

  const taskList = (m) =>
    m.f.tasks.length
      ? `<ol class="tasks">${m.f.tasks.map((t) => {
          const st = stateOf(t);
          const time = st === "done" ? dur(ms(t.done) - ms(t.started)) : st === "doing" ? `running ${dur(nowMs - ms(t.started))}` : "";
          return `<li class="${st}">${icon(st)}<span class="tt">${esc(t.title)}</span><span class="td">${time}</span></li>`;
        }).join("")}</ol>`
      : `<p class="na">No plan yet. Tasks appear at Stage 4.</p>`;

  const row = (m) => {
    const f = m.f, sp = spans(m, nowMs);
    const inStage = (sp.find((s) => s.n === f.stage) || {}).d;
    const times = sp.filter((s) => isFinite(s.d)).map((s) => `<li><span>${label(s.n)}</span><span>${dur(s.d)}${s.n === f.stage ? " so far" : ""}</span></li>`).join("");
    const tk = f.tasks.length ? `${m.done.length} of ${f.tasks.length}` : `<span class="na">No plan yet</span>`;
    const eta = isFinite(m.left) ? `<span data-time-left>About ${dur(m.left)} left</span>` : f.stage === 5 && f.tasks.length ? `<span class="na">Not estimated yet</span>` : "";
    const decs = f.decisions.length ? `<h3 style="margin-top:12px">Decided without you</h3><div class="decs">${f.decisions.map((d) => `<p>${esc(d)}</p>`).join("")}</div>` : "";
    return `<details class="row${f.waiting ? " you" : ""}" data-key="${esc(m.key)}"${open.has(m.key) ? " open" : ""}>
<summary>
 <span class="c-f"><span class="t">${esc(f.title)}</span><span class="s">track ${esc(f.track)}</span>${f.waiting ? `<span class="flag">Needs you: ${esc(f.waiting)}</span>` : ""}</span>
 <span class="c-st"><span class="sn"><strong>${label(f.stage)}</strong> for ${dur(inStage)}</span>${bar(m, nowMs, f.stage)}</span>
 <span class="c-tk">${tk}</span>
 <span class="c-eta">${eta}</span>
 <span class="c-up${m.idle ? " stale" : ""}">${m.idle ? "Idle, " : ""}${ago(f.updated)}</span>
</summary>
<div class="more"><div><h3>Tasks</h3>${taskList(m)}</div><div><h3>Time per stage</h3><ol class="times">${times}</ol>${decs}</div></div>
</details>`;
  };

  const rows = active.map(model);
  const needs = rows.filter((m) => m.f.waiting);
  const lede = rows.length
    ? `${rows.length} ${rows.length === 1 ? "feature" : "features"} in progress. ${needs.length ? `${needs.length} ${needs.length === 1 ? "needs" : "need"} you.` : "Nothing needs you."}`
    : "No feature in progress.";
  const head = `<div class="hd"><p class="lede">${lede}</p><p class="stamp">Updated ${clock(nowMs)}</p></div>`;

  let body;
  if (!rows.length) {
    body = `<p class="empty">Start one with the dev-loop skill in any repo that has a dev-loop.json. It shows up here at its first stage.</p>`;
  } else {
    const decs = rows.filter((m) => m.f.decisions.length);
    const panel = needs.length || decs.length
      ? `<div class="you-panel"><h2>Needs you</h2><ul>${needs.map((m) => `<li><strong>${esc(m.f.title)}</strong>: ${esc(m.f.waiting)}. Waiting ${dur(nowMs - ms(m.f.updated))}.</li>`).join("")}${decs.map((m) => m.f.decisions.map((d) => `<li><strong>${esc(m.f.title)}</strong> went on with a default: ${esc(d)}</li>`).join("")).join("")}</ul></div>`
      : "";
    const counts = STAGES.map((_, i) => [i + 1, rows.filter((m) => m.f.stage === i + 1).length]).filter(([, c]) => c)
      .map(([n, c]) => `<span><strong>${c}</strong> in ${label(n)}</span>`).join("");
    // D26. With `here` (server page): this machine, then the other machines of its owner, then other owners.
    // Without it (Obsidian note): owners by name, then machines by name.
    const here = str(opts.here);
    const hereRow = here ? rows.find((m) => m.f.machine_id === here) : null;
    const me = hereRow ? hereRow.f.owner : here ? str(opts.me) : "";
    const groups = new Map();
    for (const m of rows) groups.set(m.f.machine_id, [...(groups.get(m.f.machine_id) || []), m]);
    const rank = (g) => (here && g[0].f.machine_id === here ? 0 : me && g[0].f.owner === me ? 1 : 2);
    const byName = (a, b) => rank(a) - rank(b) || a[0].f.owner.localeCompare(b[0].f.owner) || a[0].f.machine.localeCompare(b[0].f.machine);
    const cols = `<div class="cols"><span>Feature</span><span>Stage</span><span>Tasks</span><span>Time left</span><span>Last update</span></div>`;
    const order = (a, b) => (b.f.waiting ? 1 : 0) - (a.f.waiting ? 1 : 0) || at(b.f.updated) - at(a.f.updated);
    const group = (g) => {
      const projects = [...new Set(g.map((m) => m.f.project))].sort((a, b) => a.localeCompare(b));
      const mark = here && g[0].f.machine_id === here ? ' <span class="here">this machine</span>' : "";
      return `<h2 class="grp">${esc(g[0].f.owner)}, on ${esc(g[0].f.machine)}${mark}</h2>${cols}${projects.map((p) =>
        `<h3 class="proj">${esc(p)}</h3>${g.filter((m) => m.f.project === p).sort(order).map(row).join("")}`).join("")}`;
    };
    body = panel + `<p class="counts">${counts}</p>` + [...groups.values()].sort(byName).map(group).join("");
  }

  const newer = Math.floor(Number(opts.newerSchema)) || 0;
  const newerLine = newer > 0 ? `<p class="newer">${newer} ${newer === 1 ? "note needs" : "notes need"} a newer dev-loop</p>` : "";

  // D32: per project, the 3 latest ship times, newest first.
  const latest = [];
  for (const p of new Set(shipped.map((f) => f.project))) {
    latest.push(...shipped.filter((f) => f.project === p).sort((a, b) => at(b.shipped) - at(a.shipped)).slice(0, 3));
  }
  latest.sort((a, b) => at(b.shipped) - at(a.shipped));
  const shippedItem = (f) => {
    const m = model({ ...f, stage: 7 }), end = ms(f.shipped);
    const first = Math.min(...Object.values(f.times));
    const date = isFinite(end) ? new Date(end).toISOString().slice(0, 10) : "on an unknown date";
    return `<li><span class="t">${esc(f.title)}</span><span class="s">${esc(f.project)}, shipped ${date}, ${dur(end - first)} in total</span>${bar(m, end, 0)}</li>`;
  };
  const shippedList = latest.length ? `<h2 class="grp">Recently shipped</h2><ul class="shipped">${latest.map(shippedItem).join("")}</ul>` : "";

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
.dlb .tasks{list-style:none;padding:0}
.dlb .tasks li{display:flex;gap:8px;align-items:center;padding:4px 0}
.dlb .tasks .done{color:var(--muted)}
.dlb .tasks .done .ic{color:var(--done)}
.dlb .tasks .doing{font-weight:600}
.dlb .tasks .doing .ic{color:var(--active)}
.dlb .tasks .todo .ic{color:var(--line)}
.dlb .tasks .td{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums;font-weight:400;white-space:nowrap}
.dlb .decs p{border-inline-start:3px solid var(--you);padding:4px 10px;margin-top:6px}
${themeCss({ bg: "#ffffff", fg: "#1c2430", muted: "#5d6876", line: "#d5dbe2", surface: "#f3f5f8", active: "#0b6e79", done: "#2e7d4f", you: "#9b2f6a", idle: "#8a6a1f" },
    { bg: "#1a1f26", fg: "#e4e8ed", muted: "#9aa5b1", line: "#36404c", surface: "#212833", active: "#5cc0ca", done: "#64c38a", you: "#e584b6", idle: "#d6ad55" })}
.dlb{font:15px/1.45 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.dlb .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:14px}
.dlb .lede{font-size:20px;font-weight:600;letter-spacing:-.01em}
.dlb .stamp{color:var(--muted);font-size:13px;white-space:nowrap}
.dlb .cols,.dlb summary{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,2.3fr) 90px 104px 92px;gap:14px;align-items:center}
.dlb .cols{font-size:12px;color:var(--muted);padding:0 12px 6px;border-bottom:1px solid var(--line)}
.dlb .row{border-bottom:1px solid var(--line)}
.dlb .row summary{padding:12px}
.dlb .row summary:hover{background:var(--surface)}
.dlb .row[open] summary{background:var(--surface)}
.dlb .row.you{box-shadow:inset 3px 0 0 var(--you)}
.dlb .t{font-weight:600;display:block;overflow-wrap:anywhere}
.dlb .s{font-size:13px;color:var(--muted);display:block}
.dlb .flag{font-size:13px;color:var(--you);font-weight:600;display:block;margin-top:2px}
.dlb .sn{font-size:13px;display:block;margin-bottom:6px}
.dlb .sn strong{font-weight:600}
.dlb .stale{color:var(--idle)}
.dlb .more{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:24px;padding:4px 12px 16px 12px;font-size:14px}
.dlb .more h3{font-size:13px;font-weight:600;color:var(--muted);margin-bottom:4px}
.dlb .times{list-style:none;padding:0;font-size:13px}
.dlb .times li{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--line)}
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
.dlb .grp{font-size:16px;font-weight:600;margin:22px 0 8px}
.dlb .grp .here{font-size:13px;font-weight:400;color:var(--muted);margin-left:6px}
.dlb .proj{font-size:13px;font-weight:600;color:var(--muted);padding:10px 12px 2px}
.dlb .newer{font-size:13px;color:var(--idle);margin-top:16px}
.dlb .shipped{list-style:none;padding:0}
.dlb .shipped li{padding:10px 12px;border-bottom:1px solid var(--line)}
.dlb .shipped .tb{margin-top:6px;max-width:640px}
@container (max-width: 720px){
 .dlb .cols{display:none}
 .dlb summary{grid-template-columns:1fr auto;gap:6px 12px}
 .dlb .c-f,.dlb .c-st{grid-column:1/-1}
 .dlb .c-tk,.dlb .c-eta,.dlb .c-up{font-size:13px}
 .dlb .c-up{grid-column:1/-1;color:var(--muted)}
 .dlb .more{grid-template-columns:1fr}
 .dlb .tb .w1 .short{visibility:hidden}
}`;
  const theme = opts.theme === "light" || opts.theme === "dark" ? ` data-theme="${opts.theme}"` : "";
  return `<style>${css}</style><div class="dlb"${theme}>${head}${body}${newerLine}${shippedList}</div>`;
}
