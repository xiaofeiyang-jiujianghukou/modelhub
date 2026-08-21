"""
Anthropic 供应商适配器
将 OpenAI Chat Completions 格式转换为 Anthropic Messages API 格式，响应转回 OpenAI 格式
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx
from loguru import logger

from src.config import settings
from src.providers.base import BaseProvider, make_upstream_client
from src.services.chat_tools import chat_tool_call_element

# Anthropic stop_reason → OpenAI finish_reason
_FINISH_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
}


def _openai_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    """chat tools → Anthropic tools（function.parameters → input_schema；过滤无名工具）

    OpenAI: {"type":"function","function":{"name","description","parameters"}}
    Anthropic: {"name","description","input_schema"}
    """
    result: list[dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        name = fn.get("name") or t.get("name")
        if not name:
            continue
        result.append({
            "name": name,
            "description": fn.get("description") or t.get("description") or "",
            "input_schema": fn.get("parameters") or t.get("input_schema") or {"type": "object"},
        })
    return result


def _openai_tool_choice_to_anthropic(tc: Any) -> Optional[dict[str, Any]]:
    """chat tool_choice → Anthropic tool_choice（auto/none/required/具体函数）"""
    if tc is None:
        return None
    if isinstance(tc, str):
        return {
            "auto": {"type": "auto"},
            "none": {"type": "none"},
            "required": {"type": "any"},
            "any": {"type": "any"},
        }.get(tc)
    if isinstance(tc, dict):
        name = (tc.get("function") or {}).get("name") or tc.get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def _content_to_text(content: Any) -> str:
    """把 chat content（字符串或 blocks 数组）压成纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", None):
                parts.append(str(b.get("text", "")))
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return "" if content is None else str(content)


def _openai_messages_to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """chat messages → Anthropic messages（含工具调用往返）

    - assistant.tool_calls → content blocks 里的 tool_use（arguments JSON 串 → input 对象）
    - role="tool" → user content 里的 tool_result（Anthropic 不认识 role="tool"）
    - 连续的多个 tool 结果合并进同一条 user 消息：Anthropic 要求一轮并行工具调用的
      全部 tool_result 出现在同一条 user 消息中，拆成多条会报 tool_use/tool_result 不匹配
    """
    out: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id") or "",
                "content": _content_to_text(msg.get("content")),
            }
            # 合并进上一条 user 消息（同轮并行工具结果必须同处一条消息）
            if out and out[-1]["role"] == "user" and isinstance(out[-1].get("content"), list) \
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            text = _content_to_text(msg.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg["tool_calls"]:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                    "name": fn.get("name", ""),
                    "input": args if isinstance(args, dict) else {"value": args},
                })
            out.append({"role": "assistant", "content": blocks})
            continue

        out.append({"role": role, "content": msg.get("content")})

    return out


