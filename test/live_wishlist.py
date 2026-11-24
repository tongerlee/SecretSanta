"""Real-browser checks of the WISHLIST mode on a DEPLOYED site (real Lambda + DynamoDB): one test list, several 'devices'.
Covers the front page, a wedding list from start to finish, picks that only signed-in friends can see, two friends going
for the same thing, leaving, and the rule the whole mode rests on: the owner never learns who picked what.
The test list is deleted at the end, pass or fail.

    SITE=https://main.xxxx.amplifyapp.com API=https://xxxx.lambda-url.<region>.on.aws python3 test/live_wishlist.py
    (needs: pip install playwright requests; Chrome, CHROME=/path/to/chrome if not found)"""
import datetime, json, os, sys
import requests
from playwright.sync_api import sync_playwright

SITE = os.environ["SITE"].rstrip("/") + "/"
API = os.environ["API"].rstrip("/")
DAY = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
results, problems, api_seen, EVENT = [], [], [], []
QUIET = ("401", "403", "404", "409", "429")      # expected "no" answers that browsers also log as console errors

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

def phone(browser, label, locale="en-US"):
    page = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, locale=locale).new_page()
    page.on("pageerror", lambda e: problems.append(f"{label}: script error: {e}"))
    page.on("console", lambda m: problems.append(f"{label}: console error: {m.text[:200]}") if m.type == "error" and not any(q in m.text for q in QUIET) else None)
    page.on("requestfailed", lambda r: problems.append(f"{label}: request failed: {r.url[:90]}"))
    page.on("response", lambda r: api_seen.append((r.url.split("/api/")[-1], r.status)) if "/api/" in r.url else None)
    page.on("dialog", lambda d: (problems.append(f"{label}: a browser pop-up appeared: {d.message}"), d.dismiss()))
    return page

def post(path, body):
    r = requests.post(API + "/api/" + path, data=json.dumps(body), timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)

