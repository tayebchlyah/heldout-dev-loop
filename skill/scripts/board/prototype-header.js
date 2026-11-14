// PROTOTYPE, throwaway: board header variants H, H1, H2, shared by prototype-header.html and the Obsidian test note.
// protoHeader(key, {counts, needs, stamp, light, dark}) -> HTML (with its <style>) to put at the top of .dlb.
const PROTO_VARIANTS = {
  H: "H: segmented meter (previous)",
  H1: "H1: round badge, top middle",
  H2: "H2: round badge, right of the count",
  H3: "H3: round badge, top right of the cell",
};
const PROTO_SAMPLES = { basic: "Sample: 7 features, 3 need you", busy: "Sample: 21 features, 7 need you" };
function protoHeader(key, d) {
  const SHORT = { Acceptance: "Accept" };
  const youTxt = (n) => `${n} need${n === 1 ? "s" : ""} you`;
  const logo = `<span class="logo">${d.svg}</span>`;
  const top = `<div class="bar">${logo}<span class="badge"><b>${d.needs}</b>need you</span><span class="stamp">${d.stamp}</span></div>`;
  const base = `
.dlb .logo{display:block;flex:none}
.dlb .logo svg{display:block;height:72px;width:216px}
.dlb{--lg-code-a:#2b0adb;--lg-code-b:#3015e8;--lg-ink-a:#080b2b;--lg-ink-b:#040821;--lg-violet-a:#4010e5;--lg-violet-b:#3505d1;--lg-rim:.84}
@media (prefers-color-scheme: dark){.dlb:not([data-theme]){--lg-code-a:#a48cff;--lg-code-b:#8f74ff;--lg-ink-a:#f5f7ff;--lg-ink-b:#e6e9fb;--lg-violet-a:#a48cff;--lg-violet-b:#8f74ff;--lg-rim:.25}}
body.theme-light .dlb:not([data-theme]){--lg-code-a:#2b0adb;--lg-code-b:#3015e8;--lg-ink-a:#080b2b;--lg-ink-b:#040821;--lg-violet-a:#4010e5;--lg-violet-b:#3505d1;--lg-rim:.84}
body.theme-dark .dlb:not([data-theme]){--lg-code-a:#a48cff;--lg-code-b:#8f74ff;--lg-ink-a:#f5f7ff;--lg-ink-b:#e6e9fb;--lg-violet-a:#a48cff;--lg-violet-b:#8f74ff;--lg-rim:.25}
.dlb[data-theme=light]{--lg-code-a:#2b0adb;--lg-code-b:#3015e8;--lg-ink-a:#080b2b;--lg-ink-b:#040821;--lg-violet-a:#4010e5;--lg-violet-b:#3505d1;--lg-rim:.84}
.dlb[data-theme=dark]{--lg-code-a:#a48cff;--lg-code-b:#8f74ff;--lg-ink-a:#f5f7ff;--lg-ink-b:#e6e9fb;--lg-violet-a:#a48cff;--lg-violet-b:#8f74ff;--lg-rim:.25}
.dlb .bar{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;margin-bottom:18px}
.dlb .stamp{color:var(--muted);font-size:13px;white-space:nowrap}
.dlb .badge{margin-left:auto;display:flex;align-items:center;gap:6px;color:var(--you);border:1.5px solid var(--you);border-radius:999px;padding:2px 10px 2px 3px;font-size:13px;font-weight:600;line-height:1}
.dlb .badge b{background:var(--you);color:var(--bg);border-radius:999px;min-width:22px;height:22px;display:grid;place-items:center;font-size:13px}
@container (max-width: 720px){.dlb .logo svg{height:56px;width:168px}.dlb .stamp{width:100%;order:3}}`;
  const tile = (x, cls = "st") => `<div class="${cls}${x.c ? "" : " zero"}"><b>${x.c}</b><span>${x.n}. ${x.s}</span>${x.you ? `<em>${youTxt(x.you)}</em>` : ""}</div>`;
  const tileCss = `
.dlb .st{background:var(--surface);padding:10px 12px 8px;border-top:4px solid var(--active);min-width:0}
.dlb .st b{display:block;font-size:32px;line-height:1.1;font-weight:700}
.dlb .st span{font-size:12px;color:var(--muted);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.dlb .st.zero{border-top-color:var(--line)}.dlb .st.zero b{color:var(--line)}
.dlb .st em{font-style:normal;color:var(--you);font-size:12px;font-weight:600;display:block}`;
  const V = {
    // 12-column grid: one row of 7 on a wide screen; on a phone 4 tiles of 3 columns, then 3 tiles of 4 columns: two full rows.
    H: () => [`<div class="meter">${d.counts.map((x) => `<div class="${x.c ? "on" : "zero"}">${x.you ? `<i data-n="${x.you}">${youTxt(x.you)}</i>` : "<i></i>"}<b>${x.c}</b><span><span class="full">${x.n}. ${x.s}</span><span class="short">${SHORT[x.s] || x.s}</span></span></div>`).join("")}</div>`, `
.dlb .meter{display:grid;grid-template-columns:repeat(7,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:14px}
.dlb .meter > div{display:flex;flex-direction:column;align-items:center;padding:8px 4px 10px;border-left:1px solid var(--line);min-width:0}
.dlb .meter > div:first-child{border-left:0}
.dlb .meter .on{background:var(--surface);box-shadow:inset 0 -4px 0 var(--active)}
.dlb .meter b{font-size:34px;line-height:1.1;font-weight:700}
.dlb .meter .zero b{color:var(--line)}
.dlb .meter span{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
.dlb .meter i{font-style:normal;font-size:11px;font-weight:700;color:var(--you);min-height:15px;white-space:nowrap}
.dlb .meter .short{display:none}
@container (max-width: 720px){.dlb .meter b{font-size:24px}.dlb .meter .full{display:none}.dlb .meter .short{display:inline;font-size:11px}.dlb .meter i{font-size:0;min-height:18px}.dlb .meter i[data-n]::before{content:attr(data-n);display:inline-block;font-size:11px;line-height:16px;min-width:16px;padding:0 4px;border-radius:999px;background:var(--you);color:var(--bg);text-align:center}}`],
    H1: () => [`<div class="meter">${d.counts.map((x) => `<div class="${x.c ? "on" : "zero"}"><span class="top">${x.you ? `<i class="rb">${x.you}</i><em>need${x.you === 1 ? "s" : ""} you</em>` : ""}</span><b>${x.c}</b><span><span class="full">${x.n}. ${x.s}</span><span class="short">${SHORT[x.s] || x.s}</span></span></div>`).join("")}</div>`, `
.dlb .meter{display:grid;grid-template-columns:repeat(7,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:14px}
.dlb .meter > div{display:flex;flex-direction:column;align-items:center;padding:8px 4px 10px;border-left:1px solid var(--line);min-width:0}
.dlb .meter > div:first-child{border-left:0}
.dlb .meter .on{background:var(--surface);box-shadow:inset 0 -4px 0 var(--active)}
.dlb .meter b{font-size:34px;line-height:1.1;font-weight:700}
.dlb .meter .zero b{color:var(--line)}
.dlb .meter > div > span{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
.dlb .meter .short{display:none}
.dlb .meter .rb{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--you);color:var(--bg);font:700 12px/1 inherit;font-style:normal;flex:none}
@container (max-width: 720px){.dlb .meter b{font-size:24px}.dlb .meter .full{display:none}.dlb .meter .short{display:inline;font-size:11px}.dlb .meter .rb{width:18px;height:18px;font-size:11px}}
.dlb .meter .top{display:flex;align-items:center;gap:5px;min-height:22px;color:var(--you);font-size:11px;font-weight:700}
.dlb .meter .top em{font-style:normal}
@container (max-width: 720px){.dlb .meter .top{min-height:18px}.dlb .meter .top em{display:none}}`],
    H3: () => [`<div class="meter">${d.counts.map((x) => `<div class="${x.c ? "on" : "zero"}"><span class="top">${x.you ? `<em>need${x.you === 1 ? "s" : ""} you</em><i class="rb">${x.you}</i>` : ""}</span><b>${x.c}</b><span><span class="full">${x.n}. ${x.s}</span><span class="short">${SHORT[x.s] || x.s}</span></span></div>`).join("")}</div>`, `
.dlb .meter{display:grid;grid-template-columns:repeat(7,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:14px}
.dlb .meter > div{display:flex;flex-direction:column;align-items:center;padding:8px 4px 10px;border-left:1px solid var(--line);min-width:0}
.dlb .meter > div:first-child{border-left:0}
.dlb .meter .on{background:var(--surface);box-shadow:inset 0 -4px 0 var(--active)}
.dlb .meter b{font-size:34px;line-height:1.1;font-weight:700}
.dlb .meter .zero b{color:var(--line)}
.dlb .meter > div > span{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
.dlb .meter .short{display:none}
.dlb .meter .rb{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--you);color:var(--bg);font:700 12px/1 inherit;font-style:normal;flex:none}
@container (max-width: 720px){.dlb .meter b{font-size:24px}.dlb .meter .full{display:none}.dlb .meter .short{display:inline;font-size:11px}.dlb .meter .rb{width:18px;height:18px;font-size:11px}}
.dlb .meter .top{align-self:flex-end;margin-right:2px;display:flex;align-items:center;gap:5px;min-height:22px;color:var(--you);font-size:11px;font-weight:700}
.dlb .meter .top em{font-style:normal}
@container (max-width: 720px){.dlb .meter .top{min-height:18px}.dlb .meter .top em{display:none}}`],
    H2: () => [`<div class="meter">${d.counts.map((x) => `<div class="${x.c ? "on" : "zero"}"><span class="cnt"><b>${x.c}</b>${x.you ? `<i class="rb" title="${youTxt(x.you)}">${x.you}</i>` : ""}</span><span><span class="full">${x.n}. ${x.s}</span><span class="short">${SHORT[x.s] || x.s}</span></span></div>`).join("")}</div>`, `
.dlb .meter{display:grid;grid-template-columns:repeat(7,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:14px}
.dlb .meter > div{display:flex;flex-direction:column;align-items:center;padding:8px 4px 10px;border-left:1px solid var(--line);min-width:0}
.dlb .meter > div:first-child{border-left:0}
.dlb .meter .on{background:var(--surface);box-shadow:inset 0 -4px 0 var(--active)}
.dlb .meter b{font-size:34px;line-height:1.1;font-weight:700}
.dlb .meter .zero b{color:var(--line)}
.dlb .meter > div > span{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
.dlb .meter .short{display:none}
.dlb .meter .rb{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--you);color:var(--bg);font:700 12px/1 inherit;font-style:normal;flex:none}
@container (max-width: 720px){.dlb .meter b{font-size:24px}.dlb .meter .full{display:none}.dlb .meter .short{display:inline;font-size:11px}.dlb .meter .rb{width:18px;height:18px;font-size:11px}}
.dlb .meter > div{padding-top:12px}
.dlb .meter .cnt{position:relative;display:inline-block}
.dlb .meter .cnt b{color:var(--fg)}.dlb .meter .zero .cnt b{color:var(--line)}
.dlb .meter .cnt .rb{position:absolute;left:100%;top:2px;margin-left:4px}
@container (max-width: 720px){.dlb .meter .cnt b{font-size:22px}.dlb .meter .cnt .rb{width:16px;height:16px;font-size:10px;margin-left:-3px;top:-7px}}`],
  };
  const [html, css] = V[key]();
  return `<style>${base}${css}</style>${top}${html}`;
}
// Sample features, and the header data they give.
function protoSample(now, scenario) {
  const H = 3600000, iso = (h) => new Date(now - h * H).toISOString();
  const times = (upTo) => Object.fromEntries([1, 2, 3, 4, 5, 6, 7].filter((n) => n <= upTo).map((n) => [n, iso(40 - n * 5)]));
  const f = (project, feature, stage, extra = {}) => ({ project, feature, stage, track: "M", owner: "tayebchlyah", machine: "mbp", machine_id: "m1",
    stage_times: times(stage), updated: iso(0.2), tasks: [], decisions: [], ...extra });
  const tasks = (done, all) => Array.from({ length: all }, (_, i) => ({ n: i + 1, title: `Task ${i + 1}`, state: i < done ? "done" : i === done ? "doing" : "todo",
    started: iso(6 - i), done: i < done ? iso(5.5 - i) : null }));
  const features = [
    f("dev-loop", "stale-check-project", 6, { waiting: "acceptance guard stopped: stuck test" }),
    f("dev-loop", "board-header", 2, { waiting: "approve the spec" }),
    f("dev-loop", "mutation-go", 5, { tasks: tasks(3, 6), decisions: ["kept mutago as default adapter"] }),
    f("dev-loop", "release-carry", 7, { track: "S", waiting: "merge the PR (human path)" }),
    f("shop-api", "index-advisor", 5, { tasks: tasks(1, 4) }),
    f("shop-api", "query-cache", 5, { tasks: tasks(4, 5), track: "L" }),
    f("shop-api", "bulk-export", 5, { tasks: tasks(0, 3) }),
    f("dev-loop", "notes-migrate", 7, { shipped_at: iso(30) }),
  ];
  if (scenario === "busy") {
    const w = (n, stage, wait, extra = {}) => Array.from({ length: n }, (_, i) => f(i % 2 ? "shop-api" : "dev-loop", `${["spec", "acc", "plan", "build", "gate", "ship"][stage - 2]}-item-${i + 1}`, stage,
      { ...(i < wait ? { waiting: stage === 2 ? "approve the spec" : stage === 7 ? "merge the PR (human path)" : "a stop item" } : {}), ...extra }));
    features.splice(0, features.length, ...w(3, 2, 3), ...w(1, 3, 0), ...w(12, 5, 1, { tasks: tasks(2, 5) }), ...w(2, 6, 2), ...w(3, 7, 1));
  }
  const STAGES = ["Frame", "Spec", "Acceptance", "Plan", "Build", "Gate", "Ship"];
  const active = features.filter((x) => !x.shipped_at);
  const counts = STAGES.map((s, i) => ({ n: i + 1, s, c: active.filter((x) => x.stage === i + 1).length, you: active.filter((x) => x.stage === i + 1 && x.waiting).length }));
  const stamp = `Updated ${new Date(now).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  return { features, counts, needs: active.filter((x) => x.waiting).length, stamp };
}
// Draw the board with header `key` into `app`; returns nothing.
function protoDraw(app, key, now, logos, opts, scenario) {
  const s = protoSample(now, scenario);
  app.innerHTML = renderBoard(s.features, now, { here: "m1", me: "tayebchlyah", ...opts });
  const dlb = app.querySelector(".dlb");
  dlb.querySelectorAll(".hd, .you-panel, .counts").forEach((n) => n.remove());
  dlb.insertAdjacentHTML("afterbegin", protoHeader(key, { ...s, ...logos }));
}
