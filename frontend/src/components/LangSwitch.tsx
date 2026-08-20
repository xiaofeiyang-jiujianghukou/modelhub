import { Button } from 'antd'
import { GlobalOutlined } from '@ant-design/icons'
import { useUiStore } from '../store/ui'

export default function LangSwitch() {
  const lang = useUiStore((s) => s.lang)
  const toggleLang = useUiStore((s) => s.toggleLang)
  return (
    <Button icon={<GlobalOutlined />} onClick={toggleLang} size="small">
      {lang === 'en' ? '中文' : 'EN'}
    </Button>
  )
}
