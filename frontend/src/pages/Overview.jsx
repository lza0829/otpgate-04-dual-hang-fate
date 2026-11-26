import React, { useEffect, useState } from 'react'
import { api } from '../api'

const REFUSED = '双挂拒投'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const verdict = data.双挂判定 || {}
  return (
    <div className="panel">
      <h2>路况</h2>
      <div className="verdict" data-double={verdict.双挂 ? '是' : '否'}>
        <div>
          <span className={`tag ${verdict.双挂 ? 'tag-refuse' : 'tag-ok'}`}>
            {verdict.双挂 ? '主备双挂 · 硬拒' : '有可用通道 · 可投'}
          </span>
          <span className="msg"> 主路况：{verdict.主路况} ｜ 备路况：{verdict.备路况}</span>
        </div>
        <div className="msg">硬投标：{verdict.硬投标 === null ? '恒无（硬投已废止）' : verdict.硬投标} ｜ {verdict.依据}</div>
      </div>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className={`stat ${k === REFUSED ? 'stat-refuse' : ''}`} key={k}>
            <div className="n">{v}</div><div className="l">{k}</div>
          </div>
        ))}
      </div>

      <h2>双挂拒投单（独立桶，不计在途/成功）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单号</th><th>状态</th><th>原因</th><th>时间</th></tr></thead>
          <tbody>
            {(data.双挂拒投单 || []).length === 0 && (
              <tr><td colSpan={4} className="msg">暂无双挂拒投单</td></tr>
            )}
            {(data.双挂拒投单 || []).map((r) => (
              <tr key={r.编号} className="row-refused">
                <td>{r.投递单号}</td>
                <td><span className="tag tag-refuse">{r.状态}</span></td>
                <td className="bad">{r.原因}</td><td>{r.时间}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>在途投递</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单号</th><th>手机掩码</th><th>场景</th><th>状态</th><th>当前通道</th></tr></thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr key={r.编号}><td>{r.投递单号}</td><td>{r.手机掩码}</td><td>{r.场景}</td><td>{r.状态}</td><td>{r.当前通道 || '-'}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>通道态势（启停灯）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>代号</th><th>名称</th><th>角色</th><th>状态</th><th>权重</th><th>连续失败</th></tr></thead>
          <tbody>
            {(data.通道 || []).map((c) => (
              <tr key={c.代号}>
                <td>{c.代号}</td><td>{c.名称}</td><td>{c.角色}</td>
                <td><span className={`lamp lamp-${c.状态}`}>{c.状态}</span></td>
                <td>{c.权重}</td><td>{c.连续失败}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
