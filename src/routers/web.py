"""
Web 控制台路由（历史占位）

前端已迁移至 frontend/（Vite + React + Ant Design + i18next），
构建产物 frontend/dist 由 main.py 的 SPA fallback 统一 serve。
此文件保留空 router 以兼容 main.py 的挂载。
"""

from fastapi import APIRouter

router = APIRouter(tags=["Web"])
