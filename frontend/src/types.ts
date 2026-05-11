export type MessageRole = "user" | "assistant";

export type ClarificationOption = {
  label: string;
  value: string;
};

export type ClarificationField = "period" | "geography" | "metric" | "formula" | "other";

export type ClarificationStep = {
  field: ClarificationField;
  question?: string | null;
  reason?: string;
  options: ClarificationOption[];
};

export type ClarificationResult = {
  is_complete: boolean;
  question?: string | null;
  missing_fields: ClarificationField[];
  options: ClarificationOption[];
  steps?: ClarificationStep[];
  reason: string;
};

export type AgentTraceEventType = "thought" | "tool_call" | "tool_result" | "iteration";
export type AgentTracePhase =
  | "analysis"
  | "planning"
  | "retrieval"
  | "sql"
  | "calculation"
  | "validation"
  | "finalization"
  | "clarification";
export type AgentTraceStatus = "running" | "done" | "retry" | "error";
export type AgentTraceVisibility = "summary" | "detail";

export type AgentTraceEvent = {
  id: string;
  type: AgentTraceEventType;
  title: string;
  tool?: string;
  payload?: unknown;
  phase?: AgentTracePhase;
  status?: AgentTraceStatus;
  visibility?: AgentTraceVisibility;
  createdAt: string;
};

export type ClarificationTurn = {
  id: string;
  clarification: ClarificationResult;
  selectedOption: ClarificationOption;
  traceEnd: number;
  createdAt: string;
};

export type ChatMessage = {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  clarification?: ClarificationResult;
  clarificationStatus?: "pending" | "answered";
  clarificationTraceEnd?: number;
  clarificationHistory?: ClarificationTurn[];
  agentTrace?: AgentTraceEvent[];
};

export type PendingClarification = {
  id: string;
  message: string;
  sendMode: SendMode;
  assistantMessageId: string;
  userMessageId: string;
  clarification: ClarificationResult;
  stepIndex?: number;
  traceEnd: number;
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
