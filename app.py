#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot-hosting 自动续期（多账号版，账号密码登录）
# 配置方式（编号后缀式，兼容旧单账号）：
#   单账号（旧）：EMAIL / PASSWORD
#   多账号：EMAIL_1 + PASSWORD_1，EMAIL_2 + PASSWORD_2 ...（最多支持 10 个）
#   TG_BOT_TOKEN / TG_CHAT_ID 全局共用，只需填一次
# 登录与续期流程对齐 eooce/Auto-Renew-Bothosting（账号密码 + Turnstile 坐标点击）

import os, re, sys, time, random, requests, subprocess, ctypes
from seleniumbase import SB

# Windows 本地控制台默认 GBK，打印 emoji 会抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 全局共用配置
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""      # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""    # TG bot token

MAX_ACCOUNTS = 10
BASE_URL = "https://bot-hosting.net"


def collect_accounts():
    """收集账号列表。优先编号式；无编号时回退到旧单账号。"""
    accounts = []
    for i in range(1, MAX_ACCOUNTS + 1):
        email = (os.environ.get(f"EMAIL_{i}") or "").strip()
        password = (os.environ.get(f"PASSWORD_{i}") or "").strip()
        if email and password:
            accounts.append({"idx": i, "email": email, "password": password})
    if accounts:
        # 兼容：有人已配好 _2/_3，但 _1 仍用旧变量名，也一并纳入
        legacy_email = (os.environ.get("EMAIL") or "").strip()
        legacy_password = (os.environ.get("PASSWORD") or "").strip()
        idxs = {a["idx"] for a in accounts}
        if legacy_email and legacy_password and 1 not in idxs:
            accounts.insert(0, {"idx": 1, "email": legacy_email, "password": legacy_password})
        accounts.sort(key=lambda a: a["idx"])
        return accounts
    # 无编号变量：回退旧单账号模式
    legacy_email = (os.environ.get("EMAIL") or "").strip()
    legacy_password = (os.environ.get("PASSWORD") or "").strip()
    if not legacy_email or not legacy_password:
        return []
    return [{"idx": 1, "email": legacy_email, "password": legacy_password}]


def mask_email(email: str) -> str:
    if '@' in email:
        name, domain = email.split('@', 1)
        if len(name) > 4:
            return f"{name[:2]}****{name[-2:]}@{domain}"
        return f"{name}@{domain}"
    if email:
        return email[:2] + '****'
    return "（未填）"


# 发送tg通知
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")


# 通知格式（多账号：按账号填充邮箱与到期时间）
def format_notification(status: str, email: str = "", extra: str = "",
                        error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    masked = mask_email(email)

    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked}",
    ]
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)


# ---------- 基础工具 ----------
def js(sb, script, default=None, errors=None):
    try:
        result = sb.execute_script(script)
        return default if result is None else result
    except Exception as e:
        if errors is not None:
            errors.append(f"execute_script: {type(e).__name__}: {e}")
        try:
            result = sb.cdp.evaluate("(function(){ " + script + " })()")
            return default if result is None else result
        except Exception as e2:
            if errors is not None:
                errors.append(f"cdp.evaluate: {type(e2).__name__}: {e2}")
            return default


# Turnstile 定位失败时的现场诊断快照
def diag_turnstile_dom(sb, errors=None):
    snap = js(sb, """
        var out = [];
        var ifr = document.querySelectorAll('iframe');
        out.push('top-doc iframes: ' + ifr.length);
        for (var i = 0; i < ifr.length; i++) {
            var r = ifr[i].getBoundingClientRect();
            out.push('iframe w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' src=' + (ifr[i].src || '').slice(0, 90));
        }
        var ts = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"], input[name="cf-turnstile-response"]');
        out.push('turnstile-ish elements: ' + ts.length);
        for (var j = 0; j < ts.length; j++) {
            var rr = ts[j].getBoundingClientRect();
            var cls = (typeof ts[j].className === 'string') ? ts[j].className : (ts[j].getAttribute('class') || '');
            out.push(' - ' + ts[j].tagName + ' class=' + cls + ' w=' + Math.round(rr.width) + ' h=' + Math.round(rr.height));
        }
        out.push('window.turnstile=' + (typeof window.turnstile) + ' readyState=' + document.readyState);
        return out.join('\\n');
    """, "")
    for line in str(snap).splitlines():
        print("   [diag] " + line)
    if errors:
        for err in errors:
            print("   [diag][exec-error] " + str(err)[:200])


