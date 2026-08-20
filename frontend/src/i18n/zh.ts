export default {
  app: { name: '⚡ 模枢 ModelHub', sub: '多模型智能编排网关' },

  common: {
    save: '保存', cancel: '取消', delete: '删除', edit: '编辑', add: '添加',
    confirm: '确定', search: '搜索', refresh: '刷新', status: '状态', name: '名称',
    type: '类型', actions: '操作', enabled: '启用', disabled: '禁用',
    createdAt: '创建时间', lastUsed: '最近使用', never: '从未使用',
    success: '成功', failed: '失败', copy: '复制', copied: '已复制',
  },

  login: {
    title: '⚡ 模枢 ModelHub', subtitle: '多模型智能编排网关控制台',
    tabLogin: '登录', tabRegister: '注册',
    email: '邮箱', password: '密码', passwordHint: '密码（至少 8 位）',
    displayName: '显示名', displayNamePh: '你的名字',
    loginBtn: '登 录', registerBtn: '注 册',
    loginFailed: '登录失败', registerOk: '注册成功！请登录', registerFailed: '注册失败',
  },

  nav: {
    overview: '概览', keys: '我的 API Keys', models: '模型',
    logs: '请求日志', test: '测试对话', docs: '接入文档',
    references: '模型参考价', providers: '供应商', logout: '退出登录',
  },

  overview: {
    balance: '账户余额', keyCount: 'API Keys', requestCount: '总请求数', totalCost: '总消费',
    quickStart: '快速开始', quickStartDesc: '使用 OpenAI SDK 连接：',
  },

  keys: {
    createPh: 'Key 名称，如 prod-server', create: '创建',
    keyPrefix: 'Key', active: '活跃', revoked: '已撤销',
    revoke: '撤销', revokeConfirm: '确定撤销该 Key？撤销后立即失效。',
    createdTip: '⚠️ 请立即保存，仅显示一次！',
  },

  models: {
    searchPh: '搜索模型 ID 或名称…', allVendors: '全部厂商',
    modelId: '模型 ID', inputPrice: '输入价(/1M)', outputPrice: '输出价(/1M)',
    context: '上下文', priceSource: '价格来源',
    sourceOfficial: '官方', sourceDefault: '默认', sourceManual: '手动',
    total: '共 {{total}} 个模型，显示 {{start}}-{{end}}', perPage: '每页',
    llm: 'LLM', image: '图片', video: '视频', prev: '上一页', next: '下一页',
  },

  logs: {
    time: '时间', model: '模型', provider: '供应商', reqType: '类型',
    typeChat: '对话', typeImage: '图片', tokens: 'Tokens', cacheHit: '缓存命中',
    cost: '费用', latency: '延迟', noCache: '-',
  },

  chat: {
    model: '模型', key: 'Key', clear: '清空',
    inputPh: '输入消息，Enter 发送，Shift+Enter 换行', send: '发送',
    thinking: '思考中…（{{n}} 字）', thought: '已深度思考（{{n}} 字）',
    noContent: '（无内容返回）',
  },

  docs: { title: '接入文档' },

  providers: {
    title: '供应商管理', add: '添加供应商',
    desc: '添加/修改 API Key 后自动拉取该供应商的模型；删除供应商会级联清理其独占模型。Key 加密存储，仅显示状态。',
    baseUrl: 'Base URL', keyConfigured: '已配置 Key', keyMissing: '无 Key',
    modelCount: '模型数', lastSync: '上次同步',
    sync: '同步', test: '测试', enable: '启用', disable: '禁用',
    deleteConfirm: '确定删除供应商 {{name}}？将级联删除其 {{count}} 个独占模型与路由通道。',
    modalAdd: '添加供应商', modalEdit: '编辑供应商',
    providerType: '供应商类型', apiKey: 'API Key',
    apiKeyPh: '订阅密钥；编辑时留空 = 不修改',
    baseUrlPh: '默认用官方地址，仅官方更换时修改', timeout: '超时（毫秒）',
    advanced: '高级选项（Base URL / 超时）', apiKeyRequired: '新建供应商必须填写 API Key',
    syncResult: '同步结果：新增 {{added}} / 更新 {{updated}} / 跳过 {{skipped}}',
    enableConfirmDisable: '确定禁用该供应商？禁用后其独占模型将从列表隐藏。',
    enableConfirmEnable: '确定启用该供应商？',
  },

  references: {
    title: '模型参考价 / 上下文', add: '添加参考价',
    desc: '模型官方核对价与上下文窗口。供应商同步时上游能返回的自动覆盖，返回不了的用这里兜底。改完点「同步」供应商即生效。',
    modelId: '模型 ID', vendor: '厂商', upstream: '上游模型名',
    inputPricePh: '如 0.28', outputPricePh: '如 1.10', contextPh: '如 131072',
    modalAdd: '添加参考价', modalEdit: '编辑参考价',
    deleteConfirm: '删除参考价 {{id}}？',
  },
}
