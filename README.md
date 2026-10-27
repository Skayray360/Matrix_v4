# Matrix RH 1.4.2

<!-- Creado por Aldo Garcia. -->

Asistente documental de Recursos Humanos con FastAPI/Python, React/TypeScript, MySQL, Qdrant y modelos locales mediante Ollama. Consulta documentos autorizados, resume secciones, compara fuentes y analiza adjuntos privados de la conversación. Conserva cuentas, permisos, conversaciones e índices en esta instalación.

La instalación principal es **Windows x64 con WampServer**. Apache publica la aplicación y Python sirve la API y la interfaz compilada. Matrix utiliza su propia base de datos. Docker es una alternativa separada para Linux.

## Arquitectura y alcance

![Arquitectura local de Matrix RH: entrada, orquestación, RAG, modelos y datos](backend/release/architecture/matrix-architecture.svg)

El diagrama representa componentes existentes. El perfil `standalone` mantiene autenticación, generación, embeddings, índice y datos localmente. Los adaptadores `openai_compatible` permiten runtimes locales aprobados; la configuración Vertex y `LLM_LOCAL_ONLY=false` se rechazan. Activar Entra ID cambia la frontera de identidad a un proveedor externo y debe describirse como perfil híbrido de autenticación, aunque la inferencia permanezca local. Las descargas y auditorías de instalación usan red; la operación local no significa instalación sin Internet.

### Responsabilidades en el código

![Estructura real por capas y responsabilidades de Matrix RH](backend/release/architecture/matrix-layers.svg)

| Responsabilidad | Implementación real | Contrato que protege |
|---|---|---|
| Interacción | `frontend/src/pages`, `components`, `services`, `security` | Mensajes, adjuntos y referencias; la UI no concede permisos |
| Entrada HTTP | `backend/app/api/routes`, `deps.py`, middleware | Sesión, CSRF, validación y límites antes de operar |
| Orquestación | `backend/app/agents` | Cola, selección de flujo, plazo total y cancelación |
| Conocimiento | `backend/app/ingestion`, `rag` | Extracción aislada, ACL antes de recuperar, versiones y citas |
| Modelos | `backend/app/llm` | Generación y embeddings configurables; endpoints locales aprobados |
| Persistencia | `backend/app/database`, `memory`, `rag/vector_store.py` | Estado en MySQL, vectores en Qdrant y adjuntos por propietario/conversación |
| Controles comunes | `auth`, `authorization`, `security`, `audit` | Identidad, mínimo privilegio, trazabilidad y errores redactados |

Los dibujos adaptan las referencias visuales proporcionadas por el propietario al código real de Matrix. Las carpetas conservan su organización actual; la persistencia utiliza SQLAlchemy y servicios especializados, sin añadir una capa `Repositories` vacía.

### Recorrido de una respuesta

1. El navegador envía la pregunta con su sesión; la API comprueba origen, CSRF, cuerpo y permisos.
2. La cola reserva capacidad, conserva la operación y aplica el límite por usuario/conversación.
3. El orquestador construye el contexto permitido. Una pregunta documental recupera dentro del alcance autorizado; los resúmenes recorren el documento seleccionado con un presupuesto acotado.
4. Qdrant aporta fragmentos de generaciones activas y compatibles con el embedding; el modelo local recibe evidencia delimitada, historial autorizado e instrucciones.
5. Se validan afirmaciones, localizadores y reglas numéricas aplicables. El usuario recibe respuesta con fuentes, una respuesta parcial o una limitación explícita.
6. MySQL conserva la operación y los mensajes; el frontend muestra estados legibles, permite cancelar y elimina la vista privada cuando una respuesta 401 confirma la expiración de sesión.

### Decisión de arquitectura: RAG y CAG

**Se conserva RAG autorizado como estrategia principal. No se implementó ni se midió CAG en esta revisión.**

