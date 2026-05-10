import type { ChatCheckpoint, ChatMessage, ChatSession, PendingClarification, PersistedChatState } from "./types";

export const STORAGE_KEY = "mathmod.chat.sessions.v1";

const emptyState: PersistedChatState = {
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
    typeof clarification.reason === "string"
  );
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
    normalized.clarification = {
      is_complete: clarification.is_complete,
      question: clarification.question,
      missing_fields: [...clarification.missing_fields],
      options: clarification.options.map((option) => ({ ...option })),
      reason: clarification.reason,
    };
    if (message.clarificationStatus === "pending" || message.clarificationStatus === "answered") {
      normalized.clarificationStatus = message.clarificationStatus;
    }
  }

  return normalized;
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
    clarification: {
      is_complete: clarification.is_complete,
      question: clarification.question,
      missing_fields: [...clarification.missing_fields],
      options: clarification.options.map((option) => ({ ...option })),
      reason: clarification.reason,
    },
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
    messages: checkpoint.messages.map(normalizeMessage).filter((message) => message.content.trim() || message.clarification),
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
    messages: session.messages.map(normalizeMessage).filter((message) => message.content.trim() || message.clarification),
    pendingClarification: isPendingClarification(session.pendingClarification)
      ? normalizePendingClarification(session.pendingClarification)
      : undefined,
    checkpoints: checkpoints.length > 0 ? checkpoints : undefined,
  };
}

export function loadChatState(): PersistedChatState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState;

    const parsed = JSON.parse(raw) as Partial<PersistedChatState>;
    if (parsed.version !== 1 || !Array.isArray(parsed.sessions)) {
      return emptyState;
    }

    const sessions = parsed.sessions.filter(isSession).map(normalizeSession);
    const activeSessionId = sessions.some((session) => session.id === parsed.activeSessionId)
      ? parsed.activeSessionId
      : sessions[0]?.id;

    return { version: 1, activeSessionId, sessions };
  } catch {
    return emptyState;
  }
}

export function saveChatState(state: PersistedChatState): void {
  const normalized: PersistedChatState = {
    version: 1,
    activeSessionId: state.activeSessionId,
    sessions: state.sessions.map((session) => ({
      ...session,
      messages: session.messages.map((message) => ({ ...message })),
    })),
  };

  localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
}
