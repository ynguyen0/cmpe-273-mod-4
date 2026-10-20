import argparse
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from common import JsonHandler

STOCK = {"book": 5, "pen": 0, "notebook": 10}


class InventoryHandler(JsonHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            return self.send_json(200, {"status": "ok", "service": "inventory"})
        if url.path != "/availability":
            return self.send_json(404, {"error": "not_found"})
        query = parse_qs(url.query)
        try:
            item_id = query["item_id"][0]
            quantity = int(query["quantity"][0])
            if quantity <= 0:
                raise ValueError()
        except (KeyError, ValueError):
            return self.send_json(400, {"error": "item_id and positive integer quantity are required"})
        self.send_json(200, {
            "item_id": item_id,
            "quantity": quantity,
            "available": STOCK.get(item_id, 0) >= quantity,
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), InventoryHandler)
    print(f"Inventory listening on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
