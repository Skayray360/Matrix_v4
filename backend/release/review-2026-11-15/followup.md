<!-- Creado por Aldo Garcia. -->
# Continuación de la revisión — 15 de noviembre de 2026

Base: `045e4871571e9a64752d9f15fe4f0022864accee`, rama `audit/hardening-2026-10-27`, [PR #3](https://github.com/Skayray360/Matrix_v4/pull/3). Se aplican correcciones acotadas y se recupera cobertura excluida por clasificación; no se cambia el modelo, ranking, esquema SQL ni comportamiento de recuperación vectorial. No se fusiona ni despliega.

## Hallazgos y correcciones

| Hallazgo confirmado | Impacto | Corrección aplicada |
|---|---|---|
| `windows_encryption_status` recibía UTF-8 de PowerShell sin especificar cómo decodificarlo en Python. | En Windows con otra página de códigos, una ruta Unicode podía producir un rechazo de la sonda o un error de decodificación. El respaldo exigente fallaba cerrado, no omitía el cifrado. | `encoding="utf-8"` explícito; se conserva la validación exacta de las rutas recibidas. |
| Esa misma sonda heredaba `PSModulePath` al invocar PS5.1 a través de Python desde PS7. | Podía intentar cargar módulos de una edición incompatible y no verificar el volumen. | Se excluye la variable únicamente del entorno del proceso hijo. El entorno padre permanece intacto. |
| El gate local trataba mypy como informativo, aunque CI lo exigía. | Un fallo de tipado podía no impedir un veredicto local favorable. | Mypy bloqueante y regresiones que distinguen fallo de comprobación ausente. |
| 23 pruebas locales estaban excluidas del grupo aislado por un marcador de integración global. | CI no ejecutaba 17 consultas SQLite temporales, una comprobación de cookie y cinco utilidades de migraciones, aunque no necesitaban servicios. | Marcadores acotados por clase/caso. Las pruebas con MySQL conservan su clasificación de integración. |

Código: `backend/scripts/preflight.py` (función `windows_encryption_status`) y `backend/scripts/run_quality_gate.py` (control de tipado en `build_gates`). Cobertura recuperada: `test_structured_end_to_end.py` y `test_sessions_and_migrations.py` en `backend/tests/integration/`.

## Evidencia y alcance

- Las 23 pruebas recuperadas pasan con `check_database`, `socket.connect` y `socket.connect_ex` bloqueados mediante excepciones. Usan SQLite temporal y datos sintéticos.
- Se añaden cinco regresiones: dos sobre el veredicto de tipado y tres variantes de nombre de `PSModulePath`, con rutas `Nómina/東京` y bytes UTF-8 emitidos por un proceso real. El productor de esa salida es Python sintético; no es una consulta BitLocker real.
- La revisión independiente del exportador no encontró una corrección funcional justificada. Sus 57 pruebas dirigidas aprobaron ACL, aislamiento, revalidación, preservación del índice original y limpieza. No se modificó el exportador.
- Ruff, mypy sobre 116 archivos, encabezados, documentación, manifiesto de 209 archivos operativos y Bandit de `app` aprobados. Los conjuntos dirigidos se solapan con la suite completa; no deben sumarse como casos distintos.
- La colección raíz posterior contiene 3.927 casos: 3.758 aislados y 169 de integración. La ejecución local del grupo aislado no tuvo fallos: 3.748 casos aprobados y diez omitidos por ausencia de PowerShell, por lo que el gate devuelve `INCOMPLETE`. El agregado JUnit incluye 40 subtests adicionales; no se cuentan como nuevos casos recopilados. La [evidencia local](followup-validation.json) conserva ese resultado sin convertirlo en PASS. CI debe ejecutar los 3.758 sin omisiones antes de considerar validado el commit.

La clasificación de integración pasa de 192 a 169 casos. Diez ya se ejecutan en cada modo Qdrant en CI; los otros **159** siguen pendientes de validación con sus servicios. La reclasificación no demuestra que esos 159 hayan pasado.

## Revisión de señales de scripts

Bandit produjo 37 señales en `backend/scripts`: 25 de importación/ejecución de procesos, dos de SQL, tres de direcciones de escucha/comparación, dos de aserciones, dos de constantes parecidas a contraseñas, una de ejecución del AST del harness y dos del parser JUnit local. La revisión contextual de estas señales no confirmó una vulnerabilidad explotable: se observaron comandos constantes o restringidos, identificadores SQL validados y entradas internas. Esto no acredita seguridad global de scripts ni sustituye pruebas adversariales de todas sus rutas.

No se añadieron excepciones masivas, no se ocultaron las señales y el gate Bandit continúa limitado a `app`.

## Límites que se conservan

No se accedió al corpus, bases, índices o volúmenes operativos. Siguen pendientes la instalación/actualización/restauración completa en Windows, BitLocker del destino real, evaluación con Ollama y el conjunto privado anotado. La revisión del diff fue independiente de quienes implementaron los cambios. El estado de las comprobaciones del commit publicado se consulta en la PR; los informes anteriores conservan sus resultados históricos.

Referencias primarias: [herencia de PSModulePath mediante Python](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-5.1) y [codificación de subprocess en Python 3.12](https://docs.python.org/3.12/library/subprocess.html).

Creado por Aldo Garcia.
