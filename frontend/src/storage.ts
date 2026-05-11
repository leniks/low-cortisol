import type {
  AgentTraceEvent,
  AgentTracePhase,
  AgentTraceStatus,
  AgentTraceVisibility,
  ChatCheckpoint,
  ClarificationField,
  ClarificationStep,
  ClarificationTurn,
  ChatMessage,
  ChatSession,
  PendingClarification,
  PersistedChatState,
} from "./types";
import { chatClientHeaders } from "./chatApi";

const LEGACY_STORAGE_KEY = "mathmod.chat.sessions.v1";

export const emptyState: PersistedChatState = {
  version: 1,
  activeSessionId: undefined,
  sessions: [],
};

function isMessage(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  return (
    typeof message.id === "string" &&
    (message.role === "user" || message.role === "assistant") &&
    typeof message.content === "string" &&
    typeof message.createdAt === "string"
  );
}

function isClarificationOption(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const option = value as Record<string, unknown>;
  return typeof option.label === "string" && typeof option.value === "string";
}

function isClarificationField(value: unknown): value is ClarificationField {
  return value === "period" || value === "geography" || value === "metric" || value === "formula" || value === "other";
}

function isClarificationStep(value: unknown): value is ClarificationStep {
  if (!value || typeof value !== "object") return false;
  const step = value as Record<string, unknown>;
  return (
    isClarificationField(step.field) &&
    (typeof step.question === "string" || step.question === null || step.question === undefined) &&
    (typeof step.reason === "string" || step.reason === undefined) &&
    Array.isArray(step.options) &&
    step.options.every(isClarificationOption)
  );
}

function isClarification(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const clarification = value as Record<string, unknown>;
  return (
    typeof clarification.is_complete === "boolean" &&
    (typeof clarification.question === "string" || clarification.question === null || clarification.question === undefined) &&
    Array.isArray(clarification.missing_fields) &&
    clarification.missing_fields.every((field) => typeof field === "string") &&
    Array.isArray(clarification.options) &&
    clarification.options.every(isClarificationOption) &&
    (clarification.steps === undefined ||
      (Array.isArray(clarification.steps) && clarification.steps.every(isClarificationStep))) &&
    typeof clarification.reason === "string"
  );
}

function normalizeClarification(value: NonNullable<ChatMessage["clarification"]>): NonNullable<ChatMessage["clarification"]> {
  const steps = value.steps?.filter(isClarificationStep).map((step) => ({
    field: step.field,
    question: step.question,
    reason: step.reason,
    options: step.options.map((option) => ({ ...option })),
  }));

  return {
    is_complete: value.is_complete,
    question: value.question,
    missing_fields: [...value.missing_fields],
    options: value.options.map((option) => ({ ...option })),
    ...(steps && steps.length > 0 ? { steps } : {}),
    reason: value.reason,
  };
}

function normalizeMessage(value: unknown): ChatMessage {
  const message = value as Record<string, unknown>;
  const normalized: ChatMessage = {
    id: message.id as string,
    role: message.role as ChatMessage["role"],
    content: message.content as string,
    createdAt: message.createdAt as string,
  };

  if (isClarification(message.clarification)) {
    const clarification = message.clarification as NonNullable<ChatMessage["clarification"]>;
    normalized.clarification = normalizeClarification(clarification);
    if (message.clarificationStatus === "pending" || message.clarificationStatus === "answered") {
      normalized.clarificationStatus = message.clarificationStatus;
    }
    if (typeof message.clarificationTraceEnd === "number") {
      normalized.clarificationTraceEnd = Math.max(0, Math.floor(message.clarificationTraceEnd));
    }
  }

  if (Array.isArray(message.clarificationHistory)) {
    const clarificationHistory = message.clarificationHistory
      .filter(isClarificationTurn)
      .map(normalizeClarificationTurn);
    if (clarificationHistory.length > 0) {
      normalized.clarificationHistory = clarificationHistory;
    }
  }

  if (Array.isArray(message.agentTrace)) {
    const agentTrace = message.agentTrace
      .filter(isAgentTraceEvent)
      .map(normalizeAgentTraceEvent);
    if (agentTrace.length > 0) {
      normalized.agentTrace = agentTrace;
    }
  }

  return normalized;
}

