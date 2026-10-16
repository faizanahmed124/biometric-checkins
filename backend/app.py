"""
Biometric Checkins - local backend
Pulls logs directly from ZKTeco machines over the LAN, stores them in SQLite,
and serves an HTTP API for the React frontend. Runs independently of ERP.
"""
import csv
import io
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from zk import ZK

import erp

BASE = Path(__file__).resolve().parent
DEVICES_FILE = Path(os.getenv("DEVICES_FILE", BASE / "devices.json"))
DB_FILE = Path(os.getenv("DB_FILE", BASE / "checkins.db"))
AUTO_SYNC_MINUTES = int(os.getenv("AUTO_SYNC_MINUTES", "30"))  # 0 = auto sync off
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
MAX_RANGE_DAYS = 31
FRONTEND_DIST = BASE.parent / "frontend" / "dist"

_device_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_db_lock = threading.Lock()
_last_sync = {"at": None, "start": None, "end": None, "results": []}


# ---------------------------------------------------------------- helpers
def load_devices():
    with open(DEVICES_FILE, encoding="utf-8") as f:
        return [d for d in json.load(f) if d.get("active", True)]


def device_lock(name: str) -> threading.Lock:
    with _locks_guard:
        return _device_locks.setdefault(name, threading.Lock())


def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkins(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT NOT NULL,
                user_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                punch INTEGER,
                status INTEGER,
                log_type TEXT,
                fetched_at TEXT,
                UNIQUE(device, user_id, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_checkins_ts ON checkins(ts);
            CREATE TABLE IF NOT EXISTS users(
                user_id TEXT PRIMARY KEY,
                name TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"Invalid date: {value} (expected YYYY-MM-DD)")


def check_range(start: date, end: date):
    if end < start:
        raise HTTPException(400, "End date cannot be before start date")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise HTTPException(400, f"Maximum range is {MAX_RANGE_DAYS} days")


def connect(dev: dict):
    zk = ZK(
        dev["ip"],
        port=int(dev.get("port", 4370)),
        timeout=DEVICE_TIMEOUT,
        password=int(dev.get("password", 0)),
        force_udp=bool(dev.get("force_udp", False)),
        ommit_ping=bool(dev.get("ommit_ping", True)),
    )
    last_err = None
    for _ in range(2):  # one retry: the machine is sometimes busy with the ERP cron
        try:
            return zk.connect()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def save_rows(rows, users):
    with _db_lock:
        conn = db()
        try:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO checkins(device,user_id,ts,punch,status,log_type,fetched_at)"
                " VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            new = conn.total_changes - before
            if users:
                conn.executemany(
                    "INSERT INTO users(user_id,name) VALUES (?,?)"
                    " ON CONFLICT(user_id) DO UPDATE SET name=excluded.name"
                    " WHERE excluded.name IS NOT NULL AND excluded.name != ''",
                    users,
                )
            conn.commit()
            return new
        finally:
            conn.close()


# ---------------------------------------------------------------- device work
def pull_device(dev: dict, start: date, end: date) -> dict:
    name = dev["name"]
    res = {"device": name, "ip": dev["ip"], "ok": False, "on_device": 0,
           "in_range": 0, "new": 0, "error": None, "seconds": 0}
    t0 = time.time()
    lock = device_lock(name)
    if not lock.acquire(timeout=90):
        res["error"] = "Machine busy (another sync is still running)"
        return res
    conn = None
    try:
        conn = connect(dev)
        users = []
        try:
            users = [(str(u.user_id), (u.name or "").strip()) for u in conn.get_users() or []]
        except Exception:  # noqa: BLE001  - still fetch logs if names fail
            pass
        records = conn.get_attendance() or []
        res["on_device"] = len(records)

        lo = datetime.combine(start, datetime.min.time())
        hi = datetime.combine(end + timedelta(days=1), datetime.min.time())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (name, str(a.user_id), a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
             getattr(a, "punch", None), getattr(a, "status", None),
             dev.get("log_type"), now)
            for a in records
            if a.user_id and a.timestamp and lo <= a.timestamp < hi
        ]
        res["in_range"] = len(rows)
        res["new"] = save_rows(rows, users)
        res["ok"] = True
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e) or e.__class__.__name__
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:  # noqa: BLE001
                pass
        lock.release()
        res["seconds"] = round(time.time() - t0, 1)
    return res


def run_sync(start: date, end: date) -> list[dict]:
    devices = load_devices()
    with ThreadPoolExecutor(max_workers=max(1, len(devices))) as pool:
        results = list(pool.map(lambda d: pull_device(d, start, end), devices))
    _last_sync.update(at=datetime.now().isoformat(timespec="seconds"),
                      start=start.isoformat(), end=end.isoformat(), results=results)
    return results


def device_status(dev: dict) -> dict:
    res = {"device": dev["name"], "ip": dev["ip"], "port": dev.get("port", 4370),
           "log_type": dev.get("log_type"), "online": False, "error": None,
           "device_time": None, "firmware": None, "records": None}
    lock = device_lock(dev["name"])
    if not lock.acquire(timeout=5):
        res["error"] = "Sync in progress"
        return res
    conn = None
    try:
        conn = connect(dev)
        res["online"] = True
        res["device_time"] = conn.get_time().strftime("%Y-%m-%d %H:%M:%S")
        res["firmware"] = conn.get_firmware_version()
        try:
            conn.read_sizes()
            res["records"] = conn.records
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e) or e.__class__.__name__
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:  # noqa: BLE001
                pass
        lock.release()
    return res


