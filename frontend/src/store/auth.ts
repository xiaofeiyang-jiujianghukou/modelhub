import { create } from 'zustand'

export interface CurrentUser {
  email: string
  is_admin: boolean
}

interface AuthState {
  token: string | null
  user: CurrentUser | null
  setToken: (t: string | null) => void
  setUser: (u: CurrentUser | null) => void
  logout: () => void
}

// 沿用旧静态 HTML 的 token key，老登录态无缝继承
const TOKEN_KEY = 'gateway_token'

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  setToken: (t) => {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
    set({ token: t })
  },
  setUser: (u) => set({ user: u }),
  logout: () => {
    localStorage.removeItem(TOKEN_KEY)
    set({ token: null, user: null })
  },
}))
