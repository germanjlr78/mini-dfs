import os

# Directorio raíz absoluto del proyecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🌐 Configuración de Red
#CENTRAL_NODE_HOST = "localhost"
CENTRAL_NODE_HOST = "127.0.0.1"
CENTRAL_NODE_PORT = 8000

# 📦 Configuración del Sistema de Archivos Distribuidos (Tema 6.2)
BLOCK_SIZE = 4 * 1024 * 1024  
REPLICATION_FACTOR = 2  

# ⏱️ Tolerancia a Fallas & Monitoreo (Tema 4.1 y 4.3)
HEARTBEAT_INTERVAL = 3       
DATA_NODE_TIMEOUT = 9        
CLIENT_MAX_RETRIES = 3       

# 🔐 Rutas de Certificados SSL/TLS construidas con paths absolutos seguros (Tema 5.2)
CERT_DIR = os.path.normpath(os.path.join(BASE_DIR, "certificates"))
CA_CERT = os.path.normpath(os.path.join(CERT_DIR, "ca.crt"))
CENTRAL_CRT = os.path.normpath(os.path.join(CERT_DIR, "central.crt"))
CENTRAL_KEY = os.path.normpath(os.path.join(CERT_DIR, "central.key"))
DATANODE_CRT = os.path.normpath(os.path.join(CERT_DIR, "datanode.crt"))
DATANODE_KEY = os.path.normpath(os.path.join(CERT_DIR, "datanode.key"))
SESSION_KEY = os.path.normpath(os.path.join(BASE_DIR, "central_node", "session.key"))
USERS_DB = os.path.normpath(os.path.join(BASE_DIR, "central_node", "users.sqlite3"))

# 📁 Simulación de Almacenamiento
STORAGE_DIR = os.path.normpath(os.path.join(BASE_DIR, "storage_simulation"))
