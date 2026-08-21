"""
OpenAI 供应商适配器
直接转发 OpenAI 格式请求，同时支持 LLM 和图像生成
"""

import json
from typing import Any, AsyncGenerator

import httpx

from src.providers.base import make_upstream_client
from loguru import logger

from src.providers.base import BaseProvider


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _split_inline_think(content: str) -> tuple[str, str]:
    """拆出内联 <think> 思考段，返回 (reasoning, content)。

    未闭合的 <think>（流被截断）视为思考，剩余全部归思考。
    """
    if _THINK_OPEN not in content:
        return "", content
    import re
    pat = re.escape(_THINK_OPEN) + r"(.*?)" + re.escape(_THINK_CLOSE)
    parts = re.findall(pat, content, re.S)
    rest = re.sub(pat, "", content, flags=re.S)
    if _THINK_OPEN in rest:  # 未闭合（截断）
        head, tail = rest.split(_THINK_OPEN, 1)
        parts.append(tail)
        rest = head
    return "\n".join(p.strip() for p in parts if p.strip()), rest.strip()


def _split_message_inline_think(data: dict[str, Any]) -> None:
    """非流式：choices[].message.content 内联 <think> 拆到 reasoning_content（原地）"""
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content")
        if isinstance(content, str):
            reasoning, clean = _split_inline_think(content)
            if reasoning:
                msg["content"] = clean
                msg["reasoning_content"] = (msg.get("reasoning_content") or "") + reasoning
    except (KeyError, IndexError, TypeError):
        pass


class _InlineThinkSplitter:
    """流式内联 <think> 拆分器：feed(content_delta) -> (reasoning_delta, content_delta)。

    标签可能被 chunk 边界切断（如 "<th" + "ink>"），用尾部前缀缓冲处理；
    flush() 在流结束时输出残留缓冲。
    """

    def __init__(self) -> None:
        self._in_think = False
        self._buf = ""

    def _tail_partial(self, tag: str) -> int:
        for k in range(min(len(self._buf), len(tag) - 1), 0, -1):
            if self._buf.endswith(tag[:k]):
                return k
        return 0

    def feed(self, text: str) -> tuple[str, str]:
        self._buf += text
        r_out: list[str] = []
        c_out: list[str] = []
        while self._buf:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            idx = self._buf.find(tag)
            if idx != -1:
                (r_out if self._in_think else c_out).append(self._buf[:idx])
                self._buf = self._buf[idx + len(tag):]
                self._in_think = not self._in_think
                continue
            keep = self._tail_partial(tag)
            cut = len(self._buf) - keep
            (r_out if self._in_think else c_out).append(self._buf[:cut])
            self._buf = self._buf[cut:]
            break
        return "".join(r_out), "".join(c_out)

    def flush(self) -> tuple[str, str]:
        buf, self._buf = self._buf, ""
        return (buf, "") if self._in_think else ("", buf)


