import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Spin } from 'antd'
import { useAuthStore } from './store/auth'
import Login from './pages/Login'
import SiderLayout from './components/SiderLayout'
import Overview from './pages/dashboard/Overview'
import ApiKeys from './pages/dashboard/ApiKeys'
import Models from './pages/dashboard/Models'
import Logs from './pages/dashboard/Logs'
import TestChat from './pages/dashboard/TestChat'
import Docs from './pages/dashboard/Docs'
import Providers from './pages/dashboard/Providers'

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const loc = useLocation()
  if (!token) {
    return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />
  }
  return <>{children}</>
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  // user 未加载完成时等待（SiderLayout 挂载后会 getMe），避免 admin 被误踢
  if (!user) return <Spin style={{ display: 'block', margin: '80px auto' }} />
  if (!user.is_admin) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <SiderLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Overview />} />
          <Route path="keys" element={<ApiKeys />} />
          <Route path="models" element={<Models />} />
          <Route path="logs" element={<Logs />} />
          <Route path="test" element={<TestChat />} />
          <Route path="docs" element={<Docs />} />
          <Route
            path="providers"
            element={
              <RequireAdmin>
                <Providers />
              </RequireAdmin>
            }
          />
        </Route>
        {/* replace：未知路径重定向不压入历史，浏览器返回键不被"卡死" */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
