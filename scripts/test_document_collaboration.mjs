import process from "node:process";
import { HocuspocusProvider, HocuspocusProviderWebsocket } from "@hocuspocus/provider";
import WebSocket from "ws";
import * as Y from "yjs";

const [url, documentName, token] = process.argv.slice(2);
if (!url || !documentName || !token) {
  throw new Error("Usage: node scripts/test_document_collaboration.mjs <url> <document-id> <token>");
}

const firstDocument = new Y.Doc();
const secondDocument = new Y.Doc();
function createProvider(document, authToken = token) {
  const websocketProvider = new HocuspocusProviderWebsocket({
    url,
    connect: false,
    WebSocketPolyfill: WebSocket,
  });
  return new HocuspocusProvider({ name: documentName, token: authToken, websocketProvider, document });
}

const first = createProvider(firstDocument);
const second = createProvider(secondDocument);

function waitFor(provider, event, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const handler = (payload) => {
      clearTimeout(timer);
      provider.off(event, handler);
      resolve(payload);
    };
    const timer = setTimeout(() => {
      provider.off(event, handler);
      reject(new Error(`等待 ${event} 超时`));
    }, timeout);
    provider.on(event, handler);
  });
}

const synced = Promise.all([waitFor(first, "synced"), waitFor(second, "synced")]);
void first.connect();
void second.connect();
await synced;

const firstText = firstDocument.getText("markdown");
const secondText = secondDocument.getText("markdown");
firstText.insert(firstText.length, "\n客户端甲");
secondText.insert(secondText.length, "\n客户端乙");

const started = Date.now();
while (
  (firstText.toString() !== secondText.toString()
    || !firstText.toString().includes("客户端甲")
    || !firstText.toString().includes("客户端乙"))
  && Date.now() - started < 8000
) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

second.disconnect();
secondText.insert(secondText.length, "\n离线修改");
firstText.insert(firstText.length, "\n在线修改");
const reconnectStarted = Date.now();
void second.connect();
while (
  (firstText.toString() !== secondText.toString()
    || !firstText.toString().includes("离线修改")
    || !firstText.toString().includes("在线修改"))
  && Date.now() - reconnectStarted < 8000
) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

const passed = firstText.toString() === secondText.toString()
  && firstText.toString().includes("客户端甲")
  && firstText.toString().includes("客户端乙")
  && firstText.toString().includes("离线修改")
  && firstText.toString().includes("在线修改");

const forgedToken = `${token.slice(0, -1)}${token.endsWith("a") ? "b" : "a"}`;
const unauthorized = createProvider(new Y.Doc(), forgedToken);
const rejected = waitFor(unauthorized, "authenticationFailed", 5000).then(() => true);
void unauthorized.connect();
const forgedTokenRejected = await rejected;
unauthorized.destroy();
const result = {
  status: passed && forgedTokenRejected ? "passed" : "failed",
  first: firstText.toString(),
  second: secondText.toString(),
  duration_ms: Date.now() - started,
  reconnect_ms: Date.now() - reconnectStarted,
  forged_token_rejected: forgedTokenRejected,
};

first.destroy();
second.destroy();
console.log(JSON.stringify(result, null, 2));
await new Promise((resolve) => setTimeout(resolve, 100));
process.exit(result.status === "passed" ? 0 : 1);
