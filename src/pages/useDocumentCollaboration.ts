import { HocuspocusProvider } from "@hocuspocus/provider";
import { useEffect, useRef, useState } from "react";
import * as Y from "yjs";
import { getCollaborationClientId } from "./documentPageSupport";

export type CollaborationState = "connecting" | "online" | "offline";
export interface OnlineMember { id: string; name: string; color: string }

export function useDocumentCollaboration() {
  const [collaborationState, setCollaborationState] = useState<CollaborationState>("offline");
  const [onlineMembers, setOnlineMembers] = useState<OnlineMember[]>([]);
  const collaborationText = useRef<Y.Text | null>(null);
  const collaborationMetadata = useRef<Y.Map<unknown> | null>(null);
  const collaborationProvider = useRef<HocuspocusProvider | null>(null);
  const collaborationOrigin = useRef({});
  const collaborationClientId = useRef(getCollaborationClientId());

  useEffect(() => () => {
    collaborationProvider.current?.destroy();
    collaborationProvider.current = null;
  }, []);

  return {
    collaborationState, setCollaborationState, onlineMembers, setOnlineMembers,
    collaborationText, collaborationMetadata, collaborationProvider,
    collaborationOrigin, collaborationClientId,
  };
}
