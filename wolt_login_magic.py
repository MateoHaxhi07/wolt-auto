#!/usr/bin/env python3
"""
Wolt magic-link automation with session health-check + Categories reorder.

CI-safe + robust page-ready:
- Headless on CI (Render) when CI=true
- Skip persistent user-data-dir on CI
- Env secrets -> files for Gmail
- Adjacent-swap drag with retries
- Strong ensure_categories_ready() and multi-strategy handle discovery
"""

import os
import re
import time
import json
import base64
import random
import unicodedata
from typing import Optional, List

# ─────────────────────────────────────────────────────────────
# CI / Environment
# ─────────────────────────────────────────────────────────────
CI = os.environ.get("CI", "").lower() == "true"
PROFILE_BASE = r"C:\tmp\wolt_profile" if os.name == "nt" else "/tmp/wolt_profile"
PROFILE_DIR = PROFILE_BASE  # local only; CI skips

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver import ActionChains
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    MoveTargetOutOfBoundsException,
    NoSuchElementException,
)
from webdriver_manager.chrome import ChromeDriverManager

# Gmail API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ==============================
# CONFIG (edit these)
# ==============================
WOLT_EMAIL = os.environ.get("WOLT_EMAIL", "vilaferdinand1970@gmail.com")
WOLT_LOGIN_URL = "https://merchant.wolt.com/login?next=/"

TARGET_LISTING_MANAGER = (
    "https://merchant.wolt.com/experience/venue/66c454dfca15873495dbab96/"
    "s/66c454dfca15873495dbab96/listing-manager/categories"
)

# ----- REORDER SETTINGS -----
MOVE_THIS_TO_TOP = "COMBOS"  # used only if DESIRED_ORDER == []
DESIRED_ORDER: List[str] = [
    "MENU DITORE",
    "COMBOS",
    "PAKETA",
    "HEALTHY BOWL",
    "PAKETA TRADICIONALE",
    "PANINE & WRAPS",
    "SHOQERUESE & EXTRA",
    "SUPA",
    "SALLATA",
    "PASTA",
    "MISH & PESHKU",
    "ËMBËLSIRA",
    "JUICES & SMOOTHIES",
    "PIJE",
    "NEW",
]

# Screenshots/debug folder
DEBUG_DIR = "wolt_debug"
os.makedirs(DEBUG_DIR, exist_ok=True)

# Gmail config
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SEARCH_WINDOW_MIN = 60

# other tuning
WAIT = 30 if CI else 25
COOLDOWN_MIN = 10
STAMP_FILE = "wolt_last_request.json"
URL_REGEX = r"https?://[^\s\"'>]+"
MERCHANT_DOMAIN_HIT = "merchant.wolt.com"

# Locators (broad)
SEARCH_INPUTS = [
    (By.CSS_SELECTOR, "input[placeholder*='Search']"),
    (By.CSS_SELECTOR, "input[type='search']"),
]
CATEGORIES_TAB_SELECTORS = [
    (By.XPATH, "//button[.//text()[contains(.,'Categories')]]"),
    (By.XPATH, "//a[.//text()[contains(.,'Categories')]]"),
    (By.XPATH, "//button[contains(.,'Categories')]"),
]
# Primary handle selector (original)
HANDLE_PRIMARY = (By.CSS_SELECTOR, "div[aria-roledescription='draggable']")
# Fallback handle: role=button + svg (often used for drag celery)
HANDLE_FALLBACK = (By.XPATH, "//div[@role='button' and @tabindex='0' and @aria-disabled='false' and .//svg]")
# Last resort: find rows by name then handle cell as first column
ROW_NAME_CELL_XP = ".//div[normalize-space()=$NAME]"

# ==============================
# Utilities
# ==============================
def can_request_again():
    if not os.path.exists(STAMP_FILE):
        return True
    try:
        data = json.load(open(STAMP_FILE, "r"))
        last = float(data.get("epoch", 0))
        elapsed_min = (time.time() - last) / 60.0
        return elapsed_min > COOLDOWN_MIN
    except Exception:
        return True

