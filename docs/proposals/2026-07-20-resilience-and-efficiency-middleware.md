# Proposal: Resilience & Token-Efficiency Middleware

**Status:** Design proposal (no code yet) — for review
**Date:** 2026-07-20
**Target:** `archolith_mcp_framework.middleware` (+ `__init__.py` exports)

## 1. Why

The framework standardizes MCP-server bootstrap, tool discovery, response
shape, and async jobs, but every server that wraps a flaky or expensive backend
(network APIs, SSH/subprocess, licensed tools, GPUs) currently re-implements the
same cross-cutting protections — or ships without them. The existing middleware
story (`ErrorHandling`, `Timing`, `Timeout`, `StructuredLogging`) covers error
safety and observability but has no answer for:

- **Transient failures** — a flaky HTTP/subprocess call fails the whole tool turn.
- **Runaway load** — nothing stops an LLM from firing a burst of expensive calls.
- **Unbounded parallelism** — backends with limited concurrency get trampled.
- **Oversized outputs** — one huge tool return can blow the client's context window.
- **Repeated identical calls** — LLM clients routinely re-call the same idempotent
  tool; each call re-executes and re-bills tokens/latency.

This proposal adds **five opt-in middleware** that close these gaps. Each is pure
cross-cutting infrastructure with **no business logic**, matching the repo's
"stay narrow and reusable" design boundary. All reuse the framework's existing
`Middleware` / `on_call_tool` contract and `ToolResult` shape, so they compose
with every server built on `create_gateway_server` or `BaseGatewayServer`.

### Design constraints honored

- Subclass `fastmcp.server.middleware.Middleware`; override `on_call_tool`.
- Return / pass through `ToolResult`; errors surface as `ToolResult(is_error=True)`
  or by re-raising into `ErrorHandlingMiddleware`.
- **Opt-in and targeted.** Nothing is added to the `create_gateway_server`
  defaults. Each middleware supports `include`/`exclude` tool sets so servers
  apply them selectively (e.g. never retry/cache a mutating tool).
