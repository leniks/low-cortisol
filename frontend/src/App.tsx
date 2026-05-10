import {
  Copy,
  Menu,
  MessageSquarePlus,
  PenLine,
  Search,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { clarifyMissing, createChatStreamUrl, syncDialog } from "./chatApi";
import { loadChatState, saveChatState } from "./storage";
import type {
  AgentTraceEvent,
  ChatCheckpoint,
  ChatMessage,
  ChatSession,
  ClarificationOption,
  PendingClarification,
  PersistedChatState,
  SendMode,
} from "./types";

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

function cloneMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((message) => ({
    ...message,
    clarification: message.clarification ? cloneClarification(message.clarification) : undefined,
    agentTrace: message.agentTrace?.map(cloneTraceEvent),
  }));
}

function cloneTraceEvent(event: AgentTraceEvent): AgentTraceEvent {
  return {
    ...event,
    payload: cloneJsonValue(event.payload),
  };
}

function cloneJsonValue(value: unknown): unknown {
  if (value === undefined) return undefined;
  try {
    return JSON.parse(JSON.stringify(value)) as unknown;
  } catch {
    return String(value);
  }
}

function cloneClarification(clarification: NonNullable<ChatMessage["clarification"]>): NonNullable<ChatMessage["clarification"]> {
  return {
    ...clarification,
    missing_fields: [...clarification.missing_fields],
    options: clarification.options.map((option) => ({ ...option })),
  };
}

function formatCheckpointTitle(checkpoint: ChatCheckpoint, index: number): string {
  return `${index + 1}. ${checkpoint.title}`;
}

function appendClarificationValue(message: string, value: string): string {
  const normalized = message.trim();
  if (!normalized) return value;
  return `${normalized}, ${value}`;
}

function createTraceEvent(data: Partial<AgentTraceEvent>): AgentTraceEvent {
  const type = data.type && ["thought", "tool_call", "tool_result", "iteration"].includes(data.type)
    ? data.type
    : "thought";
  return {
    id: createId(),
    type,
    title: data.title || getTraceTypeLabel(type),
    tool: data.tool,
    payload: data.payload,
    createdAt: nowIso(),
  };
}

function getTraceTypeLabel(type: AgentTraceEvent["type"]): string {
  switch (type) {
    case "tool_call":
      return "Вызов";
    case "tool_result":
      return "Результат";
    case "iteration":
      return "Итерация";
    default:
      return "Мысль";
  }
}

function formatTracePayload(payload: unknown): string {
  if (payload === undefined || payload === null) return "";
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

type AnswerBlock =
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] }
  | { type: "code"; language: string; code: string }
  | { type: "table"; headers: string[]; rows: string[][] };

type AnswerSection = {
  title?: string;
  blocks: AnswerBlock[];
};

