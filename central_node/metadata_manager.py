import os
import json
import threading
import re
from utils.config import BASE_DIR


_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")
_SAFE_BLOCK_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MetadataManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.file_table = {}
        self.wal_path = os.path.join(BASE_DIR, "central_node", "wal.log")
        self._recover_from_wal()

    @staticmethod
    def _validate_file_record(filename, size, blocks):
        if not isinstance(filename, str) or not _SAFE_FILENAME.fullmatch(filename):
            raise ValueError("El nombre remoto del archivo no es válido.")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("El tamaño del archivo no es válido.")
        if not isinstance(blocks, list):
            raise ValueError("La lista de bloques no es válida.")
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("La información de un bloque no es válida.")
            block_id = block.get("block_id")
            nodes = block.get("nodes")
            checksum = block.get("checksum")
            if not isinstance(block_id, str) or not _SAFE_BLOCK_ID.fullmatch(block_id):
                raise ValueError("El identificador de un bloque no es válido.")
            if not isinstance(nodes, list) or not nodes or not all(
                isinstance(node, str) and node for node in nodes
            ):
                raise ValueError("La ubicación de un bloque no es válida.")
            if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
                raise ValueError("El checksum de un bloque no es válido.")

    def _recover_from_wal(self):
        if not os.path.exists(self.wal_path):
            print("[Metadata] No se encontró bitácora WAL previa. Iniciando sistema limpio.")
            return

        print("[Recuperación] Procesando archivo WAL para restaurar metadatos...")
        with open(self.wal_path, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if line.strip():
                    try:
                        record = json.loads(line)
                        op = record.get("op")
                        if op == "REGISTER_FILE":
                            filename = record["filename"]
                            size = record["size"]
                            blocks = record["blocks"]
                            self._validate_file_record(filename, size, blocks)
                            self.file_table[filename] = {
                                "size": size,
                                "blocks": blocks,
                                "owner": record.get("owner", "legacy"),
                            }
                        elif op == "DELETE_FILE":
                            self.file_table.pop(record["filename"], None)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                        print(f"[Recuperación] Línea WAL {line_number} ignorada: {error}")
        print(f"[Recuperación] Estado restaurado. Archivos en memoria: {list(self.file_table.keys())}")

    def _write_to_wal(self, log_entry):
        with open(self.wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def register_file(self, filename, size, blocks, owner):
        self._validate_file_record(filename, size, blocks)
        if not isinstance(owner, str) or not owner:
            raise ValueError("El propietario del archivo no es válido.")
        with self.lock:
            log_entry = {
                "op": "REGISTER_FILE",
                "filename": filename,
                "size": size,
                "blocks": blocks,
                "owner": owner,
            }
            self._write_to_wal(log_entry)
            self.file_table[filename] = {
                "size": size,
                "blocks": blocks,
                "owner": owner,
            }
            print(f"[Metadata] Archivo '{filename}' registrado y guardado en WAL.")

    def get_file_metadata(self, filename, owner=None):
        with self.lock:
            metadata = self.file_table.get(filename)
            if metadata is None or (owner is not None and metadata["owner"] != owner):
                return None
            return json.loads(json.dumps(metadata))

    def list_files(self, owner=None):
        with self.lock:
            return {
                filename: {"size": metadata["size"]}
                for filename, metadata in sorted(self.file_table.items())
                if owner is None or metadata["owner"] == owner
            }

    def delete_file(self, filename, owner):
        with self.lock:
            metadata = self.file_table.get(filename)
            if metadata is None or metadata["owner"] != owner:
                return None
            self._write_to_wal({"op": "DELETE_FILE", "filename": filename, "owner": owner})
            del self.file_table[filename]
            return json.loads(json.dumps(metadata))

    def get_all_metadata(self):
        with self.lock:
            return json.loads(json.dumps(self.file_table))

    def replace_block_nodes(self, filename, block_id, nodes):
        with self.lock:
            metadata = self.file_table.get(filename)
            if metadata is None:
                return False
            updated_blocks = json.loads(json.dumps(metadata["blocks"]))
            for block in updated_blocks:
                if block["block_id"] == block_id:
                    block["nodes"] = nodes
                    break
            else:
                return False
            log_entry = {
                "op": "REGISTER_FILE",
                "filename": filename,
                "size": metadata["size"],
                "blocks": updated_blocks,
                "owner": metadata["owner"],
            }
            self._write_to_wal(log_entry)
            metadata["blocks"] = updated_blocks
            return True
