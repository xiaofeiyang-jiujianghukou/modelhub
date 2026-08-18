"""
POST /v1/chat/completions 文本对话接口
兼容 OpenAI Chat Completions API，支持流式和非流式
通过 RouterService 实现多通道路由与故障转移
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.db.models import User, ApiKey, Model
from src.middleware.billing import billing_service, calc_llm_cost
from src.services.router import router_service
from src.services.model_key import is_reasoning_model
from src.services.cache_usage import extract_cache_usage

router = APIRouter(tags=["Chat"])


# ── 请求模型 ────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    # assistant 带 tool_calls 时 content 为 null；多模态时为 content blocks 数组
    content: Optional[str | list[dict]] = None
    name: Optional[str] = None                   # 函数/工具名（部分客户端发送）
    tool_calls: Optional[list[dict]] = None      # assistant 发起的工具调用
    tool_call_id: Optional[str] = None           # role="tool" 的结果对应哪次调用


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    stream: Optional[bool] = Field(default=False)
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    stop: Optional[str | list[str]] = Field(default=None)
    presence_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    tools: Optional[list[dict]] = Field(default=None)   # 工具定义（Cherry Studio / OpenAI SDK 依赖）
    tool_choice: Optional[Any] = Field(default=None)    # auto | none | required | {"type":"function",...}


# ── 响应模型 ────────────────────────────────────────────────────────────────

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    index: int
    message: Optional[ChatMessage] = None
    delta: Optional[ChatMessage] = None
    finish_reason: Optional[str] = None


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Optional[Usage] = None


def _build_payload(request: ChatRequest) -> dict:
    """将请求转换为 OpenAI 格式 payload"""
    payload = {
        "model": request.model,
        # exclude_none：不把未设置的 tool_calls/tool_call_id/name 以 null 形式发给上游
        "messages": [m.model_dump(exclude_none=True) for m in request.messages],
        "stream": request.stream,
    }
    # 可选参数
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        mt = request.max_tokens
        # reasoning 模型（glm-5/deepseek/doubao-seed 等）思考与正文共享 max_tokens 预算，
        # 测试对话传的小值（如 516）会被思考耗尽 → 正文为空。强制抬到 4096 保证"该思考思考、该输出输出"
        if is_reasoning_model(request.model):
            mt = max(mt, 4096)
        payload["max_tokens"] = mt
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.stop is not None:
        payload["stop"] = request.stop
    if request.presence_penalty is not None:
        payload["presence_penalty"] = request.presence_penalty
    if request.frequency_penalty is not None:
        payload["frequency_penalty"] = request.frequency_penalty
    # 工具定义透传（tools 排序稳定化在 router 层统一做，见 prefix_cache.stabilize_payload）
    if request.tools:
        payload["tools"] = request.tools
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
    return payload


def _build_error(status_code: int, message: str, error_type: str, code: str):
    """构建标准 OpenAI 错误响应"""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
                "param": None,
            }
        },
    )


# ── 流式 SSE 生成器 ───────────────────────────────────────────────────────────

async def _billing_after_stream(
    db,
    req_id: str,
    user: User,
    api_key: ApiKey,
    model: Model,
    model_name: str,
    provider_name: str,
    usage: Optional[dict],
    latency_ms: int,
    stream_error: bool,
):
    """
    流结束后执行计费与日志（独立 session，不阻塞响应）
    优先使用上游返回的 usage；无 usage 时按 prompt 估算
    """
    try:
        prompt_tokens = (usage or {}).get("prompt_tokens", 0)
        completion_tokens = (usage or {}).get("completion_tokens", 0)
        total_tokens = (usage or {}).get("total_tokens", prompt_tokens + completion_tokens)
        cache_hit, cache_miss = extract_cache_usage(usage)

        # 无 usage 时估算：按输入字符数粗略估算 prompt tokens
        estimated = False
        if not usage and model.model_type == "llm":
            estimated = True
            prompt_tokens = 0

        cost = 0.0
        if total_tokens > 0 and model.model_type == "llm":
            cost = calc_llm_cost(model, prompt_tokens, completion_tokens)

        status = "error" if stream_error else "success"
        status_code = 500 if stream_error else 200

        if cost > 0:
            try:
                await billing_service.deduct(
                    db, user.id, cost,
                    description=f"chat(stream) {model_name} ({prompt_tokens}+{completion_tokens} tokens)",
                    request_log_id=req_id,
                )
            except Exception as e:
                logger.warning("stream deduct failed user={} cost={}: {}", user.id, cost, e)

        await billing_service.record_log(
            db,
            request_id=req_id,
            user_id=user.id,
            api_key_id=api_key.id,
            model=model_name,
            provider=provider_name,
            request_type="chat",
            status=status,
            status_code=status_code,
            prompt_tokens=prompt_tokens if not estimated else None,
            completion_tokens=completion_tokens if not estimated else None,
            total_tokens=total_tokens if not estimated else None,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            cost_usd=cost,
            latency_ms=latency_ms,
            error_code=None if not stream_error else "upstream_error",
        )
    except Exception as e:
        logger.error("billing after stream failed: {}", e)


async def _stream_chat(
    gen: AsyncGenerator[str, None],
    req_id: str,
    model_name: str,
    provider_name: str,
    user: User,
    api_key: ApiKey,
    model: Model,
) -> AsyncGenerator[str, None]:
    """将上游 SSE 流透传给客户端，同时收集 usage 用于流后计费"""
    from src.database import AsyncSessionLocal

    start_time = time.time()
    usage: Optional[dict] = None
    stream_error = False

    try:
        async for line in gen:
            # 解析 SSE 行，提取 usage（OpenAI 兼容流式末尾 chunk 携带）
            if line.startswith("data: ") and line.strip() != "data: [DONE]":
                try:
                    chunk = json.loads(line[6:].strip())
                    if isinstance(chunk, dict) and chunk.get("usage"):
                        usage = chunk["usage"]
                except Exception:
                    pass
            yield line
        yield "data: [DONE]\n\n"
    except Exception as e:
        stream_error = True
        logger.error(f"stream error: {e}")
        error_data = {
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": "upstream_error",
            }
        }
        yield f"data: {json.dumps(error_data)}\n\n"
    finally:
        # 流结束后（含中断）执行计费与日志，独立 session
        latency_ms = int((time.time() - start_time) * 1000)
        async with AsyncSessionLocal() as db:
            await _billing_after_stream(
                db, req_id, user, api_key, model, model_name,
                provider_name, usage, latency_ms, stream_error,
            )


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.post("/chat/completions")
async def chat_completions(
    request: ChatRequest,
    http_req: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    文本对话接口，完全兼容 OpenAI Chat Completions API

    支持:
    - 流式输出 (stream: true)
    - 多轮对话
    - 多通道路由 + 故障自动转移
    - 常用生成参数 (temperature, max_tokens, top_p, stop, penalties)
    """
    # 从 request.state 获取认证信息（由 auth 中间件设置）
    user: User = getattr(http_req.state, "user", None)
    api_key: ApiKey = getattr(http_req.state, "api_key", None)

    if not user or not api_key:
        return _build_error(401, "Invalid authentication", "authentication_error", "invalid_api_key")

    # 余额预检（不足返回 402）
    try:
        await billing_service.precheck_balance(db, user.id)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content=e.detail)

    # 查询模型信息（解析别名 + 官方模型名兜底）
    try:
        model, actual_model_name = await Model.resolve_or_default(db, request.model)
    except ValueError as e:
        return _build_error(400, str(e), "invalid_request_error", "invalid_model")
    if not model or not model.is_active:
        return _build_error(
            400,
            f"Model '{request.model}' does not exist or is not available",
            "invalid_request_error",
            "invalid_model",
        )

    # 生成请求 ID
    req_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    # 构建 payload（model 使用映射后的实际模型名）
    payload = _build_payload(request)
    payload["model"] = actual_model_name

    # 记录请求开始时间
    start_time = time.time()

    # ── 通过路由引擎转发（含故障转移）──
    result, channel, provider, error_info = await router_service.route_chat(
        db, actual_model_name, payload, stream=bool(request.stream)
    )

    if result is None:
        # 透传上游真实错误（如"余额不足"、"模型未开通"），而非笼统 503
        if error_info:
            err_status = error_info.get("status_code", 502)
            if err_status >= 500:
                err_status = 502  # 服务端错误统一 502
            return _build_error(
                err_status,
                f"[{error_info.get('provider', 'upstream')}] {error_info.get('message', 'upstream error')}",
                "upstream_error",
                "upstream_error",
            )
        return _build_error(
            503,
            "No available provider for model",
            "api_error",
            "no_available_provider",
        )

    latency_ms = int((time.time() - start_time) * 1000)
    provider_name = provider.name if provider else "unknown"

    # ── 流式响应（流结束后由生成器执行计费与日志）──
    if request.stream:
        response = StreamingResponse(
            _stream_chat(result, req_id, request.model, provider_name, user, api_key, model),
            media_type="text/event-stream",
        )
    else:
        # ── 非流式响应：完整计费链路 ──
        response = JSONResponse(content=result)

        # 从响应中提取 usage 计算费用
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        cache_hit, cache_miss = extract_cache_usage(usage)

        if total_tokens > 0 and model.model_type == "llm":
            cost = calc_llm_cost(model, prompt_tokens, completion_tokens)
            if cost > 0:
                try:
                    # 原子扣费 + 交易记录
                    await billing_service.deduct(
                        db, user.id, cost,
                        description=f"chat {request.model} ({prompt_tokens}+{completion_tokens} tokens)",
                        request_log_id=req_id,
                    )
                except HTTPException as e:
                    logger.warning("deduct failed user={} cost={}", user.id, cost)

            # 请求日志（异步写入）
            await billing_service.record_log(
                db,
                request_id=req_id,
                user_id=user.id,
                api_key_id=api_key.id,
                model=request.model,
                provider=provider_name,
                request_type="chat",
                status="success",
                status_code=200,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cache_hit_tokens=cache_hit,
                cache_miss_tokens=cache_miss,
                cost_usd=cost,
                latency_ms=latency_ms,
            )

    # 附加路由调试元数据（PRD 4.4）
    response.headers["X-Gateway-Model"] = request.model
    response.headers["X-Gateway-Provider"] = provider_name
    response.headers["X-Gateway-Latency"] = f"{latency_ms}ms"
    response.headers["X-Gateway-Request-ID"] = req_id

    return response
