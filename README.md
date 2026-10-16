# Biometric Checkins (ERP se alag)

Ye app 12 ZKTeco machines se **LAN par direct** (IP:4370) logs uthati hai, apne local SQLite database mein save karti hai, aur React screen par dikhati hai. ERP band ho, ya ERP ka 24-ghante wala filter ho, is app par koi asar nahi parta.

Browser khud machine ke TCP port 4370 se baat nahi kar sakta. Is liye ek chhota Python backend (pyzk) machines se connect karta hai, aur React us backend se data leta hai.

```
React (browser) ──HTTP──▶ backend (FastAPI + pyzk) ──TCP 4370──▶ 12 machines
                                   │
                              checkins.db (SQLite)
```

## 1. Machines ki list (`backend/devices.json`)

Is file mein abhi sample IPs hain. Apni asli list ERP se nikalne ke liye ERP server par ye chalayein:

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
Output copy karke `backend/devices.json` mein paste kar dein.

Har machine ke optional fields: `password` (comm key, default 0), `force_udp` (purani machines ke liye `true`), `active`.

## 2. Install

Server/PC par Python 3.9+ aur Node 18+ chahiye, aur wo machines wale network (10.1.1.x) mein hona chahiye.

```bash
# backend
cd biometric-checkins/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# frontend (ek dafa build)
cd ../frontend
npm install
npm run build
```

## 3. Chalayein

```bash
cd biometric-checkins/backend
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8800
```

Browser mein kholein: `http://<server-ip>:8800`

Settings (environment variables):

| Variable | Default | Matlab |
|---|---|---|
| `AUTO_SYNC_MINUTES` | 30 | Har itne minute baad kal + aaj ke logs khud fetch. `0` = band |
| `DEVICE_TIMEOUT` | 10 | Machine connect timeout (seconds) |
| `DB_FILE` | `backend/checkins.db` | Local database |

## 4. Hamesha chalta rahe (systemd)

`/etc/systemd/system/biometric-checkins.service`:
```ini
[Unit]
Description=Biometric Checkins
After=network-online.target

[Service]
User=ats-hr
WorkingDirectory=/home/ats-hr/biometric-checkins/backend
Environment=AUTO_SYNC_MINUTES=30
ExecStart=/home/ats-hr/biometric-checkins/backend/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8800
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now biometric-checkins
```

## Istemal

- **Date chunein → "Machines se fetch karo"**: 12 machines se ek saath (parallel) us din ke logs aate hain. Purani date bhi chalegi, jab tak machine ki memory mein logs mojood hain.
- **Employee summary**: har employee ka pehla/aakhri punch, ghante, kitne punch, kis machine par. Sirf 1 punch wale "missing" dikhte hain.
- **Saare punches**: har punch alag line mein.
- **Machines**: connection check, machine ka waqt (agar machine ka clock galat ho to yahin pata chalega), record count, aur aakhri fetch ka result ya error.
- **CSV**: summary ya saare punches download.

Duplicate punches save nahi hote (machine + employee + waqt unique hai), is liye fetch jitni dafa marzi dabayein.

## Dhyan rahe

- ZKTeco machine ek waqt mein aam tor par ek hi connection leti hai. ERP ka 5-minute cron bhi isi waqt connect kare to us machine par "busy/timeout" aa sakta hai. App ek retry khud karti hai. Phir bhi fail ho to thori dair baad dobara fetch karein.
- Backend development ke liye: `npm run dev` (frontend, port 5173) + uvicorn port 8800. Vite `/api` ko khud 8800 par bhej deta hai.
