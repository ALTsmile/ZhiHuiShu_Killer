"""章节测验的「整页试卷」处理（在线考试页）。

实测结构见 site.py 里的注释。要点：

1. 试卷**一页一题**：`.examPaper_subject` 共 N 个都在 DOM 里，
   但只有当前题是可见的，靠「下一题」或答题卡翻页。
   所以流程是：作答当前可见题 → 点「下一题」→ 再作答 → …
2. 题型在 `.subject_type` 里（【单选题】/【多选题】/【判断题】）。
   多选题（`input[type=checkbox]`）要让 AI 一次给多个字母。
3. **判断"这题做过没"只能看隐藏 input 的 `:checked`**。
   选项里的 `img.flagChecked` 每个选项都有（即使未选中），拿它判断会全部误判成已作答。
4. 提交按钮是 `button.btnStyleXSumit`（文案「提交作业」），点完通常还有确认框。
"""

from __future__ import annotations

import random
import time

from playwright.sync_api import Page

from . import ai as ai_mod
from . import popups, site
from .ai import AiClient
from .bridge import Bridge
from .logger import Logger

MAX_QUESTIONS = 200


def read_answer_card(page: Page) -> dict:
    """读答题卡：完成率 + 每题是否已作答 + 题号元素。

    实测结构：
        <h3 class="percentage_tit"><span>完成率</span><em class="fr"><i>100</i>%</em></h3>
        <ul class="answerCard_list1"><li class="green">1</li><li class="green">2</li>…</ul>
    已作答的题号带 `green` 类，所以可以据此直接跳到未作答的题目，
    不用从第一题一题一题翻过去。
    """
    try:
        data = page.evaluate(
            """(args) => {
                const {itemSel, rateSel, hints} = args;
                const clean = (t) => (t || "").split(/\\s+/).join(" ").trim();
                const rateNode = document.querySelector(rateSel);
                const items = Array.from(document.querySelectorAll(itemSel));
                return {
                    rate: rateNode ? clean(rateNode.innerText).replace('%', '') : '',
                    items: items.map((li, i) => {
                        const cls = (li.className || '').toString().toLowerCase();
                        return {
                            pos: i,
                            label: clean(li.innerText),
                            cls,
                            done: hints.some(h => cls.includes(h)),
                        };
                    }),
                };
            }""",
            {
                "itemSel": site.EXAM_CARD_ITEM,
                "rateSel": site.EXAM_CARD_RATE,
                "hints": list(site.EXAM_CARD_DONE_HINTS),
            },
        )
    except Exception:
        return {"rate": "", "items": []}
    return data or {"rate": "", "items": []}


def card_rate(card: dict) -> int | None:
    raw = str((card or {}).get("rate") or "").strip()
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def wait_card_ready(page: Page, logger: Logger, bridge: Bridge,
                    timeout: float = 25.0) -> dict:
    """等答题卡把「完成率」和题号渲染出来。

    实测坑：新标签页刚打开时答题卡可能先渲染成"完成率 0%、题号都没有状态"，
    这时就去判断会误以为整份卷子都没做，于是逐题重答一遍（又慢又白做）。
    所以这里要求**连续两次读到相同的完成率**才认为稳定。
    """
    deadline = time.time() + timeout
    last_rate = None
    last_items = 0
    card = {"rate": "", "items": []}
    while time.time() < deadline:
        bridge.check_stop()
        card = read_answer_card(page)
        items = card.get("items") or []
        rate = card_rate(card)
        if items and rate is not None:
            if last_rate == rate and last_items == len(items):
                return card
            last_rate, last_items = rate, len(items)
        bridge.sleep(0.9)
    if card.get("items"):
        logger.warn("答题卡完成率读取不稳定，按当前读到的值继续。")
    return card


def jump_to_question(page: Page, pos: int) -> bool:
    """点答题卡里的第 pos 题，直接跳过去。"""
    try:
        node = page.locator(site.EXAM_CARD_ITEM).nth(pos)
        if node.count() == 0:
            return False
        node.scroll_into_view_if_needed(timeout=2000)
        node.evaluate(
            """(el) => el.dispatchEvent(new MouseEvent('click', {
                bubbles: true, cancelable: true, view: window,
            }))"""
        )
        return True
    except Exception:
        return False


def is_exam_page(page: Page) -> bool:
    """当前是不是整页试卷。"""
    try:
        return page.locator(site.EXAM_QUESTION).count() > 0
    except Exception:
        return False


