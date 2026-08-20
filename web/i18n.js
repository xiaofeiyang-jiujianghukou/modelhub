/**
 * i18n.js - ModelHub 中英文切换
 * - 字典 DICT: 中文原文 -> 英文
 * - t(zh): JS 动态文本翻译（I18N.t('登录失败')）
 * - apply(lang): 运行时遍历 body 文本节点 + placeholder/title/aria-label 替换
 * - i18n:change 事件: 语言切换后派发，供页面 JS 重新渲染动态内容
 * - localStorage 'mh_lang' 持久化；默认按 navigator.language 探测
 */
(function () {
  const DICT = {
    // ── 通用 / 标题 ──
    "⚡ 模枢 ModelHub": "⚡ ModelHub",
    "模枢 ModelHub": "ModelHub",
    "多模型智能编排网关控制台": "Multi-Model Orchestration Console",
    "登录 - 模枢 ModelHub": "Login - ModelHub",
    "控制台 - 模枢 ModelHub": "Console - ModelHub",
    // ── login ──
    "登录": "Login",
    "注册": "Register",
    "邮箱": "Email",
    "密码": "Password",
    "密码（至少8位）": "Password (min 8 chars)",
    "显示名": "Display name",
    "你的名字": "Your name",
    "登 录": "Log in",
    "注 册": "Sign up",
    "登录失败": "Login failed",
    "注册成功！请登录": "Registered! Please log in",
    "注册失败": "Registration failed",
    "请先登录": "Please log in first",
    "退出登录": "Log out",
    "保存": "Save", "取消": "Cancel", "确定": "OK", "关闭": "Close", "删除": "Delete",
    "编辑": "Edit", "添加": "Add", "搜索": "Search", "刷新": "Refresh",
    "全部": "All", "状态": "Status", "名称": "Name", "类型": "Type", "操作": "Actions",
    "启用": "Enabled", "禁用": "Disabled", "创建时间": "Created", "更新时间": "Updated"
  };

  const KEY = 'mh_lang';
  let LANG = localStorage.getItem(KEY) ||
    (navigator.language && navigator.language.toLowerCase().startsWith('en') ? 'en' : 'zh');

  function t(zh) { return LANG === 'en' ? (DICT[zh] || zh) : zh; }

  function apply(lang) {
    LANG = lang;
    document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
    if (document.body) walk(document.body);
    document.querySelectorAll('[data-lang-toggle]').forEach(function (b) {
      b.textContent = lang === 'en' ? '中文' : 'EN';
    });
    document.dispatchEvent(new CustomEvent('i18n:change', { detail: { lang: lang } }));
  }

  function walk(el) {
    el.childNodes.forEach(function (n) {
      if (n.nodeType === 3) { // text node
        var k = n.textContent.trim();
        if (k && DICT[k]) {
          if (n._i18nZh === undefined) n._i18nZh = n.textContent;
          n.textContent = LANG === 'en' ? DICT[k] : n._i18nZh;
        }
      } else if (n.nodeType === 1 && n.tagName !== 'SCRIPT' && n.tagName !== 'STYLE') {
        ['placeholder', 'title', 'aria-label'].forEach(function (a) {
          if (n.hasAttribute(a)) {
            var v = n.getAttribute(a);
            if (DICT[v]) {
              if (n.dataset['_i18n_' + a] === undefined) n.dataset['_i18n_' + a] = v;
              n.setAttribute(a, LANG === 'en' ? DICT[v] : n.dataset['_i18n_' + a]);
            }
          }
        });
        walk(n);
      }
    });
  }

  window.I18N = {
    get lang() { return LANG; },
    t: t,
    apply: apply,
    set: function (lang) { localStorage.setItem(KEY, lang); apply(lang); },
    toggle: function () { this.set(LANG === 'en' ? 'zh' : 'en'); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { apply(LANG); });
  } else {
    apply(LANG);
  }
})();
