import argparse
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from common import JsonHandler

INVENTORY_URL = os.environ.get("INVENTORY_URL", "http://127.0.0.1:8001").rstrip("/")
INVENTORY_TIMEOUT = 2
ORDERS = {}
ORDER_LOCK = threading.Lock()


class OrderHandler(JsonHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.send_json(200, {"status": "ok", "service": "order"})
        if path.startswith("/orders/"):
            with ORDER_LOCK:
                order = ORDERS.get(path.removeprefix("/orders/"))
            if order is not None:
                return self.send_json(200, order)
        self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if urlsplit(self.path).path != "/orders":
            return self.send_json(404, {"error": "not_found"})
        try:
            body = self.read_json()
            item_id = body.get("item_id")
            quantity = body.get("quantity")
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("item_id must be a nonempty string")
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
        except (ValueError, OSError) as exc:
            return self.send_json(400, {"error": "invalid_request", "message": str(exc)})

        query = urlencode({"item_id": item_id, "quantity": quantity})
        try:
            with urlopen(f"{INVENTORY_URL}/availability?{query}", timeout=INVENTORY_TIMEOUT) as response:
                availability = json.load(response)
            if not isinstance(availability, dict) or type(availability.get("available")) is not bool:
                raise ValueError("Invalid Inventory response")
        except (URLError, OSError, ValueError):
            return self.send_json(503, {
                "error": "inventory_unavailable",
                "message": "Cannot check inventory. Try again later; no order was created.",
            })
        if not availability["available"]:
            return self.send_json(409, {
                "error": "item_unavailable", "item_id": item_id, "quantity": quantity,
            })

        order = {
            "order_id": str(uuid.uuid4()), "item_id": item_id, "quantity": quantity,
            "status": "created", "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with ORDER_LOCK:
            ORDERS[order["order_id"]] = order
        # Event demonstration only: stdout is not a durable event transport.
        print(json.dumps({"event": "OrderCreated", "order": order}), flush=True)
        self.send_json(201, order)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), OrderHandler)
    print(f"Order listening on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
