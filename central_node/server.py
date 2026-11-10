import logging
import socket
import ssl
import threading
import time
import uuid

from central_node.auth_manager import AuthManager
from central_node.heartbeat_monitor import HeartbeatMonitor
from central_node.metadata_manager import MetadataManager
from utils.config import (
    BLOCK_SIZE,
    CA_CERT,
    CENTRAL_CRT,
    CENTRAL_KEY,
    CENTRAL_NODE_HOST,
    CENTRAL_NODE_PORT,
    REPLICATION_FACTOR,
)
from utils.crypto_helpers import verify_sha256
from utils.protocol import receive_exact, receive_message, send_message

logger = logging.getLogger(__name__)


class CentralNodeServer:
    def __init__(self):
        self.host = CENTRAL_NODE_HOST
        self.port = CENTRAL_NODE_PORT
        self.auth_manager = AuthManager()
        self.heartbeat_monitor = HeartbeatMonitor()
        self.metadata_manager = MetadataManager()
        self.pending_uploads = {}
        self.pending_lock = threading.Lock()
        self.repair_requested = threading.Event()
        self.repair_thread = None
        self.repair_lock = threading.Lock()

    def _setup_ssl_context(self):
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_verify_locations(cafile=CA_CERT)
        context.load_cert_chain(certfile=CENTRAL_CRT, keyfile=CENTRAL_KEY)
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def _node_ssl_context(self):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=CA_CERT)
        context.load_cert_chain(certfile=CENTRAL_CRT, keyfile=CENTRAL_KEY)
        context.check_hostname = False
        return context

    def _issue_block_capabilities(self, blocks, owner, action):
        result = []
        for block in blocks:
            capabilities = {
                node_id: self.auth_manager.issue_capability(
                    owner, action, block["block_id"], node_id
                )
                for node_id in block["nodes"]
            }
            result.append({**block, "capabilities": capabilities})
        return result

    def _handle_request(self, message):
        operation = message.get("type")
        if operation == "REGISTER_USER":
            token = self.auth_manager.register(
                message.get("username"), message.get("password")
            )
            return {"status": "SUCCESS", "token": token}
        if operation == "LOGIN":
            token = self.auth_manager.login(
                message.get("username"), message.get("password")
            )
            return {"status": "SUCCESS", "token": token}
        if operation == "HEARTBEAT":
            node_id = message.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("El identificador del nodo no es válido.")
            self.heartbeat_monitor.register_heartbeat(node_id)
            return {"status": "ACK"}

        owner = self.auth_manager.authenticate(message.get("token"))
        if operation == "GET_NODES":
            return {
                "status": "SUCCESS",
                "nodes": self.heartbeat_monitor.get_healthy_nodes(),
            }
        if operation == "AUTHORIZE_UPLOAD":
            filename = message.get("filename")
            size = message.get("size")
            blocks = message.get("blocks")
            self.metadata_manager._validate_file_record(filename, size, blocks)
            expected_blocks = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
            if len(blocks) != expected_blocks:
                raise ValueError("La cantidad de bloques no coincide con el tamaño del archivo.")
            healthy_nodes = set(self.heartbeat_monitor.get_healthy_nodes())
            for block in blocks:
                block_nodes = block["nodes"]
                if (
                    len(block_nodes) < REPLICATION_FACTOR
                    or len(set(block_nodes)) != len(block_nodes)
                    or not set(block_nodes).issubset(healthy_nodes)
                ):
                    raise ValueError("La asignación de réplicas contiene DataNodes no disponibles.")
            upload_id = uuid.uuid4().hex
            with self.pending_lock:
                if filename in self.metadata_manager.list_files(owner=None) or any(
                    pending["filename"] == filename and pending["expires"] > time.time()
                    for pending in self.pending_uploads.values()
                ):
                    raise ValueError(
                        "Ese nombre de archivo ya existe o tiene una subida en curso."
                    )
                self.pending_uploads[upload_id] = {
                    "owner": owner,
                    "filename": filename,
                    "size": size,
                    "blocks": blocks,
                    "expires": time.time() + 300,
                }
            authorized_blocks = self._issue_block_capabilities(blocks, owner, "WRITE_BLOCK")
            return {
                "status": "SUCCESS",
                "upload_id": upload_id,
                "blocks": authorized_blocks,
            }
        if operation == "REGISTER_FILE":
            upload_id = message.get("upload_id")
            with self.pending_lock:
                upload = self.pending_uploads.get(upload_id)
                if upload is None or upload["expires"] <= time.time():
                    self.pending_uploads.pop(upload_id, None)
                    raise ValueError("La autorización de subida no existe o ha caducado.")
                if upload["owner"] != owner:
                    raise ValueError("La subida pertenece a otra cuenta.")
                if upload["filename"] in self.metadata_manager.list_files(owner=None):
                    raise ValueError(
                        "Ese nombre de archivo ya existe; elimínalo antes de reemplazarlo."
                    )
                if (
                    upload["filename"] != message.get("filename")
                    or upload["size"] != message.get("size")
                ):
                    raise ValueError("Los metadatos no coinciden con la subida autorizada.")
                registered_blocks = message.get("blocks")
                if not isinstance(registered_blocks, list) or len(registered_blocks) != len(
                    upload["blocks"]
                ):
                    raise ValueError("La cantidad de bloques no coincide con la subida autorizada.")
                self.metadata_manager._validate_file_record(
                    upload["filename"], upload["size"], registered_blocks
                )
                for planned, registered in zip(upload["blocks"], registered_blocks):
                    if (
                        planned["block_id"] != registered["block_id"]
                        or planned["checksum"] != registered["checksum"]
                        or len(registered["nodes"]) != REPLICATION_FACTOR
                        or len(set(registered["nodes"])) != len(registered["nodes"])
                        or not set(registered["nodes"]).issubset(set(planned["nodes"]))
                    ):
                        raise ValueError("Las réplicas registradas no coinciden con la autorización.")
                self.metadata_manager.register_file(
                    upload["filename"], upload["size"], registered_blocks, owner
                )
                del self.pending_uploads[upload_id]
            return {"status": "SUCCESS"}
        if operation == "GET_FILE":
            metadata = self.metadata_manager.get_file_metadata(message.get("filename"), owner)
            if metadata is None:
                raise ValueError("El archivo no existe o no tienes permiso para acceder a él.")
            metadata["blocks"] = self._issue_block_capabilities(
                metadata["blocks"], owner, "READ_BLOCK"
            )
            return {"status": "SUCCESS", "metadata": metadata}
        if operation == "LIST_FILES":
            return {
                "status": "SUCCESS",
                "files": self.metadata_manager.list_files(owner),
            }
        if operation == "DELETE_FILE":
            filename = message.get("filename")
            metadata = self.metadata_manager.get_file_metadata(filename, owner)
            if metadata is None:
                raise ValueError("El archivo no existe o no tienes permiso para eliminarlo.")
            deleted = self.metadata_manager.delete_file(filename, owner)
            if deleted is None:
                raise ValueError("El archivo ya no existe.")
            blocks = self._issue_block_capabilities(deleted["blocks"], owner, "DELETE_BLOCK")
            return {"status": "SUCCESS", "blocks": blocks}
        raise ValueError("Operación no reconocida.")

    def handle_client_thread(self, client_sock, addr, ssl_context):
        secure_sock = None
        try:
            secure_sock = ssl_context.wrap_socket(client_sock, server_side=True)
            message = receive_message(secure_sock)
            response = self._handle_request(message)
            send_message(secure_sock, response)
        except (OSError, ssl.SSLError, ValueError, KeyError) as error:
            logger.warning("Solicitud central rechazada desde %s: %s", addr, error)
            if secure_sock:
                try:
                    send_message(secure_sock, {"status": "ERROR", "message": str(error)})
                except OSError:
                    logger.debug("No se pudo responder al cliente %s.", addr)
        except Exception as error:
            logger.exception("Error atendiendo conexión central desde %s: %s", addr, error)
            if secure_sock:
                try:
                    send_message(
                        secure_sock,
                        {"status": "ERROR", "message": "Error interno del Nodo Central."},
                    )
                except OSError:
                    logger.debug("No se pudo responder al cliente %s.", addr)
        finally:
            if secure_sock:
                secure_sock.close()
            else:
                client_sock.close()

    def _request_repairs(self, node_id, status):
        logger.info("Cambio de estado del DataNode %s: %s; se revisarán réplicas.", node_id, status)
        self.repair_requested.set()

    def _repair_loop(self):
        while True:
            self.repair_requested.wait(timeout=30)
            self.repair_requested.clear()
            time.sleep(0.5)
            try:
                self.repair_underreplicated_blocks()
            except Exception:
                logger.exception("Falló la rutina de reparación automática de réplicas.")

    def _request_node(self, node_id, request, payload=None):
        host, separator, port_text = node_id.rpartition(":")
        if not separator or not host:
            raise ValueError(f"Identificador de DataNode inválido: {node_id}")
        with socket.create_connection((host, int(port_text)), timeout=5) as raw:
            with self._node_ssl_context().wrap_socket(raw, server_hostname=host) as secure:
                send_message(secure, request)
                if payload is not None:
                    secure.sendall(payload)
                response = receive_message(secure)
                if response.get("status") != "SUCCESS":
                    raise ValueError(response.get("message", f"El DataNode {node_id} rechazó la operación."))
                data = b""
                if request.get("type") == "READ_BLOCK":
                    size = response.get("size")
                    if not isinstance(size, int) or not 0 <= size <= BLOCK_SIZE:
                        raise ValueError("Tamaño de bloque no válido durante la reparación.")
                    data = receive_exact(secure, size)
                return response, data

    def repair_underreplicated_blocks(self):
        if not self.repair_lock.acquire(blocking=False):
            self.repair_requested.set()
            return
        try:
            healthy_nodes = self.heartbeat_monitor.get_healthy_nodes()
            healthy_set = set(healthy_nodes)
            files = self.metadata_manager.get_all_metadata()
            for filename, metadata in files.items():
                for block in metadata["blocks"]:
                    live_replicas = [node for node in block["nodes"] if node in healthy_set]
                    if not live_replicas:
                        continue
                    if live_replicas != block["nodes"]:
                        if not self.metadata_manager.replace_block_nodes(
                            filename, block["block_id"], live_replicas
                        ):
                            continue
                    if len(live_replicas) >= REPLICATION_FACTOR:
                        continue

                    data = None
                    for source_node in live_replicas:
                        try:
                            read_cap = self.auth_manager.issue_capability(
                                "system", "READ_BLOCK", block["block_id"], source_node
                            )
                            read_response, candidate_data = self._request_node(
                                source_node,
                                {
                                    "type": "READ_BLOCK",
                                    "block_id": block["block_id"],
                                    "capability": read_cap,
                                },
                            )
                            if (
                                read_response.get("checksum") != block["checksum"]
                                or not verify_sha256(candidate_data, block["checksum"])
                            ):
                                raise ValueError(
                                    f"El bloque {block['block_id']} no superó su checksum."
                                )
                            data = candidate_data
                            break
                        except (OSError, ssl.SSLError, ValueError) as error:
                            logger.warning(
                                "No se pudo leer la réplica %s del bloque %s: %s",
                                source_node,
                                block["block_id"],
                                error,
                            )
                    if data is None:
                        continue

                    for target_node in healthy_nodes:
                        if len(live_replicas) >= REPLICATION_FACTOR:
                            break
                        if target_node in live_replicas:
                            continue
                        try:
                            write_cap = self.auth_manager.issue_capability(
                                "system", "WRITE_BLOCK", block["block_id"], target_node
                            )
                            write_response, _ = self._request_node(
                                target_node,
                                {
                                    "type": "WRITE_BLOCK",
                                    "block_id": block["block_id"],
                                    "size": len(data),
                                    "capability": write_cap,
                                },
                                data,
                            )
                            if write_response.get("checksum") != block["checksum"]:
                                raise ValueError(f"Checksum incorrecto al reparar {block['block_id']}.")
                            live_replicas.append(target_node)
                            if not self.metadata_manager.replace_block_nodes(
                                filename, block["block_id"], live_replicas
                            ):
                                logger.warning(
                                    "El archivo %s cambió durante la reparación del bloque %s.",
                                    filename,
                                    block["block_id"],
                                )
                                break
                            logger.info(
                                "Bloque %s de %s replicado en %s (%s/%s).",
                                block["block_id"],
                                filename,
                                target_node,
                                len(live_replicas),
                                REPLICATION_FACTOR,
                            )
                        except (OSError, ssl.SSLError, ValueError) as error:
                            logger.warning(
                                "No se pudo crear una réplica del bloque %s de %s en %s: %s",
                                block["block_id"],
                                filename,
                                target_node,
                                error,
                            )
        finally:
            self.repair_lock.release()

    def start(self):
        self.heartbeat_monitor.start_auditing(self._request_repairs)
        self.repair_thread = threading.Thread(target=self._repair_loop, daemon=True)
        self.repair_thread.start()
        ssl_context = self._setup_ssl_context()
        raw_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_server_socket.bind((self.host, self.port))
        raw_server_socket.listen(100)
        print(f"[Central Node] Servidor TLS escuchando en {self.host}:{self.port}")
        try:
            while True:
                client_sock, addr = raw_server_socket.accept()
                threading.Thread(
                    target=self.handle_client_thread,
                    args=(client_sock, addr, ssl_context),
                    daemon=True,
                ).start()
        except KeyboardInterrupt:
            print("\nApagando el Nodo Central.")
        finally:
            raw_server_socket.close()


if __name__ == "__main__":
    CentralNodeServer().start()
