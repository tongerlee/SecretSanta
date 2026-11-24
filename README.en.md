[中文](README.md) · **English**

# Secret Santa and wishlists (the Waffle and Eggs Benedict edition) · multi-event

Anyone can set up two kinds of event on the site:

- **Gift exchange (Secret Santa)**: create an event, drop the link in the group chat, everyone signs up on their own, and after the draw each person sees only their own match.
- **Wishlist** (birthday / wedding): the owner lists what they'd love, friends each pick something, and the owner never sees who picked what. See "Wishlists" below.

```
browser ──> Amplify Hosting (static page: web/)
   └─────> Lambda Function URL (API: api/) ──> DynamoDB (one item per event)
```

The page and the API are on different origins. The API only accepts browser requests coming from this Amplify site (CORS).

## Deploying

You need the AWS CLI (with credentials configured), the SAM CLI, and the system's zip, curl and perl.

```bash
./deploy.sh                                   # default profile and region
./deploy.sh --region us-east-2 --profile santa
```

Use a Region your account is allowed to use. AWS accounts (projects) created after September 2026 can only deploy in the Region assigned to them, shown under settings.aws.com → Project → Additional Info. The wrong one fails with `explicit deny in a service control policy`.

The script does three things: creates the table, the Lambda and the Amplify app with SAM; writes the API address into `config.js`; zips `web/` and uploads it to Amplify. It prints the site address at the end. Run it again after changing code to update. To remove everything: `sam delete --stack-name secret-santa`.

### Deploy on push (GitHub Actions)

`.github/workflows/deploy.yml`: every push to main runs the unit tests, builds the page, then runs `./deploy.sh`. One-time setup (needs `gh` installed and signed in on your machine):

```bash
./setup-ci.sh --profile santa --region us-east-2
gh secret set ALERT_EMAIL --repo tongerlee/SecretSanta     # optional, see Alerts below; press Enter, then type the address
```

The script tries OIDC first (GitHub assumes a role that trusts only this repo's main branch, with temporary credentials and no long-lived key stored anywhere). When the account's SCP forbids creating an OIDC provider (true for accounts created in 2026), it instead creates an IAM user with permissions for this stack only and writes its access key straight into the repo's secrets with `gh`, without showing it on screen. Running it again rotates the key and revokes the old one. Finally it writes `CI_DEPLOY=on`, `AWS_REGION` and so on into the repo variables; the workflow skips itself unless `CI_DEPLOY` is `on`. Local `./deploy.sh` keeps working. The repo is public and so are the workflow logs, which is why the email address and the keys are secrets (masked in logs).

### Alerts

`template.yaml` has two CloudWatch alarms (a Lambda error within 5 minutes; 5 or more throttles), sent by email through SNS. They are created only when the `AlertEmail` parameter is given: locally `ALERT_EMAIL=you@example.com ./deploy.sh …`, or the `ALERT_EMAIL` secret in CI. The first time, AWS sends a "Confirm subscription" email; the alarms work once you click it. Deploying with `ALERT_EMAIL=""` removes the alarms; leaving it unset keeps things as they are. Cost alerts are not in the template: Free Tier accounts get an email by default when usage nears the free allowance, and you can also set a budget of a few dollars under Billing → Budgets.

## How to use it

**Organizer**

1. Open the site, choose "Gift exchange" on the front page, fill in the event name, budget and exchange day, set an **organizer PIN** only you know (4–8 digits), and press "Create event".
2. The page gives you two links. The **group link** goes in the group chat. The **organizer link** is for you alone: the draw and the settings depend on it, so save a copy right away. The device that created the event remembers it automatically.
3. Sign up on the event page yourself (optional; you can run the draw without signing up).
   Lost the organizer link, or changed phone or browser: open the group link on any device, press "I'm the organizer: get my controls back" at the bottom of the page, and enter the organizer PIN. You get a fresh organizer link (the old one stops working at the same moment). Events created before organizer PINs existed have none; set one under "My organizer link".
4. When everyone is in, press "Draw names". You can also set a time under "Event settings and auto-draw time" and the draw happens automatically after it.
5. Someone forgot their PIN: reset it for them in the organizer controls and tell them privately.

**Participants**

1. Open the group link, enter your name, make up a 4-digit PIN, add a wish if you like, and press "Sign up".
2. After the draw, open the same link again. On the same phone you see your gift tag straight away; on another device, tap your name and enter your PIN once.

## Wishlists (the second kind of event)

The front page first asks "What to set up": **Gift exchange** (goes straight to the original Secret Santa form; the form and the flow are unchanged) or **Wishlist** (opens a choice of occasion: birthday / wedding), and the form only appears after you choose. "Choose something else" on the form and the browser's Back button both return to this page; each of the two forms keeps what you had half filled in. Links to existing events (`?e=…`, `#admin=…`) skip this page and open the event directly, as before.

- The list owner picks an occasion (birthday / wedding), enters a name, a date and an organizer PIN, and gets two links: one for friends, one for themselves (same as a gift exchange).
- The owner adds wishes on the list page (a line of text plus an optional link), and can edit or remove them.
- Friends who open the link see the list. Only after leaving a name + PIN (the same mechanism as signing up; on another device, tap your name and enter the PIN to come back) do they see what is already taken and by whom, and can press "I'll get this" / "Put it back". First come, first served: if two people press the same item at once, exactly one gets it and the other is told.
- **The owner never sees the picks**: neither the public state nor the organizer API returns them, and the owner's device does not show the sign-up form. (An owner who signs up under a fake name to peek is spoiling their own surprise; that can't be stopped and doesn't need to be.)
- When a friend leaves or is removed, what they picked goes back on the list.
- Each occasion has its own look (`data-occasion` switches the palette, the title font, the hanging decorations at the top, the ornament on the left, and what falls from the sky). A gift exchange always has the Christmas look.
- In the wedding look, Eggs Benedict and Waffle are flower kids: both wear a flower crown, and Waffle also gets a bow tie. These are SVGs laid over the picture (`page/flowerkids.svg`, in the same box and with the same animations as each pet, so they move together). **`web/pets.png` itself is not modified.**

In the data: a wishlist and a gift exchange are the same kind of item in the same table, with `kind: 'wishlist'`, `occasion` and `items` added. **An old item without `kind` is a gift exchange**, and the API returns exactly the same fields for it as before. One test in `test/api.test.mjs` rewrites an item into its first-version shape and runs every flow against it.

## Your own domain

The `CustomDomain` parameter in `template.yaml` is the site's official address (currently `gifts.departmentofunnecessarythings.com`). It decides two things: which address is allowed to call the API (CORS), and the image address in WeChat / iMessage link previews. To change the domain, change this parameter, then `git pull && ./deploy.sh`.

After a domain change, the old address (main.xxx.amplifyapp.com) still opens, but a notice at the top says the page has moved. Participants enter their PIN again at the new address; organizers follow the link in the notice and their organizer access travels with it (what a browser remembers is stored per address and does not move by itself).

To attach the domain to Amplify, run `./domain.sh --profile santa --region us-east-2`. The first run creates this subdomain in Amplify (the root domain and www are left alone) and prints the two CNAME records to add, in the format of Shopify's form (Name with the domain part removed). Add them in the Shopify admin under Settings → Domains → this domain → Domain settings → Edit DNS settings → Add custom record, then run the script again to see the status; AVAILABLE means it is live. Amplify issues and renews the certificate.

## Languages

The page comes in four languages: 中文, English, 한국어, Tiếng Việt.

- A row of language buttons sits under the tag card. One tap switches on the spot, with no reload, and nothing half-typed is lost. The choice is remembered on that device.
- On a first visit the page follows the phone's system language; any other system language gets English.
- To have the page open in a given language for a friend, add `&lang=ko` to the group link (`zh`, `en`, `ko` or `vi`).
- Names and wishes that people type are shown as written and are not translated. Dates follow each language's habit (12/24 周四 · Thu, Dec 24 · 12월 24일 (목) · Thứ Năm, 24/12).
- All text lives in `page/i18n.js`, one set per language, written the way locals would say it and not translated sentence by sentence. After editing, run `python3 page/build.py` and deploy. `npm test` checks that the four languages have matching entries with none missing.
- Link previews in WeChat do not run page scripts, so the preview line always says "sign up" in all four languages.

## Page behavior at a glance

- Look: off-white cards, rounded corners, names shown as small badges with a colored initial (muted berry / pine / ochre / slate blue / plum / moss, white text at a contrast of at least 4.5:1). An event name in Latin letters uses the holiday font, and one longer than 16 characters is only stepped down a size so it wraps well; a name with Chinese, Korean or Vietnamese text uses the bold body font (pine green).
- After creating an event the handover comes in two blocks: "For the group" holds only the group link; "For you only" holds the organizer link, hidden like a password by default, which you can show, copy, or save with "Email it to me" through the system mail app. Phones also get a "Share the link" button (hidden on computers without a share sheet).
- Coming back on another device: the top of the sign-up form asks "Signed up already, on another device?" with "Find your name", which jumps to the list of people signed up and highlights it; tap your name and enter the PIN. "Events on this device" on the home page says which name you signed up with and explains that the list lives on this device only.
- The sign-up form keeps one line of explanation, with the long one tucked under "Why a PIN?". The wish box grows as you type. After signing up, the confirmation is written inside the card (the PIN is never shown on screen) and focus moves to the heading; before the draw it says how to come back for the result (with the time and time zone if an automatic draw time is set).
- After the draw: the tag header gains a line "N days until exchange day", which becomes "Exchange day is today!" on the day, with one burst of confetti. The organizer controls have "Copy a message for the group" (an invitation while sign-ups are open, a reminder after the draw), with the event name, budget, date and link in it. When your recipient changes their wish, the gift tag is marked "Wish updated" until you flip it open once (remembered on that device).
- Organizer controls: while sign-ups are open, "Invite people" comes first. With too few people the red button is "Copy group link"; with enough it becomes "Draw names". Once the organizer has signed up, this area moves to the top of the page; after the draw it moves back below the list of names and the invitation block collapses.
- Flipping the gift tag open throws confetti; with the system's "reduce motion" setting on, it does not. The first load shows gray skeleton placeholders.
- Wide screens (≥900px) get two columns: the scene and the tag stay fixed on the left, the cards are on the right.
- The footer is DOUT's stamp; tapping it goes to the shop.

- Home page: events this device has joined or organized come first, then the "What to set up" choice (gift exchange / wishlist); the matching form appears only after choosing.
- Event page: the scene at the top is smaller so the sign-up or the result comes into view sooner; the home page keeps the full scene.
- "Organizer controls" appear only in a browser that holds the organizer link (dashed border, lock icon); participant management is collapsed by default.
- The automatic draw is not an alarm clock: after the set time, the first person to open the page triggers the draw (with at least 3 people signed up). The page says so, with the time zone.
- A quiet round button at the top right of the illustration (44px touch target) pauses the pets, the snow and the lights together. With the system's "reduce motion" setting on, it is not shown and nothing animates.
- Footer: under the stamp come a cheer, "This event deletes itself on Jan 1, 2027." (with the year), and a link back to the home page.

## What is stored, and who can see it

| Data | Notes |
| --- | --- |
| Event name, budget, exchange day, draw time | Visible to anyone with the group link |
| The name each person enters | Visible to anyone with the group link |
| The wish each person enters | That person, whoever draws them, and the organizer |
| PIN | Stored only as a salted scrypt hash. Five wrong tries in a row lock that name for 10 minutes |
| Device token | After a successful sign-in the server gives the device a random string; the table stores only its hash. **The PIN is not stored on the phone** |
| Organizer key | Shown once, at creation; the table stores only its hash |
| Organizer PIN | A scrypt hash like participant PINs, with the same lock after 5 wrong tries; it can be exchanged for a fresh key (the old one is revoked immediately). The older way of tying organizer access to a sign-up is still supported by the server; the page shows only "Untie" for events that were tied |
| Draw pairings | Stored scrambled. The API returns "who you give to" only to that person; not even the organizer can get it |
| Items on a wishlist (text + optional link) | Visible to anyone with the link. A list holds at most 40 items |
| Wishlist picks (who is giving what) | Visible only to friends who left a name + PIN. The owner cannot see them; neither the public state nor the organizer API returns them |

- No phone numbers or email addresses are collected, and there are no cookies or analytics.
- **Automatic deletion**: an event is deleted in full 7 days after its exchange day (for a wishlist, the date on the list; checked on every visit, with a DynamoDB TTL as a backstop). The organizer can also delete it by hand at any time.
- **Abuse limit**: one network address can create at most 5 events per hour. The counter is keyed by a hash of the address and expires after 2 hours; the raw IP is not stored.
- When the organizer resets someone's PIN, that person is signed out on all devices and a trace is left: at their next sign-in they see that the organizer reset their PIN, and when.
- The "scrambling" of the pairings only prevents a spoiler from a casual look in the DynamoDB console. It is not encryption; the owner of the AWS account can always find out.
- **Organizer link lost for good, and no organizer PIN set** (old events): the site cannot give it back, but the account owner can run `./recover.sh <event-id> --profile santa --region us-east-2` to put a new key in the table and print a new link.
- Wishes are written by the people themselves and the site does not translate them. When a wish is clearly in a different language from the page (a Chinese wish under an English interface, say), the gift tag and the organizer's list show "Translate ↗". Only tapping it opens that one wish in Google Translate; nothing is sent otherwise.
- The page uses Google Fonts, so Google sees a visitor's IP address when the fonts load.

## Layout of the repo

```
api/index.mjs        Lambda: creating events, sign-ups, PINs, tokens, the draw, and wishlist items and picks. One DynamoDB item per event, updated with optimistic locking
web/                 The static site hosted on Amplify: index.html, pets.png (Waffle and Eggs Benedict, the original picture), config.js (the API address, generated at deploy time)
page/                Page sources (i18n.js is the text in four languages; flowerkids.svg and friends are the decorations for each occasion); python3 page/build.py assembles them into web/index.html
template.yaml        SAM template: the table, the Lambda, the Amplify app, the alarms
deploy.sh            One command to deploy
domain.sh            Attaches your own domain to Amplify and prints the DNS records to add
setup-ci.sh          One-time setup of the deploy permissions for GitHub Actions
recover.sh           For the account owner: issue a new organizer key when the organizer link is lost for good
.github/workflows/   deploy.yml (a push to main deploys), test.yml (tests on every pull request)
test/                Local tests (api.test.mjs, i18n.test.mjs, browser.sh) and checks for a deployed site (live_*.py)
```

Addresses look like `https://<your domain>/?e=<event id>`, or `https://main.<app id>.amplifyapp.com/?e=<event id>` when no domain is set up. Gift exchange links and wishlist links have the same format. For setting up a domain, see "Your own domain" above.

## Running and testing locally

```bash
npm install
npm test                   # API tests (the real handler + dynalite, a local DynamoDB emulator) and the consistency check of the four languages
npm run dev                # site at http://127.0.0.1:8787/ , API on port 8788, cross-origin like production
./test/browser.sh          # three headless-browser walks: the full flow in Chinese (create, sign up, draw, come back on another device with a PIN),
                           # each of the four languages (follows the system language, switching, &lang=, nothing overflowing),
                           # and wishlists (create a list, add wishes, pick, two people going for the same item, the owner not seeing picks). Screenshots land in test/shots/
```

Every pull request runs `npm test` and `./test/browser.sh` in GitHub Actions (`.github/workflows/test.yml`) without touching AWS; only a merge into main deploys.

## Checks after going live

After a deploy you can check again against the real address. Each of the four commands below creates its own test events (named TEST please ignore) and deletes them all at the end, pass or fail, without touching any other event. The first three check the gift exchange, the fourth checks wishlists.

```bash
export SITE=https://main.xxxx.amplifyapp.com API=https://xxxx.lambda-url.us-east-2.on.aws
python3 test/live_api.py                              # about 120 API rules: sign-up, PIN lock, seeing only your own result, the draw, a full event of 60, simultaneous sign-ups…
python3 test/live_browser.py                          # about 50 real-browser checks: several devices, four languages × four screen sizes, the Safari engine, going offline, blocked storage…
SITE=$SITE/ ENGINE=webkit python3 test/browser.py     # the full flow in Chinese on the Safari engine (what iPhones and WeChat on iPhone use)
python3 test/live_wishlist.py                         # about 20 wishlist checks: the front page, a wedding list, picking, two people going for the same item, another device, the owner not seeing picks
```

Replace `SITE` and `API` with the real addresses. The ones live today:

```bash
export SITE=https://gifts.departmentofunnecessarythings.com API=https://p2yg2w4mbgqms5eqzpjj5qb66i0tburz.lambda-url.us-east-2.on.aws
```

Known limits:

- A new AWS account can run only 10 Lambda requests at once. When more than 10 arrive in the same instant, AWS turns the extra ones away (429); the page quietly retries by itself and people usually do not notice.
- "At most 5 events per hour per network address" is a speed bump only; a different IP gets around it. The four checks above create 5 test events between them, exactly one network's allowance for an hour, so a second full run within the hour is stopped by this rule (429 when creating an event). Wait an hour and run again.
- These checks do not replace real devices: the browser inside WeChat and real iPhones and Android phones still need a tap-through by hand.
