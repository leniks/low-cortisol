import type { ChatMessage, ClarificationResult, SendMode } from "./types";

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

export async function clarifyMissing(
  message: string,
  conversationId: string | undefined,
): Promise<ClarificationResult> {
  const response = await fetch("/invoke/clarify-missing", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });

  if (!response.ok) {
    throw new Error(`Clarifier request failed: ${response.status}`);
  }

  return (await response.json()) as ClarificationResult;
}

export async function syncDialog(conversationId: string | undefined, messages: ChatMessage[]): Promise<void> {
  if (!conversationId) return;

  await fetch("/invoke/dialog", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId,
      dialog: messages
        .filter((message) => message.content.trim())
        .map((message) => ({ role: message.role, content: message.content })),
    }),
  });
}
