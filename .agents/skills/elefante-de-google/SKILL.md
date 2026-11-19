---
name: elefante-de-google
description: >
  Adaptacion para Matrix RH del metodo Elephant-Goldfish Model (EGM):
  analizar decisiones de arquitectura con contexto, redactar especificaciones
  verificables y someterlas a revision critica independiente antes de implementar.
  Usar ante redisenos, nuevas funcionalidades, auditorias de diseno, cambios en
  RAG, autorizacion, frontend, agentes, despliegue o infraestructura.
license: Apache-2.0
metadata:
  alias: "Elefante de Google"
  upstream: "https://github.com/google/elephant-goldfish-model"
  upstream-plugin: "egm@egm"
  integration: "project-level adapter for Matrix RH"
---

# Elefante de Google — Skill de diseno y revision para Matrix RH

**Identidad local:** `elefante-de-google` ("Elefante de Google").  
**Proyecto:** Matrix RH, repositorio `Skayray360/Matrix_v4`.  
**Referencia:** [Elephant-Goldfish Model (EGM)](https://github.com/google/elephant-goldfish-model), licencia Apache-2.0. EGM es un proyecto alojado en la organizacion `google`, **no un producto oficialmente respaldado por Google**.

## Alcance y limitacion de instalacion

Esta skill es un **adaptador de instrucciones del proyecto**. No contiene ni sustituye el plugin EGM completo. Para utilizar las funciones originales (por ejemplo, `egm-brainstorm`, `egm-draft`, `egm-review`, `egm-bootstrap` y `egm-drift-report`) se debe instalar **todo** el plugin EGM, incluidos `skills/`, `agents/`, `resources/` y sus manifiestos, en un entorno compatible. Consultar [instalacion oficial](https://github.com/google/elephant-goldfish-model/blob/main/docs/harnesses.md).

**Regla de capacidad:** antes de anunciar una revision Goldfish, verificar que el entorno permite crear subagentes en contextos nuevos e independientes. Si no hay esa capacidad, **no simular un Goldfish ni afirmar que hubo revision independiente**. Se puede hacer un analisis critico ordinario y etiquetarlo como tal.

## Modo de operacion

Aplicar este flujo cuando el usuario invoque "elefante de google", pida un EGM para Matrix o solicite revision critica del diseno:

1. **Elefante — contexto comprobado.** Inspeccionar los archivos relevantes del repositorio, contratos, pruebas y decisiones vigentes. Separar hechos observados, hipotesis y requisitos solicitados; evitar basarse solo en recuerdos de conversaciones.
2. **Hipotesis y diseno.** Formular problema, objetivo, usuarios, alcance, exclusiones, alternativas, dependencias, amenazas, trade-offs, presupuesto y criterios de aceptacion observables. Proponer cambios concretos por archivo sin alterar codigo automaticamente.
3. **Goldfish — cuestionamiento independiente, solo si esta disponible.** Proveer a revisores aislados la especificacion y sus fuentes, sin trasladar la conversacion previa. Pedir evaluaciones de seguridad, privacidad, disponibilidad, complejidad, costos, compatibilidad, operabilidad y UX. Exigir hallazgos con evidencia y severidad.
4. **Resolucion.** Distinguir hallazgos confirmados de sospechas; proponer correccion, prueba de regresion, propietario del riesgo y criterio de cierre. Las decisiones no resueltas requieren aprobacion humana.
5. **Validacion y deriva.** Comparar los documentos aprobados con el codigo real. Ejecutar pruebas solo en un entorno permitido y aislado; no declarar "aprobado" si no se ejecutaron o falta evidencia.
6. **Entrega.** Ofrecer resumen ejecutivo, matriz de riesgos, cambios propuestos, pruebas y limitaciones. Mantener trazabilidad entre solicitud, archivo, modificacion y resultado.

## Criterios obligatorios de revision para Matrix RH

- **Autorizacion y aislamiento:** comprobar permisos en backend para documento, categoria, usuario, conversacion, adjuntos, y filtros aplicados al retrieval. No confundir visibilidad de frontend con autorizacion efectiva.
- **RAG y respuestas:** verificar fuentes autorizadas, citas trazables, invalidacion/reconciliacion de documentos retirados, relevancia de chunks, tratamiento de evidencia insuficiente y proteccion contra inyeccion de prompts.
- **Identidad y secretos:** verificar configuracion por ambiente, autenticacion/sesion/CSRF, controles de endpoints administrativos, manejo de errores, secretos fuera de codigo y registros sin datos sensibles.
- **Arquitectura:** evaluar FastAPI/Python, React/TypeScript, Qdrant, Ollama, MySQL, dependencias, limites de memoria y contratos entre servicios de acuerdo con el codigo actual (no asumir configuraciones historicas).
- **Despliegue:** comprobar arranque limpio de Docker y Windows/WAMP, seguridad de red, migraciones, respaldos, rollback, configuracion reproducible y ausencia de dependencias ocultas.
- **Capacidad:** estimar carga concurrente, latencia p95/p99, almacenamiento, tasa de inferencias, presupuestos de tokens y degradacion controlada; no inventar cifras medidas.
- **Calidad y auditabilidad:** verificar tests existentes, comportamiento e2e, control de regresiones, logs, metricas, respuesta ante fallas y evidencia de remediacion.
- **Privacidad:** no enviar documentos corporativos, datos personales, llaves ni contenido de RH a modelos o servicios externos sin autorizacion expresa y validacion de politica.

## Formato minimo de salida

En cada entrega indicar:

- **Estado:** `confirmado`, `parcial`, `sin evidencia` o `bloqueado`.
- **Evidencia:** ruta del archivo y linea, prueba ejecutada o documento de referencia; si no se pudo inspeccionar, declararlo.
- **Riesgo:** criticidad `critico/alto/medio/bajo`, impacto y probabilidad razonada.
- **Alternativas:** al menos una opcion conservadora y una propuesta de cambio cuando corresponda.
- **Validacion:** pasos medibles, resultados reales (nunca inventados) y prueba pendiente.
- **Decision:** `aprobar`, `revisar` o `no implementar aun` con justificacion.

## Activadores sugeridos

- "Elefante de Google: analiza el cambio de RAG antes de programar."
- "Elefante de Google: redacta el diseno tecnico de este modulo."
- "Elefante de Google: revisa los riesgos de seguridad y despliegue."
- "Elefante de Google: compara el diseno aprobado con el codigo."

## Gobernanza

Esta skill se usa en **desarrollo y revision**, no como dependencia de ejecucion de Matrix ni como agente de produccion. No modifica codigo, permisos, despliegues o secretos sin una solicitud explicita. Al instalar EGM en otro entorno, verificar la version, el origen del repositorio y las capacidades reales de delegacion. Una revision EGM no sustituye pruebas de seguridad, calidad ni aprobacion de Ciberseguridad.

**Fuentes oficiales:** [README](https://github.com/google/elephant-goldfish-model), [entornos soportados](https://github.com/google/elephant-goldfish-model/blob/main/docs/harnesses.md), [integracion Codex/ChatGPT](https://github.com/google/elephant-goldfish-model/blob/main/docs/codex.md).
