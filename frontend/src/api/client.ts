import axios from 'axios'
import { useAuthStore } from '../store/auth'

const client = axios.create({ baseURL: '/v1' })

client.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 401 统一清 token 跳登录（带 next 回跳）
client.interceptors.response.use(
  (resp) => resp,
  (err) => {
    if (err?.response?.status === 401) {
      useAuthStore.getState().logout()
      if (!location.pathname.startsWith('/login')) {
        const next = encodeURIComponent(location.pathname + location.search)
        location.href = `/login?next=${next}`
      }
    }
    return Promise.reject(err)
  },
)

/** 统一提取后端错误信息（OpenAI 格式 {error:{message}}） */
export function errMsg(e: unknown): string {
  const r = (e as any)?.response?.data?.error
  if (r?.message) return String(r.message)
  return (e as any)?.message || String(e)
}

export default client
