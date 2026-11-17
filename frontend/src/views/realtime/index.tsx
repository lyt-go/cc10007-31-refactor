import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/realtime'
const columns = [
  "规则编号",
  "触发事件",
  "决策条件",
  "执行动作",
  "冷却时间秒",
  "优先级",
  "分流比例",
  "生效时段"
]
const actions = [
  "提交规则评审",
  "生效规则",
  "暂停规则"
]
const statuses = [
  "草稿",
  "规则评审中",
  "已生效",
  "已暂停"
]
const stats = [
  {
    "label": "决策规则总数",
    "value": 0
  },
  {
    "label": "已生效规则",
    "value": 0
  },
  {
    "label": "评审中规则",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function RealtimeView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('决策规则列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时决策列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '实时决策动作未生效，请稍后重试')
      }
      await reload()
      // 生效规则会登记规则命中结果：动作完成后开新页面，再回清单核对关联的执行明细。
      if (action === '生效规则') {
        const code = String(payload.entry?.['规则编号'] ?? row['规则编号'] ?? '')
        window.open(`${window.location.origin}/decision?ref=${encodeURIComponent(code)}`, '_blank')
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时决策操作失败')
    }
  }

  function openDecisionDetail(row: Row) {
    window.open(`${window.location.origin}/decision?ref=${encodeURIComponent(String(row['规则编号'] ?? ''))}`, '_blank')
  }

  return (
    <section className="page" data-module="realtime">
      <header className="page-head">
        <div>
          <h2>实时决策管理</h2>
          <p className="page-desc">配置实时运营决策规则，基于用户实时行为触发个性化动作。规则命中结果与策略库发布记录共享同一生效会话，最终执行动作唯一。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('决策规则登记入口尚未接入审批流')}>
            登记决策规则
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出实时决策清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => openDecisionDetail(row)}>
                  执行明细
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无实时决策数据，可先登记决策规则</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条实时决策记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
