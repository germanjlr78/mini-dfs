import os
import re
import tempfile

from utils.config import STORAGE_DIR
from utils.crypto_helpers import sha256_bytes

_SAFE_BLOCK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class StorageEngine:
    def __init__(self, node_port):
        self.node_port = node_port
        self.node_dir = os.path.join(STORAGE_DIR, f"node_{node_port}")
        os.makedirs(self.node_dir, exist_ok=True)

    def _block_path(self, block_id):
        if not isinstance(block_id, str) or not _SAFE_BLOCK_ID.fullmatch(block_id):
            raise ValueError("El identificador del bloque no es válido.")
        return os.path.join(self.node_dir, block_id)

    def write_block(self, block_id, block_data):
        block_path = self._block_path(block_id)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.node_dir, delete=False) as temp_file:
                temporary_path = temp_file.name
                temp_file.write(block_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temporary_path, block_path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

        checksum = sha256_bytes(block_data)
        print(f"[Storage-{self.node_port}] Bloque '{block_id}' guardado. SHA256: {checksum[:10]}...")
        return checksum

    def read_block(self, block_id):
        block_path = self._block_path(block_id)
        if not os.path.exists(block_path):
            raise FileNotFoundError(f"El bloque {block_id} no existe en este nodo.")
        with open(block_path, "rb") as block_file:
            return block_file.read()

    def delete_block(self, block_id):
        block_path = self._block_path(block_id)
        try:
            os.remove(block_path)
        except FileNotFoundError:
            return False
        return True
