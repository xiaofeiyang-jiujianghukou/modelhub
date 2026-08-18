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

from src.db.models import User, ApiKey, Model
from src.config import settings
from src.middleware.billing import billing_service, calc_llm_cost
from src.services.router import router_service
from src.services.chat_tools import chat_tool, chat_tool_call_element, chat_tool_result
from src.services.model_key import parse_model_key
from src.services.cache_usage import extract_cache_usage

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
    - function_call: {type: function_call, name, arguments, call_id}
    - function_call_output: {type: function_call_output, call_id, output}
    """
    role: str = "user"
    type: Optional[str] = None  # message | additional_tools | function_call | function_call_output
    content: Optional[list[ResponseContentBlock] | str] = None
    tools: Optional[list[dict]] = None  # additional_tools 的工具定义
    name: Optional[str] = None          # function_call 工具名
    arguments: Optional[str] = None     # function_call 参数（JSON 字符串）
    call_id: Optional[str] = None       # function_call / function_call_output 的关联 id
    output: Optional[Any] = None        # function_call_output 的工具执行结果
    model_config = {"extra": "allow"}


class ResponsesRequest(BaseModel):
    model: str
    input: list[ResponseInputMessage] | str
    instructions: Optional[str] = None
    stream: Optional[bool] = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    tools: Optional[list[dict]] = None          # Responses 顶层 tools（Codex 工具调用依赖）
    tool_choice: Optional[Any] = None
    # 允许额外字段（reasoning、metadata 等），Codex 会发送，忽略即可
    model_config = {"extra": "allow"}


# ── 请求转换（Responses → 内部 OpenAI 格式）────────────────────────────────

def _responses_tools_to_chat(tools: Optional[list[dict]]) -> Optional[list[dict]]:
    """Responses 顶层 tools → chat tools（只保留 type=function 且有 name 的工具；namespace/web_search 等托管工具 DeepSeek 不支持，过滤）"""
    if not tools:
        return None
    result = []
    for t in tools:
        if t.get("type") != "function":
            continue  # namespace / web_search / 其他托管工具跳过
        if "function" in t:
            fn = t.get("function") or {}
            if fn.get("name"):          # 已 chat 格式且 name 有效才透传
                result.append(t)
        elif t.get("name"):
            result.append(chat_tool(t.get("name"), t.get("description", ""), t.get("parameters")))
    return result or None


def _to_openai_payload(req: ResponsesRequest) -> dict:
    """将 Responses API 请求转换为 chat/completions payload"""
    messages = []

    # instructions → system 消息（Codex 的系统提示）
    if req.instructions:
        messages.append({"role": "system", "content": req.instructions})

    # input → messages（含 function_call / function_call_output 多轮工具调用）
    if isinstance(req.input, str):
        messages.append({"role": "user", "content": req.input})
    else:
        items = req.input
        i = 0
        while i < len(items):
            item = items[i]
            itype = item.type
            # additional_tools 无 content → 跳过（工具定义走顶层 tools）
            if itype == "additional_tools":
                i += 1
                continue
            # 连续的 function_call → 合并成一个 assistant 消息（并行工具调用，DeepSeek 要求同一条 assistant）
            if itype == "function_call":
                calls = []
                while i < len(items) and items[i].type == "function_call":
                    fc = items[i]
                    calls.append(chat_tool_call_element(
                        fc.call_id or f"call_{uuid.uuid4().hex[:16]}",
                        fc.name or "",
                        fc.arguments or "{}",
                    ))
                    i += 1
                messages.append({"role": "assistant", "content": None, "tool_calls": calls})
                continue
            # function_call_output → tool 消息（工具执行结果）
            if itype == "function_call_output":
                out = item.output
                if isinstance(out, str):
                    out_text = out
                elif isinstance(out, (dict, list)):
                    out_text = json.dumps(out, ensure_ascii=False)
                else:
                    out_text = str(out) if out is not None else ""
                messages.append(chat_tool_result(item.call_id or "", out_text))
                i += 1
                continue
            # message / developer
            content = item.content
            if content is None:
                i += 1
                continue
            role = "system" if item.role == "developer" else item.role
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            else:
                text_parts = []
                for block in content:
                    if block.type == "input_text" and block.text:
                        text_parts.append(block.text)
                messages.append({"role": role, "content": "".join(text_parts)})
            i += 1

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
    # 工具定义透传（Codex 复杂任务依赖 function_call）
    chat_tools = _responses_tools_to_chat(req.tools)
    if chat_tools:
        payload["tools"] = chat_tools
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
    return payload


# ── 响应转换（OpenAI 格式 → Responses 格式）────────────────────────────────

def _chat_message_to_responses_output(message: dict) -> list[dict]:
    """chat message → Responses output items（text message + function_call items）"""
    output: list[dict] = []
    text = message.get("content") or ""
    if text:
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        output.append({
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:24]}",
            "call_id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", ""),
            "status": "completed",
        })
    return output


def _to_responses_response(result: dict, resp_id: str, model_name: str) -> dict:
    """将 chat/completions 响应转换为 Responses API 格式"""
    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    output = _chat_message_to_responses_output(message)

    usage = result.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model_name,
        "output": output or [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "", "annotations": []}],
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
    on_finish=None,
) -> AsyncGenerator[str, None]:
    """将 OpenAI chat 流式 SSE 转换为 Responses API 流式 SSE events（含 function_call）；
    on_finish(输入token, 输出token, usage_dict) 在流正常结束时回调（计费/记日志，usage 供缓存命中解析）"""

    async def wrapper():
        collected_text = ""
        input_tokens = 0
        output_tokens = 0
        usage_final: Optional[dict] = None
        text_item: Optional[dict] = None  # {output_index, item_id, content_part_id}
        tool_items: dict[int, dict] = {}  # openai_index -> {output_index, item_id, name, arguments}
        next_output_index = 0
        try:
            # response.created
            yield "data: " + json.dumps({
                "type": "response.created", "response": {"id": resp_id, "object": "response", "model": model_name, "status": "in_progress"},
            }) + "\n\n"

            # 逐 token 转发（文本 + 工具调用）
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

                    # 文本 delta
                    content = delta.get("content", "")
                    if content:
                        if text_item is None:
                            text_item = {
                                "output_index": next_output_index,
                                "item_id": f"msg_{uuid.uuid4().hex[:24]}",
                                "content_part_id": f"pc_{uuid.uuid4().hex[:16]}",
                            }
                            next_output_index += 1
                            yield "data: " + json.dumps({
                                "type": "response.output_item.added",
                                "output_index": text_item["output_index"],
                                "item": {"id": text_item["item_id"], "type": "message", "role": "assistant", "status": "in_progress", "content": []},
                            }) + "\n\n"
                            yield "data: " + json.dumps({
                                "type": "response.content_part.added",
                                "item_id": text_item["item_id"], "output_index": text_item["output_index"], "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            }) + "\n\n"
                        collected_text += content
                        yield "data: " + json.dumps({
                            "type": "response.output_text.delta",
                            "item_id": text_item["item_id"], "output_index": text_item["output_index"], "content_index": 0,
                            "delta": content,
                        }) + "\n\n"

                    # usage 收集（上游末尾 chunk 返回，含 stream_options.include_usage；保留完整 dict 供缓存命中解析）
                    if chunk.get("usage"):
                        usage_final = chunk["usage"]
                        input_tokens = usage_final.get("prompt_tokens", input_tokens)
                        output_tokens = usage_final.get("completion_tokens", output_tokens)

                    # 工具调用 delta（chat tool_calls → Responses function_call）
                    for tc in delta.get("tool_calls") or []:
                        oi = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if oi not in tool_items:
                            tool_items[oi] = {
                                "output_index": next_output_index,
                                "item_id": f"fc_{uuid.uuid4().hex[:24]}",
                                "call_id": tc.get("id", ""),   # 上游 tool_call id（reasoning_content 补回匹配用）
                                "name": fn.get("name", ""),
                                "arguments": "",
                            }
                            next_output_index += 1
                            yield "data: " + json.dumps({
                                "type": "response.output_item.added",
                                "output_index": tool_items[oi]["output_index"],
                                "item": {"id": tool_items[oi]["item_id"], "type": "function_call", "status": "in_progress",
                                         "call_id": tool_items[oi]["call_id"], "name": tool_items[oi]["name"], "arguments": ""},
                            }) + "\n\n"
                        args_delta = fn.get("arguments", "")
                        if args_delta:
                            tool_items[oi]["arguments"] += args_delta
                            yield "data: " + json.dumps({
                                "type": "response.function_call_arguments.delta",
                                "item_id": tool_items[oi]["item_id"], "output_index": tool_items[oi]["output_index"],
                                "delta": args_delta,
                            }) + "\n\n"

            # 收尾 events（done 事件携带完整文本/参数，Codex 依赖此字段）
            if text_item is not None:
                for evt in [
                    {"type": "response.output_text.done", "item_id": text_item["item_id"], "output_index": text_item["output_index"], "content_index": 0, "text": collected_text},
                    {"type": "response.content_part.done", "item_id": text_item["item_id"], "output_index": text_item["output_index"], "content_index": 0, "part": {"type": "output_text", "text": collected_text, "annotations": []}},
                    {"type": "response.output_item.done", "output_index": text_item["output_index"], "item": {"id": text_item["item_id"], "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": collected_text, "annotations": []}]}},
                ]:
                    yield "data: " + json.dumps(evt) + "\n\n"

            for tool in tool_items.values():
                yield "data: " + json.dumps({
                    "type": "response.output_item.done",
                    "output_index": tool["output_index"],
                    "item": {"id": tool["item_id"], "type": "function_call", "status": "completed",
                             "call_id": tool.get("call_id", ""), "name": tool["name"], "arguments": tool["arguments"]},
                }) + "\n\n"

            yield "data: " + json.dumps({
                "type": "response.completed", "response": {"id": resp_id, "object": "response", "model": model_name, "status": "completed"},
            }) + "\n\n"
            yield "data: [DONE]\n\n"

            # 流正常结束 → 计费 + 记日志
            if on_finish:
                try:
                    await on_finish(input_tokens, output_tokens, usage_final)
                except Exception as e:
                    logger.error(f"responses stream on_finish failed: {e}")
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
        try:
            model_obj, actual_model_name = await Model.resolve_or_default(_db, req.model)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": f"Model '{req.model}' must be vendor/model format", "type": "invalid_request_error", "code": "invalid_model"}},
            )
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
            async def _on_stream_finish(pt: int, ct: int, usage: Optional[dict] = None):
                """流结束时：独立会话补扣费 + 记日志（原始会话已随请求返回关闭）"""
                async with AsyncSessionLocal() as _db:
                    total = pt + ct
                    cache_hit, cache_miss = extract_cache_usage(usage)
                    mid, _vendor = parse_model_key(model_name)
                    model_obj = await Model.get_by_model_and_vendor(_db, mid, _vendor) if _vendor else None
                    cost = 0.0
                    if total > 0 and model_obj and model_obj.model_type == "llm":
                        cost = calc_llm_cost(model_obj, pt, ct)
                        if cost > 0:
                            try:
                                await billing_service.deduct(
                                    _db, user.id, cost,
                                    description=f"responses {model_name} ({pt}+{ct})",
                                    request_log_id=resp_id,
                                )
                            except Exception as e:
                                logger.warning("responses stream deduct failed: {}", e)
                    await billing_service.record_log(
                        _db,
                        request_id=resp_id, user_id=user.id, api_key_id=api_key.id,
                        model=model_name, provider=provider_name, request_type="chat",
                        status="success", status_code=200,
                        prompt_tokens=pt, completion_tokens=ct,
                        total_tokens=total, cache_hit_tokens=cache_hit,
                        cache_miss_tokens=cache_miss, cost_usd=cost,
                        latency_ms=int((time.time() - start_time) * 1000),
                    )

            logger.info("responses stream start provider={} gen_type={}", provider_name, type(result))
            response = StreamingResponse(
                _responses_stream_events(result, resp_id, model_name, _on_stream_finish),
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
        cache_hit, cache_miss = extract_cache_usage(usage)

        mid, _vendor = parse_model_key(model_name)
        model_obj = await Model.get_by_model_and_vendor(db, mid, _vendor) if _vendor else None
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
            total_tokens=total, cache_hit_tokens=cache_hit, cache_miss_tokens=cache_miss,
            cost_usd=cost, latency_ms=latency_ms,
        )

        response = JSONResponse(content=responses_resp)
        response.headers["X-Gateway-Model"] = model_name
        response.headers["X-Gateway-Provider"] = provider_name
        response.headers["X-Gateway-Latency"] = f"{latency_ms}ms"
        response.headers["X-Gateway-Request-ID"] = resp_id
        return response
