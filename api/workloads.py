import sqlite3
from http.server import BaseHTTPRequestHandler
from _common import ApiHandler, read_json, send_json

PRESETS = [
 {"id":"ledger","name":"High-Write Ledger & Settlement DB","description":"A write-heavy transactional ledger where secondary indexes compete with insert throughput.","writeRatio":0.82,"ddlSql":"CREATE TABLE transactions (id INTEGER PRIMARY KEY, merchant_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, amount_cents INTEGER NOT NULL, reference TEXT);\nCREATE INDEX idx_tx_merchant ON transactions(merchant_id);\nCREATE INDEX idx_tx_status ON transactions(status);\nCREATE INDEX idx_tx_created ON transactions(created_at);\nCREATE INDEX idx_tx_merchant_status_created ON transactions(merchant_id, status, created_at);\nCREATE INDEX idx_tx_reference ON transactions(reference);","querySamples":[{"name":"Merchant settlement history","sql":"SELECT * FROM transactions WHERE merchant_id = ? AND status = ? ORDER BY created_at DESC","frequency":1200},{"name":"Recent transactions","sql":"SELECT id, amount_cents FROM transactions WHERE created_at >= ? ORDER BY created_at DESC","frequency":350},{"name":"Status reconciliation","sql":"SELECT status, COUNT(*) FROM transactions GROUP BY status","frequency":90}]},
 {"id":"catalog","name":"Catalog & Order Search Workload","description":"A read-oriented catalog with composite lookup and sorting patterns.","writeRatio":0.24,"ddlSql":"CREATE TABLE products (id INTEGER PRIMARY KEY, category_id INTEGER, sku TEXT, title TEXT, price REAL, active INTEGER);\nCREATE INDEX idx_products_category ON products(category_id);\nCREATE INDEX idx_products_category_active_price ON products(category_id, active, price);\nCREATE INDEX idx_products_sku ON products(sku);\nCREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, created_at TEXT);\nCREATE INDEX idx_orders_customer ON orders(customer_id);\nCREATE INDEX idx_orders_status_created ON orders(status, created_at);","querySamples":[{"name":"Category browse","sql":"SELECT * FROM products WHERE category_id = ? AND active = 1 ORDER BY price","frequency":1600},{"name":"SKU lookup","sql":"SELECT * FROM products WHERE sku = ?","frequency":800},{"name":"Customer orders","sql":"SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC","frequency":420}]},
 {"id":"telemetry","name":"Over-Indexed Telemetry Store","description":"A high-ingest event table with several overlapping indexes and rarely queried fields.","writeRatio":0.91,"ddlSql":"CREATE TABLE events (id INTEGER PRIMARY KEY, service TEXT, region TEXT, level TEXT, occurred_at TEXT, trace_id TEXT, payload TEXT);\nCREATE INDEX idx_events_service ON events(service);\nCREATE INDEX idx_events_service_region ON events(service, region);\nCREATE INDEX idx_events_service_region_time ON events(service, region, occurred_at);\nCREATE INDEX idx_events_level ON events(level);\nCREATE INDEX idx_events_trace ON events(trace_id);\nCREATE INDEX idx_events_payload ON events(payload);","querySamples":[{"name":"Service timeline","sql":"SELECT * FROM events WHERE service = ? AND region = ? ORDER BY occurred_at DESC","frequency":900},{"name":"Trace lookup","sql":"SELECT * FROM events WHERE trace_id = ?","frequency":35}]}
]
CUSTOM = {}

def catalog():
    # The catalog is deliberately reconstructed per invocation; custom saves are
    # best-effort within a warm serverless instance, not durable storage.
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE workload_catalog (id TEXT PRIMARY KEY, name TEXT, payload TEXT)")
    for item in PRESETS:
        db.execute("INSERT INTO workload_catalog VALUES (?, ?, ?)", (item["id"], item["name"], "{}"))
    db.commit()
    return db

class handler(ApiHandler):
    def do_GET(self):
        db = catalog()
        db.close()
        send_json(self, 200, {"workloads": PRESETS + list(CUSTOM.values()), "storage":"ephemeral"})
    def do_POST(self):
        try:
            item = read_json(self)
            name = str(item.get("name", "Custom workload")).strip()[:100]
            ddl = item.get("ddlSql", "")
            samples = item.get("querySamples", [])
            ratio = float(item.get("writeRatio", 0.5))
            if not isinstance(ddl, str) or len(ddl) > 100000:
                raise ValueError("ddlSql must be text under 100 KB")
            if not isinstance(samples, list) or len(samples) > 200:
                raise ValueError("querySamples must be an array of at most 200 items")
            if not 0 <= ratio <= 1:
                raise ValueError("writeRatio must be between 0 and 1")
            saved = {"id":"custom-" + str(abs(hash((name, ddl))) % 1000000000),"name":name,"description":"User supplied schema and query samples","writeRatio":ratio,"ddlSql":ddl,"querySamples":samples}
            CUSTOM[saved["id"]] = saved
            send_json(self, 201, {"workload":saved,"storage":"ephemeral","warning":"Vercel serverless memory is temporary. Export this workload to keep a copy."})
        except ValueError as exc:
            send_json(self, 400, {"error":str(exc)})
        except Exception:
            send_json(self, 500, {"error":"Unable to save workload"})
