import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const checks = [
    ['双挂回执差', data.双挂回执差],
    ['双挂事件差', data.双挂事件差],
    ['送达与回执差', data.送达与回执差],
  ]
  return (
    <div className="panel">
      <h2>轧差</h2>
      <div className="grid">
        {Object.entries(data).map(([k, v]) => (
          <div className={`stat ${k.includes('双挂') ? 'stat-refuse' : ''}`} key={k}>
            <div className="n">{v}</div><div className="l">{k}</div>
          </div>
        ))}
      </div>
      <h2>同刷对照（差异恒为 0）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>对照项</th><th>差异</th><th>结论</th></tr></thead>
          <tbody>
            {checks.map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td><td>{v}</td>
                <td>{v === 0 ? <span className="ok">自洽</span> : <span className="bad">不一致</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="msg">
        双挂拒投独立成桶：拒投回执数与双挂硬拒事件数必须等于「双挂拒投单 × 通道数」，
        不并入已送达，不存在无痕迹送达。
      </p>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
