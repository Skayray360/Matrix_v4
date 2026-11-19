/* Creado por Aldo Garcia. */
/** Navegador real con API sintetica: no certifica el IdP ni la inferencia. */
import { test, expect, type Page } from "@playwright/test";
import type { Me } from "../../src/services/contracts";

const profile = {
  user_id: "ui",
  username: "usuario-ui",
  display_name: "MatrixR1",
  auth_source: "local_test",
  roles: ["prestaciones_reader_test"],
  permissions: [],
  allowed_categories: ["prestaciones"],
  category_wildcard: false,
  csrf_token: "csrf-sintetico-ui",
};
const conversation = {
  id: "conversacion-ui",
  title: "Prueba de interfaz",
  created_at: "2026-09-30T12:00:00Z",
  updated_at: "2026-09-30T12:00:00Z",
};
const attachment = {
  id: "adjunto-ui",
  filename: "manual.txt",
  status: "indexed",
  chunk_count: 1,
  scope: "conversation",
  category: null,
  error_message: null,
};
const reply = {
  conversation_id: conversation.id,
  message_id: "respuesta-ui",
  answer: "Esta es una respuesta sintética para validar la interfaz. [[fuente-ui]]",
  sources: [
    {
      source_id: "fuente-ui",
      category: "prestaciones",
      filename: "manual-ui.txt",
      section: "Ejemplo visual",
      page_or_sheet: "",
      score: 0.9,
      label: "Manual de prueba",
      scope: "corporate",
    },
  ],
  intent: "documental",
  grounded: true,
  latency_ms: 240,
};

async function mockApi(page: Page, queued = false, general = false, session: Me = profile) {
  const response = general ? { ...reply, intent: "general", grounded: false, sources: [] } : reply;
  let authenticated = false;
  let completed = false;
  let cancelled = false;
  let attached = false;
  const errors: string[] = [];
  const unexpected: string[] = [];
  const mutations: { path: string; csrf: string | undefined; body: string | null }[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).hostname !== "127.0.0.1") unexpected.push(request.url());
  });
  const status = () => ({
    status: cancelled ? "cancelled" : completed ? "completed" : "queued",
    conversation_id: conversation.id,
    response: completed ? response : null,
    capacity: {
      active: completed ? 1 : 9,
      limit: 10,
      queued: completed ? 0 : 1,
      queue_limit: 50,
      utilization_pct: completed ? 10 : 90,
      overloaded: !completed,
    },
    notice: null,
    poll_after_ms: 2000,
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method();
    if (method !== "GET")
      mutations.push({ path, csrf: request.headers()["x-csrf-token"], body: request.postData() });
    const send = (json: unknown, code = 200) =>
      route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(json) });
    if (path === "/me")
      return authenticated
        ? send(session)
        : send({ code: "unauthenticated", message: "Inicie sesión" }, 401);
    if (path === "/auth/local/login") {
      authenticated = true;
      return send(session);
    }
    if (path === "/auth/logout") {
      authenticated = false;
      return send({ ok: true });
    }
    if (path === "/conversations") return send(method === "POST" ? conversation : [conversation]);
    if (path === `/conversations/${conversation.id}/attachments`) {
      attached = true;
      return send({ documents: [attachment] });
    }
    if (path === `/conversations/${conversation.id}`)
      return send({
        ...conversation,
        attachments: attached ? [attachment] : [],
        messages: completed
          ? [
              {
                id: response.message_id,
                role: "assistant",
                content: response.answer,
                intent: response.intent,
                model: null,
                created_at: conversation.created_at,
                sources: response.sources,
              },
            ]
          : [],
      });
    if (path === "/chat/submit") {
      completed = !queued;
      return send(status(), 202);
    }
    if (/^\/chat\/requests\/[^/]+\/cancel$/.test(path)) {
      cancelled = true;
      return send({ ok: true });
    }
    if (/^\/chat\/requests\/[^/]+$/.test(path)) return send(status());
    unexpected.push(`${method} ${path}`);
    return send({ message: "Ruta no prevista por la prueba" }, 501);
  });
  return {
    complete: () => {
      completed = true;
    },
    errors,
    unexpected,
    mutations,
  };
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Usuario", { exact: true }).fill("usuario-ui");
  await page.getByLabel("Contraseña", { exact: true }).fill("dato-sintetico");
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page.getByTestId("identity-user")).toHaveText("MatrixR1");
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await expect
    .poll(() =>
      page.evaluate(() =>
        Array.from(document.images).every((img) => img.complete && img.naturalWidth > 0),
      ),
    )
    .toBe(true);
  expect(
    await page.locator(".vite-error-overlay, #webpack-dev-server-client-overlay").count(),
  ).toBe(0);
}

