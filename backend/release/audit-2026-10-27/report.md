<!-- Creado por Aldo Garcia. -->
# Auditoría técnica y correcciones — Matrix RH

Fecha: 2026-10-27. Base: `d057889aa85f1690576e2d65d19729a91cf06da4`.

## Dictamen

Se corrigieron defectos reproducibles de autorización, contratos, consistencia documental, diagnósticos y controles de calidad. La estructura por dominios existente es razonable; no hacía falta sustituir el stack ni crear capas vacías. Se separaron los contratos del transporte frontend, retiraron componentes sin consumidores y añadió una comprobación automatizada del repositorio.

**Estado: correcciones implementadas, aceptación productiva pendiente.** Una batería aislada aprobada y cero avisos conocidos de dependencias no demuestran ausencia de vulnerabilidades. Tampoco equivalen a una instalación Windows, una restauración MySQL ni una evaluación de respuestas con datos empresariales.

La evidencia final y sus límites se registran en [validation.json](validation.json). El inventario de revisión está en [coverage.json](coverage.json). Las cifras históricas de otras entregas no deben interpretarse como resultados de esta revisión.

La petición posterior de retirar los seis PDF y cerrar la recuperación de vectores está implementada y documentada en [vectors-followup.md](vectors-followup.md). Ese seguimiento contiene la validación del nuevo protocolo; las cifras de la batería inicial de esta página se conservan como evidencia histórica.

## Alcance y método

Se inventarió el repositorio completo y se distribuyó la lectura de código propio entre seis revisores especializados y un coordinador. Se leyó íntegramente la fuente textual de `backend/app`, los scripts operativos, migraciones y la fuente/configuración frontend. Las pruebas backend se inspeccionaron por contratos, regresiones y patrones transversales; no se afirma lectura manual de cada línea de todas las pruebas históricas. Las pruebas frontend sí se leyeron completas. Dependencias, archivos generados y binarios se verificaron con sus controles específicos, no mediante una falsa afirmación de lectura línea por línea.

Las reproducciones utilizaron datos sintéticos, SQLite y Qdrant en memoria. Se revisaron cambios entre dominios y se ejecutó colección completa de pytest con partición explícita entre pruebas aisladas e integración. Se consultaron avisos y metadata de distribuidores oficiales; no se usaron credenciales, documentos ni bases productivas.

Invariantes conservados: FastAPI/Python 3.12, React/TypeScript, MySQL, Qdrant, inferencia local Ollama o runtime compatible, permisos antes de recuperar contenido, aislamiento por propietario/conversación, fuentes verificables, cuatro entradas BAT y marca/experiencia Matrix RH. Entra continúa como autenticación opcional externa; no convierte el perfil local en un despliegue enteramente desconectado.

## Hallazgos corregidos

Todos los defectos de esta tabla están confirmados por código y pruebas o comprobación de artefactos. La severidad indica impacto potencial bajo las condiciones descritas; no afirma explotación en una instalación.

