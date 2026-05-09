import type { SendMode } from "./types";

export function createChatStreamUrl(message: string, conversationId: string | undefined, mode: SendMode): string {
  const baseUrl = mode === "clarify" ? "/invoke/clarify/stream" : "/invoke/stream";
  const params = new URLSearchParams({ message });

  if (conversationId) {
    params.set("conversation_id", conversationId);
  }

  return `${baseUrl}?${params.toString()}`;
}
