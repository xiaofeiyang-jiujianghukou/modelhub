import client from './client'

// ── auth ────────────────────────────────────────────────────────────────────
export const postLogin = (email: string, password: string) =>
  client.post<{ access_token: string }>('/auth/login', { email, password })
export const postRegister = (email: string, password: string, display_name: string) =>
  client.post<{ message: string }>('/auth/register', { email, password, display_name })
export const postLogout = () => client.post('/auth/logout')

// ── dashboard（用户态）───────────────────────────────────────────────────────
export const getMe = () => client.get<{ email: string; is_admin: boolean }>('/dashboard/me')
export const getBalance = () => client.get<{ balance_usd: number }>('/dashboard/balance')
export const listKeys = () =>
  client.get<{ data: ApiKeyItem[] }>('/dashboard/keys')
export const createKey = (name: string) =>
  client.post<{ key: string; message: string }>('/dashboard/keys', { name })
export const revokeKey = (id: string) => client.delete(`/dashboard/keys/${id}`)
export const getLogs = (limit = 50) =>
  client.get<{ data: LogItem[]; total: number }>('/dashboard/logs', { params: { limit } })

export interface ApiKeyItem {
  id: string
  name: string
  key_prefix: string
  is_active: boolean
  last_used_at: number | null
  created_at: number
}

export interface LogItem {
  request_id: string
  model: string
  provider: string
  request_type: string
  status: string
  status_code: number | null
  total_tokens: number | null
  cache_hit_tokens: number | null
  cache_miss_tokens: number | null
  cost_usd: number | null
  latency_ms: number | null
  created_at: number
}

// ── models ──────────────────────────────────────────────────────────────────
export interface ModelItem {
  id: string
  owned_by: string
  context_window: number | null
  alias_for?: string
  meta: {
    type: string
    price_source: string
    vendor: string
    input_price_per_1m_tokens?: number | null
    output_price_per_1m_tokens?: number | null
    price_per_image?: number | null
    price_per_second?: number | null
  }
}

export interface ModelsResp {
  data: ModelItem[]
  total: number
  providers: { key: string; display_name: string }[]
}

export const listModels = (params: {
  search?: string
  provider?: string
  sort?: string
  limit?: number
  offset?: number
  currency?: string
}) => client.get<ModelsResp>('/models', { params })

// ── admin providers ─────────────────────────────────────────────────────────
export interface ProviderItem {
  id: string
  name: string
  display_name: string
  base_url: string
  has_key: boolean
  model_count: number
  is_active: boolean
  last_synced_at: number | null
  last_sync_status: string | null
  last_sync_error: string | null
  timeout_ms: number
}

export interface RegistryEntry {
  key: string
  display_name: string
  default_base_url: string
}

export const listProviders = () => client.get<{ data: ProviderItem[] }>('/admin/providers')
export const getRegistry = () => client.get<{ data: RegistryEntry[] }>('/admin/providers/registry')
export const createProvider = (data: {
  name: string
  base_url?: string
  credentials: { api_key: string }
  timeout_ms: number
  auto_sync: boolean
}) => client.post('/admin/providers', data)
export const updateProvider = (id: string, data: Record<string, unknown>) =>
  client.put(`/admin/providers/${id}`, data)
export const deleteProvider = (id: string) => client.delete(`/admin/providers/${id}`)
export const syncProvider = (id: string) =>
  client.post<{ added: number; updated: number; skipped: number; errors: string[] }>(
    `/admin/providers/${id}/sync`,
  )
export const testProvider = (id: string) =>
  client.post<{ ok: boolean; message: string }>(`/admin/providers/${id}/test`)

// ── admin references ────────────────────────────────────────────────────────
export interface ReferenceItem {
  model_id: string
  vendor: string | null
  upstream_model: string | null
  display_name: string | null
  input_price: number | null
  output_price: number | null
  price_currency: string
  context_window: number | null
  price_source: string
}

export const listReferences = (currency: string) =>
  client.get<{ data: ReferenceItem[] }>('/admin/references', { params: { currency } })
export const upsertReference = (data: Record<string, unknown>) =>
  client.post('/admin/references', data)
export const deleteReference = (modelId: string) =>
  client.delete(`/admin/references/${encodeURIComponent(modelId)}`)
