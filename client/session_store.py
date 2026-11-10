import json
import os
from pathlib import Path

SESSION_PATH = Path.home() / ".minidfs" / "session.json"


def save_session(username: str, token: str) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = SESSION_PATH.with_suffix(".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as session_file:
            json.dump({"username": username, "token": token}, session_file)
            session_file.flush()
            os.fsync(session_file.fileno())
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        os.replace(temporary_path, SESSION_PATH)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def load_session() -> tuple[str, str]:
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("No hay una sesión guardada. Ejecuta primero `python -m client.main login`.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"No se pudo leer la sesión local: {error}") from error
    username = data.get("username")
    token = data.get("token")
    if not isinstance(username, str) or not isinstance(token, str):
        raise ValueError("El archivo de sesión local no tiene un formato válido.")
    return username, token


def clear_session() -> None:
    try:
        SESSION_PATH.unlink()
    except FileNotFoundError:
        return