function isClarificationTurn(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const turn = value as Record<string, unknown>;
  return (
    typeof turn.id === "string" &&
    isClarification(turn.clarification) &&
    isClarificationOption(turn.selectedOption) &&
    (typeof turn.traceEnd === "number" || turn.traceEnd === undefined) &&
    typeof turn.createdAt === "string"
  );
}

function normalizeClarificationTurn(value: unknown): ClarificationTurn {
  const turn = value as Record<string, unknown>;
  return {
    id: turn.id as string,
    clarification: normalizeClarification(turn.clarification as NonNullable<ChatMessage["clarification"]>),
    selectedOption: { ...(turn.selectedOption as ClarificationTurn["selectedOption"]) },
    traceEnd: typeof turn.traceEnd === "number" ? Math.max(0, Math.floor(turn.traceEnd)) : 0,
    createdAt: turn.createdAt as string,
  };
}

function isAgentTraceEvent(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const trace = value as Record<string, unknown>;
  return (
    typeof trace.id === "string" &&
    (trace.type === "thought" || trace.type === "tool_call" || trace.type === "tool_result" || trace.type === "iteration") &&
    typeof trace.title === "string" &&
    (typeof trace.tool === "string" || trace.tool === undefined) &&
    typeof trace.createdAt === "string"
  );
}

function normalizeAgentTraceEvent(value: unknown): AgentTraceEvent {
  const trace = value as Record<string, unknown>;
  return {
    id: trace.id as string,
    type: trace.type as AgentTraceEvent["type"],
    title: trace.title as string,
    tool: typeof trace.tool === "string" ? trace.tool : undefined,
    payload: trace.payload,
    phase: isAgentTracePhase(trace.phase) ? trace.phase : undefined,
    status: isAgentTraceStatus(trace.status) ? trace.status : undefined,
    visibility: isAgentTraceVisibility(trace.visibility) ? trace.visibility : undefined,
    createdAt: trace.createdAt as string,
  };
}

function isAgentTracePhase(value: unknown): value is AgentTracePhase {
  return (
    value === "analysis" ||
    value === "planning" ||
    value === "retrieval" ||
    value === "sql" ||
    value === "calculation" ||
    value === "validation" ||
    value === "finalization" ||
    value === "clarification"
  );
}

function isAgentTraceStatus(value: unknown): value is AgentTraceStatus {
  return value === "running" || value === "done" || value === "retry" || value === "error";
}

function isAgentTraceVisibility(value: unknown): value is AgentTraceVisibility {
  return value === "summary" || value === "detail";
}

function isPendingClarification(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const pending = value as Record<string, unknown>;
  return (
    typeof pending.id === "string" &&
    typeof pending.message === "string" &&
    (pending.sendMode === "normal" || pending.sendMode === "clarify") &&
    typeof pending.assistantMessageId === "string" &&
    typeof pending.userMessageId === "string" &&
    isClarification(pending.clarification) &&
    (typeof pending.traceEnd === "number" || pending.traceEnd === undefined) &&
    typeof pending.createdAt === "string"
  );
}

function normalizePendingClarification(value: unknown): PendingClarification {
  const pending = value as Record<string, unknown>;
  const clarification = pending.clarification as PendingClarification["clarification"];
  return {
    id: pending.id as string,
    message: pending.message as string,
    sendMode: pending.sendMode as PendingClarification["sendMode"],
    assistantMessageId: pending.assistantMessageId as string,
    userMessageId: pending.userMessageId as string,
    clarification: normalizeClarification(clarification),
    stepIndex: typeof pending.stepIndex === "number" ? Math.max(0, Math.floor(pending.stepIndex)) : undefined,
    traceEnd: typeof pending.traceEnd === "number" ? Math.max(0, Math.floor(pending.traceEnd)) : 0,
    createdAt: pending.createdAt as string,
  };
}

