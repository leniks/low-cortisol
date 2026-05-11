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
import { chatClientHeaders, createChatStreamUrl, syncDialog } from "./chatApi";
import { emptyState, loadChatState, saveChatState } from "./storage";
import type {
  AgentTraceEvent,
  AgentTracePhase,
  AgentTraceStatus,
  AgentTraceVisibility,
  ChatCheckpoint,
  ChatMessage,
  ChatSession,
  ClarificationOption,
  ClarificationTurn,
  ClarificationResult,
  PendingClarification,
  PersistedChatState,
  SendMode,
} from "./types";

const firstPrompt = "Найди и подготовь набор данных по динамике ВВП России и Казахстана за 2020-2025 годы.";
const executionStreamUiRevision = "execution-stream-compact-v2";

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
    clarificationTraceEnd: message.clarificationTraceEnd,
    clarificationHistory: message.clarificationHistory?.map(cloneClarificationTurn),
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
    steps: clarification.steps?.map((step) => ({
      ...step,
      options: step.options.map((option) => ({ ...option })),
    })),
  };
}

function cloneClarificationTurn(turn: ClarificationTurn): ClarificationTurn {
  return {
    ...turn,
    clarification: cloneClarification(turn.clarification),
    selectedOption: { ...turn.selectedOption },
    traceEnd: turn.traceEnd,
  };
}

function formatCheckpointTitle(checkpoint: ChatCheckpoint, index: number): string {
  return `${index + 1}. ${checkpoint.title}`;
}

function appendClarificationValue(message: string, value: string): string {
  const normalized = message.trim();
  if (!normalized) return value;
  if (normalized.toLowerCase().includes(value.trim().toLowerCase())) return normalized;
  return `${normalized}, ${value}`;
}

function normalizeClarificationToken(value: string): string {
  return value.trim().toLowerCase().replaceAll("ё", "е").replace(/\s+/g, " ");
}

function isManualClarificationOption(option: ClarificationOption): boolean {
  const value = normalizeClarificationToken(option.value);
  const label = normalizeClarificationToken(option.label);
  return (
    value === "manual" ||
    value === "__manual__" ||
    value === "ввести вручную" ||
    value === "введите вручную" ||
    label === "ввести вручную" ||
    label === "введите вручную" ||
    label.includes("вручную")
  );
}

function normalizeClarificationOptions(options: unknown[]): ClarificationOption[] {
  return options
    .filter(
      (option): option is ClarificationOption =>
        Boolean(option) &&
        typeof option === "object" &&
        typeof (option as ClarificationOption).label === "string" &&
        typeof (option as ClarificationOption).value === "string",
    )
    .map((option) => (isManualClarificationOption(option) ? { ...option, value: "manual" } : option));
}

type ClarificationField = ClarificationResult["missing_fields"][number];
type ClarificationStep = NonNullable<ClarificationResult["steps"]>[number];

function isClarificationField(value: unknown): value is ClarificationField {
  return value === "period" || value === "geography" || value === "metric" || value === "formula" || value === "other";
}

function normalizeClarificationSteps(value: unknown): ClarificationStep[] {
  if (!Array.isArray(value)) return [];

  return value
    .map((candidate): ClarificationStep | undefined => {
      if (!candidate || typeof candidate !== "object") return undefined;
      const step = candidate as Partial<ClarificationStep>;
      if (!isClarificationField(step.field)) return undefined;

      const options = Array.isArray(step.options) ? normalizeClarificationOptions(step.options) : [];
      if (options.length === 0) return undefined;

      return {
        field: step.field,
        question: typeof step.question === "string" || step.question === null ? step.question : undefined,
        reason: typeof step.reason === "string" ? step.reason : undefined,
        options,
      };
    })
    .filter((step): step is ClarificationStep => Boolean(step));
}

