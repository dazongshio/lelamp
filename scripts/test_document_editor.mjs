import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body><div id=\"editor\"></div></body></html>", {
  pretendToBeVisual: true,
});
for (const key of [
  "window", "document", "navigator", "DOMParser", "Node", "HTMLElement",
  "MutationObserver", "getComputedStyle", "Event", "KeyboardEvent",
]) {
  globalThis[key] = dom.window[key];
}
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
globalThis.cancelAnimationFrame = (timer) => clearTimeout(timer);

const [
  { Editor },
  { default: StarterKit },
  { default: TaskList },
  { default: TaskItem },
  { default: Table },
  { default: TableRow },
  { default: TableHeader },
  { default: TableCell },
] = await Promise.all([
  import("@tiptap/core"),
  import("@tiptap/starter-kit"),
  import("@tiptap/extension-task-list"),
  import("@tiptap/extension-task-item"),
  import("@tiptap/extension-table"),
  import("@tiptap/extension-table-row"),
  import("@tiptap/extension-table-header"),
  import("@tiptap/extension-table-cell"),
]);

const editor = new Editor({
  element: document.getElementById("editor"),
  extensions: [
    StarterKit,
    TaskList,
    TaskItem,
    Table,
    TableRow,
    TableHeader,
    TableCell,
  ],
  content: "<p></p>",
});

editor.commands.focus();
const originalView = editor.view;
const typed = "持续输入不会丢字。".repeat(80);
for (const character of typed) {
  assert.equal(editor.commands.insertContent(character), true);
}
assert.equal(editor.state.doc.textContent, typed);
assert.equal(editor.view, originalView);
assert.equal(editor.state.selection.from, typed.length + 1);

const largeText = "十万字性能验证内容。".repeat(10_000);
const started = performance.now();
editor.commands.setContent(`<h1>十万字文档</h1><p>${largeText}</p>`);
editor.commands.insertContentAt(editor.state.doc.content.size - 1, "末");
const elapsed = performance.now() - started;
assert.ok(editor.state.doc.textContent.length > 100_000);
assert.ok(editor.state.doc.textContent.endsWith("末"));
assert.ok(elapsed < 5_000, `十万字编辑耗时过长：${elapsed}ms`);

console.log(JSON.stringify({
  status: "passed",
  continuous_input_chars: typed.length,
  focus_preserved: true,
  large_document_chars: editor.state.doc.textContent.length,
  large_document_edit_ms: Math.round(elapsed),
}, null, 2));
editor.destroy();
process.exit(0);
