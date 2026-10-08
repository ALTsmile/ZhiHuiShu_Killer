"""离线自测：登录流程的顺序与「不刷新页面」保证。

运行： .venv\\Scripts\\python.exe tests\\offline_login.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

from zhskiller import login  # noqa: E402
from zhskiller.bridge import UNANSWERED  # noqa: E402
from zhskiller.config import Config  # noqa: E402

LOGIN_URL = "https://login.zhihuishu.com/?origin=zhs"
HOME_URL = "https://www.zhihuishu.com/"
COURSE_URL = "https://studywisdomh5.zhihuishu.com/study/index?recruitAndCourseId=abc"


class FakeLocator:
    def __init__(self, count: int = 0, visible: bool = True):
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def wait_for(self, **_kwargs):
        return None


class FakePage:
    """模拟智慧树课程页：未登录时会在十几秒后跳到登录页。"""

    def __init__(self, url: str = "about:blank", course_url: str = "",
                 logged_in: bool = False, has_login_form: bool = False):
        self.url = url
        self.course_url = course_url
        self.logged_in = logged_in
        self._login_form = has_login_form
        self.gotos: list[str] = []
        self._pending_redirect: str | None = None

    @property
    def has_login_form(self) -> bool:
        """登录表单只在登录页才可见。

        真实站点登录后表单节点可能还留在 DOM 里（只是隐藏），
        所以这里用"在不在登录页"来模拟"表不表可见"。
        """
        return str(self.url).startswith(LOGIN_URL)

    @has_login_form.setter
    def has_login_form(self, value: bool) -> None:
        # 赋值即表示"停在登录页"，URL 由调用方负责设置
        if value:
            self._login_form = True
        else:
            self._login_form = False

    @property
    def frames(self):
        return [self]

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = url
        self.has_login_form = url.startswith(LOGIN_URL)
        if url == self.course_url and not self.logged_in:
            # 延迟跳转：下一次真正查询 DOM 时才"过一会儿"跳到登录页
            self._pending_redirect = LOGIN_URL
        return None

    def wait_for_timeout(self, _ms):
        return None

    def wait_for_selector(self, *_args, **_kwargs):
        if self._pending_redirect:
            self.url = self._pending_redirect
            self.has_login_form = True
            self._pending_redirect = None
        if self.logged_in:
            return None
        raise PlaywrightTimeout("模拟：目标选择器未出现")

    def locator(self, selector: str):
        if "input[name='mobile']" in selector and self.has_login_form:
            return FakeLocator(1, visible=self.has_login_form)
        if self.logged_in and "child-info.hasvideo" in selector:
            return FakeLocator(1)
        return FakeLocator(0)

    def evaluate(self, _script, _arg=None):
        return False


class FakeContext:
    def __init__(self):
        self.cookies_calls = 0

    def cookies(self, _urls=None):
        self.cookies_calls += 1
        # 故意返回一个"名字里带 session"的 cookie：
        # 站点在未登录时也会下发这种 cookie，早期的实现就是被它骗了。
        return [
            {"name": "o_session_id", "value": "x" * 32, "domain": ".zhihuishu.com"},
            {"name": "JSESSIONID", "value": "y" * 32, "domain": "passport.zhihuishu.com"},
        ]


class FakeSession:
    def __init__(self, url="about:blank", course_url="", logged_in=False,
                 has_login_form=False):
        self.page = FakePage(url, course_url=course_url, logged_in=logged_in,
                             has_login_form=has_login_form)
        self.context = FakeContext()


class FakeBridge:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.asked: list[str] = []
        self.on_ask = None
        self._pending: dict[int, object] = {}
        self._ids = 0

    def ask(self, kind, payload=None, timeout=None):
        self.asked.append(kind)
        if self.on_ask:
            self.on_ask(kind, payload)
        if self.answers:
            return self.answers.pop(0)
        return None

    # 非阻塞提示：post + poll + release
    def post(self, kind, payload=None):
        self.asked.append(kind)
        if self.on_ask:
            self.on_ask(kind, payload)
        self._ids += 1
        self._pending[self._ids] = self.answers.pop(0) if self.answers else None
        return self._ids

    def poll(self, request_id, timeout=0.0):
        if self._pending.get(request_id) is not None:
            return self._pending.pop(request_id)
        return UNANSWERED

    def release(self, request_id):
        self._pending.pop(request_id, None)

    def check_stop(self):
        return None

    def sleep(self, *_a, **_k):
        return None


class SilentLogger:
    def __init__(self):
        self.lines: list[str] = []

    def _add(self, level, message):
        self.lines.append(f"[{level}] {message}")

    def info(self, m): self._add("信息", m)
    def warn(self, m): self._add("警告", m)
    def error(self, m): self._add("错误", m)
    def debug(self, m): pass
    def success(self, m): self._add("完成", m)
    def section(self, m): self._add("信息", m)


def check(name: str, condition: bool) -> bool:
    print(("  [通过] " if condition else "  [失败] ") + name)
    return condition


def temp_config() -> Config:
    path = Path(tempfile.mkdtemp(prefix="zhs-cfg-")) / "config.json"
    return Config(path)


def main() -> int:
    failures = 0
    real_save = login.save_credentials
    real_load = login.load_credentials
    login.save_credentials = lambda u, p, path=None, **kwargs: "dpapi"
    login.load_credentials = lambda path=None: None
    try:
        print("1. prepare_login：没有账号 → 询问 → 同意 → 返回自动登录")
        config = temp_config()
        bridge = FakeBridge(answers=["agree", {"username": "u", "password": "p"}])
        plan = login.prepare_login(config, SilentLogger(), bridge)
        failures += not check("先问的是 first_run", bridge.asked[:1] == ["first_run"])
        failures += not check("得到自动登录计划", plan["mode"] == "auto")
        failures += not check("带回账号", (plan["credentials"] or {}).get("username") == "u")

        print("2. prepare_login：拒绝 → 手动登录，且不改 prompt_state")
        config = temp_config()
        bridge = FakeBridge(answers=["decline"])
        plan = login.prepare_login(config, SilentLogger(), bridge)
        failures += not check("转为手动登录", plan["mode"] == "manual")
        failures += not check(
            "prompt_state 仍是 ask（下次还要提醒）",
            config.get("login.prompt_state") == "ask",
        )

        print("3. prepare_login：不再提醒 → 记住 muted")
        config = temp_config()
        bridge = FakeBridge(answers=["mute"])
        plan = login.prepare_login(config, SilentLogger(), bridge)
        failures += not check("转为手动登录", plan["mode"] == "manual")
        failures += not check("已记住不再提醒", config.get("login.prompt_state") == "muted")

        print("4. prepare_login：muted 时不弹窗")
        config = temp_config()
        config.set("login.prompt_state", "muted")
        bridge = FakeBridge(answers=["agree"])
        plan = login.prepare_login(config, SilentLogger(), bridge)
        failures += not check("没有询问", bridge.asked == [])
        failures += not check("直接手动登录", plan["mode"] == "manual")

        print("5. 手动登录：探页被站点跳到登录页 → 只加载一次，且不再刷新")
        session = FakeSession("about:blank", course_url=COURSE_URL)
        bridge = FakeBridge(answers=[True])
        logger = SilentLogger()
        def logged_in_now(kind, _payload):
            # 模拟用户扫码成功后：站点自己把页面跳走，并且课程页从此可以正常打开
            if kind == "manual_login":
                session.page.url = HOME_URL
                session.page.logged_in = True

        bridge.on_ask = logged_in_now

        ok = login.ensure_login(session, config, logger, bridge,
                                {"credentials": None, "mode": "manual"},
                                probe_url=COURSE_URL, probe_timeout=8)
        failures += not check("登录成功", ok is True)
        failures += not check(
            "只打开课程页（探页 + 最终确认各一次），没有刷新登录页",
            session.page.gotos == [COURSE_URL, COURSE_URL],
        )
        failures += not check("确认过程中没有重新 goto", LOGIN_URL not in session.page.gotos)

        print("5b. 确认时站点已自动跳走 → 判定登录成功")
        session = FakeSession(LOGIN_URL, has_login_form=True)
        bridge = FakeBridge(answers=[True])
        bridge.on_ask = lambda kind, _p: (
            setattr(session.page, "url", HOME_URL) if kind == "manual_login" else None
        )
        ok = login.ensure_login(session, config, logger, bridge,
                                {"credentials": None, "mode": "manual"},
                                probe_url="", probe_timeout=8)
        failures += not check("登录成功", ok is True)
        failures += not check("已经登录时不额外导航", session.page.gotos == [])

        print("6. 手动登录：确认时仍未登录 → 再次询问，同样不刷新")
        session = FakeSession(LOGIN_URL, has_login_form=True)
        bridge = FakeBridge(answers=[True, True])

        def still_not_logged_in(kind, _payload):
            if len(bridge.asked) >= 2 and kind == "manual_login":
                session.page.url = HOME_URL

        bridge.on_ask = still_not_logged_in
        ok = login.ensure_login(session, config, logger, bridge,
                                {"credentials": None, "mode": "manual"},
                                probe_url="", probe_timeout=1)
        failures += not check("第二次确认后判定成功", ok is True)
        failures += not check("问了两次", len(bridge.asked) == 2)
        failures += not check("一次导航都没有", session.page.gotos == [])

        print("7. 课程页可直接打开 → 判定已登录，不碰登录页")
        session = FakeSession("about:blank", course_url=COURSE_URL, logged_in=True)
        bridge = FakeBridge()
        ok = login.ensure_login(session, config, logger, bridge,
                                {"credentials": None, "mode": "manual"},
                                probe_url=COURSE_URL, probe_timeout=8)
        failures += not check("判定已登录", ok is True)
        failures += not check("只打开了课程页", session.page.gotos == [COURSE_URL])
        failures += not check("没有弹登录询问", bridge.asked == [])

        print("8. safe_goto：被站点跳转打断不算失败")

        class InterruptedPage(FakePage):
            def goto(self, url, **kwargs):
                self.url = HOME_URL
                raise Exception(
                    'Page.goto: Navigation to "https://passport.zhihuishu.com/login" '
                    'is interrupted by another navigation to "https://www.zhihuishu.com/"'
                )

        page = InterruptedPage()
        failures += not check("返回 True", login.safe_goto(page, LOGIN_URL, logger) is True)

        print("9. session_ready：about:blank 不算已登录")
        failures += not check(
            "about:blank 判定为未登录",
            login.session_ready(FakeSession("about:blank")) is False,
        )
        failures += not check(
            "登录页判定为未登录",
            login.session_ready(FakeSession(LOGIN_URL)) is False,
        )
        failures += not check(
            "登录页 + 带 session 字样的 cookie 仍然算未登录（回归：曾因此自作主张）",
            login.session_ready(FakeSession(LOGIN_URL)) is False,
        )
        failures += not check(
            "首页判定为已登录", login.session_ready(FakeSession(HOME_URL)) is True
        )
    finally:
        login.save_credentials = real_save
        login.load_credentials = real_load

    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
