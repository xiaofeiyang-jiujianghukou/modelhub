import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, InputNumber, Select, Space, Tag,
  Typography, Alert, message, Dropdown, Tooltip, Grid,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  PlusOutlined, SyncOutlined, ApiOutlined, EditOutlined, DeleteOutlined,
  MoreOutlined, StopOutlined, PlayCircleOutlined, CheckCircleOutlined, CloseCircleOutlined,
} from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import {
  listProviders, getRegistry, createProvider, updateProvider,
  deleteProvider, syncProvider, testProvider,
  type ProviderItem, type RegistryEntry,
} from '../../api'
import { errMsg } from '../../api/client'

export default function Providers() {
  const { t } = useTranslation()
  const screens = Grid.useBreakpoint()
  const wide = !!screens.md // ≥768px 显示完整操作按钮，窄屏收纳进 Dropdown

  const [rows, setRows] = useState<ProviderItem[]>([])
  const [registry, setRegistry] = useState<RegistryEntry[]>([])
  const [open, setOpen] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [syncErr, setSyncErr] = useState<string | null>(null)
  const [form] = Form.useForm()

  const load = () => {
    listProviders()
      .then((r) => setRows(r.data.data || []))
      .catch(() => {})
  }
  useEffect(() => {
    load()
    getRegistry()
      .then((r) => setRegistry(r.data.data || []))
      .catch(() => {})
  }, [])

  // ── 弹窗 ────────────────────────────────────────────────────────────────────
  const openAdd = () => {
    setEditId(null)
    form.resetFields()
    if (registry.length) {
      form.setFieldsValue({ name: registry[0].key, base_url: registry[0].default_base_url, timeout_ms: 60000 })
    }
    setOpen(true)
  }

  const openEdit = (p: ProviderItem) => {
    setEditId(p.id)
    form.setFieldsValue({ name: p.name, base_url: p.base_url, timeout_ms: p.timeout_ms, api_key: '' })
    setOpen(true)
  }

  const onTypeChange = (key: string) => {
    const entry = registry.find((r) => r.key === key)
    if (entry) form.setFieldsValue({ base_url: entry.default_base_url })
  }

  const save = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      if (editId) {
        const body: Record<string, unknown> = { base_url: v.base_url, timeout_ms: v.timeout_ms }
        if (v.api_key?.trim()) body.credentials = { api_key: v.api_key.trim() }
        await updateProvider(editId, body)
      } else {
        if (!v.api_key?.trim()) {
          message.warning(t('providers.apiKeyRequired'))
          setSaving(false)
          return
        }
        await createProvider({
          name: v.name,
          base_url: v.base_url,
          credentials: { api_key: v.api_key.trim() },
          timeout_ms: v.timeout_ms ?? 60000,
          auto_sync: true,
        })
      }
      setOpen(false)
      load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  // ── 行操作 ──────────────────────────────────────────────────────────────────
  const doSync = async (p: ProviderItem) => {
    try {
      const r = await syncProvider(p.id)
      setSyncErr(null)
      setSyncMsg(t('providers.syncResult', { added: r.data.added, updated: r.data.updated, skipped: r.data.skipped }))
      load()
    } catch (e) {
      setSyncMsg(null)
      setSyncErr(errMsg(e))
    }
  }

  const doTest = async (p: ProviderItem) => {
    try {
      const r = await testProvider(p.id)
      if (r.data.ok) message.success(r.data.message)
      else message.error(r.data.message)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doToggle = (p: ProviderItem) => {
    const tip = p.is_active ? t('providers.enableConfirmDisable') : t('providers.enableConfirmEnable')
    Modal.confirm({
      title: tip,
      onOk: async () => {
        try {
          await updateProvider(p.id, { is_active: !p.is_active })
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const doDelete = (p: ProviderItem) => {
    Modal.confirm({
      title: t('providers.deleteConfirm', { name: p.name, count: p.model_count }),
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteProvider(p.id)
          message.success(`${p.name} deleted (${p.model_count} models)`)
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  // ── 操作列（宽屏图标组 / 窄屏 Dropdown）────────────────────────────────────
  const menuItems = (p: ProviderItem): MenuProps['items'] => [
    { key: 'edit', icon: <EditOutlined />, label: t('common.edit'), onClick: () => openEdit(p) },
    { key: 'sync', icon: <SyncOutlined />, label: t('providers.sync'), onClick: () => doSync(p) },
    { key: 'test', icon: <ApiOutlined />, label: t('providers.test'), onClick: () => doTest(p) },
    {
      key: 'toggle',
      icon: p.is_active ? <StopOutlined /> : <PlayCircleOutlined />,
      label: p.is_active ? t('providers.disable') : t('providers.enable'),
      onClick: () => doToggle(p),
    },
    { type: 'divider' },
    { key: 'delete', icon: <DeleteOutlined />, label: t('common.delete'), danger: true, onClick: () => doDelete(p) },
  ]

  const renderActions = (p: ProviderItem) =>
    wide ? (
      <Space size={0} wrap={false}>
        <Tooltip title={t('common.edit')}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(p)} />
        </Tooltip>
        <Tooltip title={t('providers.sync')}>
          <Button type="text" size="small" icon={<SyncOutlined />} onClick={() => doSync(p)} />
        </Tooltip>
        <Tooltip title={t('providers.test')}>
          <Button type="text" size="small" icon={<ApiOutlined />} onClick={() => doTest(p)} />
        </Tooltip>
        <Tooltip title={p.is_active ? t('providers.disable') : t('providers.enable')}>
          <Button
            type="text"
            size="small"
            icon={p.is_active ? <StopOutlined /> : <PlayCircleOutlined />}
            onClick={() => doToggle(p)}
          />
        </Tooltip>
        <Tooltip title={t('common.delete')}>
          <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => doDelete(p)} />
        </Tooltip>
      </Space>
    ) : (
      <Dropdown menu={{ items: menuItems(p) }} trigger={['click']}>
        <Button type="text" size="small" icon={<MoreOutlined />} />
      </Dropdown>
    )

  const fmtTime = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : '-')

  const columns = [
    {
      title: t('common.name'),
      dataIndex: 'name',
      fixed: 'left' as const,
      width: 150,
      render: (_: string, p: ProviderItem) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{p.display_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {p.name}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: t('providers.baseUrl'),
      dataIndex: 'base_url',
      responsive: ['md' as const], // 窄屏隐藏，避免挤压
      ellipsis: true,
      render: (v: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: v }}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: 'Key',
      dataIndex: 'has_key',
      responsive: ['sm' as const],
      width: 95,
      render: (v: boolean) =>
        v ? <Tag color="success">{t('providers.keyConfigured')}</Tag> : <Tag>{t('providers.keyMissing')}</Tag>,
    },
    {
      title: t('providers.modelCount'),
      dataIndex: 'model_count',
      width: 70,
      align: 'center' as const,
      responsive: ['sm' as const],
    },
    {
      title: t('common.status'),
      dataIndex: 'is_active',
      width: 100,
      render: (v: boolean, p: ProviderItem) => (
        <Space direction="vertical" size={2}>
          {v ? <Tag color="processing">{t('common.enabled')}</Tag> : <Tag>{t('common.disabled')}</Tag>}
          {p.last_sync_status === 'success' && (
            <Tag icon={<CheckCircleOutlined />} color="success" style={{ marginInlineEnd: 0 }}>
              {t('common.success')}
            </Tag>
          )}
          {p.last_sync_status === 'error' && (
            <Tooltip title={p.last_sync_error || ''}>
              <Tag icon={<CloseCircleOutlined />} color="error" style={{ marginInlineEnd: 0 }}>
                {t('common.failed')}
              </Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: t('providers.lastSync'),
      dataIndex: 'last_synced_at',
      responsive: ['lg' as const],
      width: 165,
      render: (v: number | null) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {fmtTime(v)}
        </Typography.Text>
      ),
    },
    {
      title: t('common.actions'),
      key: 'actions',
      width: wide ? 235 : 60,
      fixed: 'right' as const,
      render: (_: unknown, p: ProviderItem) => renderActions(p),
    },
  ]

  return (
    <Card
      title={t('providers.title')}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          {t('providers.add')}
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        {t('providers.desc')}
      </Typography.Paragraph>

      {syncMsg && (
        <Alert type="success" showIcon style={{ marginBottom: 12 }} message={syncMsg} closable onClose={() => setSyncMsg(null)} />
      )}
      {syncErr && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message={syncErr} closable onClose={() => setSyncErr(null)} />
      )}

      <Table<ProviderItem>
        rowKey="id"
        dataSource={rows}
        size="middle"
        columns={columns}
        pagination={false}
        scroll={{ x: 'max-content' }}
      />

      <Modal
        title={editId ? `${t('providers.modalEdit')} · ${rows.find((r) => r.id === editId)?.name ?? ''}` : t('providers.modalAdd')}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={save}
        confirmLoading={saving}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label={t('providers.providerType')} rules={[{ required: true }]}>
            <Select
              showSearch
              disabled={!!editId}
              onChange={onTypeChange}
              options={registry.map((r) => ({ value: r.key, label: `${r.display_name} (${r.key})` }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={t('providers.apiKey')}
            rules={editId ? [] : [{ required: true, message: t('providers.apiKeyRequired') }]}
          >
            <Input.Password placeholder={t('providers.apiKeyPh')} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="base_url" label={t('providers.baseUrl')} extra={t('providers.baseUrlPh')}>
            <Input />
          </Form.Item>
          <Form.Item name="timeout_ms" label={t('providers.timeout')} initialValue={60000}>
            <InputNumber style={{ width: '100%' }} min={1000} step={1000} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
