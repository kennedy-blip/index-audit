import re
import sqlite3
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from _common import ApiHandler, read_json, send_json

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
def split_statements(sql):
    statements, current = [], ""
    for line in sql.splitlines():
        current += line + "\n"
        if sqlite3.complete_statement(current):
            if current.strip():
                statements.append(current.strip())
            current = ""
    if current.strip():
        raise ValueError("DDL contains an incomplete SQL statement")
    return statements

def safe_schema(sql):
    if not isinstance(sql, str) or len(sql) > 100000:
        raise ValueError("ddlSql must be text under 100 KB")
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA query_only = OFF")
    for statement in split_statements(sql):
        clean = re.sub(r"/\*.*?\*/|--[^\n]*", "", statement, flags=re.S).strip()
        if not clean:
            continue
        if not re.match(r"(?is)^CREATE\s+(?:TEMP(?:ORARY)?\s+)?(?:TABLE|INDEX|UNIQUE\s+INDEX)\b", clean):
            db.close()
            raise ValueError("Only CREATE TABLE and CREATE INDEX DDL is accepted")
        try:
            db.execute(statement)
        except sqlite3.Error as exc:
            db.close()
            raise ValueError("SQLite rejected DDL: " + str(exc))
    return db

def index_catalog(db):
    tables = {}
    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        table = row[0]
        columns = [r[1] for r in db.execute('PRAGMA table_info("' + table.replace('"','""') + '")')]
        indexes = []
        for ix in db.execute("PRAGMA index_list('" + table.replace("'","''") + "')"):
            name = ix[1]
            cols = [r[2] for r in db.execute('PRAGMA index_info("' + name.replace('"','""') + '")') if r[2]]
            indexes.append({"name":name,"columns":cols,"unique":bool(ix[2]),"origin":ix[3] if len(ix)>3 else "c"})
        tables[table] = {"columns":columns,"indexes":indexes}
    return tables

def query_columns(sql, tables):
    hits = set()
    for table, meta in tables.items():
        # Restrict candidate columns to known schema identifiers to avoid SQL token false positives.
        for col in meta["columns"]:
            pattern = r"(?i)(?:\b" + re.escape(col) + r"\b)"
            if re.search(pattern, sql):
                hits.add((table, col))
    return hits

def audit(payload):
    ddl = payload.get("ddlSql", "")
    samples = payload.get("querySamples", [])
    if not isinstance(samples, list) or len(samples) > 200:
        raise ValueError("querySamples must be an array of at most 200 items")
    db = safe_schema(ddl)
    try:
        tables = index_catalog(db)
        index_rows = []
        for table, meta in tables.items():
            for ix in meta["indexes"]:
                index_rows.append({**ix,"table":table,"hits":0,"matchedQueries":[],"status":"UNUSED_CANDIDATE","explanation":"No supplied workload query references these indexed columns."})
        total_frequency = 0
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            sql = str(sample.get("sql", ""))[:10000]
            try:
                frequency = max(0, min(int(sample.get("frequency", 1)), 1000000000))
            except (ValueError, TypeError):
                frequency = 1
            total_frequency += frequency
            referenced = query_columns(sql, tables)
            # Match contiguous leading index columns against referenced columns on each table.
            for ix in index_rows:
                leading = ix["columns"][:1]
                if leading and (ix["table"], leading[0]) in referenced:
                    ix["hits"] += frequency
                    label = str(sample.get("name") or "Workload query")[:100]
                    if label not in ix["matchedQueries"]:
                        ix["matchedQueries"].append(label)
        # Same-table identical definitions and left-prefix redundancy.
        for ix in index_rows:
            peers = [p for p in index_rows if p["table"] == ix["table"] and p["name"] != ix["name"]]
            duplicate = next((p for p in peers if p["columns"] == ix["columns"]), None)
            covering = next((p for p in peers if len(p["columns"]) > len(ix["columns"]) and p["columns"][:len(ix["columns"])] == ix["columns"]), None)
            if duplicate:
                ix["status"] = "DUPLICATE_DEFINITION"
                ix["explanation"] = "This index has the same ordered column definition as " + duplicate["name"] + ". Keep one definition after checking uniqueness and constraints."
                ix["candidate"] = True
            elif covering and not ix["unique"]:
                ix["status"] = "REDUNDANT_PREFIX"
                ix["explanation"] = covering["name"] + " begins with the same ordered columns and may serve the same left-prefix lookups."
                ix["candidate"] = True
            elif ix["hits"] == 0 and ix["origin"] == "c":
                ix["status"] = "UNUSED_CANDIDATE"
                ix["explanation"] = "No supplied query matched the leading indexed column. Confirm production telemetry before removal."
                ix["candidate"] = True
            else:
                ix["status"] = "ESSENTIAL"
                ix["explanation"] = "Referenced by the supplied workload or retained as a non-duplicate access path."
                ix["candidate"] = False
        write_ratio = payload.get("writeRatio", 0.65)
        try:
            write_ratio = max(0.0, min(1.0, float(write_ratio)))
        except (TypeError, ValueError):
            write_ratio = 0.65
        count = len(index_rows)
        candidates = [ix for ix in index_rows if ix.get("candidate")]
        # Heuristic, not measured runtime cost: weighted by index count and write ratio.
        overhead = round(min(100, count * write_ratio * 8.5), 1)
        reduction = round((len(candidates) / count * overhead) if count else 0, 1)
        cleanup = ["DROP INDEX IF EXISTS \"" + ix["name"].replace('"','""') + "\";" for ix in candidates]
        return {"summary":{"tablesScanned":len(tables),"indexesScanned":count,"cleanupCandidates":len(candidates),"writeRatio":round(write_ratio,2),"writeOverheadScore":overhead,"severity":"HIGH" if overhead >= 45 else "MODERATE" if overhead >= 20 else "LOW","estimatedOverheadReduction":reduction,"totalQueryFrequency":total_frequency},"tables":[{"name":name,"columns":meta["columns"],"indexCount":len(meta["indexes"])} for name,meta in tables.items()],"indexes":index_rows,"cleanupSql":cleanup,"caveats":["Column-reference matching is heuristic and does not execute representative data workloads.","SQLite's planner and index statistics vary with data distribution; validate with EXPLAIN QUERY PLAN.","An index can be required by UNIQUE constraints, foreign keys, or queries absent from the supplied sample.","Write-overhead and cleanup-impact values are directional estimates, not measured latency or storage savings."]}
    finally:
        db.close()

class handler(ApiHandler):
    def do_POST(self):
        try:
            payload = read_json(self)
            result = audit(payload)
            send_json(self, 200, result)
        except ValueError as exc:
            send_json(self, 400, {"error":str(exc)})
        except Exception:
            send_json(self, 500, {"error":"Audit failed unexpectedly"})
