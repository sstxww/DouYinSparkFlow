import os
import subprocess
import sys
import traceback

from playwright.sync_api import sync_playwright

from utils.config import DEBUG, Environment, get_config, get_environment

PLAYWRIGHT_BROWSERS_PATH = "../chrome"


def install_browser():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        print("????????????????")
    except subprocess.CalledProcessError as e:
        print(f"???????{e}")


def get_browser():
    headless = True
    env = get_environment()
    config = get_config()

    if env == Environment.LOCAL:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(
            os.path.join(os.path.dirname(__file__), PLAYWRIGHT_BROWSERS_PATH)
        )
        if DEBUG:
            headless = False
    elif env == Environment.PACKED:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(
            os.path.join(os.path.dirname(sys.executable), PLAYWRIGHT_BROWSERS_PATH)
        )

    launch_kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--window-size=1440,960",
        ],
    }
    if config.get("proxyAddress"):
        launch_kwargs["proxy"] = {"server": config["proxyAddress"]}

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(**launch_kwargs)
        return playwright, browser
    except Exception as e:
        if "Executable doesn't exist" in str(e) and env != Environment.GITHUBACTION:
            print("????????????")
            install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise
