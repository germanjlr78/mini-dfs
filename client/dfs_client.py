import os
import re
import socket
import ssl
import tempfile
import uuid
from pathlib import Path
from typing import Any

from client.auth import create_client_ssl_context
from utils.config import (
    BLOCK_SIZE,
    CENTRAL_NODE_HOST,
    CENTRAL_NODE_PORT,
    CLIENT_MAX_RETRIES,
    REPLICATION_FACTOR,
)
from utils.crypto_helpers import sha256_bytes, verify_sha256
from utils.protocol import receive_exact, receive_message, send_message

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")


class DFSClientError(RuntimeError):
    """Se produce cuando no se puede completar una operación de MiniDFS."""


class DFSClient:
    def __init__(
        self,
        central_host: str = CENTRAL_NODE_HOST,
        central_port: int = CENTRAL_NODE_PORT,
        timeout: float = 10,
        token: str | None = None,
    ):
        self.central_address = (central_host, central_port)
        self.timeout = timeout
        self.token = token
        self.ssl_context = create_client_ssl_context()

    def _central_request(
        self,
        request: dict[str, Any],
        authenticated: bool = True,
    ) -> dict[str, Any]:
        message = dict(request)
        if authenticated and self.token:
            message["token"] = self.token
        try:
            with socket.create_connection(self.central_address, timeout=self.timeout) as raw:
                with self.ssl_context.wrap_socket(
                    raw, server_hostname=self.central_address[0]
                ) as secure:
                    send_message(secure, message)
                    response = receive_message(secure)
        except (OSError, ssl.SSLError, ValueError, ConnectionError) as error:
            raise DFSClientError(f"No se pudo comunicar con el Nodo Central: {error}") from error
        if response.get("status") == "ERROR":
            raise DFSClientError(response.get("message", "El Nodo Central rechazó la operación."))
        if response.get("status") not in {"SUCCESS", "ACK"}:
            raise DFSClientError(f"Respuesta inesperada del Nodo Central: {response}")
        return response

    def register_account(self, username: str, password: str) -> str:
        response = self._central_request(
            {"type": "REGISTER_USER", "username": username, "password": password},
            authenticated=False,
        )
        self.token = response["token"]
        return self.token

    def login(self, username: str, password: str) -> str:
        response = self._central_request(
            {"type": "LOGIN", "username": username, "password": password},
            authenticated=False,
        )
        self.token = response["token"]
        return self.token

    def _node_request(
        self,
        node_id: str,
        request: dict[str, Any],
        payload: bytes | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        host, separator, port_text = node_id.rpartition(":")
        if not separator or not host:
            raise DFSClientError(f"Identificador de DataNode inválido: {node_id}")
        try:
            port = int(port_text)
            with socket.create_connection((host, port), timeout=self.timeout) as raw:
                with self.ssl_context.wrap_socket(raw, server_hostname=host) as secure:
                    send_message(secure, request)
                    if payload is not None:
                        secure.sendall(payload)
                    response = receive_message(secure)
                    if response.get("status") == "ERROR":
                        raise DFSClientError(
                            response.get("message", f"El DataNode {node_id} rechazó la operación.")
                        )
                    if response.get("status") != "SUCCESS":
                        raise DFSClientError(f"Respuesta inesperada de {node_id}: {response}")
                    data = b""
                    if request.get("type") == "READ_BLOCK":
                        size = response.get("size")
                        if (
                            not isinstance(size, int)
                            or isinstance(size, bool)
                            or not 0 <= size <= BLOCK_SIZE
                        ):
                            raise DFSClientError(f"Tamaño de bloque inválido recibido de {node_id}.")
                        data = receive_exact(secure, size)
                    return response, data
        except DFSClientError:
            raise
        except (OSError, ssl.SSLError, ValueError, ConnectionError) as error:
            raise DFSClientError(f"Error comunicándose con el DataNode {node_id}: {error}") from error

    def get_healthy_nodes(self) -> list[str]:
        response = self._central_request({"type": "GET_NODES"})
        nodes = response.get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
            raise DFSClientError("El Nodo Central devolvió una lista de DataNodes inválida.")
        return nodes

    def upload(
        self,
        local_path: str | os.PathLike[str],
        remote_name: str | None = None,
    ) -> dict[str, Any]:
        source = Path(local_path)
        if not source.is_file():
            raise DFSClientError(f"No se encontró el archivo local: {source}")
        filename = remote_name or source.name
        if not _SAFE_FILENAME.fullmatch(filename):
            raise DFSClientError("El nombre remoto del archivo no es válido.")

        nodes = self.get_healthy_nodes()
        if REPLICATION_FACTOR < 1:
            raise DFSClientError("REPLICATION_FACTOR debe ser al menos 1.")
        if len(nodes) < REPLICATION_FACTOR:
            raise DFSClientError(
                f"Se requieren {REPLICATION_FACTOR} DataNodes activos; solo hay {len(nodes)}."
            )

        file_size = source.stat().st_size
        plans: list[dict[str, Any]] = []
        bytes_read = 0
        with source.open("rb") as source_file:
            while True:
                block_data = source_file.read(BLOCK_SIZE)
                if not block_data:
                    break
                block_index = len(plans)
                offset = block_index % len(nodes)
                plans.append(
                    {
                        "block_id": f"{uuid.uuid4().hex}_{block_index}",
                        "nodes": nodes[offset:] + nodes[:offset],
                        "checksum": sha256_bytes(block_data),
                    }
                )
                bytes_read += len(block_data)
        if bytes_read != file_size:
            raise DFSClientError("El archivo local cambió mientras se preparaba la subida.")

        authorization = self._central_request(
            {
                "type": "AUTHORIZE_UPLOAD",
                "filename": filename,
                "size": file_size,
                "blocks": plans,
            }
        )
        upload_id = authorization.get("upload_id")
        authorized_blocks = authorization.get("blocks")
        if (
            not isinstance(upload_id, str)
            or not isinstance(authorized_blocks, list)
            or len(authorized_blocks) != len(plans)
        ):
            raise DFSClientError("La autorización de subida devuelta por el nodo central es inválida.")

        saved_blocks: list[dict[str, Any]] = []
        bytes_read = 0
        with source.open("rb") as source_file:
            for index, (plan, authorized) in enumerate(zip(plans, authorized_blocks)):
                block_data = source_file.read(BLOCK_SIZE)
                if not block_data or sha256_bytes(block_data) != plan["checksum"]:
                    raise DFSClientError("El archivo local cambió durante la subida.")
                bytes_read += len(block_data)
                capabilities = authorized.get("capabilities")
                if not isinstance(capabilities, dict):
                    raise DFSClientError("El nodo central devolvió capacidades de bloque inválidas.")

                successful_nodes: list[str] = []
                errors: list[str] = []
                for node_id in plan["nodes"]:
                    if len(successful_nodes) >= REPLICATION_FACTOR:
                        break
                    capability = capabilities.get(node_id)
                    if not isinstance(capability, str):
                        continue
                    for _ in range(max(CLIENT_MAX_RETRIES, 1)):
                        try:
                            response, _ = self._node_request(
                                node_id,
                                {
                                    "type": "WRITE_BLOCK",
                                    "block_id": plan["block_id"],
                                    "size": len(block_data),
                                    "capability": capability,
                                },
                                block_data,
                            )
                            if response.get("checksum") != plan["checksum"]:
                                raise DFSClientError(
                                    f"Checksum incorrecto al guardar el bloque en {node_id}."
                                )
                            successful_nodes.append(node_id)
                            break
                        except DFSClientError as error:
                            errors.append(str(error))
                if len(successful_nodes) < REPLICATION_FACTOR:
                    details = "; ".join(errors[-3:]) or "No hubo suficientes nodos disponibles."
                    raise DFSClientError(
                        f"No se pudo replicar el bloque {index}: "
                        f"{len(successful_nodes)}/{REPLICATION_FACTOR} copias. {details}"
                    )
                saved_blocks.append(
                    {
                        "block_id": plan["block_id"],
                        "nodes": successful_nodes,
                        "checksum": plan["checksum"],
                    }
                )
        if bytes_read != file_size:
            raise DFSClientError("El archivo local cambió mientras se estaba subiendo.")

        self._central_request(
            {
                "type": "REGISTER_FILE",
                "upload_id": upload_id,
                "filename": filename,
                "size": file_size,
                "blocks": saved_blocks,
            }
        )
        return {"filename": filename, "size": file_size, "blocks": len(saved_blocks)}

    def list_files(self) -> dict[str, dict[str, int]]:
        response = self._central_request({"type": "LIST_FILES"})
        files = response.get("files")
        if not isinstance(files, dict):
            raise DFSClientError("El Nodo Central devolvió una lista de archivos inválida.")
        return files

    def download(
        self,
        remote_name: str,
        output_path: str | os.PathLike[str] | None = None,
    ) -> Path:
        if not _SAFE_FILENAME.fullmatch(remote_name):
            raise DFSClientError("El nombre remoto del archivo no es válido.")
        response = self._central_request({"type": "GET_FILE", "filename": remote_name})
        metadata = response.get("metadata")
        if not isinstance(metadata, dict):
            raise DFSClientError("El Nodo Central devolvió metadatos inválidos.")

        output = Path(output_path) if output_path is not None else Path.cwd() / remote_name
        if output.exists() and output.is_dir():
            output = output / remote_name
        if not output.parent.is_dir():
            raise DFSClientError(f"No existe la carpeta de destino: {output.parent}")

        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=f".{output.name}.", suffix=".part", delete=False
            ) as output_file:
                temporary_path = Path(output_file.name)
                blocks = metadata.get("blocks")
                if not isinstance(blocks, list):
                    raise DFSClientError("Los metadatos no contienen una lista de bloques válida.")

                for block in blocks:
                    if not isinstance(block, dict):
                        raise DFSClientError("Los metadatos contienen un bloque inválido.")
                    block_id = block.get("block_id")
                    node_ids = block.get("nodes")
                    expected_checksum = block.get("checksum")
                    capabilities = block.get("capabilities")
                    if (
                        not isinstance(block_id, str)
                        or not isinstance(node_ids, list)
                        or not all(isinstance(node_id, str) for node_id in node_ids)
                        or not isinstance(expected_checksum, str)
                        or not isinstance(capabilities, dict)
                    ):
                        raise DFSClientError("Los metadatos contienen una ubicación de bloque inválida.")

                    last_error = "No hay DataNodes registrados para este bloque."
                    for node_id in node_ids:
                        capability = capabilities.get(node_id)
                        if not isinstance(capability, str):
                            continue
                        try:
                            block_response, block_data = self._node_request(
                                node_id,
                                {
                                    "type": "READ_BLOCK",
                                    "block_id": block_id,
                                    "capability": capability,
                                },
                            )
                            if (
                                block_response.get("checksum") != expected_checksum
                                or not verify_sha256(block_data, expected_checksum)
                            ):
                                raise DFSClientError(
                                    f"El bloque {block_id} no superó la verificación de integridad en {node_id}."
                                )
                            output_file.write(block_data)
                            break
                        except DFSClientError as error:
                            last_error = str(error)
                    else:
                        raise DFSClientError(f"No se pudo recuperar el bloque {block_id}: {last_error}")

                output_file.flush()
                os.fsync(output_file.fileno())

            expected_size = metadata.get("size")
            if (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or temporary_path.stat().st_size != expected_size
            ):
                raise DFSClientError("El tamaño del archivo descargado no coincide con sus metadatos.")
            os.replace(temporary_path, output)
            temporary_path = None
            return output
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def delete(self, remote_name: str) -> dict[str, Any]:
        if not _SAFE_FILENAME.fullmatch(remote_name):
            raise DFSClientError("El nombre remoto del archivo no es válido.")
        response = self._central_request({"type": "DELETE_FILE", "filename": remote_name})
        blocks = response.get("blocks")
        if not isinstance(blocks, list):
            raise DFSClientError("El Nodo Central devolvió información de eliminación inválida.")
        failures: list[str] = []
        deleted_copies = 0
        for block in blocks:
            if not isinstance(block, dict):
                failures.append("El Nodo Central devolvió un bloque inválido.")
                continue
            block_id = block.get("block_id")
            capabilities = block.get("capabilities")
            if not isinstance(block_id, str) or not isinstance(capabilities, dict):
                failures.append("El Nodo Central devolvió capacidades de eliminación inválidas.")
                continue
            for node_id, capability in capabilities.items():
                if not isinstance(node_id, str) or not isinstance(capability, str):
                    failures.append(f"Capacidad inválida para el bloque {block_id}.")
                    continue
                try:
                    self._node_request(
                        node_id,
                        {
                            "type": "DELETE_BLOCK",
                            "block_id": block_id,
                            "capability": capability,
                        },
                    )
                    deleted_copies += 1
                except DFSClientError as error:
                    failures.append(f"{block_id} en {node_id}: {error}")
        if failures:
            raise DFSClientError(
                f"El archivo se quitó del catálogo, pero no se pudieron borrar todas las copias: "
                + "; ".join(failures)
            )
        return {"filename": remote_name, "deleted_copies": deleted_copies}
