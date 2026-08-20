import { useCallback, useEffect, useRef, useState } from 'react'
import { Card, Table, Tag, Input, Select, Space, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { listModels, type ModelItem } from '../../api'
import { useUiStore } from '../../store/ui'

type Sorter = { field: string; order: 'ascend' | 'descend' } | null

const SORT_FIELD_MAP: Record<string, string> = {
  id: 'id',
  input_price: 'input_price',
  output_price: 'output_price',
  context_window: 'context_window',
}

export default function Models() {
  const { t } = useTranslation()
  const { currency, setCurrency } = useUiStore()
  const [rows, setRows] = useState<ModelItem[]>([])
  const [total, setTotal] = useState(0)
  const [vendors, setVendors] = useState<{ key: string; display_name: string }[]>([])
  const [search, setSearch] = useState('')
  const [vendor, setVendor] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [sorter, setSorter] = useState<Sorter>(null)
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef<number>()

  const fetchModels = useCallback(() => {
    setLoading(true)
    const params: Record<string, unknown> = {
      limit: pageSize,
      offset: (page - 1) * pageSize,
      currency,
    }
    if (search.trim()) params.search = search.trim()
    if (vendor) params.provider = vendor
    if (sorter && SORT_FIELD_MAP[sorter.field]) {
      params.sort = (sorter.order === 'descend' ? '-' : '') + SORT_FIELD_MAP[sorter.field]
    }
    listModels(params as never)
      .then((r) => {
        setRows(r.data.data || [])
        setTotal(r.data.total || 0)
        setVendors(r.data.providers || [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [search, vendor, page, pageSize, sorter, currency])

  useEffect(() => {
    fetchModels()
  }, [fetchModels])

  // 搜索 debounce 300ms
  const onSearchChange = (v: string) => {
    setSearch(v)
    window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => setPage(1), 300)
  }

  const pricePrefix = currency === 'CNY' ? '¥' : '$'

  const priceText = (v: number | null | undefined) =>
    v === null || v === undefined ? '-' : `${pricePrefix}${v}`

  const typeLabel = (m: ModelItem) =>
    m.meta.type === 'image' ? t('models.image') : m.meta.type === 'video' ? t('models.video') : t('models.llm')

  const sourceTag = (s: string) => {
    if (s === 'official') return <Tag color="success">{t('models.sourceOfficial')}</Tag>
    if (s === 'manual') return <Tag color="warning">{t('models.sourceManual')}</Tag>
    return <Tag>{t('models.sourceDefault')}</Tag>
  }

  const start = total === 0 ? 0 : (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)

  return (
    <Card
      title={t('nav.models')}
      extra={
        <Space wrap>
          <Input.Search
            style={{ width: 240 }}
            placeholder={t('models.searchPh')}
            allowClear
            onChange={(e) => onSearchChange(e.target.value)}
          />
          <Select
            style={{ width: 160 }}
            value={vendor}
            onChange={(v) => {
              setVendor(v)
              setPage(1)
            }}
            options={[
              { value: '', label: t('models.allVendors') },
              ...vendors.map((v) => ({ value: v.key, label: v.display_name })),
            ]}
          />
          <Select
            style={{ width: 100 }}
            value={currency}
            onChange={(c) => {
              setCurrency(c)
              setPage(1)
            }}
            options={[
              { value: 'CNY', label: '¥ CNY' },
              { value: 'USD', label: '$ USD' },
            ]}
          />
        </Space>
      }
    >
      <Table<ModelItem>
        rowKey="id"
        dataSource={rows}
        loading={loading}
        size="middle"
        pagination={{
          current: page,
          pageSize,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          total,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
        onChange={(_, __, s) => {
          const single = Array.isArray(s) ? s[0] : s
          setSorter(single && single.order ? (single as Sorter) : null)
          setPage(1)
        }}
        columns={[
          {
            title: t('models.modelId'),
            dataIndex: 'id',
            sorter: true,
            render: (v: string, row) => (
              <Space size={4}>
                <Typography.Text code>{v}</Typography.Text>
                {row.alias_for && <Tag color="blue">alias</Tag>}
              </Space>
            ),
          },
          {
            title: t('common.type'),
            key: 'type',
            render: (_, row) => typeLabel(row),
          },
          {
            title: t('models.inputPrice'),
            key: 'input',
            sorter: true,
            render: (_, row) =>
              row.meta.type === 'image'
                ? `${priceText(row.meta.price_per_image)}/📷`
                : row.meta.type === 'video'
                  ? `${priceText(row.meta.price_per_second)}/s`
                  : priceText(row.meta.input_price_per_1m_tokens),
          },
          {
            title: t('models.outputPrice'),
            key: 'output',
            sorter: true,
            render: (_, row) =>
              row.meta.type === 'llm' ? priceText(row.meta.output_price_per_1m_tokens) : '-',
          },
          {
            title: t('models.context'),
            dataIndex: 'context_window',
            sorter: true,
            render: (v: number | null) => (v ? v.toLocaleString() : '-'),
          },
          {
            title: t('models.priceSource'),
            render: (_, row) => sourceTag(row.meta.price_source),
          },
        ]}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('models.total', { total, start, end })}
      </Typography.Text>
    </Card>
  )
}
