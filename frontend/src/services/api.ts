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

import {
  isAcknowledgement,
  isAttachmentUpload,
  isChatReply,
  isChatRequestStatus,
  isConversationDetail,
  isConversationList,
  isConversationSummary,
  isDocumentStatus,
  isMe,
  isNonEmptyText,
  isRecord,
  type ChatReply,
  type ChatRequestStatus,
  type ConversationDetail,
  type ConversationSummary,
  type DocumentStatus,
  type Me,
} from "./contracts";

// Mantiene compatibles los consumidores existentes del cliente HTTP.
export type {
  AnswerBasis,
  ChatMessage,
  ChatReply,
  ChatRequestStatus,
  ConversationDetail,
  ConversationSummary,
  DocumentStatus,
  Me,
  SourceRef,
} from "./contracts";

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
  validate: (payload: unknown) => payload is T;
};

async function request<T>(path: string, options: RequestOptions<T>): Promise<T> {
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

    // Todo endpoint debe validar su contrato, incluidas las confirmaciones.
    // Un JSON valido no demuestra que una operacion se haya completado.
    if (options.validate(payload)) return payload;
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

// --- Operaciones ------------------------------------------------------------

export const api = {
  async me(): Promise<Me> {
    const profile = await request<Me>("/me", { validate: isMe });
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async localLogin(username: string, password: string): Promise<Me> {
    const profile = await request<Me>("/auth/local/login", {
      method: "POST",
      body: { username, password },
      validate: isMe,
    });
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<Me> {
    const profile = await request<Me>("/auth/local/change-password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
      validate: isMe,
    });
    setCsrfToken(profile.csrf_token);
    return profile;
  },

  async logout(): Promise<void> {
    await request("/auth/logout", { method: "POST", validate: isAcknowledgement });
    setCsrfToken("");
  },

  listConversations(): Promise<ConversationSummary[]> {
    return request<ConversationSummary[]>("/conversations", { validate: isConversationList });
  },

  createConversation(title = ""): Promise<ConversationSummary> {
    return request<ConversationSummary>("/conversations", {
      method: "POST",
      body: { title },
      validate: isConversationSummary,
    });
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
      validate: isAcknowledgement,
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
      validate: isAcknowledgement,
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
      { method: "POST", formData: form, timeoutMs: 660_000, validate: isAttachmentUpload },
    );
  },

  documentStatus(documentId: string): Promise<DocumentStatus> {
    return request<DocumentStatus>(`/documents/${encodeURIComponent(documentId)}/status`, {
      validate: (value): value is DocumentStatus =>
        isDocumentStatus(value) && value.id === documentId,
    });
  },
};
