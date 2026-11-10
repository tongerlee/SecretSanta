"""The same event walked through once per language, on phones set to that language:
the page must pick the phone's language by itself, read whole (nothing missing, nothing overflowing),
switch on tap without a reload, remember the choice, and honour ?lang= in a link.
Run it with  ./test/browser.sh  (it needs a freshly started local server: the API only lets one network
create 5 events an hour, and this walk-through creates 4 of them through the page)."""
import json, os, pathlib, sys, urllib.request
from playwright.sync_api import sync_playwright
from PIL import Image

SITE = os.environ.get("SITE", "http://127.0.0.1:8787/")
API = os.environ.get("API", "http://127.0.0.1:8788")
OUT = pathlib.Path(__file__).parent / "shots"; OUT.mkdir(exist_ok=True)
errors = []

# phone language -> what the page should show, and names people there would really type
CASES = {
    "zh": dict(locale="zh-CN", html="zh-CN", join="报名", people=["小雪", "阿杰", "Jennifer"], wish="猫咪周边", budget="$50 以内", day="12/24 周四"),
    "en": dict(locale="en-US", html="en", join="Sign up", people=["Zoë", "Mia", "Jennifer"], wish="Fuzzy slippers", budget="under $50", day="Thu, Dec 24"),
    "ko": dict(locale="ko-KR", html="ko", join="참가하기", people=["지민", "서연", "민준"], wish="따뜻한 목도리", budget="$50 이하", day="12월 24일 (목)"),
    "vi": dict(locale="vi-VN", html="vi", join="Đăng ký tham gia", people=["Hương", "Nguyễn An", "Minh"], wish="Nến thơm", budget="dưới $50", day="Thứ Năm, 24/12"),
}

def phone(browser, label, locale):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, locale=locale)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
    quiet = ("401", "404", "409", "429")
    page.on("console", lambda m: errors.append(f"{label} console: {m.text}") if m.type == "error" and not any(q in m.text for q in quiet) else None)
    return page

