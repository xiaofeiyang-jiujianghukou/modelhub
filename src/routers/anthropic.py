"""
Anthropic Messages API 协议兼容层
便于 Claude Code / Claude 生态通过 ANTHROPIC_BASE_URL 接入本网关

端点: POST /v1/messages
认证: x-api-key: sk-xxx（网关 API Key）
协议: 与 Anthropic Messages API 完全兼容（含流式 SSE）

Claude Code 配置示例:
  export ANTHROPIC_BASE_URL=http://localhost:8000
  export ANTHROPIC_API_KEY=<网关 API Key>
  export ANTHROPIC_MODEL=doubao-seed-2-0-pro
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.db.models import User, ApiKey, Model, Provider
from src.config import settings
from src.middleware.billing import billing_service, calc_llm_cost
from src.services.router import router_service
from src.services.chat_tools import chat_tool, chat_tool_call_element, chat_tool_result
from src.services.model_key import parse_model_key, is_reasoning_model

router = APIRouter(tags=["Anthropic"])


# ── 请求/响应模型（Anthropic 格式）──────────────────────────────────────────

class AnthropicMessage(BaseModel):
    role: str  # user | assistant
    content: str | list[dict] = Field(..., description="字符串或 content block 列表")


class AnthropicRequest(BaseModel):
    model: str
    max_tokens: int = Field(..., ge=1)
    messages: list[AnthropicMessage]
    system: Optional[str | list[dict]] = None   # Claude Code 2.x 发送 content blocks 数组
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    top_p: Optional[float] = Field(default=None)
    stream: Optional[bool] = False
    stop_sequences: Optional[list[str]] = None
    metadata: Optional[dict] = None
    tools: Optional[list[dict]] = None          # Anthropic tools（含 name/description/input_schema）
    tool_choice: Optional[dict] = None          # Anthropic tool_choice


# ── 模型映射 ────────────────────────────────────────────────────────────────

# Claude 官方内置模型名前缀（映射到默认模型，不是网关模型包装）
_CLAUDE_BUILTIN_PREFIXES = (
    "claude-opus", "claude-sonnet", "claude-haiku", "claude-fable",
    "opus[", "sonnet[", "haiku[",
)


def _resolve_model(model_name: str) -> str:
    """
    模型名解析：
    1. 网关模型 ID → 直接使用
    2. claude-<网关模型ID> 包装（/model 选择器发来的）→ 剥壳映射到真实模型
    3. Claude 官方内置名（claude-opus-4-x / opus[1m] 等）→ 默认模型
    """
    default = getattr(settings, "default_claude_model", None) or "glm/glm-4-flash"
    lower = model_name.lower()
    if lower in ("opus", "sonnet", "haiku") or lower.startswith(_CLAUDE_BUILTIN_PREFIXES):
        logger.info("claude builtin model {} mapped to {}", model_name, default)
        return default
    if model_name.startswith("claude-"):
        stripped = model_name[len("claude-"):]
        logger.info("claude-wrapped model {} resolved to {}", model_name, stripped)
        return stripped
    return model_name


# ── 请求转换（Anthropic → 内部 OpenAI 格式）─────────────────────────────────

def _extract_text(content: str | list) -> str:
    """提取纯文本：字符串原样；content block 列表拼接 text 块"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            # image 等非文本块跳过
        else:
            parts.append(str(block))
    return "".join(parts)


