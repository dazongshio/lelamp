import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const requiredFiles = [
  "index.html",
  "meeting.html",
  "documents.html",
  "results.html",
  "styles.css",
  "app.js",
];

const requiredText = {
  "index.html": [
    "AI 助手运行在树莓派",
    "树莓派 AI 服务",
    "沙箱控制",
    "全权控制",
    "SSH host reachable",
    "direct computer control enabled",
    "沙箱控制不直连电脑",
    "全权控制通过 SSH 直接控制电脑",
    "请求全权控制",
  ],
  "meeting.html": [
    "投影状态",
    "音频采集",
    "实时转写",
    "自动生成纪要",
    "导出授权",
  ],
  "documents.html": [
    "树莓派共享空间",
    "常开共享文件夹",
    "文件内容预览",
    "AI 交互区",
    "来源范围",
    "生成结果",
    "电脑控制权限",
    "沙箱控制不直连电脑",
    "全权控制通过 SSH 直接控制电脑",
  ],
  "results.html": [
    "结果中心",
    "Preview drawer",
    "Detail page",
    "授权面板",
    "会议纪要",
    "合同表格",
  ],
  "styles.css": [
    ".sidebar",
    ".result-flow",
    ".command-box",
    ".preview-drawer",
    ".permission-row",
    ".grid.documents",
  ],
};

for (const file of requiredFiles) {
  const filePath = path.join(root, file);
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing required file: ${file}`);
  }
}

for (const [file, terms] of Object.entries(requiredText)) {
  const content = fs.readFileSync(path.join(root, file), "utf8");
  for (const term of terms) {
    if (!content.includes(term)) {
      throw new Error(`Missing required text in ${file}: ${term}`);
    }
  }
}

console.log("Static page checks passed");
