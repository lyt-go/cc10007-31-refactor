import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/decision/executions'
const columns = [
  "明细编号",
  "触发对象",
  "触发条件",
  "优先级",
  "最终执行动作",
  "冲突状态",
  "状态",
  "数据版本"
]
const statuses = ["待处理", "已闭环"]

type Row = Record<string, any>

type ReplayStep = { 步骤: number; 说明: string }
type VersionRow = { 数据版本: string; 事件: string; 明细编号?: string; 会话编号?: string; 时间: string }

export default function DecisionView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    const ref = searchParams.get('ref')
    if (ref) initial['关联编号'] = ref
    const status = searchParams.get('status')
    if (status) initial['状态'] = status
    return initial
  })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<Row | null>(null)
  const [replay, setReplay] = useState<{ 回放步骤: ReplayStep[]; 版本核对: VersionRow[]; 会话: Row[]; 后续登记: Row[] } | null>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters['关联编号']) query.set('ref', filters['关联编号'])
    if (filters['状态']) query.set('status', filters['状态'])
    if (filters['明细编号或触发对象']) query.set('keyword', filters['明细编号或触发对象'])
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('执行明细列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '执行明细列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const loadReplay = useCallback(async (detailId: number) => {
    setSelectedId(detailId)
    setDetail(null)
    setReplay(null)
    setErrorMessage('')
    try {
      const [detailResponse, replayResponse] = await Promise.all([
        request(`${ENDPOINT}/${detailId}`),
        request(`${ENDPOINT}/${detailId}/replay`),
      ])
      if (!detailResponse.ok || !replayResponse.ok) {
        throw new Error('执行明细读取失败')
      }
      setDetail(await detailResponse.json())
      setReplay(await replayResponse.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '执行明细读取失败')
    }
  }, [])

  async function confirmDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action: '确认执行明细' }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '执行明细确认失败')
      }
      await reload()
      await loadReplay(row.id)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '执行明细确认失败')
    }
  }

  function applyFilter(next: Record<string, string>) {
    setFilters(next)
    const query: Record<string, string> = {}
    if (next['关联编号']) query.ref = next['关联编号']
    if (next['状态']) query.status = next['状态']
    setSearchParams(query)
  }

  const stats = useMemo(() => ([
    { label: '执行明细总数', value: total },
    { label: '待处理明细', value: rows.filter((row) => row['状态'] === '待处理').length },
    { label: '已闭环明细', value: rows.filter((row) => row['状态'] === '已闭环').length },
  ]), [rows, total])

  return (
    <section className="page" data-module="decision">
      <header className="page-head">
        <div>
          <h2>实时决策执行明细</h2>
          <p className="page-desc">同一触发对象只保留一个最终执行动作；策略库发布记录与实时规则命中共用同一生效会话，落败会话与冲突原因可一起回放。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => void reload()}>刷新明细</button>
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
        <label className="filter-item">
          <span>明细编号或触发对象</span>
          <input
            value={filters['明细编号或触发对象'] ?? ''}
            placeholder="按明细编号或触发对象检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, 明细编号或触发对象: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>关联编号</span>
          <input
            value={filters['关联编号'] ?? ''}
            placeholder="策略编号或规则编号"
            onChange={(event) => setFilters((prev) => ({ ...prev, 关联编号: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={filters['状态'] ?? ''} onChange={(event) => setFilters((prev) => ({ ...prev, 状态: event.target.value }))}>
            <option value="">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => applyFilter({})}>重置条件</button>
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
                <button className="link" type="button" onClick={() => void loadReplay(row.id)}>查看回放</button>
                {row['状态'] === '待处理' && (
                  <button className="link" type="button" onClick={() => void confirmDetail(row)}>确认执行明细</button>
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无执行明细，可在策略库发布策略或实时决策生效规则后查看</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条执行明细</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {selectedId !== null && (
        <section className="detail-panel">
          <header className="detail-head">
            <h3>执行明细回放</h3>
            <button className="btn ghost" type="button" onClick={() => { setSelectedId(null); setDetail(null); setReplay(null) }}>收起</button>
          </header>

          {detail && (
            <>
              <dl className="detail-grid">
                <div><dt>明细编号</dt><dd>{detail['明细编号']}</dd></div>
                <div><dt>触发对象</dt><dd>{detail['触发对象']}</dd></div>
                <div><dt>触发条件</dt><dd>{detail['触发条件'] || '—'}</dd></div>
                <div><dt>最终执行动作</dt><dd>{detail['最终执行动作']}</dd></div>
                <div><dt>生效会话</dt><dd>{detail['生效会话']}</dd></div>
                <div><dt>裁决依据</dt><dd>{detail['裁决依据']}</dd></div>
                <div><dt>冲突状态</dt><dd>{detail['冲突状态']}</dd></div>
                <div><dt>数据版本</dt><dd>{detail['数据版本']}</dd></div>
                <div className="detail-wide"><dt>冲突原因</dt><dd>{detail['冲突原因'] || '—'}</dd></div>
              </dl>

              <h4>落败会话</h4>
              <table className="data-table">
                <thead>
                  <tr><th>会话编号</th><th>执行动作</th><th>优先级</th><th>来源</th><th>关联编号</th><th>落败原因</th></tr>
                </thead>
                <tbody>
                  {(detail['落败会话'] ?? []).map((item: Row) => (
                    <tr key={item['会话编号']}>
                      <td>{item['会话编号']}</td>
                      <td>{item['执行动作']}</td>
                      <td>{item['优先级']}</td>
                      <td>{item['来源']}</td>
                      <td>{item['关联编号'] || '—'}</td>
                      <td>{item['落败原因']}</td>
                    </tr>
                  ))}
                  {(detail['落败会话'] ?? []).length === 0 && (
                    <tr><td className="empty-state" colSpan={6}>没有落败会话，最终动作唯一</td></tr>
                  )}
                </tbody>
              </table>

              <h4>发布记录与规则命中结果（同一生效会话）</h4>
              <table className="data-table">
                <thead>
                  <tr><th>类型</th><th>关联编号</th><th>执行动作</th><th>数据版本</th><th>来源</th></tr>
                </thead>
                <tbody>
                  {(detail['发布记录'] ?? []).map((item: Row, index: number) => (
                    <tr key={`publish-${index}`}>
                      <td>发布记录</td>
                      <td>{item['策略编号']}</td>
                      <td>{item['执行动作']}</td>
                      <td>{item['数据版本']}</td>
                      <td>{item['发布动作']}</td>
                    </tr>
                  ))}
                  {(detail['命中结果'] ?? []).map((item: Row, index: number) => (
                    <tr key={`hit-${index}`}>
                      <td>规则命中</td>
                      <td>{item['规则编号'] || '—'}</td>
                      <td>{item['命中动作']}</td>
                      <td>{item['数据版本']}</td>
                      <td>{item['来源']}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>回放步骤</h4>
              <ol className="replay-steps">
                {(replay?.['回放步骤'] ?? []).map((step) => (
                  <li key={step.步骤}>{step.说明}</li>
                ))}
              </ol>

              <h4>数据版本核对</h4>
              <table className="data-table">
                <thead>
                  <tr><th>数据版本</th><th>事件</th><th>明细编号</th><th>会话编号</th><th>时间</th></tr>
                </thead>
                <tbody>
                  {(replay?.['版本核对'] ?? []).map((item) => (
                    <tr key={`${item.数据版本}-${item.事件}`}>
                      <td>{item.数据版本}</td>
                      <td>{item.事件}</td>
                      <td>{item.明细编号 || '—'}</td>
                      <td>{item.会话编号 || '—'}</td>
                      <td>{item.时间}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {(replay?.['后续登记'] ?? []).length > 0 && (
                <>
                  <h4>后续登记（已确认结果未被改写）</h4>
                  <ul className="replay-steps">
                    {(replay?.['后续登记'] ?? []).map((item, index) => (
                      <li key={index}>
                        数据版本 {item['数据版本']}：保留最终动作「{item['最终执行动作']}」；
                        新增落败会话 {(item['落败会话'] ?? []).map((loser: Row) => loser['会话编号']).join('、') || '无'}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
          {!detail && !errorMessage && <p className="page-desc">正在读取执行明细…</p>}
        </section>
      )}
    </section>
  )
}
