import os
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def generate_certs():
    cert_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("🧹 Limpiando certificados anteriores...")
    for f in os.listdir(cert_dir):
        if f.endswith(".crt") or f.endswith(".key") or f.endswith(".srl"):
            try:
                os.remove(os.path.join(cert_dir, f))
            except:
                pass

    print("🔐 Generando Infraestructura PKI con extensiones Key Usage y TLS...")

    # 1. Crear la Autoridad Certificadora (CA) Local
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    ca_subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "MX"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Edomex"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Ecatepec"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MiniDFS"),
        x509.NameAttribute(NameOID.COMMON_NAME, "MiniDFS-RootCA"),
    ])
    
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(ca_ski, critical=False)
        # REQUERIDO: Permitir que la CA firme otros certificados
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False
        ), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    ca_aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key())

    # 2. Certificado para el NODO CENTRAL (Servidor TLS)
    central_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    central_subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])
    central_cert = (
        x509.CertificateBuilder()
        .subject_name(central_subject)
        .issuer_name(ca_subject)
        .public_key(central_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(central_key.public_key()), critical=False)
        .add_extension(ca_aki, critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        # REQUERIDO: Uso extendido para autenticación de servidores y clientes TLS
        .add_extension(x509.ExtendedKeyUsage([
            x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH
        ]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    # 3. Certificado para los NODOS DE DATOS (Cliente TLS)
    datanode_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    datanode_subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])
    datanode_cert = (
        x509.CertificateBuilder()
        .subject_name(datanode_subject)
        .issuer_name(ca_subject)
        .public_key(datanode_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(datanode_key.public_key()), critical=False)
        .add_extension(ca_aki, critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([
            x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH
        ]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    files = {
        "ca.key": ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        "ca.crt": ca_cert.public_bytes(serialization.Encoding.PEM),
        "central.key": central_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        "central.crt": central_cert.public_bytes(serialization.Encoding.PEM),
        "datanode.key": datanode_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        "datanode.crt": datanode_cert.public_bytes(serialization.Encoding.PEM),
    }

    for filename, content in files.items():
        with open(os.path.join(cert_dir, filename), "wb") as f:
            f.write(content)
            
    print("✅ ¡Certificados maestros generados con extensiones Key Usage válidas!")

if __name__ == "__main__":
    generate_certs()
