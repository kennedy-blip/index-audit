import json
from http.server import BaseHTTPRequestHandler

def send_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def read_json(handler, limit=1_000_000):
    try:
        size = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise ValueError("Invalid Content-Length")
    if size < 0 or size > limit:
        raise ValueError("Request body must be smaller than 1 MB")
    raw = handler.rfile.read(size)
    try:
        value = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Request body must be valid JSON")
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value

class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Vercel captures stderr; keep request logs concise.
        print("%s - %s" % (self.address_string(), fmt % args))
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
