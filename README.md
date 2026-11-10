# MiniDFS

MiniDFS es un prototipo educativo de sistema de archivos distribuido para uso local. Transfiere bloques mediante TLS mutuo, replica cada bloque entre los nodos de datos activos, registra los metadatos en un registro de escritura anticipada (WAL), autentica usuarios con contraseñas y sesiones temporales, permite borrar archivos propios y repone réplicas cuando detecta una caída de nodo.

Consulta [FUNCIONALIDAD.md](./FUNCIONALIDAD.md) para una descripción de sus componentes, flujos de operación, seguridad y limitaciones.

## Requisitos

- Python 3.10 o posterior
- PowerShell en Windows
- Los certificados de desarrollo incluidos en `certificates/`

Desde la carpeta del proyecto, crea y activa un entorno virtual e instala las dependencias:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Para instalar también las herramientas de pruebas:

```powershell
python -m pip install -r requirements-dev.txt
```

Los certificados incluidos son suficientes para el uso local. Si faltan o han caducado, puedes generarlos de nuevo desde la carpeta del proyecto:

```powershell
python -m certificates.generate_certs
```

Al regenerar los certificados, se reemplazan las claves y los certificados locales existentes.

## Iniciar los servicios

Abre cuatro terminales de PowerShell. En cada una, ve a la carpeta del proyecto:

```powershell
cd C:\Users\User\Desktop\mini-dfs
```

En la primera terminal, inicia el nodo central:

```powershell
python -m central_node.server
```

En cada una de las otras tres terminales, inicia un nodo de datos:

```powershell
python -m data_node.node_runner 5001
```

```powershell
python -m data_node.node_runner 5002
```

```powershell
python -m data_node.node_runner 5003
```

Inicia primero el nodo central: crea la base de datos de usuarios y una clave privada local para firmar sesiones y permisos de bloque. Después, espera unos segundos para que los nodos de datos envíen sus primeros latidos. Mantén los cuatro procesos en ejecución mientras usas el cliente. Para detener cada proceso, presiona `Ctrl+C`.

## Cuentas y archivos

Desde otra terminal, crea una cuenta. El usuario debe tener entre 3 y 32 caracteres; la contraseña, entre 10 y 1024. Se solicitará dos veces y no se mostrará en pantalla:

```powershell
python -m client.main register ana
```

Inicia sesión para guardar un token temporal en el perfil local de Windows. La sesión dura una hora; vuelve a iniciar sesión al caducar:

```powershell
python -m client.main login ana
```

Ya con sesión activa, usa los comandos desde la carpeta del proyecto:

```powershell
python -m client.main list
python -m client.main upload C:\ruta\al\archivo.txt
python -m client.main upload C:\ruta\al\archivo.txt --name copia.txt
python -m client.main download archivo.txt
python -m client.main download archivo.txt --output C:\ruta\descargas
python -m client.main delete archivo.txt
python -m client.main logout
```

Cada archivo pertenece a la cuenta que lo subió. El listado, la descarga y la eliminación solo muestran o permiten operar sobre archivos propios. Para reemplazar un archivo, elimínalo primero y vuelve a subirlo.

Al subir, el archivo se divide en bloques de `BLOCK_SIZE` bytes. Cada bloque se almacena en `REPLICATION_FACTOR` nodos activos y se verifica su suma de comprobación SHA-256. Los nodos de datos requieren permisos firmados por el nodo central para leer, escribir o eliminar bloques. Cuando un nodo deja de enviar latidos, el central intenta copiar sus bloques desde una réplica sana a otros nodos activos y actualiza el WAL. También vuelve a revisar réplicas cuando un nodo se registra o recupera y periódicamente si una reparación anterior no pudo completarse.

La eliminación quita primero el archivo del catálogo y después borra sus copias accesibles. Si algún nodo de datos está desconectado, el cliente informa que el catálogo se actualizó, pero que no se pudieron borrar todas las copias físicas.

## Limitaciones actuales

- Es un prototipo local, no un sistema de archivos distribuido para producción. La configuración predeterminada escucha únicamente en `127.0.0.1`.
- Los certificados y claves de desarrollo se comparten entre los componentes de esta demostración; no los uses en un despliegue real.
- Los tokens de usuario duran una hora y se guardan en el perfil local del usuario. La clave que los firma (`central_node/session.key`) y la base de datos (`central_node/users.sqlite3`) son archivos locales sensibles: no los publiques ni los borres si quieres conservar las cuentas y las sesiones.
- La re-replicación necesita que quede al menos una copia íntegra del bloque y que haya nodos activos con espacio disponible. Si se pierden todas las copias, no hay recuperación de datos.
- No se limpian automáticamente los bloques huérfanos de subidas interrumpidas ni las copias inaccesibles de nodos desconectados durante una eliminación.
- El WAL conserva los metadatos y la propiedad de los archivos tras reiniciar. Los registros creados por versiones anteriores, sin propietario, se recuperan como datos heredados y no quedan disponibles para las cuentas nuevas.

## Ejecutar las pruebas automatizadas

Desde la carpeta del proyecto y con el entorno virtual activado, ejecuta toda la suite:

```powershell
python -m pytest -q
```

La suite incluye pruebas unitarias para autenticación, permisos firmados, metadatos/WAL, almacenamiento, protocolo y monitoreo de latidos, además de pruebas de integración con TLS para aislamiento entre usuarios, subida/descarga, reparación de réplicas y eliminación. Las pruebas usan directorios temporales; no deben modificar el WAL, las cuentas ni los bloques de `storage_simulation`.
