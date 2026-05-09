import type { ChatSession, PersistedChatState } from "./types";

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

export function loadChatState(): PersistedChatState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState;

    const parsed = JSON.parse(raw) as Partial<PersistedChatState>;
    if (parsed.version !== 1 || !Array.isArray(parsed.sessions)) {
      return emptyState;
    }

    const sessions = parsed.sessions.filter(isSession);
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
