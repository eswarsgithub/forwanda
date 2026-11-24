#!/usr/bin/env python3
"""
BookMyShow show-count watcher.

Opens the showtimes page for ONE movie / city / date, counts the showtimes
visible on it, and sends a Telegram message when the count jumps
(e.g. from 2 shows to 20), instead of alerting on the first couple of shows.

Setup:
    pip install playwright
    playwright install chromium

Run:
    python bms_watcher.py --test     # check what the script sees, no alerts
    python bms_watcher.py            # start watching

Config can be edited below or set as environment variables.
"""

import argparse
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

# ---------------- CONFIG ----------------
# Open BookMyShow in your browser, pick the movie, city and DATE, then copy
# the full URL of that showtimes page. It should end with the date: /20261218
URL = os.getenv("BMS_URL", "https://in.bookmyshow.com/buytickets/PASTE-YOUR-PAGE-URL/20261218")

# Telegram: create a bot with @BotFather to get the token, then message your
# bot once and open https://api.telegram.org/bot<TOKEN>/getUpdates to find chat id.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PASTE_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PASTE_CHAT_ID")

# Alert when show count rises by at least this many compared to the last check.
MIN_JUMP = int(os.getenv("MIN_JUMP", "5"))
# Also alert whenever the count reaches this many shows (set 0 to disable).
ALERT_AT_OR_ABOVE = int(os.getenv("ALERT_AT_OR_ABOVE", "0"))

# Polling interval in minutes (random jitter is added). Keep it gentle.
CHECK_EVERY_MIN = float(os.getenv("CHECK_EVERY_MIN", "10"))

STATE_FILE = "bms_state.json"
# ----------------------------------------

# Matches showtimes like "07:30 PM" or "10:15 AM"
TIME_RE = re.compile(r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s?(?:AM|PM)\b", re.IGNORECASE)


def count_shows(page_text: str) -> int:
    return len(TIME_RE.findall(page_text))


def fetch_page_text(playwright) -> tuple[str, str]:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        locale="en-IN",
        viewport={"width": 1366, "height": 900},
    )
    page = context.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)  # let the JS render showtimes
        # scroll to trigger lazy loading of theatre lists
        for _ in range(4):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1000)
        text = page.inner_text("body")
        final_url = page.url
    finally:
        browser.close()
    return text, final_url


def send_telegram(message: str) -> None:
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": "true"}
    ).encode()
    with urllib.request.urlopen(urllib.request.Request(api, data=data), timeout=20) as r:
        r.read()


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def check_once(pw, state: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text, final_url = fetch_page_text(pw)

    blocked = any(k in text.lower() for k in ("access denied", "captcha", "verify you are human"))
    if blocked:
        print(f"[{now}] Page looks blocked. Increase the interval or try later.")
        return

    count = count_shows(text)
    prev = state.get("count")
    print(f"[{now}] shows seen: {count} (previous: {prev})")

    if prev is None:
        state["count"] = count  # first run sets the baseline
        save_state(state)
        return

    jumped = count - prev >= MIN_JUMP
    reached = ALERT_AT_OR_ABOVE > 0 and count >= ALERT_AT_OR_ABOVE > prev

    if jumped or reached:
        msg = (
            f"🎬 More shows opened!\n"
            f"Shows on page: {prev} -> {count}\n"
            f"{URL}"
        )
        send_telegram(msg)
        print("Alert sent.")

    state["count"] = count
    save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="show what the script sees, then exit")
    parser.add_argument("--once", action="store_true", help="check one time and exit (for GitHub Actions)")
    args = parser.parse_args()

    with sync_playwright() as pw:
        if args.once:
            check_once(pw, load_state())
            return

        if args.test:
            text, final_url = fetch_page_text(pw)
            print("Final URL:", final_url)
            print("Showtimes detected:", count_shows(text))
            print("Times found:", TIME_RE.findall(text)[:40])
            print("\n--- first 800 chars of page text ---\n", text[:800])
            if TELEGRAM_TOKEN.startswith("PASTE"):
                print("\n(Telegram not configured yet)")
            else:
                send_telegram("✅ BookMyShow watcher test message")
                print("\nTelegram test message sent.")
            return

        state = load_state()
        print(f"Watching {URL}\nEvery ~{CHECK_EVERY_MIN} min. Ctrl+C to stop.")
        while True:
            try:
                check_once(pw, state)
            except Exception as e:  # keep running through network hiccups
                print("Error:", e)
            sleep_s = CHECK_EVERY_MIN * 60 * random.uniform(0.85, 1.15)
            time.sleep(sleep_s)


if __name__ == "__main__":
    main()
