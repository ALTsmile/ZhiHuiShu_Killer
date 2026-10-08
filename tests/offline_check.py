"""离线自测：用模拟 DOM 验证页面脚本逻辑（不需要智慧树账号）。

其中的「AI 随堂练习」弹窗是**按真实编译产物复刻**的：
源码位于课程页 chunk `index-7NdklYGJ.js`，
   div.question > .question-container > .title + .options > .option > .prefix/.text
   div.submit-btn 「提交作答」（提交后消失）
   div.answer-container（提交后出现）

运行： .venv\\Scripts\\python.exe tests\\offline_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from zhskiller import captcha, catalog as catalog_mod, player, popups, quiz, site  # noqa: E402


def _ai_question(index: int, options: list[str]) -> str:
    rows = "".join(
        f'<div class="option" onclick="window.__pick({index - 1},{pos},this)">'
        f'<span class="class-question-select">{"ABCDEFGH"[pos]}</span>'
        f'<span class="answer"><p>{text}</p></span></div>'
        for pos, text in enumerate(options)
    )
    return f"""
    <div class="item ques-card-box">
      <div class="row">
        <span class="type">{index}、[单选题] </span>
        <span class="question">关于人工智能发展历程中的三次浪潮，下列说法正确的是？</span>
      </div>
      <div class="options">{rows}</div>
    </div>
    """


# 真实结构：AI 随堂练习（用户从浏览器复制的 DOM + 组件源码 AITestQuestion 还原）
AI_QUIZ = """
<div class="transparent-mask"></div>
<div class="components-box has-compoent">
  <div class="ai-test-question-wrapper">
    <div class="header-box">
      <img class="left-img" src="about:blank" alt="">
      <div class="right-box">
        <p class="tit">AI随堂练习</p>
        <p class="tips"><span>「AI 随堂练习的表现不会影响您的课程成绩」</span>以下所有内容均由AI生成请注意甄别</p>
      </div>
      <div class="close-box">
        <svg class="svg-icon icon-close" style="width: 30px; height: 30px;"
             onclick="window.__close()">×</svg>
      </div>
    </div>
    <div class="question-body">
      <div class="out-contioner">
        <div class="el-scrollbar">
          <div class="el-scrollbar__wrap">
            <div class="el-scrollbar__view">
              <div class="ques">%QUESTIONS%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="submit-footer">
      <div class="submit-btn"><span class="submits" onclick="window.__submit()">提交作答</span></div>
    </div>
  </div>
</div>
<script>
window.__picks = [];
window.__submitted = false;
window.__closed = false;
window.__closeBlocked = false;
window.__questionCount = %QCOUNT%;
window.__pick = function (q, o, el) {
    document.querySelectorAll('.ques .item')[q].querySelectorAll('.option').forEach(function (opt) {
        opt.querySelector('.class-question-select').classList.remove('isSelect');
    });
    el.querySelector('.class-question-select').classList.add('isSelect');
    window.__picks.push(q + '-' + o);
};
window.__submit = function () {
    // 真实组件里：未作答点提交会 toast「暂未作答，请完成作答后再提交！」
    if (window.__picks.length < window.__questionCount) {
        window.__toast = '未作答的弹题不能关闭';
        return;
    }
    window.__submitted = true;
    document.querySelectorAll('.submits').forEach(function (span) {
        span.className = 'done';
        span.textContent = '已提交';
    });
    document.querySelectorAll('.ques .item').forEach(function (item) {
        if (item.querySelector('.analyze')) return;
        const box = document.createElement('div');
        box.className = 'analyze ques-card-box';
        box.innerHTML = '<div class="analyze-box ar"><span class="ana">回答正确</span></div>';
        item.appendChild(box);
    });
};
window.__close = function () {
    // 真实组件里：未作答时关闭会被拦下（bullet15）
    if (window.__submitted !== true) {
        window.__closeBlocked = true;
        window.__toast = '未作答的弹题不能关闭';
        return;
    }
    window.__closed = true;
    document.querySelector('.components-box').remove();
};
</script>
"""


def ai_quiz_html(question_count: int = 1) -> str:
    questions = "".join(
        _ai_question(i + 1, [f"选项{chr(65 + j)}的内容" for j in range(4)])
        for i in range(question_count)
    )
    return AI_QUIZ.replace("%QUESTIONS%", questions).replace(
        "%QCOUNT%", str(question_count)
    )


LEGACY_QUIZ = """
<div class="el-dialog__wrapper">
  <div class="el-dialog">
    <div class="topic-title">随堂练习</div>
    <div class="el-scrollbar__view">
      <div class="number">1</div>
      <ul>
        <li class="topic-item" onclick="window.__picked='A'">A. 选项一</li>
        <li class="topic-item" onclick="window.__picked='B'">B. 选项二</li>
      </ul>
    </div>
    <button onclick="window.__submitted=true; this.closest('.el-dialog__wrapper').remove();">提交</button>
  </div>
</div>
"""

CHAPTER_QUIZ = """
<div class="el-dialog__wrapper">
  <div class="el-dialog">
    <div class="el-dialog__title">章节测试</div>
    <div class="question">以下说法正确的是？</div>
    <div class="option-item" onclick="window.__picked='A'">A. 甲</div>
    <div class="option-item" onclick="window.__picked='B'">B. 乙</div>
    <button onclick="window.__submitted=true; this.closest('.el-dialog__wrapper').remove();">提交</button>
  </div>
</div>
"""

NETWORK_ERROR = """
<div class="el-message-box">
  <div class="el-message-box__content">网络错误，请检查网络后重试</div>
  <div class="el-message-box__btns">
    <button onclick="window.__confirmed=true; this.closest('.el-message-box').remove();">确定</button>
  </div>
</div>
"""

AI_NOTICE = """
<div class="el-overlay">
  <div class="el-dialog ai-notice-dialog">
    <div class="ai-notice-wrapper">
      <div class="ai-notice-wrapper-left"><img class="top-img" src="about:blank"></div>
      <div class="ai-notice-wrapper-right">
        <p class="top-notice-title">同学，我们对刚才学的知识点进行一个随堂练习！</p>
        <div class="btn" onclick="window.__notice_closed = true; this.closest('.el-overlay').remove();">知道了</div>
      </div>
    </div>
  </div>
</div>
"""

YIDUN = """
<div class="yidun_popup">
  <div class="yidun_modal">
    <div class="yidun_modal__title">请完成安全验证</div>
    <img class="yidun_bg-img" src="about:blank">
    <img class="yidun_jigsaw" src="about:blank">
    <div class="yidun_slider"></div>
  </div>
