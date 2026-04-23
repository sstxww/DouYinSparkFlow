import os
import re
import time
import traceback
from pathlib import Path

from playwright.sync_api import Response

from utils import norm
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from core.msg_builder import build_message
from core.browser import get_browser

SEARCH_EMPTY_TEXT = "\u672a\u641c\u7d22\u5230\u76f8\u5173\u5185\u5bb9"

config = get_config()
userData = get_userData()
logger = setup_logger(level=config.get("logLevel", "Info"))
userIDDict = {}


def handle_response(response: Response):
    global userIDDict
    if "aweme/v1/web/im/user/info" not in response.url:
        return
    try:
        json_data = response.json()
        for item in json_data.get("data", []):
            short_id = norm(item.get("short_id"))
            unique_id = norm(item.get("unique_id"))
            sec_uid = norm(item.get("sec_uid"))
            nickname = norm(item.get("nickname"))
            remark_name = norm(item.get("remark_name", nickname))
            aliases = {v for v in [short_id, unique_id, sec_uid, nickname, remark_name] if v}
            for key in [remark_name, nickname]:
                if key:
                    userIDDict.setdefault(key, set()).update(aliases | {key})
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        last = tb[-1]
        print(f"Failed to parse user info response: {e}")
        print(f"File: {last.filename}, line: {last.lineno}, function: {last.name}")


def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    for attempt in range(retries):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{name} failed, retry {attempt + 1}: {e}")
                time.sleep(delay)
            else:
                logger.error(f"{name} failed after max retries: {e}")
                raise


def safe_slug(text: str) -> str:
    text = norm(text) or "user"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_") or "user"


