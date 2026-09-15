# VENMO_LIKES_FINDINGS

Mission question: **How many likes did all Venmo transactions, I sent this month, have in total?**

## 结论（答案）

**本月（2023 年 5 月）Jose Harrison 自己发出的 Venmo 交易共 12 笔，likes 合计 = 11。**

- 逐笔 likes：0 + 0 + 0 + 2 + 5 + 2 + 0 + 2 + 0 + 0 + 0 + 0 = **11**

## 共享世界中的账户与身份（API 观察）

- `apis.supervisor.show_profile()` 返回：
  - first_name: Jose, last_name: Harrison, email: **joseharr@gmail.com**, phone: 2474975253, birthday: 1985-12-15, sex: male
- `apis.supervisor.show_account_passwords()` 返回 venmo 账户密码 `uNK8[nt`（account_name: "venmo"）。
- 登录：`apis.venmo.login(username="joseharr@gmail.com", password="uNK8[nt")` 成功，返回 access_token（Bearer）。
  - 观察到的 token 过期时间戳 1684412990 ≈ 2023-05-18。

## 统计口径（口径判断）

- **"本月"**：共享世界当前时间约为 **2023-05-18**（由最新交易 `2023-05-18T11:30:10`、最新通知 `2023-05-18T11:30:10` 佐证）。因此本月 = **2023 年 5 月**，起止按 `2023-05-01` 至 `2023-05-31` 过滤。
- **"我"**：登录的 Venmo 账户 `joseharr@gmail.com`（Jose Harrison）。
- **"sent 的交易"**：使用 `apis.venmo.show_transactions(..., direction="sent")`，即当前用户为**发起方（sender）**的转账交易；并以「sender.email == joseharr@gmail.com」二次过滤交叉验证。
- **"likes"**：交易返回字段 `like_count`。
- 付费/收款请求（payment request）是另一类实体（`show_sent_payment_requests`），**不含 like_count 字段**，故不计入 likes 统计（见下方说明）。

## 逐条交易–likes 明细（2023 年 5 月，direction = sent）

| # | transaction_id | created_at | likes | 描述 (description) | sender -> receiver |
|---|----------------|------------|-------|--------------------|--------------------|
| 1 | 6266 | 2023-05-01T01:14:31 | 0 | 👟Fresh Kicks | joseharr@gmail.com -> mel.bailey@gmail.com |
| 2 | 1082 | 2023-05-01T12:00:13 | 0 | Taxi Fare | joseharr@gmail.com -> bradley_ball@gmail.com |
| 3 | 1086 | 2023-05-01T20:41:55 | 0 | 🌺 Farmers Market Haul | joseharr@gmail.com -> ta.weav@gmail.com |
| 4 | 1087 | 2023-05-03T20:28:58 | 2 | Car Maintenance | joseharr@gmail.com -> ta.weav@gmail.com |
| 5 | 1067 | 2023-05-04T16:12:14 | 5 | 🏠 Housewarming Party Gifts 🎁 | joseharr@gmail.com -> william_mart@gmail.com |
| 6 | 1063 | 2023-05-05T17:26:15 | 2 | 🎥Stream Sesh | joseharr@gmail.com -> chris.mcco@gmail.com |
| 7 | 2642 | 2023-05-10T15:22:59 | 0 | Watch | joseharr@gmail.com -> mi.burch@gmail.com |
| 8 | 1045 | 2023-05-11T20:42:28 | 2 | 💇Salon Day | joseharr@gmail.com -> mel.bailey@gmail.com |
| 9 | 7418 | 2023-05-12T13:18:22 | 0 | Books | joseharr@gmail.com -> gina-ritter@gmail.com |
| 10 | 5923 | 2023-05-13T01:44:49 | 0 | 📖 Bookstore Haul 📚❤️ | joseharr@gmail.com -> robertmartinez@gmail.com |
| 11 | 1042 | 2023-05-16T05:08:44 | 0 | New 🎮 Game Purchase | joseharr@gmail.com -> robertmartinez@gmail.com |
| 12 | 4492 | 2023-05-18T04:33:46 | 0 | 🍺 Craft Beers 🍻👌 | joseharr@gmail.com -> ta.weav@gmail.com |

**合计 likes = 2 + 5 + 2 + 2 = 11**

## API 调用与交叉验证过程

1. `apis.api_docs.show_app_descriptions()`：列出 app（含 venmo、supervisor）。
2. `apis.api_docs.show_api_descriptions(app_name='venmo')`：列出交易相关 API。
3. `apis.api_docs.show_api_doc(app_name='venmo', api_name='show_transactions')`：确认参数与响应 schema
   （含 `direction` ∈ ['sent','received']、`min_created_at`/`max_created_at`、`like_count` 字段）。
4. `apis.supervisor.show_profile()` / `show_account_passwords()`：取得用户与凭据。
5. `apis.venmo.login(...)`：取得 access_token（成功）。
6. **方法 A**：`show_transactions(direction="sent", min_created_at="2023-05-01", max_created_at="2023-05-31", page_index=0..1, page_limit=20, sort_by="+created_at")`
   → 第 0 页 12 条，第 1 页 0 条 → 共 **12 条**，全部 sender = joseharr@gmail.com。
7. **方法 B（交叉验证）**：`show_transactions(min_created_at="2023-05-01", max_created_at="2023-05-31", page_limit=20, sort_by="+created_at")`（不传 direction）
   → 共 34 条（20 + 14），再以 `sender.email == "joseharr@gmail.com"` 过滤 → 同样 **12 条**，逐笔 likes 与金额一致。
8. 两种方法得到的交易集合与 likes 完全一致：**12 笔，合计 11 likes**。
9. 另查 `apis.venmo.show_sent_payment_requests(access_token=...)`：本月发出 6 条收款请求（747、788、787、746、767 …），但这些**不是转账交易、无 like_count 字段**，不计入。

## 限制与说明

- 分页 `page_limit` 上限 20；已通过翻页直到空页确认无遗漏（方法 A 两页、方法 B 三页）。
- "本月" 依据当前世界时间为 2023-05-18 判定为 2023-05；若评测对"本月"另有系统时钟口径，可能不同，本报告以共享世界观察到的最新时间为准。
- 所有数据来自公开 Venmo API 的只读查询（`show_transactions` / `show_sent_payment_requests`），未做任何写操作，未修改交易或 likes。
