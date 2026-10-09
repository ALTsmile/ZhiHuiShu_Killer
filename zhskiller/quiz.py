"""题目弹窗的处理：随堂练习 与 章节练习/测试。

这两者是完全不同的东西，处理策略也不同：

* **随堂练习**：每个视频都会弹，属于「必须过掉才能继续看」的关卡，
  对错不计入成绩，所以默认随机选一个就提交，目的是尽快把弹窗关掉。
* **章节练习 / 章节测试**：属于正式计分内容，**默认不自动作答**，
  只有用户勾选了「自动完成章节练习」才会动它。

新版共享课（studywisdomh5）AI 随堂练习的真实结构（从编译后的模板读出）：

    div.question
      div.question-container
        div.title        -> h1（题号/题型）+ 题干 + 多选标记
        div.options
          div.option     -> div.prefix（A/B/C…，选中时加 is-checked）+ div.text
    div.submit-btn       -> 「提交作答」，提交后消失
    div.answer-container -> 提交后出现：答案/正确答案/解析

所以流程是：每道题各点一个选项 -> 点「提交作答」-> 再关掉解析弹窗。
"""

from __future__ import annotations

import random
import time

from playwright.sync_api import Page

from . import site
from . import ai as ai_mod
from .ai import AiClient
from .bridge import Bridge
from .logger import Logger

MAX_ROUNDS = 40
# 连续这么多轮点了选项但弹窗还是没变化，就认为卡住了，交给用户
STUCK_ROUNDS = 4


