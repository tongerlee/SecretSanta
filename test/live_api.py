"""Checks a DEPLOYED Secret Santa (real Lambda + DynamoDB) from the outside, the way a stranger on the internet could.
It creates its own test events, and deletes every one of them at the end, pass or fail.

    SITE=https://main.xxxx.amplifyapp.com API=https://xxxx.lambda-url.<region>.on.aws python3 test/live_api.py

It uses 2 of the 5 "new events per hour" that one network address is allowed.
RATE=1 runs only the check of that limit instead (and uses up the rest of the hour's allowance)."""
import concurrent.futures as cf, json, os, statistics, sys, time, unicodedata
import requests

SITE = os.environ["SITE"].rstrip("/")
API = os.environ["API"].rstrip("/")
DAY = "2026-12-24"
results, timings, events = [], {}, []           # events: [(id, adminKey)]

def call(path, body=None, origin=None, method="POST", raw=None):
    headers = {"Origin": origin} if origin else {}
    t0 = time.time()
    r = requests.request(method, API + path, data=raw if raw is not None else json.dumps(body or {}), headers=headers, timeout=30)
    ms = (time.time() - t0) * 1000
    timings.setdefault(path, []).append(ms)
    try: data = r.json()
    except Exception: data = None
    return r.status_code, data, r

def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   -> " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)

def expect(name, got, code, error=None):
    status, data, _ = got
    ok = status == code and (error is None or (data or {}).get("error") == error)
    return check(name, ok, f"got {status} {json.dumps(data, ensure_ascii=False)[:200]}")

def create(title="TEST please ignore", budget="$1", day=DAY):
    status, data, _ = got = call("/api/create", {"title": title, "budget": budget, "exchangeOn": day})
    if status == 200: events.append((data["id"], data["adminKey"]))
    return got

def admin(ev, action, **more):
    return call("/api/admin", {"id": ev[0], "adminKey": ev[1], "action": action, **more})

def join(ev, name, pin="1234", wish=""):
    return call("/api/join", {"id": ev[0], "name": name, "pin": pin, "wish": wish})

def me(ev, name, token):
    return call("/api/me", {"id": ev[0], "name": name, "token": token})

def secrets_in(data):
    """Field names that must never appear in an answer from the server."""
    text = json.dumps(data)
    return [w for w in ('"hash"', '"salt"', '"sessions"', '"adminHash"', '"draw"', '"drawId"', '"pin"', '"fails"', '"key"', '"to"') if w in text]

def check_draw(ev, people, label):
    """people: {name: token}. Everyone gives to exactly one other person and receives from exactly one."""
    def one(item):
        name, token = item
        s, d, _ = me(ev, name, token)
        return name, (d or {}).get("giftTo"), (d or {}).get("missed"), s
    with cf.ThreadPoolExecutor(6) as pool: rows = list(pool.map(one, people.items()))
    gives = {name: (g or {}).get("name") for name, g, _, _ in rows}
    missing = [n for n, g in gives.items() if not g]
    check(f"{label}: every person got someone to give to", not missing, f"no recipient for {missing}")
    check(f"{label}: nobody drew themselves", all(k != v for k, v in gives.items()), gives)
    stored = {unicodedata.normalize("NFKC", n) for n in gives}
    got = [v for v in gives.values() if v]
    check(f"{label}: everyone receives exactly one gift", sorted(got) == sorted(stored), f"receivers {sorted(got)[:8]}... vs {sorted(stored)[:8]}...")
    return gives

def section(title): print("\n== " + title, flush=True)