def wait_ready(page: Page, logger: Logger, bridge: Bridge, timeout: float = 30.0) -> bool:
    """等试卷真正渲染出来。

    实测：新标签页打开后页面会先显示"正在加载中"，试卷是异步渲染的。
    如果这时就去查 `.examPaper_subject`，会误判成"没识别到题目结构"，
    于是跳过一个正常的章节测验（用户看到的现象就是"从第一章跳到第四章"）。

    另外平台偶发网络波动时，试卷页会弹一个 Element-UI 的
    「网络异常，请稍后再试，错误代码0-979」提示框（`.el-message-box`），
    不点掉它试卷永远加载不出来 —— 这里自动点「确定」，还不行就重新加载一次。
    """
    deadline = time.time() + timeout
    waited = 0.0
    reloads = 0
    reload_after = 12.0
    network_seen = False
    while time.time() < deadline:
        bridge.check_stop()
        try:
            if page.locator(site.EXAM_QUESTION).count() > 0:
                if waited > 2:
                    logger.debug(f"试卷已渲染（等待 {waited:.1f}s）。")
                return True
        except Exception:
            pass
        try:
            if popups.has_error_dialog(page):
                network_seen = True
                if popups.dismiss_error_dialog(page, logger):
                    logger.info("试卷页弹了网络异常提示，已点「确定」，继续等它加载。")
                    deadline = max(deadline, time.time() + 15)
                    reload_after = waited + 12
        except Exception:
            pass
        if waited >= reload_after and reloads < 2:
            reloads += 1
            logger.warn(f"试卷页等了 {waited:.0f} 秒还没渲染出题目，重新加载一次…")
            try:
                page.reload(wait_until="commit", timeout=30_000)
            except Exception as exc:
                logger.debug(f"重新加载试卷页失败：{exc}")
            deadline = max(deadline, time.time() + 15)
            reload_after = waited + 15
        bridge.sleep(0.8)
        waited += 0.8
    logger.warn(f"等待 {timeout:.0f} 秒后试卷仍未渲染出题目。")
    if network_seen:
        logger.warn("（这个答题页出现过「网络异常」，多半是平台网络波动，重试同一条目即可。）")
    return False


STEM_DIAG_JS = r"""
() => {
    const stem = document.querySelector('.subject_describe')
        || document.querySelector('.examPaper_subject .subject_stem');
    if (!stem) return null;
    const fonts = [];
    try {
        for (const sheet of Array.from(document.styleSheets)) {
            let rules = null;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of Array.from(rules || [])) {
                if (rule && rule.constructor && rule.constructor.name === 'CSSFontFaceRule') {
                    fonts.push((rule.style && rule.style.fontFamily) || 'unknown');
                }
            }
        }
    } catch (e) {}
    const inner = stem.firstElementChild;
    return {
        outerHTML: (stem.outerHTML || '').slice(0, 700),
        innerText: (stem.innerText || '').slice(0, 200),
        textContent: (stem.textContent || '').slice(0, 200),
        childCount: stem.children.length,
        hasShadowRoot: Boolean(stem.shadowRoot)
            || Array.from(stem.children).some(c => Boolean(c.shadowRoot)),
        fontFamily: getComputedStyle(stem).fontFamily,
        innerHTML: inner ? (inner.innerHTML || '').slice(0, 300) : '',
        innerFontFamily: inner ? getComputedStyle(inner).fontFamily : '',
        definedFonts: Array.from(new Set(fonts)).slice(0, 8),
    };
}
"""


def diagnose_stem(page: Page, logger: Logger) -> None:
    """题干读不到时，把可能的渲染方式打进日志，便于定位。"""
    try:
        data = page.evaluate(STEM_DIAG_JS)
    except Exception as exc:
        logger.debug(f"题干诊断失败：{exc}")
        return
    if not data:
        logger.warn("题干诊断：页面上找不到 .subject_describe / .subject_stem。")
        return
    logger.warn("===== 题干读不到，诊断信息（请把这段发我）=====")
    logger.warn(f"outerHTML: {data.get('outerHTML')}")
    logger.warn(f"innerHTML: {data.get('innerHTML')}")
    logger.warn(
        f"innerText={data.get('innerText')!r} textContent={data.get('textContent')!r}"
    )
    logger.warn(
        f"子节点数={data.get('childCount')} 有shadowRoot={data.get('hasShadowRoot')}"
    )
    logger.warn(
        f"font-family={data.get('fontFamily')!r} "
        f"内层font={data.get('innerFontFamily')!r}"
    )
    logger.warn(f"页面自定义字体: {data.get('definedFonts')}")


