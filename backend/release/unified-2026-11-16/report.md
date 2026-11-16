<!-- Creado por Aldo Garcia. -->
# Contraste de la auditoría unificada de Matrix RH

Fecha: 16 de noviembre de 2026 (America/Monterrey). Base de esta revisión: `bcd074a3e3b7df5beb1b797a0ed105cd1ec86f5d`.

## Alcance y evidencia

El informe recibido evalúa `6fde263`, anterior al PR #14. Sus resultados con Ollama real, HTTPS, OIDC simulado, SQL externo y carga son evidencia aportada por el usuario: no se dispone de sus scripts, trazas ni respuestas por caso para atribuirlos a una reproducción propia. No permite presentar los trece hallazgos como trece defectos vigentes de main ni afirmar ausencia general de vulnerabilidades. No se han leído nuevamente las 77.000 líneas ni se certifica producción.

Se mantiene el propósito: consultar conocimiento empresarial autorizado y vigente, con inferencia/embeddings locales; orientación general claramente identificada, aislamiento de cuentas/conversaciones/adjuntos, API compatible y perfiles Windows y Docker. No se cambian modelos, geometría vectorial, framework, corpus real, políticas de acceso ni configuración de una instalación existente. Implementación y pruebas sintéticas en copia de desarrollo y CI; sin acceso a producción, estrés real ni descargas de modelos.

## Trazabilidad y decisión

| ID | Contraste actual | Decisión y prueba |
|---|---|---|
| M1 | Corregido en PR #14: inventario/hash y bajas, checkpoints SQL entre reinicios. | Conservar regresiones MySQL; persiste el límite de reindexación fallida que conserva la generación anterior. |
| M2 | Corregido en PR #14: sin bloqueo persistente, fallo genérico y demora acotada. | Conservar pruebas de credenciales correctas después de fallos y límites por IP. |
| M3 | Corregido en PR #14: espera 250 ms y error de capacidad trazable. | Conservar pruebas de secuencia/cancelación; saturación real sigue produciendo 503. |
| M4 | Confirmados fixture ausente del build, tmpfs YAML fragmentado y asignación dinámica en la red del proxy. | Corregir empaquetado/red, documentar preparación y añadir CI de construcción/arranque sintético. Revert del cambio; sin manipular datos existentes. |
| M5 | TimeoutError reportado sin traza: agotamiento del pool es hipótesis, no causa acreditada. | Revisar traducción de errores SQL y probar agotamiento controlado; no aumentar conexiones por conjetura. |
| M6 | No equivale automáticamente a alucinación: el contrato de evaluación exige negativa y ausencia de evidencias. Los cuatro outputs no fueron entregados. | Revisar fronteras por pregunta y diagnósticos del evaluador, preservar orientación general y exigir fuentes para datos empresariales. |
| B1 | Corregido en PR #14: resumen público, detalle autorizado y launcher sin Server de Uvicorn. | Conservar regresiones de privacidad. |
| B2 | Corregido en PR #14: controles binarios y límites de extracción devuelven 422. | Conservar regresiones de codificación/ingesta. |
| B3 | Corregido en PR #14: API/CLI de estado y rol con revocación y protección transaccional del último administrador. | Conservar pruebas de concurrencia MySQL. |
| B4 | Parcial: diagnóstico sanitizado y reintento; causa aislada no reproducida. | No atribuir la ausencia en un arranque MySQL 8.4 a una resolución demostrada. |
| B5 | La expiración es compatible con capacidad insuficiente; falta medir hardware objetivo. | Revisar admisión anticipada con estimación explícita y límites, sin prometer 150–250 usuarios. |
| B6 | Confirmado: Docker local usa development y habilita docs por entorno. | Desactivar docs en el modo Docker local sin fingir que satisface todos los requisitos de production. |
| B7 | Posible carrera: la creación hace flush y el commit queda para el cierre de la dependencia. | Reproducir visibilidad desde otra sesión al enviar la respuesta; confirmar antes de devolver éxito sin cambiar los 404 de aislamiento. |

## Límites de interpretación

- 14/22 contratos de producto no representan una medida de exactitud global. La rúbrica externa solo fue medible en siete casos; recuperar una fuente no prueba que respalde cada respuesta.
- Una prueba por inyección y un proveedor OIDC simulado no acreditan robustez multivuelta ni integración con Entra real.
- Doce usuarios sin GPU y con terminaciones por memoria no permiten dimensionar producción. Un límite de contenedor no reserva RAM física ni demuestra que los modelos caben junto con contexto y concurrencia.
- Root rechazado por el preparador es una medida deliberada. Una subred ocupada requiere escoger una red libre, no desactivar el aislamiento.
- El smoke Docker podrá validar aplicación e infraestructura con un doble local de Ollama; no acredita inferencia ni calidad con modelos reales.

## Validación y cierre

Se implementaron los cambios descritos a continuación; la publicación inicial precede el cierre de la suite integrada y la CI.

| Hallazgo | Estado del cambio | Evidencia / límite |
|---|---|---|
| M1–M3, B1–B3 | IMPLEMENTED previamente | PR #14; se conservan regresiones. |
| M4 | IMPLEMENTED; arranque pendiente de CI inicial | Fixture frontend copiado selectivamente; tmpfs bloque; backend IP fija; guía y smoke Docker. |
| M5 | PARTIAL | Agotamiento QueuePool reproducido con HTTP503/reintento/rollback. No se ha identificado la excepción original de los 18 errores externos. |
| M6 | PARTIAL | `unsup-01` exigirá evidencia empresarial; 17 regresiones nuevas. `route-01/05/07` ya tenían guardias en la base auditada. El evaluador distingue las causas sin rebajar su contrato. |
| B4 | PARTIAL previo | Diagnóstico mejorado; causa original no reproducida. |
| B5 | PARTIAL | Rechazo anticipado basado en p95 de ejecuciones completadas; sin historial y con variaciones de carga/modelo permanece incertidumbre. |
| B6 | IMPLEMENTED | Docs desactivados por modo `docker_local`, sin eludir requisitos de production. |
| B7 | IMPLEMENTED para carrera reproducida | Confirmación SQL antes de respuesta; 3 casos aislados y 2 MySQL de visibilidad/propiedad. No atribuye inequívocamente los 2/9 externos. |

Validaciones dirigidas aprobadas (no sumarlas): Docker/perfiles 101; capacidad 18 nuevas + controles relacionados; grounding/evaluación 213; conversaciones/cargas 46. Ruff, mypy sobre 116 módulos, documentación, autoría, secretos e integridad aprobaron en el conjunto previo a publicación. Entorno local Python 3.12.14; CI conserva 3.12.16. El entorno local no dispone de Docker ni PowerShell.

La publicación final de adjuntos sigue dependiendo del cierre transaccional de su dependencia HTTP: merece una comprobación separada de consulta inmediata de estado/fallo de commit; no se modificó ese contrato como parte de la carrera de creación de conversaciones. Reversión: revert de los commits; no hay migración nueva ni cambios en datos productivos. Una configuración Docker existente requiere añadir la IP documentada, sin regenerar secretos. Los resultados dirigidos y la suite completa se solapan; no se suman como casos distintos. La evidencia final de CI y el commit probado quedarán enlazados en el PR. Estado de producción: **NO EVALUADO**.
