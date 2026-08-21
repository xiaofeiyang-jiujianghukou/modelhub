import { useEffect, useState } from 'react'
import { Card, Tabs, Form, Input, Button, Typography, message } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../store/auth'
import { postLogin, postRegister } from '../api'
import { errMsg } from '../api/client'
import LangSwitch from '../components/LangSwitch'

export default function Login() {
  const { t } = useTranslation()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const setToken = useAuthStore((s) => s.setToken)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  // next 回跳目标（仅允许 /dashboard 开头）
  const next = sp.get('next')
  const target = next && next.startsWith('/dashboard') ? next : '/dashboard'

  // 已登录访问 /login 直接进入控制台（修复浏览器返回键卡在登录页）
  useEffect(() => {
    if (useAuthStore.getState().token) nav(target, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const doLogin = async (v: { email: string; password: string }) => {
    setErr('')
    setLoading(true)
    try {
      const r = await postLogin(v.email, v.password)
      setToken(r.data.access_token)
      nav(target, { replace: true })
    } catch (e) {
      setErr(errMsg(e) || t('login.loginFailed'))
    } finally {
      setLoading(false)
    }
  }

  const doRegister = async (v: { email: string; password: string; displayName: string }) => {
    setErr('')
    setLoading(true)
    try {
      await postRegister(v.email, v.password, v.displayName)
      // 短暂停顿让用户感知注册成功，再自动登录跳转
      await new Promise((r) => setTimeout(r, 700))
      const r = await postLogin(v.email, v.password)
      setToken(r.data.access_token)
      nav('/dashboard/keys', { replace: true })
    } catch (e) {
      setErr(errMsg(e) || t('login.registerFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{ position: 'fixed', top: 16, right: 16, zIndex: 10 }}>
        <LangSwitch />
      </div>
      <Card style={{ width: 400, boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>
          {t('login.title')}
        </Typography.Title>
        <Typography.Text type="secondary">{t('login.subtitle')}</Typography.Text>

        <Tabs
          style={{ marginTop: 16 }}
          items={[
            {
              key: 'login',
              label: t('login.tabLogin'),
              children: (
                <Form layout="vertical" onFinish={doLogin}>
                  <Form.Item
                    name="email"
                    label={t('login.email')}
                    rules={[{ required: true }]}
                  >
                    <Input type="email" placeholder="you@example.com" />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    label={t('login.password')}
                    rules={[{ required: true }]}
                  >
                    <Input.Password placeholder="••••••••" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" block loading={loading}>
                    {t('login.loginBtn')}
                  </Button>
                </Form>
              ),
            },
            {
              key: 'register',
              label: t('login.tabRegister'),
              children: (
                <Form layout="vertical" onFinish={doRegister}>
                  <Form.Item
                    name="email"
                    label={t('login.email')}
                    rules={[{ required: true }]}
                  >
                    <Input type="email" placeholder="you@example.com" />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    label={t('login.passwordHint')}
                    rules={[{ required: true }, { min: 8 }]}
                  >
                    <Input.Password placeholder="••••••••" />
                  </Form.Item>
                  <Form.Item name="displayName" label={t('login.displayName')}>
                    <Input placeholder={t('login.displayNamePh')} />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" block loading={loading}>
                    {t('login.registerBtn')}
                  </Button>
                </Form>
              ),
            },
          ]}
        />
        {err && (
          <Typography.Text type="danger" style={{ fontSize: 13 }}>
            {err}
          </Typography.Text>
        )}
      </Card>
    </div>
  )
}