CAG (*Cache-Augmented Generation*) precarga conocimiento en el contexto del modelo y reutiliza estados de inferencia. El [trabajo original](https://arxiv.org/abs/2412.15605) lo estudia para conocimiento de tamaño manejable; sus resultados no establecen una superioridad universal. Matrix ya tiene una caché de embeddings de consulta: reutilizar esos vectores no equivale a mantener una caché KV del corpus.

| Criterio | RAG actual | CAG como experimento futuro |
|---|---|---|
| Documentos y permisos cambiantes | Filtrado previo y generaciones activas | Reconstruir o invalidar la caché al cambiar permisos, fuentes o modelo |
| Adjuntos privados | Propietario y conversación delimitan búsqueda/contexto | Aislamiento estricto por esos mismos alcances; nunca una caché global compartida |
| Memoria y contexto | Enviar evidencia seleccionada con presupuesto | El corpus autorizado, instrucciones y salida deben caber en el contexto efectivo |
| Coste por consulta | Recuperación y generación | Precarga inicial y reutilización dependen del runtime; requieren medición |
| Evidencia disponible aquí | Contratos y pruebas sintéticas | Sin comparación A/B real; ventaja no demostrada para Matrix |

La documentación de [contexto](https://docs.ollama.com/context-length) y [concurrencia de Ollama](https://docs.ollama.com/faq) explica el aumento de memoria con contexto y paralelismo. Por eso no se aumenta el contexto ni se elimina Qdrant por preferencia arquitectónica. Para decidir, comparar RAG y CAG con el mismo modelo/digest, roles, preguntas y corpus autorizado: exactitud revisada por RH, citas correctas, abstención, cero fugas ACL, p50/p95, primera respuesta, memoria y coste de reconstruir la caché. Conservar RAG si CAG pierde cualquiera de esos controles.

## Estado de esta revisión

La [remediación del 25 de octubre de 2026](backend/release/remediation-2026-10-25.json) conserva las mejoras de arquitectura, seguridad y chat e incorpora:

- Retirada de **16 documentos internos** del árbol actual del repositorio. Las pruebas utilizan documentos sintéticos.
- Exclusión del corpus operativo de Git y de los paquetes de código, incluso cuando hay documentos locales presentes.
- Actualización de dependencias, runtimes y referencias de imágenes verificadas.
- Correcciones de los cuatro BAT y del controlador de Windows, con recuperación ante fallos de actualización.
- Corrección del manifiesto de integridad para verificar código del backend y del frontend en la entrega nativa.

La eliminación del árbol actual **no purga el historial Git ni los clones existentes**. Los archivos de instalaciones del usuario no se han modificado.

### Versiones verificadas

| Componente | Versión de esta revisión |
|---|---|
| Node.js / npm | 24.22.1 / 11.19.1 |
| uv | 0.13.5 |
| Python Windows | 3.12.15, build 20261021 |
| MySQL | 8.4.13 |
| Qdrant y cliente Python | 1.20.0 |
| Ollama en la alternativa Docker | 0.40.11 |
| Nginx en la alternativa Docker | 1.30.6-alpine-slim |
| pypdf / filelock | 6.21.2 / 4.2.1 |
| Vitest / Zod | 4.1.12 / 4.6.7 |

Las dependencias se resuelven desde los archivos lock. Los artefactos externos aprobados se fijan por versión y hash. El Ollama y los modelos existentes del usuario se conservan; el supervisor de Windows no los reemplaza.

**Pendiente del proveedor:** [Python 3.12.16](https://www.python.org/downloads/release/python-31216/) se publicó como código fuente el 25 de octubre. No hay un binario Windows correspondiente en el registro de la versión fijada de uv; Python 3.12.15 aún carece de esas correcciones. Algunas imágenes oficiales de Docker también conservan avisos en sus componentes. Actualizar las versiones no equivale a certificar toda la plataforma sin vulnerabilidades.

## Organización del proyecto

| Área | Contenido |
|---|---|
| `backend/` | API, identidad, autorización, ingesta, RAG, adaptadores de modelos, migraciones y pruebas |
| `frontend/` | Chat React/TypeScript, presentación de fuentes, adjuntos, estados de operación y pruebas |
| `knowledge-base/` | Área reservada para conocimiento local autorizado; la distribución solo incluye un marcador vacío |
| Cuatro BAT de la raíz | Entrada única a instalación, arranque, parada y diagnóstico en Windows |
| README | Arquitectura, uso general y evidencia pública agregada |

Los documentos empresariales se incorporan únicamente en la instalación autorizada. No se incluyen cuentas operativas, conversaciones, adjuntos ni corpus real en el paquete de código. Los ejemplos de las pruebas son ficticios y no establecen políticas de Recursos Humanos.

## Uso general en Windows

Se requiere Windows x64, PowerShell 5.1 x64 o posterior, WampServer con Apache 2.4.69 o posterior y Ollama local con los modelos configurados. La preparación necesita acceso a los proveedores oficiales para descargar y auditar dependencias. La RAM, VRAM y capacidad de almacenamiento necesarias dependen del modelo y del volumen documental.

1. Obtenga una entrega completa. En una instalación existente, valide previamente el procedimiento de actualización y recuperación con el responsable de los datos.
2. Inicie WampServer y Ollama con los modelos autorizados disponibles.
3. Ejecute `instalar.bat` con elevación explícita y la misma cuenta de Windows que utilizará para operar Matrix. Siga las indicaciones locales del instalador.
4. Ejecute `diagnosticar.bat` y complete la aceptación funcional antes de dar acceso a usuarios.

| Archivo | Función |
|---|---|
| `instalar.bat` | Preparar y validar componentes, auditar dependencias y compilar la interfaz |
| `iniciar.bat` | Verificar la instalación y arrancar los servicios propios |
| `detener.bat` | Detener los procesos propios de Matrix conservando los datos |
| `diagnosticar.bat` | Comprobar la preparación y comunicar fallos accionables |

Los BAT mantienen rutas relativas a su ubicación, ejecutan PowerShell mediante una ruta del sistema y propagan el resultado de la operación. Se corrigió el manejo de rutas con signos de exclamación. El controlador comprueba la procedencia e integridad de runtimes y conserva una copia recuperable durante actualizaciones; Python y su entorno virtual se tratan conjuntamente.

El instalador puede reiniciar Apache tras validar su configuración, lo que interrumpe brevemente otros sitios de WAMP. Una actualización de código no realiza migraciones destructivas de datos ni autoriza cambios de modelos. La alternativa Docker protege contra reutilizar datos persistentes con versiones distintas sin una actualización controlada; requiere validación propia antes de utilizarse.

## Experiencia del chat y aceptación

El chat permite seleccionar conversaciones, adjuntar documentos privados, consultar fuentes y cancelar operaciones. Los estados de espera y procesamiento se muestran al usuario; una sesión vencida elimina la vista de datos privados. Las respuestas documentales deben conservar citas y condiciones, y explicar cuándo la evidencia es insuficiente.

La aceptación en el equipo de destino debe incluir:

- Una consulta cuya respuesta esté explícitamente respaldada por un documento autorizado.
- Un resumen de una sección concreta y una comparación entre fuentes compatibles.
- Una pregunta sin evidencia suficiente, que debe producir una limitación clara.
- Comprobación de que otro usuario no puede recuperar documentos, adjuntos ni conversaciones ajenos.
- Cancelación, expiración de sesión y varias peticiones simultáneas con medición de latencia y consumo de recursos.

Los resultados con fixtures sintéticos no demuestran por sí solos la calidad del modelo sobre los documentos de la organización. Esa evaluación requiere preguntas y respuestas de referencia revisadas por sus responsables.

## Validación y límites

| Comprobación | Resultado |
|---|---|
| Backend | 3.189 casos y 40 subpruebas aprobados; cero fallos |
| Casos backend omitidos | 156, por requerir MySQL/MariaDB disponible |
| Frontend | 127 pruebas aprobadas; tipos, lint, formato y compilación correctos |
| Dependencias Python y npm | Cero avisos conocidos en los escaneos de los locks consultados |
| PowerShell | Funciones aisladas y escenarios sintéticos ejecutados con PowerShell 7.6.9 en Linux |
| Integridad de la entrega | ZIP aceptado; alteraciones de código detectadas por los validadores correspondientes |
| Qdrant | Compatibilidad local con índice sintético y comprobaciones de servidor oficial con datos ficticios |
| Revisión independiente | Cambios del alcance revisados; regresión de integridad corregida |

El [registro agregado de esta remediación](backend/release/remediation-2026-10-25.json) distingue los escaneos de paquetes de los avisos pendientes en runtimes e imágenes base. Los [resultados de la revisión anterior](backend/release/audit-2026-10-25/validation.json) y su [puntuación provisional](backend/release/audit-2026-10-25/score.json) corresponden a su propio alcance.

Faltan la instalación completa con Windows CMD/PowerShell 5.1 y WAMP, las pruebas con MySQL real, el build y despliegue completos de Docker, y la aceptación de calidad y concurrencia con Ollama/GPU en el equipo de destino. Los hashes y las pruebas sintéticas no acreditan la ausencia de vulnerabilidades desconocidas. **No se declara certificación de producción ni una puntuación de 9.5/10.**

Creado por Aldo Garcia.
