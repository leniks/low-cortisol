import {
  Check,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  PenLine,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { createChatStreamUrl } from "./chatApi";
import { loadChatState, saveChatState } from "./storage";
import type { ChatMessage, ChatSession, PersistedChatState, SendMode } from "./types";

const firstPrompt = "Найди и подготовь набор данных по динамике ВВП России и Казахстана за 2020-2025 годы.";

function createId(): string {
  return crypto.randomUUID();
}

function nowIso(): string {
  return new Date().toISOString();
}

function titleFromMessage(message: string): string {
  const normalized = message.replace(/\s+/g, " ").trim();
  if (!normalized) return "Новый диалог";
  return normalized.length > 54 ? `${normalized.slice(0, 51)}...` : normalized;
}

function createSession(initialTitle = "Новый диалог"): ChatSession {
  const createdAt = nowIso();
  return {
    id: createId(),
    title: initialTitle,
    createdAt,
    updatedAt: createdAt,
    messages: [],
  };
}

function cx(...classes: Array<string | false | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

function getSessionPreview(session: ChatSession): string {
  const lastMessage = [...session.messages].reverse().find((message) => message.content.trim());
  return lastMessage?.content.trim() || "Пустой диалог";
}

function App() {
  const [chatState, setChatState] = useState<PersistedChatState>(() => loadChatState());
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<SendMode>("normal");
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const activeSession = useMemo(
    () => chatState.sessions.find((session) => session.id === chatState.activeSessionId),
    [chatState.activeSessionId, chatState.sessions],
  );

  const isStreaming = Boolean(streamingSessionId);
  const latestAssistantMessage = [...(activeSession?.messages ?? [])]
    .reverse()
    .find((message) => message.role === "assistant");
  const canShowFeedback =
    Boolean(latestAssistantMessage?.content.trim()) &&
    !isStreaming &&
    activeSession?.feedbackHiddenForMessageId !== latestAssistantMessage?.id;

  useEffect(() => {
    saveChatState(chatState);
  }, [chatState]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeSession?.messages, streamingSessionId]);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  function updateSessions(updater: (sessions: ChatSession[]) => ChatSession[]): void {
    setChatState((current) => {
      const sessions = updater(current.sessions);
      const activeSessionId = sessions.some((session) => session.id === current.activeSessionId)
        ? current.activeSessionId
        : sessions[0]?.id;
      return { version: 1, activeSessionId, sessions };
    });
  }

  function createNewChat(): void {
    const session = createSession();
    setChatState((current) => ({
      version: 1,
      activeSessionId: session.id,
      sessions: [session, ...current.sessions],
    }));
    setInput("");
    setMode("normal");
    setError(null);
    setSidebarOpen(false);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function ensureActiveSession(): ChatSession {
    if (activeSession) return activeSession;

    const session = createSession();
    setChatState((current) => ({
      version: 1,
      activeSessionId: session.id,
      sessions: [session, ...current.sessions],
    }));
    return session;
  }

  function selectSession(sessionId: string): void {
    if (isStreaming) return;
    setChatState((current) => ({ ...current, activeSessionId: sessionId }));
    setMode("normal");
    setError(null);
    setSidebarOpen(false);
  }

  function deleteSession(sessionId: string): void {
    if (streamingSessionId === sessionId) return;
    setChatState((current) => {
      const sessions = current.sessions.filter((session) => session.id !== sessionId);
      const activeSessionId = current.activeSessionId === sessionId ? sessions[0]?.id : current.activeSessionId;
      return { version: 1, activeSessionId, sessions };
    });
  }

  function closeCurrentStream(): void {
    sourceRef.current?.close();
    sourceRef.current = null;
    setStreamingSessionId(null);
  }

  function appendAssistantChunk(sessionId: string, assistantMessageId: string, chunk: string): void {
    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== sessionId) return session;
        return {
          ...session,
          updatedAt: nowIso(),
          messages: session.messages.map((message) =>
            message.id === assistantMessageId ? { ...message, content: message.content + chunk } : message,
          ),
        };
      }),
    );
  }

  function updateConversationId(sessionId: string, conversationId: string): void {
    updateSessions((sessions) =>
      sessions.map((session) => (session.id === sessionId ? { ...session, conversationId } : session)),
    );
  }

  function stageOutgoingMessages(session: ChatSession, message: string, sendMode: SendMode): ChatMessage {
    const timestamp = nowIso();
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: message,
      createdAt: timestamp,
    };
    const assistantMessage: ChatMessage = {
      id: createId(),
      role: "assistant",
      content: "",
      createdAt: timestamp,
    };

    updateSessions((sessions) =>
      sessions.map((item) => {
        if (item.id !== session.id) return item;

        const hasLastPair =
          item.messages.length >= 2 &&
          item.messages[item.messages.length - 2]?.role === "user" &&
          item.messages[item.messages.length - 1]?.role === "assistant";

        const messages =
          sendMode === "clarify" && hasLastPair
            ? [...item.messages.slice(0, -2), userMessage, assistantMessage]
            : [...item.messages, userMessage, assistantMessage];

        return {
          ...item,
          title: item.messages.length === 0 ? titleFromMessage(message) : item.title,
          updatedAt: timestamp,
          feedbackHiddenForMessageId: undefined,
          messages,
        };
      }),
    );

    return assistantMessage;
  }

  function startStream(session: ChatSession, message: string, assistantMessageId: string, sendMode: SendMode): void {
    closeCurrentStream();
    setError(null);
    setStreamingSessionId(session.id);

    const source = new EventSource(createChatStreamUrl(message, session.conversationId, sendMode));
    sourceRef.current = source;

    source.addEventListener("meta", (event) => {
      try {
        const data = JSON.parse(event.data || "{}") as { conversation_id?: string };
        if (data.conversation_id) {
          updateConversationId(session.id, data.conversation_id);
        }
      } catch {
        // Ignore malformed stream metadata.
      }
    });

    source.addEventListener("delta", (event) => {
      try {
        const data = JSON.parse(event.data || "{}") as { text?: string };
        if (data.text) {
          appendAssistantChunk(session.id, assistantMessageId, data.text);
        }
      } catch {
        // Ignore malformed stream chunks.
      }
    });

    source.addEventListener("done", () => {
      closeCurrentStream();
    });

    source.onerror = () => {
      closeCurrentStream();
      setError("Соединение оборвалось. Попробуйте отправить сообщение еще раз.");
      appendAssistantChunk(session.id, assistantMessageId, "\n\n[Соединение оборвалось]");
    };
  }

  function sendMessage(event?: FormEvent): void {
    event?.preventDefault();
    const message = input.trim();
    if (!message || isStreaming) return;

    const session = ensureActiveSession();
    const sendMode = mode;
    const assistantMessage = stageOutgoingMessages(session, message, sendMode);
    setInput("");
    setMode("normal");
    startStream(session, message, assistantMessage.id, sendMode);
  }

  function sendStarterPrompt(): void {
    if (isStreaming) return;
    setInput(firstPrompt);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  function acceptAnswer(): void {
    if (!activeSession || !latestAssistantMessage) return;
    updateSessions((sessions) =>
      sessions.map((session) =>
        session.id === activeSession.id
          ? { ...session, feedbackHiddenForMessageId: latestAssistantMessage.id }
          : session,
      ),
    );
  }

  function beginClarification(): void {
    if (!activeSession || isStreaming) return;
    setMode("clarify");
    setInput("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  const sessionList = chatState.sessions;

  return (
    <div className="min-h-screen bg-[#f6f7f9] text-neutral-950">
      <div className="flex min-h-screen">
        <aside
          className={cx(
            "fixed inset-y-0 left-0 z-30 w-[300px] border-r border-neutral-200 bg-[#eceff3] transition-transform duration-200 lg:sticky lg:top-0 lg:z-auto lg:h-screen",
            sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
            desktopSidebarCollapsed && "lg:w-[74px]",
          )}
        >
          <div className="flex h-full flex-col">
            <div className="flex h-16 items-center gap-2 border-b border-neutral-200 px-3">
              <button
                type="button"
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-neutral-700 transition hover:bg-white/75"
                onClick={() => setDesktopSidebarCollapsed((value) => !value)}
                aria-label={desktopSidebarCollapsed ? "Развернуть боковую панель" : "Свернуть боковую панель"}
                title={desktopSidebarCollapsed ? "Развернуть" : "Свернуть"}
              >
                {desktopSidebarCollapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
              </button>
              {!desktopSidebarCollapsed && (
                <>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">MathMod DataAgent</div>
                    <div className="truncate text-xs text-neutral-500">Локальная история</div>
                  </div>
                  <button
                    type="button"
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-neutral-700 transition hover:bg-white/75 lg:hidden"
                    onClick={() => setSidebarOpen(false)}
                    aria-label="Закрыть боковую панель"
                    title="Закрыть"
                  >
                    <X size={19} />
                  </button>
                </>
              )}
            </div>

            <div className="p-3">
              <button
                type="button"
                className={cx(
                  "flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-neutral-950 px-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-55",
                  desktopSidebarCollapsed && "lg:px-0",
                )}
                onClick={createNewChat}
                disabled={isStreaming}
                title="Новый чат"
              >
                <MessageSquarePlus size={18} />
                {!desktopSidebarCollapsed && <span>Новый чат</span>}
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-4">
              {sessionList.length === 0 && !desktopSidebarCollapsed && (
                <div className="mx-2 mt-2 rounded-lg border border-dashed border-neutral-300 px-3 py-4 text-sm text-neutral-500">
                  История появится после первого сообщения.
                </div>
              )}

              <div className="space-y-1">
                {sessionList.map((session) => {
                  const selected = session.id === activeSession?.id;
                  return (
                    <div key={session.id} className="group relative">
                      <button
                        type="button"
                        className={cx(
                          "flex min-h-14 w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition",
                          selected ? "bg-white shadow-sm" : "hover:bg-white/65",
                          desktopSidebarCollapsed && "lg:justify-center lg:px-0",
                        )}
                        onClick={() => selectSession(session.id)}
                        disabled={isStreaming}
                        title={session.title}
                      >
                        <span
                          className={cx(
                            "grid h-8 w-8 shrink-0 place-items-center rounded-lg",
                            selected ? "bg-emerald-100 text-emerald-800" : "bg-neutral-200 text-neutral-600",
                          )}
                        >
                          <PenLine size={16} />
                        </span>
                        {!desktopSidebarCollapsed && (
                          <span className="min-w-0 flex-1 pr-8">
                            <span className="block truncate text-sm font-medium">{session.title}</span>
                            <span className="block truncate text-xs text-neutral-500">{getSessionPreview(session)}</span>
                          </span>
                        )}
                      </button>
                      {!desktopSidebarCollapsed && (
                        <button
                          type="button"
                          className="absolute right-2 top-1/2 hidden h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-neutral-400 transition hover:bg-rose-50 hover:text-rose-700 group-hover:grid"
                          onClick={() => deleteSession(session.id)}
                          disabled={isStreaming}
                          aria-label="Удалить диалог"
                          title="Удалить"
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </aside>

        {sidebarOpen && (
          <button
            type="button"
            className="fixed inset-0 z-20 bg-neutral-950/35 lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Закрыть боковую панель"
          />
        )}

        <main className="flex min-h-screen min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex h-16 items-center gap-3 border-b border-neutral-200 bg-[#f6f7f9]/95 px-4 backdrop-blur">
            <button
              type="button"
              className="grid h-10 w-10 place-items-center rounded-lg text-neutral-700 transition hover:bg-white lg:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="Открыть боковую панель"
              title="Меню"
            >
              <Menu size={21} />
            </button>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-base font-semibold">{activeSession?.title || "MathMod DataAgent"}</h1>
              <p className="truncate text-xs text-neutral-500">
                {isStreaming ? "Ответ формируется" : "История хранится в браузере"}
              </p>
            </div>
          </header>

          <section className="flex-1 overflow-y-auto px-4 py-6">
            <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
              {!activeSession?.messages.length && (
                <div className="flex min-h-[54vh] flex-col items-center justify-center text-center">
                  <div className="mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-emerald-100 text-emerald-800">
                    <MessageSquarePlus size={26} />
                  </div>
                  <h2 className="text-2xl font-semibold tracking-normal">MathMod DataAgent</h2>
                  <button
                    type="button"
                    className="mt-6 max-w-xl rounded-lg border border-neutral-200 bg-white px-4 py-3 text-left text-sm text-neutral-700 shadow-sm transition hover:border-emerald-300 hover:text-neutral-950"
                    onClick={sendStarterPrompt}
                    disabled={isStreaming}
                  >
                    {firstPrompt}
                  </button>
                </div>
              )}

              {activeSession?.messages.map((message) => (
                <article
                  key={message.id}
                  className={cx("flex w-full", message.role === "user" ? "justify-end" : "justify-start")}
                >
                  <div
                    className={cx(
                      "max-w-[min(760px,100%)] whitespace-pre-wrap break-words rounded-lg px-4 py-3 text-[15px] leading-7 shadow-sm",
                      message.role === "user"
                        ? "bg-neutral-950 text-white"
                        : "border border-neutral-200 bg-white text-neutral-900",
                      message.content.length === 0 && "min-h-14 min-w-28",
                    )}
                  >
                    {message.content || (
                      <span className="inline-flex items-center gap-1 text-neutral-400">
                        <span className="h-2 w-2 animate-pulse rounded-full bg-current" />
                        <span className="h-2 w-2 animate-pulse rounded-full bg-current [animation-delay:120ms]" />
                        <span className="h-2 w-2 animate-pulse rounded-full bg-current [animation-delay:240ms]" />
                      </span>
                    )}
                  </div>
                </article>
              ))}

              {canShowFeedback && (
                <div className="flex flex-wrap items-center gap-2 pl-1">
                  <button
                    type="button"
                    className="inline-flex h-9 items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm font-medium text-neutral-700 transition hover:border-emerald-300 hover:text-emerald-800"
                    onClick={acceptAnswer}
                  >
                    <Check size={16} />
                    Принять
                  </button>
                  <button
                    type="button"
                    className="inline-flex h-9 items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm font-medium text-neutral-700 transition hover:border-amber-300 hover:text-amber-800"
                    onClick={beginClarification}
                  >
                    <PenLine size={16} />
                    Уточнить
                  </button>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </section>

          <footer className="sticky bottom-0 border-t border-neutral-200 bg-[#f6f7f9]/95 px-4 py-4 backdrop-blur">
            <div className="mx-auto w-full max-w-4xl">
              {error && (
                <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {error}
                </div>
              )}
              {mode === "clarify" && (
                <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  <span>Уточнение заменит последний запрос и ответ.</span>
                  <button
                    type="button"
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-lg transition hover:bg-amber-100"
                    onClick={() => setMode("normal")}
                    aria-label="Отменить уточнение"
                    title="Отменить"
                  >
                    <X size={16} />
                  </button>
                </div>
              )}
              <form
                className="flex items-end gap-3 rounded-xl border border-neutral-300 bg-white p-2 shadow-sm focus-within:border-emerald-400"
                onSubmit={sendMessage}
              >
                <textarea
                  ref={textareaRef}
                  className="max-h-48 min-h-12 flex-1 resize-none bg-transparent px-3 py-3 text-[15px] leading-6 outline-none placeholder:text-neutral-400"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder={mode === "clarify" ? "Уточните ответ" : "Спросите про данные, расчеты или источники"}
                  rows={1}
                  disabled={isStreaming}
                />
                <button
                  type="submit"
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-emerald-700 text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
                  disabled={!input.trim() || isStreaming}
                  aria-label="Отправить"
                  title="Отправить"
                >
                  <Send size={19} />
                </button>
              </form>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default App;