class OpenAIProvider(BaseProvider):
    """OpenAI 官方 API 适配器（直接转发，格式已兼容）"""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0):
        super().__init__(base_url, api_key, timeout_seconds)
        # 持久连接池：跨请求复用 TCP/TLS（trust_env=False 忽略系统代理，国内直连更可预测）
        # build_provider 缓存 adapter 后，连接池跨请求存活，避免每次握手
        self._http = make_upstream_client(timeout=timeout_seconds)
        # base_url 为完整 API 根路径（如 https://api.deepseek.com/v1 或 https://open.bigmodel.cn/api/paas/v4）

    @property
    def name(self) -> str:
        return "openai"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> bool:
        """健康检查：请求 /models 端点验证连通性

        - 未配置 key：直接 False，不发网络请求（上游必拒，省去网络等待）
        - 2xx / 404（端点存在性差异）：连通正常 -> True
        - 401/403：API Key 无效 -> False（避免"测试通过"假阳性）
        - 其他 4xx：连通正常 -> True；5xx/异常：False
        - 探测用短超时（≤5s）：连通性测试不该等业务 60s 超时（国内直连被墙时秒回失败）
        """
        if not self.api_key:
            return False
        try:
            probe_timeout = min(self.timeout_seconds, 5.0)
            # 复用持久 client 的连接池，仅用短超时覆盖本次探测
            resp = await self._http.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=probe_timeout,
            )
            if resp.status_code in (401, 403):
                return False
            return resp.status_code < 500
        except Exception:
            return False

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """非流式对话：直接转发至 OpenAI"""
        body = {**payload, "model": upstream_model, "stream": False}
        try:
            resp = await self._http.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            _split_message_inline_think(data)
            return data
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "openai upstream error status={} body={}",
                exc.response.status_code,
                exc.response.text[:200],
            )
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {"error": {"message": exc.response.text, "type": "api_error", "code": "upstream_error"}}
            err_body["_status_code"] = exc.response.status_code
            return err_body
        except Exception as exc:
            logger.error("openai request failed: {}", exc)
            return self._openai_error(str(exc), "api_error", "upstream_error", 500)

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """流式对话：透明代理 SSE 流（不改 SSE 内容）"""
        body = {**payload, "model": upstream_model, "stream": True}
        # 要求上游在流末尾返回 usage（OpenAI 兼容，DeepSeek/方舟等支持），网关据此计费
        if "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        try:
            async with self._http.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as resp:
                # 非 2xx：在流关闭前读取 body，携带真实错误消息
                if resp.status_code >= 400:
                    err_body: dict = {}
                    try:
                        raw = await resp.aread()
                        err_body = json.loads(raw)
                    except Exception:
                        pass
                    msg = err_body.get("error", {}).get("message", "Upstream error")
                    code = err_body.get("error", {}).get("code", "upstream_error")
                    err = json.dumps({
                        "error": {"message": msg, "type": "upstream_error", "code": code},
                        "_status_code": resp.status_code,
                    })
                    # 拆行 yield：每行一个 data 块（合并会导致上层 JSON 解析失败）
                    yield f"data: {err}\n\n"
                    yield f"data: [DONE]\n\n"
                    return
                # 内联 <think> 拆分：MiniMax-M3 / 部分开源模型把思考内联在 content，
                # 网关统一拆成 reasoning_content（流式状态机处理跨 chunk 标签切断）
                splitter = _InlineThinkSplitter()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        yield line + "\n"
                        continue
                    payload_str = line[5:].strip()
                    if payload_str == "[DONE]":
                        r, c = splitter.flush()
                        if r or c:
                            tail: dict[str, Any] = {}
                            if r:
                                tail["reasoning_content"] = r
                            if c:
                                tail["content"] = c
                            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": tail}]}, ensure_ascii=False) + "\n"
                        yield line + "\n"
                        continue
                    try:
                        obj = json.loads(payload_str)
                    except Exception:
                        yield line + "\n"
                        continue
                    choices = obj.get("choices") or []
                    delta = choices[0].get("delta") if choices else None
                    if isinstance(delta, dict) and isinstance(delta.get("content"), str) and delta["content"]:
                        orig = delta["content"]
                        r, c = splitter.feed(delta.pop("content"))
                        if r == "" and c == orig:
                            # 无 <think> 痕迹：原行透传（普通模型零改写）
                            delta["content"] = orig
                            yield line + "\n"
                            continue
                        if r:
                            delta["reasoning_content"] = (delta.get("reasoning_content") or "") + r
                        if c:
                            delta["content"] = c
                        yield "data: " + json.dumps(obj, ensure_ascii=False) + "\n"
                        continue
                    yield line + "\n"
        except Exception as exc:
            logger.error("openai stream failed: {}", exc)
            err = json.dumps({"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}})
            yield f"data: {err}\n\n"
            yield f"data: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """图像生成：直接转发至 OpenAI Images API"""
        body = {**payload, "model": upstream_model}
        try:
            resp = await self._http.post(
                f"{self.base_url}/images/generations",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("openai image error status={}", exc.response.status_code)
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {"error": {"message": exc.response.text, "type": "api_error", "code": "upstream_error"}}
            err_body["_status_code"] = exc.response.status_code
            return err_body
        except Exception as exc:
            logger.error("openai image request failed: {}", exc)
            return self._openai_error(str(exc), "api_error", "upstream_error", 500)