| ID | Severidad | Defecto y condición | Corrección y evidencia |
|---|---|---|---|
| AUTH-01 | Alta | Sesiones antiguas seguían utilizables después de cambiar proveedor o desactivar identidad sintética. | `auth/sessions.py` exige coincidencia entre proveedor activo, sesión y usuario; limita identidades sintéticas a `local_test` en entornos permitidos. Regresiones `test_session_provider_boundary.py`. |
| AUTH-02 | Media | Campos mal escritos, claves repetidas y categorías duplicadas podían alterar la política efectiva sin rechazo. | Esquema estricto, nombres completos, versión/sensibilidad cerradas y loader YAML que rechaza duplicados. `test_category_policy_validation.py`. |
| AUTH-03 | Media | Configuración federada expuesta a red en desarrollo admitía transporte/cookies inseguros. | HTTPS y cookie Secure obligatorios al exponer la aplicación, para todos los proveedores. `test_federated_transport_config.py`. |
| DATA-01 | Alta | `load_entra_mapping --prune` ignoraba el rol al comparar mappings; una degradación podía conservar privilegios y sesiones. | Comparación incluye rol; retira concesiones huérfanas y revoca sesiones afectadas del mismo proveedor. `test_entra_mapping_revocation.py`. Se aplica cuando el operador ejecuta la sincronización. |
| DATA-02 | Media | Planes SQL admitían valores no finitos, listas sin límite efectivo y proyecciones/agrupaciones incoherentes. | Límites y formas por operador, identificadores completos, columnas agrupadas y proyecciones inequívocas. `test_structured_plan_boundaries.py`. Se conserva SQL parametrizado; no se atribuye una inyección no demostrada. |
| DATA-03 | Media, condicionada | Una recarga de catálogo entre autorización y selección del adapter podía cambiar la fuente efectiva. | Copias del contrato y comprobación de la fuente del adapter antes de ejecutar; oculta parámetros SQL en errores. No congela cambios manuales de una variable DSN externa. |
| DATA-04 | Media, validación | Cinco restricciones UNIQUE de MySQL faltaban en el ORM usado por SQLite de pruebas. | ORM alineado al DDL y pruebas de unicidad. No se modificaron migraciones históricas ni existía ese defecto en los UNIQUE productivos. |
| DATA-05 | Media | Smoke de autorización podía operar sin entorno descartable y volcar contenido documental en un error. | Exige base descartable y cuentas sintéticas; informa solo categorías fuera del alcance. `test_smoke_authorization_guard.py`. |
| RAG-01 | Media | Un fallo SQL después del upsert dejaba vectores candidatos huérfanos, incluido el commit externo o la caída de la ingesta. | Compensación local más intención durable previa a chunks, bloqueo transaccional y recuperación según la generación SQL confirmada. Regresiones `test_ingestion_compensation_boundaries.py` y `test_generation_write_journal.py`, con caída real de proceso hijo. |
| RAG-02 | Media | El reconciliador privado perdía el reintento durable cuando fallaba también la limpieza. | Persiste trabajo de compensación y permite reintentarlo sin eliminar la generación activa. |
| RAG-03 | Media | Podía cambiar la revisión de embeddings entre calcular la huella y publicar/consultar. | Verifica nuevamente la huella antes de upsert y ranking; falla cerrada ante diferencias. |
| RAG-04 | Media | Leer todos los chunks del índice podía presentarse como cobertura completa pese a omisiones conocidas del extractor. | `extraction_incomplete` se conserva desde worker/adjunto hasta Qdrant y recuperación; avisos de parcialidad no se atribuyen solo a presupuesto. Regresiones `test_extraction_coverage_contract.py`. |
| LLM-01 | Media | Código residual Vertex podía intentar resolver ADC aun con inferencia local bajo una configuración legacy. | Retiradas ramas cloud y dependencias sin uso; proveedor no soportado se rechaza. La variable legacy queda inerte por compatibilidad. No se demostró envío real a cloud. |
| LLM-02 | Media | Embeddings permitían overrides cloud internos y podían reutilizar/cachear resultados sin verificar el pin correcto. | Valida proveedor/modelo y pin por lote, antes de cache hit y después de generar. `test_local_provider_boundaries.py`. |
| UI-01 | Media | Endpoints sin validador aceptaban JSON incompleto o confirmaciones `{ok:false}` como éxito. | Contrato obligatorio en cada request y validadores compartidos en `services/contracts.ts`; CSRF solo cambia tras respuesta válida. 46 casos nuevos de contratos. |
| UI-02 | Baja | Menú móvil cerrado conservaba controles enfocables fuera de pantalla. | Visibilidad coherente con apertura; regresión de navegador preparada. Ejecución visual pendiente. |
| UI-03 | Media, validación | E2E de adjuntos esperaba un texto de estado que la interfaz no muestra. | Comprueba `data-status` del adjunto exacto; conserva comprobación de error de perfil. E2E con servicios pendiente. |
| OPS-01 | Media | Preflight truncaba un nombre fijo o el destino de un symlink al comprobar escritura. | Archivo temporal exclusivo y limpieza de su propio archivo, incluso con error. `test_preflight_boundaries.py`. Requiere acceso local al directorio para explotar el caso previo. |
| OPS-02 | Media, condicionada | El sondeo Qdrant heredaba proxies ambientales, incluida su sonda de compatibilidad. | `trust_env=False` y `check_compatibility=False`, conservando timeout y cierre. Sin tráfico a un proxy real durante la prueba. |
| OPS-03 | Media | Una política de suministro vacía podía aprobar sin aplicar reglas. | Valida esquema, versión y reglas; política ausente/inválida falla cerrada, también en `--lock-only`. `test_supply_chain_policy.py`. |
| OPS-04 | Media | El scanner omitía líneas minificadas, solo veía el primer match y eximía plantillas por nombre. | Revisa todos los matches, no omite líneas largas, identifica `.env` anidado y no imprime el secreto. `test_secrets_scan_boundaries.py`. |
| QA-01 | Media | Un hook de pytest podía retirar casos después de generar el inventario y el gate aprobar una ejecución parcial. | Compara casos padre JUnit con selección real; admite subtests legítimos. Mini suites adversarias reales en `test_quality_gate_completeness.py`. |
| QA-02 | Baja | Benchmark aceptaba SLO infinito/NaN o porcentajes imposibles. | Rechaza umbrales antes de abrir sesiones o clientes. `test_load_test_contracts.py`. |
| RUNTIME-01 | Alta, componente | Python 3.12.15 no incorporaba las correcciones de seguridad de 3.12.16 y el manifiesto aún decía que faltaba el binario Windows. | Python 3.12.16/build 20261026 y uv 0.13.6 con bytes/hash oficiales verificados; Docker usa digest oficial de 3.12.16-slim. Sin actualización de una instalación productiva. |