#  页面注入脚本
# Turnstile token（官方 JS 接口 或 表单隐藏域），未通过时为空串
TURNSTILE_TOKEN_JS = """
try { if (window.turnstile && window.turnstile.getResponse) {
        var t = window.turnstile.getResponse(); if (t) return t; } } catch (e) {}
var el = document.querySelector('input[name="cf-turnstile-response"]');
return el ? (el.value || '') : '';
"""

TURNSTILE_EXISTS_JS = """
return document.querySelector(
    'input[name="cf-turnstile-response"], iframe[src*="challenges.cloudflare.com"], .cf-turnstile'
) !== null;
"""
TS_COORDS_JS = """
function findWidget(root) {
    var els = root.querySelectorAll(
        'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id*="cf-chl"]');
    for (var i = 0; i < els.length; i++) {
        var r = els[i].getBoundingClientRect();
        if (r.width > 50 && r.height > 20) return els[i];
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
        if (all[j].shadowRoot) {
            var f = findWidget(all[j].shadowRoot);
            if (f) return f;
        }
    }
    return null;
}
function boxFromTokenInput() {
    var el = document.querySelector('input[name="cf-turnstile-response"]');
    var cur = el ? el.parentElement : null;
    for (var k = 0; k < 6 && cur; k++) {
        var r = cur.getBoundingClientRect();
        if (r.width > 100 && r.width < 600 && r.height > 30 && r.height < 140) return cur;
        cur = cur.parentElement;
    }
    return null;
}
var hit = findWidget(document) || boxFromTokenInput();
if (!hit) return null;
var hr = hit.getBoundingClientRect();
return {cx: Math.round(hr.x + 30), cy: Math.round(hr.y + hr.height / 2)};
"""


# Turnstile 脚本异步渲染，轮询等待组件出现
def wait_turnstile_present(sb, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js(sb, TURNSTILE_EXISTS_JS, False):
            return True
        time.sleep(1)
    return False


def _activate_window():
    if os.name == "nt":
        return

    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _windows_mouse_click(x: int, y: int):
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
    except Exception:
        pass


def _xdotool_click(x: int, y: int):
    if os.name == "nt":
        _windows_mouse_click(x, y)
        return

    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")


def turnstile_token(sb) -> str:
    token = js(sb, TURNSTILE_TOKEN_JS, "")
    return token if isinstance(token, str) else ""


def _click_turnstile(sb, locate_timeout=10) -> bool:
    coords = None
    errors = []
    deadline = time.time() + locate_timeout
    while time.time() < deadline:
        coords = js(sb, TS_COORDS_JS, None, errors)
        if coords:
            break
        time.sleep(1)
    if not coords:
        print("⚠️ 无法定位 Turnstile 坐标, 打印现场诊断:")
        diag_turnstile_dom(sb, errors)
        return False

    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(coords["cx"]) + sx
        ay = int(coords["cy"]) + sy + bar
        print(f"🖱️ 底层点击 Turnstile ({ax}, {ay})")
        _xdotool_click(ax, ay)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 底层点击失败: {e}")
        return False


def click_turnstile(sb) -> bool:
    if _click_turnstile(sb):
        return True
    coords = js(sb, TS_COORDS_JS)
    if coords:
        try:
            if hasattr(sb, "cdp") and hasattr(sb.cdp, "gui_click_x_y"):
                print(f"🖱️ 坐标点击 Turnstile ({coords['cx']},{coords['cy']})")
                sb.cdp.gui_click_x_y(int(coords["cx"]), int(coords["cy"]))
                return True
        except Exception as e:
            print(f"⚠️ 坐标点击失败: {e}")
    try:
        if hasattr(sb, "cdp") and hasattr(sb.cdp, "click_captcha"):
            print("🖱️ 兜底调用cdp点击 ...")
            sb.cdp.click_captcha()
            return True
        print("⚠️ 当前 SeleniumBase 版本无 sb.cdp.click_captcha")
    except Exception as e:
        print(f"⚠️ click_captcha 出错: {e}")
    return False


def solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8) -> bool:
    if turnstile_token(sb):
        return True

    deadline = time.time() + timeout
    next_click = time.time() + first_wait
    clicks = 0
    while time.time() < deadline:
        if turnstile_token(sb):
            print(f"✅ {tag}已通过(已获取 token)")
            return True
        if time.time() >= next_click:
            clicks += 1
            print(f"🖱️ 第 {clicks} 次点击 Turnstile 验证框...")
            click_turnstile(sb)
            next_click = time.time() + 10 + random.uniform(0, 3)
        time.sleep(2)
    ok = bool(turnstile_token(sb))
    print(f"{'✅' if ok else '❌'} {tag}{'已通过' if ok else '未通过'}")
    return ok


