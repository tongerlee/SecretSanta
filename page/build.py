"""Assemble web/index.html (the static site Amplify serves) from the parts in this folder."""
import pathlib
here = pathlib.Path(__file__).parent
read = lambda n: (here / n).read_text()
body = read('body.html').replace('@@LIGHTS@@', read('lights.svg')).replace('@@TREE@@', read('tree.svg'))
icon = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230d2a22'/%3E"
        "%3Cpath d='M16 5 6 22h20z' fill='%232f8a66'/%3E%3Crect x='14' y='22' width='4' height='5' fill='%237a4a2a'/%3E%3Ccircle cx='16' cy='6' r='2.4' fill='%23f2c14e'/%3E"
        "%3Ccircle cx='13' cy='15' r='1.6' fill='%23ff5468'/%3E%3Ccircle cx='19' cy='18' r='1.6' fill='%23f2c14e'/%3E%3C/svg%3E")
csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src https: http://127.0.0.1:*; base-uri 'none'; form-action 'none'")
page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#0d2a22">
<meta name="robots" content="noindex">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>Secret Santa</title>
<meta property="og:title" content="Secret Santa">
<meta property="og:description" content="Sign up · 报名 · 참가하기 · Đăng ký">
<meta property="og:image" content="@@ORIGIN@@/pets.png">
<link rel="icon" href="{icon}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Caveat:wght@600&family=Ma+Shan+Zheng&family=Mountains+of+Christmas:wght@700&display=swap" media="print" onload="this.media='all'">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gaegu:wght@700&family=Patrick+Hand&display=swap" media="print" onload="this.media='all'">
<style>{read('style.css')}</style>
</head>
<body>
{body}
<script src="config.js"></script>
<script>
{read('i18n.js')}
{read('app.js')}</script>
</body>
</html>
'''
import unicodedata
assert '@@LIGHTS@@' not in page and '@@TREE@@' not in page
for part in ('app.js', 'i18n.js'):
    assert '</script' not in read(part).lower(), part
# Vietnamese letters must be stored as single composed characters, or some phones draw the marks beside the letter.
assert unicodedata.normalize('NFC', read('i18n.js')) == read('i18n.js'), 'i18n.js is not NFC'

web = here.parent / 'web'
(web / 'index.html').write_text(page)
# Where the API lives. Empty means "same origin" (local dev overrides this; deploy.sh writes the real address).
if not (web / 'config.js').exists():
    (web / 'config.js').write_text('window.SANTA_API = "";\n')
print('built', web / 'index.html', len(page.encode()), 'bytes')
