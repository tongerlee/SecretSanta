# Secret Santa（Waffle 和蛋饼版）· 多活动

谁都可以在网站上建一个自己的 Secret Santa：建活动，把链接发到群里，大家各自报名，抽签后每个人只看得到自己的结果。

```
浏览器 ──> Amplify Hosting（静态页面：web/）
   └────> Lambda Function URL（接口：api/）──> DynamoDB（每个活动一条记录）
```

页面和接口不同源，接口只允许来自这个 Amplify 站点的浏览器请求（CORS）。

## 部署

需要 AWS CLI（已配置好凭证）、SAM CLI，以及系统自带的 zip、curl、perl。

```bash
./deploy.sh                                   # 默认 profile 和 region
./deploy.sh --region us-east-2 --profile santa
```

Region 要用你的账号能用的那个。2026 年 9 月之后新注册的 AWS 账号（project）只能在分配给它的 Region 部署，在 settings.aws.com → Project → Additional Info 里能看到；选错会报 `explicit deny in a service control policy`。

它做三件事：用 SAM 建表、Lambda 和 Amplify 应用；把接口地址写进 `config.js`；把 `web/` 打包上传到 Amplify。跑完会打印网站地址。改了代码再跑一次就是更新。全部删除：`sam delete --stack-name secret-santa`。

## 怎么用

**组织者**

1. 打开网站，填活动名称、预算、交换日，点「创建活动」。
2. 页面给你两个链接。**群链接**发到群里。**组织者链接**只给自己，抽签和改设置都靠它，建议马上存一份；创建活动的这台设备会自动记住。
3. 自己也在活动页报名。
4. 人齐了点「抽签」。也可以在「活动设置」里填一个截止时间，到点后自动抽。
5. 有人忘了 PIN：在组织者面板给 TA 重设一个，私下告诉 TA。

**参加的人**

1. 打开群链接，填名字，自己设一个 4 位数字 PIN，心愿可填可不填，点报名。
2. 抽签后再打开同一个链接。同一台手机直接看到自己的吊牌；换了设备就点自己的名字，输一次 PIN。

## 自己的域名

`template.yaml` 里的 `CustomDomain` 参数就是网站的正式地址（现在是 `gifts.departmentofunnecessarythings.com`）。它决定两件事：接口允许哪个网址调用（CORS），以及微信/iMessage 链接预览里的图片地址。换域名就改这个参数，`git pull && ./deploy.sh`。

把域名挂到 Amplify 用 `./domain.sh --profile santa --region us-east-2`：第一次运行会在 Amplify 创建这个子域（根域名和 www 不动），然后把要加的两条 CNAME 按 Shopify 表单的格式打印出来（Name 已去掉域名部分）。到 Shopify 后台 Settings → Domains → 这个域名 → Domain settings → Edit DNS settings → Add custom record 加上去，再跑一次脚本看状态，AVAILABLE 就通了。证书由 Amplify 自动签发和续期。

## 语言

页面有四种语言：中文、English、한국어、Tiếng Việt。

- 标签卡片底下有一排语言按钮，点一下当场切换，不用刷新，填到一半的内容不会丢。选择记在这台设备上。
- 第一次打开时跟着手机的系统语言走；系统是其他语言的，显示英文。
- 想让某位朋友一打开就是指定语言，在群链接后面加 `&lang=ko`（可选 `zh` `en` `ko` `vi`）。
- 朋友自己填的名字和心愿原样显示，不会被翻译。日期按各语言的习惯写（12/24 周四 · Thu, Dec 24 · 12월 24일 (목) · Thứ Năm, 24/12）。
- 所有文字都在 `page/i18n.js`，每种语言一份，是按当地人的说法写的，不是逐句对译。改完跑 `python3 page/build.py` 再部署。`npm test` 会检查四种语言的条目一一对应、没有漏的。
- 微信里的链接预览不会跑页面脚本，所以预览那一行固定写着四种语言的「报名」。

## 存什么，谁能看到

