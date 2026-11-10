import threading
import time
import logging
from utils.config import DATA_NODE_TIMEOUT

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    def __init__(self):
        # Estructura: { "host:port": { "last_seen": timestamp, "status": "ACTIVE" } }
        self.active_nodes = {}
        self.lock = threading.Lock()
        self.on_node_change = None

    def register_heartbeat(self, node_id):
        changed = False
        with self.lock:
            current_time = time.time()
            if node_id not in self.active_nodes:
                print(f"[Heartbeat] Nuevo DataNode registrado: {node_id}")
                changed = True
            elif self.active_nodes[node_id]["status"] != "ACTIVE":
                print(f"[Heartbeat] DataNode recuperado: {node_id}")
                changed = True
            self.active_nodes[node_id] = {
                "last_seen": current_time,
                "status": "ACTIVE"
            }
        if changed and self.on_node_change:
            self.on_node_change(node_id, "ACTIVE")

    def get_healthy_nodes(self):
        """
        Retorna la lista de identificadores de nodos que están actualmente sanos.
        """
        with self.lock:
            return sorted(
                node_id
                for node_id, info in self.active_nodes.items()
                if info["status"] == "ACTIVE"
            )

    def start_auditing(self, on_node_change=None):
        self.on_node_change = on_node_change
        audit_thread = threading.Thread(target=self._audit_loop, daemon=True)
        audit_thread.start()
        print("[Heartbeat] Hilo de auditoría de fallas iniciado.")

    def _audit_loop(self):
        while True:
            time.sleep(2)
            current_time = time.time()
            failed_nodes = []
            with self.lock:
                for node_id, info in list(self.active_nodes.items()):
                    if info["status"] == "ACTIVE":
                        elapsed = current_time - info["last_seen"]
                        if elapsed > DATA_NODE_TIMEOUT:
                            info["status"] = "DEAD"
                            failed_nodes.append(node_id)
                            print(f"[FALLA DETECTADA] El DataNode {node_id} no responde. Tiempo: {elapsed:.1f}s.")
                            print(f"[Mitigación] Retirando {node_id} de la lista de nodos activos.")
            if self.on_node_change:
                for node_id in failed_nodes:
                    try:
                        self.on_node_change(node_id, "DEAD")
                    except Exception:
                        logger.exception("Error al notificar la caída de %s.", node_id)
