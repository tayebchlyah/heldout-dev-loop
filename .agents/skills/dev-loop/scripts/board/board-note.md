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
dv.el("div", "").innerHTML = renderBoard(features, Date.now(), { newerSchema });
```
