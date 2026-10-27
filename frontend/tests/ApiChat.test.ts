/* Creado por Aldo Garcia. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setCsrfToken } from "../src/services/api";
import contract from "../../backend/tests/fixtures/chat_contract.json";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setCsrfToken("");
});

describe("contratos de chat compartidos con Pydantic", () => {
  it.each(["submit", "poll", "legacy"])(
    "rechaza una conversación distinta en %s aunque el resultado sea internamente consistente",
    async (operation) => {
      const payload = operation === "legacy" ? contract.completed.response : contract.completed;
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify(payload), {
            headers: { "X-Request-ID": "synthetic-wrong-conversation" },
          }),
        ),
      );
      const result =
        operation === "submit"
          ? api.submitChat("Consulta", "requested-A", "synthetic-request")
          : operation === "poll"
            ? api.chatStatus("synthetic-request", undefined, "requested-A")
            : api.chat("Consulta", "requested-A");
      await expect(result).rejects.toMatchObject({
        code: "invalid_response",
        requestId: "synthetic-wrong-conversation",
      });
    },
  );

  it("envía la conversación conocida al cancelar", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":true}'));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.cancelChat("synthetic-request", "A")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat/requests/synthetic-request/cancel",
      expect.objectContaining({
        body: JSON.stringify({ conversation_id: "A" }),
        method: "POST",
      }),
    );
  });

  it("rechaza un historial de otro hilo y conserva fuentes legacy del hilo correcto", async () => {
    const history = {
      id: "B",
      title: "Sintético",
      created_at: "2026-10-26",
      updated_at: "2026-10-26",
      messages: [
        {
          id: "synthetic-message",
          role: "assistant",
          content: "Historial ficticio",
          model: null,
          created_at: "2026-10-26",
          sources: [{ source_id: "legacy#0" }],
        },
      ],
      attachments: [],
      next_before_seq: null,
    };
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(history), {
          headers: { "X-Request-ID": "synthetic-history" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.getConversation("A")).rejects.toMatchObject({
      code: "invalid_response",
      requestId: "synthetic-history",
    });
    history.id = "A";
    await expect(api.getConversation("A", undefined, 20)).resolves.toEqual(history);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/conversations/A?before_seq=20",
      expect.any(Object),
    );
  });

  it.each(contract.invalid_mutations)(
    "rechaza $name sin perder la referencia",
    async (mutation) => {
      const payload = structuredClone(contract.completed);
      let target: Record<string | number, unknown> = payload;
      for (const key of mutation.path.slice(0, -1)) {
        target = target[key] as Record<string | number, unknown>;
      }
      target[mutation.path[mutation.path.length - 1]] = mutation.value;
      const fetchMock = vi.fn().mockImplementation(
        async () =>
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { "X-Request-ID": "synthetic-invalid-contract" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);
      for (const operation of [
        () => api.submitChat("Consulta ficticia", "synthetic-conversation", "synthetic-request"),
        () => api.chatStatus("synthetic-request"),
      ]) {
        await expect(operation()).rejects.toMatchObject({
          code: "invalid_response",
          status: 200,
          requestId: "synthetic-invalid-contract",
        });
      }
      expect(fetchMock).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["queued", "running", "completed", "failed", "cancelled", "expired"])(
    "acepta el estado %s y conserva las métricas y metadatos opcionales",
    async (status) => {
      const payload = {
        ...contract.completed,
        status,
        response: status === "completed" ? contract.completed.response : null,
        error_code: status === "failed" ? "generation_failed" : null,
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
      await expect(api.chatStatus("synthetic-request")).resolves.toEqual(payload);
    },
  );

  it("mantiene la compatibilidad con respuestas antiguas sin campos opcionales", async () => {
    const payload: Record<string, unknown> = structuredClone(contract.completed);
    for (const field of ["operation_id", "error_code", "timing"]) delete payload[field];
    const response = payload.response as Record<string, unknown>;
    delete response.answer_basis;
    const capacity = payload.capacity as Record<string, unknown>;
    for (const field of ["inference_active", "inference_limit", "metrics"]) delete capacity[field];
    const source = (response.sources as Record<string, unknown>[])[0];
    delete source.provenance;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
    await expect(api.chatStatus("synthetic-legacy")).resolves.toEqual(payload);
  });

  it.each(["{}", "[]", "true", '"completed"', '{"status":"completed"}'])(
    "rechaza JSON con forma incompleta: %s",
    async (body) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
      await expect(api.chatStatus("synthetic-request")).rejects.toMatchObject({
        code: "invalid_response",
      });
    },
  );

  it("rechaza un número JSON no finito en una fuente del chat síncrono", async () => {
    const body = JSON.stringify(contract.completed.response).replace(
      '"score":0.7',
      '"score":1e309',
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    await expect(api.chat("Consulta", null)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("valida también las fuentes del chat síncrono", async () => {
    const payload = { ...contract.completed.response, sources: [{ source_id: "incomplete" }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
    await expect(api.chat("Consulta", null)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each([{ ok: false }, { ok: "true" }, { ok: 1 }, {}, []])(
    "no confirma cancelación con una respuesta inválida: %j",
    async (payload) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
      await expect(api.cancelChat("synthetic-request")).rejects.toMatchObject({
        code: "invalid_response",
      });
    },
  );

  it("no confirma operaciones a partir de 204 sin cuerpo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(null, {
          status: 204,
          headers: { "X-Request-ID": "synthetic-empty" },
        }),
      ),
    );
    await expect(api.submitChat("Consulta", null, "synthetic-request")).rejects.toMatchObject({
      code: "invalid_response",
      requestId: "synthetic-empty",
    });
  });

  it("controla errores HTTP malformados sin enviar objetos a la interfaz", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: {},
            message: [],
            request_id: "synthetic-body-reference",
          }),
          { status: 500 },
        ),
      ),
    );
    await expect(api.chatStatus("synthetic-request")).rejects.toMatchObject({
      code: "internal_error",
      message: "No fue posible completar la operacion.",
      requestId: "synthetic-body-reference",
      status: 500,
    });
  });
});

describe("transporte de solicitudes aceptadas", () => {
  it("rechaza una página HTML del proxy sin tratarla como respuesta de la API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html>Proxy</html>", {
          status: 200,
          headers: { "X-Request-ID": "synthetic-proxy" },
        }),
      ),
    );
    await expect(api.chatStatus("request-uncertain")).rejects.toMatchObject({
      code: "invalid_response",
      requestId: "synthetic-proxy",
    });
  });

  it("acepta 202 y envia el identificador, CSRF y AbortSignal al endpoint de cola", async () => {
    const accepted = {
      status: "queued",
      conversation_id: "A",
      response: null,
      capacity: {
        active: 50,
        limit: 50,
        queued: 1,
        queue_limit: 100,
        utilization_pct: 100,
        overloaded: true,
      },
      notice: null,
      poll_after_ms: 2000,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(accepted), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("synthetic-csrf");
    const controller = new AbortController();
    await expect(
      api.submitChat("Consulta", "A", "client-request-123", controller.signal),
    ).resolves.toEqual(accepted);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat/submit",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        signal: expect.any(AbortSignal),
        headers: { "Content-Type": "application/json", "X-CSRF-Token": "synthetic-csrf" },
        body: JSON.stringify({
          message: "Consulta",
          conversation_id: "A",
          client_request_id: "client-request-123",
        }),
      }),
    );
  });

  it("libera una consulta de estado que no responde sin cancelar la operación del servidor", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = expect(api.chatStatus("request-uncertain")).rejects.toMatchObject({
      name: "ApiError",
      code: "request_timeout",
      status: 408,
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await result;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/chat/requests/request-uncertain");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propaga la cancelación del consumidor y limpia el temporizador", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );
    const controller = new AbortController();
    const result = expect(
      api.chatStatus("cancelled-read", controller.signal),
    ).rejects.toMatchObject({
      name: "AbortError",
    });
    controller.abort();
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aplica el límite también cuando llegan cabeceras pero el cuerpo queda pendiente", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, options: RequestInit) => ({
        status: 200,
        ok: true,
        headers: new Headers({ "X-Request-ID": "body-timeout" }),
        text: () =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      })),
    );
    const result = expect(api.chatStatus("slow-body")).rejects.toMatchObject({
      code: "request_timeout",
      requestId: "body-timeout",
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propaga un rechazo de capacidad como error y no como aceptacion", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "chat_capacity_full",
            message: "La solicitud no fue aceptada.",
          }),
          { status: 503, headers: { "X-Request-ID": "trace-123" } },
        ),
      ),
    );
    await expect(api.submitChat("Consulta", "A", "client-request-123")).rejects.toMatchObject({
      name: "ApiError",
      code: "chat_capacity_full",
      status: 503,
      requestId: "trace-123",
    } satisfies Partial<ApiError>);
  });
});
