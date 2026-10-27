/* Creado por Aldo Garcia. */
/**
 * Cliente HTTP del frontend.
 *
 * Decisiones de seguridad:
 *  - `credentials: "same-origin"`: la sesion viaja en una cookie HttpOnly que el
 *    JavaScript no puede leer. No se guarda NINGUN token en localStorage ni en
 *    sessionStorage.
 *  - el token CSRF se mantiene solo en memoria del modulo y se envia en la
 *    cabecera `X-CSRF-Token` de toda peticion mutante.
 *  - los errores del backend llegan tipados (`code`, `message`, `request_id`); el
 *    frontend nunca muestra stack traces.
 */

const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;

  constructor(code: string, message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

/** Token CSRF vigente. Solo en memoria: se pierde al recargar y se vuelve a pedir. */
let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function getCsrfToken(): string {
  return csrfToken;
}

type RequestOptions<T> = {
  method?: string;
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
  validate?: (payload: unknown) => payload is T;
};

async function request<T>(path: string, options: RequestOptions<T> = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;

  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  // Acotar cada intercambio HTTP evita dejar el compositor bloqueado si una
  // conexion no responde. La generacion sigue en el servidor y conserva su ID;
  // este limite no es el plazo de Gemma ni el tiempo total de la cola.
  const controller = new AbortController();
  const abort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  let requestId: string | null = null;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 30_000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: controller.signal,
    });

    requestId = response.headers.get("X-Request-ID");
    if (response.status === 204 && !options.validate) {
      return undefined as T;
    }

    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      const detail = isRecord(payload) ? payload : {};
      requestId ??= isNonEmptyText(detail.request_id) ? detail.request_id : null;
      throw new ApiError(
        isNonEmptyText(detail.code) ? detail.code : "internal_error",
        isNonEmptyText(detail.message) ? detail.message : "No fue posible completar la operacion.",
        response.status,
        requestId,
      );
    }

    // Los endpoints de chat validan tambien estados y referencias anidadas.
    // No se corrigen ni completan payloads corruptos para aparentar exito.
    if (options.validate) {
      if (options.validate(payload)) return payload;
    } else if (payload !== null) {
      return payload as T;
    }
    throw new ApiError(
      "invalid_response",
      "El servidor no devolvió una respuesta válida de Matrix RH. Compruebe la conexión e inténtelo nuevamente.",
      response.status,
      requestId,
    );
  } catch (caught) {
    if (timedOut && !options.signal?.aborted) {
      throw new ApiError(
        "request_timeout",
        "El servidor tardó demasiado en responder. Compruebe la conexión e inténtelo nuevamente.",
        408,
        requestId,
      );
    }
    throw caught;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyText(value: unknown): value is string {
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

function isChatReply(value: unknown): value is ChatReply {
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

function isChatRequestStatus(value: unknown): value is ChatRequestStatus {
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

function isChatCancellation(value: unknown): value is { ok: true } {
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

function isConversationDetail(value: unknown): value is ConversationDetail {
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
    value.attachments.every(
      (document) =>
        isRecord(document) &&
        isNonEmptyText(document.id) &&
        typeof document.filename === "string" &&
        isNonEmptyText(document.status) &&
        isCount(document.chunk_count) &&
        isNonEmptyText(document.scope) &&
        (document.category === null || typeof document.category === "string") &&
        (document.error_message === null || typeof document.error_message === "string"),
    ) &&
    (value.next_before_seq == null || (isCount(value.next_before_seq) && value.next_before_seq > 0))
  );
}

// --- Operaciones ------------------------------------------------------------

export const api = {
  async me(): Promise<Me> {
    const profile = await request<Me>("/me");
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async localLogin(username: string, password: string): Promise<Me> {
    const profile = await request<Me>("/auth/local/login", {
      method: "POST",
      body: { username, password },
    });
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<Me> {
    const profile = await request<Me>("/auth/local/change-password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    });
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async logout(): Promise<void> {
    await request("/auth/logout", { method: "POST" });
    setCsrfToken("");
  },

  listConversations(): Promise<ConversationSummary[]> {
    return request<ConversationSummary[]>("/conversations");
  },

  createConversation(title = ""): Promise<ConversationSummary> {
    return request<ConversationSummary>("/conversations", { method: "POST", body: { title } });
  },

  getConversation(
    id: string,
    signal?: AbortSignal,
    beforeSeq?: number,
  ): Promise<ConversationDetail> {
    const query = beforeSeq ? `?before_seq=${beforeSeq}` : "";
    return request<ConversationDetail>(`/conversations/${encodeURIComponent(id)}${query}`, {
      signal,
      validate: (value): value is ConversationDetail =>
        isConversationDetail(value) && value.id === id,
    });
  },

  deleteConversation(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },

  chat(
    message: string,
    conversationId: string | null,
    clientRequestId?: string,
    signal?: AbortSignal,
  ): Promise<ChatReply> {
    return request<ChatReply>("/chat", {
      method: "POST",
      body: { message, conversation_id: conversationId, client_request_id: clientRequestId },
      signal,
      timeoutMs: 660_000,
      validate: (value): value is ChatReply =>
        isChatReply(value) && (conversationId === null || value.conversation_id === conversationId),
    });
  },

  submitChat(
    message: string,
    conversationId: string | null,
    clientRequestId: string,
    signal?: AbortSignal,
  ): Promise<ChatRequestStatus> {
    return request<ChatRequestStatus>("/chat/submit", {
      method: "POST",
      body: { message, conversation_id: conversationId, client_request_id: clientRequestId },
      signal,
      validate: (value): value is ChatRequestStatus =>
        isChatRequestStatus(value) &&
        (conversationId === null || value.conversation_id === conversationId),
    });
  },

  chatStatus(
    id: string,
    signal?: AbortSignal,
    conversationId?: string | null,
  ): Promise<ChatRequestStatus> {
    return request<ChatRequestStatus>(`/chat/requests/${encodeURIComponent(id)}`, {
      signal,
      validate: (value): value is ChatRequestStatus =>
        isChatRequestStatus(value) &&
        (conversationId == null || value.conversation_id === conversationId),
    });
  },

  cancelChat(id: string, conversationId?: string | null): Promise<{ ok: boolean }> {
    return request(`/chat/requests/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      body: conversationId == null ? undefined : { conversation_id: conversationId },
      validate: isChatCancellation,
    });
  },

  uploadAttachments(
    conversationId: string,
    files: File[],
  ): Promise<{ documents: DocumentStatus[] }> {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    return request<{ documents: DocumentStatus[] }>(
      `/conversations/${encodeURIComponent(conversationId)}/attachments`,
      { method: "POST", formData: form, timeoutMs: 660_000 },
    );
  },

  documentStatus(documentId: string): Promise<DocumentStatus> {
    return request<DocumentStatus>(`/documents/${encodeURIComponent(documentId)}/status`);
  },
};
