import React, { useEffect, useState } from 'react'
import { api } from '../api'

const DEPRECATED = '允许双挂硬投'

export default function Settings() {
  const [data, setData] = useState({ 项: {}, 废止项: {} })
  const [msg, setMsg] = useState('')
  const load = () => api('/设置').then(setData).catch((e) => setMsg(e.message))
  useEffect(() => { load() }, [])
  const items = data.项 || {}
  const deprecated = data.废止项 || {}
  const save = async () => {
    try {
      await api('/设置', { method: 'PUT', body: JSON.stringify({ 项: items }) })
      setMsg('已保存')
      load()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="panel">
      <h2>渠规</h2>
      <p className="msg">
        全站唯一架构：主备双挂一律硬拒，整笔失败并留拒投痕；不存在双挂硬投，硬投标恒为空。
      </p>
      {Object.entries(items).map(([k, v]) => {
        const dead = k === DEPRECATED || Boolean(deprecated[k])
        return (
          <div className="row" key={k}>
            <span style={{ minWidth: 160 }}>{k}</span>
            {dead ? (
              <>
                <select value={v} disabled>
                  <option value="是">是</option>
                  <option value="否">否</option>
                </select>
                <span className="bad">已废止 · 不参与双挂判定（拨任何值结论不变）</span>
              </>
            ) : (
              <select value={v} onChange={(e) => setData({ ...data, 项: { ...items, [k]: e.target.value } })}>
                <option value="是">是</option>
                <option value="否">否</option>
              </select>
            )}
          </div>
        )
      })}
      <div className="row">
        <button type="button" onClick={save}>保存</button>
        <span className="msg">{msg}</span>
      </div>
    </div>
  )
}
