import type { ChatMessage, SendMode } from "./types";

const CHAT_CLIENT_ID_KEY = "mathmod.chat.clientId.v1";
let fallbackChatClientId: string | undefined;

export function getChatClientId(): string {
  try {
    const existing = localStorage.getItem(CHAT_CLIENT_ID_KEY);
    if (existing) return existing;

    const clientId = crypto.randomUUID();
    localStorage.setItem(CHAT_CLIENT_ID_KEY, clientId);
    return clientId;
  } catch {
    fallbackChatClientId ??= crypto.randomUUID();
    return fallbackChatClientId;
  }
}

export function chatClientHeaders(): HeadersInit {
  return { "X-Client-Id": getChatClientId() };
}

export function createChatStreamUrl(
  message: string,
  conversationId: string | undefined,
  mode: SendMode,
): string {
  const baseUrl = mode === "clarify" ? "/invoke/clarify/stream" : "/invoke/stream";
  const params = new URLSearchParams({ message });

  if (conversationId) {
    params.set("conversation_id", conversationId);
  }

  return `${baseUrl}?${params.toString()}`;
}

export async function syncDialog(conversationId: string | undefined, messages: ChatMessage[]): Promise<void> {
  if (!conversationId) return;

  await fetch("/invoke/dialog", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...chatClientHeaders() },
    body: JSON.stringify({
      conversation_id: conversationId,
      dialog: messages
        .filter((message) => message.content.trim())
        .map((message) => ({ role: message.role, content: message.content })),
    }),
  });
}