function clarificationSteps(clarification: ClarificationResult): ClarificationStep[] {
  if (clarification.steps?.length) return clarification.steps;

  return [
    {
      field: clarification.missing_fields.find(isClarificationField) ?? "other",
      question: clarification.question,
      reason: clarification.reason,
      options: clarification.options.length > 0
        ? clarification.options
        : [{ label: "Ввести вручную", value: "manual" }],
    },
  ];
}

function clarificationForStep(clarification: ClarificationResult, step: ClarificationStep): ClarificationResult {
  return {
    ...clarification,
    question: step.question ?? clarification.question,
    missing_fields: [step.field],
    options: step.options,
    reason: step.reason || clarification.reason,
  };
}

function currentPendingClarification(pending: PendingClarification): ClarificationResult {
  const steps = clarificationSteps(pending.clarification);
  const stepIndex = Math.min(pending.stepIndex ?? 0, steps.length - 1);
  return clarificationForStep(pending.clarification, steps[stepIndex]);
}

function clarificationFieldsKey(clarification: ClarificationResult): string {
  return clarification.missing_fields.length > 0
    ? [...clarification.missing_fields].sort().join("|")
    : clarification.question || "other";
}

function mergeClarificationHistory(
  history: ClarificationTurn[] | undefined,
  turn: ClarificationTurn,
): ClarificationTurn[] {
  const key = clarificationFieldsKey(turn.clarification);
  return [...(history ?? []).filter((item) => clarificationFieldsKey(item.clarification) !== key), turn];
}

function parseStreamClarification(value: unknown): ClarificationResult | undefined {
  if (!value || typeof value !== "object") return undefined;
  const event = value as { clarification?: unknown };
  const candidate = event.clarification && typeof event.clarification === "object"
    ? event.clarification
    : value;
  if (!candidate || typeof candidate !== "object") return undefined;

  const clarification = candidate as Partial<ClarificationResult>;
  if (
    typeof clarification.is_complete !== "boolean" ||
    !Array.isArray(clarification.missing_fields) ||
    !Array.isArray(clarification.options) ||
    typeof clarification.reason !== "string"
  ) {
    return undefined;
  }

  const steps = normalizeClarificationSteps(clarification.steps);

  return {
    is_complete: clarification.is_complete,
    question: clarification.question,
    missing_fields: clarification.missing_fields,
    options: normalizeClarificationOptions(clarification.options),
    ...(steps.length > 0 ? { steps } : {}),
    reason: clarification.reason,
  };
}

function createTraceEvent(data: Partial<AgentTraceEvent>): AgentTraceEvent {
  const type = data.type && ["thought", "tool_call", "tool_result", "iteration"].includes(data.type)
    ? data.type
    : "thought";
  return {
    id: createId(),
    type,
    title: data.title || "Событие выполнения",
    tool: data.tool,
    payload: undefined,
    phase: data.phase,
    status: data.status === "done" ? "done" : "running",
    visibility: data.visibility,
    createdAt: nowIso(),
  };
}

type ExecutionStreamItem = {
  id: string;
  phase: AgentTracePhase;
  title: string;
  status: AgentTraceStatus;
  details: AgentTraceEvent[];
  retryCount: number;
  hasSummary: boolean;
  clarifications: Array<{
    id: string;
    question: string;
    reason: string;
    selectedLabel?: string;
    pending: boolean;
  }>;
};

const tracePhaseOrder: AgentTracePhase[] = [
  "analysis",
  "planning",
  "retrieval",
  "sql",
  "calculation",
  "validation",
  "finalization",
  "clarification",
];

const tracePhaseTitles: Record<AgentTracePhase, string> = {
  analysis: "Анализирую запрос",
  planning: "Планирую получение данных",
  retrieval: "Ищу подходящие датасеты",
  sql: "Проверяю данные через SQL",
  calculation: "Считаю показатели",
  validation: "Проверяю качество ответа",
  finalization: "Готовлю итоговый ответ",
  clarification: "Уточняю параметры",
};