/** Aplica la politica de produccion al servidor Vite de pruebas y comprueba CSSOM. */
async function strictStylePolicy(page: Page) {
  await page.addInitScript(() => {
    const violations: string[] = [];
    Object.assign(window, { matrixCspViolations: violations });
    document.addEventListener("securitypolicyviolation", (event) =>
      violations.push(event.effectiveDirective),
    );
  });
  await page.route("**/*", async (route) => {
    if (route.request().resourceType() !== "document") return route.fallback();
    const response = await route.fetch();
    await route.fulfill({
      response,
      headers: {
        ...response.headers(),
        "content-security-policy":
          "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
          "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; " +
          "form-action 'self'; object-src 'none'",
      },
    });
  });
}

test("CSP estricta mantiene TracePanel y autosize de Composer", async ({ page }) => {
  await strictStylePolicy(page);
  const api = await mockApi(page);
  const documentResponse = page.waitForResponse(
    (response) => response.request().resourceType() === "document",
  );
  await login(page);
  expect((await documentResponse).headers()["content-security-policy"]).toContain(
    "style-src 'self';",
  );
  const composer = page.getByTestId("composer-input");
  await composer.fill(Array.from({ length: 15 }, () => "Linea sintetica").join("\n"));
  await expect(composer).toHaveCSS("height", "180px");
  await composer.fill("Consulta sintetica de prestaciones");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("message-assistant")).toContainText("respuesta sintética");
  await page.getByRole("button", { name: "Mostrar u ocultar trazabilidad" }).click();
  await expect(page.locator(".inspector")).toBeVisible();
  const fraction = await page.locator(".evidence-score .fill").evaluate((element) => {
    return (
      element.getBoundingClientRect().width / element.parentElement!.getBoundingClientRect().width
    );
  });
  expect(fraction).toBeCloseTo(0.9, 2);
  expect(await page.evaluate(() => Reflect.get(window, "matrixCspViolations"))).toEqual([]);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("CSP estricta bloquea un atributo style inyectado", async ({ page }) => {
  await strictStylePolicy(page);
  await mockApi(page);
  await login(page);
  await page.evaluate(() => {
    const injected = document.createElement("div");
    injected.id = "csp-inline-style-probe";
    injected.setAttribute("style", "background-color: rgb(1, 2, 3)");
    document.body.append(injected);
  });
  await expect(page.locator("#csp-inline-style-probe")).not.toHaveCSS(
    "background-color",
    "rgb(1, 2, 3)",
  );
  await expect
    .poll(async () => {
      const directives = await page.evaluate(
        () => Reflect.get(window, "matrixCspViolations") as string[],
      );
      return directives.some((directive) => directive.startsWith("style-src"));
    })
    .toBe(true);
});

test("un perfil malformado se rechaza antes de renderizar y permite reintentar", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 667 });
  let profileReads = 0;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = path.endsWith("/me")
      ? { ...profile, allowed_categories: ++profileReads === 1 ? null : profile.allowed_categories }
      : [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(json),
    });
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("No se pudo verificar su sesión");
  await expect(page.locator(".recovery-card")).not.toContainText("TypeError");
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("recovery-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Reintentar conexión" }).click();
  await expect(
    page.getByRole("heading", { name: "Consulta conocimiento interno con contexto" }),
  ).toBeVisible();
  expect(profileReads).toBe(2);
  expect(errors).toEqual([]);
});

