import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { JSDOM } from "jsdom";

const output = path.join(os.tmpdir(), `lelamp-document-markdown-${process.pid}.mjs`);
await build({
  entryPoints: ["src/utils/documentMarkdown.ts"],
  outfile: output,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
});

const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;

const { markdownToHtml, htmlToMarkdown } = await import(`${pathToFileURL(output).href}?v=${Date.now()}`);
const source = `# 中文标题

- 普通列表
- [ ] 待办事项
- [x] 已完成事项

> 引用内容

\`\`\`js
console.log("代码");
\`\`\`

| 姓名 | 状态 |
| --- | --- |
| 小明 | 完成 |

[内部链接](/documents?document=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)

![图片](images/device.png)

<aside data-type="callout">
提示内容
</aside>

<script>globalThis.hacked = true</script>
<img src="javascript:alert(1)" onerror="alert(2)">
`;

const html = markdownToHtml(source);
assert.match(html, /<h1>中文标题<\/h1>/);
assert.match(html, /data-type="taskList"/);
assert.match(html, /data-type="taskItem"/);
assert.match(html, /<table>/);
assert.match(html, /<blockquote>/);
assert.match(html, /<pre><code class="language-js">/);
assert.match(html, /href="\/documents\?document=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/);
assert.match(html, /src="images\/device.png"/);
assert.doesNotMatch(html, /<script|onerror|javascript:/i);
assert.match(html, /<aside data-type="callout">/);

const roundTrip = htmlToMarkdown(html);
assert.match(roundTrip, /^# 中文标题/m);
assert.match(roundTrip, /- \[ \] 待办事项/);
assert.match(roundTrip, /- \[x\] 已完成事项/i);
assert.match(roundTrip, /\| 姓名 \| 状态 \|/);
assert.match(roundTrip, /> 引用内容/);
assert.match(roundTrip, /```js/);
assert.match(roundTrip, /\[内部链接\]\(\/documents\?document=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\)/);
assert.match(roundTrip, /!\[图片\]\(images\/device.png\)/);
assert.match(roundTrip, /<aside data-type="callout">/);

console.log(JSON.stringify({
  status: "passed",
  preserved: ["标题", "列表", "待办", "引用", "代码块", "表格", "链接", "图片"],
  markdown_chars: roundTrip.length,
}, null, 2));
