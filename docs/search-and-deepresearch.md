# Search and DeepResearch

simple_harness includes an in-process Search Gateway. Normal users do not need to
install Docker, SearXNG, a browser automation package, or a paid search API.
Both quick search (`web_search`) and new DeepResearch runs use this gateway so
that routing, result normalization, deduplication, caching, cooldowns, and
diagnostics behave consistently.

## Default behavior

The gateway is enabled by default. Its desktop-safe provider set is Baidu,
DuckDuckGo, Google through the existing Edge CDP integration, and Bing through
the same CDP integration. A request has a total deadline and each provider has
its own shorter timeout; an empty result, timeout, 403/429 response, CAPTCHA,
or parse failure is recorded as a structured attempt and the gateway continues
to the next provider while budget remains.

simple_harness does not solve CAPTCHAs or bypass an upstream service's rate limit. It
uses bounded fallback, temporary provider cooldown, cached results, and a clear
degraded response instead. When no source is available, DeepResearch reports
the missing evidence instead of inventing citations.

## Optional SearXNG

SearXNG is an optional HTTP provider, not a bundled second backend. To use an
existing SearXNG instance, it must expose its JSON search format. Configure its
base URL in `config.toml`:

```toml
[search_gateway]
enabled = true
searxng_url = "https://search.example.com"
```

simple_harness only registers the provider when `searxng_url` is non-empty and valid.
The instance must allow a request equivalent to
`GET /search?q=deskpet&format=json`. If the server disables JSON, requires an
interactive challenge, or cannot be reached, the provider is skipped or
degraded without disabling the built-in routes.

To stop using SearXNG, set `searxng_url = ""` and restart simple_harness. To disable
the entire gateway for diagnosis, set `enabled = false`; this is not the
recommended everyday configuration.

## Diagnosing search

Search responses expose safe operational diagnostics such as providers tried,
providers that returned results, elapsed time, cache use, and public error
codes. They never include cookies, authorization headers, prompts, tokens, or
complete private URLs. Typical error codes have these meanings:

- `timeout`: the provider did not finish within its own budget;
- `blocked`, `captcha`, or `rate_limited`: the upstream service refused the
  automated request, so the gateway tried another route;
- `empty` or `parse_error`: no usable normalized candidates were produced;
- `cooldown`: recent repeated failures temporarily removed the provider from
  this request's route.

For SearXNG, first open its `/search?format=json&q=test` endpoint directly on
the same machine. A JSON response confirms the required format; an HTML page,
403, or 429 explains why simple_harness will fall back.

## DeepResearch progress

New DeepResearch runs use the durable `deep_research/v2` workflow. The chat
keeps one overall progress card visible for each run. Each completed,
user-understandable stage is also stored as a child message. Successful child
messages are collapsed by default; expanding the progress card shows the
ordered audit trail, including safe counts, elapsed time, degradation status,
and the next stage. Waiting, cancellation, unrecoverable failure, and final
completion stay visible in the main message stream.

Progress and reports are durable. Reloading a conversation or restarting the
desktop app rebuilds the same progress group from stored workflow events and
continues an unfinished run from its checkpoint. Older `deep_research/v1`
runs remain readable and resumable with their original definition.
