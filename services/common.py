import json
from http.server import BaseHTTPRequestHandler


class JsonHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 16_384:
            raise ValueError("Request body must contain 1–16384 bytes")
        self.connection.settimeout(3)
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object")
        return body
