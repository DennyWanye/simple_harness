# REPORT.md

Discovery Task (mission-dfa80e406536b063:task-1). Full report: see `DISCOVERY_REPORT.md` (identical content).

## Summary
- Simulated user: Lindsey Simpson (lindseysimpson@gmail.com). Simulated now: 2023-05-18 11:58.
- Roommates (phone contacts, relationship=roommate): Chris Mccoy (chris.mcco@gmail.com), Jose Harrison (joseharr@gmail.com), Paul Miller (paul_mill@gmail.com).
- 68 sent payment requests total; 19 pending; 8 pending to roommates.
- ELIGIBLE (roommate + pending + 30+ days) = **7 requests**: 3455, 3456, 3457 (Jose Harrison); 3461, 3463, 3464, 3470 (Paul Miller).
- Excluded: 3462 (Paul Miller, only 2 days pending); 11 decided roommate requests; 11 pending requests to non-roommates.
- Reminder API: `apis.venmo.remind_payment_request(payment_request_id=<int>, access_token=<Bearer from venmo login>)`.
- No reminders/approvals/denials were performed in this Task (discovery only).