def stem_via_accessibility(page: Page, index: int) -> str:
    """用 CDP 无障碍树读题干。

    实测发现：智慧树的题干被渲染进 **closed shadow root**，
    所以 `outerHTML` / `innerText` / `textContent` 全都是空的，
    `element.shadowRoot` 也是 null（closed 模式外部拿不到）。
    但浏览器的无障碍树是能看到渲染内容的，包括 closed shadow root，
    这里用 `Accessibility.getPartialAXTree` 把题干读出来。
    """
    try:
        client = page.context.new_cdp_session(page)
        document = client.send("DOM.getDocument", {"depth": 0})
        root_id = document["root"]["nodeId"]
        # 先按题号拿到那道题，再在题内找题干节点（这样不依赖两个列表长度一致）
        subjects = client.send(
            "DOM.querySelectorAll",
            {"nodeId": root_id, "selector": site.EXAM_QUESTION},
        ).get("nodeIds") or []
        if index < 0 or index >= len(subjects):
            return ""
        stem_node = client.send(
            "DOM.querySelector",
            {"nodeId": subjects[index], "selector": site.EXAM_STEM},
        ).get("nodeId")
        if not stem_node:
            return ""
        described = client.send("DOM.describeNode", {"nodeId": stem_node})
        backend_id = described["node"]["backendNodeId"]
        # 注意：getPartialAXTree 不会进入 closed shadow root，
        # 必须取整棵无障碍树，再按 backendDOMNodeId 找到题干节点往下走。
        nodes = client.send("Accessibility.getFullAXTree").get("nodes", [])
    except Exception:
        return ""
    by_id = {node.get("nodeId"): node for node in nodes}
    host = next(
        (node for node in nodes if node.get("backendDOMNodeId") == backend_id), None
    )
    if host is None:
        return ""
    parts: list[str] = []
    stack = [host]
    guard = 0
    while stack and guard < 500:
        guard += 1
        node = stack.pop()
        name = (node.get("name") or {}).get("value")
        role = (node.get("role") or {}).get("value")
        if name and role in ("StaticText", "InlineTextBox", "text", "paragraph"):
            parts.append(str(name))
        for child_id in node.get("childIds") or []:
            child = by_id.get(child_id)
            if child is not None:
                stack.append(child)
    # 去重并按出现顺序拼接（无障碍树里同一段文字可能重复出现）
    seen: list[str] = []
    for part in parts:
        text = " ".join(part.split())
        if text and text not in seen:
            seen.append(text)
    return " ".join(seen).strip()


def _visible_question(page: Page):
    """返回当前可见的那道题（Locator）。"""
    items = page.locator(site.EXAM_QUESTION)
    total = items.count()
    for index in range(min(total, MAX_QUESTIONS)):
        node = items.nth(index)
        try:
            if node.is_visible():
                return node, index, total
        except Exception:
            continue
    return None, -1, total


def read_question(page: Page) -> dict | None:
    """读取当前可见题目的题干、题型、选项和已选状态。"""
    data = None
    total = 0
    index = -1
    # 题干是异步渲染的，第一次读可能还是空的：多等几次再判断
    for attempt in range(4):
        node, index, total = _visible_question(page)
        if node is None:
            return None
        data = _read_node(node)
        if data and data.get("stem"):
            break
        if attempt < 3:
            page.wait_for_timeout(800)
    if data is None:
        return None
    if not data.get("stem"):
        # DOM 里读不到 → 用无障碍树兜底（题干在 closed shadow root 里）
        stem = stem_via_accessibility(page, index)
        if stem:
            data["stem"] = stem
            data["stemSource"] = "accessibility"
    else:
        data["stemSource"] = "dom"
    data["index"] = index
    data["total"] = total
    return data


def _read_node(node) -> dict | None:
    try:
        return node.evaluate(
            """(root) => {
                const clean = (t) => (t || "").split(/\\s+/).join(" ").trim();
                const typeNode = root.querySelector('.subject_type');
                const stemNode = root.querySelector('.subject_describe')
                    || root.querySelector('.subject_describe-markdown');
                const numberNode = root.querySelector('.subject_num');
                const options = Array.from(root.querySelectorAll('.subject_node .nodeLab'));
                return {
                    type: clean(typeNode && typeNode.innerText),
                    stem: clean(stemNode && (stemNode.innerText || stemNode.textContent)),
                    number: clean(numberNode && numberNode.innerText),
                    options: options.map((item, pos) => {
                        const input = item.querySelector('input');
                        const text = item.querySelector('.node_detail');
                        const letter = item.querySelector('.ABCase')
                            || item.querySelector('span.mr10');
                        return {
                            pos,
                            text: clean(text && text.innerText).slice(0, 300),
                            letter: clean(letter && letter.innerText),
                            checked: Boolean(input && input.checked),
                            kind: input ? input.type : '',
                        };
                    }),
                };
            }"""
        )
    except Exception:
        return None


