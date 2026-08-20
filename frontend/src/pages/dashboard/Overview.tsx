import { useEffect, useState } from 'react'
import { Card, Col, Row, Statistic, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { getBalance, listKeys, getLogs, type ApiKeyItem, type LogItem } from '../../api'

const QUICK_START = `from openai import OpenAI

client = OpenAI(
    api_key="sk-<your-key>",
    base_url="http://localhost:8000/v1",
)
r = client.chat.completions.create(
    model="ark/glm-5.3",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(r.choices[0].message.content)`

export default function Overview() {
  const { t } = useTranslation()
  const [balance, setBalance] = useState<number>(0)
  const [keyCount, setKeyCount] = useState<number>(0)
  const [logCount, setLogCount] = useState<number>(0)
  const [totalCost, setTotalCost] = useState<number>(0)

  useEffect(() => {
    Promise.all([getBalance(), listKeys(), getLogs(100)])
      .then(([b, k, l]) => {
        setBalance(b.data.balance_usd || 0)
        setKeyCount((k.data.data || []).length)
        setLogCount((l.data.data || []).length)
        setTotalCost((l.data.data || []).reduce((s, x: LogItem) => s + (x.cost_usd || 0), 0))
      })
      .catch(() => {})
  }, [])

  return (
    <Row gutter={[16, 16]}>
      <Col xs={12} md={6}>
        <Card>
          <Statistic
            title={t('overview.balance')}
            value={balance}
            prefix="$"
            precision={2}
            valueStyle={{ color: '#38a169' }}
          />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title={t('overview.keyCount')} value={keyCount} />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title={t('overview.requestCount')} value={logCount} />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title={t('overview.totalCost')} value={totalCost} prefix="$" precision={6} />
        </Card>
      </Col>
      <Col span={24}>
        <Card title={t('overview.quickStart')}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
            {t('overview.quickStartDesc')}
          </Typography.Paragraph>
          <pre
            style={{
              background: '#1a1a2e',
              color: '#7df9ff',
              padding: 12,
              borderRadius: 8,
              fontSize: 12.5,
              overflowX: 'auto',
              lineHeight: 1.5,
            }}
          >
            {QUICK_START}
          </pre>
        </Card>
      </Col>
    </Row>
  )
}
