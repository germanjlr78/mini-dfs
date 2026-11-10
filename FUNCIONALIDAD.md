# Funcionalidad de MiniDFS

MiniDFS es un prototipo educativo de sistema de archivos distribuido que permite guardar, consultar, recuperar y eliminar archivos usando una interfaz de línea de comandos. El servicio central administra usuarios, metadatos y permisos; los nodos de datos almacenan los bloques y sus réplicas.

> MiniDFS está configurado para ejecutarse localmente en `127.0.0.1`. No es un sistema listo para producción.

## Componentes

| Componente | Responsabilidad |
|---|---|
| Cliente (`client/`) | Ofrece comandos para registrar e iniciar sesión, listar, subir, descargar y eliminar archivos. Divide y recompone archivos, y valida la integridad de los bloques. |
| Nodo Central (`central_node/`) | Atiende solicitudes de clientes y DataNodes, verifica sesiones, administra los metadatos y coordina las réplicas y su reparación. Escucha en `127.0.0.1:8000`. |
| Nodos de datos (`data_node/`) | Reciben solicitudes autorizadas para guardar, leer y borrar bloques. Cada nodo usa su propio directorio de almacenamiento. |
| Protocolo y configuración (`utils/`) | Define el intercambio de mensajes JSON con prefijo de longitud, la configuración de red, el tamaño de bloque y el factor de replicación. |
| Almacenamiento simulado (`storage_simulation/`) | Contiene los bloques de cada DataNode, organizados en directorios como `node_5001`. |

El Nodo Central mantiene una tabla de metadatos que relaciona cada nombre de archivo con su tamaño, propietario y bloques. Por cada bloque se registran su identificador, suma SHA-256 y nodos que guardan una copia. El contenido no se almacena en el catálogo central.

## Capacidades principales

### Cuentas y sesiones

- `register` crea una cuenta con un nombre de 3 a 32 caracteres y una contraseña de 10 a 1024 caracteres. El nombre se normaliza a minúsculas.
- Las contraseñas se almacenan como hashes PBKDF2-HMAC-SHA256 con salt individual; no se guardan en texto plano.
- `login` emite un token firmado que vence en una hora. El cliente guarda la sesión localmente y `logout` elimina ese archivo.
- Cada archivo pertenece a la cuenta que lo subió. Las operaciones de listado, descarga y eliminación verifican esa propiedad.

### Subida de archivos

1. El cliente obtiene la lista de DataNodes activos y divide el archivo en bloques de `BLOCK_SIZE` bytes.
2. Calcula el SHA-256 de cada bloque y solicita autorización al Nodo Central, que valida los metadatos y la asignación de nodos.
3. El Nodo Central entrega capacidades firmadas para escribir cada bloque en los DataNodes previstos. Estas capacidades están restringidas a una acción, un bloque y un nodo concretos.
4. El cliente transmite cada bloque a los nodos y comprueba que el checksum informado por el almacenamiento coincida.
5. Cuando se guardaron las copias requeridas, el cliente registra el archivo y sus metadatos en el Nodo Central.

Los nombres remotos deben ser únicos en el catálogo, incluso entre usuarios. Para reemplazar un archivo hay que eliminarlo primero. Una autorización de subida pendiente vence después de cinco minutos.

### Listado y descarga

- `list` devuelve los nombres y tamaños de los archivos del usuario autenticado.
- `download` pide los metadatos al Nodo Central y recupera los bloques en orden desde los DataNodes indicados.
- Si una réplica no responde o falla la verificación SHA-256, el cliente intenta otra réplica registrada.
- Antes de guardar el resultado final, el cliente verifica que su tamaño corresponda a los metadatos. La descarga se construye en un archivo temporal y se mueve al destino solo si termina correctamente.
- Si no se especifica destino, el archivo se guarda en la carpeta actual. Si la ruta indicada es una carpeta existente, se conserva el nombre remoto dentro de ella.

### Eliminación