</div>
"""


class FakeNode:
    def __init__(self, attribute: str | None = None, text: str | None = None):
        self.attribute = attribute
        self.text = text

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def get_attribute(self, _name):
        return self.attribute

    def text_content(self):
        return self.text


class FakeEmptyNode(FakeNode):
    def count(self):
        return 0


class FakeLesson:
    """按选择器返回假节点，用来验证进度读取的优先级。"""

    def __init__(self, mapping: dict):
        self.mapping = mapping

    def locator(self, selector: str):
        for key, node in self.mapping.items():
            if key in selector:
                return node
        return FakeEmptyNode()


class _Logger:
    def __init__(self):
        self.lines: list[str] = []

    def _add(self, level, message):
        self.lines.append(f"[{level}] {message}")

    def info(self, m): self._add("信息", m)
    def warn(self, m): self._add("警告", m)
    def error(self, m): self._add("错误", m)
    def success(self, m): self._add("完成", m)
    def debug(self, m): pass


class _Config:
    def __init__(self, values: dict | None = None):
        self._values = dict(values or {})

    def quiz_mode(self, kind: str) -> str:
        return self._values.get(f"quiz.{kind}_mode", "random")

    def set(self, key: str, value) -> None:
        self._values[key] = value

    def save(self) -> None:
        self.saved = True


class _Bridge:
    def __init__(self):
        self.posts: list[str] = []

    def sleep(self, *_a, **_k): pass
    def check_stop(self): pass
    def post(self, kind, payload=None):
        self.posts.append(kind)
        return len(self.posts)
    def release(self, *_a): pass


class _StopWait(Exception):
    """用来跳出"等用户手动作答"的死循环，测试里不需要真的等。"""


class _StopBridge(_Bridge):
    def __init__(self, allow: int = 1):
        super().__init__()
        self._allow = allow
        self._sleeps = 0

    def sleep(self, *_a, **_k):
        self._sleeps += 1
        if self._sleeps > self._allow:
            raise _StopWait()


class _AskBridge(_Bridge):
    """会回答 ask() 的假 bridge。"""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def ask(self, kind, payload=None, timeout=None):
        self.posts.append(kind)
        return self.answer


class _FailAi:
    """模拟 AI 接口不可用（有配置但返回不了答案）。"""

    base_url = "https://wrong.example.com/v1"
    model = "no-such-model"
    ready = True
    last_error = "timed out"
    cooling_down = False

    def choose(self, _question, _options):
        return None

    def start_cooldown(self, seconds: float = 0) -> None:
        self.cooling_down = True


def check(name: str, condition: bool) -> bool:
    print(("  [通过] " if condition else "  [失败] ") + name)
    return condition


def main() -> int:
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        logger, config, bridge = _Logger(), _Config(), _Bridge()

        print("1. AI 随堂练习（单题）：选一个选项 -> 提交 -> 关掉解析页")
        page.set_content(ai_quiz_html(1))
        info = quiz._scan(page)
        failures += not check("扫描到题目", bool(info))
        failures += not check("识别为 1 道题", bool(info) and len(info["questions"]) == 1)
        failures += not check("识别到 4 个选项", bool(info) and info["optionCount"] == 4)
        failures += not check(
            "识别到「提交作答」按钮", bool(info) and bool(info["submit"])
        )
        failures += not check(
            "识别到弹窗关闭按钮", bool(info) and bool(info["close"])
        )
        failures += not check(
            "提交按钮指向里层 .submits（事件绑在它上面）",
            page.locator('[data-zhs-submit="1"]').count() == 1
            and page.locator('[data-zhs-submit="1"].submits').count() == 1,
        )
        failures += not check(
            "关闭按钮指向里层 .icon-close",
            page.locator('[data-zhs-close="1"]').count() == 1
            and page.locator('[data-zhs-close="1"].icon-close').count() == 1,
        )
        failures += not check("弹窗会挡住视频", popups.is_video_blocked(page))

        # 单独验证「选中 -> isSelect -> 被认成已作答」这一步
        first_tag = info["questions"][0]["options"][0]["tag"]
        quiz._click_tag(page, "opt", first_tag)
        failures += not check(
            "点选项后出现 isSelect 标记",
            page.locator(".class-question-select.isSelect").count() == 1,
        )
        quiz._clear_tags(page)
        rescanned = quiz._scan(page)
        failures += not check(
            "扫描能把 isSelect 认成已作答",
            bool(rescanned) and rescanned["answeredCount"] == 1,
        )

        page.set_content(ai_quiz_html(1))
        quiz._clear_tags(page)

        handled = quiz.handle_quiz(page, config, logger, bridge, None, "in_video", "随堂练习")
        failures += not check("报告已处理", handled)
        failures += not check("点了一个选项", page.evaluate("() => window.__picks.length") == 1)
        failures += not check("点了提交", bool(page.evaluate("() => window.__submitted")))
        failures += not check("关掉了弹窗", bool(page.evaluate("() => window.__closed")))
        failures += not check(
            "是先提交再关闭（没有触发未作答拦截）",
            page.evaluate("() => window.__closeBlocked") is False,
        )
        failures += not check(
            "弹窗已消失", page.locator(".ai-test-question-wrapper").count() == 0
        )

        print("2. AI 随堂练习（3 题）：每道题都要作答后再提交")
        page.set_content(ai_quiz_html(3))
        logger, bridge = _Logger(), _Bridge()
        handled = quiz.handle_quiz(page, config, logger, bridge, None, "in_video", "随堂练习")
        failures += not check("报告已处理", handled)
        picks = page.evaluate("() => window.__picks")
        failures += not check(f"3 道题各点了一次（实际 {len(picks)}）", len(picks) == 3)
        failures += not check("三个选择互不相同", len({p.split('-')[0] for p in picks}) == 3)
        failures += not check("点了提交", bool(page.evaluate("() => window.__submitted")))
        failures += not check("关掉了弹窗", bool(page.evaluate("() => window.__closed")))

        print("3. 旧版随堂练习（无 .el-overlay）同样能过")
        page.set_content(LEGACY_QUIZ)
        page.evaluate("() => { window.__picked = null; window.__submitted = false; }")
        logger, bridge = _Logger(), _Bridge()
        handled = quiz.handle_quiz(page, config, logger, bridge, None, "in_video", "随堂练习")
        failures += not check("报告已处理", handled)
        failures += not check("点了选项", page.evaluate("() => window.__picked") is not None)
        failures += not check("点了提交", bool(page.evaluate("() => window.__submitted")))

        print("4. 章节测试：默认模式下不会自动作答")
        page.set_content(CHAPTER_QUIZ)
        page.evaluate("() => { window.__picked = null; window.__submitted = false; }")
        failures += not check("能扫描到章节测试", bool(quiz._scan(page)))
        quiz._clear_tags(page)
        manual_bridge = _StopBridge()
        try:
            quiz.handle_quiz(page, _Config({"quiz.chapter_mode": "manual"}),
                             _Logger(), manual_bridge, None, "chapter", "章节练习")
        except _StopWait:
            pass
        failures += not check(
            "章节模式=manual 时只提示、不做任何点击",
            manual_bridge.posts == ["quiz_manual"]
            and page.evaluate("() => window.__submitted") is False
            and page.evaluate("() => window.__picked") is None,
        )
        page.set_content(CHAPTER_QUIZ)
        page.evaluate("() => { window.__picked = null; window.__submitted = false; }")
        handled = quiz.handle_quiz(page, _Config({"quiz.chapter_mode": "random"}),
                                   _Logger(), _Bridge(), None, "chapter", "章节练习")
        failures += not check("勾选后才会自动作答", handled)
        failures += not check("确实点了提交", bool(page.evaluate("() => window.__submitted")))

        print("5. 遮罩层会让程序停止抢播放（避免播放/暂停抖动）")
        page.set_content(ai_quiz_html(1))
        failures += not check("有弹窗时判定为被遮住", popups.is_video_blocked(page))
        page.set_content("<div>空页面</div>")
        failures += not check("无弹窗时判定为没被遮住", not popups.is_video_blocked(page))

        print("6. AI 助教提示弹窗（无选项）不应被当成题目")
        page.set_content(AI_NOTICE)
        failures += not check("不会被误判成题目", quiz._scan(page) is None)
        failures += not check("能被关掉", popups.close_ai_notice(page, _Logger()))
        failures += not check(
            "关掉后不再遮挡", not popups.is_video_blocked(page)
        )

        print("7. 网络错误提示框")
        page.set_content(NETWORK_ERROR)
        page.evaluate("() => { window.__confirmed = false; }")
        failures += not check("识别并关闭", popups.dismiss_error_dialog(page, _Logger()))
        failures += not check("确实点了确定", bool(page.evaluate("() => window.__confirmed")))

        print("8. 人机验证检测")
        page.set_content(YIDUN)
        failures += not check("检测到滑块验证", captcha.has_verification(page))
        page.evaluate("() => document.querySelector('.yidun_popup').remove()")
        failures += not check("消失后不再报警", not captcha.has_verification(page))

        print("9. 课时进度读取（新版共享课：勾在 80% 就出现，必须先看进度）")
        progress_selector = site.WISDOM.progress
        finish_selector = site.WISDOM.finish
        lesson = FakeLesson({
            progress_selector: FakeNode(attribute="85"),
            finish_selector: FakeNode(),
        })
        failures += not check(
            "有进度环时读 85% 而不是误判 100%",
            catalog_mod.lesson_progress(lesson, site.WISDOM) == 85,
        )
        lesson = FakeLesson({
            progress_selector: FakeEmptyNode(),
            finish_selector: FakeNode(),
        })
        failures += not check(
            "只有完成勾时算 100%", catalog_mod.lesson_progress(lesson, site.WISDOM) == 100
        )
        lesson = FakeLesson({
            progress_selector: FakeEmptyNode(),
            finish_selector: FakeEmptyNode(),
        })
        failures += not check(
            "都没有时算 0%", catalog_mod.lesson_progress(lesson, site.WISDOM) == 0
        )
        lesson = FakeLesson({progress_selector: FakeNode(attribute="100")})
        failures += not check(
            "进度环到 100% 时为完成", catalog_mod.is_complete(lesson, site.WISDOM)
        )

        print("10. 模拟鼠标操作：会产生输入事件，且绝不误触视频")
        page.set_content(
            "<script>window.__moves = 0; window.__videoClicks = 0; window.__clicks = 0;"
            "document.addEventListener('click', () => window.__clicks++);"
            "document.addEventListener('mousemove', () => window.__moves++);</script>"
            '<video style="position:fixed;left:0;top:0;width:600px;height:320px;'
            'background:#333" onclick="window.__videoClicks++"></video>'
        )
        player.human_activity(page, _Logger(), rounds=2, allow_click=True)
        failures += not check(
            "产生了鼠标移动事件", page.evaluate("() => window.__moves") > 0
        )
        failures += not check(
            "产生了真实点击事件", page.evaluate("() => window.__clicks") >= 1
        )
        failures += not check(
            "没有点到视频（不会切换播放/暂停）",
            page.evaluate("() => window.__videoClicks") == 0,
        )

        print("11. 网络诊断：能筛出学习时长上报请求并记录 payload")
        import tempfile

        from zhskiller import netlog as netlog_mod

        class _Req:
            def __init__(self, url, method="POST", data=""):
                self.url, self.method, self.post_data = url, method, data

        class _Resp:
            def __init__(self, url, status=200, text=""):
                self.url, self.status, self._text = url, status, text

            def text(self):
                return self._text

        tmp = Path(tempfile.mkdtemp(prefix="zhs-net-"))
        logger_n = netlog_mod.NetworkLog(_Logger(), enabled=True)
        logger_n.path = tmp / "network.log"
        logger_n._handle = open(logger_n.path, "a", encoding="utf-8")
        logger_n._on_request(
            _Req("https://studywisdomh5.zhihuishu.com/api/saveStudyTime",
                 data='{"lessonId":1,"time":30}')
        )
        logger_n._on_request(_Req("https://studywisdomh5.zhihuishu.com/assets/app.js"))
        logger_n._on_response(
            _Resp("https://studywisdomh5.zhihuishu.com/api/saveStudyTime",
                  text='{"code":200,"data":{"progress":5}}')
        )
        logger_n.flush_bodies()
        logger_n._handle.close()
        content = logger_n.path.read_text(encoding="utf-8")
        failures += not check("记录了上报请求", "saveStudyTime" in content)
        failures += not check("记录了 payload", "lessonId" in content)
        failures += not check("记录了响应体", '"progress": 5' in content or "progress" in content)
        failures += not check("静态资源被过滤掉", "app.js" not in content)

        print("12. 随堂练习面板不能被当成章节测验（回归：会误弹询问并卡住）")
        page.set_content(ai_quiz_html(1))
        failures += not check(
            "随堂练习面板不算章节测验", quiz.is_chapter_quiz(page) is False
        )
        page.set_content(CHAPTER_QUIZ)
        failures += not check(
            "章节测试弹窗才算章节测验", quiz.is_chapter_quiz(page) is True
        )
        page.set_content(NETWORK_ERROR)
        failures += not check(
            "网络错误提示框不算章节测验", quiz.is_chapter_quiz(page) is False
        )

        print("13. 章节测验：不勾选自动提交时只作答、不提交，并提醒人工核对")
        page.set_content(CHAPTER_QUIZ)
        page.evaluate("() => { window.__picked = null; window.__submitted = false; }")
        review_bridge = _StopBridge()
        try:
            answered = quiz.handle_quiz(
                page, _Config({"quiz.chapter_mode": "random"}), _Logger(),
                review_bridge, None, "chapter", "章节测验", auto_submit=False,
            )
        except _StopWait:
            answered = True
        failures += not check("报告已作答", answered)
        failures += not check(
            "点过选项", page.evaluate("() => window.__picked") is not None
        )
        failures += not check(
            "没有提交试卷", page.evaluate("() => window.__submitted") is False
        )
        failures += not check(
            "提醒了用户自己提交", review_bridge.posts == ["quiz_review"]
        )

        print("14. AI 不可用：先询问用户，并把选择写回配置")
        config = _Config({"quiz.in_video_mode": "ai"})
        bridge = _AskBridge("manual")
        index = quiz._choose_index(
            "题目", ["A", "B"], "ai", _FailAi(), _Logger(),
            config=config, bridge=bridge, examine="in_video",
        )
        failures += not check("弹出了 ai_unavailable 询问", bridge.posts == ["ai_unavailable"])
        failures += not check("选择手动作答时不替用户选", index is None)
        failures += not check(
            "已把手动作答写进配置", config.quiz_mode("in_video") == "manual"
        )
        failures += not check("配置已落盘", getattr(config, "saved", False))

        config = _Config({"quiz.in_video_mode": "ai"})
        bridge = _AskBridge("random")
        index = quiz._choose_index(
            "题目", ["A", "B"], "ai", _FailAi(), _Logger(),
            config=config, bridge=bridge, examine="in_video",
        )
        failures += not check(
            "选择随机时立刻给一个选项", isinstance(index, int) and 0 <= index < 2
        )
        failures += not check(
            "已把随机写进配置", config.quiz_mode("in_video") == "random"
        )

        print("15. 整页形式的章节测验（没有弹窗容器）也能扫到")
        page.set_content(
            "<h2>章节测验</h2>"
            '<div class="options">'
            '<div class="option">A. 甲</div><div class="option">B. 乙</div>'
            "</div>"
            "<button>提交试卷</button>"
        )
        info = quiz._scan(page)
        failures += not check("扫描到整页测验", bool(info))
        failures += not check("识别为 page 类型", bool(info) and info["kind"] == "page")
        failures += not check(
            "识别到 2 个选项", bool(info) and info["optionCount"] == 2
        )
        failures += not check("章节测验判定成立", quiz.is_chapter_quiz(page) is True)
        page.set_content('<h2>章节测验</h2><div class="options"><div class="option">A</div></div>')
        failures += not check(
            "没有提交按钮的页面不会被误认成测验", quiz._scan(page) is None
        )

        print("16. 章节测验条目的名称提取")
        page.set_content(
            '<div class="catalogue"><div class="item-test">'
            '<span class="item-test-left-name">第一章 章节测验</span>'
            '<span class="item-test-right">去完成</span></div>'
            '<div class="item-test"><span class="item-test-right">去完成</span></div>'
            "</div>"
        )
        items = page.locator(".item-test")
        failures += not check(
            "优先取左侧名称", catalog_mod.chapter_item_title(items.nth(0)) == "第一章 章节测验"
        )
        failures += not check(
            "只有按钮文案时会把「去完成」去掉",
            catalog_mod.chapter_item_title(items.nth(1)) == "章节测验",
        )
        info = catalog_mod.chapter_item_info(items.nth(0))
        failures += not check("条目信息带 HTML 片段", "item-test" in info.get("html", ""))

        print("17. 「仅章节测验」模式不落盘（下次打开默认刷课）")
        from zhskiller.config import Config as _RealConfig
        import json as _json
        import tempfile as _tempfile

        tmp_dir = Path(_tempfile.mkdtemp(prefix="zhs-mode-"))
        cfg_path = tmp_dir / "config.json"
        cfg = _RealConfig(cfg_path)
        cfg.set("run_mode", "chapter_only")
        cfg.save()
        failures += not check(
            "内存里仍是 chapter_only", cfg.get("run_mode") == "chapter_only"
        )
        failures += not check(
            "文件里写的是 watch",
            _json.loads(cfg_path.read_text(encoding="utf-8"))["run_mode"] == "watch",
        )
        failures += not check(
            "重新加载后是 watch", _RealConfig(cfg_path).get("run_mode") == "watch"
        )

        print("18. 整页试卷（章节测验）：结构识别 + 翻页作答全流程")
        from zhskiller import exam as exam_mod

        fixture = Path(__file__).resolve().parent / "fixtures" / "exam_page.html"
        page.goto(fixture.as_uri(), wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(400)
        failures += not check("识别出是整页试卷", exam_mod.is_exam_page(page))
        from zhskiller import diagnostics as diag_mod

        failures += not check(
            "无障碍树也能判定出这是试卷页",
            diag_mod.looks_like_exam_via_accessibility(page),
        )
        question = exam_mod.read_question(page)
        failures += not check("读到当前题目", bool(question))
        if question:
            failures += not check("题目总数是 3", question.get("total") == 3)
            failures += not check(
                "题型识别为单选题", "单选题" in (question.get("type") or "")
            )
            failures += not check("识别到 4 个选项", len(question["options"]) == 4)
            failures += not check(
                "选项字母正确",
                [o["letter"] for o in question["options"]] == ["A.", "B.", "C.", "D."],
            )
            failures += not check(
                "题干读到了", "组合逻辑电路" in (question.get("stem") or "")
            )
            failures += not check(
                "第 1 题题干来自 DOM", question.get("stemSource") == "dom"
            )
            failures += not check(
                "选项正文取到了",
                "仅与当前输入有关" in question["options"][0]["text"],
            )
            failures += not check(
                "选中态默认都是未选",
                all(o["checked"] is False for o in question["options"]),
            )
        # 完整流程：逐题作答 + 翻页
        config_exam = _Config({"quiz.chapter_mode": "random"})
        card = exam_mod.read_answer_card(page)
        failures += not check(
            "读到答题卡：3 题、完成率 0%",
            len(card["items"]) == 3 and exam_mod.card_rate(card) == 0,
        )
        # 先单独验证第 2 题（题干在 closed shadow root 里）
        page.evaluate("() => window.__show(1)")
        shadow_question = exam_mod.read_question(page)
        failures += not check(
            "closed shadow root 里的题干被无障碍树读出来了",
            "时序逻辑电路" in (shadow_question.get("stem") or ""),
        )
        failures += not check(
            "题干来源标记为 accessibility",
            shadow_question.get("stemSource") == "accessibility",
        )
        page.evaluate("() => window.__show(0)")
        handled = exam_mod.handle_exam_page(
            page, config_exam, _Logger(), _Bridge(), None, "chapter", False
        )
        failures += not check("报告已处理试卷", handled)
        answers = page.evaluate("() => window.__answers")
        failures += not check(
            f"三题都作答了（实际 {sorted(answers.keys())}）",
            sorted(answers.keys()) == ["0", "1", "2"],
        )
        failures += not check(
            "单选题只选了一个", len(answers.get("0", [])) == 1
        )
        failures += not check(
            "判断题只选了一个", len(answers.get("1", [])) == 1
        )
        failures += not check(
            "多选题选了多个", len(answers.get("2", [])) >= 2
        )
        failures += not check(
            "没勾自动提交时没有提交", page.evaluate("() => window.__submitted") is False
        )
        failures += not check(
            "没勾自动提交时点了「保存」（最后一题的按钮）保存答案",
            bool(page.evaluate("() => window.__saved")),
        )
        failures += not check(
            "没有误点「暂存作业」（它会弹确认框并关掉标签页）",
            page.evaluate("() => window.__draft") is False,
        )
        card_after = exam_mod.read_answer_card(page)
        failures += not check(
            "答题卡完成率变成 100%",
            exam_mod.card_rate(card_after) == 100,
        )
        # 完成率 100% 且不复查 → 直接跳过（不该再动它）
        page.evaluate("() => { window.__show(0); window.__submitted = false; }")
        result = exam_mod.handle_exam_page(
            page, _Config({"quiz.chapter_mode": "random"}), _Logger(), _Bridge(),
            None, "chapter", True, review=False,
        )
        failures += not check(
            "完成率 100% 且不复查 → 直接跳过", result == "skipped"
        )
        failures += not check(
            "跳过时不会提交", page.evaluate("() => window.__submitted") is False
        )

        # 复查模式：逐题重选，并按设置提交
        page.evaluate("() => { window.__show(0); window.__saved = false; }")
        result = exam_mod.handle_exam_page(
            page, _Config({"quiz.chapter_mode": "random"}), _Logger(), _Bridge(),
            None, "chapter", True, review=True,
        )
        failures += not check("复查模式会重新作答", result == "done")
        failures += not check(
            "复查后仍点「保存」", bool(page.evaluate("() => window.__saved"))
        )
        failures += not check(
            "勾了自动提交时会提交", bool(page.evaluate("() => window.__submitted"))
        )
        page.set_content("<div>空页面</div>")
        failures += not check(
            "普通页面不会被当成试卷", exam_mod.is_exam_page(page) is False
        )

        print("19. 答题卡异步渲染：不能把「还没加载出来」当成 0%")
        page.set_content(
            '<div class="answerCard">'
            '<h3 class="percentage_tit"><span>完成率</span><em class="fr"><i></i></em></h3>'
            '<div class="answerCard_list"><ul><li>1</li><li>2</li></ul></div>'
            "</div>"
            "<script>"
            "setTimeout(function(){"
            "  document.querySelectorAll('.answerCard_list li')"
            "    .forEach(function(li){ li.className = 'green'; });"
            "  document.querySelector('.percentage_tit em i').textContent = '100';"
            "}, 1500);"
            "</script>"
        )
        card = exam_mod.wait_card_ready(page, _Logger(), _Bridge(), timeout=10)
        failures += not check(
            "等到稳定后完成率是 100%", exam_mod.card_rate(card) == 100
        )
        failures += not check(
            "两题都识别为已作答", all(item["done"] for item in card["items"])
        )

        print("20. 章节目录被折叠时能自动展开（随堂练习会导致这个状态）")
        page.set_content(
            '<div class="side-expand-box"><svg class="collapse-btn"></svg></div>'
            '<div class="wisdom-category-box hide-box">'
            '<div class="child-info hasvideo"></div></div>'
            "<script>"
            "document.querySelector('.side-expand-box')"
            "  .addEventListener('click', function(){"
            "    document.querySelector('.wisdom-category-box')"
            "      .classList.remove('hide-box');"
            "    window.__expanded = true;"
            "  });"
            "</script>"
        )
        failures += not check(
            "检测到折叠并自动展开",
            catalog_mod.ensure_visible(page, site.WISDOM, _Logger()) is True,
        )
        failures += not check(
            "确实点到了展开按钮", bool(page.evaluate("() => window.__expanded"))
        )
        page.set_content('<div class="wisdom-category-box"><div class="child-info hasvideo"></div></div>')
        failures += not check(
            "本来就没折叠时不会乱点",
            catalog_mod.ensure_visible(page, site.WISDOM, _Logger()) is True,
        )
        # 回归：用户手动收起侧边栏时 hide-box 加在外层 <aside> 上，
        # 只看容器自己的 class 会漏判，条目点不动、程序卡住。
        page.set_content(
            '<aside class="el-aside a-side hide-box animate-css">'
            '<div class="side-wrapper"><div class="wisdom-category-box">'
            '<div class="category-wrapper"><div class="child-info hasvideo"></div>'
            "</div></div></div></aside>"
            '<div class="side-expand-box animated-box show">'
            '<svg class="svg-icon collapse-btn"></svg></div>'
        )
        # set_content 第二次写同一页面时内联 <script> 不会执行，这里显式注入
        page.evaluate(
            """() => {
                document.querySelector('.side-expand-box')
                    .addEventListener('click', () => {
                        document.querySelector('aside.el-aside')
                            .classList.remove('hide-box');
                        window.__asideExpanded = true;
                    });
            }"""
        )
        failures += not check(
            "识别出「hide-box 加在外层 aside」的折叠",
            catalog_mod.catalog_hidden_reason(page) == "hide-box",
        )
        failures += not check(
            "这种情况也能自动展开",
            catalog_mod.ensure_visible(page, site.WISDOM, _Logger()) is True
            and bool(page.evaluate("() => window.__asideExpanded")),
        )

        print("20b. 试卷页弹「网络异常」时自动点确定并继续（回归：曾卡到超时）")
        # 真实结构（来自用户导出的 unknown-exam-*.html）：Element-UI 的
        # .el-message-box + 「确定」按钮；不点掉它试卷永远加载不出来。
        page.set_content(
            '<div class="examPaper_box"><div class="exam-loading">正在加载中</div></div>'
            '<div tabindex="-1" role="dialog" aria-modal="true" aria-label="提示" '
            'class="el-message-box__wrapper" style="z-index: 2001;">'
            '<div class="el-message-box" style="width:420px;height:150px">'
            '<div class="el-message-box__header"><div class="el-message-box__title">'
            "<span>提示</span></div></div>"
            '<div class="el-message-box__content">'
            '<div class="el-message-box__message"><p><div>网络异常，请稍后再试，'
            "错误代码0-979</div></p></div></div>"
            '<div class="el-message-box__btns">'
            '<button type="button" class="el-button el-button--primary '
            'confirmButtonClass"><span>确定</span></button>'
            "</div></div></div>"
        )
        # 点「确定」后：提示框消失 + 试卷题目才渲染出来（模拟网络恢复）
        page.evaluate(
            """() => {
                window.__errorConfirmed = false;
                const btn = document.querySelector('.el-message-box__btns button');
                btn.addEventListener('click', () => {
                    window.__errorConfirmed = true;
                    document.querySelector('.el-message-box__wrapper').remove();
                    const paper = document.querySelector('.examPaper_box');
                    const subject = document.createElement('div');
                    subject.className = 'examPaper_subject';
                    subject.innerHTML = '<div class="subject_type">【单选题】</div>';
                    paper.appendChild(subject);
                });
            }"""
        )
        failures += not check(
            "识别出「网络异常」提示框",
            popups.has_error_dialog(page) is True,
        )
        from zhskiller import exam as exam_mod2

        ready = exam_mod2.wait_ready(page, _Logger(), _Bridge(), timeout=12)
        failures += not check("点确定后试卷能正常渲染出来", ready is True)
        failures += not check(
            "确实是程序点的「确定」",
            bool(page.evaluate("() => window.__errorConfirmed")),
        )

        print("22. 易盾滑块：@2x 图片的位移与缩放（回归：曾报「尺寸读取失败」）")
        import base64

        import cv2
        import numpy as np

        from zhskiller import captcha as cap_mod

        def png(w: int, h: int, color) -> str:
            img = np.zeros((h, w, 3), np.uint8)
            img[:] = color
            ok, buf = cv2.imencode(".png", img)
            return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()

        # 页面按真实结构搭：背景 @2x（800x400）显示成 400x200，拼图块贴在左边
        page.set_content(
            '<div class="yidun_bgimg" style="position:relative;width:400px;height:200px">'
            f'<img class="yidun_bg-img" src="{png(800, 400, (200, 200, 200))}" '
            'style="width:400px;height:200px">'
            f'<img class="yidun_jigsaw" src="{png(80, 80, (30, 30, 30))}" '
            'style="position:absolute;left:0;top:100px;width:40px;height:40px">'
            "</div>"
            '<div class="yidun_slider" style="width:40px;height:40px"></div>'
        )
        # data: URL 走不了 Playwright 的 request，这里直接解出图片字节
        original_download = cap_mod._download

        def fake_download(_scope, url):
            raw = base64.b64decode(str(url).split(",", 1)[1])
            return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)

        cap_mod._download = fake_download
        try:
            geometry = cap_mod._match_offset(page)
            delta, scale = geometry["delta"], geometry["scale"]
            failures += not check(
                "算出了位移（不再是尺寸读取失败）",
                isinstance(delta, float) and delta == delta,
            )
            failures += not check(
                "@2x 背景图的缩放比例约 0.5", abs(scale - 0.5) < 0.01
            )
            failures += not check(
                "返回了缺口位置等调试信息",
                {"gap_left", "piece_left", "bg_width"} <= set(geometry),
            )
        except Exception as exc:
            print(f"  [失败] 抛异常：{type(exc).__name__}: {exc}")
            failures += 1
        finally:
            cap_mod._download = original_download

        # 登录页的人机验证在 iframe 里，scope 是 Frame；下载图片必须也能拿到上下文
        page.set_content('<iframe srcdoc="<p>x</p>"></iframe>')
        inner = page.frames[-1]
        try:
            cap_mod._download(inner, "https://example.invalid/a.png")
            context_ok = True
        except AttributeError:
            context_ok = False
        except Exception as exc:
            context_ok = "无法获取浏览器上下文" not in str(exc)
        failures += not check("iframe（Frame）里也能拿到浏览器上下文", context_ok)

        print("23. 易盾滑块：闭环拖动（回归：曾每次都差 1cm）")
        # 造一个"拼图块只跟着鼠标走 0.8 倍"的滑块 —— 这正是用户遇到的
        # "每次都差一截"的形态。旧版按 1:1 一把拖到底，必然停在缺口左边；
        # 新版会先小拖一段量出真实比例，再把剩下的距离补准。
        drag_ratio, gap = 0.8, 180
        page.set_content(
            '<div class="yidun_popup"><div class="yidun_modal">'
            '<div class="yidun_bgimg" style="position:relative;width:400px;height:200px">'
            f'<img class="yidun_bg-img" src="{png(800, 400, (200, 200, 200))}" '
            'style="width:400px;height:200px">'
            f'<img class="yidun_jigsaw" src="{png(80, 80, (30, 30, 30))}" '
            'style="position:absolute;left:0;top:80px;width:40px;height:40px;'
            'transition:left .12s linear">'
            "</div>"
            '<div class="yidun_slider" style="position:absolute;left:100px;top:300px;'
            'width:40px;height:40px;background:#37c"></div>'
            "</div></div>"
        )
        # 注意：set_content 第二次往同一个页面写 HTML 时内联 <script> 不会执行，
        # 所以这里显式用 evaluate 注入假平台的逻辑。
        page.evaluate(
            """
            ([ratio, gap]) => {
                const piece = document.querySelector('.yidun_jigsaw');
                const slider = document.querySelector('.yidun_slider');
                let down = null;
                slider.addEventListener('mousedown', e => { down = e.clientX; });
                document.addEventListener('mousemove', e => {
                    if (down === null) return;
                    piece.style.left = Math.max(0, (e.clientX - down) * ratio) + 'px';
                });
                document.addEventListener('mouseup', () => {
                    if (down === null) return;
                    down = null;
                    const left = parseFloat(piece.style.left) || 0;
                    window.__finalPieceLeft = left;
                    if (Math.abs(left - gap) <= 5) {
                        document.querySelector('.yidun_popup').remove();
                    } else {
                        piece.style.left = '0px';
                    }
                });
            }
            """,
            [drag_ratio, gap],
        )
        bg_left = page.locator(".yidun_bgimg").bounding_box()["x"]
        fake_geometry = {
            "delta": float(gap),
            "scale": 0.5,
            "gap_left": bg_left + gap,
            "piece_left": bg_left,
            "bg_left": bg_left,
            "bg_width": 400.0,
            "natural_width": 800,
            "match_x": gap * 2,
        }
        original_match = cap_mod._match_offset
        cap_mod._match_offset = lambda _scope, _g=fake_geometry: dict(_g)
        try:
            solved = cap_mod.try_solve_slider(page, _Logger(), attempts=1, offset=0.0)
        finally:
            cap_mod._match_offset = original_match
        failures += not check("闭环校正后滑块通过", solved is True)
        final_left = page.evaluate("() => window.__finalPieceLeft")
        failures += not check(
            f"拼图块真的对上了缺口（鼠标:拼图块 = 1:{drag_ratio}、且拼图块带滞后动画）",
            isinstance(final_left, (int, float)) and abs(final_left - gap) <= 3,
        )

        print("24. 易盾滑块：位置对准了但平台要求再往后 6px（回归：每次都差一点）")
        # 这一把故意"图上对准了平台却不认"，只有再往后 6px 才算过 —— 复现用户
        # 描述的"总是差 1~2mm"。程序应该发现"位置已经对准却被判失败"，下一轮
        # 自动把目标整体后移，而不是一直按同一个位置重复失败。
        drag_ratio, gap, need_extra = 0.8, 180, 6
        page.set_content(
            '<div class="yidun_popup"><div class="yidun_modal">'
            '<div class="yidun_bgimg" style="position:relative;width:400px;height:200px">'
            f'<img class="yidun_bg-img" src="{png(800, 400, (200, 200, 200))}" '
            'style="width:400px;height:200px">'
            f'<img class="yidun_jigsaw" src="{png(80, 80, (30, 30, 30))}" '
            'style="position:absolute;left:0;top:80px;width:40px;height:40px">'
            "</div>"
            '<div class="yidun_slider" style="position:absolute;left:100px;top:300px;'
            'width:40px;height:40px;background:#37c"></div>'
            '<div class="yidun_tips" style="display:none">拖动滑块完成拼图</div>'
            "</div></div>"
        )
        page.evaluate(
            """
            ([ratio, gap, extra]) => {
                const required = gap + extra;
                const piece = document.querySelector('.yidun_jigsaw');
                const slider = document.querySelector('.yidun_slider');
                const tips = document.querySelector('.yidun_tips');
                let down = null;
                window.__attempts = 0;
                slider.addEventListener('mousedown', e => { down = e.clientX; });
                document.addEventListener('mousemove', e => {
                    if (down === null) return;
                    piece.style.left = Math.max(0, (e.clientX - down) * ratio) + 'px';
                });
                document.addEventListener('mouseup', () => {
                    if (down === null) return;
                    down = null;
                    window.__attempts++;
                    const left = parseFloat(piece.style.left) || 0;
                    window.__lastLeft = left;
                    if (Math.abs(left - required) <= 5) {
                        document.querySelector('.yidun_popup').remove();
                    } else {
                        piece.style.left = '0px';
                        tips.style.display = 'block';
                        tips.textContent = '验证失败，请重新拖动滑块';
                    }
                });
            }
            """,
            [drag_ratio, gap, need_extra],
        )
        bg_left = page.locator(".yidun_bgimg").bounding_box()["x"]
        fake_geometry = {
            "delta": float(gap),
            "scale": 0.5,
            "gap_left": bg_left + gap,
            "piece_left": bg_left,
            "bg_left": bg_left,
            "bg_width": 400.0,
            "natural_width": 800,
            "match_x": gap * 2,
        }
        cap_mod._match_offset = lambda _scope, _g=fake_geometry: dict(_g)
        slider_log = _Logger()
        try:
            solved = cap_mod.try_solve_slider(page, slider_log, attempts=2, offset=0.0)
        finally:
            cap_mod._match_offset = original_match
        if not solved:
            for line in slider_log.lines:
                print(f"      {line}")
        failures += not check("自动把目标后移后通过", solved is True)
        failures += not check(
            f"第一次按缺口位置失败、第二次按缺口+{need_extra}px 通过",
            page.evaluate("() => window.__attempts") == 2
            and abs(page.evaluate("() => window.__lastLeft") - (gap + need_extra)) <= 5,
        )

        print("25. 过期日志自动清理（运行程序时顺手删掉时间久远的日志）")
        import os
        import tempfile
        import time as time_mod

        from zhskiller import logger as logger_mod

        with tempfile.TemporaryDirectory() as tmp:
            fake_logs = Path(tmp)
            old_log = fake_logs / "run-19990101-000000.log"
            old_dump = fake_logs / "unknown-exam-19990101-000000.html"
            fresh_log = fake_logs / "run-29990101-000000.log"
            other_file = fake_logs / "notes.txt"
            for item in (old_log, old_dump, fresh_log, other_file):
                item.write_text("x", encoding="utf-8")
            stale = time_mod.time() - 30 * 86400
            for item in (old_log, old_dump):
                os.utime(item, (stale, stale))
            original_logs_dir = logger_mod.paths.logs_dir
            logger_mod.paths.logs_dir = lambda: fake_logs
            try:
                removed = logger_mod.cleanup_old_logs(days=7, keep_max=1000)
            finally:
                logger_mod.paths.logs_dir = original_logs_dir
            failures += not check("超过 7 天的日志与导出被删掉",
                                  not old_log.exists() and not old_dump.exists())
            failures += not check("最近的日志保留", fresh_log.exists())
            failures += not check("不碰非日志文件", other_file.exists())
            failures += not check("返回删除数量", removed == 2)

        print("26. run.bat 必须是纯 ASCII + CRLF（回归：中文/裸 LF 会让 cmd 报错）")
        bat_path = Path(__file__).resolve().parents[1] / "run.bat"
        bat_bytes = bat_path.read_bytes()
        failures += not check("run.bat 存在", bat_path.is_file())
        failures += not check(
            "没有 BOM（cmd 会把 BOM 当成命令的一部分）",
            not bat_bytes.startswith(b"\xef\xbb\xbf"),
        )
        failures += not check(
            "全部是 ASCII（批处理里写中文会被 cmd 按代码页读错）",
            all(byte < 128 for byte in bat_bytes),
        )
        failures += not check(
            "用 CRLF 换行（裸 LF 会让 cmd 读串行，实测把 chcp 65001 断成 01）",
            b"\r\n" in bat_bytes and b"\n" not in bat_bytes.replace(b"\r\n", b""),
        )

        print("27. 仅答题模式：让视频保持暂停的看门狗（回归：视频偷播→弹随堂练习→卡死）")
        page.set_content('<video id="v"></video>')
        page.evaluate(
            """() => {
                window.__pauseCalls = 0;
                const video = document.querySelector('#v');
                Object.defineProperty(video, 'paused', {get: () => false});
                const original = HTMLMediaElement.prototype.pause;
                HTMLMediaElement.prototype.pause = function () {
                    window.__pauseCalls += 1;
                    return original.apply(this, arguments);
                };
            }"""
        )
        player.install_keep_video_paused(page)
        failures += not check(
            "装上后先把正在播的视频按停",
            page.evaluate("() => window.__pauseCalls") >= 1,
        )
        page.evaluate(
            "() => document.querySelector('#v').dispatchEvent(new Event('play'))"
        )
        failures += not check(
            "站点再想自动续播也会被按停",
            page.evaluate("() => window.__pauseCalls") >= 2,
        )
        before_tick = page.evaluate("() => window.__pauseCalls")
        page.wait_for_timeout(1300)
        failures += not check(
            "定时兜底扫描仍在工作",
            page.evaluate("() => window.__pauseCalls") > before_tick,
        )
        player.release_keep_video_paused(page)
        after_release = page.evaluate("() => window.__pauseCalls")
        page.wait_for_timeout(1300)
        failures += not check(
            "切回刷视频模式后不再干扰播放",
            page.evaluate("() => window.__pauseCalls") == after_release
            and page.evaluate("() => !window.__zhsKeepPaused"),
        )

        print("28. AI 作答：先重试 5 次，别一失败就改随机")
        from zhskiller import ai as ai_mod

        ai_log = _Logger()
        ai_client = ai_mod.AiClient(
            {"base_url": "https://example.invalid/v1", "api_key": "k", "model": "m"},
            ai_log,
        )
        original_backoff = ai_mod.AI_RETRY_BACKOFF
        ai_mod.AI_RETRY_BACKOFF = 0.0        # 测试里不等退避
        calls = {"n": 0}

        def always_fail(system, prompt):
            calls["n"] += 1
            raise RuntimeError("The read operation timed out")

        try:
            ai_client._chat_with_system = always_fail
            result = ai_client.choose("题目", ["A选项", "B选项"])
            failures += not check("全部失败时返回 None（不改随机）", result is None)
            failures += not check(
                f"确实重试了 {ai_mod.AI_MAX_ATTEMPTS} 次",
                calls["n"] == ai_mod.AI_MAX_ATTEMPTS,
            )
            failures += not check(
                "记下了最后一次错误（弹窗里要显示原因）",
                "timed out" in ai_client.last_error,
            )
            # 第三次成功：应该拿到答案，且不再继续重试
            calls["n"] = 0

            def third_time_ok(system, prompt):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RuntimeError("timeout")
                return "B"

            ai_client._chat_with_system = third_time_ok
            result = ai_client.choose("题目", ["A选项", "B选项"])
            failures += not check("中途恢复就正常作答", result == 1 and calls["n"] == 3)
            # 冷却期内不再干等 AI
            ai_client.start_cooldown(60)
            calls["n"] = 0
            failures += not check(
                "冷却期内直接返回 None（由调用方随机作答）",
                ai_client.choose("题目", ["A选项", "B选项"]) is None
                and calls["n"] == 0
                and ai_client.cooling_down is True,
            )

            print("29. AI 用不了时的弹窗：可以选「继续用 AI」")
            asked = {}

            class _StubBridge:
                def __init__(self, answer):
                    self.answer = answer

                def ask(self, kind, payload=None, timeout=None):
                    asked["kind"] = kind
                    asked["payload"] = payload or {}
                    return self.answer

            fresh = ai_mod.AiClient(
                {"base_url": "https://x/v1", "api_key": "k", "model": "m"}, _Logger()
            )
            fresh.last_error = "timed out"
            decision = ai_mod.resolve_ai_failure(
                fresh, _StubBridge("retry_ai"), None, _Logger(), "chapter"
            )
            failures += not check("点「继续用 AI」→ 继续用 AI", decision == "ai")
            failures += not check(
                "弹窗里有「继续用 AI」和「改用手动作答」两个选项",
                asked["payload"].get("ok_text") == "继续用 AI"
                and asked["payload"].get("cancel_text") == "改用手动作答"
                and asked["payload"].get("ok_value") == "retry_ai",
            )
            failures += not check(
                "弹窗里列出了可能原因与最后错误",
                "续费" in asked["payload"].get("message", "")
                and "timed out" in asked["payload"].get("message", ""),
            )
            again = ai_mod.resolve_ai_failure(
                fresh, _StubBridge("retry_ai"), None, _Logger(), "chapter"
            )
            failures += not check("同一次运行里只问一次（不反复弹窗）", again == "random")
            video = ai_mod.AiClient(
                {"base_url": "https://x/v1", "api_key": "k", "model": "m"}, _Logger()
            )
            ai_mod.resolve_ai_failure(
                video, _StubBridge("random"), None, _Logger(), "in_video"
            )
            failures += not check(
                "随堂练习的兜底是「改用随机选择」",
                asked["payload"].get("cancel_text") == "改用随机选择"
                and asked["payload"].get("cancel_value") == "random",
            )
        finally:
            ai_mod.AI_RETRY_BACKOFF = original_backoff

        browser.close()
    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
