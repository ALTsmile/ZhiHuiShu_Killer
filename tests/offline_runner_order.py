"""回归检查：仅答题模式进入章节测验前，作答选项必须已经初始化。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zhskiller import examlist, runner as runner_mod  # noqa: E402


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)

    warn = info
    error = info
    debug = info
    success = info


class _Bridge:
    def check_stop(self) -> None:
        pass

    def sleep(self, _seconds: float) -> None:
        pass

    def status(self, _message: str) -> None:
        pass


class _Config:
    def __init__(self, auto_chapter_quiz: bool) -> None:
        self.data = {
            "quiz": {
                "auto_chapter_quiz": auto_chapter_quiz,
                "auto_submit_chapter": True,
                "review_chapter": True,
            }
        }

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def quiz_mode(self, _kind: str) -> str:
        return "ai"


class _Page:
    def evaluate(self, _script: str):
        return True


class _StartButton:
    def count(self) -> int:
        return 1

    def bounding_box(self) -> dict:
        return {"x": 10, "y": 10, "width": 100, "height": 30}


class _StartLocator:
    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def bounding_box(self) -> dict:
        return {"x": 10, "y": 10, "width": 100, "height": 30}


class _Mouse:
    def click(self, *_args) -> None:
        pass


class _ListPage:
    url = (
        "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
        "#/webExamList?recruitId=example"
    )

    def __init__(self) -> None:
        self.mouse = _Mouse()
        self.context = SimpleNamespace(pages=[self])

    def locator(self, _selector: str):
        return _StartLocator()


def _make_runner(config: _Config, bridge: _Bridge, logger: _Logger):
    review_runner = object.__new__(runner_mod.Runner)
    review_runner.config = config
    review_runner.bridge = bridge
    review_runner.logger = logger
    review_runner.ai = SimpleNamespace(ready=True)
    return review_runner


def main() -> int:
    page = _Page()
    bridge = _Bridge()
    logger = _Logger()
    url = "https://example.invalid/course"
    catalog = SimpleNamespace(key="standard")
    replacements = (
        (runner_mod.login, "safe_goto", lambda *_args: None),
        (
            runner_mod.catalog_mod,
            "detect_with_recovery",
            lambda *_args, **_kwargs: catalog,
        ),
        (runner_mod.catalog_mod, "expand_folds", lambda *_args: 0),
        (runner_mod.catalog_mod, "ensure_visible", lambda *_args: True),
        (runner_mod.catalog_mod, "chapter_item_selector", lambda *_args: None),
        (runner_mod.popups, "close_announcements", lambda *_args: None),
        (runner_mod, "_pause_background_video", lambda *_args: None),
        (runner_mod.player_mod, "install_keep_video_paused", lambda *_args: None),
        (runner_mod.captcha, "has_verification", lambda *_args: False),
    )
    originals = [
        (owner, name, getattr(owner, name)) for owner, name, _replacement in replacements
    ]
    for owner, name, replacement in replacements:
        setattr(owner, name, replacement)

    failures = 0
    try:
        enabled = _make_runner(_Config(True), bridge, logger)
        fast_calls: list[tuple] = []

        def fast_enabled(*args):
            fast_calls.append(args)
            return True

        enabled._run_via_exam_list = fast_enabled
        try:
            enabled_result = enabled._run_chapter_tests(page, url, 1, 1)
        except Exception as exc:
            enabled_result = False
            print(f"  [失败] 自动完成章节测验路径崩溃：{type(exc).__name__}: {exc}")
            failures += 1
        else:
            passed = (
                enabled_result is True
                and len(fast_calls) == 1
                and fast_calls[0][-2:] == (True, True)
            )
            print(
                "  [通过] 自动完成章节测验：快速通道收到已初始化的提交/复查选项"
                if passed
                else "  [失败] 自动完成章节测验：快速通道参数不正确"
            )
            failures += not passed

        disabled = _make_runner(_Config(False), bridge, logger)

        def fast_disabled(*_args):
            raise AssertionError("未勾选自动完成章节测验时不应进入快速通道")

        disabled._run_via_exam_list = fast_disabled
        try:
            disabled_result = disabled._run_chapter_tests(page, url, 1, 1)
        except Exception as exc:
            disabled_result = False
            print(f"  [失败] 未勾选自动完成路径异常：{type(exc).__name__}: {exc}")
            failures += 1
        else:
            passed = disabled_result is True
            print(
                "  [通过] 未勾选自动完成：只清点章节测验，不进入答题流程"
                if passed
                else "  [失败] 未勾选自动完成路径返回值不正确"
            )
            failures += not passed
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)

    print()
    list_url = (
        "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
        "#/webExamList?recruitId=example"
    )
    exam_url = (
        "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
        "#/webExamList/dohomework/123456/example/paper"
    )
    url_check = (
        not examlist._is_exam_url(list_url)
        and examlist._is_exam_url(exam_url)
    )
    print(
        "  [通过] 作业列表与真正答题页的 URL 能区分"
        if url_check
        else "  [失败] 作业列表被误认为真正答题页"
    )
    failures += not url_check

    target = examlist.start_item(
        _ListPage(),
        {"index": 0, "title": "第八章单元测试"},
        logger,
        bridge,
        timeout=0.01,
    )
    start_check = target is None
    print(
        "  [通过] 点击未跳转时不会把列表页当成答题页"
        if start_check
        else "  [失败] 点击未跳转时返回了列表页自身"
    )
    failures += not start_check

    list_page = object()
    original_examlist = (
        examlist.open_list,
        examlist.read_pending,
        examlist.start_item,
        examlist.refresh,
    )
    rounds = 0

    def fake_open(*_args):
        return list_page

    def fake_pending(*_args):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            return [
                {"index": 0, "title": "第八章单元测试", "chapter": "第八章"},
                {"index": 1, "title": "第九章单元测试", "chapter": "第九章"},
            ]
        return []

    examlist.open_list = fake_open
    examlist.read_pending = fake_pending
    examlist.start_item = lambda *_args, **_kwargs: None
    examlist.refresh = lambda *_args, **_kwargs: None
    try:
        list_runner = _make_runner(_Config(True), bridge, logger)
        list_runner.netlog = None
        failed_result = list_runner._run_via_exam_list(
            page, url, list_runner.config, logger, bridge, True, True
        )
    finally:
        (
            examlist.open_list,
            examlist.read_pending,
            examlist.start_item,
            examlist.refresh,
        ) = original_examlist
    failed_check = failed_result is False
    print(
        "  [通过] 两条测试都失败时不会返回全部完成"
        if failed_check
        else "  [失败] 两条测试都失败却返回了成功"
    )
    failures += not failed_check

    print()
    stale_page = object()
    open_count = {"pending": 0, "starts": [], "refreshes": 0}
    original_examlist = (
        examlist.open_list,
        examlist.read_pending,
        examlist.start_item,
        examlist.refresh,
    )

    def sequence_open(*_args):
        return stale_page

    def sequence_pending(*_args):
        open_count["pending"] += 1
        if open_count["pending"] == 1:
            return [
                {"index": 0, "title": "第八章单元测试", "chapter": "第八章"},
                {"index": 1, "title": "第九章单元测试", "chapter": "第九章"},
            ]
        if open_count["pending"] == 2:
            return [
                {"index": 0, "title": "第九章单元测试", "chapter": "第九章"},
            ]
        return []

    class _Target:
        def close(self):
            pass

    target_page = _Target()

    def sequence_start(_page, item, *_args):
        open_count["starts"].append((item["chapter"], item["index"]))
        return target_page if item["index"] == 0 else None

    def sequence_refresh(*_args):
        open_count["refreshes"] += 1

    examlist.open_list = sequence_open
    examlist.read_pending = sequence_pending
    examlist.start_item = sequence_start
    examlist.refresh = sequence_refresh
    try:
        sequence_runner = _make_runner(_Config(True), bridge, logger)
        sequence_runner.netlog = None
        sequence_runner._handle_exam_target = (
            lambda _target, **_kwargs: "done"
        )
        sequence_result = sequence_runner._run_via_exam_list(
            page, url, sequence_runner.config, logger, bridge, True, True
        )
    finally:
        (
            examlist.open_list,
            examlist.read_pending,
            examlist.start_item,
            examlist.refresh,
        ) = original_examlist

    sequence_ok = (
        sequence_result is True
        and open_count["starts"]
        == [("第八章", 0), ("第九章", 0)]
        and open_count["refreshes"] == 2
    )
    print(
        "  [通过] 提交一条后重新读取列表，不会用已经失效的旧序号"
        if sequence_ok
        else "  [失败] 刷新列表后仍用旧序号点击下一条："
        f"{open_count['starts']}"
    )
    failures += not sequence_ok

    print()
    class _CourseConfig:
        data = {"playback": {"review_when_finished": False}}

        def get(self, dotted: str, default=None):
            if dotted == "playback.min_minutes_per_course":
                return 25
            return default

    course_catalog = SimpleNamespace(
        key="fusion",
        label="融合共享课",
    )
    course_lessons = [object()]
    course_replacements = (
        (runner_mod.login, "safe_goto", lambda *_args: None),
        (
            runner_mod.catalog_mod,
            "detect_with_recovery",
            lambda *_args, **_kwargs: course_catalog,
        ),
        (runner_mod.catalog_mod, "expand_folds", lambda *_args: 0),
        (runner_mod.catalog_mod, "ensure_visible", lambda *_args: True),
        (runner_mod.catalog_mod, "course_title", lambda *_args: "测试课程"),
        (runner_mod.catalog_mod, "list_lessons", lambda *_args: course_lessons),
        (runner_mod.catalog_mod, "unfinished", lambda *_args: []),
        (runner_mod.popups, "close_announcements", lambda *_args: None),
        (runner_mod.captcha, "has_verification", lambda *_args: False),
    )
    course_originals = [
        (owner, name, getattr(owner, name))
        for owner, name, _replacement in course_replacements
    ]
    for owner, name, replacement in course_replacements:
        setattr(owner, name, replacement)
    played: list[object] = []
    course_error = ""
    try:
        course_runner = _make_runner(_CourseConfig(), bridge, logger)
        course_runner.session = None
        course_runner.stat_courses_skipped = 0
        course_runner.stat_lessons_done = 0
        course_runner.stat_lessons_failed = 0
        course_runner._play_one = lambda *_args, **_kwargs: played.append(True)
        try:
            course_result = course_runner._run_course(
                page, url, 1, 1
            )
        except Exception as exc:
            course_result = False
            course_error = f"{type(exc).__name__}: {exc}"
    finally:
        for owner, name, original in course_originals:
            setattr(owner, name, original)

    completed_course_ok = (
        course_result is True
        and not played
        and course_runner.stat_courses_skipped == 1
    )
    print(
        "  [通过] 未开复习时，已完成课程不会因残留的「刷够时长」重播"
        if completed_course_ok
        else "  [失败] 未开复习时，已完成课程仍被「刷够时长」触发重播"
        f"（结果={course_result}，播放={played}，异常={course_error}）"
    )
    failures += not completed_course_ok

    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
