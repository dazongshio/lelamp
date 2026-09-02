import process from "node:process";
import { HocuspocusProvider, HocuspocusProviderWebsocket } from "@hocuspocus/provider";
import WebSocket from "ws";
import * as Y from "yjs";

const [url, documentName, token, ...expectedMarkers] = process.argv.slice(2);
if (!url || !documentName || !token || !expectedMarkers.length) {
  throw new Error("Usage: node scripts/check_document_collaboration_state.mjs <url> <document-id> <token> <marker...>");
}

const document = new Y.Doc();
const socket = new HocuspocusProviderWebsocket({ url, connect: false, WebSocketPolyfill: WebSocket });
const provider = new HocuspocusProvider({ name: documentName, token, websocketProvider: socket, document });
const synced = new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("等待持久化文档同步超时")), 8000);
  provider.on("synced", () => {
    clearTimeout(timer);
    resolve();
  });
});
void provider.connect();
await synced;
const content = document.getText("markdown").toString();
const passed = expectedMarkers.every((marker) => content.includes(marker));
console.log(JSON.stringify({ status: passed ? "passed" : "failed", expectedMarkers, content }, null, 2));
provider.destroy();
await new Promise((resolve) => setTimeout(resolve, 100));
process.exit(passed ? 0 : 1);