def save_debug_snapshot(page, username: str, label: str):
    try:
        slug = safe_slug(username)
        page.screenshot(path=f"logs/{slug}_{label}.png", full_page=True)
        body_text = page.locator("body").inner_text(timeout=5000)
        Path(f"logs/{slug}_{label}.txt").write_text(body_text, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save debug snapshot for {username}: {e}")


def get_visible_titles(page):
    try:
        return [norm(t) for t in page.locator(".conversationConversationItemtitle").all_inner_texts() if norm(t)]
    except Exception:
        return []


def active_slot_label():
    return norm(os.getenv("ACTIVE_SLOT"))


def account_matches_active_slot(user):
    slot = active_slot_label()
    if not slot or slot == "manual":
        return True

    configured_slots = [norm(value) for value in user.get("slots", []) if norm(value)]
    if not configured_slots:
        return True

    return slot in configured_slots


def resolve_aliases(title: str):
    title = norm(title)
    aliases = {title} if title else set()
    if title in userIDDict:
        aliases.update(userIDDict[title])
    for key, values in userIDDict.items():
        if title == key or title in values:
            aliases.add(key)
            aliases.update(values)
    return {norm(v) for v in aliases if norm(v)}


def wait_for_chat_ready(page, username: str):
    search_input = page.locator("input[type='text']").first
    page.wait_for_selector("input[type='text']", timeout=config["browserTimeout"])
    time.sleep(max(config.get("friendListTimeout", 2000), 2000) / 1000)
    titles = get_visible_titles(page)
    if titles:
        logger.info(f"{username} visible conversations: {titles[:12]}")
    else:
        logger.warning(f"{username} has no visible conversation titles; saving snapshot")
        save_debug_snapshot(page, username, "chat_ready")
    return search_input


def clear_search_input(page, search_input):
    search_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    time.sleep(0.3)


def wait_for_search_results(page):
    deadline = time.time() + 6
    while time.time() < deadline:
        if page.locator(".SearchPanelitembox").count() > 0:
            return True
        try:
            body = page.locator("body").inner_text(timeout=1000)
            if SEARCH_EMPTY_TEXT in body:
                return False
        except Exception:
            pass
        time.sleep(0.2)
    return page.locator(".SearchPanelitembox").count() > 0


def search_and_select_user(page, username, targets):
    search_input = wait_for_chat_ready(page, username)
    normalized_targets = [norm(t) for t in targets if norm(t)]
    remaining_targets = set(normalized_targets)
    sent_titles = set()

    logger.info(f"{username} search targets: {normalized_targets}")

    for query in normalized_targets:
        if query not in remaining_targets:
            continue

        clear_search_input(page, search_input)
        search_input.type(query, delay=40)
        search_input.press("Enter")
        found = wait_for_search_results(page)
        if not found:
            logger.info(f"{username} search miss: {query}")
            continue

        results = page.locator(".SearchPanelitembox")
        result_count = results.count()
        matched = False

        for idx in range(min(result_count, 5)):
            item = results.nth(idx)
            title_loc = item.locator(".SearchPanelitemtitle")
            title = norm(title_loc.inner_text() if title_loc.count() else item.inner_text())
            aliases = resolve_aliases(title)
            aliases.add(query)
            cover = {t for t in remaining_targets if t in aliases or t == title}
            if not cover and query != title:
                continue

            if title in sent_titles:
                remaining_targets.difference_update(cover or {query})
                matched = True
                break

            chat_btn = item.locator(".SearchPanelitemchat_btn")
            if chat_btn.count():
                chat_btn.first.click()
            else:
                item.click()

            sent_titles.add(title)
            if not cover:
                cover = {query}
            remaining_targets.difference_update(cover)
            logger.info(f"{username} search matched {title}; covered: {sorted(cover)}")
            yield title
            matched = True
            break

        if not matched:
            logger.info(f"{username} search results for {query} were not actionable")

    clear_search_input(page, search_input)

    if remaining_targets:
        logger.warning(f"{username} still missing after search; fallback list scan: {sorted(remaining_targets)}")
        yield from scroll_and_select_user(page, username, remaining_targets, sent_titles)


def scroll_and_select_user(page, username, remaining_targets, sent_titles):
    target_selector = ".conversationConversationItemwrapper"
    title_selector = ".conversationConversationItemtitle"
    list_selector = ".conversationConversationListwrapper"

    seen_titles = set()
    empty_scroll_count = 0
    max_empty_scrolls = 10

    while remaining_targets:
        items = page.locator(target_selector)
        item_count = items.count()
        prev_seen_count = len(seen_titles)
        visible_titles = []

        for idx in range(item_count):
            item = items.nth(idx)
            title_loc = item.locator(title_selector)
            if title_loc.count() == 0:
                continue
            title = norm(title_loc.inner_text())
            if not title:
                continue

            visible_titles.append(title)
            if title not in seen_titles:
                seen_titles.add(title)

            aliases = resolve_aliases(title)
            cover = {t for t in remaining_targets if t in aliases or t == title}
            if not cover or title in sent_titles:
                continue

            item.click()
            sent_titles.add(title)
            remaining_targets.difference_update(cover)
            logger.info(f"{username} list matched {title}; covered: {sorted(cover)}")
            yield title
            time.sleep(1)
            break
        else:
            if len(seen_titles) > prev_seen_count:
                empty_scroll_count = 0
                if visible_titles:
                    logger.info(f"{username} visible list titles: {visible_titles[:12]}")
            else:
                empty_scroll_count += 1

            if empty_scroll_count >= max_empty_scrolls:
                logger.warning(
                    f"{username} hit {max_empty_scrolls} empty scrolls; remaining targets: {sorted(remaining_targets)}"
                )
                save_debug_snapshot(page, username, "scroll_fallback")
                break

            scrollable = page.locator(list_selector)
            if scrollable.count() == 0:
                logger.warning(f"{username} could not find conversation list container")
                save_debug_snapshot(page, username, "missing_list")
                break

            handle = scrollable.element_handle()
            if handle:
                page.evaluate("(el) => { el.scrollTop += 800; }", handle)
            time.sleep(1.2)


def send_message(page, account_name: str, target_title: str):
    chat_input_selector = ".messageEditorimChatEditorContainer"
    page.wait_for_selector(chat_input_selector, timeout=config["browserTimeout"])
    chat_input = page.locator(chat_input_selector).first
    message = build_message()
    lines = message.split("\\n")
    for idx, line in enumerate(lines):
        chat_input.type(line)
        if idx != len(lines) - 1:
            chat_input.press("Shift+Enter")
    logger.info(f"{account_name} typed renewal message for {target_title}")
    chat_input.press("Enter")
    time.sleep(2)


def do_user_task(browser, account_name, cookies, targets):
    global userIDDict
    userIDDict = {}

    context = browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 960},
    )
    context.set_default_navigation_timeout(config["browserTimeout"])
    context.set_default_timeout(config["browserTimeout"])
    page = context.new_page()

    page.on("response", handle_response)
    context.add_cookies(cookies)

    retry_operation(
        "open douyin chat",
        page.goto,
        retries=config["taskRetryTimes"],
        delay=5,
        url="https://www.douyin.com/chat",
    )

    time.sleep(5)
    logger.info(f"{account_name} started chat targeting")

    sent_count = 0
    for target_title in search_and_select_user(page, account_name, targets):
        try:
            send_message(page, account_name, target_title)
            sent_count += 1
        except Exception as e:
            logger.warning(f"{account_name} failed to send to {target_title}: {e}")
            save_debug_snapshot(page, account_name, f"send_failed_{safe_slug(target_title)}")

    if sent_count == 0:
        logger.warning(f"{account_name} did not hit any targets; saved debug snapshot")
        save_debug_snapshot(page, account_name, "no_target_sent")
    else:
        logger.info(f"{account_name} hit {sent_count} targets in this run")

    context.close()


def runTasks():
    playwright, browser = get_browser()
    try:
        slot = active_slot_label()
        logger.info(f"starting tasks, active delivery slot: {slot or 'manual'}")
        for user in userData:
            cookies = user["cookies"]
            targets = user["targets"]
            account_name = user.get("username", "unknown")
            if not account_matches_active_slot(user):
                logger.info(f"skip account {account_name}, slot {slot} not in {user.get('slots', [])}")
                continue
            logger.info(f"processing account {account_name}")
            do_user_task(browser, account_name, cookies, targets)
            logger.info(f"account {account_name} complete")
    finally:
        browser.close()
        playwright.stop()
