"""AI 答题客户端（OpenAI 兼容接口）。

默认对接 /chat/completions，任何遵循该协议的网关（OpenAI、DeepSeek、
通义、智谱、本地 Ollama 等）都可以把 base_url 指过去。
只用标准库，避免为一个小功能再拉一堆依赖。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .logger import Logger

# 用户要求：AI 一次没回话别急着改随机，先多试几次再说
AI_MAX_ATTEMPTS = 5
AI_RETRY_BACKOFF = 2.0        # 第 n 次失败后等待 n*2 秒（最多 6 秒）
# 连续失败后先冷却一段时间：期间直接随机作答，过后会自动再试 AI
AI_COOLDOWN_SECONDS = 300

SYSTEM_PROMPT = (
    "你是一个答题助手。用户会给你一道选择题和若干选项，"
    "请判断最可能的正确答案。只输出选项的字母（例如 A），不要输出其他内容。"
)

MULTI_SYSTEM_PROMPT = (
    "你是一个答题助手。用户会给你一道选择题（可能是单选，也可能多选）和若干选项。"
    "单选题只输出一个字母；多选题输出所有正确选项的字母（例如 ACD，按字母顺序）。"
    "只输出字母，不要输出其他内容。"
)

LETTERS = "ABCDEFGHIJ"


class AiClient:
    def __init__(self, cfg: dict, logger: Logger):
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.model = str(cfg.get("model") or "")
        self.timeout = int(cfg.get("timeout") or 30)
        self.logger = logger
        self.last_error = ""
        self._cooldown_until = 0.0
        self._user_answered = False

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def cooling_down(self) -> bool:
        """连续失败后的冷却期：这段时间不再干等 AI。"""
        return time.time() < self._cooldown_until

    def start_cooldown(self, seconds: float = AI_COOLDOWN_SECONDS) -> None:
        self._cooldown_until = time.time() + max(1.0, seconds)

    def choose(self, question: str, options: list[str]) -> int | None:
        """返回选中选项的下标；失败返回 None。"""
        if not self.ready or not options or self.cooling_down:
            return None
        listing = "\n".join(
            f"{LETTERS[index]}. {text}" for index, text in enumerate(options[: len(LETTERS)])
        )
        prompt = f"题目：{question.strip() or '（题目文字未取到）'}\n{listing}\n\n请只回答一个字母。"
        text = self._chat_with_retry(SYSTEM_PROMPT, prompt, "单选")
        if text is None:
            return None
        match = re.search(r"[A-J]", (text or "").upper())
        if not match:
            self.logger.warn(f"AI 返回无法解析：{text!r}")
            return None
        index = LETTERS.index(match.group(0))
        if index >= len(options):
            return None
        return index

    def choose_multi(self, question: str, options: list[str],
                     max_answers: int = 4) -> list[int] | None:
        """多选题：返回一组选项下标；失败返回 None。"""
        if not self.ready or not options or self.cooling_down:
            return None
        listing = "\n".join(
            f"{LETTERS[index]}. {text}" for index, text in enumerate(options[: len(LETTERS)])
        )
        prompt = (
            f"题目：{question.strip() or '（题目文字未取到）'}\n{listing}\n\n"
            "如果是多选题，请输出所有正确选项的字母（例如 ACD）；"
            "如果是单选，只输出一个字母。"
        )
        text = self._chat_with_retry(MULTI_SYSTEM_PROMPT, prompt, "多选")
        if text is None:
            return None
        letters = sorted(
            {match.group(0) for match in re.finditer(r"[A-J]", (text or "").upper())}
        )
        indices = [LETTERS.index(letter) for letter in letters
                   if LETTERS.index(letter) < len(options)]
        if not indices:
            self.logger.warn(f"AI 多选返回无法解析：{text!r}")
            return None
        return indices[:max_answers]

    def _chat_with_retry(self, system: str, prompt: str, label: str) -> str | None:
        """调用接口，失败就重试（默认 5 次），全部失败返回 None。"""
        for attempt in range(1, AI_MAX_ATTEMPTS + 1):
            try:
                text = self._chat_with_system(system, prompt)
                self.last_error = ""
                return text
            except Exception as exc:
                self.last_error = str(exc).strip() or exc.__class__.__name__
                if attempt < AI_MAX_ATTEMPTS:
                    wait = min(AI_RETRY_BACKOFF * attempt, 6.0)
                    self.logger.warn(
                        f"AI {label}第 {attempt}/{AI_MAX_ATTEMPTS} 次调用失败："
                        f"{self.last_error}，{wait:.0f} 秒后重试…"
                    )
                    time.sleep(wait)
        self.logger.warn(
            f"AI {label}连续 {AI_MAX_ATTEMPTS} 次都没有成功，"
            f"最后一次错误：{self.last_error}"
        )
        return None

    def _chat_with_system(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            or ""
        )


def resolve_ai_failure(ai: AiClient | None, bridge, config, logger: Logger,
                       examine: str) -> str:
    """AI 连续失败后弹窗问用户怎么办，返回 'ai' / 'random' / 'manual'。

    只会问一次（本次运行内）：用户选「继续用 AI」后就不再打扰，
    因为每题都会重新尝试 AI，等他把额度/配置弄好就会自动恢复。
    """
    if ai is None:
        return "random"
    if getattr(ai, "_user_answered", False):
        return "random"
    ai._user_answered = True

    where = "随堂练习" if examine == "in_video" else "章节测验"
    if examine == "in_video":
        cancel_text, cancel_value = "改用随机选择", "random"
        hint = "随堂练习不计分，随机选择通常够用。"
    else:
        cancel_text, cancel_value = "改用手动作答", "manual"
        hint = "章节测验计分，建议先到界面把 AI 配置修好，或改用手动作答。"
    message = (
        f"AI 接口连续 {AI_MAX_ATTEMPTS} 次都没有正常返回，现在没法用 AI 完成{where}。\n\n"
        "常见原因：\n"
        "· API Key 失效，或额度/余额用完（token 耗尽、未续费）\n"
        "· 接口地址（Base URL）或模型名填错了\n"
        "· 网络不通 / 接口临时超时 / 代理拦截\n\n"
        f"当前配置：{getattr(ai, 'base_url', '') or '（未填写）'} · "
        f"{getattr(ai, 'model', '') or '（未填写）'}\n"
        f"最后一次错误：{getattr(ai, 'last_error', '') or '未知'}\n\n"
        "要继续用 AI 作答吗？\n"
        "· 「继续用 AI」—— 如果你刚续费/改好配置，选这个，程序会重新尝试；\n"
        f"· 「{cancel_text}」—— 立刻继续。{hint}"
    )
    if bridge is None:
        return "random"
    answer = bridge.ask(
        "ai_unavailable",
        {
            "message": message,
            "examine": examine,
            "ok_text": "继续用 AI",
            "ok_value": "retry_ai",
            "cancel_text": cancel_text,
            "cancel_value": cancel_value,
            # 用户没理会弹窗时**不要**改配置，只给个安全兜底
            "timeout_value": "timeout",
        },
    )
    if answer == "retry_ai":
        logger.info("已按你的选择继续使用 AI 作答。")
        return "ai"
    if answer in (None, "timeout"):
        if examine == "in_video":
            logger.warn("AI 弹窗没有回应，随堂练习先随机作答继续。")
            return "random"
        logger.warn("AI 弹窗没有回应，这一题交给你手动处理。")
        return "manual"
    if answer == "manual":
        if config is not None:
            try:
                config.set(f"quiz.{examine}_mode", "manual")
                config.save()
            except Exception as exc:
                logger.debug(f"写入手动作答失败：{exc}")
        logger.warn(
            f"已把{where}改成手动作答并写入配置"
            "（修好 AI 后到界面「播放与答题」改回「AI 识别」即可）。"
        )
        return "manual"
    if config is not None:
        try:
            config.set(f"quiz.{examine}_mode", "random")
            config.save()
            logger.warn(
                f"已把{where}改成随机选择并写入配置"
                "（修好 AI 后到界面「播放与答题」改回「AI 识别」即可）。"
            )
        except Exception as exc:
            logger.debug(f"写入随机兜底失败：{exc}")
    return "random"
