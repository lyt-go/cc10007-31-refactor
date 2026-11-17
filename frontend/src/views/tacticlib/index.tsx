import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/tacticlib'
const columns = [
  "策略编号",
  "策略名称",
  "策略类型",
  "触发条件",
  "执行动作",
  "生效周期",
  "策略评分",
  "使用次数"
]
const actions = [
  "提交策略评审",
  "发布策略",
  "退役策略"
]
const statuses = [
  "草稿",
  "策略评审中",
  "已发布",
  "已退役"
]
const stats = [
  {
    "label": "运营策略总数",
    "value": 0
  },
  {
    "label": "已发布策略",
    "value": 0
  },
  {
    "label": "评审中策略",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function TacticlibView() {
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
        throw new Error('运营策略列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '策略库列表读取失败')
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
        throw new Error(payload.message ?? '策略库动作未生效，请稍后重试')
      }
      await reload()
      // 发布策略会同步生成/收敛实时决策执行明细：动作完成后开新页面，再回清单核对关联记录。
      if (action === '发布策略') {
        const code = String(payload.entry?.['策略编号'] ?? row['策略编号'] ?? '')
        window.open(`${window.location.origin}/decision?ref=${encodeURIComponent(code)}`, '_blank')
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '策略库操作失败')
    }
  }

  function openPublishRecords(row: Row) {
    window.open(`${ENDPOINT}/${row.id}/publish-records`, '_blank')
  }

  function openDecisionDetail(row: Row) {
    window.open(`${window.location.origin}/decision?ref=${encodeURIComponent(String(row['策略编号'] ?? ''))}`, '_blank')
  }

  return (
    <section className="page" data-module="tacticlib">
      <header className="page-head">
        <div>
          <h2>策略库管理</h2>
          <p className="page-desc">维护可复用的运营策略库，按类型、触发条件和执行动作分类管理。发布策略会同步到实时决策，发布记录与规则命中结果共享同一生效会话。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('运营策略登记入口尚未接入审批流')}>
            登记运营策略
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出策略库清单
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
                <button className="link" type="button" onClick={() => openPublishRecords(row)}>
                  发布记录
                </button>
                <button className="link" type="button" onClick={() => openDecisionDetail(row)}>
                  执行明细
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无策略库数据，可先登记运营策略</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条策略库记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
