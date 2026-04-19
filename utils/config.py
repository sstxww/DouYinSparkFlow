import json
import os
import sys
from enum import Enum

from utils import norm
from utils.logger import setup_logger

logger = setup_logger(level="Debug")

DEBUG = True
config = None
userData = None


class Environment(Enum):
    GITHUBACTION = "GITHUB_ACTION"
    LOCAL = "LOCAL"
    PACKED = "PACKED"

    def __str__(self):
        return self.value


def get_environment():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Environment.PACKED
    if os.getenv("GITHUB_ACTIONS") == "true":
        return Environment.GITHUBACTION
    return Environment.LOCAL


def get_config():
    global config
    if config:
        return config

    config = {
        "proxyAddress": os.getenv("PROXY_ADDRESS", ""),
        "messageTemplate": os.getenv(
            "MESSAGE_TEMPLATE",
            "[??]????[??]\\n?? [??] ???? [??] ??\\n[API]",
        ),
        "hitokotoTypes": json.loads(
            os.getenv("HITOKOTO_TYPES", '["??","??","??","??"]')
        ),
        "matchMode": os.getenv("MATCH_MODE", "nickname"),
        "browserTimeout": int(os.getenv("BROWSER_TIMEOUT", "120000")),
        "friendListTimeout": int(os.getenv("FRIEND_LIST_WAIT_TIME", "2000")),
        "taskRetryTimes": int(os.getenv("TASK_RETRY_TIMES", "3")),
        "logLevel": os.getenv("LOG_LEVEL", "Debug"),
    }
    return config


def sanitize_cookies(cookies):
    for cookie in cookies:
        if "sameSite" in cookie:
            cookie.pop("sameSite")
    return cookies


def get_userData():
    global userData
    if userData:
        return userData

    tasks = json.loads(os.getenv("TASKS", "[]"))
    userData = []

    for task in tasks:
        username = norm(task.get("username", "")) or "unknown"
        unique_id = norm(task.get("unique_id"))
        if not unique_id:
            logger.warning(f"{username} ????? unique_id ??????")
            continue

        cookies_key = f"cookies_{unique_id}".upper()
        cookies_str = os.getenv(cookies_key, "")
        if not cookies_str:
            logger.warning(f"{username} ????? {cookies_key} ????????")
            continue
        try:
            cookies = json.loads(cookies_str)
        except json.JSONDecodeError:
            logger.warning(f"{username} ??? {cookies_key} ?????????")
            continue

        targets = [norm(t) for t in task.get("targets", []) if norm(t)]
        userData.append(
            {
                "unique_id": unique_id,
                "username": username,
                "cookies": sanitize_cookies(cookies),
                "targets": targets,
            }
        )

    return userData
