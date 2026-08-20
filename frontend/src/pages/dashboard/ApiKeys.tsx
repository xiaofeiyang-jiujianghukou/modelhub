import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Input, Space, Typography, Popconfirm, message } from 'antd'
import { useTranslation } from 'react-i18next'
import { listKeys, createKey, revokeKey, type ApiKeyItem } from '../../api'
import { errMsg } from '../../api/client'

export default function ApiKeys() {
  const { t } = useTranslation()
  const [rows, setRows] = useState<ApiKeyItem[]>([])
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<string | null>(null)

  const load = () => {
    listKeys()
      .then((r) => setRows(r.data.data || []))
      .catch(() => {})
  }
  useEffect(load, [])

  const doCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    try {
      const r = await createKey(name.trim())
      setNewKey(r.data.key)
      setName('')
      message.success(t('keys.createdTip'))
      load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setCreating(false)
    }
  }

  const doRevoke = async (id: string) => {
    try {
      await revokeKey(id)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const fmtTime = (ts: number | null) => {
    if (!ts) return t('common.never')
    return new Date(ts * 1000).toLocaleString()
  }

  return (
    <Card
      title={t('nav.keys')}
      extra={
        <Space>
          <Input
            style={{ width: 240 }}
            placeholder={t('keys.createPh')}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onPressEnter={doCreate}
          />
          <Button type="primary" loading={creating} onClick={doCreate}>
            {t('keys.create')}
          </Button>
        </Space>
      }
    >
      {newKey && (
        <div style={{ marginBottom: 16 }}>
          <Typography.Text type="danger" style={{ fontSize: 13 }}>
            {t('keys.createdTip')}
          </Typography.Text>
          <div
            style={{
              background: '#1a1a2e',
              color: '#7df9ff',
              padding: 12,
              borderRadius: 8,
              fontFamily: 'monospace',
              fontSize: 13,
              wordBreak: 'break-all',
              marginTop: 8,
            }}
          >
            {newKey}
          </div>
        </div>
      )}

      <Table<ApiKeyItem>
        rowKey="id"
        dataSource={rows}
        pagination={false}
        size="middle"
        columns={[
          { title: t('common.name'), dataIndex: 'name' },
          {
            title: t('keys.keyPrefix'),
            dataIndex: 'key_prefix',
            render: (v: string) => (
              <Typography.Text code copyable={{ text: v }}>
                {v}
              </Typography.Text>
            ),
          },
          {
            title: t('common.status'),
            dataIndex: 'is_active',
            render: (v: boolean) =>
              v ? <Tag color="success">{t('keys.active')}</Tag> : <Tag color="error">{t('keys.revoked')}</Tag>,
          },
          {
            title: t('common.lastUsed'),
            dataIndex: 'last_used_at',
            render: fmtTime,
          },
          {
            title: t('common.actions'),
            render: (_, row) =>
              row.is_active ? (
                <Popconfirm title={t('keys.revokeConfirm')} onConfirm={() => doRevoke(row.id)}>
                  <Button size="small" danger>
                    {t('keys.revoke')}
                  </Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />
    </Card>
  )
}
