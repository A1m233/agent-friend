"""Explicit test-process-only LLM replacement; never imported by normal dev startup."""

import json
import os
import time
from pathlib import Path

if root := os.environ.get("AGENT_FRIEND_DESKTOP_TEST_DOUBLE"):
    from llm_providers import LLMClient, LLMTextDelta, LLMTurnDone, ModelResponse

    def _text(request):
        users = [message for message in request.messages if message.get("role") == "user"]
        content = users[-1].get("content", "") if users else ""
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    def _stream(self, request):
        content = _text(request)
        audit = {"pid": os.getpid(), "model": self.spec.model, "input": content, "at": time.time()}
        with (Path(root) / "model-double.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(audit, ensure_ascii=False) + "\n")
        deadline = time.monotonic() + (45 if "QUEUE-HOLD" in content else 1)
        while time.monotonic() < deadline and not (Path(root) / "release-model").exists():
            if request.control.cancel_check:
                request.control.cancel_check()
            time.sleep(0.1)
        yield LLMTextDelta(text="桌面验收模拟回复：请求已按当前会话上下文执行。")
        yield LLMTurnDone(stop_reason="end_turn")

    def _generate(self, request):
        return ModelResponse(text="桌面验收模拟结果")

    LLMClient.stream = _stream
    LLMClient.generate = _generate
