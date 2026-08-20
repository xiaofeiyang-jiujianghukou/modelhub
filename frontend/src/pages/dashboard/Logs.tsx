import { useEffect, useState } from 'react'
import { Card, Table, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { getLogs, type LogItem } from '../../api'

export default function Logs() {
  const { t } = useTranslation()
  const [rows, setRows] = useState<LogItem[]>([])

  useEffect(() => {
    getLogs(100)
      .then((r) => setRows(r.data.data || []))
      .catch(() => {})
  }, [])

  // 缓存命中单元格：按命中率着色（绿 ≥50%，黄 >0%，红 0%，上游未上报灰 -）
  const cacheCell = (l: LogItem) => {
    const hit = l.cache_hit_tokens
    const miss = l.cache_miss_tokens
    if (hit === null && miss === null) return <Typography.Text type="secondary">-</Typography.Text>
    const total = (hit || 0) + (miss || 0)
    if (total === 0) return <Typography.Text type="secondary">-</Typography.Text>
    const pct = Math.round(((hit || 0) / total) * 100)
    const color = pct >= 50 ? '#38a169' : pct > 0 ? '#d69e2e' : '#e53e3e'
    return (
      <span style={{ color, fontWeight: 600, fontSize: 12.5 }}>
        {hit}/{total} · {pct}%
      </span>
    )
  }

  return (
    <Card title={t('nav.logs')}>
      <Table<LogItem>
        rowKey="request_id"
        dataSource={rows}
        size="middle"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={[
          {
            title: t('logs.time'),
            dataIndex: 'created_at',
            width: 170,
            render: (v: number) => new Date(v * 1000).toLocaleString(),
          },
          { title: t('logs.model'), dataIndex: 'model' },
          { title: t('logs.provider'), dataIndex: 'provider' },
          {
            title: t('logs.reqType'),
            dataIndex: 'request_type',
            width: 80,
            render: (v: string) => (v === 'image' ? t('logs.typeImage') : t('logs.typeChat')),
          },
          {
            title: t('common.status'),
            dataIndex: 'status',
            width: 80,
            render: (v: string) =>
              v === 'success' ? <Tag color="success">{t('common.success')}</Tag> : <Tag color="error">{t('common.failed')}</Tag>,
          },
          {
            title: t('logs.tokens'),
            dataIndex: 'total_tokens',
            width: 90,
            render: (v: number | null) => v?.toLocaleString() ?? '-',
          },
          { title: t('logs.cacheHit'), key: 'cache', width: 120, render: (_, l) => cacheCell(l) },
          {
            title: t('logs.cost'),
            dataIndex: 'cost_usd',
            width: 100,
            render: (v: number | null) => (v === null ? '-' : `$${v.toFixed(6)}`),
          },
          {
            title: t('logs.latency'),
            dataIndex: 'latency_ms',
            width: 90,
            render: (v: number | null) => (v === null ? '-' : `${(v / 1000).toFixed(2)}s`),
          },
        ]}
      />
    </Card>
  )
}