for (const { viewport, colorScheme } of [
  { viewport: { width: 1440, height: 900 }, colorScheme: "light" },
  { viewport: { width: 390, height: 844 }, colorScheme: "light" },
  { viewport: { width: 320, height: 667 }, colorScheme: "light" },
  { viewport: { width: 1440, height: 900 }, colorScheme: "dark" },
] as const) {
  test(`login y chat ${colorScheme} sin recortes a ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme });
    const api = await mockApi(page);
    await page.goto("/");
    await expect(
      page.getByRole("link", { name: "Continuar con acceso corporativo" }),
    ).toHaveAttribute("href", "/api/v1/auth/login");
    await expect(page.locator(".login-intro")).toHaveCSS("background-image", /login-bg/);
    await noOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`login-${viewport.width}.png`),
      fullPage: true,
    });
    await login(page);
    await expect(
      page.getByRole("heading", { name: "Consulta conocimiento interno con contexto" }),
    ).toBeVisible();
    await expect(page.getByRole("img", { name: "Asistente IA Matrix" })).toBeVisible();
    expect(
      await page.locator(".welcome-mascot").evaluate((img) => {
        const a = img.getBoundingClientRect();
        const b = img.closest(".messages")!.getBoundingClientRect();
        return a.top >= b.top && a.bottom <= b.bottom && a.left >= b.left && a.right <= b.right;
      }),
    ).toBe(true);
    await expect(page.getByTestId("identity-scope")).toHaveText("1 categoria(s)");
    await noOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`chat-${viewport.width}.png`),
      fullPage: true,
    });
    expect(api.errors).toEqual([]);
    expect(api.unexpected).toEqual([]);
  });
}

test("consulta, fuentes, trazabilidad y cierre de sesión con CSRF", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const api = await mockApi(page);
  await login(page);
  await page.getByTestId("composer-input").fill("Consulta sintética de prestaciones");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("message-assistant")).toContainText("respuesta sintética");
  await expect(page.getByTestId("sources")).toContainText("Manual de prueba");
  await page.getByRole("link", { name: "Ver fuente 1" }).click();
  await expect(page.getByTestId("sources").locator("li")).toBeFocused();
  await expect(page.getByTestId("capacity-notice")).toHaveCount(0);
  await page.getByRole("button", { name: "Mostrar u ocultar trazabilidad" }).click();
  await expect(page.locator(".inspector")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("chat-response.png"), fullPage: true });
  await page.getByRole("button", { name: "Salir", exact: true }).click();
  await expect(page.getByRole("button", { name: "Entrar", exact: true })).toBeVisible();
  expect(
    api.mutations
      .filter((m) => m.path !== "/auth/local/login")
      .every((m) => m.csrf === profile.csrf_token),
  ).toBe(true);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("acceso y consulta con teclado conservan el salto de línea y las fuentes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Entrar", exact: true })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Continuar con acceso corporativo" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Usuario", { exact: true })).toBeFocused();
  await page.keyboard.type("usuario-ui");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Mostrar", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Contraseña", { exact: true })).toBeFocused();
  await page.keyboard.type("dato-sintetico");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("identity-user")).toHaveText("MatrixR1");

  // Llega al compositor mediante el orden real de tabulación, sin focus() ni click().
  const composer = page.getByTestId("composer-input");
  for (let step = 0; step < 30; step += 1) {
    if (await composer.evaluate((node) => node === document.activeElement)) break;
    await page.keyboard.press("Tab");
    expect(
      await page.locator(".sidebar").evaluate((node) => node.contains(document.activeElement)),
    ).toBe(false);
  }
  await expect(composer).toBeFocused();
  await page.keyboard.type("Primera línea");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("Segunda línea");
  await expect(composer).toHaveValue("Primera línea\nSegunda línea");
  expect(api.mutations.filter((mutation) => mutation.path === "/chat/submit")).toHaveLength(0);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("message-assistant")).toContainText("respuesta sintética");
  await expect(page.getByTestId("sources")).toContainText("Manual de prueba");
  expect(api.mutations.filter((mutation) => mutation.path === "/chat/submit")).toHaveLength(1);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("aviso general visible y persistente en móvil", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 667 });
  const api = await mockApi(page, false, true);
  await login(page);
  await page.getByTestId("composer-input").fill("Pregunta de conocimiento general");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("general-notice")).toHaveText(
    "Respuesta general: no basada en documentación de la empresa",
  );
  await expect(page.getByTestId("sources")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("general-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Conversaciones", exact: true }).click();
  await page.getByRole("button", { name: /^Prueba de interfaz/ }).click();
  await expect(page.getByTestId("general-notice")).toBeVisible();
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("aviso al 90 por ciento y recuperación de respuesta en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await mockApi(page, true);
  await login(page);
  await page.getByTestId("composer-input").fill("Pregunta en cola");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("capacity-notice")).toBeVisible();
  await expect(page.getByTestId("send-button")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancelar solicitud" })).toBeVisible();
  await noOverflow(page);
  api.complete();
  await expect(page.getByTestId("message-assistant")).toContainText("respuesta sintética");
  await expect(page.getByTestId("capacity-notice")).toHaveCount(0);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("cancelación conserva el borrador", async ({ page }) => {
  const api = await mockApi(page, true);
  await login(page);
  await page.getByTestId("composer-input").fill("Borrador que se conserva");
  await page.getByTestId("send-button").click();
  await page.getByRole("button", { name: "Cancelar solicitud" }).click();
  await expect(page.getByTestId("error-banner")).toContainText("Solicitud cancelada");
  await expect(page.getByTestId("composer-input")).toHaveValue("Borrador que se conserva");
  expect(api.mutations.find((m) => m.path.endsWith("/cancel"))?.csrf).toBe(profile.csrf_token);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("menú móvil, tema oscuro y adjunto privado", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await mockApi(page);
  await login(page);
  await page.getByRole("button", { name: "Conversaciones", exact: true }).click();
  await expect(page.locator(".sidebar")).toHaveAttribute("data-open", "true");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.locator(".sidebar-close").click();
  await expect(page.locator(".sidebar")).toHaveAttribute("data-open", "false");
  await expect(page.locator(".sidebar")).toBeHidden();
  // El menu cerrado tampoco debe capturar el foco fuera de la pantalla.
  await page.getByRole("button", { name: "Conversaciones", exact: true }).focus();
  await page.keyboard.press("Shift+Tab");
  expect(
    await page.locator(".sidebar").evaluate((node) => node.contains(document.activeElement)),
  ).toBe(false);
  for (let step = 0; step < 30; step += 1) {
    await page.keyboard.press("Tab");
    expect(
      await page.locator(".sidebar").evaluate((node) => node.contains(document.activeElement)),
    ).toBe(false);
  }
  await page.getByTestId("theme-toggle").click();
  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByLabel("Adjuntar archivos a la conversacion", { exact: true }).setInputFiles({
    name: "manual.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Documento sintético para la prueba visual."),
  });
  await expect(page.getByTestId("attachments")).toContainText("manual.txt");
  await expect(page.getByTestId("attachments")).toContainText("Listo para consultar");
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("chat-dark-mobile.png"), fullPage: true });
  expect(
    api.mutations
      .filter((m) => m.path !== "/auth/local/login")
      .every((m) => m.csrf === profile.csrf_token),
  ).toBe(true);
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`acceso ${colorScheme}: contraseña alcanzable con teclado y objetivo táctil`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 320, height: 667 });
    await page.emulateMedia({ colorScheme });
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Inicie sesión", exact: true })).toBeVisible();

    const password = page.getByLabel("Contraseña", { exact: true });
    await password.fill("dato-sintetico");
    await page.keyboard.press("Shift+Tab");
    const toggle = page.getByRole("button", { name: "Mostrar", exact: true });
    await expect(toggle).toBeFocused();
    await expect(toggle).not.toHaveCSS("outline-style", "none");
    const target = await toggle.boundingBox();
    expect(target).not.toBeNull();
    expect(target!.width).toBeGreaterThanOrEqual(44);
    expect(target!.height).toBeGreaterThanOrEqual(44);

    await page.keyboard.press("Space");
    await expect(password).toHaveAttribute("type", "text");
    await expect(password).toHaveValue("dato-sintetico");
    await expect(page.getByRole("button", { name: "Ocultar", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.keyboard.press("Space");
    await expect(password).toHaveAttribute("type", "password");
    await expect(password).toHaveValue("dato-sintetico");
    expect(api.mutations).toEqual([]);
    await noOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`login-${colorScheme}.png`),
      fullPage: true,
    });
    expect(api.errors).toEqual([]);
    expect(api.unexpected).toEqual([]);
  });
}

test("texto al 200 por ciento conserva acceso, consulta y fuentes en móvil", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await mockApi(page, false, false, { ...profile, auth_source: "local" });
  await page.goto("/");
  // Emula una preferencia de texto ampliado, no zoom de página ni una pantalla real.
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "200%";
  });
  await expect(page.locator("body")).toHaveCSS("font-size", "32px");
  await page.getByLabel("Usuario", { exact: true }).fill("usuario-ui");
  await page.getByLabel("Contraseña", { exact: true }).fill("dato-sintetico");
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("login-large-text.png"), fullPage: true });
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page.getByTestId("identity-user")).toHaveText("MatrixR1");
  const changePassword = page.getByRole("button", { name: "Cambiar contraseña", exact: true });
  const logout = page.getByRole("button", { name: "Salir", exact: true });
  await expect(changePassword).toBeInViewport({ ratio: 1 });
  await expect(logout).toBeInViewport({ ratio: 1 });
  for (let step = 0; step < 30; step += 1) {
    if (await changePassword.evaluate((node) => node === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  await expect(changePassword).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Cambiar contraseña" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Cambiar contraseña" })).toBeHidden();
  await expect(changePassword).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(logout).toBeFocused();
  await expect(logout).toBeInViewport({ ratio: 1 });
  const composer = page.getByTestId("composer-input");
  await expect(composer).toBeInViewport();
  await composer.fill("Consulta sintética con texto ampliado");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("message-assistant")).toContainText("respuesta sintética");
  await page.getByRole("link", { name: "Ver fuente 1" }).click();
  await expect(page.getByTestId("sources").locator("li")).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("chat-large-text.png"), fullPage: true });
  await expect(page.getByTestId("sources").locator("li")).toBeInViewport({ ratio: 1 });
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

test("colores forzados y movimiento reducido conservan foco, menú y consulta", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  const api = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Entrar", exact: true })).toBeVisible();
  await page.keyboard.press("Tab");
  const corporateAccess = page.getByRole("link", { name: "Continuar con acceso corporativo" });
  await expect(corporateAccess).toBeFocused();
  await expect(corporateAccess).not.toHaveCSS("outline-style", "none");
  await expect(corporateAccess).not.toHaveCSS("outline-color", "rgba(0, 0, 0, 0)");
  await login(page);
  await page.getByRole("button", { name: "Conversaciones", exact: true }).click();
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.locator(".sidebar-close").click();
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.getByTestId("composer-input").fill("Consulta sintética accesible");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("sources")).toContainText("Manual de prueba");
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("chat-forced-colors.png"), fullPage: true });
  expect(api.errors).toEqual([]);
  expect(api.unexpected).toEqual([]);
});