function inferTracePhase(trace: AgentTraceEvent): AgentTracePhase {
  if (trace.phase) return trace.phase;
  const tool = (trace.tool || "").toLowerCase();
  const title = trace.title.toLowerCase();

  if (tool.includes("request_user_clarification") || title.includes("уточнен")) return "clarification";
  if (tool.includes("submit_data_acquisition_plan") || title.includes("план")) return "planning";
  if (
    tool.includes("request_evidence") ||
    tool.includes("query_enricher") ||
    tool.includes("pgvector") ||
    tool.includes("rag") ||
    tool.includes("technical_dataset_filter") ||
    title.includes("rag") ||
    title.includes("датасет")
  ) {
    return "retrieval";
  }
  if (
    tool.includes("evidence_agent") ||
    tool.includes("duckdb") ||
    tool.includes("parquet") ||
    tool.includes("sql") ||
    title.includes("sql")
  ) {
    return "sql";
  }
  if (tool.includes("calculate") || title.includes("счит")) return "calculation";
  if (
    title.includes("невалид") ||
    title.includes("пустой") ||
    title.includes("ошибка") ||
    title.includes("повторн") ||
    title.includes("retry")
  ) {
    return "validation";
  }
  if (tool.includes("finalizer") || title.includes("финал") || title.includes("ответ") || title.includes("готов")) {
    return "finalization";
  }
  return "analysis";
}

function inferTraceStatus(trace: AgentTraceEvent): AgentTraceStatus {
  if (trace.status) return trace.status;
  const title = trace.title.toLowerCase();
  if (title.includes("ошибка") || title.includes("error")) return "error";
  if (title.includes("повторн") || title.includes("retry")) return "retry";

  return trace.type === "tool_call" ? "running" : "done";
}

function inferTraceVisibility(trace: AgentTraceEvent): AgentTraceVisibility {
  if (trace.visibility) return trace.visibility;
  if (trace.type === "thought" || trace.type === "iteration") return "summary";

  const phase = inferTracePhase(trace);
  const title = trace.title.toLowerCase();
  if (
    phase === "planning" ||
    phase === "finalization" ||
    title.includes("датасеты, использованные") ||
    title.includes("evidence pack готов") ||
    title.includes("ответ основного агента готов")
  ) {
    return "summary";
  }

  return "detail";
}

function compareTracePhases(left: AgentTracePhase, right: AgentTracePhase): number {
  return tracePhaseOrder.indexOf(left) - tracePhaseOrder.indexOf(right);
}

function buildExecutionStream(
  traces: AgentTraceEvent[],
  history: ClarificationTurn[],
  currentClarification: ClarificationResult | undefined,
  isActive: boolean,
): ExecutionStreamItem[] {
  const items = new Map<AgentTracePhase, ExecutionStreamItem>();

  function ensureItem(phase: AgentTracePhase): ExecutionStreamItem {
    const existing = items.get(phase);
    if (existing) return existing;

    const item: ExecutionStreamItem = {
      id: phase,
      phase,
      title: tracePhaseTitles[phase],
      status: "done",
      details: [],
      retryCount: 0,
      hasSummary: false,
      clarifications: [],
    };
    items.set(phase, item);
    return item;
  }

  traces.forEach((trace) => {
    const phase = inferTracePhase(trace);
    const status = inferTraceStatus(trace);
    const visibility = inferTraceVisibility(trace);
    const item = ensureItem(phase);

    item.details.push(trace);
    if (visibility === "summary") {
      item.title = trace.title;
      item.hasSummary = true;
    }
    if (status === "retry") item.retryCount += 1;
    if (status === "error") {
      item.status = "error";
    } else if (status === "done" && item.status !== "error") {
      item.status = "done";
    } else if (item.status !== "error" && status === "retry") {
      item.status = "retry";
    }
  });

  history.forEach((turn) => {
    const item = ensureItem("clarification");
    item.hasSummary = true;
    item.clarifications.push({
      id: turn.id,
      question: turn.clarification.question || "Уточнение",
      reason: turn.clarification.reason,
      selectedLabel: turn.selectedOption.label,
      pending: false,
    });
  });

  if (currentClarification) {
    const item = ensureItem("clarification");
    item.hasSummary = true;
    item.status = "running";
    item.clarifications.push({
      id: "current",
      question: currentClarification.question || "Нужно уточнение",
      reason: currentClarification.reason,
      pending: true,
    });
  }

  const result = [...items.values()].sort((left, right) => compareTracePhases(left.phase, right.phase));
  if (isActive && result.length > 0) {
    const lastRunnable = [...result].reverse().find((item) => item.status !== "error");
    if (lastRunnable) lastRunnable.status = "running";
  }
  return result;
}

