<!-- Creado por Aldo Garcia. -->
# Seguimiento: servicios vectoriales y navegador

Fecha: 2026-10-27. Base de código: `9d03a1edc982fcb3bbe94a55a51cc8ee3048be0f`.

## Qué se añadió

La corrección vectorial necesitaba comprobar la semántica real de MySQL, además de sus regresiones SQLite. Se añadió una suite dirigida de diez casos y un job de CI con MySQL 8.4 y Qdrant 1.20.0 descartables. El job aplica las migraciones del proyecto y utiliza una cuenta limitada a su base de pruebas. La primera pasada exige Qdrant server; la segunda repite los mismos diez casos con almacenamiento embedded persistente. No usa documentos, modelos ni credenciales productivos.

Los casos comprueban escritor vivo y `NOWAIT`, documento nuevo/existente, commit/rollback, snapshot anterior bajo `REPEATABLE READ`, avance sobre otro documento libre, respuesta perdida después de un commit real y caída abrupta del proceso escritor. SQL, locks, journal e índice son reales; únicamente los embeddings son deterministas. Las pruebas no interpretan un timeout como ausencia del documento.

El job rechaza fallos, omisiones, colección vacía y discrepancias entre los casos recopilados y JUnit. Es una suite dirigida separada de los 182 casos de integración anteriores; no se presenta como aceptación de toda la plataforma. Los resultados del commit publicado se consultan en los [checks del PR #3](https://github.com/Skayray360/Matrix_v4/pull/3/checks), job **Recuperacion vectorial (MySQL y Qdrant reales)**.

Las imágenes se fijaron por digest, contrastado con los bytes del índice y la cabecera `Docker-Content-Digest` del registro oficial:

| Imagen | Digest OCI |
|---|---|
| `mysql:8.4` | `sha256:76f0358884cb92d9a086d24e5671c38048d3fef40f2588703d73eb55392d6b69` |
| `qdrant/qdrant:v1.20.0` | `sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2` |

## Navegador real

Se ejecutaron **10 pruebas Playwright aprobadas**, cero fallos, omisiones o reintentos, en 27,24 segundos sobre el build de la base indicada. Se comprobaron login/chat a 1440×900, 390×844 y 320×667, fuentes y foco de citas, trazabilidad, CSRF, cancelación, cola, adjuntos, tema oscuro y recuperación de perfil inválido. Se añadió un flujo con teclado desde login hasta consulta y una comprobación de tabulación del menú móvil cerrado. No se confirmó otro defecto de producto en estos escenarios.

El navegador fue Chrome Headless Shell 151.0.7922.34, la versión requerida por Playwright 1.62.1. El CDN de Playwright devolvía HTML en este entorno; la descarga oficial de Chrome for Testing funcionó. Se verificaron tamaño, CRC32C del distribuidor y CRC del ZIP. No se cambió el lock ni se distribuye el binario con Matrix. El job frontend instala el Chromium correspondiente a Playwright y ejecuta esta suite después del build.

El navegador es real, pero las respuestas API son sintéticas. La revisión incluyó capturas de móvil/escritorio y no detectó desbordamiento horizontal ni imágenes rotas en esos tamaños. No acredita backend, Entra/OIDC, lectores de pantalla ni accesibilidad completa. El contenido de mensajes conserva su desplazamiento vertical independiente.

## Límites y pendientes

- Los resultados nuevos complementan la evidencia histórica de [vectors-followup.md](vectors-followup.md); el estado de CI debe corresponder al SHA revisado.
- No se ejecutaron índices productivos, Windows/WAMP, restauración completa, modelos locales, evaluación con corpus empresarial, carga ni failover distribuido.
- La revisión independiente no encontró una ruta confirmada que borre la generación activa por un snapshot obsoleto. Identificó un límite de disponibilidad: la limpieza general de generaciones antiguas puede aplazar su ciclo ante una fila bloqueada; la recuperación individual del journal sí continúa con otros documentos. No se atribuye pérdida de datos a ese comportamiento.
- Las referencias internas y cachés históricas de GitHub continúan sujetas al procedimiento descrito en [history-cleanup.md](history-cleanup.md).

También se corrigió el comando del README para proporcionar el argumento obligatorio `--matrix-collection-report`.
