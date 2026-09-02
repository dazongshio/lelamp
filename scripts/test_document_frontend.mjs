import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import WebSocket from "ws";

const cacheDirectory = path.join(process.cwd(), "node_modules", ".cache", "lelamp-tests");
fs.mkdirSync(cacheDirectory, { recursive: true });
const output = path.join(cacheDirectory, `lelamp-documents-page-${process.pid}.mjs`);
await build({
  entryPoints: ["src/pages/DocumentsPage.tsx"],
  outfile: output,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  jsx: "automatic",
  loader: { ".css": "empty" },
  external: ["react", "react/*", "react-dom", "react-dom/*", "lucide-react"],
});

const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://127.0.0.1:8790/documents?token=frontend-test-token",
  pretendToBeVisual: true,
});
for (const key of [
  "window", "document", "navigator", "localStorage", "sessionStorage", "DOMParser",
  "Node", "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "MutationObserver",
  "getComputedStyle", "Event", "MouseEvent", "KeyboardEvent", "DOMException",
  "FocusEvent",
  "File", "FileReader",
]) {
  globalThis[key] = dom.window[key];
}
globalThis.WebSocket = WebSocket;
dom.window.WebSocket = WebSocket;
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
globalThis.cancelAnimationFrame = (timer) => clearTimeout(timer);
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
let downloadedFilename = "";
dom.window.URL.createObjectURL = () => "blob:frontend-export";
dom.window.URL.revokeObjectURL = () => {};
const originalAnchorClick = dom.window.HTMLAnchorElement.prototype.click;
dom.window.HTMLAnchorElement.prototype.click = function click() {
  downloadedFilename = this.download;
};
Object.defineProperty(dom.window.navigator, "clipboard", {
  configurable: true,
  value: { writeText: async (value) => { copiedShareUrl = String(value); } },
});

let documentItem = null;
let comments = [];
let attachments = [];
let copiedShareUrl = "";
let shareCreated = false;
let exportRequested = false;
const now = new Date().toISOString();
const envelope = (data, status = 200) => new Response(JSON.stringify({ ok: true, data }), {
  status,
  headers: { "Content-Type": "application/json" },
});

globalThis.fetch = async (input, init = {}) => {
  const url = new URL(String(input), dom.window.location.origin);
  const method = String(init.method || "GET").toUpperCase();
  if (url.pathname === "/api/docs" && method === "GET") {
    return envelope({ status: "completed", documents: documentItem ? [documentItem] : [], count: documentItem ? 1 : 0 });
  }
  if (url.pathname === "/api/docs" && method === "POST") {
    const body = JSON.parse(String(init.body || "{}"));
    documentItem = {
      id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      engine: "native",
      title: body.title || "无标题文档",
      space_id: "personal",
      owner_id: "lelamp-web",
      owner_name: "本机用户",
      status: "active",
      source_type: "manual",
      source_path: "",
      created_at: now,
      updated_at: now,
      content_version: 1,
      excerpt: "",
      role: "owner",
      can_edit: true,
      can_comment: true,
      content: "# 无标题文档\n\n",
      permissions: [{ principal_type: "user", principal_id: "lelamp-web", display_name: "本机用户", role: "owner" }],
      favorite: false,
    };
    return envelope({ status: "completed", document: documentItem });
  }
  const match = url.pathname.match(/^\/api\/docs\/([a-f0-9]{32})(?:\/(.+))?$/);
  if (match) {
    const resource = match[2] || "";
    if (!resource && method === "GET") return envelope({ status: "completed", document: documentItem });
    if (resource === "comments" && method === "GET") return envelope({ status: "completed", comments });
    if (resource === "comments" && method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      comments = [...comments, {
        id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        document_id: match[1],
        parent_id: "",
        body: body.body,
        anchor_text: body.anchor_text || "",
        author_id: "lelamp-web",
        author_name: "本机用户",
        created_at: now,
        updated_at: now,
        resolved: false,
      }];
      return envelope({ status: "completed", comment: comments.at(-1) });
    }
    if (resource === "history") return envelope({ status: "completed", revisions: [] });
    if (resource === "attachments" && method === "GET") return envelope({ status: "completed", attachments });
    if (resource === "attachments" && method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      attachments = [{
        id: "cccccccccccccccccccccccccccccccc",
        document_id: match[1],
        filename: body.filename,
        mime_type: body.mime_type || "text/plain",
        size: 4,
        checksum: "test",
        created_by: "lelamp-web",
        created_by_name: "本机用户",
        created_at: now,
      }];
      return envelope({ status: "completed", attachment: attachments[0] });
    }
    if (resource === "permissions" && method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      documentItem = { ...documentItem, permissions: body.permissions };
      return envelope({ status: "completed", permissions: body.permissions });
    }
    if (resource === "share-token" && method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      assert.equal(body.principal_id, "reviewer");
      shareCreated = true;
      return envelope({
        status: "completed",
        share_url: "http://127.0.0.1:8790/documents/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?document_session=scoped",
        expires_at: Math.floor(Date.now() / 1000) + 604800,
      });
    }
    if (resource === "export" && method === "GET") {
      exportRequested = true;
      return envelope({
        status: "completed",
        filename: "产品需求文档.md",
        content_base64: Buffer.from("# 产品需求文档\n\n验收内容\n", "utf8").toString("base64"),
      });
    }
    if (resource === "collaboration-token") {
      return envelope({
        status: "completed",
        document_id: match[1],
        token: "unreachable-test-token",
        expires_at: Math.floor(Date.now() / 1000) + 300,
        url: "ws://127.0.0.1:1",
        user: { id: "frontend", name: "前端测试", color: "#3370ff" },
      });
    }
    if (resource === "update" && method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      documentItem = {
        ...documentItem,
        ...(body.title !== undefined ? { title: body.title } : {}),
        ...(body.content !== undefined ? { content: body.content, content_version: documentItem.content_version + 1 } : {}),
        updated_at: new Date().toISOString(),
      };
      return envelope({ status: "completed", document: documentItem });
    }
  }
  throw new Error(`未模拟的请求：${method} ${url.pathname}`);
};

