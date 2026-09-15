# Transactions — Azure Harbor Bistro dinner reimbursement (2023-05-17)

Scope: independently re-observed the live AppWorld state (post-supersession re-check, world version 24) for the social-feed / Venmo transactions of yesterday's dinner, identified the coworker-to-manager payments, excluded the user's own missing $38 share, and computed the manager's total food payment. All rows below are verbatim `apis.*` outputs (Venmo/Phone/Supervisor), not workspace text.

## Question
"I went on dinner with my coworkers yesterday at Azure Harbor Bistro. My manager paid for food and everyone venmoed them. Everyones' transactions except mine should be on my social feed. My share was $38. How much did my manager pay for the others, including me, yesterday?"

**Answer: $227.0** = $189.0 (others, from the social feed) + $38.0 (my share, from my own transactions).

## Identity / date / roles (re-verified)
- `apis.supervisor.show_profile()` -> Ashlee Martinez, ashlee_martinez@gmail.com, phone 3506492550.
- `apis.phone.get_current_date_and_time()` -> {'date': 'Thursday, May 18, 2023', 'time': '12:00 PM'}, so "yesterday" = 2023-05-17.
- `apis.phone.search_contacts(access_token, query="Spencer Powell")` -> Spencer Powell, spencer.powell@gmail.com, relationships ['manager','coworker'], work_address identical to the user's Work address (8875 Amy Extensions Suite 797, Seattle, Washington, United States, 49596).
- `apis.phone.search_contacts(access_token, relationship="coworker")` -> Adam Blackburn, Angela Riddle, Connor Brown, Glenn Burton, Jeffrey Smith, Jordan Harrison (all work_address 8875 Amy Extensions Suite 797) plus Spencer Powell. These six are exactly the six feed payers.

## Social feed: friends' payments to the manager (Azure Harbor Bistro, 2023-05-17)
`apis.venmo.show_social_feed(access_token, page_index, page_limit=20)` paginated to exhaustion (1032 rows total); filtering description contains "Azure Harbor Bistro" yields exactly 6 rows, all created 2023-05-17T14:46:15, all received by Spencer Powell (spencer.powell@gmail.com):

| transaction_id | amount | description | created_at | sender | sender email | receiver |
|---|---|---|---|---|---|---|
| 8216 | 29.0 | Dinner at Azure Harbor Bistro | 2023-05-17T14:46:15 | Jordan Harrison | jo-harr@gmail.com | Spencer Powell |
| 8217 | 20.0 | Azure Harbor Bistro | 2023-05-17T14:46:15 | Angela Riddle | angriddle@gmail.com | Spencer Powell |
| 8218 | 42.0 | Azure Harbor Bistro | 2023-05-17T14:46:15 | Adam Blackburn | ad.blackburn@gmail.com | Spencer Powell |
| 8219 | 44.0 | Food at Azure Harbor Bistro | 2023-05-17T14:46:15 | Jeffrey Smith | jefsmith@gmail.com | Spencer Powell |
| 8220 | 23.0 | Food at Azure Harbor Bistro | 2023-05-17T14:46:15 | Connor Brown | connorbrow@gmail.com | Spencer Powell |
| 8221 | 31.0 | Dinner at Azure Harbor Bistro | 2023-05-17T14:46:15 | Glenn Burton | glenn.burton@gmail.com | Spencer Powell |

Sum of the six others' reimbursements = 29 + 20 + 42 + 44 + 23 + 31 = 189.0.

## User's own share (absent from the social feed)
`apis.venmo.show_transactions(access_token, user_email='spencer.powell@gmail.com', min_created_at='2023-05-17', max_created_at='2023-05-17')` returns exactly one row:

| transaction_id | amount | description | created_at | sender | receiver |
|---|---|---|---|---|---|
| 8222 | 38.0 | Dinner at Azure Harbor Bistro | 2023-05-17T14:46:15 | Ashlee Martinez (ashlee_martinez@gmail.com) | Spencer Powell (spencer.powell@gmail.com) |

This is why the user's own payment does not appear on their own social feed (the feed only lists friends' transactions).

## Calculation
- Others (six feed rows): 29 + 20 + 42 + 44 + 23 + 31 = 189.0
- My share: 38.0
- Manager's total paid for the others, including me: 189.0 + 38.0 = 227.0

## Exclusions / limits
- Same-day rows at a different venue "Mirage Melange Diner" were excluded — different venue and receiver.
- No mutation was performed; only read/auth calls. All figures are live API outputs within their stated scope.
