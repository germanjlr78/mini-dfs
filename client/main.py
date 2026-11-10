import argparse
import getpass
import sys

from client.dfs_client import DFSClient, DFSClientError
from client.session_store import clear_session, load_session, save_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-dfs",
        description="Cliente de línea de comandos para MiniDFS.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    for command_name, help_text in (
        ("register", "Crea una cuenta."),
        ("login", "Inicia sesión."),
    ):
        command = commands.add_parser(command_name, help=help_text)
        command.add_argument("username", help="Nombre de usuario.")

    upload_parser = commands.add_parser("upload", help="Sube un archivo al sistema.")
    upload_parser.add_argument("file", help="Ruta del archivo local.")
    upload_parser.add_argument("--name", help="Nombre remoto opcional.")

    download_parser = commands.add_parser("download", help="Descarga un archivo.")
    download_parser.add_argument("name", help="Nombre remoto.")
    download_parser.add_argument(
        "--output",
        help="Ruta local de salida (por defecto, la carpeta actual).",
    )

    commands.add_parser("list", help="Lista tus archivos.")
    delete_parser = commands.add_parser("delete", help="Elimina uno de tus archivos.")
    delete_parser.add_argument("name", help="Nombre remoto del archivo.")
    commands.add_parser("logout", help="Cierra la sesión guardada en este equipo.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "logout":
            clear_session()
            print("Sesión local cerrada.")
            return 0

        if args.command in {"register", "login"}:
            password = getpass.getpass("Contraseña: ")
            if args.command == "register":
                confirmation = getpass.getpass("Confirma la contraseña: ")
                if password != confirmation:
                    raise DFSClientError("Las contraseñas no coinciden.")
            client = DFSClient()
            if args.command == "register":
                token = client.register_account(args.username, password)
            else:
                token = client.login(args.username, password)
            save_session(args.username, token)
            print(f"Sesión iniciada para '{args.username}'. El token caduca en una hora.")
            return 0

        username, token = load_session()
        client = DFSClient(token=token)
        if args.command == "upload":
            result = client.upload(args.file, args.name)
            print(
                f"Subida completada: {result['filename']} "
                f"({result['size']} bytes, {result['blocks']} bloques)."
            )
        elif args.command == "download":
            output = client.download(args.name, args.output)
            print(f"Descarga completada: {output}")
        elif args.command == "delete":
            result = client.delete(args.name)
            print(
                f"Archivo '{result['filename']}' eliminado "
                f"({result['deleted_copies']} copias borradas)."
            )
        else:
            files = client.list_files()
            if not files:
                print(f"No hay archivos para '{username}'.")
            else:
                print(f"{'NOMBRE':<40} {'TAMAÑO (bytes)':>16}")
                for filename, metadata in files.items():
                    print(f"{filename:<40} {metadata['size']:>16}")
    except (DFSClientError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
