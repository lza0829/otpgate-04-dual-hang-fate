import React, { useEffect, useState } from 'react'
import { api } from '../api'

const REFUSED = '双挂拒投'

export default function Receipts() {
  const [sends, setSends] = useState([])
  const [rows, setRows] = useState([])
  const [sid, setSid] = useState('')
  const [result, setResult] = useState('成功')
  const [msg, setMsg] = useState('')
  const load = async () => {
    try {
      const s = await api('/投递单')
      setSends(s)
      if (!sid && s[0]) setSid(String(s[0].编号))
      setRows(await api('/回执'))
    } catch (e) { setMsg(e.message) }
  }
  useEffect(() => { load() }, [])
  const selected = sends.find((s) => String(s.编号) === sid)
  const terminalRefused = selected && selected.状态 === REFUSED
  const submit = async () => {
    try {
      await api(`/投递单/${sid}/回执`, { method: 'POST', body: JSON.stringify({ 结果: result, 厂商码: 'OK' }) })
      setMsg('回执已记')
      load()
    } catch (e) {
      setMsg(e.message)
      load()
    }
  }
  return (
    <div className="panel">
      <h2>回执簿</h2>
      <p className="msg">运营商送达回执写入与晚到改写；系统「拒投」回执随双挂硬拒同事务留痕</p>
      <div className="row">
        <select value={sid} onChange={(e) => setSid(e.target.value)}>
          {sends.map((s) => <option key={s.编号} value={s.编号}>{s.投递单号} · {s.状态}</option>)}
        </select>
        <select value={result} onChange={(e) => setResult(e.target.value)} disabled={terminalRefused}>
          <option value="成功">成功</option>
          <option value="失败">失败</option>
        </select>
        <button type="button" onClick={submit} disabled={terminalRefused}>写入回执</button>
        {terminalRefused && <span className="bad">双挂拒投终态不可写回执</span>}
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单号</th><th>通道</th><th>结果</th><th>厂商码</th><th>时间</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.结果 === '拒投' ? 'row-refused' : ''}>
                <td>{r.投递单号}</td><td>{r.通道}</td>
                <td>
                  {r.结果 === '拒投'
                    ? <span className="tag tag-refuse">拒投</span>
                    : r.结果}
                </td>
                <td>{r.厂商码}</td><td>{r.时间}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><span className="msg">{msg}</span><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
