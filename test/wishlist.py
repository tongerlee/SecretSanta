"""Wishlist walk-through in a real browser: the owner makes a list, friends pick from it, and the owner never sees
the picks. Also walks the front page: gift exchange or wishlist first, then the form, with the gift-exchange form as it was.
Run through ./test/browser.sh (it starts the local site + API)."""
import json, os, pathlib, sys, urllib.request
from playwright.sync_api import sync_playwright

SITE = os.environ.get("SITE", "http://127.0.0.1:8787/")
API = os.environ.get("API", "http://127.0.0.1:8788")
OUT = pathlib.Path(__file__).parent / "shots"; OUT.mkdir(exist_ok=True)
errors = []

def post(path, body):
    req = urllib.request.Request(API + "/api/" + path, data=json.dumps(body).encode(), method="POST", headers={"x-test-ip": "203.0.113.40"})
    try:
        with urllib.request.urlopen(req) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read())

def phone(browser, label, locale="en-US", width=390, height=844):
    page = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, locale=locale).new_page()
    page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
    quiet = ("400", "401", "404", "409", "429")      # expected answers: a bad link, wrong PIN, gone, taken, locked
    page.on("console", lambda m: errors.append(f"{label} console: {m.text}") if m.type == "error" and not any(q in m.text for q in quiet) else None)
    return page

