import { marked } from "marked";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

const turndown = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
});
turndown.use(gfm);
turndown.addRule("callout", {
  filter: (node) => node.nodeName === "ASIDE" && node.getAttribute("data-type") === "callout",
  replacement: (content) => `\n\n<aside data-type="callout">\n${content.trim()}\n</aside>\n\n`,
});
turndown.addRule("taskListItem", {
  filter: (node) => node.nodeName === "LI" && node.querySelector('input[type="checkbox"]') !== null,
  replacement: (content, node) => {
    const checkbox = node.querySelector<HTMLInputElement>('input[type="checkbox"]');
    const checked = Boolean(checkbox?.checked || checkbox?.hasAttribute("checked"));
    return `- [${checked ? "x" : " "}] ${content.trim()}\n`;
  },
});

export function markdownToHtml(markdown: string): string {
  const container = document.createElement("div");
  container.innerHTML = String(marked.parse(markdown || "", { gfm: true, breaks: false }));
  container.querySelectorAll("script,style,iframe,object,embed,meta,link,base,form").forEach((node) => node.remove());
  container.querySelectorAll<HTMLElement>("*").forEach((element) => {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim().toLowerCase().replace(/\s+/g, "");
      if (name.startsWith("on") || name === "srcdoc") {
        element.removeAttribute(attribute.name);
        continue;
      }
      const safeInlineImage = name === "src" && /^data:image\/(?:png|jpeg|gif|webp);base64,/.test(value);
      if ((name === "href" || name === "src") && /^(javascript|vbscript|data):/.test(value) && !safeInlineImage) {
        element.removeAttribute(attribute.name);
      }
    }
  });
  container.querySelectorAll<HTMLLIElement>("li").forEach((item) => {
    const checkbox = item.querySelector<HTMLInputElement>(':scope > input[type="checkbox"]');
    if (!checkbox) return;
    const checked = checkbox.checked;
    checkbox.remove();
    const content = document.createElement("div");
    while (item.firstChild) content.append(item.firstChild);
    const label = document.createElement("label");
    const renderedCheckbox = document.createElement("input");
    renderedCheckbox.type = "checkbox";
    renderedCheckbox.checked = checked;
    if (checked) renderedCheckbox.setAttribute("checked", "checked");
    const marker = document.createElement("span");
    label.append(renderedCheckbox, marker);
    item.setAttribute("data-type", "taskItem");
    item.setAttribute("data-checked", checked ? "true" : "false");
    item.append(label, content);
    item.parentElement?.setAttribute("data-type", "taskList");
  });
  return container.innerHTML;
}

export function htmlToMarkdown(html: string): string {
  return `${turndown.turndown(html).trim()}\n`;
}
