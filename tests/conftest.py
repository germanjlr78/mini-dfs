import socket
import threading
import time
from pathlib import Path

import pytest

import central_node.metadata_manager as metadata_module
import central_node.server as central_module
import data_node.node_runner as node_module
import data_node.storage_engine as storage_module
from central_node.auth_manager import AuthManager, TokenSigner
from central_node.metadata_manager import MetadataManager
from data_node.node_runner import DataNodeRunner

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def mini_dfs_cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    central_dir = tmp_path / "central_node"
    central_dir.mkdir()
    session_key = central_dir / "session.key"
    signer = TokenSigner(session_key, create=True)
    auth = AuthManager(central_dir / "users.sqlite3", signer)

    monkeypatch.setattr(metadata_module, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(storage_module, "STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(central_module, "AuthManager", lambda: auth)
    central_port = _free_port()
    monkeypatch.setattr(node_module, "CENTRAL_NODE_PORT", central_port)
    monkeypatch.setattr(central_module, "CENTRAL_NODE_PORT", central_port)
    monkeypatch.setattr(node_module, "HEARTBEAT_INTERVAL", 0.1)

    metadata = MetadataManager()
    monkeypatch.setattr(central_module, "MetadataManager", lambda: metadata)
    central = central_module.CentralNodeServer()

    central_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    central_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    central_listener.bind(("127.0.0.1", node_module.CENTRAL_NODE_PORT))
    central_listener.listen(100)
    central_listener.settimeout(0.1)
    stop_server = threading.Event()
    ssl_context = central._setup_ssl_context()

    def serve_central() -> None:
        while not stop_server.is_set():
            try:
                client_socket, address = central_listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop_server.is_set():
                    break
                raise
            threading.Thread(
                target=central.handle_client_thread,
                args=(client_socket, address, ssl_context),
                daemon=True,
            ).start()

    central_thread = threading.Thread(target=serve_central, daemon=True)
    central_thread.start()

    nodes = []
    node_threads = []
    for _ in range(3):
        node = DataNodeRunner(_free_port(), session_key)
        thread = threading.Thread(target=node.start, daemon=True)
        thread.start()
        nodes.append(node)
        node_threads.append(thread)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if len(central.heartbeat_monitor.get_healthy_nodes()) == 3:
            break
        time.sleep(0.05)
    else:
        stop_server.set()
        central_listener.close()
        for node in nodes:
            node.is_running = False
        raise AssertionError("Los DataNodes no se registraron en el tiempo esperado.")

    cluster = {
        "auth": auth,
        "central": central,
        "metadata": metadata,
        "nodes": nodes,
        "node_threads": node_threads,
        "central_listener": central_listener,
        "central_thread": central_thread,
        "stop_server": stop_server,
        "tmp_path": tmp_path,
    }
    try:
        yield cluster
    finally:
        stop_server.set()
        central_listener.close()
        for node in nodes:
            node.is_running = False
        for thread in node_threads:
            thread.join(timeout=2)
        central_thread.join(timeout=2)
