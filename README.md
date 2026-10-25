# Matrix RH 1.4.2

<!-- Creado por Aldo Garcia. -->

Asistente documental de Recursos Humanos con FastAPI/Python, React/TypeScript, MySQL, Qdrant y modelos locales mediante Ollama. Consulta documentos autorizados, resume secciones, compara fuentes y analiza adjuntos privados de la conversación. Conserva cuentas, permisos, conversaciones e índices en esta instalación.

La instalación principal es **Windows x64 con WampServer**, dentro de `D:\wamp64\www\matrix-rh-1.4.2`. Apache de WAMP publica la aplicación; Python sirve la API y la interfaz compilada. No se usa PHP ni la base de datos compartida de WAMP. Docker es una alternativa separada para Linux.

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

### Estado de verificación actual

Revisión del **25 de octubre de 2026**, sobre el commit base `dbc6eea2492ae75b428cd2b92e60758b19eb74af`. Los [resultados agregados de validación](backend/release/audit-2026-10-25/validation.json) y la [puntuación provisional](backend/release/audit-2026-10-25/score.json) documentan lo comprobado. El informe detallado se entrega por separado al propietario. La aceptación productiva y la meta de 9.5/10 siguen pendientes. Las cifras históricas al final de esta guía corresponden a sus revisiones indicadas y no sustituyen los resultados actuales.


Los documentos, índices, SQL, secretos y runtimes propios de Matrix permanecen dentro de su carpeta. **Se reutilizan el Ollama y los modelos ya instalados por el usuario**, en su ubicación actual: no se copian, descargan, actualizan ni eliminan. Windows, WAMP y Ollama local activo son prerrequisitos. El instalador agrega un include al Apache existente y valida su configuración antes de reiniciarlo; ese reinicio interrumpe brevemente los demás sitios de WAMP. `detener.bat` deja Apache disponible.

El perfil predeterminado `standalone` es para uso en este equipo: Apache, API y MySQL propios escuchan en loopback y Matrix accede a Ollama por loopback. La escucha del servicio Ollama sigue regida por su configuración existente. La identidad local es real y persistente, con contraseñas protegidas con hash Argon2id, sesiones, CSRF, permisos y bloqueo por intentos; las cuentas de prueba están desactivadas. Para uso por otros equipos se requiere diseñar y validar HTTPS, firewall, identidad y protección de datos. No habilite acceso LAN cambiando solamente `APP_HOST`.

## Estructura

```text
matrix-rh-1.4.2/
├── knowledge-base/
│   ├── documents/prestaciones/     # 6 PDF originales, sin alteraciones
│   ├── unclassified/ACR/          # 10 documentos conservados, sin indexar
│   ├── state/                     # MySQL, Qdrant, adjuntos y control de procesos
│   └── backups/                   # respaldos completos, creados al respaldar
├── backend/
│   ├── app/                       # API, identidad, extracción, RAG y modelo
│   ├── config/                    # plantilla, configuración privada y políticas
│   ├── scripts/windows/           # controlador de los cuatro BAT
│   ├── scripts/                   # diagnóstico y mantenimiento
│   ├── migrations/                # esquema SQL versionado
│   ├── seeds/                     # políticas; fixtures de usuarios desactivados
│   ├── tests/                     # pruebas y conocimiento sintético aislado
│   ├── release/                   # integridad, procedencia y mapa de movimientos
│   ├── runtime/                   # Python, uv y MySQL propios
│   ├── logs/                      # registros locales
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/                      # React/TypeScript, dist y configuración Nginx
├── instalar.bat
├── iniciar.bat
├── detener.bat
├── diagnosticar.bat
├── docker-compose.yml
├── .htaccess
└── README.md
```

Sólo hay tres carpetas en la raíz. Las carpetas de estado se crean cuando se necesitan. `.htaccess` se conserva expresamente: impide publicar código, bases y secretos desde el DocumentRoot de WAMP. La configuración Apache generada también deniega acceso directo al árbol del proyecto. No lo quite para intentar abrir la interfaz: use el puerto 8085.

### Qué se movió o retiró

| Antes | Ahora / motivo |
|---|---|
| `data/prestaciones` y PDF de gastos médicos | `knowledge-base/documents/prestaciones`; mismos bytes y categoría |
| `data/ACR` | `knowledge-base/unclassified/ACR`; conservar sin inventar clasificación ni permisos |
| Corpus sintético | `backend/tests/fixtures/knowledge`; excluido de la ingesta operativa |
| `config`, `.env.example`, SQL y Nginx de infraestructura | `backend/config`, `backend/docker` y `frontend/nginx` |
| Scripts Windows y numerosos BAT | Un controlador interno y cuatro BAT relativos |
| `docs`, README adicionales y guías históricas | Este README; sin documentación duplicada |
| Traslados automáticos entre entregas y limpieza legacy | Retirados; podían mezclar SQL, índices y adjuntos de instalaciones distintas |
| Dependencias instaladas, cachés y `reports` | No forman parte del ZIP limpio |

El mapa por archivo y los hashes de procedencia están en `backend/release/layout-migration.json`. La entrega anterior no se modifica. Este ZIP contiene el código y los documentos originales incluidos en la entrega base; **no contiene las conversaciones, usuarios ni adjuntos guardados únicamente en su computadora**.

## Requisitos e instalación Windows/WAMP

Necesita Windows x64, PowerShell 5.1 x64 o posterior y WampServer con **Apache 2.4.69 o posterior** registrado como servicio. El instalador comprueba la versión de Apache antes de preparar componentes; WAMP es un servicio compartido y debe actualizarse por su administrador si no cumple ese mínimo. Los cuatro BAT utilizan el PowerShell de Windows mediante una ruta absoluta, incluida la redirección a x64 cuando corresponde; no buscan un ejecutable del mismo nombre en la carpeta o en `PATH`.

Matrix prepara su propio **MySQL Community 8.4.13 x64**, Python 3.12.15 y Node 24.22.0 con npm 11.19.1. También detecta Visual C++ x64: reutiliza la versión aprobada o una posterior, y, si falta o es anterior, instala el redistribuible Microsoft 14.51.36247.0 fijado. Antes de ejecutarlo verifica SHA256 y firma Authenticode de Microsoft; no reinicia el equipo automáticamente. Comprueba las firmas de las bibliotecas Visual C++ existentes y que ambos ejecutables MySQL puedan responder a `--version` antes de crear datos.

La preparación necesita HTTPS a los proveedores oficiales y servicios de auditoría: GitHub/activos de versiones, PyPI, `cdn.mysql.com`, `nodejs.org`, `download.visualstudio.microsoft.com`, `registry.npmjs.org` y `api.osv.dev`. Las descargas tienen versiones y SHA256 fijados en `backend/config/runtime-manifest.json`. Se rechazan redirecciones ajenas a la lista aprobada, HTTP, credenciales en URL, paquetes alterados, rutas de escape en ZIP y variables heredadas que cambien instaladores, registros o validaciones TLS. La consulta de vulnerabilidades transmite identificadores y versiones de paquetes públicos; no transmite documentos, conversaciones ni contraseñas. Si la auditoría falla o no puede completarse, se detiene la instalación: un error de conexión no se interpreta como ausencia de vulnerabilidades.