def run(p):
    chrome = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])

    # ---------------------------------------------------------------- front page -> a wedding list
    print("\n== front page")
    o = phone(chrome, "owner")
    o.goto(SITE, wait_until="load"); o.wait_for_selector("#frontCard:not([hidden])")
    step("the home page opens on the choice between a gift exchange and a wishlist", lambda: o.is_hidden("#createCard") and o.is_visible("#pickSanta") and o.is_visible("#pickWishlist"))
    def choose():
        o.click("#pickWishlist"); o.click("#occasionChoices button[data-occasion=wedding]"); o.wait_for_selector("#createCard:not([hidden])")
        dressed = o.evaluate("[...document.querySelectorAll('.pet-acc')].every(n => getComputedStyle(n).visibility === 'visible')")
        return (o.get_attribute("html", "data-occasion") == "wedding" and dressed, "wedding look or flower-kid outfits missing")
    step("wishlist -> wedding opens the form with the wedding look and the pets dressed as flower kids", choose)
    def back():
        o.click("#backBtn"); o.wait_for_selector("#frontCard:not([hidden])")
        plain = o.get_attribute("html", "data-occasion") is None and o.evaluate("[...document.querySelectorAll('.pet-acc')].every(n => getComputedStyle(n).visibility === 'hidden')")
        if o.get_attribute("#pickWishlist", "aria-expanded") != "true": o.click("#pickWishlist")          # the occasions stay open after Back
        o.click("#occasionChoices button[data-occasion=wedding]"); o.wait_for_selector("#createCard:not([hidden])")
        return (plain, "the front page kept the wedding look")
    step("Back returns to the choice in the Christmas look, pets undressed", back)

    print("\n== creating a wishlist on the real site")
    o.fill("#newTitle", "TEST please ignore"); o.fill("#newDate", DAY); o.fill("#newOrgPin", "2468"); o.click("#createBtn")
    o.wait_for_selector("#createdCard:not([hidden])", timeout=20000)
    share = o.input_value("#newShare"); eid = share.split("?e=")[1]
    EVENT[:] = [eid, o.evaluate("id => JSON.parse(localStorage.getItem('santa:' + id + ':admin'))", eid)]
    note(share.startswith(SITE + "?e="), "the list is created; its link has the same shape as a gift exchange link", share)
    o.click("#newOpen"); o.wait_for_selector("#wlAddForm:not([hidden])")
    step("the owner's page shows nothing from the gift exchange (no sign-up, no draw)", lambda: o.is_hidden("#joinCard") and o.is_hidden("#meCard") and o.is_hidden("#drawBtn") and o.is_hidden("#reopenBtn"))
    def fill():
        o.click("#wlAddBtn"); o.wait_for_selector("#wlErr:not(:empty)")                                    # an empty wish is refused in words
        for i, (text, link) in enumerate([("A pour-over coffee set", "example.com/coffee"), ("Wool blanket", ""), ("Tickets to a show", "")]):
            o.fill("#wlText", text); o.fill("#wlLink", link); o.click("#wlAddBtn"); o.wait_for_selector(f"#wlItems li >> nth={i}")
        o.locator("#wlItems li", has_text="Wool blanket").locator("button[data-act=edit]").click(); o.wait_for_selector("#dlg[open]")
        o.fill("#dlgBody input >> nth=0", "A wool blanket"); o.click("#dlgOk"); o.wait_for_selector("#wlItems li:has-text('A wool blanket')")
        return (o.locator("#wlItems li").count() == 3 and o.get_attribute("#wlItems li >> nth=0 >> a.item-link", "href") == "https://example.com/coffee", "items or link wrong")
    step("the owner adds three wishes (one with a link) and edits one", fill)

    # ---------------------------------------------------------------- friends
    print("\n== friends on the shared link")
    z = phone(chrome, "Zoe"); z.goto(share); z.wait_for_selector("#wlItems li")
    step("a visitor sees the list in the wedding look, with no picks, no pick buttons and no owner tools",
         lambda: z.locator("#wlItems li").count() == 3 and z.locator("#wlItems button").count() == 0 and z.is_hidden("#adminCard") and z.is_hidden("#wlAddForm") and z.get_attribute("html", "data-occasion") == "wedding")
    def take():
        z.fill("#joinName", "Zoe"); z.fill("#joinPin", "1111"); z.click("#joinBtn"); z.wait_for_selector("#wlMe:not([hidden])", timeout=20000)
        z.locator("#wlItems li", has_text="coffee set").locator("button[data-act=pick]").click(); z.wait_for_selector("#wlItems li.mine")
    step("a friend leaves a name + PIN and takes the coffee set", take)
    s = phone(chrome, "Sam"); s.goto(share); s.wait_for_selector("#wlItems li")
    def second():
        before = s.locator("#wlItems li.taken, #wlItems li.mine").count()
        s.fill("#joinName", "Sam"); s.fill("#joinPin", "2222"); s.click("#joinBtn"); s.wait_for_selector("#wlMe:not([hidden])", timeout=20000)
        taken = s.locator("#wlItems li.taken")
        return (before == 0 and taken.count() == 1 and "Zoe" in taken.inner_text() and taken.locator("button").count() == 0, "second friend's view of the taken item is wrong")
    step("a second friend sees picks only after signing in: the coffee set is Zoe's and cannot be taken again", second)
    def race():
        ztoken = post("login", {"id": eid, "name": "Zoe", "pin": "1111"})[1]["token"]
        tickets = next(i["id"] for i in post("state", {"id": eid})[1]["items"] if "Tickets" in i["text"])
        first = post("pick", {"id": eid, "name": "Zoe", "token": ztoken, "itemId": tickets})[0]
        s.locator("#wlItems li", has_text="Tickets").locator("button[data-act=pick]").click(); s.wait_for_selector("#wlItems li.taken >> nth=1")
        return (first == 200 and s.locator("#wlItems li.mine").count() == 0 and s.inner_text("#toast").strip() != "", "the late click was not refused in words")
    step("two friends go for the same thing: the first gets it, the second is told and the list catches up", race)
    step("the second friend takes the blanket instead", lambda: (s.locator("#wlItems li", has_text="blanket").locator("button[data-act=pick]").click(), s.wait_for_selector("#wlItems li.mine")) and True)

    # ---------------------------------------------------------------- the surprise holds
    print("\n== what the owner can find out")
    def owner_page():
        o.reload(); o.wait_for_selector("#wlItems li"); o.wait_for_selector("#adminCard:not([hidden])")
        marks = o.locator("#wlItems li.mine, #wlItems li.taken, #wlItems .item-by, #wlItems .item-mine").count()
        return (marks == 0 and "Zoe" not in o.inner_text("#wlItems") and "Sam" not in o.inner_text("#wlItems"), "the owner's page shows a pick")
    step("the owner's page shows the three wishes and not one pick", owner_page)
    def owner_api():
        st, pub = post("state", {"id": eid}); sa, adm = post("admin", {"id": eid, "adminKey": EVENT[1], "action": "list"})
        leaks = [k for k, d in (("public state", pub), ("organizer list", adm)) if any(w in json.dumps(d) for w in ('"picks"', '"by"', '"mine"'))]
        return (st == 200 and sa == 200 and not leaks, leaks or (st, sa))
    step("neither the public state nor the organizer's own API call says who picked what", owner_api)

    # ---------------------------------------------------------------- coming back, leaving
    print("\n== coming back and leaving")
    def again():
        z2 = phone(chrome, "Zoe, other device"); z2.goto(share); z2.wait_for_selector("#names button")
        z2.locator("#names button", has_text="Zoe").click(); z2.fill("#dlgPin", "1111"); z2.click("#dlgOk"); z2.wait_for_selector("#wlMe:not([hidden])", timeout=20000)
        mine = z2.locator("#wlItems li.mine").count()
        z2.click("#wlLeaveBtn"); z2.click("#dlgOk"); z2.wait_for_selector("#joinCard:not([hidden])", timeout=20000)
        s.reload(); s.wait_for_selector("#wlItems li.mine")
        return (mine == 2 and s.locator("#wlItems li.taken").count() == 0 and s.locator("#wlItems button[data-act=pick]").count() == 2, "picks did not survive the device change or did not go back on leaving")
    step("a friend comes back on another device with name + PIN, finds both picks, leaves, and they go back on the list", again)
    def claim():
        lost = phone(chrome, "owner, other device"); lost.goto(share); lost.wait_for_selector("#orgClaimBtn:not([hidden])")
        lost.click("#orgClaimBtn"); lost.fill("#dlgPin", "2468"); lost.click("#dlgOk"); lost.wait_for_selector("#wlAddForm:not([hidden])", timeout=20000)
        EVENT[1] = lost.evaluate("id => JSON.parse(localStorage.getItem('santa:' + id + ':admin'))", eid)      # the old key stopped working just now
        return lost
    lost = []
    step("the owner gets back in on a new device with the organizer PIN", lambda: lost.append(claim()) or True)

    # ---------------------------------------------------------------- delete from the page
    print("\n== deleting the list from the page")
    def destroy():
        d = lost[0]
        d.click("#adminLinkBox summary"); d.click("#destroyBtn"); d.click("#dlgOk"); d.wait_for_selector("#goneCard:not([hidden])", timeout=20000)
        s.reload(); s.wait_for_selector("#goneCard:not([hidden])", timeout=20000)
        return (post("state", {"id": eid})[0] == 404, "still there")
    step("the owner deletes the list; a friend's device then sees it is gone", destroy)
    chrome.close()

with sync_playwright() as p:
    try:
        run(p)
    except Exception as e:
        note(False, "the walk-through stopped early", type(e).__name__ + ": " + str(e)[:400])
    finally:
        if EVENT:
            s, d = post("admin", {"id": EVENT[0], "adminKey": EVENT[1], "action": "destroy"})
            print(f"\ncleanup: test list {EVENT[0]} -> {'deleted now' if s == 200 else 'already gone' if s == 404 else s}")

odd = [c for c in api_seen if c[1] >= 500 or (c[1] == 429 and c[0] not in ("login",))]
note(not odd, "no server errors or throttling seen by any browser", odd[:10])
note(not problems, "no script errors, failed requests or unexpected pop-ups in any browser", problems[:10])
failed = [r for r in results if not r[0]]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
for _, name, detail in failed: print("  FAIL", name, "->", str(detail)[:400])
sys.exit(1 if failed else 0)