def handle_turnstile(sb) -> bool:
    print("🛡️ 处理 Turnstile 验证...")
    return solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8)


# 兜底探测：票据为空但组件显示已解时也放行（后端 60s 轮询是最终裁判）
def probe_turnstile_solved(sb) -> bool:
    try:
        sb.switch_to_frame('iframe[src*="challenges.cloudflare.com"]')
        try:
            state = sb.execute_script(
                "var el=document.querySelector('input[type=\"checkbox\"]');"
                "return el ? String(el.getAttribute('aria-checked')||el.checked) : 'no-checkbox';")
            return str(state).lower() == "true"
        finally:
            sb.switch_to_default_content()
    except Exception:
        try:
            sb.switch_to_default_content()
        except Exception:
            pass
        return False


# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


# 时间格式化
def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except:
        return countdown_str


# 归一化日期为 YYYY/MM/DD
def normalize_date(date_str: str) -> str:
    parts = date_str.split('/')
    if len(parts) == 3:
        if len(parts[2]) == 4:   # MM/DD/YYYY
            return f"{parts[2]}/{int(parts[0]):02d}/{int(parts[1]):02d}"
        if len(parts[0]) == 4:   # YYYY/MM/DD
            return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"
    return date_str


DATE_RE = r"(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})"


# 获取过期日期
def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*" + DATE_RE,   # Expires 10/24/2026. Renew manually to extend it for 4 days.
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",        # 2026/07/07 - renew
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",        # 07/07/2026 - renew
        DATE_RE,                                # 兜底: 账单页纯文本中通常只有到期日期
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            return normalize_date(match.group(1))
    return None


# 优先从 DOM 的 st-meta 元素提取到期日期 (不依赖页面语言)
def get_expiry_from_dom(sb) -> str:
    for selector in ['span.st-meta', '.st-meta', '[class*="st-meta"]']:
        try:
            if sb.is_element_present(selector):
                match = re.search(DATE_RE, sb.get_text(selector))
                if match:
                    return normalize_date(match.group(1))
        except Exception:
            pass
    return None


