import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { Server } from "@hocuspocus/server";
import * as Y from "yjs";

const port = Number(process.env.LELAMP_COLLAB_PORT || 8791);
const host = process.env.LELAMP_COLLAB_HOST || "0.0.0.0";
const workspace = path.resolve(process.env.OPENCLAW_WORKSPACE || path.join(process.cwd(), "lelamp_runtime", "workspace"));
const storageRoot = path.join(workspace, ".documents", "collaboration");
const secret = String(process.env.LELAMP_WEB_TOKEN || "");

if (!secret) {
  throw new Error("LELAMP_WEB_TOKEN is required for collaboration token verification.");
}

await fs.mkdir(storageRoot, { recursive: true });

function decodeToken(token, documentName) {
  const [encoded, signature] = String(token || "").split(".", 2);
  if (!encoded || !signature) throw new Error("协作令牌无效。");
  const expected = crypto.createHmac("sha256", secret).update(encoded).digest("hex");
  const left = Buffer.from(signature);
  const right = Buffer.from(expected);
  if (left.length !== right.length || !crypto.timingSafeEqual(left, right)) throw new Error("协作令牌签名无效。");
  const padding = "=".repeat((4 - (encoded.length % 4)) % 4);
  const payload = JSON.parse(Buffer.from(`${encoded}${padding}`, "base64url").toString("utf8"));
  if (payload.document_id !== documentName) throw new Error("协作令牌不属于当前文档。");
  if (Number(payload.exp || 0) < Math.floor(Date.now() / 1000)) throw new Error("协作令牌已过期。");
  if (!["owner", "editor", "commenter", "viewer"].includes(payload.role)) throw new Error("协作角色无效。");
  return payload;
}

function storagePath(documentName) {
  if (!/^[a-f0-9]{32}$/.test(documentName)) throw new Error("文档 ID 无效。");
  return path.join(storageRoot, `${documentName}.bin`);
}

const server = Server.configure({
  name: "LeLamp 文档协作服务",
  port,
  address: host,
  debounce: 700,
  maxDebounce: 3000,
  timeout: 30000,
  async onAuthenticate({ token, documentName, connection }) {
    const user = decodeToken(token, documentName);
    connection.readOnly = !["owner", "editor"].includes(user.role);
    return { user };
  },
  async onLoadDocument({ documentName }) {
    const document = new Y.Doc();
    try {
      const update = await fs.readFile(storagePath(documentName));
      Y.applyUpdate(document, update);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    return document;
  },
  async onStoreDocument({ documentName, document }) {
    const target = storagePath(documentName);
    const temporary = `${target}.${crypto.randomUUID()}.tmp`;
    await fs.writeFile(temporary, Y.encodeStateAsUpdate(document));
    await fs.rename(temporary, target);
  },
});

await server.listen();
console.log(JSON.stringify({ status: "ready", host, port, storageRoot }));

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    await server.destroy();
    process.exit(0);
  });
}
