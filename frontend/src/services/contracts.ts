/* Creado por Aldo Garcia. */
/** Contratos HTTP de Matrix RH y validacion en el limite de confianza. */

// --- Tipos de dominio -------------------------------------------------------

export type Me = {
  user_id: string;
  username: string;
  display_name: string;
  auth_source: string;
  roles: string[];
  permissions: string[];
  allowed_categories: string[];
  category_wildcard: boolean;
  csrf_token: string;
};

export type SourceRef = {
  source_id: string;
  category: string;
  filename: string;
  section: string;
  page_or_sheet: string;
  score: number;
  label: string;
  scope: string;
  provenance?: {
    document_sha256?: string | null;
    generation?: string | null;
    index_fingerprint?: string | null;
    embedding_model?: string | null;
    embedding_dimension?: number | null;
    ingestion_version?: string | null;
  } | null;
};

/** Procedencia de la respuesta; no certifica la veracidad de sus afirmaciones. */
export type AnswerBasis = "documented" | "general" | "mixed" | "insufficient";

export type ChatReply = {
  conversation_id: string;
  message_id: string;
  answer: string;
  sources: SourceRef[];
  intent: string;
  grounded: boolean;
  answer_basis?: AnswerBasis | null;
  latency_ms: number;
};

type LatencySummary = {
  samples: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
};

type ChatMetrics = {
  sample_limit: number;
  sample_size: number;
  cache_ttl_ms: number;
  terminal_counts: { completed: number; failed: number; cancelled: number; expired: number };
  queue_ms: LatencySummary;
  execution_ms: LatencySummary;
  total_ms: LatencySummary;
};

export type ChatRequestStatus = {
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "expired";
  conversation_id: string;
  operation_id?: string | null;
  response: ChatReply | null;
  error_code?: string | null;
  capacity: {
    active: number;
    limit: number;
    queued: number;
    queue_limit: number;
    utilization_pct: number;
    overloaded: boolean;
    inference_active?: number | null;
    inference_limit?: number | null;
    metrics?: ChatMetrics | null;
  };
  notice: string | null;
  poll_after_ms: number;
  timing?: {
    queue_ms: number | null;
    execution_ms: number | null;
    total_ms: number | null;
  } | null;
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isNonEmptyText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isCount(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isSafeInteger(value) && value >= 0;
}

function isOptionalText(value: unknown): boolean {
  return value === undefined || value === null || isNonEmptyText(value);
}

function isSourceRef(value: unknown): value is SourceRef {
  return (
    isRecord(value) &&
    isNonEmptyText(value.source_id) &&
    ["category", "filename", "section", "page_or_sheet", "label", "scope"].every(
      (field) => typeof value[field] === "string",
    ) &&
    isFiniteNumber(value.score) &&
    isSourceProvenance(value.provenance)
  );
}

function isSourceProvenance(value: unknown): boolean {
  return (
    value == null ||
    (isRecord(value) &&
      [
        "document_sha256",
        "generation",
        "index_fingerprint",
        "embedding_model",
        "ingestion_version",
      ].every((field) => isOptionalText(value[field])) &&
      (value.embedding_dimension == null ||
        (isCount(value.embedding_dimension) && value.embedding_dimension > 0)))
  );
}

export function isChatReply(value: unknown): value is ChatReply {
  return (
    isRecord(value) &&
    isNonEmptyText(value.conversation_id) &&
    isNonEmptyText(value.message_id) &&
    isNonEmptyText(value.answer) &&
    Array.isArray(value.sources) &&
    value.sources.every(isSourceRef) &&
    isNonEmptyText(value.intent) &&
    typeof value.grounded === "boolean" &&
    isCount(value.latency_ms) &&
    isAnswerBasis(value.answer_basis)
  );
}

function isAnswerBasis(value: unknown): boolean {
  return (
    value == null ||
    (typeof value === "string" &&
      ["documented", "general", "mixed", "insufficient"].includes(value))
  );
}

function isChatCapacity(value: unknown): value is ChatRequestStatus["capacity"] {
  return (
    isRecord(value) &&
    isCount(value.active) &&
    isCount(value.limit) &&
    value.limit > 0 &&
    isCount(value.queued) &&
    isCount(value.queue_limit) &&
    isFiniteNumber(value.utilization_pct) &&
    value.utilization_pct >= 0 &&
    value.utilization_pct <= 100 &&
    typeof value.overloaded === "boolean" &&
    (value.inference_active == null || isCount(value.inference_active)) &&
    (value.inference_limit == null ||
      (isCount(value.inference_limit) && value.inference_limit > 0)) &&
    isChatMetrics(value.metrics)
  );
}

function isLatencySummary(value: unknown): value is LatencySummary {
  if (!isRecord(value) || !isCount(value.samples)) return false;
  if (value.samples === 0) return value.p50 === null && value.p95 === null && value.p99 === null;
  return (
    isFiniteNumber(value.p50) &&
    value.p50 >= 0 &&
    isFiniteNumber(value.p95) &&
    value.p95 >= value.p50 &&
    isFiniteNumber(value.p99) &&
    value.p99 >= value.p95
  );
}

function isChatMetrics(value: unknown): boolean {
  if (value == null) return true;
  if (
    !isRecord(value) ||
    !isCount(value.sample_limit) ||
    value.sample_limit < 1 ||
    !isCount(value.sample_size) ||
    value.sample_size > value.sample_limit ||
    !isCount(value.cache_ttl_ms) ||
    !isRecord(value.terminal_counts) ||
    !isLatencySummary(value.queue_ms) ||
    !isLatencySummary(value.execution_ms) ||
    !isLatencySummary(value.total_ms)
  )
    return false;
  const terminalCounts = value.terminal_counts;
  const sampleSize = value.sample_size;
  const counts = ["completed", "failed", "cancelled", "expired"].map(
    (field) => terminalCounts[field],
  );
  return (
    counts.every(isCount) &&
    counts.reduce((total, count) => total + count, 0) === sampleSize &&
    [value.queue_ms, value.execution_ms, value.total_ms].every(
      (summary) => summary.samples <= sampleSize,
    )
  );
}

function isChatTiming(value: unknown): boolean {
  return (
    value == null ||
    (isRecord(value) &&
      ["queue_ms", "execution_ms", "total_ms"].every(
        (field) => value[field] === null || (isFiniteNumber(value[field]) && value[field] >= 0),
      ))
  );
}

export function isChatRequestStatus(value: unknown): value is ChatRequestStatus {
  if (
    !isRecord(value) ||
    typeof value.status !== "string" ||
    !["queued", "running", "completed", "failed", "cancelled", "expired"].includes(value.status) ||
    !isNonEmptyText(value.conversation_id) ||
    !isOptionalText(value.operation_id) ||
    !isOptionalText(value.error_code) ||
    (value.error_code != null && value.status !== "failed") ||
    !isChatCapacity(value.capacity) ||
    !(value.notice === null || typeof value.notice === "string") ||
    !isCount(value.poll_after_ms) ||
    value.poll_after_ms < 1000 ||
    value.poll_after_ms > 10000 ||
    !isChatTiming(value.timing)
  ) {
    return false;
  }
  return value.status === "completed"
    ? isChatReply(value.response) && value.response.conversation_id === value.conversation_id
    : value.response === null;
}

export function isAcknowledgement(value: unknown): value is { ok: true } {
  return isRecord(value) && value.ok === true;
}

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: string;
  content: string;
  model: string | null;
  created_at: string;
  // Entregas anteriores guardaban solo el ID. Los metadatos adicionales
  // provienen del backend tras comprobar el acceso actual a cada fuente.
  sources: (Pick<SourceRef, "source_id"> & Partial<Omit<SourceRef, "source_id">>)[];
  intent?: string | null;
  answer_basis?: AnswerBasis | null;
};

