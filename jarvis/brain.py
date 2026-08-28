"""贾维斯的大脑：OpenAI 兼容的中转站 + 工具调用 + 长期记忆 + MCP 工具。

通过你自己的中转站（OpenAI 兼容网关，/v1/chat/completions）调用任意大模型，
比如 DeepSeek、GPT、Claude 等——只要中转站支持「函数调用(tools)」。
负责把用户说的话理解成意图、必要时调用工具(本地工具/MCP工具)、处理多步任务，
最后给出一句简短的口语回复。

配置见 config.py：base_url.txt（中转站地址）/ api_key.txt（key）/ model.txt（模型名）。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from . import config, memory, tools

SYSTEM_PROMPT = """你是「贾维斯」(Jarvis)，用户电脑上的中文语音助手，风格像电影里钢铁侠的 AI 管家：\
干练、礼貌、略带幽默。

重要规则：
1. 你的回复会被语音朗读出来，所以必须简短、口语化，一般一两句话即可，不要列清单、不要用 markdown、不要念网址。用户泛问“能做什么”时，只举三个贴近当前电脑的例子，并问他想先做哪一个；不要罗列全部能力。
2. 用户的话来自语音识别，可能有错别字或同音字，请结合上下文理解真实意图。
3. 能用工具完成的事就调用工具，别只是空谈。
4. 不确定时可以追问一句，但尽量主动把事办了。
5. 始终用简体中文回答。
6. 发微信(send_wechat)前，必须先口头复述"要发给谁、内容是什么"并等用户确认后，才在下一轮真正调用工具发送。
7. 用户问屏幕上的内容、让你总结当前页面/文章时，调用 read_screen 看屏幕再回答。
8. 记忆：身份、硬性偏好和长期规则存为 core；特定项目事实存为 project；其他长期信息存为 long_term。修改、导出或清空记忆必须等待用户下一轮确认；下面"已经记住的事"要自然运用。
9. 多步任务：遇到"整理文件夹""批量重命名"等需要好几步的活儿，先用 list_directory 看现状，再分步执行；删除一律用 move_to_trash。办完用一句话汇报结果。
10. 高风险工具返回“尚未执行”时，立即停止调用工具，说明将执行什么并等待用户下一轮明确确认；不得在同一轮重试或代替用户确认。
11. 创建或修改文件必须调用 propose_file_change 生成待审提案，不得直接写入；需要读取文件时调用 read_text_file 并等待用户确认。
12. 浏览器上传、点击、提交、付款、接受对话框或执行脚本返回“尚未执行”时，必须说明目标、文件或动作并等待下一轮确认。"""


def _os_hint() -> str:
    """告诉大脑当前操作系统，好让 run_shell 用对命令语法。"""
    shell = "PowerShell" if config.IS_WINDOWS else "bash/zsh"
    return (f"\n\n[运行环境] 你现在运行在 {config.OS_NAME} 上；"
            f"run_shell 执行的是 {shell} 命令，请按该系统的命令语法来写命令。")


def _to_openai_tool(t: dict) -> dict:
    """把 Anthropic 风格的工具 schema 转成 OpenAI 的 function 格式。"""
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {
                "type": "object", "properties": {}},
        },
    }


# 句子结束标点：流式时凑齐一句就立刻送去合成朗读
_SENT_END = "。！？!?；;…\n"


def _network_error(error: Exception) -> str:
    """把 urllib/接口错误转换成可操作且适合直接展示的中文信息。"""
    if isinstance(error, urllib.error.HTTPError):
        hints = {401: "API Key 无效或已过期", 403: "当前 Key 没有访问权限",
                 404: "接口地址或模型名称不正确", 429: "请求过于频繁或额度不足"}
        hint = hints.get(error.code, "模型服务暂时不可用" if error.code >= 500
                         else "模型服务拒绝了请求")
        return f"模型接口返回 {error.code}：{hint}"
    if isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout)):
        return "无法连接模型服务，请检查接口地址、网络或本地模型是否已启动"
    if isinstance(error, (json.JSONDecodeError, KeyError, IndexError, TypeError)):
        return "模型返回格式不兼容，请确认该接口支持 OpenAI Chat Completions"
    return f"模型调用失败：{error}"


def _open_request(req, timeout: float = 120):  # noqa: ANN001
    """瞬时连接错误只重试一次；HTTP 业务错误原样返回。"""
    for attempt in range(2):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt:
                raise
            time.sleep(0.5)
    raise AssertionError("unreachable")


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """从缓冲里切出已完成的句子，返回(完整句列表, 剩余未完成串)。"""
    out, start = [], 0
    for i, ch in enumerate(buf):
        if ch in _SENT_END:
            seg = buf[start:i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
    return out, buf[start:]


class Brain:
    def __init__(self, api_key: str, mcp=None) -> None:
        self._api_key = api_key
        self._mcp = mcp
        self._messages: list[dict] = []
        self._pending: tuple[str, dict] | None = None
        # 本地工具 + MCP 工具，统一转成 OpenAI function 格式
        anthropic_tools = tools.tool_schemas()
        if mcp:
            anthropic_tools += mcp.tool_schemas()
        self._tools = [_to_openai_tool(t) for t in anthropic_tools]
        self._system_base = SYSTEM_PROMPT + _os_hint()

    def _system_prompt(self) -> str:
        """每次请求读取最新记忆，避免增删后必须重启。"""
        categories, _ = config.cloud_memory_policy()
        return self._system_base + memory.as_prompt(categories)

    def reset(self) -> None:
        self._messages = []
        self._pending = None

    def _start_turn(self, user_text: str) -> None:
        """追加用户消息，并按完整用户轮次限制历史长度。"""
        starts = [i for i, message in enumerate(self._messages)
                  if message.get("role") == "user"]
        keep_previous = config.HISTORY_TURNS - 1
        if len(starts) > keep_previous:
            cut = starts[-keep_previous] if keep_previous else len(self._messages)
            self._messages = self._messages[cut:]
        self._messages.append({"role": "user", "content": user_text})

    def _dispatch(self, name: str, args: dict, *, confirmed: bool = False) -> str:
        if self._mcp and name.startswith("mcp__"):
            if (not self._mcp.validation_error(name, args)
                    and self._mcp.requires_confirmation(name, args)
                    and not confirmed):
                self._pending = (name, args)
            out = self._mcp.call(name, args, confirmed=confirmed)
        else:
            if tools.requires_confirmation(name) and not confirmed:
                self._pending = (name, args)
            out = tools.run(name, args, confirmed=confirmed)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    def _resolve_pending(self, user_text: str) -> str | None:
        if not self._pending:
            return None
        normalized = "".join(user_text.lower().split()).strip("，。！？,.!?")
        name, args = self._pending
        self._pending = None
        if normalized in {"确认", "确认执行", "同意", "同意执行"}:
            result = self._dispatch(name, args, confirmed=True)
            self._start_turn(user_text)
            self._messages.append({"role": "assistant", "content": result})
            return result
        tools.audit(name, args, "cancelled",
                    risk="high" if name.startswith("mcp__") else None)
        if normalized in {"取消", "不要执行", "停止"}:
            return "已取消这个操作。"
        return None

    def _request_body(self, messages: list[dict], *, stream: bool) -> dict:
        body = {
            "model": config.MODEL,
            "messages": [{"role": "system", "content": self._system_prompt()}] + messages,
            "tools": self._tools,
            "tool_choice": "auto",
            "max_tokens": config.MAX_TOKENS,
            "stream": stream,
        }
        if config.is_local_model():
            body["reasoning_effort"] = "none"
        elif config.is_deepseek_api():
            body["thinking"] = {"type": "disabled"}
        return body

    def _chat(self, messages: list[dict]) -> dict:
        """调一次中转站 /chat/completions，返回 choices[0].message。"""
        body = self._request_body(messages, stream=False)
        req = urllib.request.Request(
            config.llm_endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with _open_request(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(_network_error(e)) from None

    def ask(self, user_text: str) -> str:
        """处理一句用户输入，返回要朗读的回复文本。"""
        resolved = self._resolve_pending(user_text)
        if resolved is not None:
            return resolved
        self._start_turn(user_text)

        # 工具调用可能来回多轮（多步任务），循环直到模型不再调用工具
        for _ in range(8):
            msg = self._chat(self._messages)
            tool_calls = msg.get("tool_calls") or []
            # 原样保存这条 assistant 消息（含 tool_calls，供下一轮上下文）
            assistant: dict = {"role": "assistant",
                               "content": msg.get("content") or ""}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            self._messages.append(assistant)

            if not tool_calls:
                return (msg.get("content") or "").strip()

            pending = self._run_tools(tool_calls)
            if pending:
                return pending

        return "抱歉，这个有点复杂，我先停一下。"

    # ---- 流式：边生成边吐句子，让 TTS 尽早开口 ----------------------
    def _stream_once(self, messages: list[dict]):
        """流式调一次中转站，逐块产出 ("content", 文本) / ("tool", 增量元组)。"""
        body = self._request_body(messages, stream=True)
        req = urllib.request.Request(
            config.llm_endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key}"},
            method="POST",
        )
        with _open_request(req) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield ("content", delta["content"])
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    yield ("tool", (tc.get("index", 0), tc.get("id"),
                                    fn.get("name"), fn.get("arguments")))

    def _run_tools(self, tool_calls: list[dict]) -> str | None:
        """执行一批工具调用，把结果追加进对话历史。"""
        waiting = self._pending is not None
        pending = None
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            output = ("未执行：前一个高风险操作正在等待用户确认。" if waiting
                      else self._dispatch(fn.get("name", ""), args))
            self._messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": output,
            })
            waiting = waiting or self._pending is not None
            if waiting and pending is None:
                pending = output
        return pending

    def ask_stream(self, user_text: str):
        """处理一句用户输入；以「一句一吐」的方式产出回复文本，便于边说边生成。

        工具调用轮不产出文本（不朗读），执行完工具继续下一轮；
        纯文本轮则凑齐一句就 yield 一句。流式失败自动回退到非流式 _chat。
        """
        resolved = self._resolve_pending(user_text)
        if resolved is not None:
            yield resolved
            return
        self._start_turn(user_text)

        for _ in range(8):
            content, buf = "", ""
            acc: dict = {}          # index -> 拼接中的工具调用
            had_tool = False
            try:
                for kind, val in self._stream_once(self._messages):
                    if kind == "content":
                        content += val
                        if not had_tool:        # 工具轮不朗读预流文本
                            buf += val
                            sents, buf = _split_sentences(buf)
                            for s in sents:
                                yield s
                    else:
                        had_tool = True
                        idx, cid, name, args = val
                        a = acc.setdefault(idx, {"id": "", "name": "",
                                                 "arguments": ""})
                        a["id"] += cid or ""
                        a["name"] += name or ""
                        a["arguments"] += args or ""
            except Exception:  # noqa: BLE001 流式失败 → 回退非流式
                msg = self._chat(self._messages)
                tcs = msg.get("tool_calls") or []
                text = msg.get("content") or ""
                assistant: dict = {"role": "assistant", "content": text}
                if tcs:
                    assistant["tool_calls"] = tcs
                self._messages.append(assistant)
                if tcs:
                    pending = self._run_tools(tcs)
                    if pending:
                        yield pending
                        return
                    continue
                if text.strip():
                    yield text.strip()
                return

            if had_tool and acc:
                tcs = [{"id": a["id"], "type": "function",
                        "function": {"name": a["name"],
                                     "arguments": a["arguments"]}}
                       for a in acc.values()]
                self._messages.append({"role": "assistant",
                                       "content": content, "tool_calls": tcs})
                pending = self._run_tools(tcs)
                if pending:
                    yield pending
                    return
                continue

            self._messages.append({"role": "assistant", "content": content})
            tail = buf.strip()
            if tail:
                yield tail
            return

        yield "抱歉，这个有点复杂，我先停一下。"
