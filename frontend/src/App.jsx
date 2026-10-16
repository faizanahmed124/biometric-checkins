import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Compare from './Compare.jsx'

const today = () => new Date().toLocaleDateString('en-CA') // YYYY-MM-DD
const shiftDay = (d, n) => {
  const x = new Date(d + 'T00:00:00')
  x.setDate(x.getDate() + n)
  return x.toLocaleDateString('en-CA')
}
const hhmm = (ts) => (ts ? ts.slice(11, 16) : '—')

async function api(path, opts) {
  const res = await fetch(path, opts)
  if (!res.ok) {
    let msg = res.statusText
    try { msg = (await res.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

export default function App() {
  const [day, setDay] = useState(today())
  const [tab, setTab] = useState('summary')
  const [search, setSearch] = useState('')
  const [device, setDevice] = useState('')
  const [devices, setDevices] = useState([])
  const [summary, setSummary] = useState([])
  const [punches, setPunches] = useState([])
  const [status, setStatus] = useState(null)
  const [syncResult, setSyncResult] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [config, setConfig] = useState(null)
  const [erpInfo, setErpInfo] = useState(null)
  const [shiftFilter, setShiftFilter] = useState('')

  const reqId = useRef(0)
  const load = useCallback(async () => {
    const id = ++reqId.current // ignore responses for a date the user has already left
    setError('')
    try {
      const q = `date=${day}${device ? `&device=${encodeURIComponent(device)}` : ''}`
      const [s, p] = await Promise.all([api(`/api/summary_erp?${q}`), api(`/api/checkins?${q}`)])
      if (id !== reqId.current) return
      setSummary(s.rows)
      setErpInfo({ erp: s.erp, error: s.erp_error })
      setPunches(p)
    } catch (e) { if (id === reqId.current) setError(e.message) }
  }, [day, device])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api('/api/devices').then(setDevices).catch((e) => setError(e.message))
    api('/api/config').then(setConfig).catch(() => {})
  }, [])

  const fetchFromMachines = async () => {
    setBusy('sync'); setError('')
    try {
      const r = await api(`/api/sync?start=${day}`, { method: 'POST' })
      setSyncResult(r)
      await load()
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const checkMachines = async () => {
    setBusy('status'); setError('')
    try { setStatus(await api('/api/devices/status')) }
    catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const term = search.trim().toLowerCase()
  const match = (r) => !term || [r.user_id, r.name, r.erp_name, r.employee].some((v) => (v || '').toLowerCase().includes(term))
  const fSummary = useMemo(() => summary.filter((r) => match(r) && (!shiftFilter || (r.shift || '(no shift)') === shiftFilter)), [summary, term, shiftFilter]) // eslint-disable-line
  const shiftNames = useMemo(() => [...new Set(summary.map((r) => r.shift || '(no shift)'))].sort(), [summary])
  const erpOn = erpInfo?.erp === 'ok'
  const fPunches = useMemo(() => punches.filter(match), [punches, term]) // eslint-disable-line

  const singlePunch = summary.filter((s) => s.punches === 1).length
  const onlineCount = status ? status.filter((s) => s.online).length : null

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Biometric Checkins</h1>
          <p className="sub">
            {devices.length} machines · direct LAN fetch · v4
            {config?.auto_sync_minutes ? ` · auto sync every ${config.auto_sync_minutes} min` : ''}
          </p>
        </div>
        <div className="controls">
          <button className="ghost" onClick={() => setDay(shiftDay(day, -1))} aria-label="Previous day">‹</button>
          <input type="date" value={day} max={today()} onChange={(e) => e.target.value && setDay(e.target.value)} />
          <button className="ghost" onClick={() => setDay(shiftDay(day, 1))} disabled={day >= today()} aria-label="Next day">›</button>
          <button className="primary" onClick={fetchFromMachines} disabled={!!busy}>
            {busy === 'sync' ? 'Fetching from machines…' : 'Fetch from machines'}
          </button>
        </div>
      </header>

      {error && <div className="alert">⚠ {error}</div>}

      {syncResult && (
        <div className={`banner ${syncResult.failed.length ? 'warn' : 'ok'}`}>
          <strong>{syncResult.new_total}</strong> new checkins fetched ({syncResult.start}).
          {syncResult.failed.length > 0 && <> Failed: {syncResult.failed.join(', ')}. See the Machines tab for details.</>}
          <button className="link" onClick={() => setSyncResult(null)}>×</button>
        </div>
      )}

      <section className="stats">
        <Stat label="Employees" value={summary.length} />
        <Stat label="Total punches" value={punches.length} />
        <Stat label="Single punch" value={singlePunch} hint="IN or OUT missing" tone={singlePunch ? 'warn' : ''} />
        <Stat label="Machines online" value={onlineCount === null ? '—' : `${onlineCount}/${status.length}`}
          tone={onlineCount !== null && onlineCount < status.length ? 'bad' : ''} />
      </section>

      <nav className="tabs">
        {[['summary', 'Employee summary'], ['punches', 'All punches'], ['compare', 'ERP comparison'], ['machines', 'Machines']].map(([k, l]) => (
          <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
        <div className="spacer" />
        {tab === 'compare' && (
          <input className="search" placeholder="Employee ID or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
        )}
        {(tab === 'summary' || tab === 'punches') && (
          <>
            <input className="search" placeholder="Employee ID or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
            {tab === 'summary' && erpOn && (
              <select value={shiftFilter} onChange={(e) => setShiftFilter(e.target.value)}>
                <option value="">All shifts</option>
                {shiftNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            )}
            <select value={device} onChange={(e) => setDevice(e.target.value)}>
              <option value="">All machines</option>
              {devices.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
            </select>
            <a className="btn ghost" href={`/api/export.csv?date=${day}&kind=${tab === 'summary' ? 'summary' : 'punches'}`}>CSV</a>
          </>
        )}
      </nav>

      {tab === 'summary' && erpInfo && !erpOn && (
        <div className="banner warn">
          {erpInfo.erp === 'not_configured'
            ? <>ERP is not linked, so shifts are not shown. Add url, api_key and api_secret to <code>backend/erp.json</code> and restart the backend.</>
            : <>Could not load shifts from ERP: {erpInfo.error}</>}
        </div>
      )}

      {tab === 'summary' && (
        <Table empty={`No checkins for ${day} in the local database. Click "Fetch from machines".`}
          head={erpOn
            ? ['Employee ID', 'Name', 'Shift', 'First punch', 'Last punch', 'Hours', 'Machine punches', 'ERP checkins', 'Machines']
            : ['Employee ID', 'Name', 'First punch', 'Last punch', 'Hours', 'Punches', 'Machines']}
          rows={fSummary.map((s) => {
            const last = s.punches > 1 ? hhmm(s.last) : <span className="tag warn">missing</span>
            const hours = s.hours ? s.hours.toFixed(2) : '—'
            const machines = <span className="muted">{s.devices.join(', ')}</span>
            if (!erpOn) return [s.user_id, s.name || '—', hhmm(s.first), last, hours, s.punches, machines]
            return [
              <>{s.user_id}{s.employee && s.employee !== s.user_id && <div className="muted small">{s.employee}</div>}</>,
              <>{s.erp_name || s.name || '—'}{s.department && <div className="muted small">{s.department}</div>}</>,
              s.shift ? <span className="tag in">{s.shift}</span> : <span className="muted">—</span>,
              hhmm(s.first), last, hours, s.punches,
              s.erp_count === undefined ? <span className="tag warn">Not in ERP</span>
                : <span className={s.missing_in_erp ? 'neg-text' : ''}>{s.erp_count}{s.missing_in_erp ? ` (−${s.missing_in_erp})` : ''}</span>,
              machines,
            ]
          })} />
      )}

      {tab === 'punches' && (
        <Table empty="No punches found."
          head={['Time', 'Employee ID', 'Name', 'Machine', 'Log type', 'Punch', 'Status']}
          rows={fPunches.map((p) => [
            p.ts.slice(11), p.user_id, p.name || '—', p.device,
            p.log_type ? <span className={`tag ${p.log_type === 'OUT' ? 'out' : 'in'}`}>{p.log_type}</span> : '—',
            p.punch ?? '—', p.status ?? '—',
          ])} />
      )}

      {tab === 'compare' && <Compare day={day} search={search} />}

      {tab === 'machines' && (
        <div className="machines">
          <div className="row-actions">
            <button onClick={checkMachines} disabled={!!busy}>{busy === 'status' ? 'Checking…' : 'Check connections'}</button>
            <span className="muted">Connects to each machine and reads its clock and record count.</span>
          </div>
          <div className="grid">
            {devices.map((d) => {
              const st = status?.find((s) => s.device === d.name)
              const sr = syncResult?.results.find((r) => r.device === d.name)
              const state = st ? (st.online ? 'on' : 'off') : 'unknown'
              return (
                <div key={d.name} className={`card ${state}`}>
                  <div className="card-head">
                    <span className={`dot ${state}`} />
                    <strong>{d.name}</strong>
                    {d.log_type && <span className={`tag ${d.log_type === 'OUT' ? 'out' : 'in'}`}>{d.log_type}</span>}
                  </div>
                  <div className="muted mono">{d.ip}:{d.port || 4370}</div>
                  {st?.online && (
                    <dl>
                      <dt>Machine time</dt><dd className="mono">{st.device_time}</dd>
                      <dt>Records</dt><dd>{st.records ?? '—'}</dd>
                      <dt>Firmware</dt><dd className="mono small">{st.firmware || '—'}</dd>
                    </dl>
                  )}
                  {st && !st.online && <p className="err">{st.error}</p>}
                  {sr && (
                    <p className={sr.ok ? 'small' : 'err'}>
                      Last fetch: {sr.ok ? `${sr.in_range} for this day, ${sr.new} new (${sr.seconds}s)` : sr.error}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, hint, tone = '' }) {
  return (
    <div className={`stat ${tone}`}>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {hint && <span className="hint">{hint}</span>}
    </div>
  )
}

function Table({ head, rows, empty }) {
  if (!rows.length) return <div className="empty">{empty}</div>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}
