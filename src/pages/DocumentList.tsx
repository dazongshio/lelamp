import { ArchiveRestore, FileText, Plus, Trash2 } from "lucide-react";
import type { CollaborativeDocument } from "../api/documents";
import { formatRelative, friendlySource, type LibraryView } from "./documentPageSupport";

export interface DocumentListProps {
  documents: CollaborativeDocument[];
  libraryView: LibraryView;
  loading: boolean;
  selectDocument: (id: string) => void;
  createDocument: () => Promise<void>;
  restoreDocument: (document: CollaborativeDocument) => Promise<void>;
  purgeDocument: (document: CollaborativeDocument) => Promise<void>;
}

export function DocumentList(props: DocumentListProps) {
  const { documents, libraryView, loading } = props;
  return (
    <div className="docs-grid">
      {documents.map((document) => libraryView === "trash" ? (
        <article className="docs-trash-row" key={document.id}>
          <button className="docs-trash-row__document" onClick={() => props.selectDocument(document.id)}>
            <span className="docs-card__icon"><FileText size={23} /></span><strong>{document.title}</strong>
          </button>
          <span>个人空间</span><time>{formatRelative(document.updated_at)}</time>
          <div className="docs-trash-row__actions">
            <button onClick={() => void props.restoreDocument(document)}><ArchiveRestore size={15} />恢复</button>
            <button className="docs-trash-row__purge" onClick={() => void props.purgeDocument(document)}><Trash2 size={15} />永久删除</button>
          </div>
        </article>
      ) : (
        <button className="docs-card" key={document.id} onClick={() => props.selectDocument(document.id)}>
          <div className="docs-card__icon"><FileText size={23} /></div><strong>{document.title}</strong>
          <p>{document.excerpt || "空白文档"}</p>
          <div><span>{friendlySource(document.source_type)}</span><span>个人空间</span><time>{formatRelative(document.updated_at)}</time></div>
        </button>
      ))}
      {!loading && !documents.length && (
        <div className="docs-empty">
          {libraryView === "trash" ? <Trash2 size={38} /> : <FileText size={38} />}
          <h2>{libraryView === "trash" ? "回收站是空的" : "还没有文档"}</h2>
          <p>{libraryView === "trash" ? "移入回收站的文档会显示在这里。" : "新建空白文档，或导入现有 Markdown 文件。"}</p>
          {libraryView !== "trash" && <button className="docs-primary-button" onClick={() => void props.createDocument()}><Plus size={17} />新建文档</button>}
        </div>
      )}
    </div>
  );
}
