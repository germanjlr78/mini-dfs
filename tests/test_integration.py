import os
import time
from pathlib import Path

import pytest

from client.dfs_client import DFSClient, DFSClientError
from utils.config import BLOCK_SIZE


def test_user_upload_isolation_repair_download_and_delete(mini_dfs_cluster):
    central = mini_dfs_cluster["central"]
    alice = DFSClient(central_port=central.port, timeout=5)
    alice.register_account("Alice", "alice-correct-password")
    bob = DFSClient(central_port=central.port, timeout=5)
    bob.register_account("Bob", "bob-correct-password")

    source = Path(mini_dfs_cluster["tmp_path"]) / "payload.bin"
    content = os.urandom(BLOCK_SIZE + 37)
    source.write_bytes(content)
    result = alice.upload(source)

    assert result == {"filename": "payload.bin", "size": len(content), "blocks": 2}
    assert alice.list_files() == {"payload.bin": {"size": len(content)}}
    assert bob.list_files() == {}
    anonymous = DFSClient(central_port=central.port, timeout=5)
    with pytest.raises(DFSClientError, match="token|válido"):
        anonymous.list_files()
    with pytest.raises(DFSClientError, match="no existe|permiso"):
        bob.download("payload.bin", Path(mini_dfs_cluster["tmp_path"]) / "private.bin")
    with pytest.raises(DFSClientError, match="no existe|permiso"):
        bob.delete("payload.bin")

    metadata = central.metadata_manager.get_file_metadata("payload.bin", "alice")
    first_block = metadata["blocks"][0]
    with pytest.raises(DFSClientError, match="token"):
        anonymous._node_request(
            first_block["nodes"][0],
            {"type": "READ_BLOCK", "block_id": first_block["block_id"]},
        )
    failed_node_id = first_block["nodes"][0]
    failed_node = next(node for node in mini_dfs_cluster["nodes"] if node.node_id == failed_node_id)
    failed_node.is_running = False
    mini_dfs_cluster["node_threads"][mini_dfs_cluster["nodes"].index(failed_node)].join(timeout=2)
    with central.heartbeat_monitor.lock:
        central.heartbeat_monitor.active_nodes[failed_node_id]["status"] = "DEAD"

    central.repair_underreplicated_blocks()
    repaired_metadata = central.metadata_manager.get_file_metadata("payload.bin", "alice")
    assert failed_node_id not in repaired_metadata["blocks"][0]["nodes"]
    assert len(repaired_metadata["blocks"][0]["nodes"]) == 2

    downloaded = Path(mini_dfs_cluster["tmp_path"]) / "downloaded.bin"
    alice.download("payload.bin", downloaded)
    assert downloaded.read_bytes() == content

    repaired_blocks = central.metadata_manager.get_file_metadata("payload.bin", "alice")[
        "blocks"
    ]
    deleted = alice.delete("payload.bin")
    assert deleted == {"filename": "payload.bin", "deleted_copies": 4}
    assert alice.list_files() == {}
    assert central.metadata_manager.get_file_metadata("payload.bin") is None
    for block in repaired_blocks:
        for node_id in block["nodes"]:
            node = next(node for node in mini_dfs_cluster["nodes"] if node.node_id == node_id)
            assert not (Path(node.storage.node_dir) / block["block_id"]).exists()


def test_empty_file_and_duplicate_filename(mini_dfs_cluster):
    central = mini_dfs_cluster["central"]
    client = DFSClient(central_port=central.port, timeout=5)
    client.register_account("Tester", "test-correct-password")
    source = Path(mini_dfs_cluster["tmp_path"]) / "empty.bin"
    source.write_bytes(b"")

    assert client.upload(source)["blocks"] == 0
    with pytest.raises(DFSClientError, match="ya existe"):
        client.upload(source)
    output = Path(mini_dfs_cluster["tmp_path"]) / "empty-copy.bin"
    client.download("empty.bin", output)
    assert output.read_bytes() == b""
