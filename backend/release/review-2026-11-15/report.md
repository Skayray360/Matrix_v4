<!-- Creado por Aldo Garcia. -->
# Validación del archivo de mejoras — 15 de noviembre de 2026

Se contrastó íntegramente `README MEJORAS MATRIX RH.md` con el proyecto. El archivo revisaba `main@5400884`; el trabajo anterior está en la PR #3, cuya base de esta revisión es `5f17e42fa6b17f89698c91ddfcc7b1e0354e8acb`. Por eso sus afirmaciones sobre ausencia de CI, auditorías sin ejecutar y documentación antigua no describen completamente esta rama. La PR permanece en borrador, sin fusionar ni desplegar.

SHA-256 del archivo recibido: `5895fe4e7e88b4bcb618627fdb744190ffd7fe59c5bfd65226209f4d07f73862`.

La revisión aplica correcciones demostrables y añade instrumentos para medir lo que falta. No acredita rendimiento, calidad de respuestas corporativas ni preparación para producción. No se usaron documentos de RH, modelos, bases ni índices de la instalación real. Se conservan las correcciones previas de permisos, extracción y recuperación de generaciones vectoriales; los seis PDF siguen fuera del código.

## Decisión por propuesta

| ID | Resultado | Evidencia y decisión |
|---|---|---|
| F1 | Parcialmente inexacto; SSE diferido | `ChatPage.tsx` ya muestra cola/procesamiento, saturación y cancelación. El polling no comunica fases internas. SSE necesita fases reales, revalidación de sesión/permisos, gestión de conexiones y prueba de buffering en WAMP; no hay evidencia de menor latencia. No se introduce texto sin verificar ni un efecto de escritura artificial. |
| F2 | Aplicado como opción | `scripts/model_warmup.py`: plan sin red; `--run` ejecuta una sonda por rol, pins obligatorios, 32 tokens y plazo limitado. Conserva `keep_alive`, contexto y arranque; no descarga pesos. La ejecución no garantiza residencia conjunta de modelos. |
| F3 | Aplicado a perfiles Docker nuevos | `docker_prepare.py` genera `INFERENCE_MAX_INFLIGHT=1`. Compose permite una petición por modelo; dos generaciones del mismo modelo podían esperar dentro del runtime. El límite global también serializa embeddings: es una configuración conservadora, no una mejora de throughput medida. Preserva configuraciones existentes y Windows. |
| F4 | Cambio diferido | El router considera marcadores y señales de contexto. No existe una evaluación real de errores de enrutamiento que justifique sustituirlo o añadir otro modelo. |
| F5 | Instrumentación aplicada | `benchmark_response.py` compara estimación de mensaje/prefijo/schema con `prompt_eval_count`; omite la razón tras regeneración y si falta la métrica. No recalibra presupuestos ni interpreta la razón como prueba de truncamiento. |
| R1 | Condicional; no activado | Dense + MMR es el comportamiento existente. Una sonda sintética confirma Prefetch/RRF y filtros por rama en Qdrant embedded 1.20; no demuestra BM25 ni mejora de calidad. Faltan baseline representativa, costes y una migración controlada antes de cambiar el recuperador. |
| R2 | Condicional; no activado | Reranking requiere modelo, procedencia, recursos y medición de calidad/latencia. No se descargan pesos ni se modifica el ranking por intuición. |
| R3 | Infraestructura existente; datos pendientes | La evaluación ya admite casos privados externos, particiones y rúbricas. No se inventan 150–300 preguntas de RH ni se presentan casos sintéticos como corpus representativo. Faltan respuestas autorizadas y anotación humana. |
| R4 | Aplicado | Exportación privada y explícita de chunks: SQL ACL antes de Qdrant, permiso de diagnóstico, propiedad real de conversaciones, generación/fingerprint activos, límite de texto y revalidación antes de escribir. El modo ordinario sigue mostrando metadatos. El contenido exportado permanece local y fuera del repositorio. |
| R5 | Condicional; defaults conservados | OCR local ya existe. Activarlo globalmente sin muestra escaneada ni revisión de exactitud/tiempo no está justificado. |
| M1 | Corregida deriva; procedencia pendiente | `model-manifest.json` pasa de release 1.3.3 a 1.4.2 y entra al guard de versiones. No se inventan licencias, digests completos ni validación de los modelos de la máquina del usuario. |
| M2 | Medición pendiente | Una semilla de evaluación requiere soporte explícito del contrato del proveedor; temperatura 0 tampoco prueba determinismo. No se altera el muestreo productivo sin comparar el mismo conjunto real y registrar el entorno. |
| M3 | Flujo existente; cambio de modelo diferido | Se conserva la herramienta de cambio de modelo. Elegir otro modelo sin corpus, presupuesto de memoria y evaluación repetiría el problema de falta de evidencia. |
| S1 | Aplicado | Swagger/OpenAPI permitidos únicamente en `development`/`test`; `standalone`, `staging` y `production` los ocultan. Pruebas por perfil. |
| S2 | Aplicada alternativa de destino cifrado | `installation_backup backup --require-encrypted-destination` exige BitLocker completo y activo antes de copiar. Es opcional y solo para ese respaldo nativo; no cifra archivos ni otros flujos de backup, ni protege una copia trasladada a otro volumen. |
| S3 | Aplicado diagnóstico | `standalone` consulta BitLocker para código, documentos, uploads, Qdrant embedded y datadir disponible. Informa WARN si falta evidencia; no altera BitLocker ni impone una atestación ficticia. |
| S4 | Condicional; diferido | Un encadenamiento local reescribible por el mismo administrador no acredita inmutabilidad. Falta necesidad legal definida y un destino externo de solo anexado/ancla; no se presenta un hash local como garantía. |
| S5 | Ampliada prueba indirecta | Fixtures de PDF invisible, DOCX oculto/tabla, XLSX y homoglifos atraviesan extracción/ingesta/recuperación; comprueban aislamiento de títulos privados y tratamiento como datos. Embeddings simulados: no acreditan resistencia semántica de Gemma. |
| S6 | Aplicado tras prueba de navegador | CI aprobó la CSP estricta sobre el build: Composer conserva altura y TracePanel el ancho calculado; un atributo de estilo inyectado se bloquea. Se retira `unsafe-inline` de backend y Nginx y se conserva la regresión de navegador/HTTP. El uso de CSSOM no requería esa excepción. |
| S7 | Aplicado en parte | Auditoría actual de todos los locks, parches de dependencias, SBOM CycloneDX 1.6 offline, CI semanal y Dependabot. La firma de entrega exige identidad y proceso de publicación; no se inventa una firma ni una cadena de confianza. |
| S8 | Aplicado en `app` | Siete excepciones específicas B603/B405/B314 documentan mitigaciones de XML/OCR, respaldadas por 30 regresiones nuevas. Bandit bloquea en CI toda señal adicional en `app`, sin baseline ni exclusión de reglas/archivos. `scripts` conserva sus señales y no pertenece a este gate; no se anuncia cero hallazgos globales. |
| C1 | Ya existía; ampliado | CI ya tenía pruebas aisladas, servicios MySQL/Qdrant y navegador con API simulada. Se añade matriz Windows x64 PowerShell 5.1/7 con nueve fixtures por motor, schedule y SBOM. No equivale a instalar WAMP/Ollama. |
| C2 | Refactor amplio diferido | Dividir módulos por tamaño sin necesidad funcional ni baseline local aumenta el riesgo. Las herramientas nuevas tienen responsabilidades separadas; no se reestructura masivamente el orquestador o la persistencia. |
| C3 | Integración por PR conservada | El snapshot de dos commits no representa la rama de auditoría. Los cambios continúan en PR #3 para revisión; no se alteran reglas administrativas, se fusiona main ni se publica una release. |
| C4 | Aplicado guard | Se mantiene `frontend/dist` porque forma parte de la entrega Windows. CI compara inventario y bytes con el commit y rechaza faltantes, extras, enlaces o diferencias. |
| Documentación | Aplicado | Guard de versiones de app, pyproject, uv, npm y manifiesto; Dockerfile frente al runtime fijado. Los informes históricos mantienen sus cifras originales. La guía operativa explica las opciones y límites nuevos. |
| Fine-tuning | No aplicable todavía | Requiere datos permitidos, baseline y una deficiencia demostrada que RAG/configuración no resuelvan. No se entrena ni se sustituye el modelo. |

