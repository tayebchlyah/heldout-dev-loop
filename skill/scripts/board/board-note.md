---
version: "/*VERSION*/"
---

```dataviewjs
/*RENDER_JS*/

// Glue: every status note in the vault, by its raw frontmatter (not Dataview values).
let newerSchema = 0;
const features = [];
for (const p of dv.pages().where((p) => p.file.frontmatter && p.file.frontmatter.type === "dev-loop-status")) {
  const note = p.file.frontmatter;
  if (note.schema === 1) features.push(note);
  else if (Number(note.schema) > 1) newerSchema += 1;
}
// allow-comment: dependency: Obsidian mobile has no Node require; the board then keeps name order.
let viewer = {};
try {
  viewer = JSON.parse(require("fs").readFileSync(require("os").homedir() + "/.config/dev-loop/machine.json", "utf8")) || {};
} catch (e) {}
let open = new Set();
try {
  const keys = JSON.parse(localStorage.getItem("dlb-open"));
  if (Array.isArray(keys)) open = new Set(keys.filter((k) => typeof k === "string"));
} catch (e) {}
const el = dv.el("div", "");
el.innerHTML = renderBoard(features, Date.now(), { newerSchema, here: viewer.id, me: viewer.owner, open });
el.addEventListener("toggle", (e) => {
  const k = e.target.dataset && e.target.dataset.key;
  if (!k) return;
  e.target.open ? open.add(k) : open.delete(k);
  try {
    localStorage.setItem("dlb-open", JSON.stringify([...open]));
  } catch (err) {}
}, true);
```
