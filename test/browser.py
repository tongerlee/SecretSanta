"""Browser walk-through, by default against the local servers (site and API on different ports, like Amplify and Lambda).
SITE=https://your-site/ runs it against a deployed site; ENGINE=webkit runs it on Safari's engine.
create an event, sign up on separate 'devices', draw, token-based return visits, PIN-gated reveal on a new device."""
import json, os, pathlib, sys
from playwright.sync_api import sync_playwright
from PIL import Image

SITE = os.environ.get("SITE", "http://127.0.0.1:8787/")
OUT = pathlib.Path(__file__).parent / "shots"; OUT.mkdir(exist_ok=True)
errors = []

PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2)     # replaced by an iPhone profile for ENGINE=webkit

def device(browser, label):
    ctx = browser.new_context(**{**PHONE, "locale": "zh-CN"})
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
    quiet = ("401", "404", "409", "429")
    page.on("console", lambda m: errors.append(f"{label} console: {m.text}") if m.type == "error" and not any(q in m.text for q in quiet) else None)
    return page

def join(page, url, name, pin, wish=""):
    page.goto(url); page.wait_for_selector("#joinCard:not([hidden])")
    page.fill("#joinName", name); page.fill("#joinPin", pin); page.fill("#joinWish", wish); page.click("#joinBtn")
    page.wait_for_selector("#meCard:not([hidden])")
    assert name in page.inner_text("#meTitle")

with sync_playwright() as p:
    if os.environ.get("ENGINE") == "webkit":        # Safari's engine: what iPhones use, including inside WeChat
        browser = p.webkit.launch(); PHONE.clear(); PHONE.update(p.devices["iPhone 13"])
    else:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])

    # the organizer creates an event on the home page
    org = device(browser, "organizer")
    org.goto(SITE); org.wait_for_selector("#createCard:not([hidden])")
    assert not org.is_visible("#joinCard") and not org.is_visible("#adminCard")
    org.fill("#newBudget", "$50 以内"); org.fill("#newDate", "2026-12-24")
    org.screenshot(path=str(OUT / "1-home.png"), full_page=True)
    org.click("#createBtn"); org.wait_for_selector("#createdCard:not([hidden])")
    share, admin_link = org.input_value("#newShare"), org.input_value("#newAdmin")
    assert share.startswith(SITE + "?e=") and admin_link.startswith(share + "#admin="), (share, admin_link)
    org.screenshot(path=str(OUT / "2-created.png"), full_page=True)
    org.click("#newOpen"); org.wait_for_selector("#adminCard:not([hidden])")
    assert org.url == share and org.inner_text("#fDate") == "12/24 周四" and org.inner_text("#fBudget") == "$50 以内"
    org.fill("#joinName", "Jennifer"); org.fill("#joinPin", "1111"); org.fill("#joinWish", "好喝的红酒"); org.click("#joinBtn")
    org.wait_for_selector("#meCard:not([hidden])")

    fresh = device(browser, "fresh"); join(fresh, share, "小雪", "2222", "猫咪周边")
    mia = device(browser, "mia"); join(mia, share, "Mia", "3333", "毛绒拖鞋")
    jie = device(browser, "jie"); join(jie, share, "阿杰", "4444")
    assert not fresh.is_visible("#adminCard")

    # what a phone keeps: a random token, not the PIN
    kept = fresh.evaluate("JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")
    assert "2222" not in kept and "token" in kept and "pin" not in kept.lower(), kept
    fresh.reload(); fresh.wait_for_selector("#meCard:not([hidden])")          # comes back without typing anything

    # another device: tap your name, wrong PIN, then the right one
    other = device(browser, "other"); other.goto(share); other.wait_for_selector("#names button")
    other.locator("#names button", has_text="Mia").click(); other.fill("#dlgPin", "0000"); other.click("#dlgOk")
    other.wait_for_selector("#dlgBody p:has-text('PIN 不对')")
    other.fill("#dlgPin", "3333"); other.click("#dlgOk"); other.wait_for_selector("#meCard:not([hidden])")
    assert "Mia" in other.inner_text("#meTitle") and other.input_value("#wishInput") == "毛绒拖鞋"
    other.click("#logoutBtn"); other.wait_for_selector("#names button")
    assert "token" not in other.evaluate("JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")

    # opening the organizer link on a new device gives the panel, and the key leaves the address bar
    org2 = device(browser, "org2"); org2.goto(admin_link); org2.wait_for_selector("#adminCard:not([hidden])")
    assert org2.url == share, org2.url
    org2.evaluate("document.getElementById('manage').open = true")                      # participant management is collapsed by default
    org2.wait_for_selector("#adminList li >> nth=3")
    org2.screenshot(path=str(OUT / "3-organizer.png"), full_page=True)
    org2.click("#drawBtn"); org2.click("#dlgOk"); org2.wait_for_selector("#reopenBtn:not([hidden])")
    assert "看不到" in org2.inner_text("#adminStatus")

    gives = {}
    for page, name in ((org, "Jennifer"), (fresh, "小雪"), (mia, "Mia"), (jie, "阿杰")):
        page.reload(); page.wait_for_selector("#tagSlot .tag-back")
        assert not page.is_visible("#joinCard") and not page.is_visible("#leaveBtn")
        page.click("#tagSlot .tag-back"); page.wait_for_timeout(450)
        gives[name] = page.inner_text("#tagSlot .tag-name")
    print("draw:", gives)
    assert sorted(gives.values()) == sorted(gives) and all(k != v for k, v in gives.items())
    fresh.screenshot(path=str(OUT / "4-result.png"), full_page=True)

    # a stranger after the draw sees names only, and a wrong PIN gets nowhere
    late = device(browser, "late"); late.goto(share); late.wait_for_selector("#names button")
    assert not late.is_visible("#joinCard") and "TO ·" not in late.inner_text("body")
    late.locator("#names button", has_text="Jennifer").click(); late.fill("#dlgPin", "2222"); late.click("#dlgOk")
    late.wait_for_selector("#dlgBody p:has-text('PIN 不对')"); late.click("#dlgCancel")

    # the home page remembers this device's events; a bad link says so; deleting the event kills the link
    org.goto(SITE); org.wait_for_selector("#mineCard:not([hidden])")
    assert "我组织的" in org.inner_text("#mineList")
    late.goto(SITE + "?e=nope"); late.wait_for_selector("#goneCard:not([hidden])")
    org2.click("text=我的组织者链接"); org2.click("#destroyBtn"); org2.click("#dlgOk"); org2.wait_for_selector("#goneCard:not([hidden])")
    fresh.reload(); fresh.wait_for_selector("#goneCard:not([hidden])")
    browser.close()

print("page errors:", errors or "none")
imgs = [Image.open(OUT / n) for n in ("1-home.png", "2-created.png", "3-organizer.png", "4-result.png")]
sheet = Image.new("RGB", (sum(i.width for i in imgs) + 30 * 3, max(i.height for i in imgs)), "#444")
x = 0
for i in imgs:
    sheet.paste(i, (x, 0)); x += i.width + 30
sheet.thumbnail((2400, 2400)); sheet.save(OUT / "sheet.png")
sys.exit(1 if errors else 0)
