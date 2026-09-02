import type { Editor } from "@tiptap/core";
import { Bold, Code2, Heading1, Heading2, Italic, Link2, List, Paperclip, Quote, Redo2, Undo2 } from "lucide-react";

export interface DocumentToolbarProps {
  editor: Editor | null;
  toggleInsertMenu: () => void;
  uploadAttachment: (file?: File) => Promise<void>;
}

export function DocumentToolbar({ editor, toggleInsertMenu, uploadAttachment }: DocumentToolbarProps) {
  const editLink = () => {
    const url = window.prompt("输入链接地址", editor?.getAttributes("link").href || "https://");
    if (url === null) return;
    if (!url.trim()) editor?.chain().focus().unsetLink().run();
    else editor?.chain().focus().extendMarkRange("link").setLink({ href: url.trim() }).run();
  };

  return (
    <div className="docs-formatbar" role="toolbar" aria-label="文档格式">
      <button onClick={() => editor?.chain().focus().toggleBold().run()} className={editor?.isActive("bold") ? "active" : ""} title="粗体"><Bold size={17} /></button>
      <button onClick={() => editor?.chain().focus().toggleItalic().run()} className={editor?.isActive("italic") ? "active" : ""} title="斜体"><Italic size={17} /></button>
      <span />
      <button onClick={() => editor?.chain().focus().setHeading({ level: 1 }).run()} title="一级标题"><Heading1 size={17} /></button>
      <button onClick={() => editor?.chain().focus().setHeading({ level: 2 }).run()} title="二级标题"><Heading2 size={17} /></button>
      <button onClick={() => editor?.chain().focus().toggleBulletList().run()} title="列表"><List size={17} /></button>
      <button onClick={() => editor?.chain().focus().toggleBlockquote().run()} title="引用"><Quote size={17} /></button>
      <button onClick={() => editor?.chain().focus().toggleCodeBlock().run()} title="代码块"><Code2 size={17} /></button>
      <button onClick={editLink} title="链接"><Link2 size={17} /></button>
      <span />
      <button onClick={() => editor?.chain().focus().undo().run()} title="撤销"><Undo2 size={17} /></button>
      <button onClick={() => editor?.chain().focus().redo().run()} title="重做"><Redo2 size={17} /></button>
      <button onClick={toggleInsertMenu} title="插入内容">/</button>
      <label className="docs-formatbar__upload" title="上传附件"><Paperclip size={17} /><input type="file" onChange={(event) => void uploadAttachment(event.target.files?.[0])} /></label>
    </div>
  );
}
