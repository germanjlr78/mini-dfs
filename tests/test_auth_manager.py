import pytest

from central_node.auth_manager import AuthManager, TokenSigner


def test_register_login_and_case_insensitive_username(tmp_path):
    signer = TokenSigner(tmp_path / "session.key", create=True)
    manager = AuthManager(tmp_path / "users.sqlite3", signer)

    session = manager.register("Alice", "correct-horse-battery")

    assert manager.authenticate(session) == "alice"
    assert manager.authenticate(manager.login("ALICE", "correct-horse-battery")) == "alice"
    with pytest.raises(ValueError, match="incorrectos"):
        manager.login("alice", "wrong-password")


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("ab", "correct-horse-battery"),
        ("user/name", "correct-horse-battery"),
        ("valid_user", "short"),
        ("valid_user", "x" * 1025),
    ],
)
def test_register_rejects_invalid_credentials(tmp_path, username, password):
    signer = TokenSigner(tmp_path / "session.key", create=True)
    manager = AuthManager(tmp_path / "users.sqlite3", signer)

    with pytest.raises(ValueError):
        manager.register(username, password)


def test_duplicate_registration_and_expired_session_are_rejected(tmp_path):
    signer = TokenSigner(tmp_path / "session.key", create=True)
    manager = AuthManager(tmp_path / "users.sqlite3", signer)
    manager.register("alice", "correct-horse-battery")

    with pytest.raises(ValueError, match="ya está registrado"):
        manager.register("ALICE", "another-correct-password")
    with pytest.raises(ValueError, match="caducado"):
        signer.verify(signer.issue({"type": "session", "sub": "alice"}, lifetime=-1))


def test_capability_is_limited_to_its_action_block_and_node(tmp_path):
    signer = TokenSigner(tmp_path / "session.key", create=True)
    manager = AuthManager(tmp_path / "users.sqlite3", signer)
    capability = manager.issue_capability(
        "alice", "READ_BLOCK", "block_1", "127.0.0.1:5001"
    )

    manager.verify_capability(capability, "READ_BLOCK", "block_1", "127.0.0.1:5001")
    with pytest.raises(ValueError):
        manager.verify_capability(capability, "DELETE_BLOCK", "block_1", "127.0.0.1:5001")
    with pytest.raises(ValueError):
        manager.verify_capability(capability, "READ_BLOCK", "block_2", "127.0.0.1:5001")
    with pytest.raises(ValueError):
        manager.verify_capability(capability, "READ_BLOCK", "block_1", "127.0.0.1:5002")
    with pytest.raises(ValueError):
        manager.authenticate(capability)