def run():
    if os.environ.get("RATE") == "1":           # run on its own: uses up this network's allowance for the hour
        section("limit on new events per network address")
        codes = []
        for _ in range(7):
            s, d, _ = create(); codes.append(s)
            if s == 429: break
        check("creating events is cut off with 'slow down' after a handful", codes[-1] == 429 and codes.count(200) <= 5, codes)
        return
    # ------------------------------------------------------------------ transport
    section("transport and cross-origin rules")
    s, d, r = call("/", method="GET")
    check("API answers its health check", s == 200 and d == {"service": "secret-santa", "ok": True}, (s, d))
    r = requests.options(API + "/api/state", headers={"Origin": SITE, "Access-Control-Request-Method": "POST"}, timeout=30)
    check("preflight from the site is allowed", r.status_code in (200, 204) and r.headers.get("access-control-allow-origin") == SITE, (r.status_code, dict(r.headers)))
    s, d, r = call("/api/state", {"id": "aaaaaaaaaaaa"}, origin=SITE)
    check("answers to the site carry its CORS header", r.headers.get("access-control-allow-origin") == SITE, dict(r.headers))
    s, d, r = call("/api/state", {"id": "aaaaaaaaaaaa"}, origin="https://evil.example")
    # a browser lets a page read the answer only if this header names that page's own address (or "*")
    check("another website is not given permission to read answers (browsers will block it)", r.headers.get("access-control-allow-origin") not in ("https://evil.example", "*"), dict(r.headers))
    r = requests.options(API + "/api/state", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}, timeout=30)
    check("preflight from another website is not approved", r.headers.get("access-control-allow-origin") not in ("https://evil.example", "*"), (r.status_code, dict(r.headers)))
    expect("GET on an action is refused", call("/api/state", method="GET"), 405, "method_not_allowed")
    expect("unknown path", call("/api/nope", {"id": "aaaaaaaaaaaa"}), 404, "not_found")
    expect("broken JSON", call("/api/state", raw="{oops"), 400, "bad_json")
    expect("a JSON list instead of an object", call("/api/state", raw="[1,2]"), 400, "bad_json")
    expect("oversized request", call("/api/state", raw=json.dumps({"id": "a" * 5000})), 413, "too_large")
    expect("event that does not exist", call("/api/state", {"id": "aaaaaaaaaaaa"}), 404, "not_found")
    for bad in ("", "short", "AAAAAAAAAAAA", "../../etc/pass", {"$ne": 1}, 12345, None):
        s, d, _ = call("/api/state", {"id": bad})
        if not (s == 404 and d == {"error": "not_found"}): check(f"odd event id {bad!r} is just 'not found'", False, (s, d)); break
    else: check("odd event ids are all just 'not found'", True)

    # ------------------------------------------------------------------ create
    section("creating an event")
    for day, err in (("", "bad_date"), ("2026-02-31", "bad_date"), ("12/24/2026", "bad_date"), ("2020-01-01", "date_past"), ("2031-01-01", "date_far")):
        expect(f"exchange day {day!r} is rejected ({err})", call("/api/create", {"title": "x", "exchangeOn": day}), 400, err)
    s, d, _ = create(title="  TEST   please ignore " + "x" * 40, budget="$50 " + "y" * 40)
    if not check("create works", s == 200, (s, d)): return
    E1 = events[-1]; st = d["state"]
    check("event id is 12 unambiguous characters", len(d["id"]) == 12 and set(d["id"]) <= set("abcdefghijkmnpqrstuvwxyz23456789"), d["id"])
    check("organizer key is 32 random hex characters", len(d["adminKey"]) == 32 and set(d["adminKey"]) <= set("0123456789abcdef"))
    check("title is tidied and cut to 30 characters", st["title"] == ("TEST please ignore " + "x" * 40)[:30], st["title"])
    check("budget is cut to 20 characters", len(st["budget"]) == 20, st["budget"])
    check("new event starts open and empty", st["status"] == "open" and st["people"] == [] and st["min"] == 3)
    want_delete = 1798070400000 + int(8.5 * 86400000)           # 2026-12-24T00:00Z + 7 days + 1.5 days of time-zone slack
    check("deletion date is exchange day + 7 days (+1.5 days of time-zone slack)", st["deleteAt"] == want_delete and st["keepDays"] == 7, st)
    check("create answer holds nothing secret", not secrets_in(d), secrets_in(d))
    expect("too few people to draw", admin(E1, "draw"), 409, "too_few")

    # ------------------------------------------------------------------ joining
    section("signing up")
    expect("empty name", join(E1, "   "), 400, "bad_name")
    for pin in ("123", "abcd", "123456789", 1234, None, "12 34"):
        s, d, _ = call("/api/join", {"id": E1[0], "name": "Nobody", "pin": pin})
        if not (s == 400 and d.get("error") == "bad_pin_format"): check(f"PIN {pin!r} is rejected", False, (s, d)); break
    else: check("malformed PINs are rejected (too short, letters, too long, number type, missing, spaces)", True)
    tokens = {}
    s, d, _ = join(E1, "Jennifer", "1111", "red wine")
    check("first sign-up works and returns a device token", s == 200 and len(d.get("token", "")) == 32, (s, d))
    tokens["Jennifer"] = d["token"]
    check("sign-up answer: my own view only", set(d) == {"state", "me", "giftTo", "missed", "token"} and d["giftTo"] is None and d["missed"] is False and not secrets_in(d), d)
    for same in ("jennifer", "  JENNIFER  ", "Ｊｅｎｎｉｆｅｒ", "Jennifer　"):
        s, d, _ = join(E1, same, "9999")
        if not (s == 409 and d.get("error") == "name_taken"): check(f"same name written as {same!r} is refused", False, (s, d)); break
    else: check("same name in other spellings is refused (case, spaces, full-width letters)", True)
    for name, pin, wish in (("小雪", "2222", "猫咪周边"), ("Mia", "3333", "slippers"), ("阿杰", "4444", "")):
        s, d, _ = join(E1, name, pin, wish); tokens[name] = (d or {}).get("token")
        check(f"sign-up: {name}", s == 200, (s, d))
    decomposed = unicodedata.normalize("NFD", "Hương")
    s, d, _ = join(E1, decomposed, "5555", "nến thơm"); tokens["Hương"] = (d or {}).get("token")
    check("Vietnamese name typed with separate accent marks is stored as normal letters", s == 200 and d["me"]["name"] == "Hương", (s, d))
    expect("...and the same name typed the normal way counts as taken", join(E1, "Hương", "5556"), 409, "name_taken")
    s, d, _ = join(E1, "L" * 40, "6666", "w" * 300)
    check("long name is cut to 24, long wish to 100", s == 200 and d["me"]["name"] == "L" * 24 and len(d["me"]["wish"]) == 100, (s, d))
    long_token = d["token"]
    s, d, _ = call("/api/leave", {"id": E1[0], "name": "L" * 24, "token": long_token})
    check("leaving removes me from the list", s == 200 and "L" * 24 not in d["state"]["people"], (s, d))
    expect("...and my old device token stops working", me(E1, "L" * 24, long_token), 404, "no_such_person")
    s, d, _ = call("/api/state", {"id": E1[0]})
    check("public view lists names in sign-up order", d["people"] == ["Jennifer", "小雪", "Mia", "阿杰", "Hương"], d["people"])
    check("public view has names only: no wishes, nothing secret", "wish" not in json.dumps(d) and not secrets_in(d), d)

    section("device tokens")
    expect("no token", call("/api/me", {"id": E1[0], "name": "Jennifer"}), 401, "bad_token")
    expect("made-up token", me(E1, "Jennifer", "x" * 32), 401, "bad_token")
    expect("someone else's token with my name", me(E1, "Jennifer", tokens["Mia"]), 401, "bad_token")
    expect("token for a name not on the list", me(E1, "Ghost", tokens["Mia"]), 404, "no_such_person")
    expect("someone else's token cannot change my wish", call("/api/wish", {"id": E1[0], "name": "Jennifer", "token": tokens["Mia"], "wish": "hacked"}), 401, "bad_token")
    expect("someone else's token cannot remove me", call("/api/leave", {"id": E1[0], "name": "Jennifer", "token": tokens["Mia"]}), 401, "bad_token")
    s, d, _ = call("/api/wish", {"id": E1[0], "name": "Jennifer", "token": tokens["Jennifer"], "wish": "  good   red wine  "})
    check("I can change my own wish", s == 200 and d["me"]["wish"] == "good red wine", (s, d))
    logins = []
    for _ in range(5):
        s, d, _ = call("/api/login", {"id": E1[0], "name": "Mia", "pin": "3333"}); logins.append((d or {}).get("token"))
    check("signing in with name + PIN gives a fresh token each time", len(set(logins)) == 5 and all(logins), logins)
    expect("only the 5 most recent devices stay signed in (the oldest is dropped)", me(E1, "Mia", tokens["Mia"]), 401, "bad_token")
    check("...and the newest five still work", all(me(E1, "Mia", t)[0] == 200 for t in logins))
    s, d, _ = call("/api/logout", {"id": E1[0], "name": "Mia", "token": logins[0]})
    check("'not me' signs that one device out on the server", s == 200 and me(E1, "Mia", logins[0])[0] == 401 and me(E1, "Mia", logins[4])[0] == 200, (s, d))
    tokens["Mia"] = logins[4]

    # ------------------------------------------------------------------ PIN guessing
    section("PIN guessing")
    expect("name that is not on the list", call("/api/login", {"id": E1[0], "name": "Ghost", "pin": "0000"}), 404, "no_such_person")
    lefts = []
    for i in range(4):
        s, d, _ = call("/api/login", {"id": E1[0], "name": "阿杰", "pin": "000" + str(i)}); lefts.append((s, (d or {}).get("left")))
    check("wrong PINs count down 4, 3, 2, 1", lefts == [(401, 4), (401, 3), (401, 2), (401, 1)], lefts)
    s, d, _ = call("/api/login", {"id": E1[0], "name": "阿杰", "pin": "0009"})
    check("fifth wrong PIN locks the name for 10 minutes", s == 429 and d.get("error") == "locked" and d.get("retryAfter") == 600, (s, d))
    s, d, _ = call("/api/login", {"id": E1[0], "name": "阿杰", "pin": "4444"})
    check("while locked, even the right PIN is refused", s == 429 and d.get("error") == "locked" and 0 < d.get("retryAfter", 0) <= 600, (s, d))
    check("the lock does not touch a device that is already signed in", me(E1, "阿杰", tokens["阿杰"])[0] == 200)
    s, d, _ = admin(E1, "list")
    check("organizer sees who is locked", s == 200 and {p["name"]: p["locked"] for p in d["people"]}.get("阿杰") is True, d)
    call("/api/login", {"id": E1[0], "name": "小雪", "pin": "0000"}); call("/api/login", {"id": E1[0], "name": "小雪", "pin": "0000"})
    call("/api/login", {"id": E1[0], "name": "小雪", "pin": "2222"})
    s, d, _ = call("/api/login", {"id": E1[0], "name": "小雪", "pin": "0000"})
    check("a correct PIN resets the wrong-guess counter", s == 401 and d.get("left") == 4, (s, d))
    call("/api/login", {"id": E1[0], "name": "小雪", "pin": "2222"})
    # ten guesses at the same moment must not get more than five tries in
    with cf.ThreadPoolExecutor(10) as pool:
        burst = list(pool.map(lambda i: call("/api/login", {"id": E1[0], "name": "Hương", "pin": "90%02d" % i})[0], range(10)))
    check("ten guesses fired at once still get at most 4 tries before the lock", burst.count(401) <= 4 and burst.count(200) == 0, burst)
    expect("...and that name is now locked", call("/api/login", {"id": E1[0], "name": "Hương", "pin": "5555"}), 429, "locked")

    # ------------------------------------------------------------------ organizer
    section("organizer")
    for action in ("list", "draw", "reopen", "remove", "setPin", "settings", "destroy"):
        s, d, _ = call("/api/admin", {"id": E1[0], "adminKey": "0" * 32, "action": action, "name": "Mia", "pin": "0000"})
        if not (s == 403 and d == {"error": "forbidden"}): check(f"wrong organizer key cannot {action}", False, (s, d)); break
    else: check("a wrong organizer key can do nothing (all 7 actions refused)", True)
    expect("no organizer key at all", call("/api/admin", {"id": E1[0], "action": "list"}), 403, "forbidden")
    expect("a participant's token is not an organizer key", call("/api/admin", {"id": E1[0], "adminKey": tokens["Jennifer"], "action": "list"}), 403, "forbidden")
    expect("unknown organizer action", admin(E1, "explode"), 400, "bad_action")
    s, d, _ = admin(E1, "list")
    check("organizer list: names, wishes, lock state, and nothing else", s == 200 and all(set(p) == {"name", "wish", "locked"} for p in d["people"]) and not secrets_in(d), d)
    s, d, _ = admin(E1, "setPin", name="阿杰", pin="7777")
    check("organizer resets a PIN, which also lifts the lock", s == 200 and {p["name"]: p["locked"] for p in d["people"]}["阿杰"] is False, (s, d))
    expect("after a reset, that person's old devices are signed out", me(E1, "阿杰", tokens["阿杰"]), 401, "bad_token")
    expect("the old PIN no longer works", call("/api/login", {"id": E1[0], "name": "阿杰", "pin": "4444"}), 401, "bad_pin")
    s, d, _ = call("/api/login", {"id": E1[0], "name": "阿杰", "pin": "7777"}); tokens["阿杰"] = (d or {}).get("token")
    check("the new PIN works and the person is told a reset happened", s == 200 and d["me"]["pinResetAt"] > 0, (s, d))
    expect("organizer cannot set a malformed PIN", admin(E1, "setPin", name="Mia", pin="12"), 400, "bad_pin_format")
    expect("reset for a name not on the list", admin(E1, "setPin", name="Ghost", pin="1234"), 404, "no_such_person")
    admin(E1, "setPin", name="Hương", pin="5555")
    s, d, _ = call("/api/login", {"id": E1[0], "name": "Hương", "pin": "5555"}); tokens["Hương"] = (d or {}).get("token")
    s, d, _ = join(E1, "Temp", "1234"); temp = (d or {}).get("token")
    s, d, _ = admin(E1, "remove", name="temp")
    check("organizer removes someone", s == 200 and "Temp" not in d["state"]["people"], (s, d))
    expect("...their device is told they are off the list", me(E1, "Temp", temp), 404, "no_such_person")
    expect("removing a name not on the list", admin(E1, "remove", name="Ghost"), 404, "no_such_person")
    s, d, _ = admin(E1, "settings", title="TEST renamed", budget="under $50", exchangeOn="2026-12-25", drawAt=0)
    check("settings change title, budget and date", s == 200 and (d["state"]["title"], d["state"]["budget"], d["state"]["exchangeOn"]) == ("TEST renamed", "under $50", "2026-12-25"), (s, d))
    expect("settings refuse a date in the past", admin(E1, "settings", title="x", budget="", exchangeOn="2020-01-01"), 400, "date_past")
    admin(E1, "settings", title="TEST please ignore", budget="$1", exchangeOn=DAY, drawAt=0)

    # ------------------------------------------------------------------ the draw
    section("the draw")
    s, d, _ = admin(E1, "draw")
    check("draw works", s == 200 and d["state"]["status"] == "drawn", (s, d))
    check("organizer's answer to the draw holds no pairs", set(d) == {"state", "people"} and "giftTo" not in json.dumps(d) and not secrets_in(d), d)
    gives = check_draw(E1, tokens, "5 people")
    s, d, _ = me(E1, "Jennifer", tokens["Jennifer"])
    wishes = {"Jennifer": "good red wine", "小雪": "猫咪周边", "Mia": "slippers", "阿杰": "", "Hương": "nến thơm"}
    check("I see my recipient's wish, and only one recipient", s == 200 and set(d) == {"state", "me", "giftTo", "missed"} and d["giftTo"]["wish"] == wishes[d["giftTo"]["name"]] and set(d["giftTo"]) == {"name", "wish"}, d)
    s, d, _ = call("/api/state", {"id": E1[0]})
    check("public view after the draw still shows names only", s == 200 and d["status"] == "drawn" and "giftTo" not in json.dumps(d) and "wish" not in json.dumps(d) and not secrets_in(d), d)
    s, d, _ = admin(E1, "list")
    check("organizer view after the draw still holds no pairs", "giftTo" not in json.dumps(d) and not secrets_in(d), d)
    expect("second draw is refused", admin(E1, "draw"), 409, "already_drawn")
    expect("no sign-ups after the draw", join(E1, "Late", "1234"), 409, "closed")
    expect("no leaving after the draw", call("/api/leave", {"id": E1[0], "name": "Mia", "token": tokens["Mia"]}), 409, "closed")
    expect("no removing after the draw", admin(E1, "remove", name="Mia"), 409, "closed")
    s, d, _ = call("/api/login", {"id": E1[0], "name": "Mia", "pin": "0000"})
    check("a stranger with a wrong PIN learns nothing about the result", s == 401 and "giftTo" not in json.dumps(d), (s, d))
    s, d, _ = call("/api/wish", {"id": E1[0], "name": "Mia", "token": tokens["Mia"], "wish": "warm socks"})
    giver = [k for k, v in gives.items() if v == "Mia"]
    s2, d2, _ = me(E1, giver[0], tokens[giver[0]]) if giver else (0, {}, None)
    check("a wish edited after the draw reaches the person giving to me", s == 200 and s2 == 200 and d2["giftTo"] == {"name": "Mia", "wish": "warm socks"}, (d, d2))

    section("redo and automatic draw")
    s, d, _ = admin(E1, "reopen")
    check("reopen clears the result and reopens sign-ups", s == 200 and d["state"]["status"] == "open" and me(E1, "Mia", tokens["Mia"])[1]["giftTo"] is None, (s, d))
    soon = int(time.time() * 1000) + 4000
    s, d, _ = admin(E1, "settings", title="TEST please ignore", budget="$1", exchangeOn=DAY, drawAt=soon)
    check("deadline is saved", s == 200 and d["state"]["drawAt"] == soon and d["state"]["status"] == "open", (s, d))
    s, d, _ = call("/api/state", {"id": E1[0]})
    check("before the deadline the event stays open", d["status"] == "open", d)
    time.sleep(5)
    s, d, _ = call("/api/state", {"id": E1[0]})
    check("after the deadline the next visitor triggers the draw by itself", s == 200 and d["status"] == "drawn", d)
    check_draw(E1, tokens, "automatic draw")
    admin(E1, "reopen")
    s, d, _ = admin(E1, "remove", name="小雪"); admin(E1, "remove", name="阿杰"); s, d, _ = admin(E1, "remove", name="Hương")
    s, d, _ = admin(E1, "settings", title="TEST please ignore", budget="$1", exchangeOn=DAY, drawAt=int(time.time() * 1000) - 1000)
    s, d, _ = call("/api/state", {"id": E1[0]})
    check("a deadline with fewer than 3 people does not draw", s == 200 and d["status"] == "open" and len(d["people"]) == 2, d)

    section("deleting an event")
    s, d, _ = admin(E1, "destroy")
    check("organizer deletes the event", s == 200 and d == {"ok": True}, (s, d))
    expect("the link is dead for everyone", call("/api/state", {"id": E1[0]}), 404, "not_found")
    expect("...including signed-in devices", me(E1, "Jennifer", tokens["Jennifer"]), 404, "not_found")
    expect("...and the organizer key", admin(E1, "list"), 404, "not_found")
    events.remove(E1)

    # ------------------------------------------------------------------ awkward names, bursts, the 60 cap
    section("awkward names")
    s, d, _ = create()
    if not check("second test event", s == 200, (s, d)): return
    E2 = events[-1]; crowd = {}
    odd = ["__proto__", "constructor", "toString", "hasOwnProperty", "<img src=x onerror=1>", "\"><script>1</script>",
           "'; DROP TABLE events;--", "null", "undefined", "👨‍👩‍👧 fam", "🎄", "지민", "Nguyễn Thị Hương", "اسم", "ı"]
    for name in odd:
        s, d, _ = join(E2, name, "1234", "wish of " + name)
        if s == 200: crowd[d["me"]["name"]] = d["token"]
        if not (s == 200 and d["me"]["name"] == unicodedata.normalize("NFKC", name)): check(f"sign-up with the name {name!r}", False, (s, d))
    check(f"{len(crowd)} of {len(odd)} awkward names signed up and are stored exactly as typed", len(crowd) == len(odd), sorted(set(odd) - set(crowd)))
    s, d, _ = join(E2, "​​", "1234")
    check("a name made only of invisible characters is refused", s == 400, (s, (d or {}).get("me")))
    if s == 200: admin(E2, "remove", name="​​")
    s, d, _ = join(E2, "‮gnp.exe", "1234")
    check("text-direction trick characters are stripped from names", s == 200 and "‮" not in d["me"]["name"], (s, (d or {}).get("me")))
    if s == 200: admin(E2, "remove", name=d["me"]["name"])
    s, d, _ = admin(E2, "draw")
    check("draw with the awkward names", s == 200, (s, d))
    check_draw(E2, crowd, "awkward names")
    admin(E2, "reopen")

    section("many people at once")
    def burst(names):
        def one(n):
            t0 = time.time(); asked = 0
            while True:                                      # like the page: when turned away at the door, ask again
                s, d, _ = join(E2, n, "1234"); asked += 1
                turned_away = (s == 429 and not (d or {}).get("error")) or s == 503
                if not turned_away or asked >= 5: return n, s, (d or {}).get("token"), asked, time.time() - t0
                time.sleep(0.25 + 0.4 * asked * (hash(n) % 100) / 100)
        with cf.ThreadPoolExecutor(len(names)) as pool: return list(pool.map(one, names))
    for size, start in ((10, 0), (25, 10)):
        out = burst([f"P{start + i:02d}" for i in range(size)])
        ok = [o for o in out if o[1] == 200]
        for n, s, tok, asked, secs in ok: crowd[n] = tok
        again = sum(1 for o in out if o[3] > 1)
        check(f"{size} people signing up in the same second all get in", len(ok) == size, f"{len(ok)}/{size}: {sorted({o[1] for o in out})}")
        print(f"      ({again} of {size} were turned away at first and got in on a retry; slowest {max(o[4] for o in out):.1f}s, median {statistics.median(o[4] for o in out):.1f}s)")
    s, d, _ = call("/api/state", {"id": E2[0]})
    have = len(d["people"])
    check("nobody is lost or duplicated after the bursts", sorted(d["people"]) == sorted(crowd) and len(set(d["people"])) == have, (have, len(crowd)))
    i = 0
    while have < 60 and i < 80:
        s, dd, _ = join(E2, f"Q{i:02d}", "1234"); i += 1
        if s == 200: crowd[f"Q{i - 1:02d}"] = dd["token"]; have += 1
    expect("the 61st person is told the event is full", join(E2, "OneTooMany", "1234"), 409, "full")
    with cf.ThreadPoolExecutor(30) as pool:
        codes = list(pool.map(lambda _: call("/api/state", {"id": E2[0]})[0], range(30)))
    check("30 people opening the page in the same second all get an answer", codes.count(200) == 30, {c: codes.count(c) for c in set(codes)})
    s, d, _ = admin(E2, "draw")
    check("draw with a full house of 60", s == 200 and len(d["people"]) == 60, (s, len((d or {}).get("people", []))))
    check_draw(E2, crowd, "60 people")
    s, d, _ = admin(E2, "destroy")
    check("second test event deleted", s == 200, (s, d))
    if s == 200: events.remove(E2)


try:
    run()
finally:
    section("cleanup")
    for ev in list(events):
        s, d, _ = admin(ev, "destroy")
        print(f"deleted test event {ev[0]}: {s}")
    print("\n== speed (milliseconds: median / slowest / calls)")
    for path, ms in sorted(timings.items()):
        print(f"{path:14s} {statistics.median(ms):6.0f} / {max(ms):6.0f} / {len(ms)}")
    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    for _, name, detail in failed: print("  FAIL", name, "->", str(detail)[:300])
    json.dump([{"ok": ok, "name": n, "detail": str(d)[:500]} for ok, n, d in results], open("/tmp/live_api_results.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(1 if failed else 0)