function normalizeCheckpoint(value: unknown): ChatCheckpoint | undefined {
  if (!value || typeof value !== "object") return undefined;
  const checkpoint = value as Record<string, unknown>;
  if (
    typeof checkpoint.id !== "string" ||
    typeof checkpoint.title !== "string" ||
    typeof checkpoint.createdAt !== "string" ||
    !Array.isArray(checkpoint.messages) ||
    !checkpoint.messages.every(isMessage) ||
    !isPendingClarification(checkpoint.pendingClarification)
  ) {
    return undefined;
  }

  return {
    id: checkpoint.id,
    title: checkpoint.title,
    createdAt: checkpoint.createdAt,
    messages: checkpoint.messages.map(normalizeMessage).filter(shouldKeepMessage),
    pendingClarification: normalizePendingClarification(checkpoint.pendingClarification),
  };
}

function isSession(value: unknown): value is ChatSession {
  if (!value || typeof value !== "object") return false;
  const session = value as Record<string, unknown>;
  return (
    typeof session.id === "string" &&
    typeof session.title === "string" &&
    typeof session.createdAt === "string" &&
    typeof session.updatedAt === "string" &&
    Array.isArray(session.messages) &&
    session.messages.every(isMessage)
  );
}

function normalizeSession(session: ChatSession): ChatSession {
  const checkpoints = Array.isArray(session.checkpoints)
    ? session.checkpoints.map(normalizeCheckpoint).filter((checkpoint): checkpoint is ChatCheckpoint => Boolean(checkpoint))
    : [];

  return {
    id: session.id,
    conversationId: typeof session.conversationId === "string" ? session.conversationId : undefined,
    title: session.title,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messages: session.messages.map(normalizeMessage).filter(shouldKeepMessage),
    pendingClarification: isPendingClarification(session.pendingClarification)
      ? normalizePendingClarification(session.pendingClarification)
      : undefined,
    checkpoints: checkpoints.length > 0 ? checkpoints : undefined,
  };
}

function shouldKeepMessage(message: ChatMessage): boolean {
  return Boolean(
    message.content.trim() ||
    message.clarification ||
    message.clarificationHistory?.length ||
    message.agentTrace?.length,
  );
}

export function normalizeChatState(value: unknown): PersistedChatState {
  if (!value || typeof value !== "object") {
    return emptyState;
  }

  const parsed = value as Partial<PersistedChatState>;
  if (parsed.version !== 1 || !Array.isArray(parsed.sessions)) {
    return emptyState;
  }

  const sessions = parsed.sessions.filter(isSession).map(normalizeSession);
  const activeSessionId = sessions.some((session) => session.id === parsed.activeSessionId)
    ? parsed.activeSessionId
    : sessions[0]?.id;

  return { version: 1, activeSessionId, sessions };
}

export async function loadChatState(): Promise<PersistedChatState> {
  const response = await fetch("/invoke/sessions", {
    cache: "no-store",
    headers: chatClientHeaders(),
  });
  if (!response.ok) {
    throw new Error("Chat history request failed");
  }

  const serverState = normalizeChatState(await response.json());
  if (serverState.sessions.length > 0) {
    clearLegacyChatState();
    return serverState;
  }

  const legacyState = loadLegacyChatState();
  if (legacyState.sessions.length === 0) {
    return serverState;
  }

  try {
    await saveChatState(legacyState);
    clearLegacyChatState();
  } catch {
    return legacyState;
  }
  return legacyState;
}

export async function saveChatState(state: PersistedChatState, signal?: AbortSignal): Promise<void> {
  const normalized: PersistedChatState = {
    version: 1,
    activeSessionId: state.activeSessionId,
    sessions: state.sessions.map((session) => ({
      ...session,
      messages: session.messages.map((message) => ({ ...message })),
    })),
  };

  const response = await fetch("/invoke/sessions", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...chatClientHeaders() },
    body: JSON.stringify(normalized),
    signal,
  });

  if (!response.ok) {
    throw new Error("Chat history save failed");
  }
}

function loadLegacyChatState(): PersistedChatState {
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return emptyState;
    return normalizeChatState(JSON.parse(raw));
  } catch {
    return emptyState;
  }
}

function clearLegacyChatState(): void {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // Ignore storage access errors in restricted browser contexts.
  }
}
