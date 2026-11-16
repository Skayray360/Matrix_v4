<!-- Creado por Aldo Garcia. -->
# Contraste y remediación de la auditoría E2E recibida

Base revisada: `6fde263984c640831b9e025c8c7c77b36045381a` (Matrix RH 1.4.2). Fecha de trabajo: 15 de noviembre de 2026, America/Monterrey.

El usuario aportó un informe de siete hallazgos y resultados de 177 casos HTTP y 16 escenarios de Chromium. Esas cifras se registran como evidencia recibida: no se dispone aquí de sus scripts ni registros para atribuirlas a una reproducción propia. La ausencia de hallazgos críticos/altos se limita a lo probado por ese informe; no demuestra ausencia general de vulnerabilidades.

## Propósito e invariantes

Matrix RH debe responder sobre conocimiento autorizado y vigente mediante inferencia/embeddings locales, preservar aislamiento entre usuarios y conversaciones, y permitir operar cuentas e instalación Windows/Docker sin editar SQL manualmente. Se mantienen framework, modelos, geometría vectorial, políticas ACL, CSRF, límites de extracción, journal de generaciones y recuperación ante rollback. No se prueba ni altera la instalación productiva.

Alcance: correcciones acotadas del código y pruebas sintéticas autorizadas. No hay cambios de modelo, corpus real, privilegios de sistema, BitLocker, despliegue ni pruebas de carga. Los cambios de respuesta pública de readiness y los errores de entrada conservan los estados HTTP y consumidores operativos necesarios; los nuevos endpoints administrativos exigen permisos y CSRF.

## Trazabilidad del trabajo

| ID | Evidencia propia inicial | Corrección y prueba de aceptación | Estado |
|---|---|---|---|
| M1 | `scheduler._loop` programa el mantenimiento completo desde el uptime y `run_sync_once` usa `new_only=True`; ese modo excluye hashes y bajas deliberadamente. | Checkpoint SQL persistente sin nueva migración; sincronización periódica de altas/cambios/bajas con inventario completo, límites y locks. Probar reinicios, fuentes incompletas, lotes y preservación de generaciones. | Implementado; regresión local verificada |
| M2 | `LocalIdentityProvider.authenticate` comprueba el bloqueo antes de verificar la contraseña y responde 429 solo para usuarios existentes. | Fallos de autenticación genéricos, retraso progresivo acotado de intentos fallidos y límites HTTP conservados; probar que intentos ajenos no impidan una contraseña correcta. | Implementado; regresión local verificada |
| M3 | Reproducción ASGI: primera respuesta enviada mientras termina la aplicación; la siguiente recibe 503 `ollama_unavailable` con request_id nulo. | Espera breve y acotada de recepción sin liberar prematuramente el trabajo; error de capacidad de cargas propio y trazable. Probar secuencia, saturación real, desconexión y excepciones. | Implementado; regresión local verificada |
| B1 | `/ready` publica los detalles de `ReadyResponse`; los supervisores existentes usan solo el estado HTTP. | Resumen público; detalle protegido por `diagnostics.read`; desactivar cabecera Uvicorn en el launcher soportado. Probar filtrado después de caché privilegiada y denegación antes de sondear. | Implementado; regresión local verificada |
| B2 | Un TXT de 20.000 bytes NUL alcanza `prepare_attachment` y produce `ChunkBudgetExceededError` sin traducción HTTP. | Rechazar controles binarios preservando codificaciones textuales soportadas y traducir límites de fragmentación a 422 antes de publicar. | Implementado; regresión local verificada |
| B3 | La superficie administrativa permite crear/listar cuentas; no ofrece cambiar rol o estado. | API y CLI de gestión local, revocación de sesiones y protección del último administrador activo con bloqueo transaccional. Probar autorización, revocación y carrera en MySQL. | Implementado; regresión local verificada |
| B4 | La causa de la observación aislada no está reproducida. El planificador conserva solo el tipo de excepción y reintenta en otra pasada. | Mejorar diagnóstico sanitizado y recuperación acotada sin afirmar una causa inexistente. | Parcial: causa no verificada |

## Validación y límites

Resultado local sobre el conjunto integrado: 4.049 casos recopilados, 3.870 seleccionados en el grupo standalone y 179 de integración excluidos de esa ejecución. De los 3.870 casos únicos de JUnit, 3.860 pasan y 10 se omiten únicamente por ausencia de PowerShell; cero fallos y cero errores. El quality gate local queda **INCOMPLETE**, no PASS. El total agregado de pytest incorpora subpruebas y no debe confundirse con casos únicos.

Ruff, mypy (116 módulos de aplicación), Bandit y el preflight de integridad pasan. El manifiesto contiene 209 archivos operativos. Las pruebas dirigidas y globales se solapan: no se suman sus cifras como casos distintos. Se añaden 112 casos standalone y 10 de integración respecto de la base.

CI incorpora siete casos con MySQL para autenticación/ciclo de vida y tres para vigencia/checkpoints del planificador, además de las diez pruebas previas de recuperación vectorial en dos modos (servidor y embebido). La publicación inicial de este informe precede esa ejecución: su resultado y enlace quedan en el PR. La matriz usa MySQL 8.4 y servicios sintéticos; exige cero omisiones y concordancia entre colección y JUnit. Aun si esas suites dirigidas pasan, quedan 159 casos de integración fuera de esa matriz. Los controles existentes de frontend y Windows también deben pasar antes de integrar.

Los controles con modelos deterministas verifican flujos y aislamiento, no exactitud de respuestas ni resistencia semántica de un LLM real. No se han repetido los 177 casos HTTP y 16 escenarios Chromium del informe recibido; se incorporaron regresiones reproducibles para los defectos corregidos. Estado de producción: **NO EVALUADO**.

Límites que permanecen:

- M1: el inventario y los hashes recorren el corpus; su coste con el corpus real no se ha medido. Si un documento modificado no logra reindexarse, se conserva la generación confirmada anterior y se registra/reintenta el fallo. Las fuentes incompletas o inaccesibles se preservan para evitar borrados masivos accidentales.
- M2: el retraso de respuestas fallidas está acotado a dos segundos y no retiene una transacción SQL; consume un trabajador durante ese intervalo. El límite por IP sigue compartiéndose detrás de NAT y no equivale a protección completa frente a denegación de servicio.
- M3: una saturación real superior a la espera de 250 ms sigue respondiendo 503, ahora con `upload_capacity_full`, `Retry-After` e identificador de solicitud. El cupo se libera cuando termina realmente el trabajo.
- B1: `/ready` devuelve solo `ready`; `/ready/details` exige `diagnostics.read`. El launcher soportado desactiva la cabecera Uvicorn; una ejecución manual tiene su propia configuración. `/health` mantiene su contrato y Nginx puede identificarse sin versión.
- B4: se mejoró el diagnóstico sanitizado y el reintento; no se reprodujo ni se atribuye una causa al fallo aislado de arranque.
- No se certifican instalación completa en Windows/WampServer, despliegue Docker productivo, IdP real, SQL externo, carga sostenida, corpus real ni calidad de Ollama.

La reversión del código se realiza mediante revert del commit de remediación. No se añade migración SQL: los checkpoints usan registros existentes. Las futuras acciones explícitas de administración de usuarios tendrán efectos sobre cuentas/sesiones y no se revierten mediante Git; no se ejecutan aquí contra cuentas reales.

Fuentes primarias: [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) y [opciones HTTP de Uvicorn](https://uvicorn.dev/settings/).

Creado por Aldo Garcia.
