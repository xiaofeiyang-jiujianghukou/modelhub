export default {
  app: { name: '⚡ ModelHub', sub: 'Multi-Model Orchestration Gateway' },

  common: {
    save: 'Save', cancel: 'Cancel', delete: 'Delete', edit: 'Edit', add: 'Add',
    confirm: 'OK', search: 'Search', refresh: 'Refresh', status: 'Status', name: 'Name',
    type: 'Type', actions: 'Actions', enabled: 'Enabled', disabled: 'Disabled',
    createdAt: 'Created', lastUsed: 'Last used', never: 'Never',
    success: 'Success', failed: 'Failed', copy: 'Copy', copied: 'Copied',
    close: 'Close', view: 'View',
  },

  login: {
    title: '⚡ ModelHub', subtitle: 'Multi-Model Gateway Console',
    tabLogin: 'Login', tabRegister: 'Register',
    email: 'Email', password: 'Password', passwordHint: 'Password (min 8 chars)',
    displayName: 'Display name', displayNamePh: 'Your name',
    loginBtn: 'Log in', registerBtn: 'Sign up',
    loginFailed: 'Login failed', registerOk: 'Registered! Please log in', registerFailed: 'Registration failed',
  },

  nav: {
    overview: 'Overview', keys: 'My API Keys', models: 'Models',
    logs: 'Request Logs', test: 'Test Chat', docs: 'Integration Docs',
    references: 'Model Pricing', providers: 'Providers', logout: 'Log out',
  },

  overview: {
    balance: 'Balance', keyCount: 'API Keys', requestCount: 'Total Requests', totalCost: 'Total Cost',
    quickStart: 'Quick Start', quickStartDesc: 'Connect with the OpenAI SDK:',
  },

  keys: {
    createPh: 'Key name, e.g. prod-server', create: 'Create',
    keyPrefix: 'Key', active: 'Active', revoked: 'Revoked',
    revoke: 'Revoke', revokeConfirm: 'Revoke this key? It takes effect immediately.',
    createdTip: '⚠️ Save it now — shown only once!',
  },

  models: {
    searchPh: 'Search model ID or name…', allVendors: 'All vendors',
    modelId: 'Model ID', inputPrice: 'Input(/1M)', outputPrice: 'Output(/1M)',
    context: 'Context', priceSource: 'Price source',
    sourceOfficial: 'Official', sourceDefault: 'Default', sourceManual: 'Manual',
    total: '{{total}} models, showing {{start}}-{{end}}', perPage: 'Per page',
    llm: 'LLM', image: 'Image', video: 'Video', prev: 'Prev', next: 'Next',
  },

  logs: {
    time: 'Time', model: 'Model', provider: 'Provider', reqType: 'Type',
    typeChat: 'Chat', typeImage: 'Image', tokens: 'Tokens', cacheHit: 'Cache hit',
    cost: 'Cost', latency: 'Latency', noCache: '-',
  },

  chat: {
    model: 'Model', key: 'Key', clear: 'Clear',
    inputPh: 'Type a message, Enter to send, Shift+Enter for newline', send: 'Send',
    thinking: 'Thinking… ({{n}} chars)', thought: 'Thought for {{n}} chars',
    noContent: '(empty response)',
  },

  docs: { title: 'Integration Docs' },

  providers: {
    title: 'Provider Management', add: 'Add Provider',
    desc: 'After adding/updating an API Key, models are pulled automatically; deleting a provider cascades to its exclusive models. Keys are stored encrypted, only status is shown.',
    baseUrl: 'Base URL', keyConfigured: 'Key set', keyMissing: 'No key',
    modelCount: 'Models', lastSync: 'Last sync',
    sync: 'Sync', test: 'Test', enable: 'Enable', disable: 'Disable',
    deleteConfirm: 'Delete provider {{name}}? This cascades to {{count}} exclusive models and route channels.',
    modalAdd: 'Add Provider', modalEdit: 'Edit Provider',
    providerType: 'Provider type', apiKey: 'API Key',
    apiKeyPh: 'Secret; leave empty when editing = keep current',
    baseUrlPh: 'Official URL by default; change only if the provider moved', timeout: 'Timeout (ms)',
    advanced: 'Advanced (Base URL / timeout)', apiKeyRequired: 'API Key is required for a new provider',
    syncResult: 'Sync result: {{added}} added / {{updated}} updated / {{skipped}} skipped',
    enableConfirmDisable: 'Disable this provider? Its exclusive models will be hidden.',
    enableConfirmEnable: 'Enable this provider?',
  },

  references: {
    title: 'Model Pricing / Context', add: 'Add Reference',
    desc: 'Official prices and context windows. During provider sync, upstream values overwrite these when available; otherwise these act as fallback. Click "Sync" on the provider to apply.',
    modelId: 'Model ID', vendor: 'Vendor', upstream: 'Upstream model',
    inputPricePh: 'e.g. 0.28', outputPricePh: 'e.g. 1.10', contextPh: 'e.g. 131072',
    modalAdd: 'Add Reference', modalEdit: 'Edit Reference',
    deleteConfirm: 'Delete reference {{id}}?',
  },
}