def post(path, body):
    # its own pretend network address, so these calls do not use up the "5 new events an hour per network" allowance
    req = urllib.request.Request(API + "/api/" + path, data=json.dumps(body).encode(), method="POST", headers={"x-test-ip": "203.0.113.9"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def check(page, label):
    """Nothing half-filled, nothing wider than the phone."""
    text = page.inner_text("body")
    for bad in ("undefined", "NaN", "[object", "${"):
        if bad in text: errors.append(f"{label}: '{bad}' on the page")
    wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if wide > 0: errors.append(f"{label}: page is {wide}px wider than the phone")
    spill = page.evaluate("""() => [...document.querySelectorAll('button, .pill, .nametag, h2, label, dt, dd, summary, .tag-side *')]
        .filter(n => n.offsetParent && n.scrollWidth > n.clientWidth + 1).map(n => (n.id || n.className || n.tagName) + ': ' + n.textContent.slice(0, 30))""")
    if spill: errors.append(f"{label}: text spills out of {spill}")
    empty = page.evaluate("""() => [...document.querySelectorAll('[data-i18n]')].filter(n => !n.textContent.trim()).map(n => n.dataset.i18n)""")
    if empty: errors.append(f"{label}: untranslated {empty}")

def shot(page, lang, name, full=True):
    check(page, f"{lang}/{name}")
    page.screenshot(path=str(OUT / f"{lang}-{name}.png"), full_page=full)

def walk(browser, lang, c):
    a, b, me = c["people"]
    org = phone(browser, lang + " organizer", c["locale"])
    org.goto(SITE); org.wait_for_selector("#createCard:not([hidden])")
    assert org.evaluate("document.documentElement.lang") == c["html"], (lang, "did not follow the phone's language")
    assert org.get_attribute(f"#langs button[data-lang={lang}]", "aria-pressed") == "true"
    org.fill("#newBudget", c["budget"]); org.fill("#newDate", "2026-12-24")
    shot(org, lang, "1-home")
    org.click("#createBtn"); org.wait_for_selector("#createdCard:not([hidden])")
    share = org.input_value("#newShare")
    shot(org, lang, "2-created")
    org.click("#newOpen"); org.wait_for_selector("#adminCard:not([hidden])")
    assert org.inner_text("#joinCard h2") == c["join"] and org.inner_text("#fDate") == c["day"], (org.inner_text("#joinCard h2"), org.inner_text("#fDate"))
    org.fill("#joinName", me); org.fill("#joinPin", "1111"); org.fill("#joinWish", c["wish"]); org.click("#joinBtn")
    org.wait_for_selector("#meCard:not([hidden])")
    eid = share.split("?e=")[1]
    post("join", {"id": eid, "name": a, "pin": "2222", "wish": c["wish"]})
    post("join", {"id": eid, "name": b, "pin": "3333", "wish": ""})
    org.click("#settings summary"); org.fill("#cfgDrawAt", "2026-12-20T20:00"); org.click("#cfgSave")
    org.wait_for_selector("#deadline:not([hidden])"); org.wait_for_selector("#adminOk:not(:empty)")
    assert not org.evaluate("document.getElementById('manage').open"), "participant management should start collapsed"
    org.evaluate("document.getElementById('manage').open = true")
    org.wait_for_selector("#adminList li >> nth=2")
    shot(org, lang, "3-organizer")

    # someone who has not signed up yet, then a wrong PIN on a name
    new = phone(browser, lang + " newcomer", c["locale"]); new.goto(share); new.wait_for_selector("#names button")
    shot(new, lang, "4-newcomer")
    new.locator("#names button", has_text=a).click(); new.fill("#dlgPin", "0000"); new.click("#dlgOk")
    new.wait_for_selector("#dlgBody p:has-text('4')")                    # "4 tries left", in every language
    shot(new, lang, "5-wrong-pin", full=False)
    new.click("#dlgCancel")

    # organizer resets a PIN, then draws
    org.locator("#adminList li", has_text=a).locator("button[data-act=setPin]").click(); org.fill("#dlgPin", "5555"); org.click("#dlgOk")
    org.wait_for_selector("#adminOk:has-text('5555')")
    org.click("#drawBtn"); org.wait_for_selector("#dlg[open]")
    shot(org, lang, "6-draw-dialog", full=False)
    org.click("#dlgOk"); org.wait_for_selector("#reopenBtn:not([hidden])")
    org.wait_for_selector("#tagSlot .tag-back")
    org.locator("#tagSlot").scroll_into_view_if_needed(); org.evaluate("window.scrollBy(0, -140)")
    shot(org, lang, "7-tag-back", full=False)
    org.click("#tagSlot .tag-back"); org.wait_for_timeout(450)
    got = org.inner_text("#tagSlot .tag-name")
    assert got in (a, b), got
    plain = "plain" in (org.get_attribute("#tagSlot .tag-name", "class") or "")
    assert plain == (got in ("Hương", "Nguyễn An")), (got, plain)        # only names the handwriting font cannot draw
    shot(org, lang, "8-tag-front", full=False)
    org.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    shot(org, lang, "9-organizer-drawn", full=False)

    # the person whose PIN was reset comes back on a new phone and sees the note about it
    back = phone(browser, lang + " returning", c["locale"]); back.goto(share); back.wait_for_selector("#names button")
    back.locator("#names button", has_text=a).click(); back.fill("#dlgPin", "5555"); back.click("#dlgOk")
    back.wait_for_selector("#resetNote:not([hidden])")
    shot(back, lang, "10-returning", full=False)
    return share

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or "/usr/bin/google-chrome", args=["--no-sandbox"])
    shares = {lang: walk(browser, lang, c) for lang, c in CASES.items()}

    # a phone in a language we do not have gets English, and can switch with one tap; the choice survives a reload
    fresh = SITE + "?e=" + post("create", {"title": "Secret Santa 2026", "budget": "$50", "exchangeOn": "2026-12-24"})["id"]   # sign-ups still open
    fr = phone(browser, "french phone", "fr-FR"); fr.goto(fresh); fr.wait_for_selector("#joinCard:not([hidden])")
    assert fr.evaluate("document.documentElement.lang") == "en" and fr.inner_text("#joinCard h2") == "Sign up"
    fr.fill("#joinName", "Camille")                                                     # typing must survive a switch
    for lang, c in CASES.items():
        fr.click(f"#langs button[data-lang={lang}]")
        assert fr.inner_text("#joinCard h2") == c["join"] and fr.evaluate("document.documentElement.lang") == c["html"], lang
        assert fr.input_value("#joinName") == "Camille"
        check(fr, "switch to " + lang)
    fr.click("#langs button[data-lang=ko]"); fr.reload(); fr.wait_for_selector("#joinCard:not([hidden])")
    assert fr.inner_text("#joinCard h2") == "참가하기"
    assert fr.evaluate("localStorage.getItem('santa:lang')") == '"ko"'

    # ?lang=vi in a link wins over the phone's language, then leaves the address bar
    link = phone(browser, "lang link", "zh-CN"); link.goto(fresh + "&lang=vi"); link.wait_for_selector("#joinCard:not([hidden])")
    assert link.inner_text("#joinCard h2") == "Đăng ký tham gia" and link.url == fresh, link.url
    link.click("#langs button[data-lang=zh]"); link.reload(); link.wait_for_selector("#joinCard:not([hidden])")
    assert link.inner_text("#joinCard h2") == "报名"                                     # a later choice sticks
    home = phone(browser, "lang link home", "en-US"); home.goto(SITE + "?lang=ko"); home.wait_for_selector("#createCard:not([hidden])")
    assert home.evaluate("document.documentElement.lang") == "ko" and home.url == SITE, home.url

    # a result already showing is rewritten when the language changes (and goes back face down)
    seen = phone(browser, "switch on result", "en-US"); seen.goto(shares["en"]); seen.wait_for_selector("#names button")
    seen.locator("#names button", has_text="Mia").click(); seen.fill("#dlgPin", "3333"); seen.click("#dlgOk")
    seen.wait_for_selector("#tagSlot .tag-back"); seen.click("#tagSlot .tag-back"); seen.wait_for_timeout(450)
    assert "TO · You" in seen.inner_text("#tagSlot")
    seen.click("#langs button[data-lang=ko]")
    assert seen.is_visible("#tagSlot .tag-back") and "눌러서 확인" in seen.inner_text("#tagSlot .tag-back")
    # when AWS turns a request away because too many arrived at once (a bare 429), the page quietly asks again...
    cors = {"access-control-allow-origin": SITE.rstrip("/"), "content-type": "application/json"}
    hits = {"join": 0, "login": 0}
    def turned_away(route):
        hits["join"] += 1
        if hits["join"] <= 2: route.fulfill(status=429, headers=cors, body='{"Reason":"ConcurrentInvocationLimitExceeded","Type":"User","message":"Rate Exceeded."}')
        else: route.continue_()
    busy = phone(browser, "turned away", "en-US"); busy.goto(fresh); busy.wait_for_selector("#joinCard:not([hidden])")
    busy.route(API + "/api/join", turned_away)
    busy.fill("#joinName", "Patient"); busy.fill("#joinPin", "1234"); busy.click("#joinBtn")
    busy.wait_for_selector("#meCard:not([hidden])", timeout=15000)
    assert hits["join"] == 3 and busy.inner_text("#joinErr") == "", hits
    # ...but a real answer that happens to use the same status (a locked PIN) is shown at once, not retried
    def locked(route):
        hits["login"] += 1; route.fulfill(status=429, headers=cors, body='{"error":"locked","retryAfter":600}')
    other = phone(browser, "locked", "en-US"); other.goto(fresh); other.wait_for_selector("#names button")
    other.route(API + "/api/login", locked)
    other.locator("#names button", has_text="Patient").click(); other.fill("#dlgPin", "1234"); other.click("#dlgOk")
    other.wait_for_selector("#dlgBody p:has-text('Locked for 10 minutes')")
    assert hits["login"] == 1, hits

    # ---- the review's checklist: desktop and the two phone widths, organizer vs participant, keyboard, reduced motion
    drawn_share = shares["en"]
    for label, w, h in (("320 phone", 320, 568), ("390 phone", 390, 844), ("desktop", 1280, 800)):
        for name, url, wait in (("home", SITE, "#createCard:not([hidden])"), ("open event", fresh, "#joinCard:not([hidden])"), ("drawn event", drawn_share, "#names button")):
            pg = phone(browser, f"{label} {name}", "en-US"); pg.set_viewport_size({"width": w, "height": h})
            pg.goto(url); pg.wait_for_selector(wait); pg.wait_for_timeout(400)
            check(pg, f"{label} / {name}")
            if name == "open event":
                # the first thing to do (tap your name, or sign up) must start within the first screen on a phone
                top = pg.evaluate("[...document.querySelectorAll('main > .card:not([hidden])')][0].getBoundingClientRect().top")
                if top > h - 120: errors.append(f"{label}: first card starts at {top:.0f}px, too far down for a {h}px screen")
                if w == 320: pg.screenshot(path=str(OUT / "check-320-event.png"), full_page=True)
            if name == "home" and w == 1280: pg.screenshot(path=str(OUT / "check-desktop-home.png"), full_page=False)
            pg.context.close()

    # saved events sit above "start a new event" once this device has any
    home = phone(browser, "home order", "en-US"); home.goto(SITE); home.wait_for_selector("#createCard:not([hidden])")
    assert home.is_hidden("#mineCard"), "no saved events yet, so the list should be hidden"
    home.fill("#newDate", "2026-12-24"); home.click("#createBtn"); home.wait_for_selector("#createdCard:not([hidden])")
    home.goto(SITE); home.wait_for_selector("#mineCard:not([hidden])")
    order = home.evaluate("[...document.querySelectorAll('main > .card:not([hidden])')].map(c => c.id)")
    assert order.index("mineCard") < order.index("createCard"), order
    assert home.is_visible("#createCard"), "starting a new event must stay available underneath"

    # organizer view vs participant view of the same event
    org_view = phone(browser, "organizer view", "en-US"); org_view.goto(home.get_attribute("#mineList a", "href") + "#admin=" + home.evaluate("localStorage.getItem('santa:' + document.querySelector('#mineList a').href.split('?e=')[1] + ':admin')").strip('"'))
    org_view.wait_for_selector("#adminCard:not([hidden])")
    assert "Only visible to you on this device" in org_view.inner_text("#adminCard") and org_view.inner_text("#adminTitle").strip() == "Organizer controls"
    assert not org_view.evaluate("document.getElementById('manage').open")
    part_view = phone(browser, "participant view", "en-US"); part_view.goto(org_view.url); part_view.wait_for_selector("#joinCard:not([hidden])")
    assert part_view.is_hidden("#adminCard") and part_view.evaluate("localStorage.length") == 0 or part_view.is_hidden("#adminCard")

    # the pause control: visible on the picture, works by mouse and keyboard, stops pets, lights and snow together
    pg = phone(browser, "motion", "en-US"); pg.goto(fresh); pg.wait_for_selector("#joinCard:not([hidden])")
    box = pg.evaluate("(() => { const r = document.getElementById('motionBtn').getBoundingClientRect(); return [r.top, r.right, r.width, r.height]; })()")
    assert box[0] < 120 and box[2] >= 44 and box[3] >= 44, box
    assert pg.get_attribute("#motionBtn", "aria-label") == "Pause animations (pets, snow, lights)"
    pg.click("#motionBtn")
    assert pg.get_attribute("#motionBtn", "aria-pressed") == "true" and pg.get_attribute("#motionBtn", "aria-label") == "Play animations"
    states = pg.evaluate("[...document.querySelectorAll('.bulb, .stage .pets')].map(n => getComputedStyle(n).animationPlayState)")
    assert states and all(st == "paused" for st in states), states
    # snow: the canvas must stop changing
    a = pg.evaluate("document.getElementById('snow').toDataURL()"); pg.wait_for_timeout(300); b = pg.evaluate("document.getElementById('snow').toDataURL()")
    assert a == b, "snow kept falling while paused"
    pg.click("#motionBtn")
    pg.wait_for_timeout(300); c = pg.evaluate("document.getElementById('snow').toDataURL()")
    assert c != b, "snow did not resume"
    pg.click("#langs button[data-lang=ko]")
    assert pg.get_attribute("#motionBtn", "aria-label") == "애니메이션 멈추기 (동물, 눈, 전구)"
    # keyboard only: the control is the first thing Tab reaches, and Enter works it
    kb = phone(browser, "keyboard", "en-US"); kb.goto(fresh); kb.wait_for_selector("#joinCard:not([hidden])")
    kb.keyboard.press("Tab")
    assert kb.evaluate("document.activeElement.id") == "motionBtn"
    kb.keyboard.press("Enter")
    assert kb.get_attribute("#motionBtn", "aria-pressed") == "true"
    kb.keyboard.press("Tab"); kb.keyboard.press("Tab"); kb.keyboard.press("Tab")        # past the two pets, onto the first language button
    assert kb.evaluate("document.activeElement.closest('#langs') !== null")

    # reduced motion: nothing animates and the control is not shown
    rm = browser.new_context(viewport={"width": 390, "height": 844}, locale="en-US", reduced_motion="reduce").new_page()
    rm.goto(fresh); rm.wait_for_selector("#joinCard:not([hidden])")
    assert rm.is_hidden("#motionBtn")
    assert all(n == "none" for n in rm.evaluate("[...document.querySelectorAll('.bulb, .stage .pets')].map(n => getComputedStyle(n).animationName)"))

    # time zone shows next to the automatic draw time (the walk-throughs set one on every event)
    ev = post("create", {"title": "Deadline check", "budget": "$20", "exchangeOn": "2026-12-24"})
    post("admin", {"id": ev["id"], "adminKey": ev["adminKey"], "action": "settings", "title": "Deadline check", "budget": "$20", "exchangeOn": "2026-12-24", "drawAt": 1797897600000})
    dl = phone(browser, "deadline", "en-US"); dl.goto(SITE + "?e=" + ev["id"]); dl.wait_for_selector("#deadline:not([hidden])")
    assert "Sign-ups close" in dl.inner_text("#deadline") and " (" in dl.inner_text("#deadline") and ")" in dl.inner_text("#deadline"), dl.inner_text("#deadline")

    # the stamp sits on the revealed tag but stays secondary (smaller than everything it must not compete with)
    seen.click("#langs button[data-lang=en]"); seen.wait_for_selector("#tagSlot .tag-back"); seen.click("#tagSlot .tag-back"); seen.wait_for_timeout(450)
    sizes = seen.evaluate("(() => { const f = (s) => parseFloat(getComputedStyle(document.querySelector(s)).fontSize); return [f('.tag-stamp'), f('.tag-name'), f('.tag-wish'), f('.tag-meta')]; })()")
    assert sizes[0] < min(sizes[1:]), sizes
    assert "approved for unnecessary thoughtfulness" in seen.inner_text("#tagSlot").lower()      # inner_text applies the uppercase styling
    check(seen, "tag with stamp")
    seen.screenshot(path=str(OUT / "check-tag-stamp.png"), full_page=False)
    browser.close()

print("problems:", errors or "none")
# contact sheets to look over: per language, the ten phone screens in rows of five (tall pages are cut at 1150px)
names = ["1-home", "2-created", "3-organizer", "4-newcomer", "5-wrong-pin", "6-draw-dialog", "7-tag-back", "8-tag-front", "9-organizer-drawn", "10-returning"]
for lang in CASES:
    for part, group in (("a", names[:4]), ("b", names[4:7]), ("c", names[7:])):
        imgs = [Image.open(OUT / f"{lang}-{n}.png") for n in group]
        imgs = [i.crop((0, 0, i.width, min(i.height, 1500))) for i in imgs]
        sheet = Image.new("RGB", (sum(i.width for i in imgs) + 12 * (len(imgs) - 1), max(i.height for i in imgs)), "#555")
        x = 0
        for i in imgs:
            sheet.paste(i, (x, 0)); x += i.width + 12
        sheet.save(OUT / f"sheet-{lang}-{part}.png")
sys.exit(1 if errors else 0)