Antes de instalar paquetes Python, `uv audit` comprueba el lock de producción para Windows, sin ignorar avisos; `uv sync` mantiene las versiones de `uv.lock`, reutiliza paquetes compatibles y prepara los faltantes en el entorno propio. Se admiten ruedas binarias, sin compilar dependencias ni descargar herramientas de build para instalar el código local. Después se ejecuta `uv pip check`. Python y uv ajenos al proyecto no se modifican.

Node/npm existentes se reutilizan únicamente si el ejecutable y el bundle npm completo coinciden por hash con la distribución aprobada. En caso contrario se prepara una copia propia en `backend/runtime/node`, sin sustituir la global. La interfaz se compila desde el código: primero se valida `package-lock.json`, su procedencia y lista de bloqueo; se ejecuta `npm audit` incluyendo herramientas de compilación; se reutiliza el árbol de paquetes si su recibo y hashes coinciden o se restaura con `npm ci --ignore-scripts`; se verifica el árbol instalado y sus firmas con `npm audit signatures`. No se ejecutan hooks de instalación ni `npm audit fix`. TypeScript y Vite compilan en una carpeta temporal, y sólo un build terminado reemplaza `frontend/dist`. Se conserva la copia anterior y se registra `backend/config/frontend-build.json`, ligado a hashes del código fuente y los archivos generados. `iniciar.bat` valida ese recibo; un build ausente o de otra fuente requiere repetir `instalar.bat`.

Para instalaciones nuevas se descarga el ZIP oficial de MySQL (aproximadamente 281 MB), se comprueba su SHA256 y el de `mysqld.exe`/`mysqladmin.exe`, se extrae temporalmente y se publica sólo si las validaciones pasan. El ZIP 8.4.13 se verificó con la firma GPG de MySQL Release Engineering, huella `BCA43417C3B485DD128EC6D4B7B3B788A8D3785C`. Se conservan las licencias del fabricante y se crea una base propia en el puerto 3308. No se usan las bases de WAMP.

**Ollama y sus modelos siguen siendo externos:** deben estar activos localmente con `gemma4:latest` y `embeddinggemma:latest` disponibles. Se validan su versión mediante consulta OSV, inventario, digests completos, generación y embeddings; no se instala otra instancia ni se reemplazan sus pesos. La consulta OSV de Ollama no inspecciona todas las bibliotecas incorporadas a su ejecutable. Los controles detectan problemas conocidos y alteraciones verificables; no garantizan ausencia de vulnerabilidades desconocidas. El OCR continúa siendo opcional y requiere los ejecutables locales indicados en la sección de conocimiento.

Reserve espacio para los runtimes propios, índices y respaldos; el instalador exige al menos 10 GiB libres inicialmente y 5 GiB para operación/reinstalación. No reserva ni duplica los pesos de su Ollama. Estos mínimos no garantizan espacio para cualquier corpus. RAM/VRAM y tiempo de respuesta dependen del modelo instalado y del contexto; el instalador ejecuta pruebas de inferencia y embeddings para detectar incompatibilidad o falta de recursos. No se promete funcionamiento de Gemma en cualquier equipo.

1. Detenga Matrix desde su carpeta actual; conserve esa carpeta y sus respaldos. Extraiga **el ZIP completo en una carpeta nueva**: `D:\wamp64\www\matrix-rh-1.4.2`. No superponga archivos sobre otra versión.
2. Inicie WampServer y su aplicación Ollama. Confirme que Apache funciona y que `ollama list` muestra `gemma4:latest` y `embeddinggemma:latest`.
3. Clic derecho en `instalar.bat` → **Ejecutar como administrador**. Use la misma cuenta de Windows y elevación para los cuatro BAT: necesitan consultar la identidad de procesos elevados y operar el servicio Apache. No se elevan silenciosamente.
4. Espere la validación de Visual C++, la preparación de MySQL/Python/Node, las auditorías de paquetes y la compilación de la interfaz. Crea configuración y claves únicas, comprueba su Ollama antes de descargar dependencias propias o inicializar MySQL, verifica los modelos instalados, comprueba coherencia de datos, migra la base, crea el administrador e indexa documentos. Luego espera `/health` y `/ready` y abre `http://127.0.0.1:8085`.
5. Ingrese como `Matrix`. Consulte la clave inicial **sólo localmente** en `backend/config/secrets/local-admin-password.txt` y cámbiela con **Cambiar contraseña** en la interfaz. La clave del archivo deja de ser válida cuando la cambie; el archivo no revela la clave actual.
6. Ejecute `diagnosticar.bat` y las preguntas de aceptación de este README antes de dar acceso a usuarios.

Ollama debe estar abierto con el usuario de Windows que ya dispone de los modelos. Elevar Matrix no cambia el perfil ni la carpeta de modelos del servidor Ollama: Matrix usa su API HTTP, sin ejecutar su CLI como administrador. Los servicios propios de Matrix se ejecutan bajo la cuenta del operador. No se instala una tarea de arranque automático al reiniciar Windows: ejecute `iniciar.bat`.

Repetir `instalar.bat` conserva credenciales, base y revisiones de modelos presentes; no adopta bases ajenas ni sustituye datos incompletos. Un recibo persistente en `backend/config/mysql-initialized.json` impide reinicializar MySQL si se pierde su datadir; `iniciar` nunca inicializa bases. Si faltan secretos o archivos de una instalación con datos, se detiene para que pueda restaurar un respaldo coherente. Una carpeta incompleta de runtime también se conserva para diagnóstico. Una descarga interrumpida no publica un runtime: al repetir se verifica/reutiliza el ZIP válido de `backend/runtime/downloads`. Si ya existe un MySQL propio verificado que cumple el mínimo 8.4.13, se reutiliza. Las versiones 8.0 y 8.4 anteriores al mínimo se bloquean por seguridad; se conservan sus binarios y datos y requieren respaldo y actualización controlada antes de continuar. No se actualiza automáticamente su datadir. Datos sin su runtime correspondiente requieren restaurar ambos desde un respaldo coherente.

Si MySQL tarda más que el tiempo de espera, el reintento comprueba su identidad y acceso autenticado antes de completar la inicialización y retirar el SQL temporal. El cliente de control usa su archivo de opciones privado y desactiva la lectura de login paths externos. Para el bloqueo de 1.4.1 que exigía MySQL dentro de WAMP, conserve la carpeta anterior y extraiga esta entrega completa en otra carpeta bajo `www`; luego ejecute `instalar.bat` como administrador. No necesita agregar un complemento MySQL a WAMP.

### Comandos y puertos

Ejecute desde una consola como administrador en la carpeta del proyecto, o con clic derecho → Ejecutar como administrador:

| Comando | Función |
|---|---|
| `instalar.bat` | Validar existentes, preparar faltantes, auditar, compilar, migrar, indexar y arrancar; requiere administrador |
| `iniciar.bat` | Comprobar Ollama existente → MySQL → preflight/API → Apache, salud y navegador |
| `detener.bat` | Parar API y MySQL propios; conservar datos y dejar Ollama/Apache activos |
| `diagnosticar.bat` | Resumen OK/FALLA de configuración, puertos, procesos, espacio, modelos, API, corpus y últimos errores |

| Servicio Windows | Puerto / persistencia |
|---|---|
| Apache WAMP / aplicación | `127.0.0.1:8085`; include propio en `backend/config/apache` |
| API | `127.0.0.1:8000`; interfaz compilada y API desde FastAPI |
| MySQL propio | `127.0.0.1:3308`; `knowledge-base/state/mysql` |
| Ollama existente | `127.0.0.1:11434`; pesos en su ubicación actual, administrados por Ollama |
| Qdrant embebido | Sin puerto; `knowledge-base/state/qdrant`, un solo proceso propietario |
| Adjuntos / registros | `knowledge-base/state/uploads` / `backend/logs` |

Los BAT usan `%~dp0`. Los puertos son configurables en `backend/config/.env` y deben ser distintos. Si cambia el puerto web, repita la instalación para regenerar el include Apache. El puerto de Ollama debe estar ocupado por su servicio existente; se comprueba mediante su API. Matrix no adopta ni detiene ese proceso. Para sus propios servicios comprueba PID, ejecutable e identidad antes de reutilizarlos o detenerlos. Diagnóstico distingue modelos instalados de modelos cargados; un modelo descargado de memoria no equivale a uno perdido.

