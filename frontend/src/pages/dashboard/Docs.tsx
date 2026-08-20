import { Card, Table, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { useUiStore } from '../../store/ui'

const CODE_TOML = `model = "ark-glm-5.3"
model_provider = "custom"
model_catalog_json = "~/.codex/model-catalog.local.json"

[model_providers.custom]
name = "modelhub"
base_url = "http://localhost:8000/v1"
wire_api = "responses"
requires_openai_auth = true`

const CODE_AUTH = `{"OPENAI_API_KEY": "sk-<your-key>"}`

const CODE_CLAUDE = `{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8000",
    "ANTHROPIC_AUTH_TOKEN": "sk-<your-key>",
    "ANTHROPIC_MODEL": "claude-deepseek-v4-flash",
    "CLAUDE_CODE_USE_GATEWAY": "1",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"
  }
}`

const CODE_SDK = `from openai import OpenAI

client = OpenAI(api_key="sk-<your-key>", base_url="http://localhost:8000/v1")
r = client.chat.completions.create(
    model="ark/glm-5.3",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)`

const PRE: React.CSSProperties = {
  background: '#1a1a2e',
  color: '#7df9ff',
  padding: 12,
  borderRadius: 8,
  fontFamily: 'monospace',
  fontSize: 12,
  overflowX: 'auto',
  lineHeight: 1.5,
  margin: '8px 0 14px',
}

const TIP: React.CSSProperties = {
  background: '#ebf8ff',
  borderLeft: '3px solid #3182ce',
  padding: '10px 14px',
  borderRadius: '0 8px 8px 0',
  margin: '12px 0',
  fontSize: 13,
  color: '#2c5282',
}

function Code({ children }: { children: string }) {
  return <pre style={PRE}>{children}</pre>
}

export default function Docs() {
  const { t } = useTranslation()
  const lang = useUiStore((s) => s.lang)
  const zh = lang === 'zh'

  const overviewRows = zh
    ? [
        { k: 'OpenAI SDK (chat/completions)', v: 'Base URL http://localhost:8000/v1 + Bearer Key，模型列表 GET /v1/models' },
        { k: 'Codex (responses 协议)', v: 'Base URL http://localhost:8000/v1，模型目录文件由网关自动维护' },
        { k: 'Claude Code (Anthropic 协议)', v: 'Base URL http://localhost:8000，启动时自动 GET /v1/models' },
      ]
    : [
        { k: 'OpenAI SDK (chat/completions)', v: 'Base URL http://localhost:8000/v1 + Bearer key, list via GET /v1/models' },
        { k: 'Codex (responses protocol)', v: 'Base URL http://localhost:8000/v1, model catalog maintained by the gateway' },
        { k: 'Claude Code (Anthropic protocol)', v: 'Base URL http://localhost:8000, auto-fetches GET /v1/models on start' },
      ]

  const faqs = zh
    ? [
        ['Claude Code /model 只显示几个内置模型？', '缺少 CLAUDE_CODE_USE_GATEWAY=1（仅设 DISCOVERY 无效），两个开关必须同时设置。'],
        ['Codex /model 只有 gpt 系列？', 'config.toml 的 model_catalog_json 未指向网关目录文件，或 cc-switch 当前活动供应商不是本网关。'],
        ['模型 ID 带 claude- 前缀？', '网关包装用于模型发现（Claude Code 只认 claude- 前缀），请求时自动映射回真实模型，直接选即可。'],
        ['模型增删后要改配置吗？', '不用。客户端每次启动自动拉取最新列表。'],
        ['切回 DeepSeek 后 /model 还显示网关模型？', 'Claude Code 缓存残留 bug（客户端问题）。~/.claude_wrapper.sh 的 claude() 包装函数每次启动前自动清理。'],
        ['支持哪些模型？', '见左侧「模型」页实时清单（含价格与上下文）。'],
      ]
    : [
        ['Claude Code /model shows only a few built-ins?', 'CLAUDE_CODE_USE_GATEWAY=1 is missing (DISCOVERY alone is not enough); both switches must be set together.'],
        ['Codex /model only lists gpt models?', 'model_catalog_json in config.toml does not point to the gateway catalog, or cc-switch is not on this gateway.'],
        ['Model IDs carry a claude- prefix?', 'Gateway wrapper for model discovery (Claude Code only accepts claude- prefixed IDs); requests map back to the real model automatically.'],
        ['Do I need to update config when models change?', 'No. Clients fetch the latest list on every start.'],
        ['/model still shows gateway models after switching back to DeepSeek?', 'A Claude Code cache bug (client-side). The claude() wrapper in ~/.claude_wrapper.sh cleans it on each start.'],
        ['Which models are supported?', 'See the live list on the Models page (with pricing and context).'],
      ]

  return (
    <div>
      <Card title={zh ? '总览' : 'Overview'} style={{ marginBottom: 16 }}>
        <Typography.Paragraph style={{ fontSize: 13, color: '#555' }}>
          {zh
            ? '网关兼容 OpenAI / Anthropic 双协议，Codex 与 Claude Code 均可像接入方舟/千问/混元一样，只填 Base URL + Key 即可自动识别全部可用模型。'
            : 'The gateway is compatible with both OpenAI and Anthropic protocols. Codex and Claude Code auto-discover all available models by filling in just the Base URL and key.'}
        </Typography.Paragraph>
        <Table
          size="small"
          pagination={false}
          dataSource={overviewRows}
          rowKey="k"
          columns={[
            { title: zh ? '客户端/协议' : 'Client / protocol', dataIndex: 'k' },
            { title: zh ? '说明' : 'Notes', dataIndex: 'v' },
          ]}
        />
      </Card>

      <Card title={`🤖 Codex ${zh ? '接入' : 'Integration'}`} style={{ marginBottom: 16 }}>
        <Typography.Paragraph style={{ fontSize: 13, color: '#555' }}>
          {zh
            ? '方式一：cc-switch（推荐）—— 添加供应商，类型选 OpenAI Codex，config 填下方 config.toml 内容（model_catalog_json 指向网关自动维护的目录文件）。方式二：手动编辑 ~/.codex/config.toml 与 auth.json：'
            : 'Option 1 (recommended): cc-switch — add a provider of type OpenAI Codex and paste the config.toml below (model_catalog_json points to the gateway-maintained catalog). Option 2: edit ~/.codex/config.toml and auth.json manually:'}
        </Typography.Paragraph>
        <Code>{CODE_TOML}</Code>
        <Code>{CODE_AUTH}</Code>
        <div style={{ ...TIP }}>
          {zh
            ? '💡 模型目录文件由网关在模型变更时自动重写（含描述），Codex 每次启动加载最新清单——无需手工维护。'
            : '💡 The model catalog is rewritten by the gateway on model changes (with descriptions); Codex loads the latest list on every start — no manual maintenance.'}
        </div>
      </Card>

      <Card title={`🧠 Claude Code ${zh ? '接入' : 'Integration'}`} style={{ marginBottom: 16 }}>
        <Typography.Paragraph style={{ fontSize: 13, color: '#555' }}>
          {zh
            ? '方式一：cc-switch（推荐），环境变量填下方 6 项。方式二：手动编辑 ~/.claude/settings.json 的 env：'
            : 'Option 1 (recommended): cc-switch with the 6 env vars below. Option 2: edit the env block in ~/.claude/settings.json:'}
        </Typography.Paragraph>
        <Code>{CODE_CLAUDE}</Code>
        <div style={TIP}>
          {zh
            ? '💡 CLAUDE_CODE_USE_GATEWAY=1 是激活模型发现的关键开关（仅设 DISCOVERY 无效）；启动后 banner 显示 Cloud gateway 即接入成功。'
            : '💡 CLAUDE_CODE_USE_GATEWAY=1 is the key switch that activates model discovery (DISCOVERY alone is not enough); a "Cloud gateway" banner confirms success.'}
        </div>
      </Card>

      <Card title="OpenAI SDK (Python)" style={{ marginBottom: 16 }}>
        <Code>{CODE_SDK}</Code>
      </Card>

      <Card title={`❓ ${zh ? '常见问题' : 'FAQ'}`}>
        {faqs.map(([q, a], i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>
              {zh ? '问：' : 'Q: '}
              {q}
            </Typography.Text>
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {zh ? '答：' : 'A: '}
              {a}
            </Typography.Text>
          </div>
        ))}
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('docs.title')} · ModelHub
      </Typography.Text>
    </div>
  )
}
