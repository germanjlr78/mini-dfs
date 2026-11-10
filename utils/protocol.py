import json
import socket
import struct
from typing import Any

MAX_MESSAGE_SIZE = 1024 * 1024


def receive_exact(sock: socket.socket, size: int) -> bytes:
    """Receive exactly size bytes or raise if the peer disconnects early."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("La conexión se cerró antes de recibir todos los datos.")
        chunks.extend(chunk)
    return bytes(chunks)


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ValueError("El mensaje supera el tamaño máximo permitido.")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def receive_message(sock: socket.socket) -> dict[str, Any]:
    size = struct.unpack("!I", receive_exact(sock, 4))[0]
    if size == 0 or size > MAX_MESSAGE_SIZE:
        raise ValueError("El tamaño del mensaje recibido no es válido.")
    message = json.loads(receive_exact(sock, size).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("El mensaje debe ser un objeto JSON.")
    return message