# 关闭 Google Funding Choices 同意弹窗（CI 干净 profile + EU 出口必弹，全屏遮罩挡点击）。
# 找不到就静默返回，绝不抛异常。
def dismiss_consent_popup(sb, tag):
    try:
        if sb.is_element_visible('button:contains("Do not consent")', timeout=3):
            sb.click('button:contains("Do not consent")')
            print(f"{tag} 🍪 已关闭同意弹窗（主文档）")
            sb.sleep(1)
            return True
    except Exception:
        pass
    # 同源 shadow DOM 穿透找按钮（Funding Choices 偶尔包 shadow root）
    try:
        hit = sb.execute_script(
            "function f(r){for(const el of r.querySelectorAll('*')){"
            "if(el.shadowRoot){const x=f(el.shadowRoot);if(x)return x;}"
            "if(el.tagName==='BUTTON'&&el.textContent&&el.textContent.includes('Do not consent'))return el;}"
            "return null;}"
            "var b=f(document); if(b){b.click(); return true;} return false;")
        if hit:
            print(f"{tag} 🍪 已关闭同意弹窗（shadow DOM）")
            sb.sleep(1)
            return True
    except Exception:
        pass
    # 递归扫 iframe（含嵌套两层），Funding Choices 通常是独立 iframe
    def scan(depth):
        try:
            n = len(sb.driver.find_elements("css selector", "iframe"))
        except Exception:
            return False
        for i in range(n):
            try:
                sb.driver.switch_to.frame(i)
                try:
                    btns = sb.driver.find_elements("xpath", ".//button[contains(., 'Do not consent')]")
                except Exception:
                    btns = []
                for b in btns:
                    try:
                        if b.is_displayed():
                            b.click()
                            print(f"{tag} 🍪 已关闭同意弹窗（iframe层{depth}#{i}）")
                            sb.sleep(1)
                            return True
                    except Exception:
                        continue
                if depth < 2 and scan(depth + 1):
                    return True
            except Exception:
                pass
            finally:
                try:
                    sb.driver.switch_to.parent_frame()
                except Exception:
                    pass
        return False
    try:
        if scan(0):
            try:
                sb.driver.switch_to.default_content()
            except Exception:
                pass
            return True
    except Exception:
        pass
    try:
        sb.driver.switch_to.default_content()
    except Exception:
        pass
    return False


# 续期点击门：先检测弹窗 → 有就关闭 → 再点续期；无弹窗直接点。日志留痕每一步。
def ensure_clear_then_click(sb, tag, selector, label, timeout=8):
    if dismiss_consent_popup(sb, tag):
        print(f"{tag} 🍪 检测到遮挡弹窗，已关闭，继续点{label}")
    else:
        print(f"{tag} 🔍 未检测到遮挡弹窗，直接点{label}")
    safe_click(sb, tag, selector, timeout=timeout, label=label)


# 安全点击：被遮挡（同意弹窗等）就先关闭再重试，最多 retries 次。
def safe_click(sb, tag, selector, timeout=8, retries=3, label="按钮"):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            sb.click(selector, timeout=timeout)
            return True
        except Exception as e:
            last_err = e
            print(f"{tag} ⚠️ {label}第 {attempt} 次点击失败: {str(e)[:150]}")
            if dismiss_consent_popup(sb, tag):
                print(f"{tag} 🔁 已关闭遮挡弹窗，重试点{label}...")
                continue
            if attempt < retries:
                sb.sleep(2)
    raise last_err


def login_with_password(sb, acct, tag) -> bool:
    """账号密码登录（与 eooce 版一致）"""
    print(f"{tag} 🚀 启动浏览器登录...")
    try:
        print(f"{tag} 🌐 访问 {BASE_URL}/login")
        sb.open(f"{BASE_URL}/login")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        dismiss_consent_popup(sb, tag)
        sb.click('button[type="submit"]')   # Continue with email
        sb.sleep(12)
        print(f"{tag} 📧 填写邮箱...")
        sb.input('input[type="email"]', acct["email"])
        sb.sleep(1)
        print(f"{tag} 🔑 填写密码...")
        sb.input('input[type="password"]', acct["password"])
        sb.sleep(3)

        if wait_turnstile_present(sb):
            if not handle_turnstile(sb):
                print(f"{tag} ❌ 登录界面的 Turnstile 验证失败，尝试兜底点击...")
                try:
                    sb.cdp.click_captcha()
                    sb.sleep(4)
                except Exception as e:
                    print(f"{tag} ⚠️ 兜底点击失败: {e}")
        else:
            print(f"{tag} ℹ️ 未检测到 Turnstile")

        sb.sleep(1)
        sb.click('button[type="submit"]')
        print(f"{tag} 🖱️ 提交登录按钮")
        sb.sleep(6)
        sb.wait_for_ready_state_complete()
        sb.open(f"{BASE_URL}/a/billings")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        current_url = sb.get_current_url()
        print(f"{tag} 📝 当前URL: {current_url}, Title: {sb.get_title()}")

        if "/a/billings" in current_url and "/login" not in current_url and "error=" not in current_url:
            print(f"{tag} ✅ 密码登录成功,当前已到达账单页")
            return True
        print(f"{tag} ❌ 密码登录后仍未到达账单页，当前URL: {current_url}")
    except Exception as e:
        print(f"{tag} ❌ 密码登录过程异常: {type(e).__name__}: {str(e)[:150]}")
    return False


