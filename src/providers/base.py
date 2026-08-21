"""
供应商适配器抽象基类
所有供应商适配器必须继承此类并实现抽象方法
"""

import abc
import httpx
from typing import Any, AsyncGenerator, Optional


def make_upstream_client(**kwargs) -> httpx.AsyncClient:
    """上游 HTTP 客户端公共工厂：统一注入代理与默认参数。

    所有供应商适配器 / 模型同步一律经此创建 httpx 客户端，代理只在这里配置：
    - settings.upstream_proxy 非空时境外供应商（Grok/OpenAI/Claude/Gemini 等）走代理
    - trust_env=False 忽略系统代理环境变量（境内供应商直连）
    """
    from src.config import settings
    kwargs.setdefault("trust_env", False)
    kwargs.setdefault("proxy", settings.upstream_proxy or None)
    return httpx.AsyncClient(**kwargs)


class BaseProvider(abc.ABC):
    """
    供应商适配器基类
    负责将 OpenAI 格式的请求转换为各供应商的实际格式，并将响应转换回 OpenAI 格式
    """

    async def health_check(self) -> bool:
        """默认健康检查：不发起网络请求，子类可覆盖实现真实探测"""
        return True

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        # 持久连接池：由子类 __init__ 用本模块 httpx 创建（这样测试 monkeypatch
        # 子类模块的 httpx.AsyncClient 才能生效）。build_provider 缓存 adapter
        # 后，跨请求复用 TCP/TLS，避免每次握手
        self._http: Optional[httpx.AsyncClient] = None

    async def aclose(self) -> None:
        """关闭持久连接池（应用 shutdown 或 provider 配置变更时调用）"""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """供应商唯一标识"""
        ...

    @abc.abstractmethod
    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        非流式文本对话
        :param upstream_model: 上游实际模型名（如 gpt-4o）
        :param payload: OpenAI 格式请求体
        :return: OpenAI 格式响应体
        """
        ...

    @abc.abstractmethod
    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        流式文本对话，逐块 yield SSE 行（包含 data: 前缀）
        :param upstream_model: 上游实际模型名
        :param payload: OpenAI 格式请求体
        :yields: SSE 文本行
        """
        ...

    @abc.abstractmethod
    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        图像生成
        :param upstream_model: 上游实际模型名
        :param payload: OpenAI 格式请求体
        :return: OpenAI 格式响应体
        """
        ...

    def _openai_error(self, message: str, error_type: str, code: str, status: int = 500) -> dict:
        """构建标准 OpenAI 错误响应体"""
        return {
            "_status_code": status,
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
                "param": None,
            },
        }
