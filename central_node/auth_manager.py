import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from utils.config import SESSION_KEY, USERS_DB

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_PBKDF2_ITERATIONS = 310_000
_SESSION_SECONDS = 3600


class TokenSigner:
    def __init__(self, secret_path: str | os.PathLike[str] = SESSION_KEY, create: bool = False):
        self.secret_path = Path(secret_path)
        if create:
            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    self.secret_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as secret_file:
                    secret_file.write(secrets.token_bytes(32))
                    secret_file.flush()
                    os.fsync(secret_file.fileno())
        if not self.secret_path.is_file():
            raise FileNotFoundError(
                f"No existe la clave de sesiones {self.secret_path}; inicia primero el nodo central."
            )
        self._secret = self.secret_path.read_bytes()
        if len(self._secret) < 32:
            raise ValueError("La clave de sesiones debe contener al menos 32 bytes.")

    @staticmethod
    def _encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def issue(self, claims: dict[str, Any], lifetime: int = _SESSION_SECONDS) -> str:
        payload = dict(claims)
        payload["exp"] = int(time.time()) + lifetime
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = self._encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str, expected_type: str | None = None) -> dict[str, Any]:
        try:
            encoded, signature = token.split(".", 1)
            expected_signature = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(self._decode(signature), expected_signature):
                raise ValueError("La firma del token no es válida.")
            claims = json.loads(self._decode(encoded).decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("El token no es válido.") from error
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
            raise ValueError("El token no es válido.")
        if claims["exp"] <= int(time.time()):
            raise ValueError("La sesión ha caducado; inicia sesión de nuevo.")
        if expected_type is not None and claims.get("type") != expected_type:
            raise ValueError("El tipo de token no está autorizado.")
        return claims

    def verify_capability(
        self,
        token: str,
        action: str,
        block_id: str,
        node_id: str,
    ) -> None:
        claims = self.verify(token, "capability")
        if (
            claims.get("action") != action
            or claims.get("block_id") != block_id
            or claims.get("node_id") != node_id
            or not isinstance(claims.get("sub"), str)
        ):
            raise ValueError("La capacidad no autoriza esta operación o bloque.")


class AuthManager:
    def __init__(
        self,
        database_path: str | os.PathLike[str] = USERS_DB,
        signer: TokenSigner | None = None,
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.signer = signer or TokenSigner(create=True)
        self.lock = threading.Lock()
        with closing(self._connect()) as database, database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)

    def register(self, username: str, password: str) -> str:
        self._validate_credentials(username, password)
        username = username.lower()
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        try:
            with self.lock, closing(self._connect()) as database, database:
                database.execute(
                    "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                    (username, salt, password_hash),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Ese nombre de usuario ya está registrado.") from error
        return self.issue_session(username)

    def login(self, username: str, password: str) -> str:
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or not 1 <= len(password) <= 1024
        ):
            raise ValueError("Usuario o contraseña incorrectos.")
        with closing(self._connect()) as database, database:
            record = database.execute(
                "SELECT username, salt, password_hash FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if record is None:
            raise ValueError("Usuario o contraseña incorrectos.")
        canonical_username, salt, stored_hash = record
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        if not hmac.compare_digest(candidate, stored_hash):
            raise ValueError("Usuario o contraseña incorrectos.")
        return self.issue_session(canonical_username)

    def issue_session(self, username: str) -> str:
        return self.signer.issue({"type": "session", "sub": username})

    def authenticate(self, token: str) -> str:
        claims = self.signer.verify(token, "session")
        username = claims.get("sub")
        if not isinstance(username, str):
            raise ValueError("La sesión no contiene un usuario válido.")
        return username

    def issue_capability(
        self,
        username: str,
        action: str,
        block_id: str,
        node_id: str,
    ) -> str:
        return self.signer.issue(
            {
                "type": "capability",
                "sub": username,
                "action": action,
                "block_id": block_id,
                "node_id": node_id,
            },
            lifetime=3600,
        )

    def verify_capability(
        self,
        token: str,
        action: str,
        block_id: str,
        node_id: str,
    ) -> None:
        self.signer.verify_capability(token, action, block_id, node_id)

    @staticmethod
    def _validate_credentials(username: str, password: str) -> None:
        if not isinstance(username, str) or not _USERNAME.fullmatch(username):
            raise ValueError(
                "El usuario debe tener de 3 a 32 caracteres: letras, números, punto, guion o guion bajo."
            )
        if not isinstance(password, str) or not 10 <= len(password) <= 1024:
            raise ValueError("La contraseña debe tener entre 10 y 1024 caracteres.")
