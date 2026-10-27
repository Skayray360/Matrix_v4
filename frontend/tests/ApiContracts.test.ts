/* Creado por Aldo Garcia. */
/** Comprueba el limite HTTP real: un JSON cualquiera no confirma una operacion. */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  getCsrfToken,
  setCsrfToken,
  type ConversationSummary,
  type DocumentStatus,
  type Me,
} from "../src/services/api";

const profile: Me = {
  user_id: "synthetic-user",
  username: "synthetic",
  display_name: "Persona sintética",
  auth_source: "local",
  roles: ["synthetic-reader"],
  permissions: [],
  allowed_categories: ["prestaciones"],
  category_wildcard: false,
  csrf_token: "synthetic-new-csrf",
};
const conversation: ConversationSummary = {
  id: "synthetic-conversation",
  title: "Consulta sintética",
  created_at: "2026-10-27T10:00:00Z",
  updated_at: "2026-10-27T10:00:00Z",
};
const document: DocumentStatus = {
  id: "synthetic-document",
  filename: "sintetico.txt",
  status: "indexed",
  chunk_count: 1,
  scope: "conversation",
  category: null,
  error_message: null,
};
const endpoints = [
  { name: "perfil", call: () => api.me(), valid: profile },
  { name: "login", call: () => api.localLogin("synthetic", "synthetic-secret"), valid: profile },
  {
    name: "cambio de contraseña",
    call: () => api.changePassword("synthetic-old", "synthetic-new"),
    valid: profile,
  },
  { name: "logout", call: () => api.logout(), valid: { ok: true } },
  { name: "listado", call: () => api.listConversations(), valid: [conversation] },
  { name: "creación", call: () => api.createConversation(), valid: conversation },
  { name: "borrado", call: () => api.deleteConversation(conversation.id), valid: { ok: true } },
  {
    name: "adjuntos",
    call: () => api.uploadAttachments(conversation.id, [new File(["sintético"], "sintetico.txt")]),
    valid: { documents: [document] },
  },
  { name: "estado documental", call: () => api.documentStatus(document.id), valid: document },
];

function respond(payload: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(payload), {
          headers: { "X-Request-ID": "synthetic-contract" },
        }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken("");
});

describe("contratos completos de los endpoints", () => {
  it.each(endpoints)("acepta el contrato vigente de $name", async ({ call, valid, name }) => {
    respond(valid);
    await expect(call()).resolves.toEqual(name === "logout" ? undefined : valid);
  });

  it.each(endpoints)("rechaza JSON incompleto en $name", async ({ call }) => {
    for (const payload of [true, "ok", {}, null]) {
      respond(payload);
      await expect(call()).rejects.toMatchObject({
        code: "invalid_response",
        status: 200,
        requestId: "synthetic-contract",
      });
    }
  });

  it.each(endpoints)("no confunde 204 con una confirmación válida de $name", async ({ call }) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(call()).rejects.toMatchObject({ code: "invalid_response", status: 204 });
  });

  it.each([
    { roles: "admin" },
    { permissions: [{}] },
    { allowed_categories: null },
    { category_wildcard: "true" },
    { display_name: {} },
    { csrf_token: "" },
    { csrf_token: {} },
  ])("no reemplaza CSRF ante un perfil malformado: %j", async (mutation) => {
    setCsrfToken("synthetic-current-csrf");
    respond({ ...profile, ...mutation });
    await expect(api.me()).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.localLogin("synthetic", "synthetic-secret")).rejects.toMatchObject({
      code: "invalid_response",
    });
    await expect(api.changePassword("synthetic-old", "synthetic-new")).rejects.toMatchObject({
      code: "invalid_response",
    });
    expect(getCsrfToken()).toBe("synthetic-current-csrf");
  });

  it.each([{ ok: false }, { ok: "true" }, []])(
    "mantiene la sesión local cuando logout no confirma la revocación: %j",
    async (payload) => {
      setCsrfToken("synthetic-current-csrf");
      respond(payload);
      await expect(api.logout()).rejects.toMatchObject({ code: "invalid_response" });
      expect(getCsrfToken()).toBe("synthetic-current-csrf");
      await expect(api.deleteConversation(conversation.id)).rejects.toMatchObject({
        code: "invalid_response",
      });
    },
  );

  it("conserva perfiles sin nombre de presentación, admitidos por el servidor", async () => {
    const unnamed = { ...profile, display_name: "" };
    respond(unnamed);
    await expect(api.me()).resolves.toEqual(unnamed);
  });

  it("rechaza una conversación incompleta dentro de la lista", async () => {
    respond([conversation, { ...conversation, title: {} }]);
    await expect(api.listConversations()).rejects.toMatchObject({ code: "invalid_response" });
    respond([]);
    await expect(api.listConversations()).resolves.toEqual([]);
  });

  it.each([
    { chunk_count: -1 },
    { chunk_count: 1.5 },
    { filename: [] },
    { error_message: {} },
    { scope: null },
    { id: "" },
  ])("rechaza metadatos documentales malformados: %j", async (mutation) => {
    respond({ documents: [document, { ...document, ...mutation }] });
    await expect(api.uploadAttachments(conversation.id, [])).rejects.toMatchObject({
      code: "invalid_response",
    });
    respond({ ...document, ...mutation });
    await expect(api.documentStatus(document.id)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it("rechaza el estado de un documento distinto al solicitado", async () => {
    respond({ ...document, id: "another-document" });
    await expect(api.documentStatus(document.id)).rejects.toMatchObject({
      code: "invalid_response",
      requestId: "synthetic-contract",
    });
  });
});
