/* Creado por Aldo Garcia. */
/** Ataques HTTP con navegador y cuentas sinteticas; no requiere inferencia. */
import { expect, test } from "@playwright/test";

import { ADMIN_USER, RESTRICTED_USER, TEST_PASSWORD, login, logout } from "./helpers";

test("sesiones, permisos y fronteras HTTP resisten ataques y conservan el uso normal", async ({
  page,
  browser,
}) => {
  await login(page, ADMIN_USER);
  const session = (await page.context().cookies()).find(
    (cookie) => cookie.name === "matrixrh_session",
  );
  expect(session?.httpOnly).toBe(true);
  const me = await (await page.request.get("/api/v1/me")).json();
  const csrf = { "X-CSRF-Token": me.csrf_token as string };

  await test.step("CSRF y login desde otro origen se bloquean", async () => {
    const missing = await page.request.post("/api/v1/conversations", {
      data: { title: "sin CSRF" },
    });
    expect(missing.status()).toBe(403);
    const forged = await page.request.post("/api/v1/auth/local/login", {
      headers: { Origin: "https://attacker.invalid", "Content-Type": "application/json" },
      data: { username: RESTRICTED_USER, password: TEST_PASSWORD },
    });
    expect(forged.status()).toBe(403);
    expect((await (await page.request.get("/api/v1/me")).json()).username).toBe(ADMIN_USER);
  });

  const created = await page.request.post("/api/v1/conversations", {
    headers: csrf,
    data: { title: "<img src=x onerror=window.__matrixAttack=1>" },
  });
  expect(created.status()).toBe(200);
  const conversation = (await created.json()).id as string;
  await page.reload();
  await expect(page.getByTestId("identity-user")).toBeVisible();
  await expect(
    page.getByText("<img src=x onerror=window.__matrixAttack=1>", { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => Reflect.get(window, "__matrixAttack"))).toBeUndefined();
  expect(await page.locator("img[onerror]").count()).toBe(0);

  const restricted = await browser.newContext({ baseURL: new URL(page.url()).origin });
  try {
    const other = await restricted.newPage();
    await login(other, RESTRICTED_USER);
    const otherMe = await (await other.request.get("/api/v1/me")).json();
    await test.step("IDOR y elevacion de rol no revelan conversaciones ajenas", async () => {
      expect((await other.request.get(`/api/v1/conversations/${conversation}`)).status()).toBe(404);
      expect(
        (
          await other.request.delete(`/api/v1/conversations/${conversation}`, {
            headers: { "X-CSRF-Token": otherMe.csrf_token as string },
          })
        ).status(),
      ).toBe(404);
      expect((await other.request.get("/api/v1/admin/diagnostics")).status()).toBe(403);
      const forged = await other.request.get("/api/v1/me", {
        headers: { "X-Roles": "matrix_admin_test", "X-User-Id": me.user_id as string },
      });
      expect((await forged.json()).username).toBe(RESTRICTED_USER);
    });
    await logout(other);
  } finally {
    await restricted.close();
  }

  await test.step("Host falso y cuerpo excesivo se rechazan sin danar la sesion", async () => {
    expect(
      (await page.request.get("/health", { headers: { Host: "attacker.invalid" } })).status(),
    ).toBe(400);
    const oversized = await page.request.post("/api/v1/conversations", {
      headers: csrf,
      data: { title: "x".repeat(100_000) },
    });
    expect(oversized.status()).toBe(413);
    expect(oversized.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect((await page.request.get(`/api/v1/conversations/${conversation}`)).status()).toBe(200);
  });

  expect(
    (
      await page.request.delete(`/api/v1/conversations/${conversation}`, { headers: csrf })
    ).status(),
  ).toBe(200);
  await logout(page);
  expect((await page.request.get("/api/v1/me")).status()).toBe(401);
});
