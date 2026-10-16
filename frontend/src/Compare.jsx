import { useEffect, useMemo, useState } from 'react'

const STATUS = {
  match: ['Match', 'ok'],
  missing_in_erp: ['Missing in ERP', 'bad'],
  extra_in_erp: ['Only in ERP', 'warn'],
  different: ['Different', 'bad'],
  no_employee: ['Employee not in ERP', 'warn'],
}
const SRC = { erp: 'ERP checkin', assignment: 'Shift Assignment', default: 'Default shift', time: 'Guessed from time' }
const hhmm = (t) => t.slice(11, 16)

export default function Compare({ day, search }) {
  const [data, setData] = useState(null)
  const [erpStatus, setErpStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [shift, setShift] = useState('')
  const [only, setOnly] = useState('mismatch')

  const load = async () => {
    setLoading(true); setError('')
    try {
      const res = await fetch(`/api/compare?date=${day}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || res.statusText)
      setData(body)
    } catch (e) { setError(e.message); setData(null) } finally { setLoading(false) }
  }

  useEffect(() => { fetch('/api/erp/status').then((r) => r.json()).then(setErpStatus).catch(() => {}) }, [])
  useEffect(() => { load() }, [day]) // eslint-disable-line

  const term = search.trim().toLowerCase()
  const rows = useMemo(() => {
    if (!data?.employees) return []
    return data.employees.filter((r) =>
      (!shift || (r.shift || '(no shift)') === shift) &&
      (only === 'all' || (only === 'mismatch' ? r.status !== 'match' : r.status === only)) &&
      (!term || [r.employee, r.device_uid, r.name, r.department].some((v) => (v || '').toLowerCase().includes(term))))
  }, [data, shift, only, term])

  if (data && !data.configured) {
    return (
      <div className="empty left">
        <strong>ERP is not linked yet.</strong>
        <p>Create <code>backend/erp.json</code> (copy <code>erp.example.json</code>), add your ERP API key and secret, then restart the backend. Steps are in the README.</p>
      </div>
    )
  }

  const t = data?.totals
  return (
    <div>
      <div className="row-actions">
        <button onClick={load} disabled={loading}>{loading ? 'Loading from ERP…' : 'Compare again'}</button>
        {erpStatus && (erpStatus.ok
          ? <span className="muted">ERP: {erpStatus.url} · user {erpStatus.user}</span>
          : erpStatus.configured && <span className="err">ERP connection failed: {erpStatus.error}</span>)}
      </div>

      {error && <div className="alert">⚠ {error}</div>}

      {t && (
        <>
          <section className="stats">
            <div className="stat"><span className="label">Machine punches</span><span className="value">{t.machine}</span></div>
            <div className="stat"><span className="label">ERP checkins</span><span className="value">{t.erp}</span></div>
            <div className={`stat ${t.missing_in_erp ? 'bad' : ''}`}><span className="label">Missing in ERP</span><span className="value">{t.missing_in_erp}</span><span className="hint">on machines, not in ERP</span></div>
            <div className={`stat ${t.extra_in_erp ? 'warn' : ''}`}><span className="label">Only in ERP</span><span className="value">{t.extra_in_erp}</span><span className="hint">in ERP, not fetched from machines</span></div>
          </section>

          <div className="two-col">
            <div>
              <h3>Shift-wise</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Shift</th><th>Employees</th><th>Machine</th><th>ERP</th><th>Missing</th><th>Mismatched</th></tr></thead>
                  <tbody>
                    {data.by_shift.map((s) => (
                      <tr key={s.shift} className={shift === s.shift ? 'selected' : ''} onClick={() => setShift(shift === s.shift ? '' : s.shift)} style={{ cursor: 'pointer' }}>
                        <td><strong>{s.shift}</strong></td><td>{s.employees}</td><td>{s.machine}</td><td>{s.erp}</td>
                        <td className={s.missing_in_erp ? 'neg' : ''}>{s.missing_in_erp}</td><td>{s.mismatch}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">Click a shift to filter the employee list below.</p>
            </div>
            <div>
              <h3>Machine-wise</h3>
              <div className="table-wrap short">
                <table>
                  <thead><tr><th>Machine</th><th>On machine</th><th>In ERP</th><th>Difference</th></tr></thead>
                  <tbody>
                    {data.by_device.map((d) => {
                      const diff = d.machine - d.erp
                      return (
                        <tr key={d.device}>
                          <td>{d.device}</td><td>{d.machine}</td><td>{d.erp}</td>
                          <td className={diff > 0 ? 'neg' : diff < 0 ? 'warn-text' : ''}>{diff > 0 ? `+${diff}` : diff}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="tabs" style={{ marginTop: 18 }}>
            <h3 style={{ margin: 0 }}>Employees</h3>
            <div className="spacer" />
            <select value={shift} onChange={(e) => setShift(e.target.value)}>
              <option value="">All shifts</option>
              {data.by_shift.map((s) => <option key={s.shift} value={s.shift}>{s.shift}</option>)}
            </select>
            <select value={only} onChange={(e) => setOnly(e.target.value)}>
              <option value="mismatch">Mismatches only</option>
              <option value="all">All</option>
              {Object.entries(STATUS).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>

          {rows.length === 0
            ? <div className="empty">{only === 'mismatch' ? 'No differences — machine and ERP match.' : 'No records.'}</div>
            : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Employee</th><th>Name</th><th>Shift</th><th>Machine</th><th>ERP</th><th>Status</th><th>Missing in ERP</th><th>Only in ERP</th><th>Attendance</th></tr></thead>
                  <tbody>
                    {rows.map((r) => {
                      const [label, tone] = STATUS[r.status]
                      return (
                        <tr key={r.employee || r.device_uid}>
                          <td className="mono">{r.employee || <span className="muted">uid {r.device_uid}</span>}</td>
                          <td>{r.name || '—'}{r.department && <div className="muted small">{r.department}</div>}</td>
                          <td>{r.shift || '—'}{r.shift_source && <div className="muted small">{SRC[r.shift_source]}</div>}</td>
                          <td>{r.machine_count}</td>
                          <td>{r.erp_count}</td>
                          <td><span className={`tag ${tone}`}>{label}</span></td>
                          <td className="chips">{r.missing_in_erp.map((x) => <span key={x} className="chip bad">{hhmm(x)}</span>)}</td>
                          <td className="chips">{r.extra_in_erp.map((x) => <span key={x} className="chip warn">{hhmm(x)}</span>)}</td>
                          <td>{r.attendance_linked ? <span className="tag ok">Linked</span> : <span className="muted">—</span>}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </>
      )}
    </div>
  )
}