const React = await import("react");
const { createRoot } = await import("react-dom/client");
const { DocumentsPage } = await import(`${pathToFileURL(output).href}?v=${Date.now()}`);
const root = createRoot(document.getElementById("root"));
root.render(React.createElement(DocumentsPage));

async function waitFor(check, message, timeout = 6000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = check();
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(message);
}

const buttonByText = (text) => [...document.querySelectorAll("button")].find((button) => button.textContent?.includes(text));
await waitFor(() => document.querySelector('button[aria-label="新建文档"]'), "未显示新建文档入口");
document.querySelector('button[aria-label="新建文档"]').click();
await waitFor(() => buttonByText("空白文档"), "未显示中文模板菜单");
buttonByText("空白文档").click();

const titleInput = await waitFor(() => document.querySelector('input[aria-label="文档标题"]'), "新建后未进入编辑页");
const valueSetter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set;
valueSetter.call(titleInput, "产品需求文档");
titleInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
titleInput.dispatchEvent(new dom.window.FocusEvent("focusout", { bubbles: true }));
await waitFor(() => documentItem?.title === "产品需求文档", "重命名没有保存到后端");

buttonByText("评论").click();
const commentBox = await waitFor(() => document.querySelector(".docs-comment-box textarea"), "未打开评论面板");
const textareaSetter = Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value").set;
textareaSetter.call(commentBox, "请确认发布范围");
commentBox.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
await waitFor(() => !buttonByText("发送评论")?.disabled, "评论输入未生效");
buttonByText("发送评论").click();
await waitFor(() => document.body.textContent?.includes("请确认发布范围"), "评论没有显示");

buttonByText("分享").click();
await waitFor(() => document.body.textContent?.includes("共享与权限"), "未显示中文共享与权限区域");
assert.match(document.body.textContent, /所有者/);
assert.match(document.body.textContent, /AI 文档助手|AI/);
assert.equal(document.body.textContent.includes("Docmost"), false);

const collaboratorInput = document.querySelector('input[placeholder="成员标识或用户名"]');
valueSetter.call(collaboratorInput, "reviewer");
collaboratorInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
await waitFor(() => !buttonByText("添加")?.disabled, "协作者输入未生效");
buttonByText("添加").click();
await waitFor(() => document.body.textContent?.includes("reviewer"), "共享权限没有保存");
buttonByText("复制链接").click();
await waitFor(() => shareCreated && copiedShareUrl.includes("document_session="), "未生成限定文档的分享链接");

const attachmentInput = document.querySelector('.docs-info input[type="file"]');
const attachmentFile = new dom.window.File(["测试"], "验收附件.txt", { type: "text/plain" });
Object.defineProperty(attachmentInput, "files", { configurable: true, value: [attachmentFile] });
attachmentInput.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
await waitFor(() => document.body.textContent?.includes("验收附件.txt"), "附件上传后未显示");

buttonByText("导出 Markdown").click();
await waitFor(() => exportRequested && downloadedFilename === "产品需求文档.md", "Markdown 导出没有触发下载");

console.log(JSON.stringify({
  status: "passed",
  flow: ["新建", "进入编辑器", "重命名", "发表评论", "设置共享权限", "生成分享链接", "上传附件", "导出 Markdown"],
  chinese_ui: true,
}, null, 2));
root.unmount();
dom.window.HTMLAnchorElement.prototype.click = originalAnchorClick;
fs.unlinkSync(output);
process.exit(0);