Fuentes técnicas de MySQL: [ZIP oficial de Windows](https://dev.mysql.com/downloads/mysql/8.4.html?os=3), [instalación y prerrequisitos](https://dev.mysql.com/doc/refman/8.4/en/windows-installation.html) y [verificación de firmas](https://dev.mysql.com/doc/refman/8.4/en/checking-gpg-signature.html).

## Configuración del modelo y RAG

Edite `backend/config/.env` con Matrix detenido. La plantilla `env.example` documenta variables; nunca la copie sobre una configuración existente. Secretos y configuración no se publican por HTTP ni se incluyen en el ZIP.

Configuración Windows generada en `backend/config/.env`:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_FAST_MODEL=gemma4:latest
OLLAMA_DEEP_MODEL=gemma4:latest
OLLAMA_EMBEDDING_MODEL=embeddinggemma:latest
```

Si su Ollama escucha en otro puerto local, edite sólo `OLLAMA_BASE_URL` en ese archivo y repita `instalar.bat`. No hace falta indicar la ruta de los pesos. Puede comprobar la API sin modificarla con `Invoke-RestMethod http://127.0.0.1:11434/api/tags` en PowerShell. Matrix no cambia los ajustes globales de Ollama, su concurrencia ni su arranque; el servicio puede seguir usándose en otras aplicaciones. Referencias técnicas: [API de inventario](https://docs.ollama.com/api/tags) y [configuración de Ollama](https://docs.ollama.com/faq).

Se conservan `gemma4:latest` para generación y `embeddinggemma:latest` para embeddings de 768 dimensiones. El instalador comprueba esas etiquetas exactas y fija una sola vez los digests completos que devuelve su Ollama. Los identificadores abreviados de `ollama list` no se usan como SHA256 completo. Si falta un modelo, muestra qué etiqueta falta y se detiene sin descargar ni sustituir nada. `latest` es mutable en el registro: el pin local detecta sustituciones y una reinstalación no autoriza actualizar pesos. Cambiar embeddings exige reconstruir un índice compatible; no elimine colecciones o banderas SQL manualmente.

| Parámetro | Valor inicial | Motivo |
|---|---|---|
| Temperatura documental / resumen | `0.1` / `0.1` | Menor variación en hechos y citas |
| Temperatura general / regeneración | `0.25` / `0.03` | Flexibilidad conceptual; reparación conservadora |
| `top_p` / `OLLAMA_TOP_K` | `0.9` / `40` | Muestreo explícito y reproducible por configuración |
| Penalización de repetición | `1.0` | Evitar alterar repeticiones legítimas de cifras o términos; ajustar tras medir |
| Contexto fast/deep | `8192` tokens | Presupuesto acotado para instrucciones, historial y evidencia; no equivale a 8192 tokens de PDF |
| Salida fast/deep | `1536` / `3072` tokens | Respuestas y resúmenes acotados; la reserva de salida reduce el espacio de entrada |
| Chunks / solapamiento | `900` / `120` tokens | Conservar contexto próximo y límites de página/sección; tablas tienen tratamiento específico |
| Recuperación final / candidatos | `6` / `24` | Diversidad y selección dentro del documento y permisos aplicables |
| Similitud mínima / MMR | `0.35` / `0.65` | Mantener recuperación útil sin bajar indiscriminadamente el filtro |
| Resúmenes | Hasta `256` chunks examinados; lotes de `24` | Recorrido acotado con cobertura declarada, sin introducir un PDF completo a un único prompt |
| Salida documental | JSON interno por afirmación, citas y límites | Estructura validable que se presenta como texto legible |

En los registros aportados sí se recuperaban seis fragmentos, con similitudes próximas a 0.52–0.59. Por tanto, aquellos fallos no demostraban que el modelo no leyera los PDF: varios ocurrieron **después de la recuperación**, al validar citas, JSON o la aplicación numérica del caso.

La corrección selecciona el documento nombrado dentro del conjunto autorizado antes del ranking global; si el título es ambiguo solicita precisión. Los resúmenes respetan la sección solicitada y la cantidad de puntos cuando hay evidencia suficiente. El seguimiento conserva la referencia documental sin arrastrar automáticamente edades o antigüedades de preguntas anteriores. Las tablas distinguen rangos publicados de la antigüedad declarada por el usuario.

El prompt prioriza las fuentes, admite respuestas parciales e identifica información faltante. Tras un intento de reparación, se pueden publicar afirmaciones independientes que conservan sus condiciones y pasan la validación, junto con un límite explícito. Para resúmenes puede recurrir a extractos citados de la sección seleccionada; los cálculos acotados pueden resolverse mediante la fila y regla verificadas. Las citas inventadas y cifras incompatibles siguen sin publicarse. Si ningún hecho queda respaldado, la aplicación devuelve una limitación controlada; no presenta un error de formato como una caída de toda la conversación ni inventa una respuesta. Los errores reales de conexión, memoria o extracción siguen siendo errores diagnosticables.

Estas validaciones comprueban contratos, referencias y reglas numéricas; no prueban por sí solas que toda paráfrasis sea semánticamente correcta. La aceptación con los documentos y el modelo reales sigue siendo necesaria.

Para un equipo con 8 GB de VRAM, pruebe inicialmente `INFERENCE_MAX_INFLIGHT=1`, `CHAT_MAX_INFLIGHT=2` y contexto fast/deep de 8192, conservando el checkpoint fijado. Compruebe con `ollama ps` la carga real en GPU. Con 64 GB de VRAM, compare candidatos locales de mayor tamaño con contexto inicial de 16384 y una inferencia simultánea; aumente la concurrencia solo después de medir. Son perfiles de evaluación, no garantías de capacidad: la RAM del equipo no equivale a VRAM. Los límites de concurrencia de Matrix y los del servicio Ollama son independientes. Antes de cambiar un modelo o los parámetros de recuperación, mida con preguntas anotadas de los PDF reales la cobertura de tablas, las fuentes recuperadas, las respuestas sin respaldo y la latencia.

### Agregar y actualizar conocimiento

Coloque documentos autorizados en `knowledge-base/documents/<categoria>/`. Las categorías deben estar declaradas en `backend/config/authorization/categories.yaml` y el usuario debe tener la concesión correspondiente. No se indexan `state`, `models`, `backups`, `unclassified` ni los fixtures de pruebas. Los adjuntos de una conversación son privados y no pasan automáticamente al corpus corporativo.

El sincronizador detecta altas estables cada 60 segundos; espera al menos 10 segundos sin cambios de tamaño/fecha. La reconciliación completa tiene intervalo de 24 horas. Para reconciliar inmediatamente modificaciones o bajas, detenga Matrix y, desde la raíz, ejecute esta secuencia de PowerShell:

```powershell
.\detener.bat
.\instalar.bat
```

La instalación repetida reconcilia la ingesta manteniendo los usuarios. No abra simultáneamente Qdrant embebido desde otro proceso. No borre originales pensando que sólo borra caché: una reconciliación aplica cambios documentales y debe responder a una baja autorizada.

Los PDF conservan su texto digital y sus localizadores de página. También se detectan imágenes en páginas que contienen texto: una tabla incrustada como imagen no se considera leída por el mero hecho de extraer su encabezado. Si el OCR está desactivado, se registra un aviso de cobertura pendiente; el texto digital sigue disponible.

Para recuperar imágenes y páginas escaneadas, instale previamente Tesseract con el idioma español y Poppler (`tesseract` y `pdftoppm` en `PATH`), active `EXTRACTION_OCR_ENABLED=true` en la configuración privada y repita la reconciliación con Matrix detenido. No hay descargas ni llamadas a servicios externos desde el extractor. En páginas mixtas el OCR trabaja sobre una copia sin texto digital, solicita conservar los espacios entre columnas y complementa el texto original. Sus resultados deben contrastarse con la imagen, especialmente cifras y tablas: el reconocimiento no acredita exactitud ni reconstruye automáticamente cualquier tabla. Si faltan los binarios, el idioma o texto reconocible, se conserva lo extraído y se registra la limitación. Los límites de tiempo y memoria del worker siguen aplicándose. En la comprobación local, los PDF completos de prestaciones y pensiones tardaron aproximadamente 56 y 27 segundos con OCR; el primero excede el límite predeterminado de 30 segundos. Para evaluar este corpus con OCR, use `EXTRACTION_TIMEOUT_SECONDS=120` y vuelva a medir en el equipo de destino. Se recuperó texto de las tablas de la página 8, pero persistieron errores en rangos de antigüedad y códigos de fondos: estas tablas requieren una transcripción revisada antes de usar sus cifras como evidencia fiable.

La revisión de ingesta **v10** conserva el tratamiento de imágenes, los encabezados explícitos `CHUNK N — sección` y los títulos de embedding con archivo y sección. Añade filas de Excel indivisibles, tablas Markdown con o sin barras exteriores y bloques de código delimitados. Cambia la huella del índice: actualice el código completo y su manifiesto, detenga Matrix y ejecute `instalar.bat` para aplicar la migración SQL 0012 y reconstruir documentos y adjuntos mediante el reconciliador existente. No borre colecciones ni cambie indicadores SQL manualmente. Hasta terminar la reconstrucción, los vectores de una huella anterior no se utilizan. Cambiar solo el modelo generador no requiere esta reindexación.

Se admiten Word `.docx`, Excel `.xlsx`, PDF `.pdf` y Markdown `.md`, además de los formatos ya disponibles TXT, CSV y PPTX. Word mantiene el orden de párrafos/tablas y su localizador; las tablas se representan como Markdown. Excel conserva hoja, número físico de fila y pares `columna: valor`, sin separar los valores de sus etiquetas entre fragmentos. Una hoja vacía no genera evidencia. Las fórmulas se leen por su resultado guardado, sin ejecutarlas: si falta ese resultado, se avisa para recalcular y guardar el libro antes de cargarlo. Los formatos antiguos `.doc`, `.xls` y `.ppt` requieren conversión local al formato moderno correspondiente.

Markdown hace explícitas las secciones, listas y tablas; su extensión por sí sola no mejora la precisión del modelo. Conserve los encabezados, unidades, condiciones y referencias al preparar documentos. El extractor no ejecuta código ni sigue enlaces del Markdown. Si una sola fila de Excel o línea de código excede el presupuesto de un fragmento, la indexación falla de forma explícita para evitar perder su significado; revise ese contenido antes de volver a cargarlo. Los límites de archivos, hojas, filas, columnas, tiempo y memoria siguen vigentes.

La recuperación por documento restringe también el filtro SQL por identidad y generación. Al leer varios documentos, se comprueba su vigencia en una consulta SQL final; las escrituras vectoriales usan lotes de hasta 64 fragmentos. La migración 0012 añade índices compuestos para las consultas corporativas y privadas sin modificar datos. En una prueba MySQL 8.4.13 con 20.000 documentos sintéticos, `EXPLAIN ANALYZE` pasó de recorrer 20.000/10.000 registros a 100/100 respectivamente, conservando los resultados. La lectura de ocho documentos usa dos consultas SQL de generaciones/vigencia en lugar de nueve, aparte de la consulta del catálogo. Estas mediciones no sustituyen una prueba de carga con el corpus y hardware de destino.

Validación de esta revisión: 3.057 pruebas y 40 subpruebas aprobadas en la ejecución general; los 156 casos omitidos por necesitar servicios se cubrieron en la ejecución separada de 337 pruebas con MySQL real. Cuatro pruebas adicionales comprobaron DOCX, XLSX, PDF y MD a través del worker aislado. Ruff y Mypy aprobaron; Bandit no añadió hallazgos respecto de la auditoría anterior. La migración se verificó sobre 20.000 registros, conservando resultados y sin cambios pendientes al repetirla. No se midió la latencia de generación con la GPU de destino ni la concurrencia contra Qdrant servidor; se conserva la configuración de modelos y conexiones existente.

## Usuarios, permisos y contraseñas

`Matrix` se crea una sola vez con rol `matrix_admin`, permisos de administración de conocimiento/usuarios y diagnóstico. El comodín cubre sólo categorías con `wildcard_eligible: true`; no otorga acceso implícito a nómina confidencial, investigaciones ni otros dominios restringidos. `prestaciones_reader` permite leer prestaciones sin administrar. No se crea un lector con contraseña pública y no hay una clave universal.

Desde la carpeta `backend`, un operador autorizado puede crear otro usuario o cambiar su clave con entrada oculta:

```powershell
.\runtime\venv\Scripts\python.exe -m scripts.local_identity create-user --username ConsultaRH --role prestaciones_reader
.\runtime\venv\Scripts\python.exe -m scripts.local_identity change-password --username Matrix
```

Cambiar una contraseña invalida las sesiones anteriores. La interfaz solicita la contraseña actual; el comando local es una herramienta de administración con acceso al equipo y a la configuración privada. No use `MATRIX_SEED_PASSWORD` para recuperar contraseñas reales ni habilite `LOCAL_TEST_*`.

## Preguntas de aceptación

Use una conversación nueva y después mantenga la misma conversación para los turnos 2–5. Las expectativas siguientes se verificaron contra el texto de las páginas 9–10 del PDF incluido; son pruebas del documento histórico, no una afirmación sobre políticas actuales.

| # | Pregunta | Resultado esperado |
|---|---|---|
| 1 | Según “Plática del Plan de Pensiones por Jubilación de diciembre de 2022”, resume la sección de portabilidad en cinco puntos. Indica las páginas utilizadas. | Cinco puntos sobre portabilidad; fuentes de páginas 9–10, sin resumen de antecedentes ni volcado de todo el PDF |
| 2 | Según ese documento, ¿qué porcentaje corresponde a quienes ingresaron a partir del 1 de abril de 2016 y tienen entre 7 y 7.99 años? ¿A cuáles aportaciones aplica? | 70% para básica, básica complementaria y adicional complementaria; página 9 |
| 3 | Ingresé en mayo de 2016 y salgo antes de jubilarme con 7 años y 6 meses de antigüedad. Según ese documento, ¿qué porcentaje corresponde y por qué? | 7.5 años dentro de 7–7.99; 70%; página 9; condicionado a los datos declarados |
| 4 | Mantén mi fecha de ingreso y las demás condiciones, pero cambia la antigüedad a 8 años y 6 meses. ¿Qué porcentaje corresponde ahora? | 8.5 años → 80%; no reutilizar 7.5 ni 70%; página 9 |
| 5 | Según ese documento, por renuncia voluntaria antes de jubilarme, ¿a dónde pueden transferirse los fondos y con qué restricciones? | AFORE del empleado, sólo Profuturo, o PPR del empleado; página 10 |
| Límite | Según ese documento, ¿puedo transferirlo a cualquier AFORE y cuál será el saldo exacto de mi cuenta? | Corregir “cualquier” con la restricción documentada; indicar que no dispone del saldo personal, sin negar la parte respondible |
| Adjunto | Adjunte un PDF con texto y solicite “Resume sólo este adjunto en tres puntos e indica sus páginas”. | Usar únicamente el adjunto autorizado; pedir precisión si hay varios candidatos |
| Ausencia / permiso | Pregunte por un dato inexistente o una categoría que su usuario no puede consultar. | Informar falta de evidencia accesible y sugerir precisión, sin inventar hechos ni filtrar títulos privados |

## Solución de problemas

| Síntoma | Acción |
|---|---|
| Integridad o versión incorrecta | Extraer el ZIP completo en otra carpeta; no editar `__init__.py` ni regenerar hashes para esconder diferencias |
| Puerto ocupado | Ejecutar diagnóstico; detener sólo la instancia identificada. No finalizar todos los procesos Python/Ollama |
| No abre `localhost/matrix-rh-1.4.2` | Use `http://127.0.0.1:8085`; el acceso directo a archivos está denegado deliberadamente |
| Apache no inicia | Revisar conflicto del puerto web y `backend/logs`; el instalador valida `httpd -t` y restaura la configuración si falla |
| SQL conserva índices pero faltan colecciones/adjuntos | Restaurar un respaldo conjunto. No forzar ingesta ni eliminar indicadores SQL para omitir el problema |
| Documento no aparece | Revisar categoría, permisos, estabilidad del archivo y extracción de texto; no confundir “archivo existente” con “índice publicado” |
| Respuesta parcial o validación repetida | Conservar referencia y ejecutar diagnóstico de solicitud; revisar motivo concreto y evidencia antes de cambiar parámetros |
| Ollama no responde | Abra su Ollama habitual; compruebe `/api/tags` y el puerto en `OLLAMA_BASE_URL`. Matrix no inicia una segunda instancia |
| Falta un modelo | Compare las etiquetas exactas de `ollama list` con `.env`. No hay descargas automáticas ni sustitución por otro modelo |
| Timeout/memoria del modelo | Revisar logs y recursos; bajar contexto o evaluar otro modelo de forma controlada. No aumentar simultáneamente contexto y concurrencia |
| Credenciales perdidas | Usar cambio de contraseña local autorizado. No borrar la base ni regenerar secretos de una instalación existente |
| WAMP sólo tiene MariaDB, otro MySQL o ninguno | Es compatible con este instalador: Matrix prepara MySQL propio desde el ZIP oficial; sólo necesita Apache de WAMP |
| Instalación incompleta / componentes PENDIENTE | El diagnóstico muestra la última etapa en `knowledge-base/state/run/installation-progress.json`; corrija el primer error del instalador y repita `instalar.bat`. No interpreta servicios aún no instalados como cinco fallos independientes |
| MySQL no puede ejecutar `--version` | Compruebe el redistribuible Visual C++ x64 y si el antivirus bloqueó un ejecutable; los datos aún no se inicializaron |
| Descarga MySQL bloqueada | Permita HTTPS al CDN oficial según su política. Alternativa: coloque el ZIP exacto `mysql-8.4.13-winx64.zip` en `backend/runtime/downloads` y repita; se exige el mismo SHA256 del manifiesto |

Para una referencia fallida, desde la raíz:

```powershell
& .\backend\runtime\venv\Scripts\python.exe -B `
  .\backend\scripts\diagnosticar_solicitud.py `
  --root (Get-Location).Path --reference 'PEGUE_AQUI_LA_REFERENCIA'
```

No comparta `.env`, claves ni documentos privados al solicitar soporte. El diagnóstico de solicitud resume eventos sin publicar el borrador completo.

## Respaldo, restauración y actualización

Primero ejecute `detener.bat`. Para un respaldo consistente desde la carpeta `backend`:

```powershell
.\runtime\venv\Scripts\python.exe -m scripts.installation_backup backup
.\runtime\venv\Scripts\python.exe -m scripts.installation_backup verify --backup '..\knowledge-base\backups\RUTA_IMPRESA_POR_EL_COMANDO'
```

El comando imprime el destino real. Sustituya el marcador por esa ruta, sin copiarlo literalmente. Copia código, configuración privada, credenciales, runtime MySQL correspondiente al datadir, originales, adjuntos, índices y SQL **en frío**. Ollama externo puede seguir activo: sus binarios y pesos fuera del proyecto no se incluyen en el respaldo. Consérvelos o respáldelos por separado y compruebe que sus revisiones coincidan al restaurar. Si quedan pesos antiguos dentro del proyecto, se conservan en la copia sin utilizarlos. Comprueba los procesos y puertos propios, rechaza enlaces/junctions y verifica cada archivo mediante SHA256. No detiene servicios ni declara que un hash pruebe recuperación SQL. El respaldo contiene secretos: restrinja su acceso y protéjalo según la política de la organización. Una copia en el mismo disco no protege de una avería de ese disco.

Restaure siempre el directorio `installation` completo a una carpeta nueva y vacía. Conserve juntos MySQL, su recibo de versión y su datadir. Una reubicación requiere regenerar el entorno Python y rutas locales/Apache; no copie únicamente SQL ni únicamente Qdrant. La herramienta verifica la copia, no automatiza una restauración destructiva. Valide la restauración en el equipo destino antes de confiar en el procedimiento.

Para actualizar: respaldo frío verificado, ZIP completo nuevo, conservación conjunta del estado compatible y configuración, reinstalación y prueba de aceptación. Esta versión no ejecuta un traslado automático desde 1.3.x: ese flujo se retiró tras los incidentes de carpetas borradas y estado incompleto. Mantenga la instalación anterior para una migración supervisada o inicie una base nueva con los documentos originales. Nunca borre la anterior antes de verificar usuarios, archivos y consultas en la nueva.

## Docker opcional: Linux

**Estado de aceptación (25-oct-2026):** este perfil sigue pendiente de remediación y validación operativa antes de un nuevo release. El informe detallado y el plan de actualización se entregan por separado al propietario. El perfil Windows usa Qdrant embebido.

**Este perfil Linux es opcional y distinto del solicitado para Windows/WAMP; ninguno de los cuatro BAT lo ejecuta.** Sus comandos de descarga requieren una ejecución manual explícita y no utilizan el Ollama de Windows. Para reutilizar sus modelos actuales utilice la instalación Windows descrita arriba. La operación Windows/WAMP no utiliza Docker. La alternativa contenedorizada se prepara sobre una copia separada en un sistema de archivos Linux; no monte el datadir MySQL ni Qdrant de Windows en esos contenedores. Sus datos, configuración y credenciales son independientes dentro de la copia Linux.

**Pendiente de remediación de terceros (20 de octubre de 2026):** el escaneo de las imágenes fijadas encontró vulnerabilidades altas/críticas en paquetes de MySQL, Qdrant y Ollama, y altas en las bases Python, Node y Nginx. Actualizar Node/Nginx reduce hallazgos, pero no los elimina. Este perfil no tiene una aceptación de seguridad aprobada. Mantenga sus puertos internos y no lo publique en Internet; las imágenes de bases de datos requieren respaldo y migración verificada. Los BAT de Windows no utilizan estas imágenes.

Requiere Docker Engine y el plugin Compose v2; no necesita Python/Node en el host. Ejecute como operador Linux sin privilegios de root, con permiso para Docker, desde la raíz de esa copia:

```bash
docker version
docker compose version
docker build -f backend/Dockerfile -t matrix-rh-backend:1.4.2 .
docker run --rm --network none --user "$(id -u):$(id -g)" --mount "type=bind,source=$(pwd),target=/project" --entrypoint python matrix-rh-backend:1.4.2 -m scripts.docker_prepare --root /project
docker compose --env-file backend/config/docker.env --profile prepare down
docker compose --env-file backend/config/docker.env --profile prepare run --rm --no-deps model-download
docker compose --env-file backend/config/docker.env --profile prepare run --rm prepare
docker compose --env-file backend/config/docker.env up -d --wait --wait-timeout 600
```

Abra `https://127.0.0.1:8443`. Usuario inicial `Matrix`; clave en `backend/config/secrets/docker/bootstrap-admin-password.txt`. El certificado es autofirmado local: revise su procedencia antes de aceptarlo en el navegador; no es un certificado corporativo confiable. El perfil se declara `development` con identidad local real, HTTPS y cookies seguras; no inventa certificaciones de cifrado del disco o respaldo.

La preparación crea `backend/config/docker.env` con permisos privados y nombre único persistente de instancia. Conserva claves y pins existentes. Verifica coherencia SQL/archivos y existencia de colecciones Qdrant antes de preparar y antes de arrancar, sin afirmar que comprueba cada vector. El preflight de modelos comprueba embeddings y capacidades; la calidad de generación se acepta con las preguntas anteriores.

| Decisión Docker | Justificación |
|---|---|
| Python 3.12, Node 24.22.0, MySQL 8.4, Qdrant 1.12.4, Ollama 0.34.0 y Nginx con digest de imagen | Evitar cambios silenciosos de imágenes; dependencias de aplicación fijadas por locks |
| Estado bajo `knowledge-base/state/docker` y config privada separada | Persistencia local sin compartir datadirs Windows ni base externa |
| Redes internas separadas de datos, modelo y proxy | API/SQL/Qdrant/Ollama sin egreso; Ollama sin montar documentos ni compartir red con SQL |
| Perfil de descarga separado | Sólo la preparación de pesos necesita Internet; detenga el stack antes de ejecutarla |
| Sólo Nginx publica `127.0.0.1:8443` | Evitar puertos públicos de API, base y modelo |
| Healthchecks y dependencias saludables | API y proxy esperan a los servicios; `--wait` informa un arranque fallido |
| UID/GID del operador, filesystem de imagen de sólo lectura y capacidades eliminadas donde son compatibles | Reducir privilegios; MySQL conserva la inicialización requerida por su imagen oficial |
| Límites de RAM/CPU/PID, logs rotados y `unless-stopped` | Acotar consumo y recuperar servicios tras reinicios del daemon |

La configuración inicial reserva límites de 24 GiB/4 CPU para Ollama, 4 GiB para API y 2 GiB para cada base. Son límites, no una garantía de capacidad: ajústelos a hardware y modelo en `docker.env`. El perfil usa CPU; GPU no se activa como cambio opcional. Si la subred privada predeterminada coincide con otra red, cambie conjuntamente `MATRIX_PROXY_SUBNET` y `MATRIX_NGINX_IP` antes de preparar.

Comandos útiles:

```bash
docker compose --env-file backend/config/docker.env config --quiet
docker compose --env-file backend/config/docker.env ps
docker compose --env-file backend/config/docker.env logs --tail=100 backend qdrant ollama
docker compose --env-file backend/config/docker.env exec backend python -m scripts.bootstrap status
docker compose --env-file backend/config/docker.env down
```

`config --quiet` valida sin imprimir secretos. `down` conserva los directorios de datos. Para actualizar o reconciliar documentos: `down`, build, preparación de configuración, descarga si falta algún modelo, `prepare` y `up` en ese orden. No ejecute `model-download` mientras Ollama esté activo sobre los mismos pesos. Respalde la copia Linux completa en frío después de `down`, incluyendo config, secretos, modelos y estado; no use el comando de respaldo nativo Windows en este perfil.

## Auditoría y límites de la entrega

| Prioridad | Hallazgo y corrección |
|---|---|
| Crítica | Publicar el proyecto bajo `www` podía exponer archivos; `.htaccess` y Apache deniegan el árbol y sólo permiten proxy local |
| Crítica | El instalador exigía un MySQL concreto dentro de WAMP y se detenía antes de preparar Python; descarga oficial fijada, publicación tras validación y diagnóstico de dependencias pendientes |
| Crítica | SQL externo y estado parcial rompían actualizaciones; MySQL propio, rutas canónicas y comprobación de coherencia antes de mutaciones |
| Crítica | Cuentas de prueba no eran identidad operativa; proveedor local persistente, bloqueo de intentos, CSRF y revocación de sesiones |
| Importante | Ollama propio duplicaba el servicio y los pesos del usuario; reutilización de API local, verificación sin descargas y parada/respaldo que respetan el servicio externo |
| Importante | Título de documento aplicado tarde y resúmenes demasiado amplios; selección autorizada previa, sección y puntos explícitos |
| Importante | JSON/citas/caso numérico fallidos bloqueaban todo; reparación acotada y publicación parcial verificable sin perder condiciones |
| Importante | Un arranque tardío podía dejar el bootstrap pendiente; finalización común tras comprobar autenticación y limpieza del SQL temporal en ambos caminos de reintento |
| Importante | Muchas guías y arranques divergentes; un README, cuatro BAT, controlador único, versiones e integridad comprobables |
| Opcional, no aplicado | Acceso LAN/SSO, OCR, GPU específica y nuevos modelos; requieren definir hardware, identidad o corpus antes de activar |

Verificación de esta entrega (9 de octubre de 2026):

- [x] 2,933 pruebas de backend y 40 subpruebas aprobadas, en entorno aislado sin acceder a servicios operativos; 3 pruebas que requieren MySQL real omitidas expresamente.
- [x] 122 pruebas de interfaz aprobadas; TypeScript y compilación Vite completados.
- [x] Ruff y verificadores de autoría/documentación aprobados.
- [x] Funciones del supervisor ejecutadas con PowerShell 7 en Linux y procesos/servicios simulados: rutas con espacios/Unicode, puertos, hashes, credenciales estables, PID ajeno, junctions, pérdida de datadir, MySQL sin complemento WAMP, reinstalación sin cambiar de versión, descarga fallida y diagnóstico de preparación interrumpida.
- [x] Manifiesto SHA256 y contenido del ZIP comprobados tras extracción independiente; exactamente tres carpetas raíz, cuatro BAT y un README.
- [x] Seis PDF y diez documentos ACR conservados byte a byte; seis documentos sintéticos conservados fuera del corpus operativo.
- [x] Sin `reports`, entornos instalados, cachés, secretos, bases reales ni archivos heredados de operación en el paquete.
- [ ] Instalación real desde cero en Windows/PowerShell 5.1/WAMP, GPU y MySQL: requiere ejecución en el equipo destino.
- [ ] Respuestas reales de Gemma con el corpus, latencia y concurrencia: requiere aceptación en el equipo destino.
- [ ] Build/arranque Docker y restauración MySQL real: no ejecutados en este entorno.

Los controles de estructura y coherencia están automatizados; las casillas pendientes impiden afirmar que la entrega ya esté certificada para producción. Se comprobó también la extracción del ZIP oficial de MySQL y su recibo en un entorno temporal, sin ejecutar sus binarios Windows. El instalador realiza las comprobaciones disponibles en el equipo destino y devuelve un fallo accionable si alguna no pasa.

La entrega conserva Python/FastAPI, React/TypeScript, MySQL, Qdrant y Ollama. No hay secretos predefinidos en el código, ni dependencia de otro proyecto, una base externa o rutas de corpus fuera de esta carpeta. Las dependencias inevitables de Windows son el sistema operativo, WAMP y sus redistribuibles, su Ollama local con los dos modelos instalados, y conexión inicial para MySQL/Python/uv/paquetes. El uso de Ollama externo es explícito; Matrix no copia sus pesos ni controla su servicio. Los hashes verifican integridad; no certifican seguridad ni sustituyen una firma de esta entrega. La firma del fabricante verificada para el ZIP de MySQL es una comprobación independiente de su procedencia.

Creado por Aldo Garcia.

Comprobación posterior de la corrección RAG v9 (20 de octubre de 2026):

- 197 pruebas dirigidas aprobadas tras el ajuste final; los seis PDF conservaron su texto digital y localizadores en la extracción aislada. Ruff, documentación e integridad del código aprobados.
- La suite general registró 2.911 pruebas aprobadas, 43 omitidas, 40 subpruebas aprobadas y tres fallos reproducibles también en el código original: expectativa del usuario MySQL de pruebas, finales de línea de los BAT y selección de `.gitignore` en el paquete. Los verificadores de tipos y autoría también señalan incidencias anteriores, respectivamente en el despachador de extractores y el encabezado de `.gitignore`.
- OCR real comprobado sobre los PDF completos de prestaciones y pensiones: procesa las páginas mixtas, pero algunas cifras siguen siendo incorrectas. Los avisos de extracción no sustituyen la revisión de esas tablas. La generación con Ollama, la calidad de recuperación con embeddings reales y el rendimiento de las GPU de 8/64 GB quedan pendientes de aceptación en el equipo destino.

Revisión del instalador (20 de octubre de 2026): se sustituyó el pin MySQL 8.4.11 por 8.4.13 tras revisar el [aviso de Oracle](https://www.oracle.com/security-alerts/cpuoct2026.html), y se fijó el mínimo Apache usando los [avisos del proyecto Apache](https://httpd.apache.org/security/vulnerabilities_24.html). El lock del frontend actualiza únicamente `source-map-js` 1.2.1 a 1.2.2 para corregir [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q). Las auditorías consultadas no reportaron vulnerabilidades conocidas en las 58 dependencias Python de producción para Windows ni en el lock npm corregido. Las 277 firmas de paquetes npm instalados se verificaron. La ejecución real del instalador de Microsoft, Authenticode y los servicios Windows requieren aceptación en Windows; las pruebas PowerShell en Linux utilizan componentes y servicios simulados.

Validación final de esta revisión: 2.928 pruebas de backend/seguridad y 40 subpruebas aprobadas, 36 omitidas; las simulaciones del supervisor se ejecutaron con PowerShell 7.6.7 sobre Linux. Las 122 pruebas de interfaz y la compilación TypeScript/Vite con Node 24.22.0/npm 11.19.1 pasaron. Se comprobaron fallos de auditoría, procedencias no autorizadas, ZIP con rutas de escape, archivos npm alterados, reutilización de dependencias verificadas y recuperación del build anterior. Ruff, documentación, autoría e integridad aprobados. Las tres incidencias previas mencionadas en la revisión RAG quedaron resueltas: BAT con CRLF preservado, exclusión de metadatos Git del paquete y fixture explícita para la base de pruebas sin exigir root como usuario predeterminado.

Revisión de ciberseguridad (20 de octubre de 2026):

- Se ejecutaron ataques controlados sobre copias locales con datos sintéticos: SQLi, XSS, CSRF de sesión y de login, IDOR, escalada de rol, fuerza bruta, nombres/rutas maliciosos, MIME falso, archivos excesivos, ZIP bombs, Host/Origin falsos, cabeceras duplicadas, encuadre HTTP ambiguo y envío lento o fragmentado. Las pruebas de protocolo utilizan sockets reales con los parsers h11 y httptools; Chromium comprueba navegación, sesiones, permisos y representación de contenido malicioso. No se atacaron equipos externos ni servicios operativos.
- Se corrigieron los límites que sólo protegían multipart: JSON y demás cuerpos tienen ahora un máximo predeterminado de 64 KiB, 10 segundos de recepción total y 64 recepciones simultáneas. Multipart conserva sus límites propios. `HTTP_BODY_MAX_BYTES`, `HTTP_BODY_TIMEOUT_SECONDS` y `HTTP_MAX_INFLIGHT` permiten ajustes explícitos. Las cabeceras HTTP completas se limitan a 16 KiB. La frontera Host/Origin bloquea DNS rebinding y mutaciones desde otros sitios, incluido el login sin Content-Type; no sustituye el token CSRF ni la autorización por usuario. Se preservan loopback, el origen público configurado y el proxy de desarrollo Vite.
- Apache elimina cabeceras de proxy aportadas por clientes, comprueba los módulos `headers` y `reqtimeout`, y aplica límites antes de ASGI. El tiempo para recibir cabeceras se acota mediante [mod_reqtimeout](https://httpd.apache.org/docs/2.4/mod/mod_reqtimeout.html). Se limita el número de claves del control de intentos sin expulsar bloqueos activos, se limita la creación de estados OIDC y se rechaza el estado Unicode inválido sin error 500. Las respuestas de error conservan las cabeceras de seguridad y el identificador de solicitud.
- `certifi` queda fijado a 2026.10.19 tras el [aviso de certificados comprometidos](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2); Vite pasa a 8.2.4 por los [avisos de ejecución de comandos en Windows](https://github.com/advisories/GHSA-587h-7wmj-h5pm). Los locks auditados reportaron cero vulnerabilidades conocidas: 105 paquetes Python, incluidos extras de desarrollo, y el árbol npm. Se verificaron 276 firmas de registro npm y 81 attestations. Estos resultados tienen fecha y alcance; no certifican los binarios externos ni las imágenes Docker.
- La limpieza separa los adaptadores de chat compatible y Vertex, simplifica la recepción HTTP y elimina sombras de variables y tipos ambiguos en RAG/ingesta. Mypy pasó de 24 errores a cero en 107 módulos. No cambian los contratos públicos, la dimensión de embeddings, el formato de índices ni el esquema SQL por esta revisión. Los documentos del corpus se conservan byte a byte.

Riesgos pendientes: Trivy 0.76.0, limitado a severidades HIGH/CRITICAL, detectó 40 hallazgos en la imagen MySQL fijada, 160 en Qdrant y 51 en Ollama. Después de actualizar los digests de Node y Nginx quedan respectivamente 4 y 2; la base Python registra 44. Son coincidencias de paquetes, no una demostración de que cada CVE sea explotable desde Matrix; algunas pertenecen a herramientas de desarrollo o paquetes sin corrección publicada. Tampoco se descartan sólo por estar en una red interna. Las imágenes recientes de Qdrant y Ollama se escanearon y siguen teniendo hallazgos; no se realiza una migración automática de datos para sustituirlas. El tag MySQL 8.4 consultado aún contiene 8.4.11, mientras que el instalador Windows exige 8.4.13. El build Docker audita dependencias Python, verifica firmas npm y restringe Python a wheels del lock; esto no sanea las bibliotecas del sistema de las imágenes base. Quedan pendientes el build/arranque Docker, la aceptación Windows/WAMP/Authenticode y la evaluación del Ollama/GPU real del equipo destino. Bandit conserva 28 avisos bajos y 8 medios revisados (XML con guardas, SQL con identificadores validados y bindings internos); no reportó severidad alta. No se ocultaron esos avisos ni se declaró una certificación de seguridad.

Validación final de ciberseguridad: 3.016 pruebas de backend y 40 subpruebas aprobadas; 37 casos dependientes de SQL se omitieron en la ejecución sin base y se cubrieron después con MySQL real (34 dentro de las 179 pruebas de integración/ataques y 3 de políticas de acceso). Las 122 pruebas de interfaz, el recorrido de seguridad en Chromium, Ruff, Mypy, ESLint y TypeScript/Vite pasaron. La interfaz incluida corresponde al build de Vite 8.2.4 probado en Chromium. Se regeneró y verificó el manifiesto SHA256 de la entrega, sin incluir credenciales, bases ni entornos temporales. Los servicios de prueba se detuvieron al terminar.


Revisión de rutas ocultas (22 de octubre de 2026): se reprodujo con archivos ficticios que `/.git/HEAD` recibía el HTML de la SPA con estado 200 y que `/assets/.git/HEAD` entregaba el archivo si se había copiado dentro del build público. El primer caso no demuestra fuga Git; el segundo depende de la presencia del archivo en `frontend/dist/assets`. No se comprobó una fuga en una instalación operativa.

El middleware `backend/app/api/private_paths.py` rechaza con 404 cualquier segmento que empiece por punto antes de los estáticos y del fallback, incluidas rutas con puntos/separadores codificados y barras inversas de Windows. Conserva nombres públicos como `app.hash.js` y no examina la query. La política incluye `.well-known`, que esta aplicación no publica. Apache incorpora una denegación por URL y Nginx devuelve 404 para esos segmentos. El contexto Docker excluye también `**/.git`; el empaquetador ya excluía `.git` en cualquier nivel.

Para activar la protección en Windows hay que actualizar el código con su manifiesto y reiniciar la API. La plantilla Apache por sí sola no cambia el servicio: la regla debe estar en el include activo `backend/config/apache/matrix-rh.conf`, validarse con el `httpd.exe -t -f` de esa instalación y recargarse Apache. `Configure-Apache` regenera ese include durante la instalación; `iniciar.bat` no lo regenera. En Docker hay que reconstruir el backend y aplicar/recrear el proxy con la configuración Nginx nueva. Verifique tanto el puerto directo de la API como el proxy: `/.git/HEAD`, `/assets/.git/HEAD` y `/assets/%2egit/HEAD` deben ser rechazados (404 en API/Nginx; 403 por la denegación Apache), mientras `/health`, la navegación y un JS público deben funcionar.

Validación local: 105 pruebas dirigidas de rutas ocultas y frontera HTTP, 17 de contrato/estructura de entrega y 10 comprobaciones HTTP reales con Uvicorn aprobadas; Ruff e integridad SHA256 aprobados. No se ejecutaron los binarios Apache/Nginx ni la instalación Windows en esta revisión. Este cambio no separa las cuentas Windows/SQL, no reclasifica ni retira el corpus público y no actualiza imágenes Docker. La copia local ya usa pipeline RAG 10; su migración 0012 y reconciliación completa siguen el procedimiento de actualización descrito arriba.
