## 🚀 Bot-hosting 自动续期（GitHub Actions，多账号版）

这是一个基于 GitHub Actions 的自动化脚本，用于定时登录自动续期 [Bot-hosting](https://bot-hosting.net) 服务。

登录与续期流程与 [eooce/Auto-Renew-Bothosting](https://github.com/eooce/Auto-Renew-Bothosting) 一致：账号密码登录 + Turnstile 坐标点击 + 账单页到期日期/冷却时间识别。

支持多账号循环续期（单账号失败不影响其他账号），跑完发逐账号通知 + 一条汇总通知。

⚠️ 有cf盾,太垃圾的机房节点可能过不了，建议用稍微干净点的节点,[B2proxy住宅代理](https://www.b2proxy.com/signup?code=0F5133)

━━━━━━━━━━━━━━━━━━━━━━

### 🔐 Secrets 配置说明

| Secret 名称         | 是否必填 | 说明                                              |
|---------------------|----------|---------------------------------------------------|
| EMAIL              | ✅ 必填  | 第 1 个账号的登录邮箱（无编号时按单账号跑）             |
| PASSWORD           | ✅ 必填  | 第 1 个账号的登录密码，在控制台右上角头像里设置          |
| NODE_LINK          | ❌ 可选  | 代理链接（如 vless:// vmess:// trojan:// hysteria2:// tuic:// anytls:// socks5:// )|
| TG_BOT_TOKEN       | ❌ 可选  | Telegram Bot Token（用于发送通知）                      |
| TG_CHAT_ID         | ❌ 可选  | Telegram Chat ID（接收通知的用户或群组 ID）               |

### 🔐 多账号配置（编号后缀式，最多支持 10 个）

| Secret 名称            | 说明                                              |
|------------------------|---------------------------------------------------|
| EMAIL_1 / EMAIL_2 / …  | 第 N 个账号的登录邮箱                               |
| PASSWORD_1 / _2…       | 第 N 个账号的登录密码，控制台右上角头像里设置           |

> * 每个账号 `EMAIL_N` 和 `PASSWORD_N` 必须成对填写，缺一个该编号会被跳过。
> * `TG_BOT_TOKEN / TG_CHAT_ID / NODE_LINK` 全局共用，只填一次。
> * 旧单账号写法（`EMAIL / PASSWORD` 不带编号）仍然兼容：只配旧变量时按单账号跑；同时配了编号和旧变量时，旧变量会被当作账号 1 纳入（仅当 `_1` 未配置时）。

━━━━━━━━━━━━━━━━━━━━━━

## 部署步骤
1：fork 本项目，在actions菜单允许工作流

2：在`setting`➡`secrets and variables`➡`Actions` 里添加上方必填的secrets

3：去actions菜单手动试运行工作流,根据自己的服务到期日期自行在[renew.yml](.github/workflows/renew.yml)里调整cron运行时间

### 密码设置
账号密码登录用的是 Bot-hosting 控制台右上角头像里设置的登录密码（不是 Discord 密码），填进 `PASSWORD` / `PASSWORD_N` 即可。

## 注意事项
* 必填变量必须要填写
* NODE_LINK支持的代理协议有：vmess,vless,hysteria2,tuic,anytls,socks5等
* 本版本为多账号版，账号间失败隔离；加第 4 个及以后账号时，Secrets 里加 `EMAIL_N` + `PASSWORD_N`，并在 `renew.yml` 里照格式加两行 env 透传即可（`app.py` 免改）
* cron运行时间不一定准确,得根据实际到期时间修改,可在设置里暂停actions功能再开启

## ⚠️ 免责声明
* 本程序仅供学习了解, 非盈利目的，如转载须注明来源。
* 使用本程序必循遵守部署服务器所在地、所在国家和用户所在国家的法律法规, 程序作者不对使用者任何不当行为负责。
