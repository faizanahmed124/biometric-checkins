"""
Read-only link to ERPNext / Frappe HRMS.
Reads Employee Checkin, Employee, Shift Assignment and Shift Type over the REST API,
and compares them with the locally stored machine checkins.
"""
import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

CONF_FILE = Path(os.getenv("ERP_CONFIG", Path(__file__).resolve().parent / "erp.json"))
CACHE_SECONDS = 600
_cache: dict = {}


def load_conf():
    if not CONF_FILE.exists():
        return None
    with open(CONF_FILE, encoding="utf-8") as f:
        c = json.load(f)
    if not c.get("url") or not c.get("api_key") or not c.get("api_secret"):
        return None
    return c


class ERP:
    def __init__(self, conf: dict):
        headers = {
            "Authorization": f"token {conf['api_key']}:{conf['api_secret']}",
            "Accept": "application/json",
        }
        if conf.get("site"):  # when ERP is reached by IP, Frappe needs the site name
            headers["Host"] = conf["site"]
            headers["X-Frappe-Site-Name"] = conf["site"]
        self.client = httpx.Client(base_url=conf["url"].rstrip("/"), headers=headers,
                                   timeout=60, verify=conf.get("verify_ssl", True))

    def _get(self, path, params=None):
        r = self.client.get(path, params=params)
        if r.status_code >= 400:
            text = r.text
            try:
                text = r.json().get("exception") or r.json().get("message") or text
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"ERP HTTP {r.status_code} on {path}: {str(text)[:300]}")
        return r.json()

    def whoami(self):
        return self._get("/api/method/frappe.auth.get_logged_user").get("message")

    def get_list(self, doctype, fields, filters=None):
        params = {"fields": json.dumps(fields), "limit_page_length": 0}
        if filters:
            params["filters"] = json.dumps(filters)
        return self._get(f"/api/resource/{doctype}", params).get("data", [])

    # ---- cached master data
    def _cached(self, key, fn):
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        val = fn()
        _cache[key] = (time.time(), val)
        return val

    def employees(self):
        return self._cached("employees", lambda: self.get_list(
            "Employee",
            ["name", "employee_name", "department", "default_shift", "attendance_device_id", "status"]))

    def shift_types(self):
        return self._cached("shift_types", lambda: self.get_list(
            "Shift Type",
            ["name", "start_time", "end_time",
             "begin_check_in_before_shift_start_time", "allow_check_out_after_shift_end_time"]))

    def shift_assignments(self, day: date):
        rows = self.get_list(
            "Shift Assignment", ["employee", "shift_type", "start_date", "end_date"],
            [["docstatus", "=", 1], ["status", "=", "Active"], ["start_date", "<=", day.isoformat()]])
        return [r for r in rows if not r.get("end_date") or r["end_date"] >= day.isoformat()]

    def checkins(self, day: date):
        return self.get_list(
            "Employee Checkin",
            ["name", "employee", "employee_name", "time", "log_type", "shift", "device_id", "attendance"],
            [["time", ">=", f"{day.isoformat()} 00:00:00"],
             ["time", "<", f"{(day + timedelta(days=1)).isoformat()} 00:00:00"]])


def _td(value) -> timedelta:
    """Frappe time '7:00:00' / '07:00:00.000000' -> timedelta"""
    if value in (None, ""):
        return timedelta()
    parts = str(value).split(".")[0].split(":")
    h, m, s = (int(p) for p in (parts + ["0", "0"])[:3])
    return timedelta(hours=h, minutes=m, seconds=s)


def _norm(ts: str) -> str:
    return str(ts)[:19]


def guess_shift(ts: str, day: date, shift_types: list) -> str | None:
    """Which shift window a punch falls in (only used when ERP/assignment has no shift)."""
    t = datetime.fromisoformat(_norm(ts))
    base = datetime.combine(day, datetime.min.time())
    for st in shift_types:
        start, end = _td(st.get("start_time")), _td(st.get("end_time"))
        if end <= start:
            end += timedelta(days=1)
        lo = base + start - timedelta(minutes=int(st.get("begin_check_in_before_shift_start_time") or 0))
        hi = base + end + timedelta(minutes=int(st.get("allow_check_out_after_shift_end_time") or 0))
        if lo <= t <= hi:
            return st["name"]
    return None


