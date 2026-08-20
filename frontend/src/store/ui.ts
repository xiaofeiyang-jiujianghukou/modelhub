import { create } from 'zustand'
import i18n from '../i18n'

export type Lang = 'zh' | 'en'

interface UiState {
  lang: Lang
  currency: 'CNY' | 'USD'
  setLang: (l: Lang) => void
  toggleLang: () => void
  setCurrency: (c: 'CNY' | 'USD') => void
}

function applyDocLang(l: Lang) {
  localStorage.setItem('mh_lang', l)
  document.documentElement.lang = l === 'en' ? 'en' : 'zh-CN'
  document.title = l === 'en' ? 'ModelHub Console' : '模枢 ModelHub'
}

export const useUiStore = create<UiState>((set, get) => {
  const initialLang = ((localStorage.getItem('mh_lang') ||
    (navigator.language.toLowerCase().startsWith('en') ? 'en' : 'zh')) as Lang) || 'zh'
  applyDocLang(initialLang)

  return {
    lang: initialLang,
    currency: (localStorage.getItem('currency') as 'CNY' | 'USD') || 'CNY',
    setLang: (l) => {
      applyDocLang(l)
      i18n.changeLanguage(l)
      set({ lang: l })
    },
    toggleLang: () => get().setLang(get().lang === 'en' ? 'zh' : 'en'),
    setCurrency: (c) => {
      localStorage.setItem('currency', c)
      set({ currency: c })
    },
  }
})