def _convert_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Anthropic usage → OpenAI usage，保留缓存字段。

    Anthropic 的 input_tokens 只是「未缓存部分」，完整 prompt 规模为
        input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    只取 input_tokens 会在命中缓存时严重少算（命中 90% 就少算 90%），
    导致计费与日志失真、命中率无法计算。这里合计为 prompt_tokens，
    并透传两个缓存字段供 services/cache_usage.py 归一化入库（Layer 3）。

    注意：命中部分上游实价 0.1×、写缓存 1.25×，网关当前按统一单价计费，
    分档计价（读/写/未缓存不同价）是后续改进项。
    """
    uncached = usage.get("input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    completion = usage.get("output_tokens", 0) or 0
    prompt_total = uncached + cache_read + cache_write

    out: dict[str, Any] = {
        "prompt_tokens": prompt_total,
        "completion_tokens": completion,
        "total_tokens": prompt_total + completion,
    }
    # 缓存字段原样透传（cache_usage.extract_cache_usage 解析 cache_read 为命中）
    if "cache_read_input_tokens" in usage:
        out["cache_read_input_tokens"] = cache_read
    if "cache_creation_input_tokens" in usage:
        out["cache_creation_input_tokens"] = cache_write
    return out


class AnthropicProvider(BaseProvider):
    """Anthropic Claude 系列模型适配器"""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0):
        super().__init__(base_url, api_key, timeout_seconds)
        # 持久连接池：跨请求复用 TCP/TLS。trust_env=False 忽略系统代理
        # （用户多用智谱 anthropic 兼容端点，直连即可；且系统 socks:// 代理 httpx 不认会报错）
        self._http = make_upstream_client(timeout=timeout_seconds)

    @property
    def name(self) -> str:
        return "anthropic"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _endpoint(self, path: str) -> str:
        """构建 Anthropic API 端点，兼容 base_url 带/不带 /v1 后缀。

        registry 默认 base_url=https://api.anthropic.com（不带 /v1），但界面可配
        成 …/v1，此时若再拼 /v1/... 会得到 …/v1/v1/... 404。统一在此收敛。
        """
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}{path}"
        return f"{base}/v1{path}"

    async def health_check(self) -> bool:
        """健康检查：请求 Anthropic /v1/models 端点验证连通性

        - 未配置 key：直接 False，不发网络请求（上游必拒，省去网络等待）
        - 401/403：无 key 或 Key 无效 → False（避免"测试通过"假阳性）
        - 其他 4xx：连通正常 → True；5xx/异常：False
        - 探测用短超时（≤5s）：连通性测试不该等业务 60s 超时
        """
        if not self.api_key:
            return False
        try:
            probe_timeout = min(self.timeout_seconds, 5.0)
            resp = await self._http.get(
                self._endpoint("/models"),
                headers=self._headers(),
                timeout=probe_timeout,
            )
            if resp.status_code in (401, 403):
                return False
            return resp.status_code < 500
        except Exception:
            return False

    def _apply_cache_control(self, body: dict[str, Any]) -> None:
        """注入 Anthropic 显式缓存标记（Layer 2，原地修改 body）。

        放置位置：最后一个 system content block。Anthropic 渲染顺序是
        tools → system → messages，所以 system 末尾的单个 breakpoint 能同时
        覆盖 tools + system —— 一个 breakpoint 拿到最大前缀（上限 4 个）。

        - system 字符串转为 content blocks 数组（Anthropic 两种形式都接受）
        - 低于模型最低门槛时上游静默不缓存（usage.cache_creation_input_tokens=0），
          不报错也不计费，所以无需在网关侧精确判断门槛（各模型 512~4096 且非单调）
        - 命中读 0.1×，写 1.25×(5m)/2×(1h)：多轮复用 system 才回本，故可配置关闭
        - 命中数据回流：usage.cache_read_input_tokens → cache_usage.py 归一化入库
        """
        if not settings.anthropic_cache_control:
            return
        system = body.get("system")
        if not system:
            return  # 无 system 时不注入（messages 是易变部分，不做 breakpoint）

        cache_control: dict[str, Any] = {"type": "ephemeral"}
        if settings.anthropic_cache_ttl and settings.anthropic_cache_ttl != "5m":
            cache_control["ttl"] = settings.anthropic_cache_ttl

        if isinstance(system, str):
            body["system"] = [{"type": "text", "text": system, "cache_control": cache_control}]
        elif isinstance(system, list) and system:
            # 已是 blocks 数组：标记最后一个 block（不覆盖客户端已有的 cache_control）
            blocks = [dict(b) if isinstance(b, dict) else b for b in system]
            last = blocks[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = cache_control
            body["system"] = blocks

    def _convert_request(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        OpenAI messages 格式 → Anthropic Messages API 格式
        - system 消息提取为顶层 system 字段
        - tools / tool_choice 转为 Anthropic 格式（input_schema / {"type":"tool"}）
        - assistant.tool_calls → tool_use blocks；role="tool" → tool_result blocks
        - system 末尾注入 cache_control（显式缓存，命中省 90%）
        """
        messages = payload.get("messages", [])
        system_content = None
        chat_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content")
            else:
                chat_messages.append(msg)

        body: dict[str, Any] = {
            "model": upstream_model,
            "messages": _openai_messages_to_anthropic(chat_messages),
            "max_tokens": payload.get("max_tokens", 4096),
        }
        if system_content:
            body["system"] = system_content
        if "temperature" in payload:
            body["temperature"] = payload["temperature"]
        if "top_p" in payload:
            body["top_p"] = payload["top_p"]
        if "stop" in payload:
            stop = payload["stop"]
            body["stop_sequences"] = [stop] if isinstance(stop, str) else stop
        # 工具定义透传（Claude Code 等客户端依赖 tool_use 执行 Bash/Read/Edit）
        anthropic_tools = _openai_tools_to_anthropic(payload.get("tools"))
        if anthropic_tools:
            body["tools"] = anthropic_tools
            tc = _openai_tool_choice_to_anthropic(payload.get("tool_choice"))
            if tc:
                body["tool_choice"] = tc
        self._apply_cache_control(body)
        return body

    def _convert_response(self, anthropic_resp: dict[str, Any], model: str) -> dict[str, Any]:
        """Anthropic 响应 → OpenAI Chat Completion 格式（含 tool_use → tool_calls）"""
        content = ""
        tool_calls: list[dict[str, Any]] = []
        for block in anthropic_resp.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(chat_tool_call_element(
                    block.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                    block.get("name", ""),
                    json.dumps(block.get("input") or {}, ensure_ascii=False),
                ))

        usage = anthropic_resp.get("usage", {})
        stop_reason = anthropic_resp.get("stop_reason", "stop")
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content or None if tool_calls else content,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": f"chatcmpl-{anthropic_resp.get('id', uuid.uuid4().hex)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                # Anthropic tool_use → OpenAI tool_calls；max_tokens → length
                "finish_reason": _FINISH_REASON_MAP.get(stop_reason, stop_reason or "stop"),
            }],
            "usage": _convert_usage(usage),
        }

    async def chat_completions(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = self._convert_request(upstream_model, payload)
        try:
            resp = await self._http.post(
                self._endpoint("/messages"),
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            return self._convert_response(resp.json(), payload.get("model", upstream_model))
        except httpx.HTTPStatusError as exc:
            logger.warning("anthropic upstream error status={}", exc.response.status_code)
            try:
                detail = exc.response.json()
                msg = detail.get("error", {}).get("message", exc.response.text)
            except Exception:
                msg = exc.response.text
            return self._openai_error(msg, "api_error", "upstream_error", exc.response.status_code)
        except Exception as exc:
            logger.error("anthropic request failed: {}", exc)
            return self._openai_error(str(exc), "api_error", "upstream_error", 500)

    async def chat_completions_stream(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        流式对话：Anthropic stream=true → 转换为 OpenAI SSE 格式
        Anthropic 事件类型：content_block_delta / message_delta / message_stop
        usage 从 message_start（input+缓存字段）与 message_delta（output）累积，
        末尾输出一个 usage chunk 供网关计费与缓存命中监控
        """
        body = {**self._convert_request(upstream_model, payload), "stream": True}
        request_model = payload.get("model", upstream_model)
        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        usage_acc: dict[str, Any] = {}
        tool_index_map: dict[Any, int] = {}   # Anthropic content block index → OpenAI tool_calls index
        stop_reason: str = "end_turn"

        try:
            async with self._http.stream(
                "POST",
                self._endpoint("/messages"),
                headers=self._headers(),
                json=body,
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line.startswith("data:"):
                        continue
                    data_str = raw_line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # usage 累积：Anthropic 把 input/缓存字段放在 message_start，
                    # 最终 output_tokens 放在 message_delta（末尾 chunk 统一输出）
                    if etype == "message_start":
                        started = (event.get("message") or {}).get("usage") or {}
                        if started:
                            usage_acc.update(started)
                    elif etype == "message_delta":
                        delta_usage = event.get("usage") or {}
                        if delta_usage:
                            usage_acc.update(delta_usage)
                        # stop_reason=tool_use 时最终 finish_reason 要报 tool_calls
                        sr = (event.get("delta") or {}).get("stop_reason")
                        if sr:
                            stop_reason = sr

                    # 工具调用流式：content_block_start(tool_use) 起头，
                    # 随后 input_json_delta 增量吐 arguments（转 OpenAI tool_calls delta）
                    if etype == "content_block_start":
                        cb = event.get("content_block") or {}
                        if cb.get("type") == "tool_use":
                            tc_index = len(tool_index_map)
                            tool_index_map[event.get("index")] = tc_index
                            chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"tool_calls": [{
                                        "index": tc_index,
                                        "id": cb.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                                        "type": "function",
                                        "function": {"name": cb.get("name", ""), "arguments": ""},
                                    }]},
                                    "finish_reason": None,
                                }],
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"

                    if etype == "content_block_delta":
                        delta = event.get("delta") or {}
                        delta_text = delta.get("text", "")
                        if delta_text:
                            chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": delta_text},
                                    "finish_reason": None,
                                }],
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"
                        elif delta.get("type") == "input_json_delta":
                            tc_index = tool_index_map.get(event.get("index"))
                            partial = delta.get("partial_json", "")
                            if tc_index is not None and partial:
                                chunk = {
                                    "id": chat_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": request_model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"tool_calls": [{
                                            "index": tc_index,
                                            "function": {"arguments": partial},
                                        }]},
                                        "finish_reason": None,
                                    }],
                                }
                                yield f"data: {json.dumps(chunk)}\n\n"

                    elif etype == "message_stop":
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": request_model,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": _FINISH_REASON_MAP.get(stop_reason, "stop"),
                            }],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                        # 末尾 usage chunk（OpenAI include_usage 风格）：
                        # 网关据此流后计费 + 解析缓存命中（Layer 3）
                        if usage_acc:
                            usage_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_model,
                                "choices": [],
                                "usage": _convert_usage(usage_acc),
                            }
                            yield f"data: {json.dumps(usage_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

        except httpx.HTTPStatusError as exc:
            logger.warning("anthropic stream error status=", exc.response.status_code)
            err = json.dumps({"error": {"message": "Upstream error", "type": "api_error", "code": "upstream_error"}})
            yield f"data: {err}\n\ndata: [DONE]\n\n"
        except Exception as exc:
            logger.error("anthropic stream failed: {}", exc)
            err = json.dumps({"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}})
            yield f"data: {err}\n\ndata: [DONE]\n\n"

    async def image_generations(
        self,
        upstream_model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Anthropic 目前不支持图像生成"""
        return self._openai_error(
            "Anthropic does not support image generation",
            "invalid_request_error",
            "unsupported_operation",
            400,
        )