## Estructura, limpieza y entrega

- Se conserva la separación existente entre API, identidad, autorización, casos de uso, RAG/ingesta, persistencia y adapters. Mover archivos sin mejorar contratos habría añadido riesgo sin beneficio.
- `frontend/src/services/contracts.ts` contiene tipos y validación; `api.ts` conserva transporte y reexportaciones compatibles.
- Se retiró `backend/scripts/uv_runtime.py`: helper sin consumidores operativos y límite de versiones incompatible con el runtime fijado. Se retiraron sus pruebas exclusivas y referencias históricas de esos tests.
- Se retiró `backend/scripts/model_inventory.py`: diagnóstico duplicado por `local_model_diagnostics.py`, que ya limita tamaño y campos de metadata.
- Se retiró `backend/docker/mysql/init/01-create-database.sql`: no está montado ni referenciado por Docker y creaba una base legacy diferente. Las migraciones vigentes permanecen intactas.
- Se retiró `BrandMark`, exportación frontend sin consumidores, y el extra/dependencias exclusivos Vertex.
- `frontend/dist` es parte de la distribución: se reconstruyó, sustituyendo únicamente los assets con hash obsoleto. Se regeneró el manifiesto de integridad del código operativo.
- CI usa acciones fijadas por SHA, token de lectura, dependencias bloqueadas, lint/tipos/tests/build y auditorías. Su gate exige ejecución completa de la partición aislada y bloquea omisiones. No acredita integración productiva.
- README incorpora prerrequisitos, operación, desarrollo y límites. El controlador Windows sigue requiriendo WampServer; una carpeta independiente `C:\www` no satisface ese contrato.

## Validación final aislada

- Backend: **3,530 pruebas padre aprobadas**, 40 subtests, cero fallos/errores/omisiones; 182 casos de servicios separados de esta partición.
- Cobertura de líneas de `app`: **91.99 %**; no se midió cobertura de ramas.
- Frontend: **252 pruebas aprobadas** en 14 archivos; tipos, lint, formato y build correctos.
- Python: 100 paquetes fijados auditados, incluidos opcionales/plataformas; cero avisos conocidos y cero paquetes omitidos. npm: 301 entradas en lock, cero avisos conocidos; 276 firmas y 81 atestaciones verificadas.
- Ruff, Mypy, encabezados, enlaces e integridad: aprobados. Los 37 candidatos de Bandit se clasificaron por contexto; no son 37 vulnerabilidades confirmadas.
- Entorno final: Python 3.12.16, PowerShell 7.6.9 sobre Linux, Node 24.22.1 y npm 11.19.1 para pruebas/build final. Las funciones PowerShell se probaron con fixtures sintéticos; esto no acredita instalación nativa Windows.
- Aviso no bloqueante observado: deprecación upstream de TestClient/HTTPX. El primer CI aprobó frontend y bloqueó backend por una prueba de suministro omitida: faltaba `node_modules` en ese job separado. Se añadió su instalación fijada sin scripts y se conserva el bloqueo ante cualquier omisión; las ejecuciones posteriores se consultan en los checks del PR. El segundo CI aprobó todos los casos y detectó después cuatro DSN sintéticos en nombres JUnit. Se sustituyeron por IDs descriptivos en tres módulos: 99 pruebas focalizadas aprobadas y cero matches en su nuevo JUnit, sin excluir reportes del scanner.

