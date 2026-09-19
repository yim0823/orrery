// Fail if any mermaid diagram in the docs does not parse.
//
// A broken diagram is invisible locally and loud on GitHub: the block renders as a red
// error box in the middle of the page. Four of them shipped at once because `call` is a
// flowchart keyword (`click X call fn()`), so using it as a subgraph id killed the parse
// — and nothing in a Python test suite was ever going to notice.
//
//   node scripts/check_mermaid.mjs README.md README.ko.md docs/*.md
import fs from "node:fs";
import path from "node:path";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Element = dom.window.Element;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});

const { default: mermaid } = await import("mermaid/dist/mermaid.esm.mjs");

let parsed = 0;
const failures = [];
for (const file of process.argv.slice(2)) {
  const blocks = [...fs.readFileSync(file, "utf8").matchAll(/```mermaid\n([\s\S]*?)```/g)];
  for (const [i, match] of blocks.entries()) {
    parsed++;
    try {
      await mermaid.parse(match[1]);
    } catch (error) {
      const message = String(error?.message ?? error).split("\n").slice(0, 3).join(" / ");
      failures.push(`${path.basename(file)} diagram #${i + 1}: ${message}`);
    }
  }
}

if (failures.length) {
  console.error("mermaid diagrams that will not render:\n");
  console.error(failures.map((f) => `  ${f}`).join("\n"));
  process.exit(1);
}
console.log(`${parsed} mermaid diagram(s) parse`);