function parseAssistantAnswer(content: string): AnswerSection[] {
  const lines = normalizeInlineMarkdownTables(content).split(/\r?\n/);
  const sections: AnswerSection[] = [{ blocks: [] }];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let tableRows: string[][] = [];
  let codeLines: string[] = [];
  let codeLanguage = "";
  let inCode = false;

  function currentSection(): AnswerSection {
    return sections[sections.length - 1];
  }

  function flushParagraph(): void {
    if (paragraph.length === 0) return;
    currentSection().blocks.push({ type: "paragraph", text: paragraph.join(" ").trim() });
    paragraph = [];
  }

  function flushList(): void {
    if (listItems.length === 0) return;
    currentSection().blocks.push({ type: "list", items: listItems });
    listItems = [];
  }

  function flushTable(): void {
    if (tableRows.length === 0) return;
    const separatorIndex = tableRows.findIndex(isTableSeparatorRow);
    const headerIndex = separatorIndex > 0 ? separatorIndex - 1 : 0;
    const headers = tableRows[headerIndex] ?? [];
    const rows =
      separatorIndex >= 0
        ? tableRows.filter((_, index) => index !== separatorIndex && index !== headerIndex)
        : tableRows.slice(1);
    const normalized = normalizeTable(headers, rows);

    if (normalized.headers.length > 0) {
      currentSection().blocks.push({ type: "table", ...normalized });
    } else {
      currentSection().blocks.push({ type: "paragraph", text: tableRows.map(joinTableRow).join(" ") });
    }

    tableRows = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const fence = line.match(/^```(\w+)?\s*$/);

    if (fence) {
      if (inCode) {
        currentSection().blocks.push({ type: "code", language: codeLanguage, code: codeLines.join("\n") });
        codeLines = [];
        codeLanguage = "";
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        flushTable();
        inCode = true;
        codeLanguage = fence[1] || "";
      }
      continue;
    }

    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      flushTable();
      continue;
    }

    if (isMarkdownTableLine(line)) {
      flushParagraph();
      flushList();
      tableRows.push(splitTableRow(line));
      continue;
    }

    flushTable();

    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      sections.push({ title: heading[1].trim(), blocks: [] });
      continue;
    }

    const listItem = line.match(/^[-*]\s+(.+)$/);
    if (listItem) {
      flushParagraph();
      listItems.push(listItem[1].trim());
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  if (inCode) {
    currentSection().blocks.push({ type: "code", language: codeLanguage, code: codeLines.join("\n") });
  }
  flushParagraph();
  flushList();
  flushTable();

  return sections.filter((section) => section.title || section.blocks.length > 0);
}

function normalizeInlineMarkdownTables(content: string): string {
  let inCode = false;
  return content
    .split(/\r?\n/)
    .map((line) => {
      if (/^```/.test(line.trim())) {
        inCode = !inCode;
        return line;
      }

      if (inCode || !looksLikeInlineTable(line)) return line;
      return line.replace(/\|\s+\|/g, "|\n|");
    })
    .join("\n");
}

function looksLikeInlineTable(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) return false;
  return /\|\s*:?-{3,}:?\s*\|/.test(trimmed) && /\|\s+\|/.test(trimmed);
}

function isMarkdownTableLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && splitTableRow(trimmed).length >= 2;
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim();
  const withoutStart = trimmed.startsWith("|") ? trimmed.slice(1) : trimmed;
  const withoutEnd = withoutStart.endsWith("|") ? withoutStart.slice(0, -1) : withoutStart;
  return withoutEnd.split("|").map((cell) => cell.trim());
}

function isTableSeparatorRow(row: string[]): boolean {
  return row.length > 0 && row.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

function normalizeTable(headers: string[], rows: string[][]): { headers: string[]; rows: string[][] } {
  const columnCount = Math.max(headers.length, ...rows.map((row) => row.length));
  if (columnCount === 0) return { headers: [], rows: [] };

  return {
    headers: normalizeTableRow(headers, columnCount),
    rows: rows.map((row) => normalizeTableRow(row, columnCount)),
  };
}

function normalizeTableRow(row: string[], columnCount: number): string[] {
  return Array.from({ length: columnCount }, (_, index) => row[index] ?? "");
}

function joinTableRow(row: string[]): string {
  return `| ${row.join(" | ")} |`;
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={`${part}-${index}`}
          className="rounded-md border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-[0.92em] font-medium text-neutral-900"
        >
          {part.slice(1, -1)}
        </code>
      );
    }

    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={`${part}-${index}`} className="font-semibold text-neutral-950">
          {part.slice(2, -2)}
        </strong>
      );
    }

    return part;
  });
}

function AssistantAnswer({ content }: { content: string }): ReactNode {
  const sections = parseAssistantAnswer(content);
  if (sections.length === 0) return null;

  return (
    <div className="space-y-4 whitespace-normal text-[15px] leading-7 text-neutral-900">
      {sections.map((section, index) => (
        <AnswerSectionView key={`${section.title || "intro"}-${index}`} section={section} />
      ))}
    </div>
  );
}

