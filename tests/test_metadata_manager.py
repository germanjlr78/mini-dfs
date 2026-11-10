import json

import pytest

from central_node.metadata_manager import MetadataManager


def _block(node="127.0.0.1:5001"):
    return {
        "block_id": "block_1",
        "nodes": [node],
        "checksum": "a" * 64,
    }


def test_wal_recovers_registration_update_and_deletion(tmp_path):
    manager = MetadataManager()
    manager.wal_path = str(tmp_path / "wal.log")
    manager.register_file("report.bin", 3, [_block()], "alice")
    assert manager.replace_block_nodes("report.bin", "block_1", ["127.0.0.1:5002"])

    recovered = MetadataManager()
    recovered.wal_path = manager.wal_path
    recovered._recover_from_wal()
    assert recovered.get_file_metadata("report.bin", "alice")["blocks"][0]["nodes"] == [
        "127.0.0.1:5002"
    ]

    assert recovered.delete_file("report.bin", "alice") is not None
    final_recovery = MetadataManager()
    final_recovery.wal_path = manager.wal_path
    final_recovery._recover_from_wal()
    assert final_recovery.get_file_metadata("report.bin") is None


def test_list_and_read_are_scoped_to_owner(tmp_path):
    manager = MetadataManager()
    manager.wal_path = str(tmp_path / "wal.log")
    manager.register_file("alice.bin", 3, [_block()], "alice")
    manager.register_file("bob.bin", 3, [_block("127.0.0.1:5002")], "bob")

    assert manager.list_files("alice") == {"alice.bin": {"size": 3}}
    assert manager.get_file_metadata("bob.bin", "alice") is None
    assert manager.delete_file("bob.bin", "alice") is None
    assert manager.get_file_metadata("bob.bin", "bob") is not None


@pytest.mark.parametrize(
    ("filename", "size", "blocks"),
    [
        ("../escape", 0, []),
        ("valid.bin", -1, []),
        ("valid.bin", 3, [{"block_id": "../x", "nodes": ["node"], "checksum": "a" * 64}]),
        ("valid.bin", 3, [{"block_id": "block", "nodes": [], "checksum": "a" * 64}]),
        ("valid.bin", 3, [{"block_id": "block", "nodes": ["node"], "checksum": "bad"}]),
    ],
)
def test_invalid_metadata_is_rejected(tmp_path, filename, size, blocks):
    manager = MetadataManager()
    manager.wal_path = str(tmp_path / "wal.log")
    with pytest.raises(ValueError):
        manager.register_file(filename, size, blocks, "alice")
    assert not (tmp_path / "wal.log").exists()


def test_wal_skips_malformed_line_and_keeps_following_records(tmp_path):
    wal_path = tmp_path / "wal.log"
    wal_path.write_text(
        json.dumps(
            {
                "op": "REGISTER_FILE",
                "filename": "valid.bin",
                "size": 0,
                "blocks": [],
                "owner": "alice",
            }
        )
        + "\n{invalid json\n",
        encoding="utf-8",
    )

    manager = MetadataManager()
    manager.wal_path = str(wal_path)
    manager._recover_from_wal()
    assert manager.get_file_metadata("valid.bin", "alice")["size"] == 0
