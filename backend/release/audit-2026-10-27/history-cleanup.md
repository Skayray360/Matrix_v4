<!-- Creado por Aldo Garcia. -->
# Retiro de seis PDF del historial

Fecha: 2026-10-27. Repositorio: `Skayray360/Matrix_v4`.

El usuario autorizó expresamente eliminar los seis PDF históricos. Se reescribieron las cuatro ramas, con comprobación de su SHA anterior antes de cada actualización. Una clonación nueva confirmó **cero PDF alcanzables y ninguno de los seis objetos retirados presente**. El código de las cuatro puntas de rama es idéntico antes y después del retiro. La evidencia estructurada está en [history-cleanup.json](history-cleanup.json).

Se reconstruyeron 13 commits desde el primer commit afectado, manteniendo sus árboles salvo los seis PDF, mensajes y relaciones entre padres, incluidos merges. El commit raíz no contenía PDF y se conservó. La interfaz autenticada disponible no admite fijar autor/fecha: esos campos de los commits reescritos corresponden a la cuenta que efectuó la limpieza; la identidad y fecha originales se conservaron en trailers y la autoría se acreditó con `Co-authored-by`. No había etiquetas que reescribir.

## Lo que falta en GitHub

Los PR históricos y las vistas en caché pueden conservar los objetos antiguos. Las referencias `refs/pull/*` son administradas por GitHub y no se pueden reescribir como ramas. Esta operación **no acredita la eliminación de esas copias, de forks ni de clones descargados**. No se envió una solicitud de soporte ni se divulgaron los nombres o contenidos de los documentos.

GitHub explica estas limitaciones y el trámite en su [documentación oficial](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). Su soporte decide si el contenido reúne los requisitos para eliminación de datos sensibles.

Datos preparados para una solicitud en el [portal de soporte](https://support.github.com/):

- Repositorio: `Skayray360/Matrix_v4`.
- Objetivo: purgar seis objetos PDF de referencias internas de PR, vistas en caché y almacenamiento que permanezcan después de limpiar las ramas.
- Primer commit afectado: `f6969a182119516db9bf64d68b72e8ad59cd23a8`.
- PR afectados: **3**, números **1, 2 y 3**. Sus referencias observadas se registran en el JSON de evidencia.
- Las cuatro ramas públicas están reescritas; no había tags. La clonación posterior no contiene ninguno de los seis objetos.
- El propietario deberá explicar la clasificación del contenido; esta revisión no leyó los PDF ni presupone que soporte aceptará la solicitud.

## Evitar que reaparezcan

Quien conserve un clon anterior debe volver a clonar el repositorio o trasladar únicamente sus cambios propios sobre la historia nueva. Fusionar una rama antigua volvería a introducir los PDF. No usar merges desde el historial retirado. La exclusión existente de `knowledge-base/**` continúa vigente.

La reescritura no fusionó el PR de correcciones ni modificó bases SQL o índices operativos.
