import { useEffect } from 'react'
import { Layout, Menu, Typography, Button, Space, Spin } from 'antd'
import { LogoutOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../store/auth'
import { getMe, postLogout } from '../api'
import LangSwitch from './LangSwitch'

const { Sider, Header, Content } = Layout

export default function SiderLayout() {
  const { t } = useTranslation()
  const nav = useNavigate()
  const loc = useLocation()
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const logout = useAuthStore((s) => s.logout)

  useEffect(() => {
    if (!user) {
      getMe()
        .then((r) => setUser(r.data))
        .catch(() => setUser({ email: '', is_admin: false }))
    }
  }, [user, setUser])

  const isAdmin = !!user?.is_admin

  const items = [
    { key: '/dashboard', label: t('nav.overview') },
    { key: '/dashboard/keys', label: t('nav.keys') },
    { key: '/dashboard/models', label: t('nav.models') },
    { key: '/dashboard/logs', label: t('nav.logs') },
    { key: '/dashboard/test', label: t('nav.test') },
    { key: '/dashboard/docs', label: t('nav.docs') },
    ...(isAdmin ? [{ key: '/dashboard/providers', label: t('nav.providers') }] : []),
  ]

  const selected =
    loc.pathname === '/dashboard'
      ? '/dashboard'
      : items.find((i) => i.key !== '/dashboard' && loc.pathname.startsWith(i.key))?.key ||
        '/dashboard'

  const doLogout = async () => {
    try {
      await postLogout()
    } catch {
      /* token 已失效也继续本地登出 */
    }
    logout()
    nav('/login', { replace: true })
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* sticky + 100vh：长内容页滚动时侧栏钉在视口，底部退出按钮始终可见 */}
      <Sider width={220} theme="dark" style={{ position: 'sticky', top: 0, height: '100vh' }}>
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div style={{ padding: '20px 16px 12px', flexShrink: 0 }}>
            <Typography.Title level={5} style={{ color: '#fff', margin: 0 }}>
              {t('app.name')}
            </Typography.Title>
            <Typography.Text style={{ color: '#999', fontSize: 12 }}>{t('app.sub')}</Typography.Text>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selected]}
            items={items}
            onClick={(e) => nav(e.key)}
            style={{ flex: 1, overflowY: 'auto', borderInlineEnd: 'none' }}
          />
          {/* 左下角常驻退出按钮 */}
          <div style={{ padding: 12, flexShrink: 0 }}>
            <Button block ghost icon={<LogoutOutlined />} onClick={doLogout}>
              {t('nav.logout')}
            </Button>
          </div>
        </div>
      </Sider>
      <Layout>
        <Header
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 20,
            background: '#fff',
            padding: '0 24px',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            borderBottom: '1px solid #f0f0f0',
            height: 56,
            lineHeight: 'normal',
          }}
        >
          <Space>
            {user?.email && <Typography.Text type="secondary">{user.email}</Typography.Text>}
            <LangSwitch />
          </Space>
        </Header>
        <Content style={{ margin: 24 }}>
          {user ? <Outlet /> : <Spin style={{ display: 'block', margin: '80px auto' }} />}
        </Content>
      </Layout>
    </Layout>
  )
}
