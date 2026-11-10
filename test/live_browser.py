"""Real-browser checks of a DEPLOYED site: one test event, many 'devices', both Chrome and the Safari engine (WebKit).
Covers what only a browser on the real address can show: the cross-origin calls, copy buttons, hostile names on screen,
four languages on four screen sizes, live updates, lock-out, organizer tools, an API outage, blocked storage.
The test event is deleted at the end, pass or fail.

    SITE=https://main.xxxx.amplifyapp.com API=https://xxxx.lambda-url.<region>.on.aws python3 test/live_browser.py
    (needs: pip install playwright pillow requests; Chrome, CHROME=/path/to/chrome if not found; optional: playwright install webkit)"""
import json, os, pathlib, sys, time
import requests
from playwright.sync_api import sync_playwright

SITE = os.environ["SITE"].rstrip("/") + "/"
API = os.environ["API"].rstrip("/")
OUT = pathlib.Path(__file__).parent / "shots"; OUT.mkdir(exist_ok=True)
results, problems, api_seen, EVENT = [], [], [], []
QUIET = ("401", "403", "404", "409", "429")      # expected "no" answers that browsers also log as console errors
MUTED = {"outage"}                                # devices whose failed requests are the point of the test
LOCALES = {"zh": "zh-CN", "en": "en-US", "ko": "ko-KR", "vi": "vi-VN"}
SIZES = {"small phone": (320, 568), "phone": (390, 844), "tablet": (768, 1024), "laptop": (1280, 800)}

def note(ok, name, detail=""):
    results.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   -> " + str(detail)[:400]) if detail and not ok else ""), flush=True)
    return bool(ok)

def step(name, fn):
    """Run one check; a crash in it is a failure of that check, and the rest still runs."""
    try:
        out = fn()
        return note(out is None or out is True or (isinstance(out, tuple) and out[0]), name, out[1] if isinstance(out, tuple) else "")
    except Exception as e:
        return note(False, name, type(e).__name__ + ": " + str(e).split("Call log")[0].strip()[:300])

def phone(browser, label, locale="en-US", size=(390, 844), **more):
    opts = dict(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1, locale=locale); opts.update(more)
    page = browser.new_context(**opts).new_page()
    page.on("pageerror", lambda e: problems.append(f"{label}: script error: {e}"))
    page.on("console", lambda m: problems.append(f"{label}: console error: {m.text[:200]}") if m.type == "error" and label not in MUTED and not any(q in m.text for q in QUIET) else None)
    page.on("requestfailed", lambda r: problems.append(f"{label}: request failed: {r.url[:90]}") if label not in MUTED else None)
    page.on("response", lambda r: api_seen.append((r.url.split("/api/")[-1], r.status)) if "/api/" in r.url else None)
    page.on("dialog", lambda d: (problems.append(f"{label}: a browser pop-up appeared: {d.message}"), d.dismiss()))
    return page

def post(path, body):
    r = requests.post(API + "/api/" + path, data=json.dumps(body), timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)

def layout(page):
    """Nothing half-filled, nothing wider than the screen, nothing untranslated."""
    bad = []
    text = page.inner_text("body")
    bad += [f"'{w}' on the page" for w in ("undefined", "NaN", "[object", "${") if w in text]
    wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if wide > 0: bad.append(f"page is {wide}px wider than the screen")
    bad += page.evaluate("""() => [...document.querySelectorAll('button, .pill, .nametag, h1, h2, label, dt, dd, summary, small, .tag-side *')]
        .filter(n => n.offsetParent && n.scrollWidth > n.clientWidth + 1).map(n => 'text spills out of ' + (n.id || n.className || n.tagName) + ': ' + n.textContent.slice(0, 30))""")
    bad += page.evaluate("() => [...document.querySelectorAll('[data-i18n]')].filter(n => !n.textContent.trim()).map(n => 'untranslated: ' + n.dataset.i18n)")
    return (not bad, bad)