_SCAN_JS = r"""
(args) => {
    const {roots, optionSelectors, submitSelectors, submitTexts,
           closeSelectors, groupSelectors, hintWords, aiDialog, topicTitle,
           aiNotice} = args;

    const isVisible = (el) => {
        if (!el) return false;
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (Number.parseFloat(style.opacity || '1') <= 0.05) return false;
        return rect.width > 0 && rect.height > 0;
    };
    // svg 图标自身可能没有尺寸（尺寸在父节点上），关按钮要用这个宽松版判断
    const isRenderable = (el) => {
        if (!el) return false;
        if (isVisible(el)) return true;
        const parent = el.parentElement;
        if (!parent) return false;
        const ps = getComputedStyle(parent);
        if (ps.display === "none" || ps.visibility === "hidden") return false;
        const pr = parent.getBoundingClientRect();
        return pr.width > 0 && pr.height > 0;
    };
    const flat = (el) => (((el && el.innerText) || "").split(/\s+/).join(" ")).trim();
    // 事件常常绑在里层的 span/svg 上（点外层容器没用），所以取最深的那个后代
    const deepestWithText = (root, wanted) => {
        let best = root;
        const walk = (node) => {
            for (const child of node.children) {
                if (flat(child).includes(wanted)) { best = child; walk(child); }
            }
        };
        walk(root);
        return best;
    };

    // ---------- 1. 找到题目弹窗的根节点 ----------
    let root = null;
    let kind = null;
    // 「AI 助教提示」弹窗的文案里也带"随堂练习"四个字，但它不是题目，
    // 只有右侧一个关闭按钮，必须先排除掉。
    const noticeRoot = document.querySelector(aiNotice);
    if (isVisible(noticeRoot)) return null;
    const aiRoot = document.querySelector(aiDialog);
    const topicRoot = document.querySelector(topicTitle);
    if (isVisible(aiRoot)) { root = aiRoot; kind = "ai_exercise"; }
    else if (isVisible(topicRoot)) { root = topicRoot; kind = "topic"; }
    else {
        const found = [];
        for (const selector of roots) {
            for (const el of document.querySelectorAll(selector)) {
                if (isVisible(el)) found.push(el);
            }
        }
        if (found.length) {
            root = found[found.length - 1];
            kind = "dialog";
        } else {
            // 整页形式的测验：没有弹窗容器，题目直接铺在页面上。
            // 为了不误判，必须同时满足"页面有选项"且"页面有提交按钮"。
            const body = document.body;
            const hasOptions = body && body.querySelector(optionSelectors.join(","));
            const submitTextsJoined = submitTexts.join("|");
            const hasSubmit = Array.from(body ? body.querySelectorAll(
                "button, .el-button, .btn, .submit-btn, [class*='submit']") : [])
                .some(el => submitTextsJoined.split("|").some(t => flat(el).includes(t)));
            if (!hasOptions || !hasSubmit) return null;
            root = body;
            kind = "page";
        }
    }

    // ---------- 2. 作用域：优先用 Element Plus 的遮罩层 ----------
    let scope = root.closest(".el-overlay") || root.closest(".el-overlay-dialog") || root;
    const optionJoin = optionSelectors.join(",");
    if (!scope.querySelector(optionJoin)) {
        let up = root;
        for (let i = 0; i < 4 && up.parentElement && up.parentElement !== document.body; i++) {
            up = up.parentElement;
            if (up.querySelector(optionJoin)) { scope = up; break; }
        }
    }
    if (kind === "dialog" && !hintWords.some((w) => flat(scope).includes(w))) {
        return null;
    }

    // ---------- 3. 收集选项并按题目分组 ----------
    const nodes = [];
    for (const selector of optionSelectors) {
        for (const el of scope.querySelectorAll(selector)) {
            if (!isVisible(el)) continue;
            if (nodes.some((other) => other.contains(el) || el.contains(other))) continue;
            nodes.push(el);
        }
    }
    const groupOf = (el) => {
        for (const selector of groupSelectors) {
            let parent = el.closest(selector);
            // 命中的是选项自己时（比如 <li class="topic-item"> 会被 "li"、
            // "[class*='topic']" 命中），说明这个选择器指的是"选项"而不是"题目"，
            // 继续往上找真正的题目容器，否则会把每个选项当成一道题。
            while (parent && parent === el && el.parentElement) {
                parent = el.parentElement.closest(selector);
            }
            if (parent && scope.contains(parent)) return parent;
        }
        return scope;
    };
    const groups = [];
    const groupIndex = new Map();
    for (const node of nodes) {
        const key = groupOf(node);
        let entry = groupIndex.get(key);
        if (!entry) {
            entry = {root: key, options: []};
            groupIndex.set(key, entry);
            groups.push(entry);
        }
        const marker = node.querySelector(
            ".class-question-select, .prefix, [class*='isSelect'], .is-checked");
        const checked = Boolean(
            node.querySelector(
                ".isSelect, [class*='isSelect'], .is-checked, [class*='checked'],"
                + " [aria-checked='true']")
            || node.className.includes("isSelect")
            || node.className.includes("is-checked")
        );
        const textNode = node.querySelector(
            ".answer, .text, .option-text, [class*='content']");
        entry.options.push({
            node,
            text: flat(textNode || node).slice(0, 300),
            checked,
            marker: marker ? flat(marker) : "",
        });
    }

    const questions = [];
    groups.forEach((group, groupPos) => {
        if (!group.options.length) return;
        // 题干：题目容器自己找不到时，往上/往同级找标题
        //（翻转课的弹题里 .topic-title 是 .topic-list 的兄弟节点）
        let stemNode = group.root.querySelector(
            ".title, .topic-title, .subject_describe, .subject_stem, .stem");
        if (!stemNode && group.root.parentElement) {
            stemNode = group.root.parentElement.querySelector(
                ".topic-title, .title, .subject_describe");
        }
        const stem = flat(stemNode || group.root).slice(0, 400);
        const typeNode = (stemNode || group.root).querySelector(
            ".title-tit, .subject_type, [class*='type']");
        const typeText = typeNode ? flat(typeNode) : stem;
        const answered = group.options.some((o) => o.checked)
            || Boolean(group.root.querySelector(
                ".analyze-box, .answer-container, .analyze"))
            || group.root.className.includes("done");
        group.options.forEach((option, optionPos) => {
            option.node.setAttribute("data-zhs-opt", groupPos + ":" + optionPos);
        });
        questions.push({
            text: stem,
            // 多选题要一次选多个：靠题干里的【多选题】标记判断
            multi: /多选|不定项|multiple/.test(typeText),
            answered,
            options: group.options.map((o) => ({
                tag: o.node.getAttribute("data-zhs-opt"),
                text: o.text,
                marker: o.marker,
            })),
        });
    });

    // ---------- 4. 提交按钮 ----------
    let submit = null;
    // 已经提交过（.done 出现）就不再点提交，交给关闭逻辑收尾
    const doneNode = scope.querySelector(".done");
    for (const selector of submitSelectors) {
        const candidates = Array.from(scope.querySelectorAll(selector)).filter(isVisible);
        for (const wanted of submitTexts) {
            const hit = candidates.find((el) => flat(el).includes(wanted));
            if (hit) { submit = deepestWithText(hit, wanted); break; }
        }
        if (submit) break;
    }
    if (isVisible(doneNode)) submit = null;
    if (submit) submit.setAttribute("data-zhs-submit", "1");

    // ---------- 5. 关闭按钮（解析弹窗靠它关掉） ----------
    let close = null;
    for (const selector of closeSelectors) {
        const candidates = Array.from(document.querySelectorAll(selector))
            .filter(isRenderable);
        if (candidates.length) { close = candidates[candidates.length - 1]; break; }
    }
    if (close) close.setAttribute("data-zhs-close", "1");

    return {
        kind,
        questions,
        optionCount: nodes.length,
        answeredCount: questions.filter((q) => q.answered).length,
        submit: submit ? {tag: "1", text: flat(submit)} : null,
        close: close ? {tag: "1"} : null,
        hasAnswer: Boolean(scope.querySelector(
            ".analyze-box, .answer-container, .analyze, .done")),
        text: flat(scope).slice(0, 300),
    };
}
"""