function compactExecutionItems(items: ExecutionStreamItem[]): ExecutionStreamItem[] {
  const important = items.filter(
    (item) =>
      item.hasSummary ||
      item.status === "error" ||
      item.phase === "retrieval" ||
      item.phase === "sql" ||
      item.phase === "clarification" ||
      item.phase === "finalization",
  );
  const compact = important.length > 0 ? important : items;
  return compact.slice(0, 4);
}

function executionStatusLabel(status: AgentTraceStatus): string {
  return status === "done" ? "готово" : "в процессе";
}

function executionStatusClass(status: AgentTraceStatus): string {
  return status === "done" ? "bg-emerald-500" : "bg-sky-500";
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

function tableToCsv(headers: string[], rows: string[][]): string {
  return [headers, ...rows]
    .map((row) => row.map(csvCell).join(","))
    .join("\n");
}

function csvCell(value: string): string {
  const normalized = value.replace(/\r?\n/g, " ").trim();
  return /[",\n]/.test(normalized) ? `"${normalized.replaceAll("\"", "\"\"")}"` : normalized;
}

function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([`\uFEFF${content}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function csvFilename(sectionTitle: string | undefined, blockIndex: number): string {
  const slug = (sectionTitle || "table")
    .toLowerCase()
    .replaceAll("ё", "е")
    .replace(/[^a-zа-я0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return `${slug || "table"}-${blockIndex + 1}.csv`;
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const href = link[2];
      const rollbackPrefix = "rollback://checkpoint/";
      if (href.startsWith(rollbackPrefix)) {
        const checkpointId = href.slice(rollbackPrefix.length);
        return (
          <button
            key={`${part}-${index}`}
            type="button"
            className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-sm font-semibold text-amber-900 shadow-sm transition hover:border-amber-300 hover:bg-amber-100"
            onClick={async () => {
              if (!window.confirm("Откатить диалог к этому состоянию?")) return;
              const response = await fetch(`/invoke/checkpoints/${checkpointId}/rollback`, {
                method: "POST",
                headers: chatClientHeaders(),
              });
              if (!response.ok) {
                window.alert("Не удалось выполнить откат.");
                return;
              }
              window.location.reload();
            }}
          >
            {link[1]}
          </button>
        );
      }

      return (
        <a
          key={`${part}-${index}`}
          className="font-semibold text-sky-700 underline decoration-sky-300 underline-offset-4 transition hover:text-sky-900"
          href={href}
          target={href.startsWith("/") ? undefined : "_blank"}
          rel={href.startsWith("/") ? undefined : "noreferrer"}
        >
          {link[1]}
        </a>
      );
    }

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
            blockIndex={index}
            sectionTitle={title}
          />
        ))}
      </div>
    </section>
  );
}

function AnswerBlockView({
  block,
  datasetMode,
  blockIndex,
  sectionTitle,
}: {
  block: AnswerBlock;
  datasetMode: boolean;
  blockIndex: number;
  sectionTitle?: string;
}): ReactNode {
  if (block.type === "code") {
    const language = block.language || "text";
    return (
      <div className="overflow-hidden rounded-lg border border-neutral-800 bg-neutral-950">
        <div className="flex h-9 items-center justify-between border-b border-white/10 px-3 text-xs text-neutral-300">
          <span className="font-medium uppercase tracking-wide">{language}</span>
          {language.toLowerCase() === "sql" && <span>Аналитический SQL</span>}
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
    const filename = csvFilename(sectionTitle, blockIndex);
    return (
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="flex items-center justify-end border-b border-neutral-100 bg-neutral-50 px-3 py-2">
          <button
            type="button"
            className="inline-flex min-h-8 items-center rounded-md border border-neutral-200 bg-white px-3 text-xs font-semibold text-neutral-800 shadow-sm transition hover:border-neutral-300 hover:bg-neutral-100"
            onClick={() => downloadCsv(filename, tableToCsv(block.headers, block.rows))}
          >
            Скачать CSV
          </button>
        </div>
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
  const [chatState, setChatState] = useState<PersistedChatState>(emptyState);
  const [chatStateLoaded, setChatStateLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<SendMode>("normal");
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionFilter, setSessionFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const traceCountsRef = useRef<Record<string, number>>({});
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
  const canShowCopy = Boolean(latestAssistantMessage?.content.trim()) && !isStreaming;

  useEffect(() => {
    let cancelled = false;
    void loadChatState()
      .then((state) => {
        if (cancelled) return;
        setChatState(state);
        setChatStateLoaded(true);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Историю чатов из Postgres загрузить не удалось.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!chatStateLoaded) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void saveChatState(chatState, controller.signal).catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError("Историю чатов в Postgres сохранить не удалось.");
      });
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [chatState, chatStateLoaded]);

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
    if (!chatStateLoaded) {
      setError("История чатов еще загружается.");
      return;
    }
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
    if (isStreaming || !chatStateLoaded) return;
    setChatState((current) => ({ ...current, activeSessionId: sessionId }));
    setMode("normal");
    setError(null);
    setSidebarOpen(false);
  }

  function deleteSession(sessionId: string): void {
    if (streamingSessionId === sessionId || !chatStateLoaded) return;
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
    traceCountsRef.current[assistantMessageId] = (traceCountsRef.current[assistantMessageId] ?? 0) + 1;
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
    traceEnd: number,
  ): void {
    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== sessionId) return session;
        return {
          ...session,
          updatedAt: nowIso(),
          messages: session.messages.map((message) =>
            message.id === assistantMessageId
              ? {
                  ...message,
                  content: status === "pending" ? "" : message.content,
                  clarification,
                  clarificationStatus: status,
                  clarificationTraceEnd: traceEnd,
                }
              : message,
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
    userMessageId: string,
    assistantMessageId: string,
    sendMode: SendMode,
  ): void {
    closeCurrentStream();
    setError(null);
    setStreamingSessionId(session.id);
    traceCountsRef.current[assistantMessageId] =
      session.messages.find((item) => item.id === assistantMessageId)?.agentTrace?.length ??
      traceCountsRef.current[assistantMessageId] ??
      0;

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

    source.addEventListener("clarification", (event) => {
      try {
        const data = JSON.parse(event.data || "{}") as unknown;
        const clarification = parseStreamClarification(data);
        if (!clarification || clarification.is_complete) return;
        const traceEnd = traceCountsRef.current[assistantMessageId] ?? 0;

        updateAssistantClarification(session.id, assistantMessageId, clarification, "pending", traceEnd);
        setPendingClarification(session.id, {
          id: createId(),
          message,
          sendMode,
          assistantMessageId,
          userMessageId,
          clarification,
          traceEnd,
          createdAt: nowIso(),
        });
        closeCurrentStream();
      } catch {
        // Ignore malformed clarification events.
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

  function chooseClarificationOption(option: ClarificationOption): void {
    if (!activeSession?.pendingClarification || isStreaming) return;

    const pending = activeSession.pendingClarification;
    const steps = clarificationSteps(pending.clarification);
    const stepIndex = Math.min(pending.stepIndex ?? 0, steps.length - 1);
    const currentClarification = clarificationForStep(pending.clarification, steps[stepIndex]);
    if (isManualClarificationOption(option)) {
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
    const answeredTurn: ClarificationTurn = {
      id: `${pending.id}-${stepIndex}`,
      clarification: cloneClarification(currentClarification),
      selectedOption: { ...option },
      traceEnd: pending.traceEnd,
      createdAt: timestamp,
    };
    const nextStepIndex = stepIndex + 1;

    if (nextStepIndex < steps.length) {
      const nextClarification = clarificationForStep(pending.clarification, steps[nextStepIndex]);
      updateSessions((sessions) =>
        sessions.map((session) => {
          if (session.id !== activeSession.id) return session;

          const messages = session.messages.map((message) =>
            message.id === pending.userMessageId
              ? { ...message, content: clarifiedMessage }
              : message.id === pending.assistantMessageId
                ? {
                    ...message,
                    clarification: cloneClarification(nextClarification),
                    clarificationStatus: "pending" as const,
                    clarificationTraceEnd: pending.traceEnd,
                    clarificationHistory: mergeClarificationHistory(message.clarificationHistory, answeredTurn),
                  }
                : message,
          );

          return {
            ...session,
            updatedAt: timestamp,
            pendingClarification: {
              ...pending,
              message: clarifiedMessage,
              stepIndex: nextStepIndex,
            },
            messages,
          };
        }),
      );
      return;
    }

    updateSessions((sessions) =>
      sessions.map((session) => {
        if (session.id !== activeSession.id) return session;

        const messages = session.messages.map((message) =>
          message.id === pending.userMessageId
            ? { ...message, content: clarifiedMessage }
            : message.id === pending.assistantMessageId
              ? {
                  ...message,
                  clarification: undefined,
                  clarificationStatus: undefined,
                  clarificationTraceEnd: undefined,
                  clarificationHistory: mergeClarificationHistory(message.clarificationHistory, answeredTurn),
                }
              : message,
        );
        const checkpoint: ChatCheckpoint = {
          id: pending.id,
          title: `${currentClarification.question || "Уточнение"}: ${option.label}`,
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

    startStream(activeSession, clarifiedMessage, pending.userMessageId, pending.assistantMessageId, pending.sendMode);
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
    if (!chatStateLoaded) {
      setError("История чатов еще загружается.");
      return;
    }

    const session = ensureActiveSession();
    const sendMode = mode;
    const { userMessage, assistantMessage } = stageOutgoingMessages(session, message, sendMode);
    setInput("");
    setMode("normal");

    startStream(session, message, userMessage.id, assistantMessage.id, sendMode);
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

  function renderExecutionStreamItem(item: ExecutionStreamItem): ReactNode {
    return (
      <div key={item.id} className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
        <div className="flex items-start gap-3">
          <span className="mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-neutral-100">
            <span className={cx("h-2 w-2 rounded-full", executionStatusClass(item.status))} />
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="font-medium text-neutral-950">{item.title}</span>
              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-500">
                {executionStatusLabel(item.status)}
              </span>
            </div>
            {item.clarifications.map((clarification) => (
              <div key={clarification.id} className="text-sm leading-6 text-neutral-700">
                <span>{clarification.question}</span>
                {clarification.selectedLabel && (
                  <span className="font-medium text-neutral-950"> Выбрано: {clarification.selectedLabel}</span>
                )}
                {clarification.pending && (
                  <span className="font-medium text-neutral-950"> Выберите вариант над полем ввода.</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  function renderExecutionTimeline(message: ChatMessage): ReactNode {
    const traces = message.agentTrace ?? [];
    const history = message.clarificationHistory ?? [];
    const isActiveMessage = activeSession?.id === streamingSessionId && latestAssistantMessage?.id === message.id;
    const streamItems = buildExecutionStream(traces, history, message.clarification, Boolean(isActiveMessage));
    if (streamItems.length === 0) return null;

    const visibleItems = compactExecutionItems(streamItems);
    const hiddenCount = Math.max(streamItems.length - visibleItems.length, 0);

    return (
      <div
        className="mb-4 max-w-[720px] whitespace-normal rounded-xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-6 text-neutral-800"
        data-trace-ui={executionStreamUiRevision}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <span className="font-medium text-neutral-950">Выполнение</span>
          <span className="rounded-full bg-white px-2 py-0.5 text-xs text-neutral-500 ring-1 ring-neutral-200">
            {isActiveMessage ? "идёт поток" : "сводка"}
          </span>
        </div>
        <div className="space-y-2">{visibleItems.map(renderExecutionStreamItem)}</div>
        {hiddenCount > 0 && (
          <details className="mt-2 rounded-lg border border-neutral-200 bg-white px-3 py-2">
            <summary className="cursor-pointer list-none text-xs font-medium text-neutral-500 transition hover:text-neutral-900">
              Показать ещё {hiddenCount}
            </summary>
            <div className="mt-2 space-y-2">
              {streamItems.filter((item) => !visibleItems.includes(item)).map(renderExecutionStreamItem)}
            </div>
          </details>
        )}
      </div>
    );
  }

  function renderPendingClarificationPanel(): ReactNode {
    const pending = activeSession?.pendingClarification;
    if (!pending) return null;

    const clarification = currentPendingClarification(pending);
    const options = clarification.options.length > 0
      ? clarification.options
      : [{ label: "Ввести вручную", value: "manual" }];

    return (
      <div className="mb-3 rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm shadow-[0_12px_40px_rgba(0,0,0,0.08)]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium uppercase text-neutral-500">Нужно уточнение</div>
            <div className="mt-1 font-medium leading-6 text-neutral-950">
              {clarification.question || "Уточните параметры запроса"}
            </div>
            {clarification.reason && (
              <div className="mt-1 text-xs leading-5 text-neutral-500">{clarification.reason}</div>
            )}
          </div>
          <div className="flex flex-wrap gap-2 sm:max-w-[52%] sm:justify-end">
            {options.map((option) => (
              <button
                key={`${pending.id}-${option.value}`}
                type="button"
                className="inline-flex min-h-9 items-center rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-1.5 text-sm font-medium text-neutral-800 transition hover:border-neutral-300 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => chooseClarificationOption(option)}
                disabled={isStreaming}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
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
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-2 rounded-full bg-neutral-900/90 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-white">
                <span className="h-2 w-2 rounded-full bg-cyan-300" />
                MathMod DataAgent
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-base font-medium text-neutral-900">
                {activeSession?.title || "Диалог"}
              </h1>
            </div>
            <div className="ml-auto flex items-center gap-3">
              <span className="text-sm text-neutral-500">
                {streamingSessionId ? "Ответ формируется" : activeSession?.pendingClarification ? "Нужно уточнение" : "Готов к запросу"}
              </span>
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
                    <h2 className="text-2xl font-semibold tracking-normal text-neutral-950">Сформируем данные по вашему запросу</h2>
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
                        message.content.length === 0 &&
                          !message.clarification &&
                          !message.clarificationHistory?.length &&
                          "min-h-12 min-w-24",
                      )}
                    >
                      {message.role === "assistant" && renderExecutionTimeline(message)}
                      {message.content ? (
                        message.role === "assistant" ? (
                          <AssistantAnswer content={message.content} />
                        ) : (
                          message.content
                        )
                      ) : (!message.clarification && !message.clarificationHistory?.length && (
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

          <footer className="fixed bottom-0 left-0 right-0 bg-gradient-to-t from-white via-white to-white/0 px-4 pb-4 pt-10 lg:left-[260px]">
            <div className="mx-auto w-full max-w-4xl">
              {error && (
                <div className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {error}
                </div>
              )}
              {renderPendingClarificationPanel()}
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
