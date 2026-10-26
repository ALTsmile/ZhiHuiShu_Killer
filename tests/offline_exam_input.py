"""回归检查：新版作业试卷的完成率与真实鼠标点击。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from zhskiller import exam, examlist  # noqa: E402


class _Logger:
    def _log(self, _message: str) -> None:
        pass

    debug = _log
    info = _log
    warn = _log
    error = _log
    success = _log


class _Bridge:
    def check_stop(self) -> None:
        pass

    def sleep(self, _seconds: float) -> None:
        time.sleep(0.01)

    def post(self, *_args, **_kwargs) -> int:
        return 1


class _Config:
    def quiz_mode(self, _kind: str) -> str:
        return "manual"


DOM = """
<div class="examPaper_box">
  <div class="examPaper_subject">
    <div class="subject_stem">
      <div class="subject_type_describe">
        <div class="subject_num"><span>1.</span></div>
        <span class="subject_type"><span>【单选题】</span></span>
        <div class="subject_describe">这是第一题的题干</div>
      </div>
    </div>
    <div class="subject_node">
      <div class="nodeLab">
        <input type="radio" value="a">
        <span class="ABCase">A.</span>
        <div class="node_detail">选项 A</div>
      </div>
      <div class="nodeLab">
        <input type="radio" value="b">
        <span class="ABCase">B.</span>
        <div class="node_detail">选项 B</div>
      </div>
    </div>
  </div>
  <div class="examPaper_subject" style="display:none">
    <div class="subject_stem">
      <div class="subject_type_describe">
        <div class="subject_num"><span>2.</span></div>
        <span class="subject_type"><span>【判断题】</span></span>
        <div class="subject_describe">这是第二题的题干</div>
      </div>
    </div>
    <div class="subject_node">
      <div class="nodeLab">
        <input type="radio" value="a">
        <span class="ABCase">A.</span>
        <div class="node_detail">错</div>
      </div>
      <div class="nodeLab">
        <input type="radio" value="b">
        <span class="ABCase">B.</span>
        <div class="node_detail">对</div>
      </div>
    </div>
  </div>
  <h3 class="percentage_tit"><span>完成率</span><em><i>0</i>%</em></h3>
  <div class="answerCard_list">
    <ul class="answerCard_list1">
      <li class="green">1</li>
      <li class="green">2</li>
    </ul>
  </div>
  <div class="switch-btn-box">
    <button type="button"><span>下一题</span></button>
  </div>
</div>
"""


def main() -> int:
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 760})
        page.set_content(DOM)

        card = exam.read_answer_card(page)
        items = card.get("items") or []
        rate_zero_ok = (
            exam.card_rate(card) == 0
            and len(items) == 2
            and all(not item["done"] for item in items)
        )
        print(
            "  [通过] 完成率 0% 时，题号全绿也不会被误判为已作答"
            if rate_zero_ok
            else "  [失败] 完成率 0% 时仍把全绿题号判成已作答"
        )
        failures += not rate_zero_ok

        page.evaluate(
            """() => {
                window.__trusted = [];
                document.addEventListener('click', (event) => {
                    if (event.target.closest?.('.nodeLab')
                        || event.target.closest?.('.answerCard_list1 li')
                        || event.target.closest?.('button')) {
                        window.__trusted.push(event.isTrusted);
                    }
                }, true);
            }"""
        )
        selected = exam.click_option(page, 0, 0)
        jumped = exam.jump_to_question(page, 1)
        clicked = exam._click_button_by_text(page, ("下一题",), _Logger())
        trusted = page.evaluate("() => window.__trusted")
        click_ok = selected and jumped and clicked and bool(trusted) and all(trusted)
        print(
            "  [通过] 试卷选项、题号跳转、下一题都使用真实鼠标点击"
            if click_ok
            else "  [失败] 试卷关键点击失败或不是可信事件："
            f"选项={selected} 跳题={jumped} 下一题={clicked} 事件={trusted}"
        )
        failures += not click_ok

        bridge = _Bridge()
        result = exam.handle_exam_page(
            page,
            config=_Config(),
            logger=_Logger(),
            bridge=bridge,
            ai=None,
            examine="chapter",
            auto_submit=False,
            review=True,
        )
        # 这里只验证完成率 0% 时至少会进入逐题队列，而不是一进来就跳过。
        entered_queue = result == "done"
        print(
            "  [通过] 复查模式会处理完成率 0% 的全绿试卷"
            if entered_queue
            else "  [失败] 复查模式没有处理完成率 0% 的试卷"
        )
        failures += not entered_queue

        list_page = browser.new_page(viewport={"width": 1000, "height": 760})
        list_url = (
            "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
            "#/webExamList?recruitId=fixture"
        )
        list_page.route(
            "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="""
                <a class="jobExamComBtn" data-zhs-start="0">
                  <span class="themeBg">开始答题</span>
                </a>
                <script>
                window.__startTrusted = [];
                document.querySelector('.themeBg').addEventListener('click', (event) => {
                    window.__startTrusted.push(event.isTrusted);
                    location.hash = '#/webExamList/dohomework/1/2/3/4/5/0';
                });
                </script>
                """,
            ),
        )
        list_page.goto(list_url, wait_until="domcontentloaded")
        opened = examlist.start_item(
            list_page,
            {"index": 0, "title": "第八章单元测试"},
            _Logger(),
            _Bridge(),
            timeout=3,
        )
        start_trusted = list_page.evaluate("() => window.__startTrusted")
        start_ok = (
            opened is list_page
            and "dohomework" in list_page.url
            and bool(start_trusted)
            and all(start_trusted)
        )
        print(
            "  [通过] 「开始答题」会真实点击内层按钮并验证进入试卷路由"
            if start_ok
            else "  [失败] 「开始答题」没有完成可信点击或未进入试卷路由："
            f"返回={opened is list_page} URL={list_page.url} 事件={start_trusted}"
        )
        failures += not start_ok

        delayed_url = (
            "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"
            "#/webExamList/dohomework/1/2/3/4/5/0"
        )
        delayed_page = browser.new_page(viewport={"width": 1000, "height": 760})
        delayed_body = (
            '<div id="app"></div><script>'
            f"setTimeout(() => {{ document.getElementById('app').innerHTML = "
            f"{json.dumps(DOM)}; }}, 700);"
            "</script>"
        )
        delayed_page.route(
            "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=delayed_body,
            ),
        )
        delayed_page.goto(delayed_url, wait_until="domcontentloaded")
        delayed_result = exam.handle_exam_page(
            delayed_page,
            config=_Config(),
            logger=_Logger(),
            bridge=_Bridge(),
            ai=None,
            examine="chapter",
            auto_submit=False,
            review=True,
        )
        delayed_ok = delayed_result == "done"
        print(
            "  [通过] 试卷页会等待单页应用延迟渲染后再识别题目"
            if delayed_ok
            else "  [失败] 试卷页渲染为空时被过早判定为无法识别"
        )
        failures += not delayed_ok

        browser.close()

    print()
    print("全部通过 ✅" if failures == 0 else f"有 {failures} 项失败 ❌")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
