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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import User, ApiKey, ModelCatalog
from src.config import settings
from src.middleware.billing import billing_service, calc_llm_cost
from src.services.router import router_service

router = APIRouter(tags=["Anthropic"])


# ── 请求/响应模型（Anthropic 格式）──────────────────────────────────────────

class AnthropicMessage(BaseModel):
    role: str  # user | assistant
    content: str | list[dict] = Field(..., description="字符串或 content block 列表")


class AnthropicRequest(BaseModel):
    model: str
    max_tokens: int = Field(..., ge=1)
    messages: list[AnthropicMessage]
    system: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    top_p: Optional[float] = Field(default=None)
    stream: Optional[bool] = False
    stop_sequences: Optional[list[str]] = None
    metadata: Optional[dict] = None


# ── 模型映射 ────────────────────────────────────────────────────────────────

def _resolve_model(model_name: str) -> str:
    """
    模型名解析：
    1. 网关中已注册的模型 ID → 直接使用
    2. claude-* 前缀（Claude 生态默认请求）→ 映射到网关默认模型
    """
    if model_name.startswith("claude-"):
        default = getattr(settings, "default_claude_model", None) or "doubao-seed-2-0-pro"
        logger.info("claude model {} mapped to {}", model_name, default)
        return default
    return model_name


# ── 请求转换（Anthropic → 内部 OpenAI 格式）─────────────────────────────────

def _to_openai_payload(req: AnthropicRequest, model_name: str) -> dict:
    """将 Anthropic 请求转换为 OpenAI 格式 payload"""
    messages = []
    # system 提示词作为第一条 system 消息
    if req.system:
        messages.append({"role": "system", "content": req.system})

    for m in req.messages:
        content = m.content
        # content block 列表 → 提取纯文本（简化：拼接 text 块）
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        # 图片块暂不支持，跳过
                        continue
                else:
                    text_parts.append(str(block))
            content = "".join(text_parts)
        messages.append({"role": m.role, "content": content})

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": bool(req.stream),
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.top_p is not None:
        payload["top_p"] = req.top_p
    if req.stop_sequences:
        payload["stop"] = req.stop_sequences
    return payload


# ── 响应转换（OpenAI 格式 → Anthropic 格式）─────────────────────────────────

def _to_anthropic_response(
    result: dict,
    req_id: str,
    model_name: str,
) -> dict:
    """将 OpenAI 格式响应转换为 Anthropic Messages 格式"""
    choices = result.get("choices", [])
    text = ""
    if choices:
        # 仅取 content，丢弃 reasoning_content（思考过程不进入对话输出，节省上下文）
        text = choices[0].get("message", {}).get("content", "") or ""

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
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason_map.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── 流式转换（OpenAI SSE → Anthropic SSE events）────────────────────────────

def _anthropic_stream_events(
    gen: AsyncGenerator[str, None],
    req_id: str,
    model_name: str,
) -> AsyncGenerator[str, None]:
    """将 OpenAI 流式 SSE 转换为 Anthropic 流式 SSE events"""
    input_tokens = 0
    output_tokens = 0

    async def wrapper():
        nonlocal input_tokens, output_tokens
        try:
            # message_start
            start_msg = {
                "type": "message_start",
                "message": {
                    "id": req_id, "type": "message", "role": "assistant",
                    "model": model_name, "content": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
            yield f"event: message_start\ndata: {json.dumps(start_msg)}\n\n"

            # content_block_start
            block_start = {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
            yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"

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
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        delta_evt = {
                            "type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": content},
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(delta_evt)}\n\n"
                    # usage 收集（部分上游在末尾 chunk 返回）
                    if chunk.get("usage"):
                        input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                        output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

            # content_block_stop
            block_stop = {"type": "content_block_stop", "index": 0}
            yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"
            # message_delta
            delta_msg = {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            }
            yield f"event: message_delta\ndata: {json.dumps(delta_msg)}\n\n"
            # message_stop
            yield "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"
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


@router.get("/models")
async def anthropic_models(http_req: Request):
    """Anthropic 模型列表端点（部分客户端启动时调用）"""
    return {"data": [
        {"type": "model", "id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5 (→ Gateway Default)", "created_at": "2026-01-01T00:00:00Z"},
        {"type": "model", "id": "doubao-seed-2-0-pro", "display_name": "Doubao Seed 2.0 Pro", "created_at": "2026-01-01T00:00:00Z"},
        {"type": "model", "id": "doubao-1-5-pro", "display_name": "Doubao 1.5 Pro", "created_at": "2026-01-01T00:00:00Z"},
        {"type": "model", "id": "deepseek-chat", "display_name": "DeepSeek Chat", "created_at": "2026-01-01T00:00:00Z"},
        {"type": "model", "id": "glm-4-flash", "display_name": "GLM-4 Flash", "created_at": "2026-01-01T00:00:00Z"},
    ]}


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
            response = StreamingResponse(
                _anthropic_stream_events(result, req_id, model_name),
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

        model_obj = await ModelCatalog.get_by_id_or_alias(db, model_name)
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