## Riesgos pendientes y condiciones de aceptación

| Prioridad | Pendiente | Evidencia y cierre requerido |
|---|---|---|
| Alta, alcance del proveedor | Copias históricas en referencias de PR/caché de GitHub | Tras autorización del usuario se reescribieron las cuatro ramas y se verificó una clonación nueva sin ninguno de los seis PDF. GitHub administra las referencias internas de PR y cachés; su purga requiere soporte y no está acreditada. [Evidencia y datos preparados para soporte](history-cleanup.md). No se leyó su contenido ni se promete revocar clones descargados. |
| Alta antes de producción | Validación del entorno real | Ejecutar instalación/actualización Windows, arranque/detención, TLS, Entra/OIDC si aplica, permisos efectivos de bases y restauración en entorno separado. No fue posible certificar estos servicios aquí. |
| Media, operación | Recuperación y residuos de índices existentes | El nuevo protocolo conserva intención durable ante commit fallido o caída de la ingesta y protege la activa bajo lock. Recuperar requiere SQL/Qdrant disponibles y ejecutar mantenimiento global. Para residuos previos sin intención se añadió diagnóstico/borrado manual por generaciones. No se ejecutó sobre índices reales ni se certificaron failover o pérdida de almacenamiento. [Detalle](vectors-followup.md). |
| Media | Evaluación RAG insuficiente | 43 casos sintéticos, 11 rúbricas completas; no validadas por expertos del dominio. Completar referencias independientes y ejecutar con corpus autorizado, modelo e índice fijados. No inventar respuestas para forzar un PASS. |
| Media | Acreditación de extracción histórica | Payloads sin `extraction_incomplete` se tratan como cobertura desconocida/parcial. Ejecutar reconciliación completa; `sync_new` no actualiza documentos existentes. Un documento legítimamente parcial no provoca reindexación infinita. |
| Media | Aislamiento y semántica | Un proceso de extracción con límites no es un sandbox de SO sin red. Las marcas detectan omisiones conocidas, no comprensión visual universal. Pins comprueban metadata del runtime, no integridad de pesos contra un runtime comprometido. |
| Media | Navegador y rendimiento | Instalación de Chromium falló por archivo descargado inválido. No hay aprobación visual/E2E real, latencia productiva, capacidad de GPU ni pruebas de carga. |

No se asigna una nota global que oculte estos pendientes. Cobertura de código por pruebas, cobertura de lectura y aceptación productiva son medidas distintas; las categorías operativas sin evidencia se mantienen **NOT_EVALUATED**. La valoración global es **PROVISIONAL**.

## Aplicación y reversión

Los cambios de aplicación son de código/configuración y no añaden migraciones. Se pueden revisar y revertir sobre la historia limpia mediante Git; no se debe fusionar ni restaurar la historia que contiene los PDF retirados. Antes de actualizar una instalación, detener Matrix y verificar respaldo coherente. La revocación Entra ejecutada con `--prune` es una acción deliberada: recuperar acceso exige restaurar concesiones aprobadas y autenticar de nuevo, no reactivar tokens antiguos. El índice previo conserva acceso bajo sus permisos, pero advierte cobertura desconocida hasta una reconciliación completa.

## Fuentes primarias del cambio de runtime

- [Python 3.12.16, publicación y correcciones de seguridad](https://www.python.org/downloads/release/python-31216/).
- [Builds oficiales de Astral, 20261026](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026).
- [uv 0.13.6](https://github.com/astral-sh/uv/releases/tag/0.13.6) y su [registro de descargas](https://raw.githubusercontent.com/astral-sh/uv/0.13.6/crates/uv-python-managed/download-metadata.json).
- Imagen oficial `docker.io/library/python:3.12.16-slim`: digest del índice OCI contrastado con los bytes y cabecera `Docker-Content-Digest` de `registry-1.docker.io`.
