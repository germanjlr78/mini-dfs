import hashlib
import socket
import threading

import pytest

import data_node.storage_engine as storage_module
from data_node.storage_engine import StorageEngine
from utils.protocol import receive_message, send_message


def test_storage_round_trip_overwrite_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "STORAGE_DIR", str(tmp_path))
    storage = StorageEngine(5001)

    assert storage.write_block("block_1", b"first") == hashlib.sha256(b"first").hexdigest()
    assert storage.read_block("block_1") == b"first"
    storage.write_block("block_1", b"second")
    assert storage.read_block("block_1") == b"second"
    assert storage.delete_block("block_1")
    assert not storage.delete_block("block_1")


@pytest.mark.parametrize("block_id", ["../outside", "..\\outside", "", "sub/path"])
def test_storage_rejects_unsafe_block_ids(tmp_path, monkeypatch, block_id):
    monkeypatch.setattr(storage_module, "STORAGE_DIR", str(tmp_path))
    storage = StorageEngine(5001)

    with pytest.raises(ValueError):
        storage.read_block(block_id)
    with pytest.raises(ValueError):
        storage.write_block(block_id, b"data")


def test_protocol_reads_framed_messages_over_socket():
    sender, receiver = socket.socketpair()
    message = {"type": "REQUEST", "filename": "archivo.bin", "size": 9}
    thread = threading.Thread(target=send_message, args=(sender, message))
    try:
        thread.start()
        assert receive_message(receiver) == message
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        sender.close()
        receiver.close()


def test_protocol_rejects_disconnected_or_invalid_message():
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(b"\x00\x00")
        sender.close()
        with pytest.raises(ConnectionError):
            receive_message(receiver)
    finally:
        receiver.close()

    sender, receiver = socket.socketpair()
    try:
        sender.sendall(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="tamaño"):
            receive_message(receiver)
    finally:
        sender.close()
        receiver.close()