def renew_one_account(sb, acct) -> dict:
    """单个账号的完整续期流程。返回 {'ok': bool, 'summary': str}，内部已发送该账号的 TG 通知。"""
    idx = acct["idx"]
    email = acct["email"] or f"账号{idx}"
    tag = f"[账号{idx} {mask_email(email)}]"
    result = {"ok": False, "summary": "未知"}

    # 账号隔离：清掉上一个账号的 cookie 与站点存储
    try:
        sb.delete_all_cookies()
    except Exception:
        pass
    try:
        sb.open(f"{BASE_URL}/")
        sb.execute_script("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
    except Exception:
        pass

    if not login_with_password(sb, acct, tag):
        send_telegram_message(format_notification("❌ 登录失败", email=email, error="账号密码登录失败"))
        result["summary"] = "❌ 登录失败（账号密码登录失败）"
        return result

    # 提取当前到期日期（先清同意弹窗，否则外层 Renew 点不到）
    dismiss_consent_popup(sb, tag)
    sb.sleep(3)
    try:
        page_source = sb.get_page_text() + "\n" + sb.get_page_source()
    except Exception:
        page_source = sb.get_page_source()
    current_expiry = get_expiry_from_dom(sb) or extract_expiry_date(page_source)
    if current_expiry:
        print(f"{tag} 📅 当前到期日期: {current_expiry}")
    else:
        print(f"{tag} ⚠️ 未能提取当前到期日期")

    # 寻找外部续期按钮
    outer_renew_selector = None
    countdown_text = None
    possible_selectors = [
        'button:contains("Renew")',
        'button:contains("Renew free plan")',
        'a:contains("Renew")',
        '[class*="renew"]',
        '[class*="Renew"]',
    ]

    for selector in possible_selectors:
        try:
            if sb.is_element_visible(selector):
                button_text = sb.get_text(selector)
                # "Renew in HH:MM:SS" / "Renewal cooldown HH:MM:SS" 都表示冷却中, 时间为距可续期的剩余时长
                match = re.search(r"(?:Renew in|Renewal cooldown)\s+(\d{1,2}:\d{2}:\d{2})", button_text, re.I)
                if match:
                    countdown_text = match.group(1)
                    print(f"{tag} ⏳ 续期冷却中, 未到续期时间: '{button_text}'")
                    break
                elif "Renew" in button_text:
                    outer_renew_selector = selector
                    print(f"{tag} ✅ 续期按钮可用: '{button_text}'")
                    break
        except Exception:
            pass

    # 点击外部续期按钮等待弹窗
    if outer_renew_selector:
        print(f"{tag} 🔄 点击外部续期按钮，等待验证窗口...")
        try:
            sb.sleep(2)
            ensure_clear_then_click(sb, tag, outer_renew_selector, "外部续期按钮")
            sb.sleep(15)  # 等待模态框加载，可能因网络因素加载慢
        except Exception as e:
            print(f"{tag} ❌ 点击外部按钮失败: {e}")
            send_telegram_message(format_notification("❌ 续期失败", email=email, error="点击外部续期按钮出错"))
            result["summary"] = "❌ 续期失败（点击按钮出错）"
            return result

        # 处理弹窗中的 Turnstile
        turnstile_passed = True  # 未检测到 Turnstile 时视为无需验证
        if wait_turnstile_present(sb):
            print(f"{tag} 🔒 等待弹窗中的 Turnstile 票据...")
            turnstile_passed = handle_turnstile(sb)
            if not turnstile_passed:
                print(f"{tag} ❌ Turnstile 验证失败，尝试兜底点击...")
                try:
                    sb.cdp.click_captcha()
                    sb.sleep(4)
                    turnstile_passed = bool(turnstile_token(sb))
                except Exception as e:
                    print(f"{tag} ⚠️ 兜底点击失败: {e}")
            if not turnstile_passed and probe_turnstile_solved(sb):
                print(f"{tag} ⚠️ 未读到票据但组件显示已解，先放行由后端校验")
                turnstile_passed = True
        else:
            print(f"{tag} ℹ️ 未检测到 Turnstile")

        if not turnstile_passed:
            print(f"{tag} ❌ Turnstile 票据最终未就绪，点确认也会被后端拒收，脚本退出")
            try:
                sb.save_screenshot(f"turnstile_fail_{idx}.png")
            except Exception:
                pass
            send_telegram_message(format_notification("❌ 续期失败", email=email, error="Turnstile 验证未通过（后端会拒收）"))
            result["summary"] = "❌ 续期失败（Turnstile 未通过）"
            return result

        # 点击弹窗确认（多选择器：eooce 实测的 dialog+btn-primary 优先，录制选择器兜底）
        print(f"{tag} ⏳ 等待续期按钮可用并点击...")
        time.sleep(5)
        modal_selectors = [
            'div[role="dialog"] button.btn-primary',
            '#renew-dialog button.inline-flex.w-full',
            'div[role="dialog"] button:contains("Renew")',
            'div[role="dialog"] button:contains("Confirm")',
        ]
        clicked = False
        last_err = None
        for selector in modal_selectors:
            try:
                sb.wait_for_element_visible(selector, timeout=8)
                try:
                    modal_text = sb.get_text(selector)
                except Exception:
                    modal_text = "（文案不可读）"
                print(f"{tag} 📝 弹窗确认按钮文案: {modal_text.strip()!r}（{selector}）")
                ensure_clear_then_click(sb, tag, selector, "弹窗确认按钮")
                clicked = True
                print(f"{tag} ✅ 已点击续期按钮")
                break
            except Exception as e:
                last_err = e
                print(f"{tag} ⚠️ 选择器未命中: {selector} ({str(e)[:120]})")
        if not clicked:
            print(f"{tag} ❌ 弹窗确认按钮点击失败: {last_err}")
            try:
                sb.save_screenshot(f"modal_click_fail_{idx}.png")
            except Exception:
                pass
            send_telegram_message(format_notification("❌ 续期失败", email=email, error=f"弹窗确认按钮点击失败: {str(last_err)[:120]}"))
            result["summary"] = "❌ 续期失败（弹窗按钮点击失败）"
            return result

        # 轮询确认结果（60s）：等倒计时或日期变化
        print(f"{tag} ⏳ 等待新的过期时间（轮询60s）...")
        new_expiry = None
        new_countdown = None
        deadline = time.time() + 60
        while time.time() < deadline:
            sb.sleep(5)
            try:
                new_page_text = sb.get_page_text() + "\n" + sb.get_page_source()
            except Exception:
                new_page_text = sb.get_page_source()
            new_match = re.search(r"(?:Renewal cooldown|Renew in)\s+(\d{1,2}:\d{2}:\d{2})", new_page_text, re.I)
            if new_match:
                new_countdown = new_match.group(1)
                new_expiry = get_expiry_from_dom(sb) or extract_expiry_date(new_page_text)
                break
            cand = get_expiry_from_dom(sb) or extract_expiry_date(new_page_text)
            if cand and cand != current_expiry:
                new_expiry = cand
                break

        # 提取新的到期日期和倒计时
        if new_countdown:
            print(f"{tag} ✅ 续期成功！新的倒计时: {new_countdown}")
            if new_expiry:
                print(f"{tag} 📅 新的到期日期: {new_expiry}")
            send_telegram_message(
                format_notification(
                    "✅ 续期成功",
                    email=email,
                    extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)}后",
                    expiry_date=new_expiry or "（未获取到）"
                )
            )
            result["ok"] = True
            result["summary"] = f"✅ 续期成功（到期 {new_expiry or '未知'}）"
        else:
            if new_expiry:
                print(f"{tag} ✅ 续期成功，到期日期已更新为: {new_expiry}")
                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        email=email,
                        extra="到期日期已更新",
                        expiry_date=new_expiry
                    )
                )
                result["ok"] = True
                result["summary"] = f"✅ 续期成功（到期 {new_expiry}）"
            else:
                print(f"{tag} ⚠️ 续期结果未知，到期日期未变化，请手动检查")
                try:
                    sb.save_screenshot(f"renew_unknown_{idx}.png")
                    body_txt = sb.get_text("body").strip().replace("\n", " ")[:300]
                except Exception:
                    body_txt = "（正文不可读）"
                print(f"{tag} 📄 页面正文片段: {body_txt!r}")
                send_telegram_message(
                    format_notification(
                        "⚠️ 续期可能未成功",
                        email=email,
                        extra="请登录后台检查",
                        error=f"页面提示: {body_txt[:120]}",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )
                result["summary"] = "⚠️ 结果未知（日期未变化）"

    else:
        if countdown_text:
            friendly = format_countdown(countdown_text)
            print(f"{tag} ⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
            send_telegram_message(
                format_notification(
                    "⏳ 未到续期时间",
                    email=email,
                    extra=f"⏱️ 可续期时间: {friendly}后",
                    expiry_date=current_expiry or "（未获取到）"
                )
            )
            result["ok"] = True
            result["summary"] = f"⏳ 未到时间（{friendly}后，到期 {current_expiry or '未知'}）"
        else:
            print(f"{tag} ℹ️ 未找到续期按钮或倒计时，状态未知")
            send_telegram_message(
                format_notification(
                    "ℹ️ 无需续期",
                    email=email,
                    extra="当前状态未知，请手动检查",
                    expiry_date=current_expiry or "（未获取到）"
                )
            )
            result["summary"] = "ℹ️ 状态未知"

    return result


# 主流程（多账号循环，浏览器复用，单账号失败不影响其他账号）
def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期（多账号）")
    print("#" * 25)

    accounts = collect_accounts()
    if not accounts:
        print("ℹ️ 未配置 EMAIL_n / PASSWORD_n（或旧 EMAIL / PASSWORD），脚本终止。")
        sys.exit(1)

    print(f"📋 共发现 {len(accounts)} 个账号：")
    for a in accounts:
        print(f"   - 账号{a['idx']} {mask_email(a['email'])}")

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": HEADLESS, "locale_code": "en-US"}

    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    results = {}
    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        for acct in accounts:
            idx = acct["idx"]
            print("\n" + "=" * 40)
            print(f"▶️ 开始处理 账号{idx}（{mask_email(acct['email'])}）")
            print("=" * 40)
            try:
                results[idx] = renew_one_account(sb, acct)
            except Exception as e:
                print(f"[账号{idx}] ❌ 执行异常: {e}")
                import traceback
                traceback.print_exc()
                results[idx] = {"ok": False, "summary": f"❌ 执行异常：{e}"}
                try:
                    send_telegram_message(format_notification(
                        "❌ 续期失败", email=acct["email"] or f"账号{idx}", error=f"执行异常：{e}"))
                except Exception:
                    pass

    # 汇总通知
    print("\n🏁 全部账号执行完毕，汇总：")
    summary_lines = ["🇫🇮 Bot-hosting 多账号续期汇总", ""]
    for acct in accounts:
        idx = acct["idx"]
        r = results.get(idx, {"ok": False, "summary": "未执行"})
        mark = "✅" if r["ok"] else "❌"
        print(f"   {mark} 账号{idx} {mask_email(acct['email'])}：{r['summary']}")
        summary_lines.append(f"{mark} 账号{idx}({mask_email(acct['email'])})：{r['summary']}")
    try:
        send_telegram_message("\n".join(summary_lines))
    except Exception as e:
        print(f"⚠️ 汇总通知发送失败: {e}")


if __name__ == "__main__":
    main()
