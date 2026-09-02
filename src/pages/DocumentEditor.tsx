import { Check, ChevronLeft, MessageSquare, MoreHorizontal, Share2 } from "lucide-react";
import type { ReactNode } from "react";
import type { CollaborativeDocument } from "../api/documents";
import type { SaveState } from "./documentPageSupport";
import type { CollaborationState, OnlineMember } from "./useDocumentCollaboration";

export interface DocumentEditorProps {
  document: CollaborativeDocument;
  saveState: SaveState;
  notice: string;
  collaborationState: CollaborationState;
  onlineMembers: OnlineMember[];
  widePage: boolean;
  goBack: () => void;
  renameDocument: (title: string) => Promise<void>;
  openComments: () => void;
  openSharing: () => void;
  toggleWidePage: () => void;
  children: ReactNode;
}

export function DocumentEditor(props: DocumentEditorProps) {
  const { document, saveState, notice, collaborationState, onlineMembers, widePage, children } = props;
  return (
    <section className="docs-editor-shell">
      <header className="docs-editor-topbar">
        <button className="docs-icon-button" onClick={props.goBack} aria-label="返回文档列表"><ChevronLeft size={20} /></button>
        <div className="docs-title-wrap">
          <input className="docs-title-input" defaultValue={document.title} key={`${document.id}:${document.title}`} disabled={!document.can_edit} onBlur={(event) => void props.renameDocument(event.target.value)} aria-label="文档标题" />
          <div className={`docs-save-state docs-save-state--${saveState}`}>
            {saveState === "saved" && <><Check size={13} />已保存</>}
            {saveState === "saving" && "正在保存…"}
            {saveState === "dirty" && "等待保存…"}
            {saveState === "offline" && "离线，修改尚未同步"}
            {saveState === "conflict" && "发现其他修改，请刷新"}
            {notice && ` · ${notice}`}
          </div>
        </div>
        <div className={`docs-presence docs-presence--${collaborationState}`} title="当前在线成员">
          <span>{onlineMembers[0]?.name?.slice(0, 1) || "本"}</span>
          <small>{collaborationState === "online" ? `${Math.max(1, onlineMembers.length)} 人在线` : collaborationState === "connecting" ? "正在连接" : "离线编辑"}</small>
        </div>
        <button className="docs-top-button" onClick={props.openComments}><MessageSquare size={17} />评论</button>
        <button className="docs-top-button docs-top-button--share" onClick={props.openSharing}><Share2 size={17} />分享</button>
        <button className="docs-icon-button" aria-label={widePage ? "切换为标准宽度" : "切换为宽页面"} title={widePage ? "标准宽度" : "宽页面"} onClick={props.toggleWidePage}><MoreHorizontal size={19} /></button>
      </header>
      {children}
    </section>
  );
}
