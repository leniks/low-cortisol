export type MessageRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
};

export type ChatSession = {
  id: string;
  conversationId?: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  feedbackHiddenForMessageId?: string;
};

export type PersistedChatState = {
  version: 1;
  activeSessionId?: string;
  sessions: ChatSession[];
};

export type SendMode = "normal" | "clarify";
