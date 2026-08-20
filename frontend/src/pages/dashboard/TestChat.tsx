import { useEffect, useRef, useState } from 'react'
import { Card, Select, Button, Input, Space, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { listModels, listKeys, type ModelItem, type ApiKeyItem } from '../../api'
import { useAuthStore } from '../../store/auth'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  reasoning: string
  model?: string
  tokens?: number
  error?: boolean
}

export default function TestChat() {
  const { t } = useTranslation()
  const [models, setModels] = useState<ModelItem[]>([])
  const [keyName, setKeyName] = useState<string>('')
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Msg[]>([])
  const [sending, setSending] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const historyRef = useRef<{ role: string; content: string }[]>([])

  useEffect(() => {
    listModels({ limit: 200 })
      .then((r) => {
        const data = (r.data.data || []).filter((m) => m.meta?.type === 'llm')
        setModels(data)
        if (data.length) setModel(data[0].id)
      })
      .catch(() => {})
    listKeys()
      .then((r) => {
        const active = ((r.data.data || []) as ApiKeyItem[]).filter((k) => k.is_active)
        if (active.length) setKeyName(active[0].name)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages])

  const patchLast = (patch: Partial<Msg>) => {
    setMessages((ms) => {
      if (!ms.length) return ms
      const n = [...ms]
      n[n.length - 1] = { ...n[n.length - 1], ...patch }
      return n
    })
  }

  const send = async () => {
    const text = input.trim()
    if (!text || !model || sending) return
    setInput('')
    setSending(true)
    setMessages((m) => [...m, { role: 'user', content: text, reasoning: '' }, { role: 'assistant', content: '', reasoning: '' }])

    let content = ''
    let reasoning = ''
    let modelName = model
    let tokens = 0
    try {
      const token = useAuthStore.getState().token
      const resp = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          model,
          messages: [...historyRef.current, { role: 'user', content: text }],
          max_tokens: 500,
          stream: true,
        }),
      })
      if (!resp.ok || !resp.body) {
        let msg = `HTTP ${resp.status}`
        try {
          const j = await resp.json()
          msg = j?.error?.message || msg
        } catch {
          /* ignore */
        }
        throw new Error(msg)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          const s = line.trim()
          if (!s.startsWith('data:')) continue
          const payload = s.slice(5).trim()
          if (payload === '[DONE]') continue
          try {
            const chunk = JSON.parse(payload)
            const delta = chunk.choices?.[0]?.delta || {}
            if (delta.reasoning_content) reasoning += delta.reasoning_content
            if (delta.content) content += delta.content
            if (chunk.model) modelName = chunk.model
            if (chunk.usage?.total_tokens) tokens = chunk.usage.total_tokens
          } catch {
            /* skip bad chunk */
          }
        }
        patchLast({ content, reasoning, model: modelName, tokens })
      }
      if (!content && !reasoning) {
        patchLast({ content: t('chat.noContent'), error: true })
      } else {
        historyRef.current = [
          ...historyRef.current,
          { role: 'user', content: text },
          { role: 'assistant', content: content || '（thinking complete）' },
        ]
      }
    } catch (e) {
      patchLast({ content: `❌ ${(e as Error).message}`, error: true })
    } finally {
      setSending(false)
    }
  }

  const clear = () => {
    historyRef.current = []
    setMessages([])
  }

  const mdComponents = {
    pre: (p: any) => (
      <pre
        style={{
          background: '#1a1a2e',
          color: '#7df9ff',
          padding: 12,
          borderRadius: 8,
          fontSize: 12,
          overflowX: 'auto',
          lineHeight: 1.5,
          margin: '8px 0',
        }}
      >
        {p.children}
      </pre>
    ),
    code: (p: any) => (
      <code
        style={{
          background: 'rgba(127,127,127,0.12)',
          padding: '1px 6px',
          borderRadius: 4,
          fontFamily: 'monospace',
          fontSize: 12,
        }}
      >
        {p.children}
      </code>
    ),
  }

  return (
    <Card
      title={t('nav.test')}
      extra={
        <Space wrap>
          <Select
            showSearch
            style={{ minWidth: 260 }}
            placeholder={t('chat.model')}
            value={model || undefined}
            onChange={setModel}
            options={models.map((m) => ({ value: m.id, label: m.id }))}
            optionFilterProp="label"
          />
          {keyName && (
            <Select
              style={{ minWidth: 140 }}
              value={keyName}
              onChange={setKeyName}
              options={keysOptions(keyName)}
              disabled
            />
          )}
          <Button onClick={clear}>{t('chat.clear')}</Button>
        </Space>
      }
    >
      <div
        ref={listRef}
        style={{
          height: '56vh',
          overflowY: 'auto',
          background: '#f8f9fc',
          borderRadius: 10,
          padding: 16,
        }}
      >
        {messages.length === 0 && (
          <Typography.Text type="secondary">ModelHub · {t('nav.test')}</Typography.Text>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
              marginBottom: 16,
            }}
          >
            <div
              style={{
                maxWidth: '86%',
                padding: '12px 16px',
                borderRadius: 12,
                fontSize: 14,
                lineHeight: 1.75,
                wordBreak: 'break-word',
                background: m.role === 'user' ? '#667eea' : '#fff',
                color: m.role === 'user' ? '#fff' : m.error ? '#e53e3e' : '#1a1a2e',
                border: m.role === 'assistant' ? '1px solid #eef0f5' : 'none',
                borderTopRightRadius: m.role === 'user' ? 4 : undefined,
                borderTopLeftRadius: m.role === 'assistant' ? 4 : undefined,
                whiteSpace: m.role === 'user' ? 'pre-wrap' : undefined,
              }}
            >
              {m.role === 'assistant' && m.reasoning && (
                <details
                  open={!m.content}
                  style={{ marginBottom: 8, color: '#999', fontSize: 12 }}
                >
                  <summary style={{ cursor: 'pointer', userSelect: 'none' }}>
                    {m.content
                      ? t('chat.thought', { n: m.reasoning.length })
                      : t('chat.thinking', { n: m.reasoning.length })}
                  </summary>
                  <div
                    style={{
                      color: '#888',
                      whiteSpace: 'pre-wrap',
                      marginTop: 6,
                      paddingLeft: 8,
                      borderLeft: '2px solid #e5e7eb',
                    }}
                  >
                    {m.reasoning}
                  </div>
                </details>
              )}
              {m.role === 'assistant' ? (
                m.content ? (
                  <Markdown remarkPlugins={[remarkGfm]} components={mdComponents as never}>
                    {m.content}
                  </Markdown>
                ) : (
                  <span style={{ color: '#bbb' }}>…</span>
                )
              ) : (
                m.content
              )}
              {m.role === 'assistant' && (m.model || m.tokens) && m.content && (
                <div style={{ color: '#aaa', fontSize: 11, marginTop: 6 }}>
                  {m.model} {m.tokens ? `· ${m.tokens} tokens` : ''}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 12 }}>
        <Input.TextArea
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder={t('chat.inputPh')}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
        />
        <Button
          type="primary"
          block
          style={{ marginTop: 8 }}
          loading={sending}
          onClick={send}
        >
          {t('chat.send')}
        </Button>
      </div>
    </Card>
  )
}

function keysOptions(current: string) {
  return [{ value: current, label: current }]
}
