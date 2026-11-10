import logging
import socket
import ssl
import sys
import threading
import time

from client.auth import create_client_ssl_context
from central_node.auth_manager import TokenSigner
from data_node.storage_engine import StorageEngine
from utils.config import (
    BLOCK_SIZE,
    CA_CERT,
    CENTRAL_NODE_HOST,
    CENTRAL_NODE_PORT,
    DATANODE_CRT,
    DATANODE_KEY,
    HEARTBEAT_INTERVAL,
    SESSION_KEY,
)
from utils.crypto_helpers import sha256_bytes
from utils.protocol import receive_exact, receive_message, send_message

logger = logging.getLogger(__name__)


class DataNodeRunner:
    def __init__(self, node_port, session_key_path=SESSION_KEY):
        self.node_host = "127.0.0.1"
        self.node_port = int(node_port)
        if not 1 <= self.node_port <= 65535:
            raise ValueError("El puerto del DataNode debe estar entre 1 y 65535.")
        self.node_id = f"{self.node_host}:{self.node_port}"
        self.is_running = True
        self.storage = StorageEngine(self.node_port)
        self.token_signer = TokenSigner(secret_path=session_key_path, create=False)

    def _authorize_block_request(self, request, action):
        block_id = request.get("block_id")
        if not isinstance(block_id, str):
            raise ValueError("El identificador del bloque no es válido.")
        self.token_signer.verify_capability(
            request.get("capability", ""), action, block_id, self.node_id
        )
        return block_id

    def _setup_server_ssl_context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=DATANODE_CRT, keyfile=DATANODE_KEY)
        context.load_verify_locations(cafile=CA_CERT)
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def send_heartbeat_loop(self):
        ssl_context = create_client_ssl_context()
        print(f"[DataNode-{self.node_port}] Transmisor de heartbeats activo.")
        while self.is_running:
            try:
                with socket.create_connection(
                    (CENTRAL_NODE_HOST, CENTRAL_NODE_PORT), timeout=5
                ) as raw_socket:
                    with ssl_context.wrap_socket(
                        raw_socket, server_hostname=CENTRAL_NODE_HOST
                    ) as secure_socket:
                        send_message(
                            secure_socket,
                            {"type": "HEARTBEAT", "node_id": self.node_id},
                        )
                        response = receive_message(secure_socket)
                        if response.get("status") != "ACK":
                            logger.warning(
                                "[DataNode-%s] El Nodo Central no confirmó el heartbeat: %s",
                                self.node_port,
                                response,
                            )
            except (OSError, ssl.SSLError, ValueError, ConnectionError) as error:
                if self.is_running:
                    logger.warning(
                        "[DataNode-%s] Error al conectar con Nodo Central: %s",
                        self.node_port,
                        error,
                    )
            time.sleep(HEARTBEAT_INTERVAL)

    def _handle_storage_client(self, client_sock, address, ssl_context):
        secure_socket = None
        try:
            secure_socket = ssl_context.wrap_socket(client_sock, server_side=True)
            request = receive_message(secure_socket)
            operation = request.get("type")
            if operation == "WRITE_BLOCK":
                block_id = self._authorize_block_request(request, "WRITE_BLOCK")
                size = request.get("size")
                if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= BLOCK_SIZE:
                    raise ValueError("El tamaño del bloque no es válido.")
                block_data = receive_exact(secure_socket, size)
                checksum = self.storage.write_block(block_id, block_data)
                response = {"status": "SUCCESS", "checksum": checksum}
                send_message(secure_socket, response)
            elif operation == "READ_BLOCK":
                block_id = self._authorize_block_request(request, "READ_BLOCK")
                block_data = self.storage.read_block(block_id)
                response = {
                    "status": "SUCCESS",
                    "size": len(block_data),
                    "checksum": sha256_bytes(block_data),
                }
                send_message(secure_socket, response)
                secure_socket.sendall(block_data)
            elif operation == "DELETE_BLOCK":
                block_id = self._authorize_block_request(request, "DELETE_BLOCK")
                self.storage.delete_block(block_id)
                send_message(secure_socket, {"status": "SUCCESS"})
            else:
                send_message(
                    secure_socket,
                    {"status": "ERROR", "message": "Operación de bloque no reconocida."},
                )
        except (OSError, ssl.SSLError, ValueError, ConnectionError) as error:
            logger.warning("[DataNode-%s] Solicitud rechazada de %s: %s", self.node_port, address, error)
            if secure_socket:
                try:
                    send_message(secure_socket, {"status": "ERROR", "message": str(error)})
                except OSError:
                    logger.debug("No se pudo enviar el error al cliente %s.", address)
        except Exception as error:
            logger.exception(
                "[DataNode-%s] Error atendiendo solicitud de %s: %s",
                self.node_port,
                address,
                error,
            )
        finally:
            if secure_socket:
                secure_socket.close()
            else:
                client_sock.close()

    def start(self):
        ssl_context = self._setup_server_ssl_context()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        heartbeat_thread = None
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.node_host, self.node_port))
            listener.listen(100)
            listener.settimeout(1)
            heartbeat_thread = threading.Thread(target=self.send_heartbeat_loop, daemon=True)
            heartbeat_thread.start()
            print(f"DataNode TLS listo en {self.node_id}; almacenamiento: {self.storage.node_dir}")
            print("Presiona Ctrl+C para apagar el nodo.")

            while self.is_running:
                try:
                    client_sock, address = listener.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_storage_client,
                    args=(client_sock, address, ssl_context),
                    daemon=True,
                ).start()
        except KeyboardInterrupt:
            print(f"\nApagando DataNode {self.node_id}.")
        finally:
            self.is_running = False
            listener.close()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=HEARTBEAT_INTERVAL + 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m data_node.node_runner <puerto>")
        sys.exit(2)
    try:
        DataNodeRunner(sys.argv[1]).start()
    except (OSError, ValueError) as error:
        logger.error("No se pudo iniciar el DataNode: %s", error)
        sys.exit(1)
