# Biometric Checkins

Fetches attendance logs **directly** from the ZKTeco biometric machines over the LAN (IP + port), stores them in a local SQLite database, and shows them in a React UI. It works independently of ERP, so ERP downtime or ERP's 24-hour import filter does not affect it. Optionally, it reads ERP (read-only) to show shifts and compare machine punches with ERP checkins.

A browser cannot talk to a machine's TCP port directly, so a small Python backend (FastAPI + pyzk) connects to the machines and the React app talks to that backend.

```
React (browser) ──HTTP──▶ backend (FastAPI + pyzk) ──TCP──▶ biometric machines
                                │        └──REST (read-only)──▶ ERPNext / HRMS
                           checkins.db (SQLite)
```

## 1. Machine list (`backend/devices.json`)

One entry per machine:

```json
{"name": "20.1.10.245", "ip": "20.1.10.245", "port": 4370, "log_type": "IN", "location": "Main Gate", "password": 0, "active": true}
```

- `name` should match the Biometric Device name in ERP (used for the machine-wise comparison).
- Optional: `password` (comm key, default 0), `force_udp` (`true` for older machines), `active`.

To export the list from ERP, run on the ERP server:

```bash
cd ~/frappe-bench
bench --site atshr.com console
```
```python
import json
devs = frappe.get_all("Biometric Device", filters={"is_active": 1}, fields=["name", "device_ip", "port", "log_type"])
out = [{"name": d.name, "ip": d.device_ip, "port": int(d.port or 4370), "log_type": d.log_type or "IN", "password": 0, "active": True} for d in devs if d.device_ip and d.device_ip != "None"]
print(json.dumps(out, indent=2))
```

## 2. ERP link (shifts + comparison)

The app only **reads** from ERP. It never writes there.

1. Use an ERP user with **read** permission on Employee Checkin, Employee, Shift Assignment and Shift Type.
2. Open that user → **Settings → API Access → Generate Keys**. Copy the API Key and API Secret (the secret is shown only once).
3. Copy `backend/erp.example.json` to `backend/erp.json` and fill it in:
   ```json
   {
     "url": "http://10.1.1.98:8000",
     "site": "atshr.com",
     "api_key": "...",
     "api_secret": "...",
     "verify_ssl": true
   }
   ```
   `site` is needed because ERP is reached by IP, so Frappe must be told which site to serve.
4. Restart the backend. `curl -s localhost:8800/api/erp/status` should return `"ok": true`.

How the comparison works:
- A machine user ID is matched to an ERP Employee by `attendance_device_id` first, otherwise by Employee ID.
- Punches are matched per employee by **exact time**. "Missing in ERP" = on a machine but not in ERP. "Only in ERP" = in ERP but not in this app's machine data.
- Shift source, in order: the shift on the ERP checkin → active Shift Assignment → the employee's Default Shift → guessed from the punch time.
- The machine-wise difference uses the ERP checkin's `device_id`. Older checkins without a `device_id` are grouped under "(no device_id)".
- Fetch the day from the machines first, then open the comparison.

## 3. Install

Needs Python 3.9+ and Node 18+, on a computer that can reach both the machines and ERP.

```bash
# backend
cd biometric-checkins/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# frontend (build once, and again after every update)
cd ../frontend
npm install
npm run build
```

## 4. Run

```bash
cd biometric-checkins/backend
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8800
```

Open `http://<computer-ip>:8800` in a browser.

Settings (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_SYNC_MINUTES` | 30 | Every N minutes, fetch yesterday + today automatically. `0` = off |
| `DEVICE_TIMEOUT` | 10 | Machine connection timeout (seconds) |
| `DB_FILE` | `backend/checkins.db` | Local database |
| `ERP_CONFIG` | `backend/erp.json` | ERP connection file |

## 5. Keep it running (systemd)

`/etc/systemd/system/biometric-checkins.service`:
```ini
[Unit]
Description=Biometric Checkins
After=network-online.target

[Service]
User=faizan
WorkingDirectory=/home/faizan/Downloads/biometric-checkins/backend
Environment=AUTO_SYNC_MINUTES=30
ExecStart=/home/faizan/Downloads/biometric-checkins/backend/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8800
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now biometric-checkins
```

## Using the app

- **Pick a date → "Fetch from machines"**: pulls that day's logs from all machines in parallel. Past dates work as long as the logs are still in the machine's memory.
- **Employee summary**: first/last punch, hours, punch count and machines per employee. With ERP linked, it also shows the shift, ERP name and department, and the ERP checkin count (a red `(−n)` means n punches are missing in ERP). Filter by shift.
- **All punches**: every punch on its own row.
- **ERP comparison**: totals, shift-wise and machine-wise tables, and an employee list showing exactly which punch times are missing in ERP or only in ERP.
- **Machines**: connection check, machine clock (spots machines with a wrong clock), record count, and the result or error of the last fetch.
- **CSV**: download the summary or all punches.

Duplicate punches are never stored (machine + employee + time is unique), so you can fetch as often as you like.

## Notes

- A ZKTeco machine usually accepts one connection at a time. If ERP's 5-minute cron connects at the same moment, that machine may report busy/timeout. The app retries once; if it still fails, fetch again a little later.
- Development: run `npm run dev` (frontend on port 5173) alongside uvicorn on port 8800. Vite proxies `/api` to 8800.
