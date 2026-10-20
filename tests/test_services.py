import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServiceTest(unittest.TestCase):
    def test_independent_services_and_dependency_failure(self):
        transcript = []
        processes = []
        inventory_port = free_port()
        order_port = free_port()
        while order_port == inventory_port:
            order_port = free_port()
        inventory_url = f"http://127.0.0.1:{inventory_port}"
        order_url = f"http://127.0.0.1:{order_port}"

        with tempfile.TemporaryFile(mode="w+") as logs:
            def start(script, port):
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "services" / script), "--port", str(port)],
                    env={**os.environ, "INVENTORY_URL": inventory_url},
                    stdout=logs, stderr=logs,
                )
                processes.append(process)
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail(f"{script} exited during startup")
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                            return process
                    except (URLError, OSError):
                        time.sleep(0.05)
                self.fail(f"{script} did not become ready")

            def request(path, expected, body=None):
                command = ["curl", "--silent", "--show-error", "--max-time", "5", "-i"]
                if body is not None:
                    command += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
                command.append(order_url + path)
                result = subprocess.run(command, capture_output=True, text=True, timeout=6, check=True)
                transcript.extend(["$ " + shlex.join(command), result.stdout, ""])
                head, payload = result.stdout.split("\n\n", 1)
                self.assertEqual(int(head.splitlines()[0].split()[1]), expected)
                return json.loads(payload)

            try:
                inventory = start("inventory_service.py", inventory_port)
                order_process = start("order_service.py", order_port)
                transcript.append(f"Independent processes: Order PID {order_process.pid}, Inventory PID {inventory.pid}\n")
                order = request("/orders", 201, {"item_id": "book", "quantity": 2})
                self.assertEqual(order["status"], "created")
                self.assertEqual(request("/orders/" + order["order_id"], 200), order)
                for item, quantity in [("pen", 1), ("unknown", 1), ("book", 6)]:
                    self.assertEqual(request("/orders", 409, {"item_id": item, "quantity": quantity})["error"], "item_unavailable")
                for quantity in [0, -1, True, "2"]:
                    request("/orders", 400, {"item_id": "book", "quantity": quantity})

                transcript.append(f"$ kill -TERM {inventory.pid}  # Stop only Inventory\n")
                inventory.terminate()
                inventory.wait(timeout=5)
                failure = request("/orders", 503, {"item_id": "book", "quantity": 1})
                self.assertEqual(failure["error"], "inventory_unavailable")
                self.assertIsNone(order_process.poll())
                request("/health", 200)
                self.assertEqual(request("/orders/" + order["order_id"], 200), order)

                transcript.append("Restart Inventory; Order keeps the same PID.\n")
                start("inventory_service.py", inventory_port)
                request("/orders", 201, {"item_id": "notebook", "quantity": 1})
                self.assertIsNone(order_process.poll())
                transcript.append("PASS: creation, tracking, unavailable stock, validation, dependency failure, liveness, and recovery.\n")
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                logs.seek(0)
                transcript.append("Service logs:\n" + logs.read())
                (ROOT / "evidence" / "integration.txt").write_text("\n".join(transcript))


if __name__ == "__main__":
    unittest.main(verbosity=2)