def _scan(page: Page) -> dict | None:
    try:
        return page.evaluate(
            _SCAN_JS,
            {
                "roots": list(site.DIALOG_ROOTS),
                "optionSelectors": list(site.QUIZ_OPTION_SELECTORS),
                "submitSelectors": list(site.QUIZ_SUBMIT_SELECTORS),
                "submitTexts": list(site.QUIZ_SUBMIT_TEXTS),
                "closeSelectors": list(site.QUIZ_CLOSE_SELECTORS),
                "groupSelectors": list(site.QUIZ_GROUP_SELECTORS),
                "hintWords": list(site.QUIZ_HINTS),
                "aiDialog": site.AI_EXERCISE_DIALOG,
                "topicTitle": site.TOPIC_TITLE,
                "aiNotice": site.AI_NOTICE_DIALOG,
            },
        )
    except Exception:
        return None


def _clear_tags(page: Page) -> None:
    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('[data-zhs-opt]').forEach(
                    el => el.removeAttribute('data-zhs-opt'));
                document.querySelectorAll('[data-zhs-submit]').forEach(
                    el => el.removeAttribute('data-zhs-submit'));
                document.querySelectorAll('[data-zhs-close]').forEach(
                    el => el.removeAttribute('data-zhs-close'));
            }"""
        )
    except Exception:
        pass


def _click_tag(page: Page, attribute: str, value: str = "1") -> bool:
    """点击被标记的元素。

    注意必须用 dispatchEvent 而不是 el.click()：
    关闭按钮是 <svg class="icon-close">，SVGElement 上没有 click() 方法，
    直接调 el.click() 会抛 TypeError（之前就是被这个坑住，弹窗永远关不掉）。
    """
    try:
        return bool(
            page.evaluate(
                """(args) => {
                    const el = document.querySelector(
                        `[data-zhs-${args.attribute}="${args.value}"]`);
                    if (!el) return false;
                    try { el.scrollIntoView({block: 'center'}); } catch (e) {}
                    el.dispatchEvent(new MouseEvent('click', {
                        bubbles: true, cancelable: true, view: window,
                    }));
                    return true;
                }""",
                {"attribute": attribute, "value": value},
            )
        )
    except Exception:
        return False


def _choose_indices(question: dict, options: list[str], mode: str,
                    ai: AiClient | None, logger: Logger,
                    config=None, bridge: Bridge | None = None,
                    examine: str = "in_video") -> list[int] | None:
    """挑出要点的选项下标：单选返回 1 个，多选返回多个；None = 交给用户。

    AI 用不了时的处理和其他地方一致：先重试（AiClient 内部会重试 5 次），
    再弹窗问用户「继续用 AI / 改用随机 / 改用手动」。
    """
    multi = bool(question.get("multi")) and len(options) > 1
    stem = question.get("text") or ""
    if mode == "ai" and ai and ai.ready and ai.cooling_down:
        logger.debug("AI 处于冷却期（刚刚连续失败），这题先用随机答案。")
    elif mode == "ai" and ai and ai.ready:
        attempt = 0
        while attempt < 2:                 # 第一次失败后，用户若选"继续用 AI"就再试一轮
            attempt += 1
            if multi:
                indices = ai.choose_multi(stem, options)
                if indices:
                    logger.info(
                        f"AI（多选）选择：{[options[i][:24] for i in indices]}"
                    )
                    return sorted(indices)
            else:
                index = ai.choose(stem, options)
                if index is not None and index < len(options):
                    logger.info(f"AI 选择第 {index + 1} 个选项：{options[index][:32]}")
                    return [index]
            decision = ai_mod.resolve_ai_failure(ai, bridge, config, logger, examine)
            if decision == "ai":
                logger.info("按你的选择重新用 AI 作答…")
                continue
            if decision == "manual":
                return None
            logger.warn("AI 仍然没能作答，这题先用随机答案，稍后会自动再试 AI。")
            ai.start_cooldown()
            break
    if mode == "manual":
        return None
    if multi:                              # 随机时多选也选 2 项
        return sorted(random.sample(range(len(options)), min(2, len(options))))
    return [random.randrange(len(options))]


def _choose_index(question: str, options: list[str], mode: str,
                  ai: AiClient | None, logger: Logger,
                  config=None, bridge: Bridge | None = None,
                  examine: str = "in_video") -> int | None:
    """选一个选项。返回 None 表示"交给用户作答"。"""
    indices = _choose_indices(
        {"text": question, "multi": False}, options, mode, ai, logger,
        config=config, bridge=bridge, examine=examine,
    )
    if not indices:
        return None
    return indices[0]


def _close_dialog(page: Page, logger: Logger) -> bool:
    """关掉已经没有提交按钮的题目弹窗（解析页）。"""
    if _click_tag(page, "close"):
        logger.debug("已点关闭按钮收起题目弹窗。")
        return True
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def handle_quiz(page: Page, config, logger: Logger, bridge: Bridge,
                ai: AiClient | None, examine: str, label: str,
                auto_submit: bool = True) -> bool:
    """处理一次题目弹窗。返回是否处理过（True 表示确实遇到了题目）。

    auto_submit=False 时只作答、不点提交（用于"让用户自己检查后提交试卷"）。
    随堂练习必须提交才能关掉弹窗，所以那边固定用 True。
    """
    mode = config.quiz_mode(examine)
    handled = False
    stuck = 0
    last_signature = None
    clicked_questions: set[str] = set()      # 这一轮已经点过的题（防反复选中/取消）
    clicked_answers: set[tuple] = set()

    for _ in range(MAX_ROUNDS):
        bridge.check_stop()
        # 先清掉上一轮的临时标记，再重新扫描（标记必须和本次扫描一一对应，
        # 否则会点到已经消失/错位的元素）
        _clear_tags(page)
        info = _scan(page)
        if not info:
            if handled:
                logger.success(f"{label}已处理完毕，弹窗已消失。")
            return handled

        handled = True
        signature = (info["optionCount"], info["answeredCount"], bool(info["submit"]),
                     info["hasAnswer"])
        stuck = stuck + 1 if signature == last_signature else 0
        last_signature = signature

        if mode == "manual":
            logger.warn(f"检测到{label}，按设置交由你手动作答。")
            bridge.post("quiz_manual", {"label": label, "question": info.get("text", "")[:200]})
            _wait_gone(page, bridge, timeout=1800)
            return True

        if stuck >= STUCK_ROUNDS:
            logger.warn(f"{label}自动作答没有进展，请手动处理。")
            bridge.post(
                "quiz_manual",
                {
                    "label": label,
                    "question": (info.get("text") or "")[:200],
                    "message": (
                        f"检测到{label}，但自动作答卡住了。\n"
                        "请在浏览器里手动完成，完成后程序会自动继续。"
                    ),
                },
            )
            if _wait_gone(page, bridge, timeout=1800):
                return True
            stuck = 0
            continue

        # 1) 每道还没作答的题选好答案（多选题一次点多个）
        picked = 0
        to_manual = False
        for question in info["questions"]:
            if question["answered"]:
                continue
            options = question["options"]
            if not options:
                continue
            indices = _choose_indices(
                question, [o["text"] for o in options],
                mode, ai, logger, config=config, bridge=bridge, examine=examine,
            )
            if indices is None:
                # AI 不可用且用户选择了手动作答：把这一轮剩下的交给用户
                to_manual = True
                break
            signature = (question["text"][:60], tuple(indices))
            if signature in clicked_answers:
                # 同样的答案这一轮已经点过了：再点一次只会把选项取消掉，
                # 页面上看起来在作答，其实永远完不成（翻转课的弹题就踩过这个坑）
                clicked_questions.add(question["text"][:60])
                continue
            clicked_tags = 0
            for index in indices:
                if _click_tag(page, "opt", options[index]["tag"] or ""):
                    clicked_tags += 1
            if clicked_tags:
                picked += clicked_tags
                clicked_answers.add(signature)
                clicked_questions.add(question["text"][:60])
                logger.info(
                    f"{label}第 {question['text'][:24]!r} 选择第 "
                    f"{', '.join(str(i + 1) for i in indices)} 个选项"
                    f"（{len(options)} 选 {len(indices)}）"
                )
        if to_manual:
            # 关键：不能带着没答完的题去点提交
            mode = "manual"
            continue
        if picked:
            bridge.sleep(0.5)

        # 2) 有提交按钮就提交（多道题一次性提交）
        if info["submit"]:
            if not auto_submit:
                logger.warn(
                    f"{label}已全部作答，但按设置**没有自动提交**，"
                    "请到浏览器里检查后自己点提交。"
                )
                bridge.post(
                    "quiz_review",
                    {
                        "label": label,
                        "message": (
                            f"{label}已由程序作答完毕，但按你的设置不会自动提交。\n"
                            "请到浏览器里核对答案后自行提交试卷。"
                        ),
                    },
                )
                _wait_gone(page, bridge, timeout=1800)
                return True
            _click_tag(page, "submit")
            logger.info(f"已点击「{info['submit']['text'] or '提交'}」。")
            bridge.sleep(1.5)
            after = _scan(page)
            if after and not after["submit"]:
                # 提交后剩下的是解析页，关掉它让视频恢复
                _close_dialog(page, logger)
                bridge.sleep(1.0)
            continue

        # 3) 没有提交按钮：可能是解析页，也可能是每选即判的旧结构
        all_clicked = bool(info["questions"]) and all(
            q["answered"] or q["text"][:60] in clicked_questions
            for q in info["questions"]
        )
        if (
            info["hasAnswer"]
            or info["answeredCount"] == len(info["questions"])
            or (picked and not info["submit"] and all_clicked)
        ):
            if picked and not info["submit"] and not info["hasAnswer"]:
                logger.info(f"{label}这张弹题没有提交按钮，选完直接关掉它。")
            _close_dialog(page, logger)
            bridge.sleep(1.0)
            if not _scan(page):
                continue
        bridge.sleep(0.8)

    logger.warn(f"{label}处理轮次已达上限。")
    return handled


def _wait_gone(page: Page, bridge: Bridge, timeout: float = 600) -> bool:
    start = time.time()
    while _scan(page):
        if time.time() - start > timeout:
            return False
        bridge.sleep(0.5)
    return True


def has_quiz(page: Page) -> bool:
    info = _scan(page)
    if info:
        _clear_tags(page)
        return True
    return False


def is_chapter_quiz(page: Page) -> bool:
    """是不是「章节练习/章节测验」这类计分弹窗。

    之前这里直接用 has_quiz()，结果把随堂练习面板（.ai-test-question-wrapper）
    也当成了章节测验 —— 用户没勾选时会白白弹窗询问，而且那个面板
    未作答根本关不掉，点「跳过」就会卡住。现在严格按文案判断。
    """
    if is_visible_panel(page):
        return False
    info = _scan(page)
    if not info:
        _clear_tags(page)
        return False
    text = info.get("text") or ""
    _clear_tags(page)
    return any(hint in text for hint in site.CHAPTER_TEST_HINTS)


def is_visible_panel(page: Page) -> bool:
    """随堂练习面板是否正在显示。"""
    try:
        return bool(
            page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                        && rect.width > 0 && rect.height > 0;
                }""",
                site.AI_QUIZ_PANEL,
            )
        )
    except Exception:
        return False


def answer_only(page: Page, config, logger: Logger, bridge: Bridge,
                ai: AiClient | None, label: str) -> bool:
    """只作答不提交的入口（章节测验用）。"""
    return handle_quiz(page, config, logger, bridge, ai, "chapter", label,
                       auto_submit=False)
