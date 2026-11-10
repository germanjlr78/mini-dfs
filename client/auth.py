import ssl

from utils.config import CA_CERT, DATANODE_CRT, DATANODE_KEY


def create_client_ssl_context() -> ssl.SSLContext:
    """Create a mutual-TLS client context using the local DataNode identity."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=CA_CERT)
    context.load_cert_chain(certfile=DATANODE_CRT, keyfile=DATANODE_KEY)
    # The demo certificates have no Subject Alternative Name extension.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context