"""
Web 控制台（单页应用）
提供简单的管理界面：注册/登录/余额/模型/Key/日志
"""

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Web"])

# 控制台静态资源目录
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "web")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """控制台主页"""
    html_path = os.path.join(WEB_DIR, "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """登录/注册页"""
    html_path = os.path.join(WEB_DIR, "login.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Login page not found</h1>")