## Dependencias: hallazgo adicional confirmado

La auditoría del 15 de noviembre detectó siete avisos en `pypdf==6.21.2` y una vulnerabilidad de `minimatch` propagada a nueve dependencias de desarrollo del frontend. No son nueve vulnerabilidades independientes ni afecta este aviso a React distribuido.

- Se fija el mínimo de `pypdf` en 6.21.4 y se actualiza únicamente ese paquete en `uv.lock`.
- ESLint pasa a 10.15.0. El parser de TypeScript mantiene su versión y recibe una sustitución acotada de `minimatch` a 11.0.5, porque la versión vigente del parser todavía requiere la rama afectada. No se aplica la degradación automática sugerida por `npm audit fix --force`. El override debe retirarse cuando el upstream acepte una versión corregida, tras verificar lint y auditoría.
- Resultado posterior: cero avisos conocidos en 100 paquetes Python del lock y cero en el árbol npm completo. Es una observación fechada, no garantía de ausencia de vulnerabilidades.
- SBOM: 402 identidades de paquetes de ambos locks. Incluye desarrollo/opcionales/plataformas; no representa lo instalado, SO, modelos, grafo de dependencias, licencias verificadas ni una firma. Es reproducible y registra hashes de ambos locks, sin publicar URLs de registro o credenciales.