def click_option(page: Page, question_index: int, option_index: int) -> bool:
    """点击第 question_index 题的第 option_index 个选项。"""
    try:
        node = page.locator(site.EXAM_QUESTION).nth(question_index)
        option = node.locator(site.EXAM_OPTION).nth(option_index)
        option.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        node = page.locator(site.EXAM_QUESTION).nth(question_index)
        option = node.locator(site.EXAM_OPTION).nth(option_index)
        option.evaluate(
            """(el) => el.dispatchEvent(new MouseEvent('click', {
                bubbles: true, cancelable: true, view: window,
            }))"""
        )
        return True
    except Exception:
        return False


def _click_button_by_text(page: Page, texts, logger: Logger) -> bool:
    """按文案点按钮（用于「下一题」「提交作业」）。"""
    try:
        clicked = page.evaluate(
            """(texts) => {
                const nodes = Array.from(document.querySelectorAll('button, .el-button'));
                for (const node of nodes) {
                    const style = getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (node.disabled) continue;
                    const text = (node.innerText || '').trim();
                    if (texts.some(t => text.includes(t))) {
                        node.dispatchEvent(new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window,
                        }));
                        return text;
                    }
                }
                return null;
            }""",
            list(texts),
        )
        if clicked:
            logger.debug(f"已点击按钮：{clicked}")
            return True
    except Exception as exc:
        logger.debug(f"按文案点按钮失败：{exc}")
    return False


def _choose(page: Page, config, logger: Logger, ai: AiClient | None,
            question: dict, examine: str, bridge: Bridge) -> list[int] | None:
    """决定这道题选哪些选项；返回 None 表示交给人处理。"""
    from . import quiz  # 局部导入，避免循环依赖

    options = question["options"]
    if not options:
        return None
    is_multi = "多选" in (question.get("type") or "") or any(
        o.get("kind") == "checkbox" for o in options
    )
    texts = [o["text"] for o in options]
    mode = config.quiz_mode(examine)
    stem = question.get("stem") or ""

    if is_multi:
        # 多选题：先让 AI 一次给多个字母
        if mode == "ai" and ai and ai.ready and not ai.cooling_down:
            indices = ai.choose_multi(stem, texts)
            if indices:
                logger.info(f"AI（多选）选择：{[texts[i][:24] for i in indices]}")
                return indices
            decision = ai_mod.resolve_ai_failure(ai, bridge, config, logger, examine)
            if decision == "ai":
                indices = ai.choose_multi(stem, texts)
                if indices:
                    logger.info(f"AI（多选）选择：{[texts[i][:24] for i in indices]}")
                    return indices
                logger.warn("AI 仍然没能作答，这题先用随机 2 项，稍后会自动再试 AI。")
                ai.start_cooldown()
            elif decision == "manual":
                return None
            logger.warn("AI 没有给出多选题答案，改用随机 2 项。")
        if mode == "manual":
            return None
        count = min(len(options), max(2, min(2, len(options))))
        return random.sample(range(len(options)), count)

    index = quiz._choose_index(
        stem, texts, mode, ai, logger, config=config, bridge=bridge, examine=examine
    )
    return None if index is None else [index]


