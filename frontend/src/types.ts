export type MessageRole = "user" | "assistant";

export type ClarificationOption = {
  label: string;
  value: string;
};

export type ClarificationResult = {
  is_complete: boolean;
  question?: string | null;
  missing_fields: Array<"period" | "geography" | "metric" | "other">;
  options: ClarificationOption[];
  reason: string;
};

export type ChatMessage = {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  clarification?: ClarificationResult;
  clarificationStatus?: "pending" | "answered";
};

export type PendingClarification = {
  id: string;
  message: string;
  sendMode: SendMode;
  assistantMessageId: string;
  userMessageId: string;
  clarification: ClarificationResult;
  createdAt: string;
};

export type ChatCheckpoint = {
  id: string;
  title: string;
  createdAt: string;
  messages: ChatMessage[];
  pendingClarification?: PendingClarification;
};

export type ChatSession = {
  id: string;
  conversationId?: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  pendingClarification?: PendingClarification;
  checkpoints?: ChatCheckpoint[];
};

export type PersistedChatState = {
  version: 1;
  activeSessionId?: string;
  sessions: ChatSession[];
};

export type SendMode = "normal" | "clarify";