def mark_requested():
    json.dump({"epoch": time.time()}, open(STAMP_FILE, "w"))

def save_debug(driver, name):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, f"{int(time.time())}_{name}.png")
        driver.save_screenshot(path)
        print(f"[debug] saved screenshot: {path}")
    except Exception as e:
        print("[debug] screenshot failed:", e)

# ==============================
# Driver
# ==============================
def build_driver(headless: bool = False) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")

    opts.add_argument("--window-size=1920,1200")
    opts.add_argument("--lang=en-US,en;q=0.9")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    # Persistent profile only locally
    if not CI:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
        opts.add_argument("--profile-directory=Default")

    # hide automation
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # normal UA
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    # stealth shims
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e) {}
                try { Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); } catch(e) {}
                try { Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4]}); } catch(e) {}
                try {
                  const getParameter = WebGLRenderingContext.prototype.getParameter;
                  WebGLRenderingContext.prototype.getParameter = function(param) {
                    if (param === 37445) return 'Intel Inc.';
                    if (param === 37446) return 'Intel Iris OpenGL';
                    return getParameter.call(this, param);
                  };
                } catch(e) {}
            """
        })
    except Exception as e:
        print("[warn] cdp inject failed:", e)

    driver.implicitly_wait(2)
    return driver

# ==============================
# Human-ish actions
# ==============================
def human_type(el, text, jitter=(0.05, 0.18)):
    try:
        el.clear()
    except Exception:
        pass
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(*jitter))

def human_click(driver, el):
    try:
        actions = ActionChains(driver)
        actions.move_to_element(el).pause(random.uniform(0.15, 0.4)).click().perform()
    except Exception:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            driver.execute_script("arguments[0].click();", el)
        except Exception:
            try:
                el.click()
            except Exception:
                pass

# ==============================
# Page helpers
# ==============================
COOKIE_ACCEPT_CSS = "button[data-test-id='consent-banner-accept-button']"
COOKIE_ACCEPT_XPATH = "/html/body/div[1]/div[3]/div/div/div/button[3]"
EMAIL_INPUT_CSS = "input#email.al-Input-inpt-be5"
NEXT_BUTTON_XPATH = "/html/body/div[1]/div/div[2]/div/div/form/button"
CONFIRM_BTN_TESTID = "magic-login-landing.confirm"
CONFIRM_BTN_XPATH = "/html/body/div[2]/div[2]/main/div/button"

def accept_cookies_if_present(driver):
    for by, sel in [(By.CSS_SELECTOR, COOKIE_ACCEPT_CSS), (By.XPATH, COOKIE_ACCEPT_XPATH)]:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
            human_click(driver, btn)
            time.sleep(0.3)
            return
        except Exception:
            pass

def set_zoom_and_layout(driver, zoom_pct=80, width=1920, height=1200):
    try:
        driver.set_window_size(width, height)
    except Exception:
        pass
    try:
        driver.execute_script(f"document.body.style.zoom='{zoom_pct}%'")
    except Exception:
        html = driver.find_element(By.TAG_NAME, "html")
        for _ in range(max(0, int((100 - zoom_pct) / 10))):
            html.send_keys(Keys.CONTROL, Keys.SUBTRACT)

def request_magic_link_human(driver: webdriver.Chrome):
    driver.get(WOLT_LOGIN_URL)
    time.sleep(random.uniform(1.0, 2.0))
    accept_cookies_if_present(driver)

    email_input = WebDriverWait(driver, WAIT).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, EMAIL_INPUT_CSS))
    )
    human_type(email_input, WOLT_EMAIL, jitter=(0.08, 0.16))
    time.sleep(random.uniform(0.4, 0.8))

    save_debug(driver, "before_submit")
    next_btn = WebDriverWait(driver, WAIT).until(
        EC.element_to_be_clickable((By.XPATH, NEXT_BUTTON_XPATH))
    )
    try:
        driver.execute_script("window.scrollBy(0, -100);")
    except Exception:
        pass
    human_click(driver, next_btn)
    time.sleep(random.uniform(1.0, 2.0))
    save_debug(driver, "after_submit")
    print("✅ Requested magic login link (attempted).")

def confirm_magic_landing(driver: webdriver.Chrome):
    accept_cookies_if_present(driver)
    try:
        btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f"button[data-test-id='{CONFIRM_BTN_TESTID}']"))
        )
        human_click(driver, btn)
        return
    except Exception:
        pass
    btn = WebDriverWait(driver, WAIT).until(
        EC.element_to_be_clickable((By.XPATH, CONFIRM_BTN_XPATH))
    )
    human_click(driver, btn)

# ==============================
# Gmail helpers
# ==============================
def gmail_service():
    creds: Optional[Credentials] = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", GMAIL_SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", GMAIL_SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    svc = build("gmail", "v1", credentials=creds)
    try:
        profile = svc.users().getProfile(userId="me").execute()
        print("📧 Gmail API is reading inbox for:", profile.get("emailAddress"))
    except Exception as e:
        print("[warn] couldn't get Gmail profile:", e)
    return svc

def _decode_part_data(b64s: str) -> str:
    return base64.urlsafe_b64decode(b64s.encode("utf-8")).decode("utf-8", errors="ignore")

def extract_text_from_payload(payload: dict) -> str:
    if not payload:
        return ""
    if "parts" in payload:
        for part in payload["parts"]:
            pdata = part.get("body", {}).get("data")
            if pdata:
                return _decode_part_data(pdata)
    else:
        data = payload.get("body", {}).get("data")
        if data:
            return _decode_part_data(data)
    return ""

def find_latest_wolt_magic_link(svc, window_min: int) -> Optional[str]:
    broad_q = f'newer_than:{window_min}m (wolt OR "Wolt for Merchants" OR merchant.wolt.com OR "magic link")'
    try:
        res = svc.users().messages().list(
            userId="me", q=broad_q, maxResults=15, includeSpamTrash=True
        ).execute()
    except Exception as e:
        print("[error] Gmail list failed:", e)
        return None

    msgs = res.get("messages", [])
    if not msgs:
        return None

    for m in msgs:
        try:
            full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        except Exception as e:
            print("[warn] failed to fetch message", m.get("id"), e)
            continue

        headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        print(f"[email] From: {headers.get('from')} | Subject: {headers.get('subject')}")

        text = extract_text_from_payload(full.get("payload", {})) + " " + full.get("snippet", "")
        candidates = re.findall(URL_REGEX, text)
        for url in candidates:
            if "wolt" in url.lower():
                return url.strip('").,>];\' ')
    return None

# ==============================
# Session health
# ==============================
def is_logged_in(driver: webdriver.Chrome) -> bool:
    try:
        driver.get("https://merchant.wolt.com/")
        WebDriverWait(driver, 8).until(EC.url_contains(MERCHANT_DOMAIN_HIT))
        try:
            WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.CSS_SELECTOR, "header")))
        except Exception:
            pass
        return True
    except Exception:
        return False

# ==============================
# CATEGORIES + readiness
# ==============================
def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.casefold().strip()

def _row_container_from_handle(handle_el):
    return handle_el.find_element(By.XPATH, "ancestor::div[contains(@class,'sc-dPV')][1]")

def _row_name_text(row):
    cols = row.find_elements(By.XPATH, "./div")
    if len(cols) >= 3:
        t = cols[2].text.strip()
        if t:
            return t
    texts = [d.text.strip() for d in row.find_elements(By.XPATH, ".//div") if d.text.strip()]
    for t in texts:
        if not re.search(r"\bitems?\b", t, flags=re.I):
            return t
    return ""

def _find_handles_anyway(driver):
    """Try multiple strategies to locate draggable handles."""
    # Strategy 1: primary selector
    handles = driver.find_elements(*HANDLE_PRIMARY)
    if handles:
        return handles

    # Strategy 2: role=button + svg
    handles = driver.find_elements(*HANDLE_FALLBACK)
    if handles:
        return handles

    # Strategy 3: derive handle from known row structure by name cells
    # Find any known name on the page (from our desired order or a subset)
    probe_names = DESIRED_ORDER[:6]  # limit probing
    found = []
    for name in probe_names:
        try:
            # Find an element whose normalized text equals the name
            node = driver.find_element(By.XPATH, f"//*[normalize-space()='{name}']")
            # climb to row container
            row = node.find_element(By.XPATH, "ancestor::div[contains(@class,'sc-dPV')][1]")
            # handle is usually the first column div with role=button or matching fallback
            try:
                h = row.find_element(By.CSS_SELECTOR, "div[aria-roledescription='draggable']")
                found.append(h)
            except NoSuchElementException:
                try:
                    h = row.find_element(By.XPATH, ".//div[@role='button' and @tabindex='0' and .//svg]")
                    found.append(h)
                except NoSuchElementException:
                    pass
        except Exception:
            continue
    return found

def _discover_handles_and_rows(driver):
    handles = _find_handles_anyway(driver)
    rows = []
    for h in handles:
        try:
            row = _row_container_from_handle(h)
        except Exception:
            try:
                row = h.find_element(By.XPATH, "../../..")
            except Exception:
                continue
        name = _row_name_text(row)
        y = row.location.get("y", 0)
        rows.append({"name": name, "row": row, "handle": h, "y": y})
    rows.sort(key=lambda r: r["y"])
    return rows

def _force_render_rows(driver):
    # Scroll the main page to coerce virtualization
    for _ in range(8):
        driver.execute_script("window.scrollBy(0, 600);")
        time.sleep(0.12)
    for _ in range(8):
        driver.execute_script("window.scrollBy(0, -600);")
        time.sleep(0.12)

def _maybe_click_categories_tab(driver):
    # Some builds render a nav/tab for "Categories"
    for by, sel in CATEGORIES_TAB_SELECTORS:
        els = driver.find_elements(by, sel)
        if els:
            try:
                human_click(driver, els[0])
                time.sleep(0.4)
                return
            except Exception:
                pass

def ensure_categories_ready(driver, timeout=35) -> bool:
    """Ensure Categories page is visible and hydrated."""
    if "/listing-manager/categories" not in driver.current_url:
        driver.get(TARGET_LISTING_MANAGER)

    accept_cookies_if_present(driver)
    _maybe_click_categories_tab(driver)

    # Wait briefly for any search input as a sign of hydration
    t0 = time.time()
    while time.time() - t0 < 6:
        for by, sel in SEARCH_INPUTS:
            if driver.find_elements(by, sel):
                break
        time.sleep(0.2)
        break

    # Try 1: scroll to force render
    _force_render_rows(driver)
    handles = _find_handles_anyway(driver)
    if handles:
        print(f"[ready] found {len(handles)} handles (pass1).")
        return True

    # Try 2: reload once
    print("[ready] handles not found yet; reloading once...")
    driver.get(TARGET_LISTING_MANAGER)
    accept_cookies_if_present(driver)
    _maybe_click_categories_tab(driver)
    time.sleep(1.0)
    _force_render_rows(driver)
    handles = _find_handles_anyway(driver)
    print(f"[ready] handles after reload: {len(handles)}")
    return len(handles) > 0

def discover_rows(driver):
    if not ensure_categories_ready(driver, timeout=35):
        save_debug(driver, "categories_not_ready")
        raise TimeoutException("Categories page not ready (no drag handles detected).")

    rows = _discover_handles_and_rows(driver)
    if len(rows) < 5:
        _force_render_rows(driver)
        rows = _discover_handles_and_rows(driver)

    print(f"[rows] discovered: {len(rows)}")
    return rows

def print_order(rows):
    print("\n--- Current Category Order ---")
    for i, r in enumerate(rows, start=1):
        print(f"{i:2d}. {r['name']}")
    print("--------------------------------\n")

def _safe_drag_to_above(driver, src_handle, dst_row):
    """Drag source HANDLE to just above destination HANDLE with progressive offsets."""
    try:
        dst_handle = dst_row.find_element(By.CSS_SELECTOR, "div[aria-roledescription='draggable']")
    except Exception:
        try:
            dst_handle = dst_row.find_element(By.XPATH, ".//div[@role='button' and @tabindex='0' and .//svg]")
        except Exception:
            dst_handle = dst_row

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dst_handle)
    time.sleep(0.2)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", src_handle)
    time.sleep(0.2)

    upward_offsets = [-14, -24, -38, -52]
    for off in upward_offsets:
        try:
            actions = ActionChains(driver)
            actions.move_to_element(src_handle).pause(0.12)
            actions.click_and_hold(src_handle).pause(0.15)
            actions.move_to_element(dst_handle).pause(0.1)
            actions.move_by_offset(-6, 0).pause(0.05)
            actions.move_by_offset(0, off).pause(0.12)
            actions.move_by_offset(0, -4).pause(0.06)
            actions.move_by_offset(0, +4).pause(0.06)
            actions.release().perform()
            time.sleep(0.6)
            return
        except MoveTargetOutOfBoundsException:
            driver.execute_script("window.scrollBy(0, -140);")
            time.sleep(0.2)
        except Exception:
            time.sleep(0.2)

def _normalize_name_list(names):  # helper
    return [ _normalize(n) for n in names ]

def _bump_up_one(driver, index_now):
    """Move row at index_now up one position (above index_now-1) with verification."""
    assert index_now > 0, "Cannot bump index 0 up"
    rows_before = discover_rows(driver)
    name = rows_before[index_now]["name"]
    print(f"   ↥ bump '{name}' {index_now+1}→{index_now}")

    for attempt in range(1, 4):
        rows = discover_rows(driver)
        names = [r["name"] for r in rows]
        norms = _normalize_name_list(names)
        curr_idx = norms.index(_normalize(name))  # refresh current index

        if curr_idx <= index_now - 1:
            print(f"   ✓ already at {curr_idx+1}")
            return

        src = rows[curr_idx]
        dst = rows[curr_idx - 1]
        _safe_drag_to_above(driver, src["handle"], dst["row"])

        rows_after = discover_rows(driver)
        names_after = [r["name"] for r in rows_after]
        new_idx = _normalize_name_list(names_after).index(_normalize(name))
        if new_idx == curr_idx - 1:
            return

        driver.execute_script("window.scrollBy(0, -140);")
        time.sleep(0.25)

    print(f"   ⚠️ could not bump '{name}' this step; continuing")

def move_name_to_position(driver, name, target_index):
    while True:
        rows = discover_rows(driver)
        names = [r["name"] for r in rows]
        norms = _normalize_name_list(names)
        try:
            idx_now = norms.index(_normalize(name))
        except ValueError:
            print(f"⚠️  '{name}' not found; skipping.")
            return
        if idx_now <= target_index:
            return
        _bump_up_one(driver, idx_now)

def verify_order(driver, desired_names, normalize=True):
    final_rows = discover_rows(driver)
    got = [r["name"] for r in final_rows]

    def N(s):
        if not normalize:
            return s
        s2 = unicodedata.normalize("NFKD", s)
        s2 = "".join(ch for ch in s2 if not unicodedata.combining(ch))
        return s2.casefold().strip()

    desired_trim = [d for d in desired_names if any(N(d) == N(g) for g in got)]
    diffs = []
    for i, want in enumerate(desired_trim):
        if i >= len(got):
            break
        if N(got[i]) != N(want):
            diffs.append((i, got[i], want))
    missing = [d for d in desired_names if all(N(d) != N(g) for g in got)]
    return (len(diffs) == 0 and not missing), missing, diffs

def reorder_to(driver, desired_names):
    print("▶ Reordering to target list (adjacent swaps with verification) ...")
    for i, want in enumerate(desired_names):
        rows = discover_rows(driver)
        names = [r["name"] for r in rows]
        norms = _normalize_name_list(names)
        try:
            idx_now = norms.index(_normalize(want))
        except ValueError:
            print(f"⚠️  '{want}' not found; skipping.")
            continue

        if idx_now == i:
            continue

        print(f"↕️  Moving '{names[idx_now]}' from {idx_now+1} → {i+1} (stepwise)")
        while idx_now > i:
            _bump_up_one(driver, idx_now)
            rows = discover_rows(driver)
            names = [r["name"] for r in rows]
            norms = _normalize_name_list(names)
            idx_now = norms.index(_normalize(want))

    ok, missing, diffs = verify_order(driver, desired_names)
    if ok:
        print("✅ Order verified ✓")
    else:
        print("⚠️  Post-check mismatches:", diffs, "Missing:", missing)
    print_order(discover_rows(driver))

def move_name_to_top(driver, name_to_top: str):
    rows = discover_rows(driver)
    names = [r["name"] for r in rows]
    norms = _normalize_name_list(names)
    try:
        idx = norms.index(_normalize(name_to_top))
    except ValueError:
        print(f"⚠️  '{name_to_top}' not found; nothing moved.")
        print_order(rows)
        return
    if idx == 0:
        print(f"ℹ️  '{names[idx]}' is already at the top.")
        print_order(rows)
        return
    move_name_to_position(driver, name_to_top, 0)

# ==============================
# Main
# ==============================
def main():
    print("=== Wolt automation with session health-check + Categories reorder (adjacent) ===")

    # Write secrets from env (Render) into files if provided
    if os.environ.get("GOOGLE_CREDENTIALS_JSON"):
        with open("credentials.json", "w", encoding="utf-8") as f:
            f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(os.environ["GOOGLE_TOKEN_JSON"])

    driver = build_driver(headless=CI)
    try:
        if is_logged_in(driver):
            print("✅ Session active — skipping magic-link flow.")
            driver.get(TARGET_LISTING_MANAGER)
        else:
            if not can_request_again():
                print(f"⛔ Cooldown in effect. Wait at least {COOLDOWN_MIN} minutes since the last request.")
                return
            request_magic_link_human(driver)
            mark_requested()
            print("⏳ Short wait (give Wolt time to enqueue email)...")
            time.sleep(5 + random.uniform(0.5, 2.0))

            print("🔎 Connecting to Gmail API and searching for magic link...")
            svc = gmail_service()

            magic_link = None
            attempts = 12
            delay_between = 10
            for i in range(attempts):
                print(f"[poll] attempt {i+1}/{attempts} ...")
                magic_link = find_latest_wolt_magic_link(svc, SEARCH_WINDOW_MIN)
                if magic_link:
                    print("✅ Magic link found:", magic_link)
                    break
                time.sleep(delay_between)

            if not magic_link:
                print("❌ No magic link found in Gmail. Check Gmail UI and debug screenshots.")
                print("Saved screenshots are in:", os.path.abspath(DEBUG_DIR))
                return

            driver.get(magic_link)
            WebDriverWait(driver, WAIT).until(EC.url_contains(MERCHANT_DOMAIN_HIT))
            print("✅ Landed on Wolt merchant domain.")
            try:
                confirm_magic_landing(driver)
                print("✅ Confirm clicked.")
            except Exception as e:
                print("[warn] confirm click failed:", e)
                save_debug(driver, "confirm_failed")

            driver.get(TARGET_LISTING_MANAGER)

        # ----- On Categories page -----
        time.sleep(1.0)
        set_zoom_and_layout(driver, zoom_pct=75, width=1920, height=1200)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.4)
        save_debug(driver, "at_listing_manager_zoomed")

        rows = discover_rows(driver)
        print_order(rows)

        if DESIRED_ORDER:
            print("▶ Applying FULL desired order ...")
            reorder_to(driver, DESIRED_ORDER)
        elif MOVE_THIS_TO_TOP.strip():
            print(f"▶ Moving '{MOVE_THIS_TO_TOP}' to the top ...")
            move_name_to_top(driver, MOVE_THIS_TO_TOP)
        else:
            print("ℹ️ No reorder requested (both DESIRED_ORDER empty and MOVE_THIS_TO_TOP blank).")

        save_debug(driver, "at_listing_manager_after_work")

        print("⏳ Sleeping 10s so you can see the final state...")
        time.sleep(10)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
