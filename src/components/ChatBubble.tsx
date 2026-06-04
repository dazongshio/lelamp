import type { AssistantMessage } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import "./components.css";

export function ChatBubble({ message }: { message: AssistantMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`chat-bubble ${isUser ? "chat-bubble--user" : "chat-bubble--assistant"}`}>
      <div className="chat-bubble__meta">
        <span>{isUser ? "lelamp-admin" : "Assistant"}</span>
        <span>{message.time}</span>
        {message.status && <StatusBadge status={message.status} />}
      </div>
      <p>{message.text}</p>
      {message.attachment && <div className="chat-bubble__attachment">{message.attachment}</div>}
    </div>
  );
}
