import { useCallback, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { listCollaborativeDocuments, type CollaborativeDocument } from "../api/documents";
import type { LibraryView } from "./documentPageSupport";

export interface DocumentWorkspaceQuery {
  libraryView: LibraryView;
  activeFolder: string;
  query: string;
  selectedId: string;
  clearSelection: () => void;
}

export function useDocumentWorkspace({ libraryView, activeFolder, query, selectedId, clearSelection }: DocumentWorkspaceQuery) {
  const [documents, setDocuments] = useState<CollaborativeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reloadDocuments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await listCollaborativeDocuments({
        status: libraryView === "trash" ? "trashed" : "active",
        query,
        sourceType: libraryView === "meeting" ? "meeting" : libraryView === "scan" ? "scan" : undefined,
        spaceId: activeFolder || undefined,
      });
      let items = response.data.documents;
      if (libraryView === "mine") items = items.filter((item) => item.role === "owner");
      if (libraryView === "shared") items = items.filter((item) => item.role !== "owner");
      if (libraryView === "favorite") items = items.filter((item) => item.favorite);
      setDocuments(items);
      if (selectedId && !items.some((item) => item.id === selectedId)) clearSelection();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [activeFolder, clearSelection, libraryView, query, selectedId]);

  return { documents, setDocuments, loading, error, setError, reloadDocuments };
}