def auto_sync_loop():
    while True:
        time.sleep(AUTO_SYNC_MINUTES * 60)
        try:
            today = date.today()
            run_sync(today - timedelta(days=1), today)  # yesterday + today, so late-night punches are not missed
        except Exception as e:  # noqa: BLE001
            print("Auto sync error:", e)


# ---------------------------------------------------------------- API
app = FastAPI(title="Biometric Checkins")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()
    if AUTO_SYNC_MINUTES > 0:
        threading.Thread(target=auto_sync_loop, daemon=True).start()


@app.get("/api/config")
def config():
    return {"auto_sync_minutes": AUTO_SYNC_MINUTES, "last_sync": _last_sync}


@app.get("/api/devices")
def devices():
    return load_devices()


@app.get("/api/devices/status")
def devices_status():
    devs = load_devices()
    with ThreadPoolExecutor(max_workers=max(1, len(devs))) as pool:
        return list(pool.map(device_status, devs))


@app.post("/api/sync")
def sync(start: str = Query(...), end: str | None = Query(None)):
    s = parse_day(start)
    e = parse_day(end) if end else s
    check_range(s, e)
    results = run_sync(s, e)
    return {
        "start": s.isoformat(), "end": e.isoformat(), "results": results,
        "new_total": sum(r["new"] for r in results),
        "failed": [r["device"] for r in results if not r["ok"]],
    }


def query_checkins(day: date, device: str | None, employee: str | None):
    sql = ("SELECT c.device, c.user_id, COALESCE(u.name,'') AS name, c.ts, c.punch, c.status, c.log_type"
           " FROM checkins c LEFT JOIN users u ON u.user_id = c.user_id"
           " WHERE c.ts >= ? AND c.ts < ?")
    args: list = [day.isoformat(), (day + timedelta(days=1)).isoformat()]
    if device:
        sql += " AND c.device = ?"
        args.append(device)
    if employee:
        sql += " AND (c.user_id = ? OR u.name LIKE ?)"
        args += [employee, f"%{employee}%"]
    sql += " ORDER BY c.ts"
    conn = db()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@app.get("/api/checkins")
def checkins(date_: str = Query(..., alias="date"), device: str | None = None, employee: str | None = None):
    return query_checkins(parse_day(date_), device, employee)


def build_summary(rows):
    people: dict[str, dict] = {}
    for r in rows:
        p = people.setdefault(r["user_id"], {"user_id": r["user_id"], "name": r["name"],
                                             "first": r["ts"], "last": r["ts"],
                                             "punches": 0, "devices": set()})
        p["punches"] += 1
        p["last"] = r["ts"]
        p["devices"].add(r["device"])
    out = []
    for p in people.values():
        f = datetime.fromisoformat(p["first"])
        l = datetime.fromisoformat(p["last"])
        p["hours"] = round((l - f).total_seconds() / 3600, 2) if p["punches"] > 1 else 0
        p["devices"] = sorted(p["devices"])
        out.append(p)
    return sorted(out, key=lambda x: x["first"])


@app.get("/api/summary")
def summary(date_: str = Query(..., alias="date"), device: str | None = None, employee: str | None = None):
    return build_summary(query_checkins(parse_day(date_), device, employee))


@app.get("/api/export.csv")
def export_csv(date_: str = Query(..., alias="date"), kind: str = "punches"):
    day = parse_day(date_)
    rows = query_checkins(day, None, None)
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "summary":
        w.writerow(["Employee ID", "Name", "First Punch", "Last Punch", "Hours", "Punches", "Machines"])
        for p in build_summary(rows):
            w.writerow([p["user_id"], p["name"], p["first"], p["last"], p["hours"], p["punches"], " | ".join(p["devices"])])
    else:
        w.writerow(["Employee ID", "Name", "Time", "Machine", "Log Type", "Punch", "Status"])
        for r in rows:
            w.writerow([r["user_id"], r["name"], r["ts"], r["device"], r["log_type"] or "", r["punch"], r["status"]])
    buf.seek(0)
    fname = f"checkins_{kind}_{day.isoformat()}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/summary_erp")
def summary_erp(date_: str = Query(..., alias="date"), device: str | None = None, employee: str | None = None):
    """Summary plus ERP shift/name (summary still works without ERP)."""
    day = parse_day(date_)
    rows = build_summary(query_checkins(day, device, employee))
    info = erp.enrich_summary(day, rows, query_checkins(day, None, None), load_devices())
    return {"rows": rows, **info}


@app.get("/api/erp/status")
def erp_status():
    return erp.status()


@app.get("/api/compare")
def compare(date_: str = Query(..., alias="date")):
    day = parse_day(date_)
    try:
        return erp.compare(day, query_checkins(day, None, None), load_devices())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load data from ERP: {e}")


# Serve the React build from this server (production)
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