| 数据 | 说明 |
| --- | --- |
| 活动名称、预算、交换日、截止时间 | 有群链接的人都能看到 |
| 每个人填的名字 | 有群链接的人都能看到 |
| 每个人填的心愿 | 本人、抽到 TA 的人、组织者 |
| PIN | 只存加盐的 scrypt 哈希。连续输错 5 次，这个名字锁 10 分钟 |
| 设备令牌 | 登录成功后服务器发一串随机字符给这台设备，表里只存它的哈希。**手机上不存 PIN** |
| 组织者密钥 | 只在创建时显示一次，表里只存哈希 |
| 抽签配对 | 打乱后存。接口只把「你要送给谁」返回给本人，连组织者也拿不到 |

- 不收手机号、邮箱，没有 cookie 和统计。
- **自动删除**：活动在交换日 7 天后整条删除（每次访问时检查，另有 DynamoDB TTL 兜底）。组织者也可以随时手动删除。
- **防滥用**：同一个网络地址每小时最多建 5 个活动。计数用的是地址的哈希，2 小时后过期，不存原始 IP。
- 组织者重设某人的 PIN 会把 TA 的所有设备登出，并且留痕：TA 下次登录会看到「组织者在某时帮你重设过 PIN」。
- 配对的「打乱」只是防止在 DynamoDB 控制台里随手翻到被剧透，不是加密。AWS 账号的主人想查总能查到。
- 页面用了 Google Fonts，加载字体时 Google 能看到访问者的 IP。

## 结构

```
api/index.mjs    Lambda：建活动、报名、PIN、令牌、抽签。每个活动一条 DynamoDB 记录，乐观锁更新
web/             Amplify 托管的静态站：index.html、pets.png（Waffle 和蛋饼，原图）、config.js（接口地址）
page/            页面源文件（i18n.js 是四种语言的文字），python3 page/build.py 拼成 web/index.html
template.yaml    SAM 模板：表、Lambda、Amplify 应用
deploy.sh        一条命令部署
test/            本地测试
```

网址长这样：`https://main.<应用ID>.amplifyapp.com/?e=<活动ID>`。想用自己的域名，在 Amplify 控制台给这个应用绑域名，然后把新域名加进 `template.yaml` 里的 `AllowOrigins` 再部署一次。

## 本地跑和测试

```bash
npm install
npm test                   # 接口测试（真实的 handler + 本地 DynamoDB 模拟器 dynalite）和四种语言的一致性检查
npm run dev                # 网站 http://127.0.0.1:8787/ ，接口在 8788 端口，和线上一样跨域
./test/browser.sh          # 无头浏览器走两遍：中文全流程（建活动、报名、抽签、换设备凭 PIN 进入），
                           # 以及四种语言各走一遍（跟随系统语言、切换、&lang=、版面不溢出）。截图在 test/shots/
```

## 上线后的检查

部署完可以对着真实网址再查一遍。下面三条都会自己建测试活动（名字是 TEST please ignore），结束时不管通过与否都会全部删掉，不碰别的活动。

```bash
export SITE=https://main.xxxx.amplifyapp.com API=https://xxxx.lambda-url.us-east-2.on.aws
python3 test/live_api.py                              # 接口规则约 120 项：报名、PIN 锁定、只能看自己的结果、抽签、60 人满员、同时报名…
python3 test/live_browser.py                          # 真浏览器约 50 项：多设备、四种语言 × 四种屏幕、Safari 内核、断网、存储被禁…
SITE=$SITE/ ENGINE=webkit python3 test/browser.py     # 中文全流程，用 Safari 内核（iPhone 和 iPhone 微信用的就是它）
```

已知的限制：

- 新 AWS 账号的 Lambda 同时只能跑 10 个请求。同一瞬间超过 10 个时，多出来的会被 AWS 挡回（429），页面会自己悄悄重试，一般察觉不到。
- 「每个网络地址每小时最多建 5 个活动」只是减速带，换 IP 就能绕过。
- 这些检查替代不了真机：微信内置浏览器、真实的 iPhone / 安卓手机还是要自己点一遍。