def compare(day: date, machine_rows: list, devices: list) -> dict:
    conf = load_conf()
    if not conf:
        return {"configured": False}
    erp = ERP(conf)

    employees = erp.employees()
    shift_types = erp.shift_types()
    assignments = erp.shift_assignments(day)
    erp_rows = erp.checkins(day)

    by_emp = {e["name"]: e for e in employees}
    device_uid_to_emp = {}
    for e in employees:
        device_uid_to_emp[str(e["name"])] = e["name"]
    for e in employees:  # attendance_device_id takes priority
        if e.get("attendance_device_id"):
            device_uid_to_emp[str(e["attendance_device_id"])] = e["name"]
    assigned = {a["employee"]: a["shift_type"] for a in assignments}

    machine = defaultdict(lambda: {"times": set(), "devices": set()})
    unknown_uids = set()
    for r in machine_rows:
        emp = device_uid_to_emp.get(str(r["user_id"]))
        key = emp or f"uid:{r['user_id']}"
        if not emp:
            unknown_uids.add(str(r["user_id"]))
        machine[key]["times"].add(_norm(r["ts"]))
        machine[key]["devices"].add(r["device"])
        machine[key].setdefault("uid", str(r["user_id"]))
        machine[key].setdefault("machine_name", r.get("name") or "")

    erp_emp = defaultdict(lambda: {"times": set(), "shifts": Counter(), "attendance": False})
    for r in erp_rows:
        e = erp_emp[r["employee"]]
        e["times"].add(_norm(r["time"]))
        if r.get("shift"):
            e["shifts"][r["shift"]] += 1
        if r.get("attendance"):
            e["attendance"] = True

    rows = []
    for key in sorted(set(machine) | set(erp_emp)):
        m, e = machine.get(key), erp_emp.get(key)
        m_times = m["times"] if m else set()
        e_times = e["times"] if e else set()
        emp = by_emp.get(key)

        if e and e["shifts"]:
            shift, shift_src = e["shifts"].most_common(1)[0][0], "erp"
        elif key in assigned:
            shift, shift_src = assigned[key], "assignment"
        elif emp and emp.get("default_shift"):
            shift, shift_src = emp["default_shift"], "default"
        else:
            first = min(m_times | e_times) if (m_times | e_times) else None
            shift = guess_shift(first, day, shift_types) if first else None
            shift_src = "time" if shift else None

        missing = sorted(m_times - e_times)
        extra = sorted(e_times - m_times)
        if key.startswith("uid:"):
            status = "no_employee"
        elif not missing and not extra:
            status = "match"
        elif missing and not extra:
            status = "missing_in_erp"
        elif extra and not missing:
            status = "extra_in_erp"
        else:
            status = "different"

        rows.append({
            "employee": None if key.startswith("uid:") else key,
            "device_uid": (m or {}).get("uid") or (emp or {}).get("attendance_device_id") or key,
            "name": (emp or {}).get("employee_name") or (m or {}).get("machine_name") or "",
            "department": (emp or {}).get("department") or "",
            "shift": shift or "",
            "shift_source": shift_src,
            "machine_count": len(m_times),
            "erp_count": len(e_times),
            "missing_in_erp": missing,
            "extra_in_erp": extra,
            "attendance_linked": bool(e and e["attendance"]),
            "devices": sorted(m["devices"]) if m else [],
            "status": status,
        })

    # shift-wise
    shifts = defaultdict(lambda: {"employees": 0, "machine": 0, "erp": 0, "missing_in_erp": 0, "extra_in_erp": 0, "mismatch": 0})
    for r in rows:
        s = shifts[r["shift"] or "(no shift)"]
        s["employees"] += 1
        s["machine"] += r["machine_count"]
        s["erp"] += r["erp_count"]
        s["missing_in_erp"] += len(r["missing_in_erp"])
        s["extra_in_erp"] += len(r["extra_in_erp"])
        s["mismatch"] += r["status"] != "match"
    by_shift = [{"shift": k, **v} for k, v in sorted(shifts.items())]

    # machine-wise (ERP device_id = Biometric Device name)
    m_dev = Counter(r["device"] for r in machine_rows)
    e_dev = Counter((r.get("device_id") or "(no device_id)") for r in erp_rows)
    names = [d["name"] for d in devices]
    by_device = [{"device": n, "machine": m_dev.get(n, 0), "erp": e_dev.get(n, 0)} for n in names]
    by_device += [{"device": n, "machine": m_dev.get(n, 0), "erp": c} for n, c in e_dev.items() if n not in names]

    return {
        "configured": True,
        "date": day.isoformat(),
        "totals": {
            "machine": sum(r["machine_count"] for r in rows),
            "erp": sum(r["erp_count"] for r in rows),
            "missing_in_erp": sum(len(r["missing_in_erp"]) for r in rows),
            "extra_in_erp": sum(len(r["extra_in_erp"]) for r in rows),
            "employees": len(rows),
            "mismatch_employees": sum(r["status"] != "match" for r in rows),
            "unknown_uids": len(unknown_uids),
        },
        "by_shift": by_shift,
        "by_device": by_device,
        "employees": rows,
    }


def enrich_summary(day: date, summary_rows: list, machine_rows: list, devices: list) -> dict:
    """Adds ERP shift, name, department and ERP count to employee summary rows."""
    if not load_conf():
        return {"erp": "not_configured"}
    try:
        cmp = compare(day, machine_rows, devices)
    except Exception as e:  # noqa: BLE001
        return {"erp": "error", "erp_error": str(e)}
    by_uid = {str(r["device_uid"]): r for r in cmp["employees"]}
    for s in summary_rows:
        r = by_uid.get(str(s["user_id"]))
        if not r:
            continue
        s["employee"] = r["employee"]
        s["erp_name"] = r["name"]
        s["department"] = r["department"]
        s["shift"] = r["shift"]
        s["shift_source"] = r["shift_source"]
        s["erp_count"] = r["erp_count"]
        s["missing_in_erp"] = len(r["missing_in_erp"])
    return {"erp": "ok"}


def status() -> dict:
    conf = load_conf()
    if not conf:
        return {"configured": False}
    try:
        return {"configured": True, "ok": True, "user": ERP(conf).whoami(), "url": conf["url"]}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "ok": False, "error": str(e), "url": conf["url"]}