export type DocumentStatus = {
  id: string;
  filename: string;
  status: string;
  chunk_count: number;
  scope: string;
  category: string | null;
  error_message: string | null;
};

export type ConversationDetail = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  attachments: DocumentStatus[];
  next_before_seq?: number | null;
};

function isHistorySource(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNonEmptyText(value.source_id) &&
    ["category", "filename", "section", "page_or_sheet", "label", "scope"].every(
      (field) => value[field] === undefined || typeof value[field] === "string",
    ) &&
    (value.score === undefined || isFiniteNumber(value.score)) &&
    isSourceProvenance(value.provenance)
  );
}

export function isConversationDetail(value: unknown): value is ConversationDetail {
  return (
    isRecord(value) &&
    isNonEmptyText(value.id) &&
    typeof value.title === "string" &&
    isNonEmptyText(value.created_at) &&
    isNonEmptyText(value.updated_at) &&
    Array.isArray(value.messages) &&
    value.messages.every(
      (message) =>
        isRecord(message) &&
        isNonEmptyText(message.id) &&
        isNonEmptyText(message.role) &&
        typeof message.content === "string" &&
        (message.model === null || typeof message.model === "string") &&
        isNonEmptyText(message.created_at) &&
        isOptionalText(message.intent) &&
        isAnswerBasis(message.answer_basis) &&
        Array.isArray(message.sources) &&
        message.sources.every(isHistorySource),
    ) &&
    Array.isArray(value.attachments) &&
    value.attachments.every(isDocumentStatus) &&
    (value.next_before_seq == null || (isCount(value.next_before_seq) && value.next_before_seq > 0))
  );
}

export function isMe(value: unknown): value is Me {
  return (
    isRecord(value) &&
    ["user_id", "username", "auth_source", "csrf_token"].every((field) =>
      isNonEmptyText(value[field]),
    ) &&
    typeof value.display_name === "string" &&
    ["roles", "permissions", "allowed_categories"].every((field) => {
      const items = value[field];
      return Array.isArray(items) && items.every(isNonEmptyText);
    }) &&
    typeof value.category_wildcard === "boolean"
  );
}

export function isConversationSummary(value: unknown): value is ConversationSummary {
  return (
    isRecord(value) &&
    isNonEmptyText(value.id) &&
    typeof value.title === "string" &&
    isNonEmptyText(value.created_at) &&
    isNonEmptyText(value.updated_at)
  );
}

export function isConversationList(value: unknown): value is ConversationSummary[] {
  return Array.isArray(value) && value.every(isConversationSummary);
}

export function isDocumentStatus(value: unknown): value is DocumentStatus {
  return (
    isRecord(value) &&
    isNonEmptyText(value.id) &&
    typeof value.filename === "string" &&
    isNonEmptyText(value.status) &&
    isCount(value.chunk_count) &&
    isNonEmptyText(value.scope) &&
    (value.category === null || typeof value.category === "string") &&
    (value.error_message === null || typeof value.error_message === "string")
  );
}

export function isAttachmentUpload(value: unknown): value is { documents: DocumentStatus[] } {
  return (
    isRecord(value) && Array.isArray(value.documents) && value.documents.every(isDocumentStatus)
  );
}
