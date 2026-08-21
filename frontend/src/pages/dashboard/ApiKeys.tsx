import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Input, Space, Typography, Popconfirm, message, Modal } from 'antd'
import { EyeOutlined, CopyOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { listKeys, createKey, revokeKey, revealKey, type ApiKeyItem } from '../../api'
import { errMsg } from '../../api/client'

export default function ApiKeys() {
  const { t } = useTranslation()
  const [rows, setRows] = useState<ApiKeyItem[]>([])
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<string | null>(null)
  const [revealTarget, setRevealTarget] = useState<ApiKeyItem | null>(null)
  const [revealedKey, setRevealedKey] = useState<string | null>(null)
  const [revealing, setRevealing] = useState(false)

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

  const doReveal = async (row: ApiKeyItem) => {
    setRevealTarget(row)
    setRevealedKey(null)
    setRevealing(true)
    try {
      const r = await revealKey(row.id)
      setRevealedKey(r.data.key)
    } catch (e) {
      message.error(errMsg(e))
      setRevealTarget(null)
    } finally {
      setRevealing(false)
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

  const KEY_DISPLAY = (k: string | null) => (
    <div>
      {k && (
        <Button
          size="small"
          icon={<CopyOutlined />}
          style={{ marginBottom: 8 }}
          onClick={() => {
            navigator.clipboard?.writeText(k)
            message.success(t('common.copied'))
          }}
        >
          {t('common.copy')}
        </Button>
      )}
      <div
        style={{
          background: '#1a1a2e',
          color: '#7df9ff',
          padding: 12,
          borderRadius: 8,
          fontFamily: 'monospace',
          fontSize: 13,
          wordBreak: 'break-all',
        }}
      >
        {k || '...'}
      </div>
    </div>
  )

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
          {KEY_DISPLAY(newKey)}
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
            responsive: ['sm'],
            render: fmtTime,
          },
          {
            title: t('common.actions'),
            render: (_, row) => (
              <Space size={4}>
                {row.can_reveal && (
                  <Button size="small" icon={<EyeOutlined />} onClick={() => doReveal(row)}>
                    {t('common.view')}
                  </Button>
                )}
                {row.is_active && (
                  <Popconfirm title={t('keys.revokeConfirm')} onConfirm={() => doRevoke(row.id)}>
                    <Button size="small" danger>
                      {t('keys.revoke')}
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={revealTarget?.name}
        open={!!revealTarget}
        onCancel={() => setRevealTarget(null)}
        footer={[
          <Button key="close" onClick={() => setRevealTarget(null)}>
            {t('common.close')}
          </Button>,
        ]}
      >
        {revealing ? (
          <Typography.Text type="secondary">...</Typography.Text>
        ) : (
          KEY_DISPLAY(revealedKey)
        )}
      </Modal>
    </Card>
  )
}
