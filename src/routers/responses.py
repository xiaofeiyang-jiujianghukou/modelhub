"""
OpenAI Responses API 协议兼容层
便于 Codex CLI / OpenAI 生态通过 OPENAI_BASE_URL 接入本网关

端点: POST /v1/responses
认证: Authorization: Bearer sk-xxx（网关 API Key）
协议: 与 OpenAI Responses API 兼容（含流式 SSE）

Codex CLI 配置示例:
  export OPENAI_BASE_URL=http://localhost:8000/v1
  export OPENAI_API_KEY=<网关 API Key>
  export CODEX_MODEL=doubao-seed-2-0-pro
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.models import User, ApiKey, ModelCatalog
from src.config import settings
from src.middleware.billing import billing_service, calc_llm_cost
from src.services.router import router_service

router = APIRouter(tags=["Responses"])


# ── 请求模型（Responses API 格式）──────────────────────────────────────────

class ResponseContentBlock(BaseModel):
    type: str  # input_text | output_text | input_image
    text: Optional[str] = None


class ResponseInputMessage(BaseModel):
    """
    Responses API 输入消息
    兼容多种类型：
    - 普通消息: {type: message, role: user, content: [...]}
    - developer 指令: {type: message, role: developer, content: [...]}
    - additional_tools: {type: additional_tools, role: developer, tools: [...]}（无 content）
    """
    role: str = "user"
    type: Optional[str] = None  # message | additional_tools | function_call 等
    content: Optional[list[ResponseContentBlock] | str] = None
    tools: Optional[list[dict]] = None  # additional_tools 的工具定义


class ResponsesRequest(BaseModel):
    model: str
    input: list[ResponseInputMessage] | str
    instructions: Optional[str] = None
    stream: Optional[bool] = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    # 允许额外字段（tool_choice、reasoning、metadata 等），Codex 会发送，忽略即可
    model_config = {"extra": "allow"}


# ── 请求转换（Responses → 内部 OpenAI 格式）────────────────────────────────

def _to_openai_payload(req: ResponsesRequest) -> dict:
    """将 Responses API 请求转换为 chat/completions payload"""
    messages = []

    # instructions → system 消息（Codex 的系统提示）
    if req.instructions:
        messages.append({"role": "system", "content": req.instructions})

    # input → messages
    if isinstance(req.input, str):
        messages.append({"role": "user", "content": req.input})
    else:
        for item in req.input:
            # additional_tools / function_call 等无 content 的特殊类型 → 跳过
            if item.type and item.type != "message":
                continue
            content = item.content
            if content is None:
                continue
            # developer 角色 → system（OpenAI chat 无 developer 角色）
            role = "system" if item.role == "developer" else item.role
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            else:
                text_parts = []
                for block in content:
                    if block.type == "input_text" and block.text:
                        text_parts.append(block.text)
                messages.append({"role": role, "content": "".join(text_parts)})

    payload: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "stream": bool(req.stream),
    }
    if req.max_output_tokens is not None:
        payload["max_tokens"] = req.max_output_tokens
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.top_p is not None:
        payload["top_p"] = req.top_p
    return payload


# ── 响应转换（OpenAI 格式 → Responses 格式）────────────────────────────────

def _to_responses_response(result: dict, resp_id: str, model_name: str) -> dict:
    """将 chat/completions 响应转换为 Responses API 格式"""
    choices = result.get("choices", [])
    text = ""
    if choices:
        text = choices[0].get("message", {}).get("content", "") or ""

    usage = result.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model_name,
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": []}
                ],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


# ── 流式转换（chat SSE → Responses SSE events）─────────────────────────────

def _responses_stream_events(
    gen: AsyncGenerator[str, None],
    resp_id: str,
    model_name: str,
) -> AsyncGenerator[str, None]:
    """将 OpenAI chat 流式 SSE 转换为 Responses API 流式 SSE events"""
    output_item_id = f"msg_{uuid.uuid4().hex[:24]}"
    content_part_id = f"pc_{uuid.uuid4().hex[:16]}"

    async def wrapper():
        collected_text = ""
        try:
            # response.created
            yield "data: " + json.dumps({
                "type": "response.created", "response": {"id": resp_id, "object": "response", "model": model_name, "status": "in_progress"},
            }) + "\n\n"

            # response.output_item.added
            yield "data: " + json.dumps({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": output_item_id, "type": "message", "role": "assistant", "status": "in_progress", "content": []},
            }) + "\n\n"

            # response.content_part.added
            yield "data: " + json.dumps({
                "type": "response.content_part.added",
                "item_id": output_item_id, "output_index": 0, "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }) + "\n\n"

            # 逐 token 转发
            async for line in gen:
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue
                    # 上游错误（429 余额不足等）→ 输出 Codex 可识别的错误终态
                    # Codex 源码解析 "response.failed" 事件提取错误；error.code 决定是否重试
                    if "error" in chunk:
                        err_msg = chunk["error"].get("message", "Upstream error")
                        # 按上游状态码映射 Codex 认识的错误码（防止被误判为可重试而反复尝试）
                        sc = chunk.get("_status_code", 500)
                        if sc == 429:
                            err_code = "insufficient_quota"      # 余额/配额不足 → 不重试
                        elif sc in (401, 403):
                            err_code = "invalid_api_key"         # 认证失败 → 不重试
                        elif sc == 404:
                            err_code = "model_not_found"         # 模型不存在 → 不重试
                        else:
                            err_code = "server_error"            # 服务端错误 → 可重试
                        # 1. error 事件（OpenAI 规范：code/message/param 在顶层）
                        yield "data: " + json.dumps({
                            "type": "error",
                            "code": err_code,
                            "message": err_msg,
                            "param": None,
                        }) + "\n\n"
                        # 2. response.failed 终态事件（Codex 唯一识别的失败终态）
                        failed = {
                            "type": "response.failed",
                            "response": {
                                "id": resp_id,
                                "object": "response",
                                "created_at": int(time.time()),
                                "status": "failed",
                                "model": model_name,
                                "error": {"code": err_code, "message": err_msg},
                                "output": [],
                                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                            },
                        }
                        yield "data: " + json.dumps(failed) + "\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        collected_text += content
                        yield "data: " + json.dumps({
                            "type": "response.output_text.delta",
                            "item_id": output_item_id, "output_index": 0, "content_index": 0,
                            "delta": content,
                        }) + "\n\n"

            # 收尾 events（done 事件携带完整文本，Codex 依赖此字段）
            for evt in [
                {"type": "response.output_text.done", "item_id": output_item_id, "output_index": 0, "content_index": 0, "text": collected_text},
                {"type": "response.content_part.done", "item_id": output_item_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": collected_text, "annotations": []}},
                {"type": "response.output_item.done", "output_index": 0, "item": {"id": output_item_id, "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": collected_text, "annotations": []}]}},
                {"type": "response.completed", "response": {"id": resp_id, "object": "response", "model": model_name, "status": "completed"}},
            ]:
                yield "data: " + json.dumps(evt) + "\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"responses stream error: {e}")
            yield "data: " + json.dumps({"type": "error", "error": {"message": str(e)}}) + "\n\n"

    return wrapper()


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.post("/responses")
async def responses_endpoint(
    req: ResponsesRequest,
    http_req: Request,
):
    """
    OpenAI Responses API 兼容端点（Codex 接入）
    认证: Authorization: Bearer sk-xxx
    """
    from src.database import AsyncSessionLocal

    user: User = getattr(http_req.state, "user", None)
    api_key: ApiKey = getattr(http_req.state, "api_key", None)
    if not user or not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    # 模型解析（含 Codex 官方模型名兜底映射）
    async with AsyncSessionLocal() as _db:
        model_obj, actual_model_name = await ModelCatalog.resolve_or_default(_db, req.model)
    if not model_obj:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": f"Model '{req.model}' does not exist", "type": "invalid_request_error", "code": "invalid_model"}},
        )
    model_name = actual_model_name

    resp_id = f"resp_{uuid.uuid4().hex[:24]}"
    payload = _to_openai_payload(req)
    payload["model"] = model_name

    async with AsyncSessionLocal() as db:
        # 余额预检
        try:
            await billing_service.precheck_balance(db, user.id)
        except Exception as e:
            return JSONResponse(
                status_code=getattr(e, "status_code", 500),
                content={"error": {"message": str(getattr(e, "detail", e)), "type": "billing_error", "code": "insufficient_balance"}},
            )

        start_time = time.time()
        result, channel, provider, error_info = await router_service.route_chat(
            db, model_name, payload, stream=bool(req.stream)
        )

        if result is None:
            # 透传上游真实错误（Codex 会显示详细原因）
            if error_info:
                err_status = error_info.get("status_code", 502)
                if err_status >= 500:
                    err_status = 502
                return JSONResponse(
                    status_code=err_status,
                    content={"error": {"message": f"[{error_info.get('provider', 'upstream')}] {error_info.get('message', 'upstream error')}", "type": "upstream_error", "code": "upstream_error"}},
                )
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "No available provider for model", "type": "api_error", "code": "no_available_provider"}},
            )

        provider_name = provider.name if provider else "unknown"
        latency_ms = int((time.time() - start_time) * 1000)

        # ── 流式 ──
        if req.stream:
            logger.info("responses stream start provider={} gen_type={}", provider_name, type(result))
            response = StreamingResponse(
                _responses_stream_events(result, resp_id, model_name),
                media_type="text/event-stream",
            )
            response.headers["X-Gateway-Model"] = model_name
            response.headers["X-Gateway-Provider"] = provider_name
            response.headers["X-Gateway-Request-ID"] = resp_id
            return response

        # ── 非流式：转换 + 计费 ──
        responses_resp = _to_responses_response(result, resp_id, model_name)

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
                        description=f"responses {model_name} ({prompt_tokens}+{completion_tokens})",
                        request_log_id=resp_id,
                    )
                except Exception as e:
                    logger.warning("responses deduct failed: {}", e)

        await billing_service.record_log(
            db,
            request_id=resp_id, user_id=user.id, api_key_id=api_key.id,
            model=model_name, provider=provider_name, request_type="chat",
            status="success", status_code=200,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total, cost_usd=cost, latency_ms=latency_ms,
        )

        response = JSONResponse(content=responses_resp)
        response.headers["X-Gateway-Model"] = model_name
        response.headers["X-Gateway-Provider"] = provider_name
        response.headers["X-Gateway-Latency"] = f"{latency_ms}ms"
        response.headers["X-Gateway-Request-ID"] = resp_id
        return response