- **Single-process model.** Each MCP server is its own stdio process on one
  asyncio event loop (the framework's documented model), so in-memory state
  (buckets, semaphores, caches) is effectively per-server — same assumption
  `jobs.py` already relies on. State is guarded by a `threading.Lock` where it
  can be touched from worker threads, matching `jobs.py`/`duration_stats.py`.

---

## 2. Recommended composition order

Middleware is an onion: the outermost `on_call_tool` runs first and wraps
everything inside it. Order matters; the recommended stack (outermost →
innermost) is:

```
1. ErrorHandlingMiddleware        (existing)  outermost — catches everything
2. OutputGuardMiddleware          (new)       size-guard the wire on the way out
3. CachingMiddleware              (new)       cache hits skip all real work below
4. RateLimitMiddleware            (new)       shed over-rate requests early
5. RetryMiddleware                (new)       retry transient backend failures
6. ConcurrencyLimitMiddleware     (new)       cap concurrent executions
7. TimeoutMiddleware              (existing)  hard per-execution deadline
8. TimingMiddleware / StructuredLoggingMiddleware (existing, flexible)
   └── [ actual tool execution ]
```

Rationale for the non-obvious placements:

- **`OutputGuard` high (outside `Caching`):** it is the last safety net before a
  result hits the wire, so it guards *every* result — fresh, cached, or error —
  against the current limit. The cache stores the *full* result; the guard
  applies the limit at egress, so raising `max_chars` later isn't defeated by
  stale truncated cache entries.
- **`Caching` outside `RateLimit`/`Retry`/`Concurrency`/`Timeout`:** a cache hit
  returns immediately without consuming a rate-limit token, a concurrency slot,
  or touching the timeout path — those protections exist to bound *real work*,
  and a memoized answer is not real work.
- **`RateLimit` outside `Retry`:** rate limiting counts *client requests*. A
  retried attempt should not burn extra client quota; `Retry`'s own backoff
  prevents it from slamming the backend.
- **`Retry` outside `Concurrency`/`Timeout`:** each attempt re-runs the
  concurrency + timeout path, so a timed-out attempt releases its slot and a
  retry starts clean.
- **`Concurrency` outside `Timeout`:** the semaphore bounds executing calls; the
  timeout cancels a hung execution so it releases the slot (a hung call must not
  hold a slot forever). Queue *wait* time is bounded separately by
  `acquire_timeout_s`, so it is not conflated with the execution timeout.

This is a recommendation, not a constraint — every middleware is independent and
servers reorder or omit freely via `middlewares=[...]`.

---

## 3. Shared conventions

All five middleware share these patterns (each takes the same targeting args):

```python
include: set[str] | None = None   # if set, ONLY these tools are affected
exclude: set[str] | None = None   # these tools are NEVER affected (wins over include)
```

Selection rule (shared helper, suggested private `_selects(tool_name)`):

```python
def _selects(self, name: str) -> bool:
    if self._exclude and name in self._exclude:
        return False
    if self._include is None:
        return True
    return name in self._include
```

- Module logger: reuse `logger = logging.getLogger("archolith.mcp.framework")`.
- Non-selected tools pass straight through: `return await call_next(context)`.
- Shedding responses (rate-limit / busy / oversize-error) are returned as
  `ToolResult(content=[mt.TextContent(type="text", text=...)], is_error=True)`
  with an **LLM-actionable** message (what happened + what to do next), never a
  raw traceback.

---

## 4. `RetryMiddleware`

Retries tool calls that fail with *transient* errors using exponential backoff
with jitter. Only meaningful for **idempotent** tools — servers exclude mutating
ones (`write_*`, `deploy`, `delete_*`).

```python
class RetryMiddleware(Middleware):
    def __init__(
        self,
        *,
        max_retries: int = 2,                 # retries AFTER the first attempt
        base_delay_s: float = 0.5,            # first backoff
        backoff_factor: float = 2.0,          # exponential multiplier
        max_delay_s: float = 8.0,             # backoff cap
        jitter: float = 0.25,                 # ±fraction randomized (anti-thundering-herd)
        retry_on_exceptions: tuple[type[BaseException], ...] | None = None,
                                              # None = retry all EXCEPT _NON_RETRYABLE
        non_retryable: tuple[type[BaseException], ...] = (ValueError, TypeError, KeyError),
                                              # bad input never succeeds on retry
        retry_on_tool_error: bool = False,    # also retry ToolResult(is_error=True)?
                                              # default False: most is_error are legit client errors
        overall_timeout_s: float | None = None,  # deadline across ALL attempts
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> None: ...

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult: ...
```

Behavior:

- Not selected → `return await call_next(context)`.
- Loop `attempt in range(max_retries + 1)`:
  - `try: result = await call_next(context)`
  - If `result.is_error` and `retry_on_tool_error` and attempts remain → backoff + retry; else return `result`.
  - `except Exception as exc`: if `exc` matches `non_retryable` (or is not in
    `retry_on_exceptions` when that is set) → **raise immediately** (no retry).
    Else if attempts remain → log `warning` (tool, attempt, exc type), backoff,
    retry; else **raise the last exception** so `ErrorHandlingMiddleware`
    converts it to a structured response.
- Backoff: `delay = min(max_delay_s, base_delay_s * backoff_factor ** attempt)`,
  then `delay *= 1 + random.uniform(-jitter, jitter)`, clamped to `>= 0`;
  `await asyncio.sleep(delay)`.
- `overall_timeout_s`: track a monotonic deadline; abort the loop and raise the
  last error if the next attempt would start past it.

Notes:

- `ToolError` (FastMCP protocol error, raised intentionally by tools) is treated
  as **non-retryable by default** — it represents a deliberate tool-level error
  that `ErrorHandlingMiddleware` re-raises to the protocol. Servers may opt it
  back in via `retry_on_exceptions`.
- Docs/docstring must carry the idempotency warning prominently.

### Test plan
- Success first try → `call_next` called once, no sleep.
- Transient fail → success → correct call count, success returned.
- All attempts fail → last exception raised (caught by a wrapper in the test).
- `non_retryable` (`ValueError`) → raised immediately, no retry.
- `retry_on_tool_error=True` retries `is_error` results; `False` returns them.
- `include`/`exclude` targeting.
- Backoff schedule captured by monkeypatching `asyncio.sleep` (assert the
  exponential sequence, jitter within bounds, cap applied).

---

## 5. `RateLimitMiddleware`

Token-bucket rate limiter to bound request rate (protects metered/expensive
backends). Sheds over-rate requests instead of letting them pile up.

```python
class RateLimitMiddleware(Middleware):
    def __init__(
        self,
        *,
        rate: float,                        # sustained tokens/second (e.g. 5.0)
        burst: int | None = None,           # bucket capacity; default ceil(rate)
        per_tool: bool = False,             # one bucket per tool name vs one global bucket
        on_limit: Literal["reject", "wait"] = "reject",
        max_wait_s: float = 5.0,            # "wait" mode: max seconds to block for a token
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> None: ...

    async def on_call_tool(self, context, call_next) -> ToolResult: ...
```

Behavior:

- Bucket state: `{tokens: float, last_refill: float(monotonic)}`. Refill lazily:
  `tokens = min(burst, tokens + (now - last_refill) * rate)`.
- On call (if selected): refill, then if `tokens >= 1.0` consume one and
  `await call_next`; else:
  - `reject` → return `ToolResult(is_error=True)` with text like
    `"Rate limit exceeded for '<tool>'. ~<wait>s until the next slot; back off and retry once."`
    and **do not** call downstream.
  - `wait` → compute `need = (1 - tokens) / rate`; if `need <= max_wait_s`,
    `await asyncio.sleep(need)`, refill, consume, proceed; else reject as above.
- `per_tool=True` keeps a `dict[str, bucket]` keyed by `context.message.name`.
- Concurrency: operations are short and synchronous; guard the bucket dict with a
  `threading.Lock` (cheap; mirrors `jobs.py`). No `await` is held under the lock
  except the optional `wait` sleep, which is done **outside** the lock.
- Helper `_seconds_until_token(bucket) -> float` for the message.

### Test plan
- Requests within `burst` all pass (downstream called each time).
- Exceeding `burst` → reject result, `is_error=True`, downstream NOT called.
- Time-based refill: monkeypatch the monotonic clock forward → allowed again.
- `per_tool=True` → exhausting tool A does not affect tool B.
- `wait` mode: sleeps ~expected then proceeds (monkeypatch sleep); exceeding
  `max_wait_s` → reject.

---

## 6. `ConcurrencyLimitMiddleware`

Caps the number of concurrently *executing* tool calls. For backends that cannot
handle parallelism (single SSH session, one GPU, a licensed single-seat tool).

```python
class ConcurrencyLimitMiddleware(Middleware):
    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        per_tool: bool = False,
        on_limit: Literal["queue", "reject"] = "queue",
        acquire_timeout_s: float | None = None,  # max time to WAIT for a slot (queue mode)
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> None: ...

    async def on_call_tool(self, context, call_next) -> ToolResult: ...
```

Behavior:

- One `asyncio.Semaphore(max_concurrent)` (global) or a `dict[str, Semaphore]`
  (per_tool). **Created lazily on first use** so it binds to the running loop
  (safe across the single-loop stdio model; avoids loop-binding at import).
- Selected call, `queue` mode:
  - `acquired = await asyncio.wait_for(sem.acquire(), acquire_timeout_s)` if a
    timeout is set, else `await sem.acquire()`.
  - On `asyncio.TimeoutError` (couldn't get a slot in time) → return
    `ToolResult(is_error=True)` `"Server busy: <tool> has <max> calls in flight. Retry shortly."`
  - Else `try: return await call_next(context) finally: sem.release()`.
- `reject` mode: `if sem.locked(): return busy_error` else acquire/proceed/release.
  (Use `locked()` as the fast non-blocking check; under a single loop there is no
  race between the check and `acquire()`.)
- Release is guaranteed by `finally`, so an exception or timeout inside the tool
  never leaks a slot.

### Test plan
- Fire `max_concurrent + N` concurrent calls; assert at most `max_concurrent`
  execute simultaneously (gate them on an `asyncio.Event`) and the rest queue.
- Slot released on success **and** on raised exception.
- `acquire_timeout_s` exceeded → busy `is_error` result.
- `reject` mode returns busy immediately when saturated.
- `per_tool` isolation.

---

## 7. `OutputGuardMiddleware`

Last-line guard on tool output size — complements `ChunkedIOMixin` (which bounds
file *reads*) by bounding *arbitrary tool returns* before they reach the client.
Prevents a single huge result from overflowing the context window.

```python
class OutputGuardMiddleware(Middleware):
    def __init__(
        self,
        *,
        max_chars: int = 100_000,           # per text content block
        max_total_chars: int | None = None, # across all text blocks combined
        strategy: Literal["truncate", "head_tail", "error"] = "truncate",
        keep_ratio: float = 0.7,            # head_tail: fraction of budget kept from the head
        notice: str | None = None,          # override the truncation notice template
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> None: ...

    async def on_call_tool(self, context, call_next) -> ToolResult: ...
```

Behavior:

- `result = await call_next(context)`; if not selected → return as-is.
- Walk `result.content`; for each `mt.TextContent` with `len(text) > budget`:
  - `truncate` → keep the first `budget` chars + append a notice:
    `"\n\n[… output truncated: {orig} chars → {budget}. Paginate or narrow the request to retrieve the rest.]"`
  - `head_tail` → keep `head = text[:int(budget*keep_ratio)]` and
    `tail = text[-(budget - len(head)):]`, joined by
    `"\n\n[… {dropped} chars elided …]\n\n"`. Ideal for logs (start + end matter).
  - `error` → replace the whole result with
    `ToolResult(is_error=True)` advising the client to narrow the request
    (include the size so the LLM can adjust).
  - Rebuild the block with `block.model_copy(update={"text": new_text})`
    (`TextContent` is a pydantic model).
- `max_total_chars`: track running total across text blocks and shrink later
  blocks' budgets so the combined text stays under the cap.
- Non-text blocks (`ImageContent`, `ResourceLink`, `EmbeddedResource`, …) are
  passed through untouched.
- Return a **new** `ToolResult` with the replaced `content`, preserving
  `is_error` and `structured_content`.
- Log a `warning` whenever truncation fires (tool, original vs kept size).

Scope note: the guard targets **text content**. `structured_content` (a JSON
dict) is left intact — servers that need to bound structured payloads should use
`CompactMixin`/`fields` projection, which is the right tool for that shape.

### Test plan
- Small output → identical result.
- Oversized single block, `truncate` → shortened, notice present and includes sizes.
- `head_tail` → head + tail kept, elision marker with dropped count.
- `error` → `is_error=True` result, downstream output replaced.
- `max_total_chars` across multiple blocks.
- Non-text blocks unaffected.
- `include`/`exclude` targeting; `is_error`/`structured_content` preserved.

---

## 8. `CachingMiddleware`

TTL + LRU memoization for **explicitly allow-listed idempotent** tools, so
repeated identical calls return instantly instead of re-executing. The single
rule that makes this safe: **cacheability is an explicit opt-in** — `include` is
required and must be non-empty, so the framework can never silently cache a
mutating tool.

```python
class CachingMiddleware(Middleware):
    def __init__(
        self,
        *,
        include: set[str],                  # REQUIRED, non-empty: only these tools are cached
        ttl_s: float = 60.0,
        max_entries: int = 256,             # LRU eviction beyond this
        key_on_args: bool = True,           # True: key=(tool,args); False: key=(tool,)
        cache_errors: bool = False,         # default: never cache is_error results
        exclude: set[str] | None = None,    # belt-and-suspenders veto, even if in include
    ) -> None: ...

    async def on_call_tool(self, context, call_next) -> ToolResult: ...
```

Behavior:

- Validate in `__init__`: `include` must be a non-empty set (raise
  `ValueError("CachingMiddleware requires a non-empty 'include' set")`).
- Key: `(tool_name, json.dumps(args, sort_keys=True, default=str))` when
  `key_on_args`, else `(tool_name,)`. Canonical JSON makes the key independent of
  argument dict ordering.
- On selected call: compute key. If present and `(now - ts) < ttl_s` → log
  `debug` "cache hit", `move_to_end`, **return the cached `ToolResult`** (no
  `call_next`). Optionally stamp `meta={"cache": "hit"}` — noted as optional to
  avoid changing wire `meta` semantics.
- On miss / stale: `result = await call_next(context)`; if
  `cache_errors or not result.is_error` → store `(result, now)`, `move_to_end`,
  and `popitem(last=False)` while `len > max_entries` (LRU). Return `result`.
- Storage: `collections.OrderedDict` guarded by a `threading.Lock` (short,
  synchronous critical sections; single loop otherwise).

### Test plan
- Tool not in `include` → never cached (`call_next` called every time).
- Cached tool, identical args, 2nd call → served from cache (`call_next` once).
- Different args → distinct entries (both execute).
- Arg-dict ordering does not change the key (canonical JSON).
- TTL expiry → re-executes after the window.
- `is_error` results not cached by default (`cache_errors=False`); cached when True.
- LRU eviction at `max_entries` (oldest dropped, recent kept).
- Empty `include` → `ValueError` at construction.

---

## 9. Exports, wiring & docs

**Location:** add all five classes to `src/archolith_mcp_framework/middleware.py`
(keeps the single-module convention; promote to a `middleware/` package later
only if it grows substantially).

**Exports:** add to `archolith_mcp_framework/__init__.py` `__all__` and the
`cth_mcp_framework` forwarding package (per the maintenance rules, any change to
the public surface is documented). `create_gateway_server` defaults are
**unchanged** — these stay opt-in.

Recommended full-stack wiring example (goes in the docstrings / README):

```python
from archolith_mcp_framework import (
    create_gateway_server,
    ErrorHandlingMiddleware, TimingMiddleware, TimeoutMiddleware,
    OutputGuardMiddleware, CachingMiddleware, RateLimitMiddleware,
    RetryMiddleware, ConcurrencyLimitMiddleware,
)

mcp = create_gateway_server(
    "example.gateway",
    middlewares=[
        ErrorHandlingMiddleware(),                                   # 1 catch-all
        OutputGuardMiddleware(max_chars=120_000),                    # 2 size guard
        CachingMiddleware(include={"search_docs", "get_status"},     # 3 memoize reads
                          ttl_s=30),
        RateLimitMiddleware(rate=10, burst=20),                      # 4 shed load
        RetryMiddleware(max_retries=2,                               # 5 transient retry
                        exclude={"deploy", "write_file", "delete_repo"}),
        ConcurrencyLimitMiddleware(max_concurrent=4),                # 6 cap parallelism
        TimeoutMiddleware(timeout_s=90),                             # 7 hard deadline
        TimingMiddleware(),                                          # 8 observe
    ],
)
```

**Docs to update on implementation** (per `.agent/README.md` maintenance rules):

- `.agent/architecture.md` — add the five middleware to the middleware table and
  add a "Middleware composition order" subsection (the onion from §2).
- `CHANGELOG.md` — new `0.3.0` entry.
- `README.md` — mention the resilience middleware in the feature list.

**Test surface:** new `tests/test_resilience_middleware.py`, reusing the direct
`MiddlewareContext` + fake `call_next` harness pattern already used in
`tests/test_framework.py` (construct `mt.CallToolRequestParams` and an async
`call_next` stub; monkeypatch `asyncio.sleep` / the monotonic clock for timing).

---

## 10. Deliberately out of scope (future work)

Reviewed but **not** in this batch, so the change stays focused:

- **MCP Resources & Prompts helpers** — the framework is tool-only today; a
  `ResourceMixin`/`PromptMixin` would fill a real primitive gap, but it is a
  larger architectural addition best done separately.
- **Metrics middleware + health/introspection tool** — in-process call
  counts/error rates/latency histograms exposed via a `<prefix>metrics` /
  `<prefix>health` tool. Valuable observability, but orthogonal to resilience.
- **Native MCP progress notifications** — `jobs.py` already solves progress for
  long-running work; wiring MCP progress tokens is a separate enhancement.
- **A `resilience=` shortcut on `create_gateway_server`** — a convenience preset
  that installs a sensible default stack. Nice-to-have, but risks encouraging
  unthinking defaults; keep explicit wiring for now.

Each is a clean follow-up that composes with the middleware in this proposal.
