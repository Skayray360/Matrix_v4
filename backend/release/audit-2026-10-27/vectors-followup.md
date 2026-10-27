<!-- Creado por Aldo Garcia. -->
# Seguimiento: historial PDF y recuperación vectorial

Fecha: 2026-10-27. Base de código tras limpiar el historial: `99c7c575c7de8259ecdc86a8d4113cedc3a19a28`.

Continuación: [pruebas dirigidas con MySQL/Qdrant y navegador](services-browser-followup.md). La evidencia aislada de esta página se conserva como registro de la primera corrección.

## Resultado

Los seis PDF se retiraron del historial de las cuatro ramas. Una clonación nueva confirmó cero PDF alcanzables y ausencia de los seis objetos. Los archivos actuales de cada rama no cambiaron por esta operación. La purga de referencias internas de PR/cachés de GitHub no está acreditada y requiere intervención del proveedor: [evidencia y solicitud preparada](history-cleanup.md).

El fallo de commit externo y la interrupción de la ingesta ya dejan una intención recuperable previa a la escritura de chunks. La limpieza conserva la generación confirmada en SQL, incluso si se perdió la respuesta del commit. No se migró el stack ni se alteraron migraciones SQL históricas.

## Protocolo implementado

1. La ingesta obtiene un bloqueo SQL por documento, incluyendo el INSERT aún no confirmado de un documento nuevo. El bloqueo dura hasta el commit o rollback del llamador; el servicio no confirma la transacción por su cuenta.
2. Antes de escribir chunks registra en Qdrant una intención sin texto, nombres de archivo ni embeddings: únicamente documento, generación, ámbito y colección. Si el registro no confirma su finalización, no escribe los chunks.
3. Upserts y borrados requieren resultado `COMPLETED` y orden `STRONG`. Un simple `ACKNOWLEDGED` no permite publicar el manifest ni retirar la intención.
4. El mantenimiento global adquiere el mismo bloqueo y vuelve a leer el estado SQL actual. Conserva la generación activa o borra exclusivamente la candidata por documento/generación/ámbito. Ante error SQL, contención o fallo Qdrant conserva el reintento; no interpreta el error como ausencia.
5. Retira la intención después de confirmar el borrado o proteger la activa. El journal persiste fuera de la transacción SQL fallida y no depende de callbacks del proceso. Los respaldos nativo y preparado ya copian todo el almacenamiento Qdrant; dos regresiones verifican que preservan también el journal.

Archivos principales: `app/database/document_locks.py`, `app/rag/generation_journal.py`, `app/ingestion/service.py`, `app/ingestion/pending_generation_cleanup.py`, `app/ingestion/reconciler.py` y `app/rag/vector_store.py`.

## Residuos anteriores al parche

El comando `python -m scripts.cleanup_vector_generations --scope corporate` (o `conversation`) diagnostica generaciones antiguas sin leer texto ni vectores. Para borrar exige `--apply --expected-manifest-count N`; SQL vacío requiere además `--allow-empty-manifest`. Revisa hasta 128 puntos por lote, devuelve cursor y preserva generaciones activas bajo bloqueo. Metadatos inválidos no autorizan un borrado.

Es mantenimiento explícito, no un barrido automático al arrancar. La [guía operativa](../../../README.md) explica revisión de configuración, paginación y uso con Qdrant embedded. No se ejecutó esta limpieza sobre documentos o índices de una instalación real.

## Validación y límites

La evidencia de ejecución está en [vectors-followup-validation.json](vectors-followup-validation.json). Se añadieron regresiones para commit externo fallido, documento nuevo cuyo INSERT se revierte, respuesta de commit perdida, escritor concurrente, caída abrupta de proceso hijo con SQLite/Qdrant persistentes, reintentos fallidos, confirmaciones incompletas y respaldo/restauración. El nuevo mantenimiento manual tiene pruebas de diagnóstico, protección de activa, ausencia SQL, errores de bloqueo, metadatos inconsistentes y serialización de cursores.

Una revisión independiente aprobó 14 variantes adversariales; la variante diferencial de caída con generación anterior comprometida se incorporó también a la suite del repositorio. La prueba termina el proceso con `os._exit`, reabre almacenamiento temporal y comprueba recuperación sin callbacks. Los modelos y datos usados son sintéticos.

El primer CI del seguimiento aprobó los 3.565 casos backend y los 252 frontend, pero bloqueó la auditoría de dependencias por [GHSA-j586-36cw-2gc2](https://github.com/advisories/GHSA-j586-36cw-2gc2), incorporado a la base de avisos de GitHub el 27 de octubre a las 15:04 UTC. Se actualizó únicamente `msgpack` de 1.2.1 a 1.2.3 y se añadió su mínimo seguro en las restricciones de uv. En este lock llega a través de `cachecontrol`, usado por la herramienta de auditoría de desarrollo. No se demostró explotación de Matrix. Los doce hashes de distribución fijados, incluido el wheel Windows CPython 3.12, se contrastaron con PyPI; no se compiló código de instalación.

SQL/Qdrant deben volver a estar disponibles y el reconciliador global debe ejecutarse; deshabilitar el scheduler requiere operación manual. Las pruebas usan SQLite y Qdrant embedded, no servidores MySQL/Qdrant productivos. No acreditan cortes de energía, failover distribuido, corrupción/destrucción del almacenamiento ni una instalación Windows. La aceptación productiva general sigue pendiente.

Referencias del contrato de almacenamiento: [MySQL locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html), [Qdrant points](https://qdrant.tech/documentation/concepts/points/) y [Qdrant consistency](https://qdrant.tech/documentation/scaling/consistency-guarantees/). La documentación no sustituye las pruebas del entorno destino.