def spill(page):
    """Anything wider than the screen, or a control whose words do not fit."""
    wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    cut = page.evaluate("""() => [...document.querySelectorAll('button, a.btn, h1, h2, h3, label, .item-text')].filter(n => n.offsetParent && n.scrollWidth > n.clientWidth + 1).map(n => n.textContent.trim().slice(0, 30))""")
    return wide, cut

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or "/usr/bin/google-chrome", args=["--no-sandbox"])

    # ---- front page: first what to make. "Gift exchange" goes straight to the form the home page always had; "wishlist"
    #      asks for the occasion, and the form then wears that look. Back returns to the choice without leaving the site.
    vis = lambda sel: o.evaluate("s => [...document.querySelectorAll(s)].map(n => getComputedStyle(n).visibility).join()", sel)
    o = phone(browser, "owner"); o.goto(SITE); o.wait_for_selector("#frontCard:not([hidden])")
    assert o.is_hidden("#createCard") and o.is_hidden("#occasionChoices") and o.is_hidden("#homeFields")
    assert o.get_attribute("html", "data-occasion") is None and o.inner_text("#title") == "Gifts" and "WISHLIST" in o.inner_text("#eyebrowText")
    assert o.is_visible("svg.lights.deco-christmas") and o.is_hidden("svg.lights.deco-birthday") and o.is_hidden("svg.lights.deco-wedding")
    assert vis(".pet-acc") == "hidden,hidden"                       # no flower crowns outside a wedding
    o.screenshot(path=str(OUT / "wl-0-front.png"))
    o.click("#pickSanta"); o.wait_for_selector("#createCard:not([hidden])")
    assert o.is_hidden("#frontCard") and o.get_attribute("html", "data-occasion") is None and o.inner_text("#title") == "Secret Santa"
    assert o.input_value("#newTitle").startswith("Secret Santa 20") and o.input_value("#newDate").endswith("-12-24") and o.is_visible("#newBudget")
    assert "Exchange day" in o.inner_text("#newDateLabel") and o.inner_text("#createHead") == "Start a new event" and o.is_visible("#homeFields")
    o.fill("#newTitle", "Office swap")
    o.go_back(); o.wait_for_selector("#frontCard:not([hidden])")     # the browser's own Back button
    assert o.url == SITE and o.is_hidden("#createCard") and o.inner_text("#title") == "Gifts"
    o.click("#pickWishlist")
    assert o.is_visible("#occasionChoices") and o.get_attribute("#pickWishlist", "aria-expanded") == "true" and o.is_hidden("#createCard")
    o.screenshot(path=str(OUT / "wl-0-front-occasions.png"))
    o.click("#occasionChoices button[data-occasion=wedding]"); o.wait_for_selector("#createCard:not([hidden])")
    assert o.get_attribute("html", "data-occasion") == "wedding" and o.is_hidden("#newBudget") and "Wedding day" in o.inner_text("#newDateLabel")
    assert o.inner_text("#title") == "Wishlist" and o.input_value("#newTitle") == "" and o.inner_text("#createHead") == "Start a wishlist"
    assert o.is_visible("svg.lights.deco-wedding") and o.is_hidden("svg.lights.deco-christmas")
    # the wedding look dresses the two pets as flower kids: drawn over the artwork, which is still the same two <img>s of the same file
    assert vis(".pet-acc") == "visible,visible"
    assert o.evaluate("[...document.querySelectorAll('img.pets')].map(n => n.getAttribute('src')).join()") == "pets.png,pets.png"
    o.wait_for_timeout(500); o.screenshot(path=str(OUT / "wl-1-home-wedding.png"))
    o.fill("#newTitle", "half typed")
    o.click("#backBtn"); o.wait_for_selector("#frontCard:not([hidden])")          # the link on the form does the same as Back
    assert o.get_attribute("html", "data-occasion") is None and o.is_visible("#occasionChoices") and vis(".pet-acc") == "hidden,hidden"
    o.click("#pickSanta"); o.wait_for_selector("#createCard:not([hidden])")
    assert o.input_value("#newTitle") == "Office swap" and o.is_visible("#newBudget")           # each kind keeps its own half-typed form
    o.click("#backBtn"); o.wait_for_selector("#frontCard:not([hidden])")
    o.click("#occasionChoices button[data-occasion=wedding]"); o.wait_for_selector("#createCard:not([hidden])"); assert o.input_value("#newTitle") == "half typed"
    for lang in ("zh", "ko", "vi", "en"):
        o.click(f"#langs button[data-lang={lang}]"); assert o.get_attribute("html", "data-occasion") == "wedding" and o.inner_text("#kindHint").strip() and o.inner_text("#createHead").strip()
    wide, cut = spill(o); assert wide <= 0 and not cut, (wide, cut)
    o.reload(); o.wait_for_selector("#createCard:not([hidden])"); assert o.get_attribute("html", "data-occasion") == "wedding"      # a reload stays on the step that was showing
    o.click("#backBtn"); o.wait_for_selector("#frontCard:not([hidden])")
    for lang in ("zh", "ko", "vi", "en"):
        o.click(f"#langs button[data-lang={lang}]"); assert o.inner_text("#title").strip() and o.inner_text("#pickSanta").strip() and o.inner_text("#pickWishlist").strip()
    o.click("#pickWishlist"); wide, cut = spill(o); assert wide <= 0 and not cut, (wide, cut)

    # ---- create a birthday list
    o.click("#occasionChoices button[data-occasion=birthday]"); o.wait_for_selector("#createCard:not([hidden])")
    assert o.get_attribute("html", "data-occasion") == "birthday" and o.is_visible("svg.lights.deco-birthday") and vis(".pet-acc") == "hidden,hidden"
    o.click("#createBtn"); o.wait_for_selector("#createErr:not(:empty)"); assert "date" in o.inner_text("#createErr").lower()
    o.fill("#newTitle", "Mia turns 30"); o.fill("#newDate", "2026-12-20"); o.fill("#newOrgPin", "2468"); o.click("#createBtn")
    o.wait_for_selector("#createdCard:not([hidden])")
    assert "pick from your list" in o.inner_text("#forGroupBody") and "edits the list" in o.inner_text("#forYouBody") and "add your wishes" in o.inner_text("#newOpen")
    share = o.input_value("#newShare"); eid = share.split("?e=")[1]
    o.screenshot(path=str(OUT / "wl-2-created.png"))
    o.click("#newOpen"); o.wait_for_selector("#wlAddForm:not([hidden])")
    assert o.get_attribute("html", "data-occasion") == "birthday" and o.inner_text("#title") == "Mia turns 30" and "BIRTHDAY" in o.inner_text("#eyebrowText")
    assert o.is_hidden("#fBudgetBox") and o.is_hidden("#statusPill") and "days to go" in o.inner_text("#countdownText")
    assert o.is_hidden("#joinCard") and o.is_hidden("#meCard") and o.is_hidden("#drawBtn") and o.is_hidden("#reopenBtn"), "nothing from the gift exchange on a wishlist"
    assert "Start with a few things" in o.inner_text("#wlEmpty")

    # ---- the owner fills it: an empty wish and a bad link are refused in words, a bare address gets https://
    o.click("#wlAddBtn"); o.wait_for_selector("#wlErr:not(:empty)")
    o.fill("#wlText", "Kettle"); o.fill("#wlLink", "not a link"); o.click("#wlAddBtn"); o.wait_for_selector("#wlErr:has-text('https://')")
    things = [("A pour-over coffee set", "example.com/coffee"), ("Wool blanket, any colour", ""), ("Tickets to a show", ""), ("手冲咖啡壶", "")]
    for i, (text, link) in enumerate(things):
        o.fill("#wlText", text); o.fill("#wlLink", link); o.click("#wlAddBtn"); o.wait_for_selector(f"#wlItems li >> nth={i}")
    assert o.locator("#wlItems li").count() == 4 and o.get_attribute("#wlItems li >> nth=0 >> a.item-link", "href") == "https://example.com/coffee"
    assert o.input_value("#wlText") == "" and o.is_hidden("#wlEmpty")
    o.locator("#wlItems li", has_text="Wool blanket").locator("button[data-act=edit]").click(); o.wait_for_selector("#dlg[open]")
    o.fill("#dlgBody input >> nth=0", "A wool blanket"); o.click("#dlgOk"); o.wait_for_selector("#wlItems li:has-text('A wool blanket')")
    o.locator("#wlItems li", has_text="手冲咖啡壶").locator("button[data-act=removeItem]").click(); o.click("#dlgOk")
    o.wait_for_selector("#wlItems li:has-text('手冲咖啡壶')", state="detached")
    o.wait_for_selector("#adminCard:not([hidden])")
    assert "Send your list" in o.inner_text("#inviteTitle") and "No one has opened it" in o.inner_text("#adminStatus")
    o.evaluate("document.getElementById('settings').open = true")
    assert o.is_hidden("#cfgBudgetBox") and o.is_hidden("#cfgDrawBox") and "Name of the list" in o.inner_text("#cfgTitleLabel")
    o.screenshot(path=str(OUT / "wl-3-owner.png"), full_page=True)

    # ---- a visitor sees the items and nothing about picks; a name + PIN lets them in
    z = phone(browser, "Zoë"); z.goto(share); z.wait_for_selector("#wlItems li")
    assert z.locator("#wlItems li").count() == 3 and z.locator("#wlItems button").count() == 0 and z.is_visible("#wlGoJoin")
    assert z.is_hidden("#joinWishBox") and "Leave your name" in z.inner_text("#joinTitle") and z.is_hidden("#adminCard") and z.is_hidden("#wlAddForm")
    z.screenshot(path=str(OUT / "wl-4-visitor.png"), full_page=True)
    z.click("#wlGoJoin"); assert z.evaluate("document.activeElement.id") == "joinName"
    z.fill("#joinName", "Zoë"); z.fill("#joinPin", "1111"); z.click("#joinBtn"); z.wait_for_selector("#wlMe:not([hidden])")
    assert "pick something" in z.inner_text("#wlMeTitle") and z.inner_text("#wlJoinedNote").strip() and z.evaluate("document.activeElement.id") == "wlMeTitle"
    assert z.locator("#wlItems button[data-act=pick]").count() == 3
    z.locator("#wlItems li", has_text="coffee set").locator("button[data-act=pick]").click(); z.wait_for_selector("#wlItems li.mine")
    assert "1 thing" in z.inner_text("#wlMeTitle") and "coffee set" in z.inner_text("#toast")

    # ---- a second friend sees it is taken and by whom, and picks something else
    s = phone(browser, "Sam"); s.goto(share); s.wait_for_selector("#wlItems li")
    assert s.locator("#wlItems li.taken, #wlItems li.mine").count() == 0, "not signed in: no picks shown"
    s.fill("#joinName", "Sam"); s.fill("#joinPin", "2222"); s.click("#joinBtn"); s.wait_for_selector("#wlMe:not([hidden])")
    taken = s.locator("#wlItems li.taken"); assert taken.count() == 1 and "Zoë has this" in taken.inner_text() and taken.locator("button").count() == 0
    # Zoë takes the show tickets while Sam's page still offers them: Sam is told in words and the list catches up
    ztoken = post("login", {"id": eid, "name": "Zoë", "pin": "1111"})["token"]
    tickets = next(i["id"] for i in post("state", {"id": eid})["items"] if "Tickets" in i["text"])
    post("pick", {"id": eid, "name": "Zoë", "token": ztoken, "itemId": tickets})
    s.locator("#wlItems li", has_text="Tickets").locator("button[data-act=pick]").click(); s.wait_for_selector("#toast:has-text('Just missed it')")
    assert s.locator("#wlItems li.taken").count() == 2
    s.locator("#wlItems li", has_text="blanket").locator("button[data-act=pick]").click(); s.wait_for_selector("#wlItems li.mine")
    s.wait_for_timeout(1700); s.evaluate("window.scrollTo(0, 0)"); s.screenshot(path=str(OUT / "wl-5-friend.png"), full_page=True)
    s.locator("#wlItems li.mine button[data-act=unpick]").click(); s.wait_for_selector("#toast:has-text('Back on the list')")
    assert s.locator("#wlItems li.mine").count() == 0
    s.locator("#wlItems li", has_text="blanket").locator("button[data-act=pick]").click(); s.wait_for_selector("#wlItems li.mine")

    # ---- four languages and three widths on the friend's page: nothing cut, nothing empty
    for lang in ("zh", "ko", "vi", "en"):
        s.click(f"#langs button[data-lang={lang}]"); s.wait_for_timeout(120)
        for sel in ("#wlMeTitle", "#wlTitle", "#wlHint", "#eyebrowText", "#fDateLabel", "#countdownText", "#rosterTitle", "#footCheer"):
            assert s.inner_text(sel).strip(), (lang, sel)
        assert s.locator("#wlItems li.mine .item-mine").inner_text().strip() and s.locator("#wlItems li.taken .item-by").first.inner_text().strip()
        for width in (320, 390):
            s.set_viewport_size({"width": width, "height": 800}); wide, cut = spill(s); assert wide <= 0 and not cut, (lang, width, wide, cut)
    s.set_viewport_size({"width": 390, "height": 844})
    v = phone(browser, "visitor zh", "zh-CN"); v.goto(share + "&lang=zh"); v.wait_for_selector("#wlItems li")
    for sel in ("#joinTitle", "#joinBtn", "#pinShort", "#wlGoJoin", "#rosterTitle", "#rosterHint"): assert v.inner_text(sel).strip(), sel
    for width in (320, 390, 1280):
        v.set_viewport_size({"width": width, "height": 800}); wide, cut = spill(v); assert wide <= 0 and not cut, (width, wide, cut)

    # ---- the owner's page after all that: friends are listed, but not one pick, anywhere
    o.reload(); o.wait_for_selector("#wlItems li"); o.wait_for_selector("#adminCard:not([hidden])")
    assert o.locator("#wlItems li.mine, #wlItems li.taken, #wlItems .item-by, #wlItems .item-mine").count() == 0
    body = o.inner_text("body"); assert "has this" not in body and "Yours" not in body
    assert "2 friends have opened it" in o.inner_text("#adminStatus")
    o.evaluate("document.getElementById('manage').open = true"); assert o.locator("#adminList li").count() == 2
    assert o.is_hidden("#joinCard") and o.is_hidden("#rosterCard") and o.is_hidden("#wlMe")

    # ---- back on another device with name + PIN; leaving puts the picks back
    z2 = phone(browser, "Zoë again"); z2.goto(share); z2.wait_for_selector("#names button")
    assert "Friends who’ve been here (2)" in z2.inner_text("#rosterTitle")
    z2.locator("#names button", has_text="Zoë").click(); z2.fill("#dlgPin", "1111"); z2.click("#dlgOk"); z2.wait_for_selector("#wlMe:not([hidden])")
    assert z2.locator("#wlItems li.mine").count() == 2 and "2 things" in z2.inner_text("#wlMeTitle")
    z2.click("#wlLeaveBtn"); z2.click("#dlgOk"); z2.wait_for_selector("#joinCard:not([hidden])")
    s.reload(); s.wait_for_selector("#wlItems li.mine"); assert s.locator("#wlItems li.taken").count() == 0 and s.locator("#wlItems button[data-act=pick]").count() == 2

    # ---- the owner without the link gets back in with the organizer PIN, exactly as on a gift exchange
    lost = phone(browser, "owner, other device"); lost.goto(share); lost.wait_for_selector("#orgClaimBtn:not([hidden])")
    lost.click("#orgClaimBtn"); lost.fill("#dlgPin", "2468"); lost.click("#dlgOk"); lost.wait_for_selector("#wlAddForm:not([hidden])")

    # ---- a wedding list looks like a wedding; a gift exchange opened right after still looks like Christmas
    w = post("create", {"kind": "wishlist", "occasion": "wedding", "title": "Mia & Sam", "exchangeOn": "2026-12-20", "orgPin": "2468"})
    post("admin", {"id": w["id"], "adminKey": w["adminKey"], "action": "addItem", "text": "A cast-iron pot", "link": ""})
    wd = phone(browser, "wedding guest"); wd.goto(SITE + "?e=" + w["id"]); wd.wait_for_selector("#wlItems li")
    assert wd.get_attribute("html", "data-occasion") == "wedding" and "WEDDING" in wd.inner_text("#eyebrowText") and "Wedding day" in wd.inner_text("#fDateLabel")
    assert wd.is_visible("svg.lights.deco-wedding") and wd.is_hidden("svg.lights.deco-christmas") and "Congratulations" in wd.inner_text("#footCheer")
    wd.screenshot(path=str(OUT / "wl-6-wedding.png"), full_page=True)
    ev = post("create", {"title": "Secret Santa 2026", "budget": "$50", "exchangeOn": "2026-12-24", "orgPin": "2468"})
    wd.goto(SITE + "?e=" + ev["id"]); wd.wait_for_selector("#joinCard:not([hidden])")
    assert wd.get_attribute("html", "data-occasion") is None and wd.is_visible("svg.lights.deco-christmas") and wd.is_hidden("#wlCard") and wd.is_hidden("#wlMe")
    assert wd.is_visible("#joinWishBox") and wd.inner_text("#joinTitle") == "Sign up" and wd.inner_text("#joinBtn") == "Sign up" and wd.is_visible("#fBudgetBox")
    assert "GIFT EXCHANGE" in wd.inner_text("#eyebrowText") and "Merry Christmas" in wd.inner_text("#footCheer")
    browser.close()

print("problems:", errors or "none")
sys.exit(1 if errors else 0)