Avisos de pypdf: `GHSA-62m5-6wh8-28x7`, `GHSA-42gh-23hr-vphw`, `GHSA-x3rh-gjgm-fmqc`, `GHSA-4vqv-7265-gm2w`, `GHSA-5849-834g-hvw5`, `GHSA-mfr5-77xh-9j7h`, `GHSA-cggf-277g-3q54`. Frontend: `GHSA-g3hv-75wg-m249`.

## Validación

La [CI del código final](https://github.com/Skayray360/Matrix_v4/actions/runs/43765743247), commit `d49ff2419692400e36f1fb8d1b2a5204497c4358`, aprobó sus cinco jobs. La [evidencia estructurada](validation.json) conserva versiones, hashes de locks, avisos antes/después y alcance. No se suman suites superpuestas como pruebas diferentes.

| Comprobación | Resultado y alcance |
|---|---|
| Backend aislado | 3.730 casos aprobados, sin fallos ni omisiones; inventario raíz contrastado con JUnit. |
| Frontend | 252 pruebas; tipos, ESLint, formato y build aprobados; `dist` coincide byte a byte. |
| Chromium | 12 casos sobre el build con API sintética, incluidos funcionamiento CSSOM y bloqueo de estilo inyectado bajo CSP estricta. |
| Recuperación vectorial | 10 escenarios con MySQL real en cada modo Qdrant: 20 ejecuciones aprobadas; 13 migraciones en base efímera. Embeddings deterministas. |
| Windows x64 | Nueve fixtures por motor: PowerShell 5.1 y 7. Runtime Python 3.12.16 del manifiesto comprobado por tamaño/SHA-256. |
| Permisos Windows | DACL real de archivo, directorio y descendiente temporal aprobada en ambas matrices. Sin importar aplicación ni usar datos reales. |
| Controles de código y dependencias | Ruff, mypy, documentación/versiones, autoría, integridad, secretos, Bandit de `app`, suministro y auditorías aprobados. Cero avisos conocidos en los locks auditados. |
| SBOM | 402 identidades de ambos locks; generación offline y validación contra schema CycloneDX 1.6. |

La matriz Windows permitió corregir dos problemas funcionales: carga explícita de `System.IO.Compression` para PowerShell 5.1 y aislamiento de `PSModulePath` en los procesos hijos que aplican/verifican DACL desde Python. También se corrigieron fixtures que asumían LF o `pwsh --version`. Las reglas de permisos y los rechazos de seguridad se conservaron.

La CI de navegador usa respuestas API sintéticas; no es un E2E con Ollama. Las fixtures Windows prueban funciones reales con servicios simulados, salvo las operaciones de permisos sobre archivos temporales. No se ejecutaron los otros 182 casos de integración ni una instalación/recuperación WAMP completa.

La exportación embedded está limitada a una copia de colección de 64 MiB/15 segundos, sin WAL/journal pendiente y con el lock original libre; el scroll admite como máximo 2.000 chunks y 4 MiB de texto. La prueba sintética conserva hashes/filas originales incluso cuando el SDK normaliza duplicados en la copia. La compatibilidad se limita al formato del cliente Qdrant fijado en el lock.

## Pendientes de la instalación real

1. Medir hardware, arranque frío/caliente, memoria, colas y latencias con los modelos y digests reales.
2. Preparar el conjunto privado autorizado y anotado; medir recuperación, citas, abstención, aislamiento y consistencia antes de cambios de ranking/modelo/chunking.
3. Ejecutar instalación/actualización WAMP y recuperación de respaldo en una copia separada. Las fixtures del controlador no verifican privilegios, BitLocker, servicios ni errores del host real.
4. Definir destino de respaldo y, si aplica, política de retención, cifrado portátil y auditoría externa con RH/Legal. La opción BitLocker no resuelve el ciclo de vida de una copia exportada.
5. Fusionar y desplegar solo después de revisión. Schedule y Dependabot operan desde la rama predeterminada; agregarlos a una PR no los activa en main.

## Fuentes primarias y trazabilidad

- [PR #3 y comprobaciones](https://github.com/Skayray360/Matrix_v4/pull/3).
- [pypdf 6.21.4, correcciones de seguridad](https://github.com/py-pdf/pypdf/releases/tag/6.21.4).
- [Advisory pypdf: EOF y bucle sin terminación](https://github.com/advisories/GHSA-5849-834g-hvw5).
- [Advisory minimatch: amplificación de patrones](https://github.com/advisories/GHSA-g3hv-75wg-m249).
- [Ollama: concurrencia por modelo, precarga y keep_alive](https://docs.ollama.com/faq).
- [CycloneDX 1.6 JSON](https://cyclonedx.org/docs/1.6/json/).
- [Dependabot: opciones y ecosistemas](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference).
- [Microsoft: herencia de PSModulePath a través de Python](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-7.3).

Creado por Aldo Garcia.