def handle_exam_page(page: Page, config, logger: Logger, bridge: Bridge,
                     ai: AiClient | None, examine: str = "chapter",
                     auto_submit: bool = False, review: bool = False) -> str:
    """在整页试卷上作答。

    返回 "done"（实际作答了）/ "skipped"（完成率 100% 且不需要复查）/ ""（不是试卷页）。

    review=True 时会**逐题复查并重选**（先清掉原有选择再选），
    用于"答题进度 100% 但还没提交"或"做到一半"的测验。
    """
    if not is_exam_page(page):
        return ""

    wait_ready(page, logger, bridge, timeout=30)
    card = wait_card_ready(page, logger, bridge, timeout=25)
    rate = card_rate(card)
    items = card.get("items") or []
    pending = [item["pos"] for item in items if not item["done"]]
    logger.info(
        f"检测到整页试卷：答题卡 {len(items)} 题，"
        f"已完成率 {rate if rate is not None else '未知'}%，未作答 {len(pending)} 题。"
    )
    if not items:
        logger.warn("读不到答题卡，回退为从头逐题扫描。")

    if not review and rate is not None and rate >= 100:
        logger.info(
            "这份测验完成率已是 100%，直接略过（想重做请在设置里勾选「复查答题结果」）。"
        )
        return "skipped"

    logger.info("开始逐题作答…" + ("（复查模式：每题都会重新选择）" if review else ""))
    total = 0
    answered = 0
    stem_diagnosed = False
    # 要处理的题号：复查模式 = 全部；否则只处理未作答的
    queue = [item["pos"] for item in items] if (review and items) else pending
    if items and not queue:
        logger.info("答题卡显示所有题目都已作答，无需补答。")

    for step, pos in enumerate(queue[:MAX_QUESTIONS]):
        bridge.check_stop()
        if items:
            # 直接点答题卡上的题号跳过去，不用从第一题一题一题翻
            jump_to_question(page, pos)
            bridge.sleep(0.8)
        question = read_question(page)
        if not question:
            logger.warn("读取不到可见题目，结束试卷处理。")
            break
        total = question.get("total") or total
        index = question["index"]
        title = question.get("number") or f"第{index + 1}题"
        kind = (question.get("type") or "").strip()
        if not question.get("stem"):
            logger.warn(f"{title} 没读到题干文字（页面可能用特殊方式渲染），只能按选项判断。")
            if not stem_diagnosed:
                stem_diagnosed = True
                diagnose_stem(page, logger)
                # 同时把真实页面导出来，方便离线核对结构
                from . import diagnostics

                diagnostics.report_structure(page, logger, "试卷页")
                diagnostics.dump_page(page, logger, "exam-page")
        elif question.get("stemSource") == "accessibility":
            if not stem_diagnosed:
                stem_diagnosed = True
                logger.info(
                    "题干取到了（来源：无障碍树 —— 站点把题干渲染进了 closed shadow root，"
                    "普通 DOM 读不到）。"
                )

        already = any(o.get("checked") for o in question["options"])
        if already and not review:
            logger.info(f"{title} 已作答，跳过。（{kind}）")
            bridge.sleep(0.3)
            continue

        chosen = _choose(page, config, logger, ai, question, examine, bridge)
        if chosen is None:
            logger.warn(f"{title} 按设置交给你手动作答，已暂停自动作答。")
            bridge.post(
                "quiz_manual",
                {
                    "label": "章节测验",
                    "question": question.get("stem", "")[:200],
                    "message": "章节测验里有题目需要你手动作答，做完后程序会自动继续。",
                },
            )
            return "done"
        if already:
            # 复查模式：先清掉原有选择，否则多选会被 toggle 成取消
            for option_index, option in enumerate(question["options"]):
                if option.get("checked"):
                    click_option(page, index, option_index)
                    bridge.sleep(0.12)
            bridge.sleep(0.2)
        for option_index in chosen:
            click_option(page, index, option_index)
            bridge.sleep(0.15)
        answered += 1
        logger.info(
            f"{title} {'重新' if already else ''}作答（{kind}，选了 {len(chosen)} 项："
            f"{[question['options'][i]['letter'] or i + 1 for i in chosen]}）"
        )
        bridge.sleep(0.4)

        # 每答完一题点一次「下一题」= 保存该题（页面上的原话就是这么说的）
        _click_button_by_text(page, site.EXAM_NEXT_TEXT, logger)
        bridge.sleep(0.6)

    logger.info(f"试卷作答结束：共 {total} 题，本次新作答 {answered} 题。")

    # 收尾：最后一题时「下一题」位置会变成「保存」，点它才会把全部答案存到服务器，
    # 而且**不会关闭标签页**。（「暂存作业」会弹确认框、确认后还会关掉页面，不能用。）
    saved = _click_button_by_text(page, site.EXAM_SAVE_TEXT, logger)
    if saved:
        logger.success("已点击「保存」，本次作答已保存（页面会保留）。")
        bridge.sleep(1.2)
    elif queue:
        logger.warn("没有找到「保存」按钮，作答可能没有被保存。")

    if auto_submit:
        logger.info("按设置自动提交试卷…")
        if _click_button_by_text(page, site.EXAM_SUBMIT_TEXT, logger):
            bridge.sleep(1.5)
            if popups.click_button_in_dialog(page, site.EXAM_CONFIRM_TEXT, logger,
                                            skip_quiz=False):
                logger.success("已确认提交试卷。")
            else:
                logger.success("已点击提交试卷。")
        else:
            logger.warn("没有找到「提交作业」按钮，请到浏览器里手动提交。")
    else:
        logger.warn("试卷已作答但未提交（按设置由你核对后自己提交）。")
        bridge.post(
            "quiz_review",
            {
                "label": "章节测验",
                "message": "章节测验已由程序作答完毕并已点「保存」，作答记录不会丢。\n"
                           "这个答题标签页会保留着，请核对答案后自行点「提交作业」。",
            },
        )
    return "done"