def _block_text(content) -> str:
    """提取 tool_result 的 content（字符串或 content block 列表）为纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _anthropic_message_to_openai(m: AnthropicMessage) -> list[dict]:
    """Anthropic 消息 → 一条或多条 OpenAI 消息（多轮工具调用：tool_use → tool_calls，tool_result → tool 消息）"""
    if isinstance(m.content, str):
        return [{"role": m.role, "content": m.content}]

    if m.role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in m.content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(chat_tool_call_element(
                    block.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                    block.get("name", ""),
                    json.dumps(block.get("input") or {}, ensure_ascii=False),
                ))
        msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return [msg]

    if m.role == "user":
        result: list[dict] = []
        text_parts: list[str] = []
        for block in m.content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                result.append(chat_tool_result(
                    block.get("tool_use_id", ""),
                    _block_text(block.get("content", "")),
                ))
        # 工具结果必须紧跟对应 assistant(tool_calls)，文本放最后（不能 insert(0) 插到 tool 前）
        if text_parts:
            result.append({"role": "user", "content": "".join(text_parts)})
        return result

    # system 等其他角色：退化为纯文本
    return [{"role": m.role, "content": _extract_text(m.content)}]


def _anthropic_tools_to_openai(tools: Optional[list[dict]]) -> Optional[list[dict]]:
    """Anthropic tools → chat tools（input_schema → parameters，走统一中间格式；过滤 name 为空的工具）"""
    if not tools:
        return None
    result = [chat_tool(t.get("name"), t.get("description", ""), t.get("input_schema")) for t in tools if t.get("name")]
    return result or None


def _anthropic_tool_choice_to_openai(tc: Optional[dict]):
    """Anthropic tool_choice → OpenAI tool_choice"""
    if not tc:
        return None
    t = tc.get("type")
    if t == "any":
        return "required"
    if t == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    if t == "none":
        return "none"
    return "auto"


def _to_openai_payload(req: AnthropicRequest, model_name: str) -> dict:
    """将 Anthropic 请求转换为 OpenAI 格式 payload"""
    messages = []
    # system 提示词作为第一条 system 消息（支持字符串或 content blocks 数组）
    if req.system:
        messages.append({"role": "system", "content": _extract_text(req.system)})

    for m in req.messages:
        messages.extend(_anthropic_message_to_openai(m))

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": bool(req.stream),
    }
    if req.max_tokens is not None:
        mt = req.max_tokens
        # reasoning 模型（glm-5/deepseek/doubao-seed 等）思考与正文共享 max_tokens 预算，
        # Claude Code 传的值（如 516）太小会被思考耗尽 → 正文为空。强制抬到 4096 保证正文有空间
        if is_reasoning_model(model_name):
            mt = max(mt, 4096)
        payload["max_tokens"] = mt
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.top_p is not None:
        payload["top_p"] = req.top_p
    if req.stop_sequences:
        payload["stop"] = req.stop_sequences
    # 工具定义透传（Claude Code 依赖 tool_use 执行 Bash/Read/Edit 等）
    openai_tools = _anthropic_tools_to_openai(req.tools)
    if openai_tools:
        payload["tools"] = openai_tools
        tc = _anthropic_tool_choice_to_openai(req.tool_choice)
        if tc:
            payload["tool_choice"] = tc
    return payload


# ── 响应转换（OpenAI 格式 → Anthropic 格式）─────────────────────────────────

def _openai_message_to_anthropic_content(message: dict) -> list[dict]:
    """OpenAI message（content + tool_calls）→ Anthropic content blocks（text + tool_use）"""
    content = []
    text = message.get("content") or ""
    if text:
        content.append({"type": "text", "text": text})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        content.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
            "name": fn.get("name", ""),
            "input": args if isinstance(args, dict) else {"value": args},
        })
    return content


def _to_anthropic_response(
    result: dict,
    req_id: str,
    model_name: str,
) -> dict:
    """将 OpenAI 格式响应转换为 Anthropic Messages 格式"""
    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    # 文本 + 工具调用一起转（丢弃 reasoning_content，思考过程不进入对话输出）
    content = _openai_message_to_anthropic_content(message)

    usage = result.get("usage", {})
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
    }
    finish = choices[0].get("finish_reason") if choices else "stop"

    return {
        "id": req_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason_map.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── 流式转换（OpenAI SSE → Anthropic SSE events）────────────────────────────

def _estimate_input_tokens(system, messages) -> int:
    """估算输入 token 数（中文≈1字/token，英文≈4字符/token，粗略但够用）。
    Claude Code 从流式 message_start 的 usage.input_tokens 读 token 数，
    上游 DeepSeek 的 usage 在流末尾才返回，故 message_start 用此估算值。"""
    total_chars = 0
    if isinstance(system, str):
        total_chars += len(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                total_chars += len(block.get("text", ""))
    for m in messages:
        content = getattr(m, "content", m) if not isinstance(m, dict) else m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total_chars += len(block.get("text", ""))
    return max(1, int(total_chars / 1.5))


def _anthropic_stream_events(
    gen: AsyncGenerator[str, None],
    req_id: str,
    model_name: str,
    on_finish=None,
    input_estimate: int = 0,
) -> AsyncGenerator[str, None]:
    """将 OpenAI 流式 SSE 转换为 Anthropic 流式 SSE events；
    on_finish(输入token, 输出token) 在流正常结束时回调（计费/记日志）；
    input_estimate 用于 message_start 的 usage.input_tokens（真实值流末尾才拿到）。"""
    input_tokens = 0
    output_tokens = 0

    async def wrapper():
        nonlocal input_tokens, output_tokens
        try:
            # message_start：input_tokens 用估算值（真实值末尾才到）
            start_msg = {
                "type": "message_start",
                "message": {
                    "id": req_id, "type": "message", "role": "assistant",
                    "model": model_name, "content": [],
                    "usage": {"input_tokens": input_estimate, "output_tokens": 0},
                },
            }
            yield f"event: message_start\ndata: {json.dumps(start_msg)}\n\n"

            # 流式状态：文本块 + 工具调用块（OpenAI tool_calls 按 index 累积 arguments 分片）
            text_block_index: Optional[int] = None
            tool_blocks: dict[int, dict] = {}  # openai_index -> {anthro_index, id, name, args}
            next_block_index = 0
            finish_reason = "stop"

            async for line in gen:
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue
                    # 上游错误（429 余额不足等）→ Anthropic error 事件
                    if "error" in chunk:
                        err_msg = chunk["error"].get("message", "Upstream error")
                        err_evt = {"type": "error", "error": {"type": "api_error", "message": err_msg}}
                        yield f"event: error\ndata: {json.dumps(err_evt)}\n\n"
                        yield "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"
                        return
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta", {})

                    # 文本 delta
                    content = delta.get("content", "")
                    if content:
                        if text_block_index is None:
                            text_block_index = next_block_index
                            next_block_index += 1
                            block_start = {
                                "type": "content_block_start", "index": text_block_index,
                                "content_block": {"type": "text", "text": ""},
                            }
                            yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"
                        delta_evt = {
                            "type": "content_block_delta", "index": text_block_index,
                            "delta": {"type": "text_delta", "text": content},
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(delta_evt)}\n\n"

                    # 工具调用 delta（OpenAI tool_calls → Anthropic tool_use）
                    for tc in delta.get("tool_calls") or []:
                        oi = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if oi not in tool_blocks:
                            tb = {
                                "anthro_index": next_block_index,
                                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                                "name": fn.get("name", ""),
                                "args": "",
                            }
                            tool_blocks[oi] = tb
                            next_block_index += 1
                            block_start = {
                                "type": "content_block_start", "index": tb["anthro_index"],
                                "content_block": {"type": "tool_use", "id": tb["id"], "name": tb["name"], "input": {}},
                            }
                            yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"
                        args_delta = fn.get("arguments", "")
                        if args_delta:
                            tool_blocks[oi]["args"] += args_delta
                            delta_evt = {
                                "type": "content_block_delta", "index": tool_blocks[oi]["anthro_index"],
                                "delta": {"type": "input_json_delta", "partial_json": args_delta},
                            }
                            yield f"event: content_block_delta\ndata: {json.dumps(delta_evt)}\n\n"

                    # usage 收集（部分上游在末尾 chunk 返回）
                    if chunk.get("usage"):
                        input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                        output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

            # 关闭所有打开的 content block（按 index 顺序）
            open_indices = ([text_block_index] if text_block_index is not None else []) \
                + [tb["anthro_index"] for tb in tool_blocks.values()]
            for idx in sorted(open_indices):
                block_stop = {"type": "content_block_stop", "index": idx}
                yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"

            # message_delta（工具调用 → stop_reason=tool_use）
            stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
            delta_msg = {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                # 带上游真实 input_tokens（若末尾拿到）；否则回退估算值，保证 Claude Code 能显示
                "usage": {"input_tokens": input_tokens or input_estimate, "output_tokens": output_tokens},
            }
            yield f"event: message_delta\ndata: {json.dumps(delta_msg)}\n\n"
            # message_stop
            yield "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"

            # 流正常结束 → 计费 + 记日志（token 数来自上游末尾 usage chunk）
            if on_finish:
                try:
                    await on_finish(input_tokens, output_tokens)
                except Exception as e:
                    logger.error(f"anthropic stream on_finish failed: {e}")
        except Exception as e:
            logger.error(f"anthropic stream error: {e}")
            err = {"type": "error", "error": {"type": "api_error", "message": str(e)}}
            yield f"event: error\ndata: {json.dumps(err)}\n\n"

    return wrapper()


# ── 辅助端点（Claude Code 启动时调用）────────────────────────────────────────

class CountTokensRequest(BaseModel):
    model: str
    messages: list[AnthropicMessage]
    system: Optional[str] = None


@router.post("/messages/count_tokens")
async def count_tokens(req: CountTokensRequest):
    """
    Token 估算端点（Anthropic API 兼容）
    Claude Code 发送请求前会调用此接口估算 token
    """
    # 简化估算：中文约 1 token/字，英文约 4 字符/token
    total_chars = 0
    if req.system:
        total_chars += len(req.system)
    for m in req.messages:
        if isinstance(m.content, str):
            total_chars += len(m.content)
        else:
            for block in m.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total_chars += len(block.get("text", ""))
    # 中英混合粗略估算：每 1.5 字符 ≈ 1 token
    estimated = max(1, int(total_chars / 1.5))
    return {"input_tokens": estimated}


# 注：GET /v1/models 由 models 路由统一处理（带 anthropic-version 头时返回 Anthropic 格式）


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.post("/messages")
async def anthropic_messages(
    req: AnthropicRequest,
    http_req: Request,
):
    """
    Anthropic Messages API 兼容端点
    认证: x-api-key: sk-xxx（或 Authorization: Bearer sk-xxx）
    """
    from src.database import AsyncSessionLocal

    # 认证信息由 AuthMiddleware 设置
    user: User = getattr(http_req.state, "user", None)
    api_key: ApiKey = getattr(http_req.state, "api_key", None)
    if not user or not api_key:
        return JSONResponse(
            status_code=401,
            content={"type": "error", "error": {"type": "authentication_error", "message": "Invalid API key"}},
        )

    # 模型映射（claude-* → 网关默认模型）
    model_name = _resolve_model(req.model)

    req_id = f"msg_{uuid.uuid4().hex[:24]}"
    payload = _to_openai_payload(req, model_name)

    # 余额预检
    async with AsyncSessionLocal() as db:
        try:
            await billing_service.precheck_balance(db, user.id)
        except Exception as e:
            return JSONResponse(
                status_code=getattr(e, "status_code", 500),
                content={"type": "error", "error": {"type": "billing_error", "message": str(getattr(e, "detail", e))}},
            )

        start_time = time.time()
        result, channel, provider, error_info = await router_service.route_chat(
            db, model_name, payload, stream=bool(req.stream)
        )

        if result is None:
            # 透传上游真实错误（Claude Code 会显示详细原因）
            if error_info:
                err_status = error_info.get("status_code", 502)
                if err_status >= 500:
                    err_status = 502
                return JSONResponse(
                    status_code=err_status,
                    content={"type": "error", "error": {"type": "api_error", "message": f"[{error_info.get('provider', 'upstream')}] {error_info.get('message', 'upstream error')}"}},
                )
            return JSONResponse(
                status_code=503,
                content={"type": "error", "error": {"type": "api_error", "message": "No available provider"}},
            )

        provider_name = provider.name if provider else "unknown"
        latency_ms = int((time.time() - start_time) * 1000)

        # ── 流式 ──
        if req.stream:
            async def _on_stream_finish(pt: int, ct: int):
                """流结束时：独立会话补扣费 + 记日志（原始会话已随请求返回关闭）"""
                async with AsyncSessionLocal() as _db:
                    total = pt + ct
                    mid, _vendor = parse_model_key(model_name)
                    model_obj = await Model.get_by_model_and_vendor(_db, mid, _vendor) if _vendor else None
                    cost = 0.0
                    if total > 0 and model_obj and model_obj.model_type == "llm":
                        cost = calc_llm_cost(model_obj, pt, ct)
                        if cost > 0:
                            try:
                                await billing_service.deduct(
                                    _db, user.id, cost,
                                    description=f"anthropic-messages {model_name} ({pt}+{ct})",
                                    request_log_id=req_id,
                                )
                            except Exception as e:
                                logger.warning("anthropic stream deduct failed: {}", e)
                    await billing_service.record_log(
                        _db,
                        request_id=req_id, user_id=user.id, api_key_id=api_key.id,
                        model=model_name, provider=provider_name, request_type="chat",
                        status="success", status_code=200,
                        prompt_tokens=pt, completion_tokens=ct,
                        total_tokens=total, cost_usd=cost,
                        latency_ms=int((time.time() - start_time) * 1000),
                    )

            response = StreamingResponse(
                _anthropic_stream_events(
                    result, req_id, model_name, _on_stream_finish,
                    input_estimate=_estimate_input_tokens(req.system, req.messages),
                ),
                media_type="text/event-stream",
            )
            response.headers["X-Gateway-Model"] = model_name
            response.headers["X-Gateway-Provider"] = provider_name
            response.headers["X-Gateway-Request-ID"] = req_id
            return response

        # ── 非流式：转换 + 计费 ──
        anthropic_resp = _to_anthropic_response(result, req_id, model_name)

        # 计费
        usage = result.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total = prompt_tokens + completion_tokens

        mid, _vendor = parse_model_key(model_name)
        model_obj = await Model.get_by_model_and_vendor(db, mid, _vendor) if _vendor else None
        cost = 0.0
        if total > 0 and model_obj and model_obj.model_type == "llm":
            cost = calc_llm_cost(model_obj, prompt_tokens, completion_tokens)
            if cost > 0:
                try:
                    await billing_service.deduct(
                        db, user.id, cost,
                        description=f"anthropic-messages {model_name} ({prompt_tokens}+{completion_tokens})",
                        request_log_id=req_id,
                    )
                except Exception as e:
                    logger.warning("anthropic deduct failed: {}", e)

        await billing_service.record_log(
            db,
            request_id=req_id, user_id=user.id, api_key_id=api_key.id,
            model=model_name, provider=provider_name, request_type="chat",
            status="success", status_code=200,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total, cost_usd=cost, latency_ms=latency_ms,
        )

        response = JSONResponse(content=anthropic_resp)
        response.headers["X-Gateway-Model"] = model_name
        response.headers["X-Gateway-Provider"] = provider_name
        response.headers["X-Gateway-Latency"] = f"{latency_ms}ms"
        response.headers["X-Gateway-Request-ID"] = req_id
        return response