def run(p):
    chrome = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])

    # ---------------------------------------------------------------- first visit
    print("\n== first visit")
    first = phone(chrome, "first visit")
    first.goto(SITE, wait_until="load"); first.wait_for_selector("#createCard:not([hidden])")
    nav = first.evaluate("(() => { const n = performance.getEntriesByType('navigation')[0]; return [Math.round(n.responseStart), Math.round(n.domContentLoadedEventEnd), Math.round(n.loadEventEnd)]; })()")
    res = first.evaluate("performance.getEntriesByType('resource').map(r => [r.name.split('/').pop().slice(0, 40), r.transferSize, Math.round(r.duration)])")
    print(f"      first byte {nav[0]} ms, page usable {nav[1]} ms, everything loaded {nav[2]} ms; files: {res}")
    note(nav[1] < 3000, "page is usable within 3 seconds on a first visit", nav)
    step("home page layout", lambda: layout(first))
    step("home page pulls nothing from unexpected places", lambda: (all(any(h in u for h in (SITE, API, "fonts.googleapis.com", "fonts.gstatic.com")) for u in first.evaluate("performance.getEntriesByType('resource').map(r => r.name)")), first.evaluate("performance.getEntriesByType('resource').map(r => r.name)")))

    # ---------------------------------------------------------------- create, copy buttons
    print("\n== creating an event on the real site")
    org = phone(chrome, "organizer", permissions=["clipboard-read", "clipboard-write"])
    org.goto(SITE); org.wait_for_selector("#createCard:not([hidden])")
    org.fill("#newTitle", "TEST please ignore"); org.fill("#newBudget", "under $50"); org.fill("#newDate", "2026-12-24")
    org.click("#createBtn"); org.wait_for_selector("#createdCard:not([hidden])", timeout=20000)
    share, admin_link = org.input_value("#newShare"), org.input_value("#newAdmin")
    EVENT[:] = [share.split("?e=")[1], admin_link.split("#admin=")[1]]
    eid = EVENT[0]
    note(share.startswith(SITE + "?e=") and admin_link.startswith(share + "#admin="), "the page hands out a group link and an organizer link", (share, admin_link[:60]))
    def copies():
        org.click("button[data-copy=newShare]"); org.wait_for_selector("#toast:not([hidden])")
        a = org.evaluate("navigator.clipboard.readText()")
        org.click("button[data-copy=newAdmin]"); org.wait_for_timeout(200)
        b = org.evaluate("navigator.clipboard.readText()")
        return (a == share and b == admin_link, (a, b[:50]))
    step("copy buttons put the right links on the clipboard", copies)
    org.click("#newOpen"); org.wait_for_selector("#adminCard:not([hidden])", timeout=20000)
    note(org.url == share, "the organizer key is not left in the address bar", org.url)
    org.fill("#joinName", "Jennifer"); org.fill("#joinPin", "1111"); org.fill("#joinWish", "good red wine"); org.click("#joinBtn")
    org.wait_for_selector("#meCard:not([hidden])", timeout=20000)
    kept = org.evaluate("JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")
    note("1111" not in kept and '\\"token\\"' in kept, "the phone keeps a random token, never the PIN", kept[:300])

    # ---------------------------------------------------------------- hostile and unusual names on screen
    print("\n== unusual names on screen")
    crowd = [("<img src=x onerror=1>", "<b>bold</b> <script>1</script>"), ("\"><script>1</script>", "x"), ("Nguyễn Thị Hương", "nến thơm"), ("지민", "따뜻한 목도리"),
             ("小雪", "猫咪周边"), ("🎄 Zoë", "🎁"), ("Wolfeschlegelsteinhausen", "W" * 100), ("Mia", "")]
    for name, wish in crowd:
        s, d = post("join", {"id": eid, "name": name, "pin": "1234", "wish": wish})
        if s != 200: note(False, f"sign-up of {name!r}", (s, d))
    org.reload(); org.wait_for_selector("#manage"); org.evaluate("document.getElementById('manage').open = true"); org.wait_for_selector(f"#adminList li >> nth={len(crowd)}", timeout=20000)
    def harmless():
        dom = org.evaluate("({ imgs: document.images.length, petImgs: document.querySelectorAll('img.pets').length, scripts: document.scripts.length, planted: document.querySelectorAll('img[src=\"x\"], main script, #adminList small b, #adminList small script, #names script').length, hit: window.__xss || 0 })")
        shown = org.inner_text("#adminList")
        # the only images on the page are the pet artwork (two clipped copies of pets.png); anything else was planted
        ok = dom["imgs"] == dom["petImgs"] and dom["scripts"] == 2 and dom["planted"] == 0 and "<img src=x onerror=1>" in shown and "<script>1</script>" in shown
        return (ok, (dom, shown[:200]))
    step("names and wishes containing HTML show as plain text and run nothing", harmless)
    step("organizer page layout with long and unusual names", lambda: layout(org))
    org.screenshot(path=str(OUT / "live-organizer.png"), full_page=True)

    # ---------------------------------------------------------------- 4 languages x 4 screen sizes
    print("\n== four languages on four screen sizes")
    for lang, locale in LOCALES.items():
        for label, size in SIZES.items():
            def one():
                pg = phone(chrome, f"{lang} {label}", locale, size)
                pg.goto(share); pg.wait_for_selector("#names button", timeout=20000)
                if pg.evaluate("document.documentElement.lang")[:2] != lang: return (False, "did not follow the device language")
                out = layout(pg)
                if label in ("small phone", "laptop"): pg.screenshot(path=str(OUT / f"live-{lang}-{size[0]}.png"), full_page=True)
                pg.context.close()
                return out
            step(f"{lang} on a {label} ({size[0]}px)", one)

    # ---------------------------------------------------------------- live update, lock-out, PIN reset, wish, leave
    print("\n== a second phone: live update, lock-out, reset, leaving")
    watcher = phone(chrome, "second phone"); watcher.goto(share); watcher.wait_for_selector("#names button")
    post("join", {"id": eid, "name": "Latecomer", "pin": "4321", "wish": ""})
    t0 = time.time()
    def appears():
        watcher.wait_for_selector("#names button:has-text('Latecomer')", timeout=45000)
        return (True, f"{time.time() - t0:.0f}s")
    step("a new sign-up shows up on an open page by itself (within ~30 s)", appears)
    def lockout():
        watcher.locator("#names button", has_text="Latecomer").click()
        for left in ("4 tries", "3 tries", "2 tries", "1 try"):
            watcher.fill("#dlgPin", "0000"); watcher.click("#dlgOk"); watcher.wait_for_selector(f"#dlgBody p:has-text('{left} left')", timeout=15000)
        watcher.fill("#dlgPin", "0000"); watcher.click("#dlgOk"); watcher.wait_for_selector("#dlgBody p:has-text('Locked for 10 minutes')", timeout=15000)
        watcher.fill("#dlgPin", "4321"); watcher.click("#dlgOk"); watcher.wait_for_selector("#dlgBody p:has-text('Locked for 10 minutes')", timeout=15000)
        watcher.click("#dlgCancel")
    step("five wrong PINs: the page counts down, then says the name is locked, even for the right PIN", lockout)
    def reset():
        org.reload(); org.wait_for_selector("#manage"); org.evaluate("document.getElementById('manage').open = true"); org.wait_for_selector("#adminList li:has-text('Latecomer')", timeout=20000)
        row = org.locator("#adminList li", has_text="Latecomer")
        if "locked" not in row.inner_text(): return (False, row.inner_text())
        row.locator("button[data-act=setPin]").click(); org.fill("#dlgPin", "8888"); org.click("#dlgOk")
        org.wait_for_selector("#adminOk:has-text('8888')", timeout=15000)
    step("organizer sees the lock and resets the PIN from the panel", reset)
    def comes_back():
        watcher.locator("#names button", has_text="Latecomer").click(); watcher.fill("#dlgPin", "8888"); watcher.click("#dlgOk")
        watcher.wait_for_selector("#meCard:not([hidden])", timeout=15000); watcher.wait_for_selector("#resetNote:not([hidden])")
        return ("reset your PIN" in watcher.inner_text("#resetNote"), watcher.inner_text("#resetNote"))
    step("the new PIN lets them in and they are told a reset happened", comes_back)
    def wish():
        watcher.fill("#wishInput", "warm socks"); watcher.click("#wishForm button"); watcher.wait_for_selector("#wishOk:has-text('Saved')", timeout=15000)
        watcher.reload(); watcher.wait_for_selector("#meCard:not([hidden])", timeout=15000)
        return (watcher.input_value("#wishInput") == "warm socks", watcher.input_value("#wishInput"))
    step("a wish can be edited and is still there after a reload", wish)
    def leave():
        watcher.click("#leaveBtn"); watcher.click("#dlgOk"); watcher.wait_for_selector("#joinCard:not([hidden])", timeout=15000)
        return ("Latecomer" not in watcher.inner_text("#rosterCard"), watcher.inner_text("#rosterCard"))
    step("cancelling a sign-up from the page takes the name off the list", leave)

    # ---------------------------------------------------------------- organizer tools from the page
    print("\n== organizer tools")
    def remove():
        org.reload(); org.wait_for_selector("#manage"); org.evaluate("document.getElementById('manage').open = true"); org.wait_for_selector("#adminList li:has-text('<script>')", timeout=20000)
        org.locator("#adminList li", has_text='"><script>').locator("button[data-act=remove]").click(); org.click("#dlgOk")
        org.wait_for_selector("#adminOk:has-text('Removed')", timeout=15000)
        org.locator("#adminList li", has_text='"><script>').wait_for(state="detached", timeout=15000)
        return ('"><script>' not in org.inner_text("#adminList"), org.inner_text("#adminList")[:200])
    step("organizer removes someone from the panel", remove)
    def settings():
        org.click("#settings summary"); org.fill("#cfgTitle", "TEST renamed"); org.fill("#cfgDrawAt", "2026-12-20T20:00"); org.click("#cfgSave")
        org.wait_for_selector("#deadline:not([hidden])", timeout=15000)
        return (org.inner_text("#title") == "TEST renamed" and "Dec 20" in org.inner_text("#deadline"), (org.inner_text("#title"), org.inner_text("#deadline")))
    step("organizer renames the event and sets a sign-up deadline", settings)

    # ---------------------------------------------------------------- an iPhone-like Safari joins the same event
    print("\n== Safari engine (WebKit), set to Korean")
    wk = None
    try:
        safari = p.webkit.launch()
        ctx = safari.new_context(**{**p.devices["iPhone 13"], "locale": "ko-KR"})
        wk = ctx.new_page()
        wk.on("pageerror", lambda e: problems.append(f"safari: script error: {e}"))
        wk.on("console", lambda m: problems.append(f"safari: console error: {m.text[:200]}") if m.type == "error" and not any(q in m.text for q in QUIET) else None)
        def safari_join():
            wk.goto(share); wk.wait_for_selector("#joinCard:not([hidden])", timeout=30000)
            if wk.inner_text("#joinCard h2") != "참가하기": return (False, wk.inner_text("#joinCard h2"))
            wk.fill("#joinName", "사파리"); wk.fill("#joinPin", "2468"); wk.fill("#joinWish", "핫초코"); wk.click("#joinBtn")
            wk.wait_for_selector("#meCard:not([hidden])", timeout=20000)
            return ("사파리" in wk.inner_text("#meTitle"), wk.inner_text("#meTitle"))
        step("Safari engine: page opens in Korean and sign-up works across origins", safari_join)
        step("Safari engine: layout", lambda: layout(wk))
        wk.screenshot(path=str(OUT / "live-safari-joined.png"), full_page=True)
    except Exception as e:
        print("      (Safari engine not available here: " + str(e).splitlines()[0][:120] + ")"); wk = None

    # ---------------------------------------------------------------- draw and results
    print("\n== the draw, from the page")
    def draw():
        org.reload(); org.wait_for_selector("#drawBtn:not([disabled])", timeout=20000)
        org.click("#drawBtn"); org.wait_for_selector("#dlg[open]")
        listed = org.inner_text("#dlgBody")
        org.click("#dlgOk"); org.wait_for_selector("#reopenBtn:not([hidden])", timeout=20000)
        return ("Nguyễn Thị Hương" in listed and "Jennifer" in listed and "Not even you" in org.inner_text("#adminStatusDrawn"), listed)
    step("organizer confirms the list and draws", draw)
    roster = [n for n, _ in crowd if n != '"><script>1</script>'] + ["Jennifer"] + (["사파리"] if wk else [])
    def org_result():
        org.wait_for_selector("#tagSlot .tag-back", timeout=20000); org.click("#tagSlot .tag-back"); org.wait_for_timeout(450)
        got = org.inner_text("#tagSlot .tag-name")
        return (got in roster and got != "Jennifer", got)
    step("organizer's own tag flips to one name that is not their own", org_result)
    def switch_on_result():
        org.click("#langs button[data-lang=vi]")
        ok = org.is_visible("#tagSlot .tag-back") and "Chạm để mở" in org.inner_text("#tagSlot .tag-back")
        out = layout(org)
        org.click("#langs button[data-lang=en]")
        return (ok and out[0], out[1])
    step("switching language on the result page rewrites everything and hides the tag again", switch_on_result)
    if wk:
        def safari_result():
            wk.reload(); wk.wait_for_selector("#tagSlot .tag-back", timeout=30000); wk.tap("#tagSlot .tag-back"); wk.wait_for_timeout(500)
            got = wk.inner_text("#tagSlot .tag-name")
            kept = wk.evaluate("JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")
            wk.screenshot(path=str(OUT / "live-safari-result.png"))
            return (got in roster and got != "사파리" and "2468" not in kept and "내가 선물할 사람" in wk.inner_text("#tagSlot"), (got, wk.inner_text("#tagSlot")[:80]))
        step("Safari engine: comes back without a PIN and the tag flips to a recipient", safari_result)
    def vi_device():
        v = phone(chrome, "vietnamese phone", "vi-VN"); v.goto(share); v.wait_for_selector("#names button", timeout=20000)
        v.locator("#names button", has_text="Nguyễn Thị Hương").click(); v.fill("#dlgPin", "1234"); v.click("#dlgOk")
        v.wait_for_selector("#tagSlot .tag-back", timeout=20000); v.click("#tagSlot .tag-back"); v.wait_for_timeout(450)
        got = v.inner_text("#tagSlot .tag-name")
        v.screenshot(path=str(OUT / "live-vi-result.png"))
        return (got in roster and got != "Nguyễn Thị Hương" and "Bạn sẽ tặng quà cho" in v.inner_text("#tagSlot") and layout(v)[0], (got, layout(v)[1]))
    step("a new Vietnamese phone signs in with name + PIN and sees its result", vi_device)

    # ---------------------------------------------------------------- what leaves the server, seen from the browser
    print("\n== what a stranger's and the organizer's browser actually receive")
    def stranger():
        late = phone(chrome, "stranger")
        with late.expect_response(lambda r: "/api/state" in r.url) as info: late.goto(share)
        body = info.value.text(); late.wait_for_selector("#names button")
        ok = "giftTo" not in body and "wish" not in body and "TO ·" not in late.inner_text("body") and not late.is_visible("#joinCard")
        return (ok, body[:300])
    step("a stranger after the draw gets names only, and no way to sign up", stranger)
    org2 = phone(chrome, "organizer second device")
    def organizer_link():
        with org2.expect_response(lambda r: "/api/admin" in r.url) as info: org2.goto(admin_link)
        body = info.value.text(); org2.wait_for_selector("#adminCard:not([hidden])", timeout=20000)
        return (org2.url == share and "giftTo" not in body and '"to"' not in body and "draw\"" not in body, (org2.url, body[:200]))
    step("the organizer link works on another device, cleans the address bar, and receives no pairs", organizer_link)
    def wrong_link():
        bad = phone(chrome, "wrong organizer link"); bad.goto(share + "#admin=" + "0" * 32); bad.wait_for_selector("#toast:not([hidden])", timeout=20000)
        said = bad.inner_text("#toast")
        return ("organizer link" in said and not bad.is_visible("#adminCard") and bad.evaluate(f"localStorage.getItem('santa:{eid}:admin')") is None, said)
    step("a made-up organizer link is refused and forgotten", wrong_link)
    def home_memory():
        org.goto(SITE); org.wait_for_selector("#mineCard:not([hidden])", timeout=15000)
        href = org.get_attribute("#mineList a", "href"); label = org.inner_text("#mineList")
        org.click("#mineList a"); org.wait_for_selector("#adminCard:not([hidden])", timeout=20000)
        return (href == share and "organizer" in label, (href, label))
    step("the home page remembers this device's event and leads back to it", home_memory)

    # ---------------------------------------------------------------- things going wrong
    print("\n== when things go wrong")
    def outage():
        out = phone(chrome, "outage")
        out.route(API + "/**", lambda route: route.abort())
        out.goto(share); out.wait_for_selector("#notice:not([hidden])", timeout=20000)
        said = out.inner_text("#notice")
        out.unroute(API + "/**"); out.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        out.wait_for_selector("#names button", timeout=20000)
        return ("connect" in said and not out.is_visible("#notice"), said)
    step("if the API cannot be reached the page says so, and recovers by itself when it is back", outage)
    def no_storage():
        ctx = chrome.new_context(viewport={"width": 390, "height": 844}, locale="en-US")
        ctx.add_init_script("Storage.prototype.setItem = function () { throw new DOMException('blocked', 'QuotaExceededError'); };")
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(share); pg.wait_for_selector("#names button", timeout=20000)
        pg.locator("#names button", has_text="지민").click(); pg.fill("#dlgPin", "1234"); pg.click("#dlgOk")
        pg.wait_for_selector("#tagSlot .tag-back", timeout=20000)
        pg.reload(); pg.wait_for_selector("#names button", timeout=20000)          # nothing could be remembered: asks again, does not break
        return (not errs, errs)
    step("with browser storage blocked (strict private modes) the page still works, it just asks for the PIN again", no_storage)
    def dead():
        d = phone(chrome, "dead link"); d.goto(SITE + "?e=aaaaaaaaaaaa"); d.wait_for_selector("#goneCard:not([hidden])", timeout=20000)
        return ("gone" in d.inner_text("#goneCard").lower(), d.inner_text("#goneCard"))
    step("a link to an event that does not exist says so clearly", dead)

    # ---------------------------------------------------------------- delete from the page
    print("\n== deleting the event from the page")
    def destroy():
        org2.click("#adminLinkBox summary"); org2.click("#destroyBtn"); org2.click("#dlgOk"); org2.wait_for_selector("#goneCard:not([hidden])", timeout=20000)
        org.reload(); org.wait_for_selector("#goneCard:not([hidden])", timeout=20000)
        if wk: wk.reload(); wk.wait_for_selector("#goneCard:not([hidden])", timeout=30000)
        return (post("state", {"id": eid})[0] == 404, "still there")
    step("organizer deletes the event; every device then sees it is gone", destroy)
    chrome.close()

with sync_playwright() as p:
    try:
        run(p)
    except Exception as e:
        note(False, "the walk-through stopped early", type(e).__name__ + ": " + str(e)[:400])
    finally:
        if EVENT:
            s, d = post("admin", {"id": EVENT[0], "adminKey": EVENT[1], "action": "destroy"})
            print(f"\ncleanup: test event {EVENT[0]} -> {'deleted now' if s == 200 else 'already gone' if s == 404 else s}")

odd = [c for c in api_seen if c[1] >= 500 or (c[1] == 429 and c[0] not in ("login",))]
note(not odd, "no server errors or throttling seen by any browser", odd[:10])
note(not problems, "no script errors, failed requests or unexpected pop-ups in any browser", problems[:10])
print(f"\nAPI calls made by the browsers: {len(api_seen)}; by answer: " + str({s: sum(1 for c in api_seen if c[1] == s) for s in sorted({c[1] for c in api_seen})}))
failed = [r for r in results if not r[0]]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
for _, name, detail in failed: print("  FAIL", name, "->", str(detail)[:400])
sys.exit(1 if failed else 0)