function AnswerSectionView({ section }: { section: AnswerSection }): ReactNode {
  const titleParts = section.title?.match(/^(\d+)\.\s*(.+)$/);
  const titleNumber = titleParts?.[1];
  const title = titleParts?.[2] || section.title;
  const datasetMode = Boolean(title?.toLowerCase().includes("набор"));

  return (
    <section className={cx(section.title && "rounded-xl border border-neutral-200 bg-white px-4 py-3 shadow-sm")}>
      {title && (
        <div className="mb-3 flex items-center gap-3">
          {titleNumber && (
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-neutral-950 text-xs font-semibold text-white">
              {titleNumber}
            </span>
          )}
          <h3 className="text-sm font-semibold uppercase tracking-wide text-neutral-950">{title}</h3>
        </div>
      )}
      <div className="space-y-3">
        {section.blocks.map((block, index) => (
          <AnswerBlockView
            key={`${block.type}-${index}`}
            block={block}
            datasetMode={datasetMode}
          />
        ))}
      </div>
    </section>
  );
}

function AnswerBlockView({ block, datasetMode }: { block: AnswerBlock; datasetMode: boolean }): ReactNode {
  if (block.type === "code") {
    const language = block.language || "text";
    return (
      <div className="overflow-hidden rounded-lg border border-neutral-800 bg-neutral-950">
        <div className="flex h-9 items-center justify-between border-b border-white/10 px-3 text-xs text-neutral-300">
          <span className="font-medium uppercase tracking-wide">{language}</span>
          {language.toLowerCase() === "sql" && <span>Analyst SQL</span>}
        </div>
        <pre className="max-h-[420px] overflow-auto p-4 text-[13px] leading-6 text-neutral-100">
          <code>{block.code}</code>
        </pre>
      </div>
    );
  }

  if (block.type === "list") {
    if (datasetMode) {
      return (
        <div className="flex flex-wrap gap-2">
          {block.items.map((item, index) => (
            <span
              key={`${item}-${index}`}
              className="inline-flex min-h-8 items-center rounded-full border border-neutral-200 bg-neutral-50 px-3 text-sm font-medium text-neutral-900"
            >
              {renderInline(item)}
            </span>
          ))}
        </div>
      );
    }

    return (
      <ul className="space-y-2">
        {block.items.map((item, index) => (
          <li key={`${item}-${index}`} className="flex gap-2 text-neutral-800">
            <span className="mt-3 h-1.5 w-1.5 shrink-0 rounded-full bg-neutral-400" />
            <span>{renderInline(item)}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (block.type === "table") {
    return (
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <thead className="bg-neutral-50">
              <tr>
                {block.headers.map((header, index) => (
                  <th
                    key={`${header}-${index}`}
                    className="border-b border-neutral-200 px-4 py-2.5 text-left font-semibold text-neutral-950"
                  >
                    {renderInline(header)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`} className="border-b border-neutral-100 last:border-b-0">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${cell}-${rowIndex}-${cellIndex}`}
                      className={cx(
                        "px-4 py-2.5 text-neutral-800",
                        cellIndex === 0 && "font-medium text-neutral-950",
                      )}
                    >
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return <p className="text-neutral-800">{renderInline(block.text)}</p>;
}

function App() {
  const [chatState, setChatState] = useState<PersistedChatState>(() => loadChatState());
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<SendMode>("normal");
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(null);
  const [clarifyingSessionId, setClarifyingSessionId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionFilter, setSessionFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const activeSession = useMemo(
    () => chatState.sessions.find((session) => session.id === chatState.activeSessionId),
    [chatState.activeSessionId, chatState.sessions],
  );

  const isClarifying = Boolean(clarifyingSessionId);
  const isStreaming = Boolean(streamingSessionId || clarifyingSessionId);
  const latestAssistantMessage = [...(activeSession?.messages ?? [])]
    .reverse()
    .find((message) => message.role === "assistant");
  const canShowCopy = Boolean(latestAssistantMessage?.content.trim()) && !isStreaming;

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

  function appendAssistantTrace(sessionId: string, assistantMessageId: string, trace: AgentTraceEvent): void {
    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== sessionId) return session;
        return {
          ...session,
          updatedAt: nowIso(),
          messages: session.messages.map((message) =>
            message.id === assistantMessageId
              ? { ...message, agentTrace: [...(message.agentTrace ?? []), trace] }
              : message,
          ),
        };
      }),
    );
  }

  function updateAssistantClarification(
    sessionId: string,
    assistantMessageId: string,
    clarification: ChatMessage["clarification"],
    status: ChatMessage["clarificationStatus"],
  ): void {
    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== sessionId) return session;
        return {
          ...session,
          updatedAt: nowIso(),
          messages: session.messages.map((message) =>
            message.id === assistantMessageId ? { ...message, clarification, clarificationStatus: status } : message,
          ),
        };
      }),
    );
  }

  function setPendingClarification(sessionId: string, pendingClarification: PendingClarification | undefined): void {
    updateSessions((sessions) =>
      sessions.map((session) =>
        session.id === sessionId ? { ...session, pendingClarification, updatedAt: nowIso() } : session,
      ),
    );
  }

  function updateConversationId(sessionId: string, conversationId: string): void {
    updateSessions((sessions) =>
      sessions.map((session) => (session.id === sessionId ? { ...session, conversationId } : session)),
    );
  }

  function stageOutgoingMessages(
    session: ChatSession,
    message: string,
    sendMode: SendMode,
  ): { userMessage: ChatMessage; assistantMessage: ChatMessage } {
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
          messages,
        };
      }),
    );

    return { userMessage, assistantMessage };
  }

  function startStream(
    session: ChatSession,
    message: string,
    assistantMessageId: string,
    sendMode: SendMode,
  ): void {
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

    source.addEventListener("trace", (event) => {
      try {
        const data = JSON.parse(event.data || "{}") as Partial<AgentTraceEvent>;
        appendAssistantTrace(session.id, assistantMessageId, createTraceEvent(data));
      } catch {
        // Ignore malformed trace events.
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

  async function clarifyBeforeNextStep(
    session: ChatSession,
    message: string,
    sendMode: SendMode,
    userMessageId: string,
    assistantMessageId: string,
  ): Promise<void> {
    setClarifyingSessionId(session.id);
    setError(null);

    try {
      const clarification = await clarifyMissing(message, session.conversationId);
      if (clarification.is_complete) {
        startStream(session, message, assistantMessageId, sendMode);
        return;
      }

      updateAssistantClarification(session.id, assistantMessageId, clarification, "pending");
      setPendingClarification(session.id, {
        id: createId(),
        message,
        sendMode,
        assistantMessageId,
        userMessageId,
        clarification,
        createdAt: nowIso(),
      });
    } catch {
      setError("Не удалось уточнить запрос. Попробуйте отправить сообщение еще раз.");
      appendAssistantChunk(session.id, assistantMessageId, "\n\n[Ошибка уточнения]");
    } finally {
      setClarifyingSessionId(null);
    }
  }

  function chooseClarificationOption(option: ClarificationOption): void {
    if (!activeSession?.pendingClarification || isStreaming) return;

    const pending = activeSession.pendingClarification;
    if (option.value === "manual") {
      updateSessions((sessions) =>
        sessions.map((session) => {
          if (session.id !== activeSession.id) return session;
          return {
            ...session,
            updatedAt: nowIso(),
            pendingClarification: undefined,
            messages: session.messages.filter(
              (message) => message.id !== pending.userMessageId && message.id !== pending.assistantMessageId,
            ),
          };
        }),
      );
      setInput(pending.message);
      setMode(pending.sendMode);
      requestAnimationFrame(() => textareaRef.current?.focus());
      return;
    }

    const clarifiedMessage = appendClarificationValue(pending.message, option.value);
    const timestamp = nowIso();

    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== activeSession.id) return session;

        const messages = session.messages.map((message) =>
          message.id === pending.userMessageId
            ? { ...message, content: clarifiedMessage }
            : message.id === pending.assistantMessageId
              ? { ...message, clarification: pending.clarification, clarificationStatus: "answered" as const }
              : message,
        );
        const checkpoint: ChatCheckpoint = {
          id: pending.id,
          title: `${pending.clarification.question || "Уточнение"}: ${option.label}`,
          createdAt: timestamp,
          pendingClarification: { ...pending, clarification: cloneClarification(pending.clarification) },
          messages: cloneMessages(activeSession.messages),
        };

        return {
          ...session,
          updatedAt: timestamp,
          pendingClarification: undefined,
          checkpoints: [...(session.checkpoints ?? []), checkpoint],
          messages,
        };
      }),
    );

    startStream(activeSession, clarifiedMessage, pending.assistantMessageId, pending.sendMode);
  }

  function rollbackToCheckpoint(checkpointId: string): void {
    if (!activeSession || isStreaming) return;
    const checkpoints = activeSession.checkpoints ?? [];
    const checkpointIndex = checkpoints.findIndex((item) => item.id === checkpointId);
    const checkpoint = checkpointIndex >= 0 ? checkpoints[checkpointIndex] : undefined;
    const pendingClarification = checkpoint?.pendingClarification;
    if (!checkpoint || !pendingClarification) return;
    const restoredMessages = cloneMessages(checkpoint.messages);
    const restoredCheckpoints = checkpoints.slice(0, checkpointIndex);

    updateSessions((sessions) =>
      sessions.map((session) =>
        session.id === activeSession.id
          ? {
              ...session,
              updatedAt: nowIso(),
              checkpoints: restoredCheckpoints,
              pendingClarification: {
                ...pendingClarification,
                clarification: cloneClarification(pendingClarification.clarification),
              },
              messages: restoredMessages,
            }
          : session,
      ),
    );
    void syncDialog(activeSession.conversationId, restoredMessages).catch(() => {
      setError("Откат применён локально, но backend-историю синхронизировать не удалось.");
    });
  }

  async function sendMessage(event?: FormEvent): Promise<void> {
    event?.preventDefault();
    const message = input.trim();
    if (!message || isStreaming) return;

    const session = ensureActiveSession();
    const sendMode = mode;
    const { userMessage, assistantMessage } = stageOutgoingMessages(session, message, sendMode);
    setInput("");
    setMode("normal");

    if (sendMode === "normal") {
      await clarifyBeforeNextStep(session, message, sendMode, userMessage.id, assistantMessage.id);
      return;
    }

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

  function copyLatestAnswer(): void {
    if (!latestAssistantMessage?.content.trim()) return;
    void navigator.clipboard?.writeText(latestAssistantMessage.content);
  }

  const normalizedSessionFilter = sessionFilter.trim().toLowerCase();
  const sessionList = normalizedSessionFilter
    ? chatState.sessions.filter((session) => {
        const haystack = `${session.title} ${getSessionPreview(session)}`.toLowerCase();
        return haystack.includes(normalizedSessionFilter);
      })
    : chatState.sessions;

  return (
    <div className="min-h-screen bg-white text-neutral-950">
      <div className="flex min-h-screen">
        <aside
          className={cx(
            "fixed inset-y-0 left-0 z-30 w-[260px] border-r border-neutral-200 bg-[#f7f7f8] transition-transform duration-200 lg:sticky lg:top-0 lg:z-auto lg:h-screen",
            sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
          )}
        >
          <div className="flex h-full flex-col">
            <div className="space-y-1 px-3 py-4">
              <button
                type="button"
                className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-neutral-900 transition hover:bg-neutral-200/70 disabled:cursor-not-allowed disabled:opacity-55"
                onClick={createNewChat}
                disabled={isStreaming}
              >
                <PenLine size={18} />
                <span>Новый чат</span>
              </button>
              <label className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm text-neutral-700 transition focus-within:bg-neutral-200/70 hover:bg-neutral-200/70">
                <Search size={18} />
                <input
                  className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-neutral-500"
                  value={sessionFilter}
                  onChange={(event) => setSessionFilter(event.target.value)}
                  placeholder="Поиск в чатах"
                />
              </label>
              <button
                type="button"
                className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-neutral-900 transition hover:bg-neutral-200/70 lg:hidden"
                onClick={() => setSidebarOpen(false)}
              >
                <X size={18} />
                <span>Закрыть меню</span>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-4">
              <div className="px-2 pb-2 pt-3 text-sm font-semibold text-neutral-800">Недавнее</div>

              {sessionList.length === 0 && (
                <div className="mx-2 rounded-lg border border-dashed border-neutral-300 px-3 py-4 text-sm text-neutral-500">
                  {chatState.sessions.length === 0 ? "История появится после первого сообщения." : "Ничего не найдено."}
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
                          "flex min-h-9 w-full items-center rounded-lg px-3 py-2 text-left text-sm transition",
                          selected ? "bg-neutral-200 text-neutral-950" : "text-neutral-800 hover:bg-neutral-200/70",
                        )}
                        onClick={() => selectSession(session.id)}
                        disabled={isStreaming}
                        title={session.title}
                      >
                        <span className="min-w-0 flex-1 pr-8">
                          <span className="block truncate">{session.title}</span>
                        </span>
                        {selected && <span className="h-2 w-2 rounded-full bg-sky-500" />}
                      </button>
                      <button
                        type="button"
                        className="absolute right-2 top-1/2 hidden h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-neutral-500 transition hover:bg-white hover:text-rose-600 group-hover:grid"
                        onClick={() => deleteSession(session.id)}
                        disabled={isStreaming}
                        aria-label="Удалить диалог"
                        title="Удалить"
                      >
                        <Trash2 size={15} />
                      </button>
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
            className="fixed inset-0 z-20 bg-neutral-950/25 lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Закрыть боковую панель"
          />
        )}

        <main className="flex min-h-screen min-w-0 flex-1 flex-col bg-white">
          <header className="sticky top-0 z-10 flex h-16 items-center gap-3 bg-white/90 px-4 backdrop-blur">
            <button
              type="button"
              className="grid h-10 w-10 place-items-center rounded-lg text-neutral-700 transition hover:bg-neutral-100 lg:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="Открыть боковую панель"
              title="Меню"
            >
              <Menu size={21} />
            </button>
            <div className="mx-auto flex w-full max-w-4xl items-center">
              <div className="min-w-0 flex-1">
                <h1 className="truncate text-base font-medium text-neutral-900">
                  {activeSession?.title || "MathMod DataAgent"}
                </h1>
              </div>
              <div className="text-sm text-neutral-500">
                {isClarifying ? "Уточняю запрос" : streamingSessionId ? "Ответ формируется" : "Готов к запросу"}
              </div>
            </div>
          </header>

          <section className="flex-1 overflow-y-auto px-4 pb-32 pt-8">
            <div className="mx-auto w-full max-w-4xl">
              <div className="flex min-w-0 flex-col gap-7">
                {!activeSession?.messages.length && (
                  <div className="flex min-h-[58vh] flex-col items-center justify-center text-center">
                    <div className="mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-neutral-950 text-white">
                      <MessageSquarePlus size={26} />
                    </div>
                    <h2 className="text-2xl font-semibold tracking-normal text-neutral-950">MathMod DataAgent</h2>
                    <button
                      type="button"
                      className="mt-6 max-w-xl rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-left text-sm leading-6 text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950"
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
                        "max-w-[min(760px,100%)] whitespace-pre-wrap break-words text-[16px] leading-8",
                        message.role === "user"
                          ? "rounded-[24px] bg-[#f4f4f4] px-5 py-3 text-neutral-950"
                          : "px-0 py-1 text-neutral-900",
                        message.content.length === 0 && !message.clarification && "min-h-12 min-w-24",
                      )}
                    >
                      {message.clarification && (
                        <div className="mb-4 max-w-[640px] whitespace-normal rounded-xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-6 text-neutral-800">
                          <div className="font-medium text-neutral-950">
                            {message.clarification.question || "Нужно уточнение"}
                          </div>
                          {message.clarification.reason && (
                            <div className="mt-2 text-neutral-700">{message.clarification.reason}</div>
                          )}
                          {message.clarificationStatus === "pending" && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {message.clarification.options.map((option) => (
                                <button
                                  key={`${message.id}-${option.value}`}
                                  type="button"
                                  className="inline-flex min-h-9 items-center rounded-lg border border-neutral-200 bg-white px-3 py-1.5 text-sm text-neutral-800 transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50"
                                  onClick={() => chooseClarificationOption(option)}
                                  disabled={isStreaming}
                                >
                                  {option.label}
                                </button>
                              ))}
                            </div>
                          )}
                          {message.clarificationStatus === "answered" && (
                            <div className="mt-3 text-xs font-medium text-neutral-500">Уточнение применено</div>
                          )}
                        </div>
                      )}
                      {message.role === "assistant" && message.agentTrace && message.agentTrace.length > 0 && (
                        <details
                          className="group mb-4 max-w-[720px] whitespace-normal rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm leading-6 text-neutral-800 shadow-sm"
                          open
                        >
                          <summary className="mb-3 flex cursor-pointer list-none items-center justify-between gap-3">
                            <span className="font-medium text-neutral-950">Ход выполнения</span>
                            <span className="flex items-center gap-2">
                              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
                                {message.agentTrace.length} шагов
                              </span>
                              <span className="text-xs text-neutral-400 group-open:hidden">показать</span>
                              <span className="text-xs text-neutral-400 group-open:inline hidden">свернуть</span>
                            </span>
                          </summary>
                          <div className="space-y-2">
                            {message.agentTrace.map((trace, index) => {
                              const payload = formatTracePayload(trace.payload);
                              return (
                                <details
                                  key={trace.id}
                                  className="group rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2"
                                >
                                  <summary className="flex cursor-pointer list-none items-start gap-3">
                                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white text-xs font-semibold text-neutral-700 ring-1 ring-neutral-200">
                                      {index + 1}
                                    </span>
                                    <span className="min-w-0 flex-1">
                                      <span className="flex min-w-0 flex-wrap items-center gap-2">
                                        <span className="shrink-0 text-xs font-medium uppercase tracking-wide text-neutral-500">
                                          {getTraceTypeLabel(trace.type)}
                                        </span>
                                        <span className="min-w-0 truncate text-sm font-medium text-neutral-950">
                                          {trace.title}
                                        </span>
                                        {trace.tool && (
                                          <code className="max-w-[240px] truncate rounded bg-white px-1.5 py-0.5 text-xs text-neutral-600 ring-1 ring-neutral-200">
                                            {trace.tool}
                                          </code>
                                        )}
                                      </span>
                                    </span>
                                  </summary>
                                  {payload && (
                                    <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-neutral-200 bg-white p-3 text-xs leading-5 text-neutral-800">
                                      {payload}
                                    </pre>
                                  )}
                                </details>
                              );
                            })}
                          </div>
                        </details>
                      )}
                      {message.content ? (
                        message.role === "assistant" ? (
                          <AssistantAnswer content={message.content} />
                        ) : (
                          message.content
                        )
                      ) : (!message.clarification && (
                          <span className="inline-flex items-center gap-1 text-neutral-500">
                            <span className="h-2 w-2 animate-pulse rounded-full bg-current" />
                            <span className="h-2 w-2 animate-pulse rounded-full bg-current [animation-delay:120ms]" />
                            <span className="h-2 w-2 animate-pulse rounded-full bg-current [animation-delay:240ms]" />
                          </span>
                        ))}
                    </div>
                  </article>
                ))}

                {canShowCopy && (
                  <div className="flex flex-wrap items-center gap-1 pl-1">
                    <button
                      type="button"
                      className="grid h-9 w-9 place-items-center rounded-lg text-neutral-500 transition hover:bg-neutral-100 hover:text-neutral-950"
                      onClick={copyLatestAnswer}
                      aria-label="Скопировать ответ"
                      title="Скопировать"
                    >
                      <Copy size={17} />
                    </button>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>

            </div>
          </section>

          <aside className="fixed right-10 top-24 z-20 hidden max-h-[70vh] w-14 2xl:block">
            {(activeSession?.checkpoints ?? []).length === 0 && (
              <div className="flex h-28 flex-col items-center justify-center gap-4">
                {[0, 1, 2, 3].map((item) => (
                  <span key={item} className="h-2 w-2 rounded-full bg-neutral-200" aria-hidden="true" />
                ))}
              </div>
            )}

            <div className="max-h-[70vh] overflow-visible py-1">
              {(activeSession?.checkpoints ?? []).map((checkpoint, index, checkpoints) => {
                const isLast = index === checkpoints.length - 1;
                return (
                  <div key={checkpoint.id} className="group relative flex flex-col items-center">
                    <button
                      type="button"
                      className={cx(
                        "grid h-10 w-10 place-items-center rounded-full border bg-white text-[11px] font-medium tabular-nums shadow-sm transition hover:border-neutral-950 hover:text-neutral-950 disabled:cursor-not-allowed disabled:opacity-50",
                        isLast ? "border-sky-400 text-sky-700" : "border-neutral-200 text-neutral-500",
                      )}
                      onClick={() => rollbackToCheckpoint(checkpoint.id)}
                      disabled={isStreaming}
                      title={formatCheckpointTitle(checkpoint, index)}
                      aria-label={`Откатиться к чекпоинту ${index + 1}`}
                    >
                      {index + 1}
                    </button>

                    <div className="pointer-events-none absolute right-12 top-0 hidden w-72 rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm leading-5 text-neutral-800 shadow-[0_18px_50px_rgba(0,0,0,0.14)] group-hover:block">
                      <div className="font-medium text-neutral-950">{formatCheckpointTitle(checkpoint, index)}</div>
                      {checkpoint.pendingClarification?.clarification.question && (
                        <div className="mt-1 text-xs text-neutral-500">
                          {checkpoint.pendingClarification.clarification.question}
                        </div>
                      )}
                      {checkpoint.pendingClarification?.clarification.reason && (
                        <div className="mt-2 text-neutral-700">
                          {checkpoint.pendingClarification.clarification.reason}
                        </div>
                      )}
                    </div>

                    {!isLast && <div className="h-8 w-px bg-neutral-200" aria-hidden="true" />}
                  </div>
                );
              })}
            </div>
          </aside>

          <footer className="fixed bottom-0 left-0 right-0 bg-gradient-to-t from-white via-white to-white/0 px-4 pb-4 pt-10 lg:left-[260px]">
            <div className="mx-auto w-full max-w-4xl">
              {error && (
                <div className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {error}
                </div>
              )}
              {mode === "clarify" && (
                <div className="mb-3 flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
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
                className="flex items-end gap-2 rounded-[28px] border border-neutral-200 bg-white p-2 shadow-[0_18px_60px_rgba(0,0,0,0.12)] focus-within:border-neutral-300"
                onSubmit={sendMessage}
              >
                <textarea
                  ref={textareaRef}
                  className="max-h-48 min-h-12 flex-1 resize-none bg-transparent px-4 py-3 text-[16px] leading-6 text-neutral-950 outline-none placeholder:text-neutral-500"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder={mode === "clarify" ? "Уточните ответ" : "Спросите MathMod DataAgent"}
                  rows={1}
                  disabled={isStreaming}
                />
                <button
                  type="submit"
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-neutral-950 text-white transition hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-200 disabled:text-neutral-500"
                  disabled={!input.trim() || isStreaming}
                  aria-label="Отправить"
                  title="Отправить"
                >
                  <Send size={19} />
                </button>
              </form>
              <p className="mt-3 text-center text-xs text-neutral-500">
                MathMod может ошибаться. Проверяйте важные данные и расчёты.
              </p>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default App;