La eliminación quita primero el registro del catálogo y luego intenta borrar cada copia de los DataNodes usando capacidades firmadas. Si algún nodo no está disponible, el cliente informa que el archivo ya no aparece en el catálogo, aunque algunas copias físicas no hayan podido borrarse.

### Disponibilidad y reparación de réplicas

- Cada DataNode envía un heartbeat cada tres segundos. El Nodo Central considera caído un nodo que no reporta durante más de nueve segundos.
- Al detectar una caída, un registro o una recuperación, el Nodo Central revisa los bloques afectados.
- Si aún existe una copia accesible e íntegra, el Nodo Central puede copiarla a un DataNode activo y actualizar las ubicaciones de las réplicas en los metadatos.
- Si una reparación no puede completarse, se vuelve a intentar periódicamente.
- La reparación no puede reconstruir un bloque si todas sus copias se perdieron o quedaron inaccesibles.

## Seguridad y comunicación

Las conexiones entre cliente, Nodo Central y DataNodes usan TLS con certificados emitidos por la CA local del proyecto; los servicios requieren certificados de cliente. Las operaciones de bloques requieren capacidades firmadas por el Nodo Central para limitar qué bloque, nodo y acción se autorizan. La integridad del contenido se comprueba con SHA-256.

Los certificados incluidos son para desarrollo local. Las claves, certificados y la clave de firma de sesiones son material sensible: no deben publicarse ni reutilizarse como credenciales de un despliegue real.

## Configuración predeterminada

Los valores se definen en `utils/config.py`:

| Parámetro | Valor | Efecto |
|---|---:|---|
| `CENTRAL_NODE_HOST` | `127.0.0.1` | Interfaz local del Nodo Central. |
| `CENTRAL_NODE_PORT` | `8000` | Puerto del Nodo Central. |
| `BLOCK_SIZE` | 4 MiB | Tamaño máximo de cada bloque de archivo. |
| `REPLICATION_FACTOR` | 2 | Número de copias requeridas por bloque. |
| `HEARTBEAT_INTERVAL` | 3 s | Frecuencia de heartbeat de cada DataNode. |
| `DATA_NODE_TIMEOUT` | 9 s | Tiempo sin heartbeat para marcar un nodo como caído. |
| `CLIENT_MAX_RETRIES` | 3 | Intentos de escritura por nodo durante una subida. |

El tamaño total de los archivos no tiene un límite propio en esta configuración, pero cada bloque debe caber dentro de `BLOCK_SIZE`.

## Ejemplo de uso

Con el Nodo Central y los DataNodes en ejecución, estos comandos se ejecutan desde la carpeta del proyecto:

```powershell
python -m client.main register ana
python -m client.main login ana
python -m client.main list
python -m client.main upload C:\ruta\al\archivo.txt
python -m client.main upload C:\ruta\al\archivo.txt --name copia.txt
python -m client.main download archivo.txt
python -m client.main download archivo.txt --output C:\ruta\descargas
python -m client.main delete archivo.txt
python -m client.main logout
```

La contraseña se solicita de forma interactiva y no se muestra en pantalla. El [README](./README.md) contiene los requisitos, la instalación y los pasos para iniciar los servicios y ejecutar las pruebas.

## Persistencia y limitaciones

- Los metadatos y la propiedad de archivos se guardan en `central_node/wal.log`, un registro de escritura anticipada que se reproduce al iniciar el Nodo Central.
- Las cuentas se almacenan en `central_node/users.sqlite3`; la clave para firmar tokens está en `central_node/session.key`.
- Los bloques viven en el almacenamiento local de cada DataNode. El WAL no es una copia de seguridad de esos contenidos.
- Los bloques huérfanos de subidas interrumpidas y las copias inaccesibles tras una eliminación no se limpian automáticamente.
- Si todos los nodos que contienen un bloque se pierden, MiniDFS no puede recuperar ese bloque.
- La configuración y los certificados están pensados para una demostración local, no proporcionan alta disponibilidad ni seguridad de producción.
