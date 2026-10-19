# XCore — Complete Knowledge Base for AI Agents

> This file is the source of truth for any agent working on XCore.
> It covers the architecture, APIs, patterns, gotchas, and complete examples.
>
> **Last verified against `main` (commit `7c95dff`, XCore 2.9.1) on 2026-10-17.**
> Each claim was checked against the code. When code and this file disagree, the code
> wins: fix this file in the same change.

---

## 1. Quick Overview

XCore is a **plugin-first orchestration framework** built on FastAPI.
It loads, isolates, and orchestrates **plugins** (Python modules) in a secure, sandboxed environment.

```
xcore/
├── kernel/          # api/ (contract, auth, rbac, router), runtime/ (supervisor, lifecycle, loader),
│                    # middlewares/, permissions/, tenancy/, events/, sandbox/, security/,
│                    # schema/, observability/
├── services/        # DB, Cache, Scheduler, Worker (Celery), extensions, DI container
├── configurations/  # YAML Loader + config dataclasses
├── registry/        # Known plugins index (versioning, state store)
├── marketplace/     # Plugin store HTTP client
└── sdk/             # Plugin author kit, vendored in this repo: manifest, @action/@schema/@route,
                     # AutoDispatchMixin, SQL adapters. Some decorators only exist with the
                     # optional `xcdk` package (see §4).
```

**Essential Commands:**
```bash
poetry run xcli worker start api   # start the API
make test                           # full tests + coverage
make lint-fix                       # black + isort
poetry run pytest tests/ -x -q     # fast pytest run
python scripts/profile_runtime.py calls   # profiling (see §18)
```

**Main Config:** `integration.yaml` (not `xcore.yaml`)

### Installation and optional extras

The core install is deliberately light (since PR #307): `fastapi`, `uvicorn[standard]`, `pydantic`,
`pyyaml`, `apscheduler`, `opentelemetry-sdk`. Python `>=3.12`. Everything else is an extra:

| Extra | Brings | Needed for |
|---|---|---|
| `db` (`postgres`, `sqlite`) | SQLAlchemy 2.1 asyncio, psycopg2 / aiosqlite | `get_service("db")`, `BaseAsyncRepository` |
| `migrations` | alembic | `MigrationRunner` |
| `redis` | redis[hiredis] | Redis cache backend, `redisAdapter` |
| `worker` | celery | the `xworker` service |
| `tracing` | OTLP HTTP exporter | exporting OpenTelemetry spans to a collector |
| `metrics` | prometheus-client | Prometheus backend and root `/metrics` |
| `sdk` | xcdk | `@cron`, `@interval`, `@health_check`, `@cached`, `@on_event`… (see §4) |
| `xcli` | xcorecli | the `xcli` command |
| `cpp` | xcorescanner | native scanner |
| `dotenv` | python-dotenv | `envconfiguration` in `plugin.yaml` |
| `all` | all of the above | |

---

## 2. Plugin — Minimal Structure

```
app/plugins/my_plugin/
├── plugin.yaml
└── src/
    └── main.py
```

### plugin.yaml — Complete Fields

```yaml
name: my-plugin
version: 1.2.0
author: team
description: What this plugin does.
framework_version: ">=2.3"

# trusted | sandboxed | ephemeral. If omitted the plugin loads as sandboxed (fail-closed).
# Only `name` and `version` are required.
execution_mode: trusted

entry_point: src/main.py

# Environment variables injected into the plugin
env:
  DATABASE_URL: "postgresql://..."
  API_KEY: "${MY_API_KEY}"   # interpolated from system env variables

# Dependencies on other plugins (loaded before this one)
requires:
  - auth-plugin
  - billing-plugin

# Declared permissions (REQUIRED to access services)
permissions:
  - resource: "db.*"
    actions: ["read", "write"]
    effect: allow
  - resource: "cache.*"
    actions: ["read", "write"]
    effect: allow
  - resource: "scheduler.*"
    actions: ["add", "remove"]
    effect: allow
  - resource: "events.*"
    actions: ["emit", "subscribe"]
    effect: allow

# Who may call this plugin over IPC. Enforced only when `tenancy.enabled` AND
# `tenancy.enforce_ipc` (default true) are on, and then deny-by-default: a plugin
# with an empty list rejects every incoming IPC call (`ipc_denied`).
allowed_callers:
  - billing-plugin

# Sandboxed plugins only: AST whitelist of importable modules
allowed_imports: [json, re, math]

# Kernel-side behaviour for this plugin
runtime:
  health_check: { enabled: true, interval_seconds: 30, timeout_seconds: 3 }
  retry: { max_attempts: 1, backoff_seconds: 0.0 }   # defaults shown: no retry

# Resource limits (optional, reasonable defaults)
resources:
  timeout_seconds: 30
  max_memory_mb: 512
  rate_limit:
    calls: 100
    period_seconds: 60

# Plugin-specific config: ANY other top-level key is collected in `PluginManifest.extra`
# and exposed as `self.ctx.config` (a plain dict). There is no `extra:` block.
my_setting: "value"      # -> self.ctx.config["my_setting"]
max_items: 100           # -> self.ctx.config["max_items"]
```

> `xcore/sdk/manifest_schema.json` (editor validation) forbids unknown top-level keys, but the
> runtime loader accepts them and keeps them in `manifest.extra`. Other recognised keys:
> `filesystem` and `envconfiguration` (loads a `.env` for the plugin, needs the `dotenv` extra).

### plugin.yaml — Ephemeral Mode

```yaml
execution_mode: ephemeral

ephemeral:
  pool_size: 4          # pre-warmed instances (0 = pure cold boot)
  max_idle_seconds: 120 # release after X seconds of inactivity
  max_concurrent: 8     # max parallelism (backpressure beyond this)
  boot_timeout: 5.0     # instance boot timeout
```

The `ephemeral:` block is not parsed by `PluginManifest`: it lands in `manifest.extra` and the
activator reads it from there, falling back to the global `plugins.ephemeral` defaults.
Each pooled instance is its own `LifecycleManager(..., pooled=True)` with a private `sys.modules` namespace.

### plugin.yaml — Sandboxed Mode

```yaml
execution_mode: sandboxed

resources:
  timeout_seconds: 10
  max_memory_mb: 256
  max_disk_mb: 100      # disk quota for the subprocess
```

---

## 3. Plugin — Complete Code

### Minimal Import

```python
from xcore.sdk import TrustedBase, ok, error
```

### Base Class

```python
# src/main.py
from xcore.sdk import (            # always available
    TrustedBase, ok, error,
    action, route, schema,
    require_service,
)
# Only with the optional `xcdk` package: pip install "XCoreRuntime[sdk]"
# (without it these names raise ImportError, see §4)
from xcore.sdk import (
    cron, interval,
    on_event, on_hook,
    health_check, traced, counted, timed,
    cached, invalidate,
    AutoMixin,
)

class Plugin(TrustedBase):

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def on_load(self):
        """Called once upon boot. Initialize services here."""
        self.db = self.get_service("db")
        self.cache = self.get_service("cache")
        self.logger.info("plugin loaded", plugin=self.ctx.name)

    async def on_reload(self):
        """Called after a hot reload."""
        await self.on_load()

    async def on_unload(self):
        """Called upon shutdown."""
        self.logger.info("plugin stopped")

    # ── Main Action Handler ──────────────────────────────────────────────────

    async def handle(self, action: str, payload: dict) -> dict:
        if action == "ping":
            return ok(msg="pong")
        if action == "create_user":
            return await self._create_user(payload)
        return error(f"Unknown action: {action}", "unknown_action")
```

### Lifecycle Hooks

`TrustedBase` hooks, all optional and `async`: `on_init`, `on_load`, `on_start`, `on_reload`,
`on_stop`, `on_unload`. Load runs `on_init → on_load → on_start`; a hot reload runs `on_reload`;
unload runs `on_stop → on_unload`. The kernel force-cleans what the plugin registered (tracked
resources, spawned tasks, exported services) at unload even if those hooks are missing or raise.

### Standardized Responses

```python
from xcore.sdk import ok, error

return ok()                              # {"status": "ok"}
return ok(data={"id": 1})               # {"status": "ok", "data": {"id": 1}}
return ok(user_id=42, name="Alice")     # {"status": "ok", "user_id": 42, "name": "Alice"}

return error("message")                 # {"status": "error", "msg": "message"}
return error("not found", "not_found")  # {"status": "error", "msg": "...", "code": "not_found"}
```

---

## 4. SDK — All Decorators

**Not everything in this section ships with XCore.** `xcore/sdk` provides a local core; the
rest is re-exported from the external `sdk` module (package `xcdk`) only when it is installed.
Without it, `from xcore.sdk import cron` raises `ImportError`.

| Source | Names |
|---|---|
| Local (always) | `TrustedBase`, `BasePlugin`, `ok`, `error`, `ExecutionMode`, `PluginManifest` (+ its config classes), `action`, `schema`, `validate_payload`, `route`, `RoutedPlugin`, `RouterRegistry`, `sandboxed`, `trusted`, `require_service`, `AutoDispatchMixin`, `RBACChecker`, `require_role`, `require_permission`, `AuthBackend`, `AuthPayload`, `register_auth_backend`, `unregister_auth_backend`, `get_auth_backend`, `has_auth_backend`, `Event`, `HookResult`, `get_logger`, `PermissionDenied`, `PluginState`, `WorkerService`, `task`, `task_registry` |
| Local, lazy (needs the `db` extra) | `BaseAsyncRepository`, `BaseSyncRepository` |
| Optional `xcdk` (`[sdk]` extra) | `AutoMixin`, `EventMixin`, `HookMixin`, `ObservabilityMixin`, `ScheduledMixin`, `cron`, `interval`, `on_event`, `on_hook`, `health_check`, `traced`, `counted`, `timed`, `cached`, `invalidate`, `BaseMongoRepository`, `BaseRedisRepository` |

Sections below marked *(xcdk)* need the extra.

### @action + AutoDispatchMixin

```python
from xcore.sdk import action, AutoDispatchMixin

class Plugin(AutoDispatchMixin, TrustedBase):
    # AutoDispatchMixin generates handle() automatically

    @action("greet")
    async def greet(self, payload: dict) -> dict:
        return ok(msg=f"Hello {payload.get('name', 'world')}")

    @action("bye")
    async def bye(self, payload: dict) -> dict:
        return ok(msg="Goodbye")
```

`@action` also declares authorization and risk, enforced/read outside the plugin:

```python
@action("close_account",
        permissions=["accounts:write"],                          # ALL required (AND)
        permission_groups=[["tenants:write"], ["admin:*"]],      # at least one group fully satisfied (OR of ANDs)
        side_effect="write")                                     # "read" | "write" | "outbound", purely declarative
async def close_account(self, payload: dict) -> dict: ...
```

`permissions`/`permission_groups` are checked by `ActionPermissionMiddleware` against the caller's
principal (`principal["roles"] | principal["permissions"]`, see §11). An action that declares neither
is not checked (backward compatible). `side_effect` is stored on the function only; XCore itself
does nothing with it (an external bridge, e.g. LLM tooling, may use it for approval decisions).

### @schema — Validation + Contract Versioning

```python
@action("create_user")
@schema(
    version="2.0",
    input={
        "email": (str, ...),       # required
        "role": (str, "user"),     # optional, defaults to "user"
        "age": (int, ...),         # required
    },
    output={"user_id": int, "created_at": str},
    deprecated_fields={"username": "Removed in v2.0"},
    breaking_since="2.0",
    validate=True,           # automatically validate payload
    type_response="dict",    # "dict" | "model" (Pydantic)
)
async def create_user(self, payload: dict) -> dict:
    return ok(user_id=1, created_at="2026-01-01")
```

`@schema` registers the contract in the global `SchemaRegistry` (JSON Schema of input/output is
built with pydantic `create_model`). **Validation only happens when `type_response` is `"dict"` or
`"model"`**: the default `"_"` just stores metadata, even with `validate=True`. `version` is required.
The registry is not exposed over HTTP today.

### @route — FastAPI HTTP Routes

```python
from xcore.sdk import route, RoutedPlugin

class Plugin(RoutedPlugin, TrustedBase):

    @route("/items", method="GET", tags=["items"])
    async def list_items(self):
        return [{"id": 1}]

    @route("/items/{item_id}", method="GET")
    async def get_item(self, item_id: int):
        return {"id": item_id}

    @route("/items", method="POST", status_code=201)
    async def create_item(self, body: dict):
        return {"created": True}

    @route("/admin", method="GET", permissions=["admin"])  # Auto RBAC
    async def admin_endpoint(self):
        return {"secret": True}

    async def handle(self, action, payload):
        return error("unknown action")

# Mounted under <app.plugin_prefix>/<plugin_name><path>  (plugin_prefix defaults to "/plugin").
# Plugin routes are plain FastAPI routes: they do NOT go through supervisor.call() or its
# middleware chain. Trusted plugins only (sandboxed plugins cannot expose routes).
```

### @cron and @interval *(xcdk)*

```python
from xcore.sdk import cron, interval, ScheduledMixin

class Plugin(ScheduledMixin, TrustedBase):

    @cron("0 3 * * *")
    async def nightly_cleanup(self):
        ...

    @cron("*/5 * * * *", job_id="my.sync", max_instances=1, timeout=60.0)
    async def every_5_min(self):
        ...

    @interval(seconds=30)
    async def heartbeat(self):
        ...

    @interval(minutes=10, retry=3, retry_delay=5.0)
    async def sync_with_api(self):
        ...
```

### @on_event and @on_hook *(xcdk)*

> `EventBus.emit()` isolates handler errors: an exception in a handler is logged and counted, never
> raised to the emitter, and `event.cancelled` only stops the *remaining* handlers. Events are
> notifications, they cannot veto an operation. If a step must be able to abort something, call it
> directly (or inspect `HookResult.error` from `HookManager.emit()`). With `gather=True` (default)
> handlers run in parallel; `gather=False` runs them one after the other and stops when
> `event.propagate` is false or `event.cancelled` is set.

```python
from xcore.sdk import on_event, on_hook, EventMixin, HookMixin, Event

class Plugin(EventMixin, HookMixin, TrustedBase):

    @on_event("user.created")
    async def welcome_user(self, event: Event):
        user_id = event.data["user_id"]

    @on_event("order.*")              # wildcard
    async def on_any_order(self, event: Event):
        ...

    @on_event("critical.alert", once=True)
    async def on_first_alert(self, event: Event):
        ...

    @on_hook("plugin.*.loaded", priority=10)
    async def after_any_plugin_load(self, event: Event):
        ...

    async def handle(self, action, payload):
        await self.ctx.events.emit("user.created", {"user_id": 42})
        return ok()
```

### @health_check *(xcdk)*

```python
from xcore.sdk import health_check, ObservabilityMixin

class Plugin(ObservabilityMixin, TrustedBase):

    @health_check("my_plugin.db")
    async def check_db(self) -> tuple[bool, str]:
        try:
            await self.get_service("db").execute("SELECT 1")
            return True, "ok"
        except Exception as e:
            return False, str(e)

    @health_check("my_plugin.api", kind="liveness")
    async def check_internal(self) -> tuple[bool, str]:
        return True, "alive"
```

### @traced, @counted, @timed *(xcdk)*

```python
@action("process")
@traced("process_item")
@counted("plugin.process.calls")
@timed("plugin.process.duration_ms")
async def process(self, payload: dict) -> dict:
    ...
```

### @cached and @invalidate *(xcdk)*

```python
@action("get_user")
@cached(key="user:{payload[user_id]}", ttl=300)
async def get_user(self, payload: dict) -> dict:
    ...

@action("update_user")
@invalidate(key="user:{payload[user_id]}")
async def update_user(self, payload: dict) -> dict:
    ...
```

### Retries (there is no `@retry` decorator)

The SDK has no `@retry`. Retrying a plugin call is done by the kernel `RetryMiddleware`, configured
per plugin in `plugin.yaml`:

```yaml
runtime:
  retry: { max_attempts: 3, backoff_seconds: 1.0 }   # defaults: 1 attempt, no backoff
```

### AutoMixin — All-in-One *(xcdk)*

```python
from xcore.sdk import AutoMixin

class Plugin(AutoMixin):
    """Combines AutoDispatchMixin + EventMixin + HookMixin +
       ObservabilityMixin + ScheduledMixin + RoutedPlugin"""

    @action("ping")
    async def ping(self, payload: dict) -> dict:
        return ok(msg="pong")

    @cron("0 * * * *")
    async def hourly(self):
        ...

    @on_event("user.*")
    async def on_user_event(self, event):
        ...

    @route("/ping", method="GET")
    async def ping_http(self):
        return {"pong": True}
```

---

## 5. Services — Plugin-level Access

### Accessing Services

```python
async def on_load(self):
    self.db      = self.get_service("db")             # AsyncSQLAdapter
    self.cache   = self.get_service("cache")          # CacheService
    self.mongo   = self.get_service("mongodb")        # MongoDBAdapter
    self.redis   = self.get_service("redisAdapter")   # RedisAdapter
    self.syncdb  = self.get_service("syncdb")         # SQLAdapter (sync)
    svc          = self.get_service_as("my_svc", MyService)  # explicit typing
```

### Databases (AsyncSQL)

```python
await self.db.execute("CREATE TABLE IF NOT EXISTS items (id SERIAL PRIMARY KEY, name TEXT)")
await self.db.execute("INSERT INTO items (name) VALUES (:name)", {"name": "foo"})

row  = await self.db.fetch_one("SELECT * FROM items WHERE id = :id", {"id": 1})
rows = await self.db.fetch_all("SELECT * FROM items WHERE active = :a", {"a": True})
item = dict(row) if row else None

from sqlalchemy import text

# session() yields a raw SQLAlchemy AsyncSession: it commits on normal exit and rolls back
# (then re-raises) on exception. Do not issue BEGIN/COMMIT yourself, and wrap SQL in text().
async with self.db.session() as session:
    await session.execute(text("INSERT INTO items (name) VALUES (:n)"), {"n": "foo"})
```

`db.execute()`, `fetch_one()` and `fetch_all()` take plain SQL strings (the adapter wraps them in
`text()`). All SQL features need the `db` extra (SQLAlchemy is optional). With tenancy enabled
`get_service("db")` returns a `TenantAwareDB` (see §10); never reach for its raw `.engine`.

### Cache

```python
await self.cache.set("key", {"data": "value"}, ttl=300)
value = await self.cache.get("key")   # None if absent
await self.cache.delete("key")
```

### Programmatic Scheduler (Advanced Use Cases)

```python
scheduler = self.get_service("scheduler")
# add_job / remove_job are SYNCHRONOUS (do not await them).
# Signature: add_job(func, trigger="cron", job_id=None, replace_existing=True, **trigger_args)
scheduler.add_job(self._my_func, trigger="interval", seconds=60, job_id="my_job")
scheduler.remove_job("my_job")
```

The service keeps the real callable in a module-level `_JOB_REGISTRY` and hands APScheduler a
picklable dispatcher, so passing a bound method to `SchedulerService.add_job()` is fine.
Duplicate runs across workers are prevented by a Redis lock (`xcore:sched:lock:<job_id>`).

### Repository Pattern

```python
from xcore.sdk import BaseAsyncRepository
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String

class Base(DeclarativeBase): pass

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String)

class ItemRepository(BaseAsyncRepository[Item]):
    def __init__(self):
        super().__init__(Item)

class Plugin(TrustedBase):
    async def on_load(self):
        self.repo = ItemRepository()

    async def handle(self, action, payload):
        async with self.get_service("db").session() as session:
            item = await self.repo.get_by_id(session, payload["id"])
            return ok(item={"id": item.id, "name": item.name})
```

### Redis Repository *(xcdk)*

```python
from xcore.sdk import BaseRedisRepository

class SessionRepo(BaseRedisRepository):
    prefix = "session"   # all keys: "session:<key>"

    async def create(self, token: str, data: dict, ttl: int = 3600):
        await self.set(token, data, ttl=ttl)

    async def fetch(self, token: str) -> dict | None:
        return await self.get(token)

class Plugin(TrustedBase):
    async def on_load(self):
        redis = self.get_service("redisAdapter")
        self.sessions = SessionRepo(redis)
```

---

## 6. Creating a Custom Service

There are **two ways** to add custom services to XCore.

---

### Method 1 — Extension via integration.yaml (Recommended, Code-Free Core Modification)

The simplest approach. Declare the service under `services.extensions` in `integration.yaml`.
XCore will instantiate it, call `init()`, and trigger `shutdown()` automatically.

```yaml
# integration.yaml
services:
  extensions:
    email:
      module: myapp.services.email:EmailService
      config:
        smtp_host: smtp.gmail.com
        smtp_port: 587
        username: "${SMTP_USER}"
        password: "${SMTP_PASS}"

    stripe:
      module: myapp.services.payments:StripeService
      config:
        api_key: "${STRIPE_SECRET_KEY}"
        webhook_secret: "${STRIPE_WEBHOOK_SECRET}"
```

**The service must inherit from `BaseService`:**

```python
# myapp/services/email.py
from xcore.services.base import BaseService, ServiceStatus

class EmailService(BaseService):
    name = "email"

    def __init__(self, config: dict):
        super().__init__()
        self.smtp_host = config["smtp_host"]
        self.smtp_port = config["smtp_port"]
        self.username  = config["username"]
        self.password  = config["password"]
        self._client   = None

    async def init(self) -> None:
        # Connection / warmup
        import aiosmtplib
        self._client = aiosmtplib.SMTP(
            hostname=self.smtp_host,
            port=self.smtp_port,
            use_tls=True,
        )
        await self._client.connect()
        await self._client.login(self.username, self.password)
        self._status = ServiceStatus.READY

    async def shutdown(self) -> None:
        if self._client:
            await self._client.quit()
        self._status = ServiceStatus.STOPPED

    async def health_check(self) -> tuple[bool, str]:
        try:
            await self._client.noop()
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def status(self) -> dict:
        return {"name": self.name, "status": self._status.value}

    # Custom business methods
    async def send(self, to: str, subject: str, body: str) -> None:
        from email.mime.text import MIMEText
        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"] = self.username
        msg["To"] = to
        await self._client.sendmail(self.username, [to], msg.as_string())
```

**Accessing the Service from a Plugin:**

```python
class Plugin(TrustedBase):
    async def on_load(self):
        # Access name pattern is "ext.<declared_name>"
        self.email = self.get_service("ext.email")

    async def handle(self, action, payload):
        if action == "send_welcome":
            await self.email.send(
                to=payload["email"],
                subject="Welcome!",
                body="<h1>Hello there!</h1>",
            )
            return ok()
```

---

### Method 2 — Custom ServiceProvider (DI Container Injection)

For absolute control over initialization or to wire up multiple services simultaneously.

```python
# myapp/providers.py
from xcore.services.base import BaseService, BaseServiceProvider, ServiceStatus
from xcore.services.container import ServiceContainer

class MyCustomService(BaseService):
    name = "my_custom"

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key
        self._client = None

    async def init(self) -> None:
        from mylib import Client
        self._client = Client(api_key=self.api_key)
        await self._client.connect()
        self._status = ServiceStatus.READY

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()
        self._status = ServiceStatus.STOPPED

    async def health_check(self) -> tuple[bool, str]:
        try:
            await self._client.ping()
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def status(self) -> dict:
        return {"name": self.name, "status": self._status.value}


class MyCustomServiceProvider(BaseServiceProvider):
    async def init(self, container: ServiceContainer) -> None:
        api_key = container._config.extensions.get("my_custom", {}).get("config", {}).get("api_key")
        svc = MyCustomService(api_key=api_key)
        await svc.init()
        # Register under name "my_custom" in the container
        container.register_service("my_custom", svc)
```

**Wiring up the Provider during Boot:**

```python
# In the app entry point (prior to container.init())
from myapp.providers import MyCustomServiceProvider
container.add_provider(MyCustomServiceProvider())
await container.init()
```

**Or Register Directly (No Provider):**

```python
# Best for lightweight utilities lacking dynamic lifecycles
container.register_service("feature_flags", FeatureFlagClient(url="..."))
```

---

### Method 3 — Registration from a Plugin (Inter-Plugin Shared Service)

A plugin can register a service directly into the shared container for other plugins to consume.

```python
class Plugin(TrustedBase):
    async def on_load(self):
        # Expose its custom HTTP client to the container
        from mylib import HttpClient
        client = HttpClient(base_url="https://api.example.com")
        await client.connect()

        # Save to shared services
        self.ctx.services["http_client"] = client

    async def on_unload(self):
        client = self.ctx.services.pop("http_client", None)
        if client:
            await client.close()
```

**Consuming from another Plugin:**

```python
class OtherPlugin(TrustedBase):
    async def on_load(self):
        # Ensure the provider is loaded beforehand via `requires`
        self.http = self.get_service("http_client")
```

```yaml
# plugin.yaml of OtherPlugin
requires:
  - my-http-provider-plugin  # guarantees load order
```

---

### BaseService Interface Contract Summary

```python
from xcore.services.base import BaseService, ServiceStatus

class MyService(BaseService):
    name = "my_service"   # label shown in status lists and logs

    def __init__(self, config: dict):
        super().__init__()  # sets self._status = ServiceStatus.UNINITIALIZED
        # store configuration

    async def init(self) -> None:
        # Connect, warm up connections, perform handshakes
        # Obligatory: update self._status to ServiceStatus.READY upon completion
        self._status = ServiceStatus.READY

    async def shutdown(self) -> None:
        # Gracefully sever connections
        self._status = ServiceStatus.STOPPED

    async def health_check(self) -> tuple[bool, str]:
        # Return (True, "ok") or (False, "error description")
        return True, "ok"

    def status(self) -> dict:
        # Dictionary rendered by /status and in logs
        return {"name": self.name, "status": self._status.value}

# ServiceStatus State Machine values:
# UNINITIALIZED → INITIALIZING → READY → DEGRADED → STOPPED → FAILED
```

---

### Service Configuration inside integration.yaml

```yaml
services:
  # Multiple Databases — each entry defines a distinct adapter
  databases:
    default:
      type: postgresql+aio       # sqlite | postgresql | mysql | sqlite+aio | postgresql+aio | mongodb | redis
      url: "${DATABASE_URL}"
      pool_size: 10
      max_overflow: 20
      pool_pre_ping: true
      pool_recycle: 1800         # recycle before DB severs (less than MySQL wait_timeout)
      pool_timeout: 30
      pool_reset_on_return: rollback   # "rollback" | "commit" | "none"
      echo: false
      connect_args:
        command_timeout: 30      # asyncpg
      isolation_level: null      # "READ COMMITTED" | "SERIALIZABLE" | null

    analytics:
      type: postgresql+aio
      url: "${ANALYTICS_DB_URL}"

    mongo:
      type: mongodb
      url: "${MONGO_URL}"
      database: myapp

    redis_store:
      type: redis
      url: "${REDIS_URL}"

  cache:
    backend: redis               # "memory" | "redis"
    url: "${REDIS_URL}"
    ttl: 300                     # default TTL in seconds
    max_size: 1000               # max size for memory backend

  scheduler:
    enabled: true
    backend: redis               # "memory" | "redis"
    url: "${REDIS_URL}"
    timezone: Europe/Paris

  xworker:
    enabled: false
    broker_url: "${REDIS_URL}"
    result_backend: "${REDIS_URL}"
    queues: ["default", "high", "low"]
    concurrency: 4

  extensions:
    email:
      module: myapp.services.email:EmailService
      config:
        smtp_host: smtp.gmail.com
        smtp_port: 587
        username: "${SMTP_USER}"
        password: "${SMTP_PASS}"
    stripe:
      module: myapp.services.payments:StripeService
      config:
        api_key: "${STRIPE_SECRET_KEY}"
```

---

### Querying Multiple Databases in a Plugin

```yaml
# plugin.yaml — explicit database resource wildcard
permissions:
  - resource: "db.*"
    actions: ["read", "write"]
    effect: allow
```

```python
async def on_load(self):
    # Primary DB (first declared adapter or key "default")
    self.db       = self.get_service("db")

    # Named Database Adapters
    self.analytics = self.get_service("analytics")
    self.mongo     = self.get_service("mongo")
    self.redis     = self.get_service("redis_store")
```

---

## 7. Observability — Core APIs

### Logger

```python
from xcore.kernel.observability import get_logger
logger = get_logger("xcore.my_module")   # structured logger namespace required

self.logger.info("action performed", details="value", id=42)
self.logger.warning("attention needed", plugin="my_plugin")
self.logger.error("action failed", err=str(e))
self.logger.debug("debug message", payload=payload)

# FORBIDDEN
import logging; logging.getLogger("my_plugin")  # unstuctured, bypasses kernel logging hooks
```

### Metrics

```python
self.metrics.counter("calls_total", labels={"plugin": "shop"}).inc()
self.metrics.gauge("queue_size").set(42)
self.metrics.gauge("active_connections").inc()
self.metrics.histogram("duration_ms").observe(123.4)
```

### Tracing

```python
with self.tracer.span("operation") as span:
    span.set_attribute("user_id", 42)
    result = await self._do_work()
    if error:
        span.set_status("error")
        span.set_attribute("error.message", str(e))
```

---

## 8. Inter-Plugin Calls

```python
result = await self.call_plugin("billing-plugin", "charge", {
    "amount": 100,
    "currency": "EUR",
    "user_id": 42,
})

if result.get("status") == "ok":
    charge_id = result["charge_id"]
else:
    return error(result.get("msg", "billing failed"), "billing_error")
```

- `call_plugin` is a `TrustedBase` method. It forwards the **current tenant** and the **already
  resolved principal** to the nested call (no new contact with the `AuthBackend`).
- It is **not available to sandboxed plugins**: their `ctx.caller` is `None` and it raises
  `RuntimeError`. A sandboxed plugin can be *called*, it cannot call others. Design APIs around that
  (the caller sends the data in the payload, the sandboxed plugin answers).
- Calls pass through the full middleware chain (§15), including the `allowed_callers` check when
  IPC enforcement is on.

---

## 9. Permissions

There are **two independent systems**:

1. `PermissionEngine` (this section): what a *plugin* may do on a *resource*, from the manifest.
2. `@action(permissions=...)` / `RBACChecker` (§4, §11): what a *user* (the principal resolved by the
   `AuthBackend`) may do. They do not share data or configuration.

```yaml
permissions:
  - resource: "db.*"
    actions: ["read", "write"]
    effect: allow
  - resource: "db.users"
    actions: ["delete"]
    effect: deny
  - resource: "events.*"
    actions: ["emit", "subscribe"]
    effect: allow
  - resource: "plugins.billing-plugin"
    actions: ["call"]
    effect: allow
```

---

## 10. Multi-tenancy

```yaml
tenancy:
  enabled: false          # default: OFF. Everything below is inert until true
  header: X-Tenant-ID     # default
  subdomain: false        # true: acme.myapp.com -> "acme" (needs 3+ host labels)
  default_tenant: default
  isolate_cache: true     # prefixes cache keys with the tenant
  isolate_db: true        # PostgreSQL only: SET search_path per transaction
  isolate_scheduler: false
  enforce_ipc: true       # allowed_callers check on IPC, deny-by-default (see §2)
```

- `TenantMiddleware` reads the header (else the subdomain, else `default_tenant`) and puts it in
  `request.state.tenant_id`. The value is client-supplied and unauthenticated: it must match
  `[a-zA-Z0-9_-]{1,63}` and not be `public`, `information_schema` or `pg_*`, otherwise **400**.
- The current tenant lives in the `_current_tenant_id` ContextVar (`kernel/tenancy/services.py`),
  set per call in `supervisor._dispatch`. Services are wrapped **once** at `on_load`
  (`TenantAwareDB`, `TenantAwareCache`, `TenantAwareScheduler`) and read the ContextVar on each
  operation: **no tenant-aware logic is needed inside plugins**, and nothing may be mutated per request.

**Limits to know:**
- **DB isolation is PostgreSQL-only.** `TenantAwareDB` isolates through `search_path`; on SQLite,
  MySQL and MongoDB it is silently skipped (debug log). The tenant's schema must already exist,
  otherwise PostgreSQL falls back to `public`.
- `TenantAwareDB.engine` and `__getattr__` expose the raw, non-isolated database. Never use them
  for tenant data.
- With tenancy on and a non-PostgreSQL adapter nothing warns at boot: a cross-tenant leak will not
  show up in SQLite tests. Test tenant isolation on PostgreSQL, or filter on an explicit `tenant_id` column.

---

## 11. Auth

XCore **stores no users and no roles**. It defines a protocol (`kernel/api/auth.py`); an **auth
plugin** implements it and registers it in `on_load`. Only one backend is active at a time.

```python
from xcore.sdk import AuthPayload, register_auth_backend, unregister_auth_backend

class JWTBackend:                      # structural Protocol: no base class to inherit
    async def extract_token(self, request) -> str | None:
        return request.headers.get("authorization", "").removeprefix("Bearer ") or None

    async def decode_token(self, token: str) -> AuthPayload | None:
        try:
            claims = jwt.decode(token, SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return None
        return {"sub": claims["sub"],                        # required
                "roles": claims.get("roles", []),            # optional
                "permissions": claims.get("permissions", []),
                "user": {"email": claims.get("email")}}      # optional free-form dict

    async def has_permission(self, payload: AuthPayload, permission: str) -> bool:
        return permission in payload.get("permissions", [])

class Plugin(TrustedBase):
    async def on_load(self):
        register_auth_backend(JWTBackend())    # one argument, no name
    async def on_unload(self):
        unregister_auth_backend()
```

`AuthPayload` is a `TypedDict` (`sub` required; `roles`, `permissions`, `user` optional).
`register_auth_backend` raises `TypeError` unless the object provides all three methods.

**Where the resolved user ("principal") is used:**
- **HTTP routes**: FastAPI dependencies `require_role(*roles)`, `require_permission(*perms)` or
  `RBACChecker`. With no backend registered they answer **503**, or skip the check when built with
  `strict=False` (dev mode); a missing role/permission answers **403**. `get_current_user` and
  `get_user_session_id` (from `xcore.kernel.api`) are dependencies returning the user / session.
- **IPC calls** (`POST …/ipc/{plugin}/{action}` and `call_plugin`): the router extracts the token with
  `AuthBackend.extract_token`; `AuthResolverMiddleware` decodes it once (or reuses the principal
  forwarded by `call_plugin`); `ActionPermissionMiddleware` enforces `@action(permissions=…,
  permission_groups=…)` against `principal["roles"] | principal["permissions"]`.
- **Inside a plugin**: `from xcore.kernel.api.auth import get_current_principal` returns the
  principal of the call in progress (`None` if no token or no backend). It is a ContextVar set in
  `supervisor._dispatch`, like the tenant. `resolve_principal_from_request(request)` is the
  best-effort resolver for code that holds a raw request; it never raises.

---

## 12. Execution Modes Comparison

| | `trusted` | `sandboxed` | `ephemeral` |
|---|---|---|---|
| Process Context | In-process | Isolated Subprocess | In-process (pooled) |
| Service Access | Direct `get_service()` | JSON IPC bridge | Direct `get_service()` |
| Persist State | Yes | Yes (subprocess) | **No** (stateless) |
| Security | Medium | High | Medium |
| Performance | High | Lower (IPC overhead) | High (warm pool) |
| Target Use Cases | Core business domains | Untrusted/3rd-party code | Stateless VFs |

Sandboxed constraints (verified in code):
- Started as a script: `python xcore/kernel/sandbox/worker.py <plugin_dir>`, one subprocess per plugin
  (~54 MB RSS each on `main`, see §18), recycled with `RECYCLE_EXIT_CODE` when its CPU budget is spent.
- No HTTP routes, no `call_plugin` (§8). The only way in is `handle(action, payload)` over the JSON IPC bridge.
- Imports are restricted by the `allowed_imports` AST whitelist (the scan also blocks function
  reflection attributes). Trusted plugins are not limited by it.
- CPU budget: `RLIMIT_CPU` is cumulative. The soft limit is re-armed per request in `worker.py`
  (`_arm_cpu_budget`); the hard limit is the worker's lifetime cap (`max_cpu_lifetime_seconds`).

---

## 13. Critical Gotchas

```python
# FORBIDDEN — standard logger, lacks structured formats
import logging; logging.getLogger("my_plugin")

# FORBIDDEN — multi-tenant race condition
self.current_tenant = tenant_id

# FORBIDDEN — handing a bound method straight to APScheduler (not picklable for RedisJobStore)
scheduler._scheduler.add_job(self.my_method, ...)

# CORRECT — go through SchedulerService.add_job() (sync) or @cron/@interval (xcdk):
# the service stores the callable in _JOB_REGISTRY and schedules a picklable dispatcher
scheduler.add_job(self.my_method, trigger="interval", seconds=60, job_id="my_job")
@cron("0 3 * * *")
async def nightly(self): ...

# FORBIDDEN — raw asyncio task: it is not tracked, so it survives unload and pins the old plugin
asyncio.create_task(self._loop())
# CORRECT — tracked by the kernel, cancelled AND awaited at unload/reload
self.ctx.spawn_task(self._loop(), name="loop")

# CORRECT — patch the original SOURCE module inside test suites
@patch("xcore.services.container.ServiceContainer.get")  # ✓
@patch("xcore.ServiceContainer.get")                      # ✗

# CORRECT — utilize structured logs
from xcore.kernel.observability import get_logger
logger = get_logger("xcore.my_plugin")
logger.info("action performed", user_id=42)  # kwargs, never f-strings
```

**Kernel invariants (break these and reload/load of *every* plugin can fail):**
- Never mutate `instance.ctx.services` per request (race condition). Wrap once at `on_load`; the tenant
  changes per request through the ContextVar.
- `LifecycleManager._services` is the **shared dict of the `ServiceContainer`**: only write services
  the plugin *exports*. `propagate_services()` ignores objects identical to the injected ones
  (`_injected_services`); never reintroduce a raw `update()` of injected services (GC proxy, tenant
  wrappers) into it.
- `PluginContext.get_service()` serves core services (`PluginRegistry.is_core_service`) from the plugin's
  own `ctx.services`, not from the registry (which holds the raw objects: job tracking and tenant
  isolation would be bypassed).
- Mount/unmount plugin routes only through `Xcore._mount_plugin_router` / `_unmount_plugin_router`
  (they track the route objects; `app.routes` has no setter and FastAPI >= 0.14x `_IncludedRouter`
  has no `.path`).
- Plugin discovery is a **directory scan** (`loader.py`: `plugin_dir.iterdir()` + `plugin.yaml`). There
  is no entry-point mechanism: a pip-installed package is not loaded as a plugin by itself.
- After an unload, the log `plugin instance still referenced after unload` means something still
  holds the instance (raw task, callback registered outside `ctx`, …). A forced full `gc.collect()`
  runs ~1 s after unload/reload, synchronously in the event loop (28-37 ms measured on a small heap).

**Behaviours that surprise:**
- `EventBus.emit` never raises and cannot veto (see §4, `@on_event`).
- DB tenant isolation is PostgreSQL-only (§10).
- `@schema` does not validate unless `type_response` is `"dict"`/`"model"` (§4).
- `from xcore.sdk import cron/interval/health_check/cached/...` fails without the `xcdk` extra (§4).
- `plugin_prefix` defaults to `/plugin` (singular).
- One shared API key (`X-Plugin-Key`) protects **every** `…/ipc/…` route, lifecycle ones included (§16).

---

## 14. Testing Patterns

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.fetch_one.return_value = {"id": 1, "name": "test"}
    db.fetch_all.return_value = [{"id": 1}, {"id": 2}]
    db.execute.return_value = None
    return db

@pytest.fixture
async def plugin(mock_db):
    from app.plugins.my_plugin.src.main import Plugin
    p = Plugin()
    p._ctx = MagicMock()
    p._ctx.services = {"db": mock_db, "cache": AsyncMock()}
    p._ctx.name = "my-plugin"
    p._ctx.config = MagicMock()
    p._ctx.events = AsyncMock()
    p._ctx.tracer = MagicMock()
    p._ctx.metrics = MagicMock()
    p._ctx.health = MagicMock()
    await p._inject_context(p._ctx)
    await p.on_load()
    return p

async def test_create_user(plugin, mock_db):
    result = await plugin.handle("create_user", {"email": "alice@test.com"})
    assert result["status"] == "ok"
    mock_db.execute.assert_called_once()

async def test_missing_field(plugin):
    result = await plugin.handle("create_user", {})
    assert result["status"] == "error"
    assert result.get("code") == "missing_field"
```

Environment variables required for testing:
```bash
DATABASE_URL=sqlite:///./test.db
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=test-secret-key
```

`asyncio_mode = auto` → do not add `@pytest.mark.asyncio` manually. Target coverage `fail_under = 80` (branch).

---

## 15. HTTP Flow → Plugin

```
HTTP Request  POST {plugin_prefix}/ipc/{plugin}/{action}   (header X-Plugin-Key, body {"payload": {...}})
  └─ TenantMiddleware        (X-Tenant-ID / subdomain → request.state.tenant_id, 400 if invalid)
       └─ Router             (checks the API key; token → AuthBackend.extract_token)
            └─ supervisor.call(name, action, payload, tenant_id=…, principal=…)
                 └─ IPCAuthMiddleware        (first; allowed_callers when tenancy.enforce_ipc)
                      └─ TracingMiddleware         (span + metrics)
                           └─ RateLimitMiddleware
                                └─ AuthResolverMiddleware      (principal via AuthBackend.decode_token)
                                     └─ ActionPermissionMiddleware   (@action permissions / groups)
                                          └─ PermissionMiddleware        (PermissionEngine, plugin → resource)
                                               └─ RetryMiddleware        (runtime.retry)
                                                    └─ _dispatch: tenant + principal ContextVars set
                                                         └─ handler.call(action, payload)
                                                              └─ Plugin.handle(action, payload)
```

Plugin **HTTP routes** (`get_router()` / `@route`) are different: they are FastAPI routes mounted at
`{plugin_prefix}/{plugin_name}` and served directly by FastAPI, without `supervisor.call()` or this
chain. They only see `TenantMiddleware` and whatever dependencies the route declares (`RBACChecker`…).

---

## 16. Out-of-the-Box System Endpoints

All of these live under `{plugin_prefix}/ipc` (`plugin_prefix` defaults to `/plugin`) and require the
header `X-Plugin-Key`. The key is `app.secret_key`, compared in constant time after hashing with
`app.server_key`.

| Endpoint (under `{plugin_prefix}/ipc`) | Description |
|---|---|
| `GET /registry` | Every plugin on disk with its persisted `enabled` flag and live state |
| `GET /status` | State of the loaded plugins |
| `GET /audit?plugin=&limit=` | IPC call journal (who called what) + per-plugin stats |
| `GET /events?event=&limit=` | Recent EventBus / HookManager activity |
| `POST /{plugin}/enable`, `/disable`, `/reload`, `/load` | Lifecycle (`disable` takes an optional `{"reason": …}`) |
| `DELETE /{plugin}/unload` | Unload |
| `POST /{plugin}/{action}` body `{"payload": {…}}` | Invoke an action. Answer: `{status, plugin, action, result}`; 404 when the plugin is unknown |
| `GET /health` | Health report |
| `GET /metrics` | JSON metrics snapshot |

Also: `GET /metrics` at the **app root** (Prometheus text format, same API key) is mounted separately,
only when `observability.metrics.backend: prometheus` and `prometheus-client` is installed. There
are no `/health/live` or `/health/ready` routes.

The single shared key grants lifecycle control (`enable/disable/reload/load/unload`) together with plain
action calls. Do not hand it to an untrusted client as-is.

---

## 17. Quick Imports Index

```python
# Base
from xcore.sdk import TrustedBase, ok, error

# ── Always available ─────────────────────────────────────────────────────
# Dispatch
from xcore.sdk import action, AutoDispatchMixin

# HTTP
from xcore.sdk import route, RoutedPlugin

# Schemas
from xcore.sdk import schema, validate_payload

# Events (types only; the decorators are xcdk)
from xcore.sdk import Event, HookResult

# Observability
from xcore.kernel.observability import get_logger

# Guards
from xcore.sdk import require_service, trusted, sandboxed

# RBAC (FastAPI dependencies)
from xcore.sdk import require_permission, require_role, RBACChecker
from xcore.kernel.api import get_current_user, get_user_session_id

# Auth
from xcore.sdk import AuthBackend, AuthPayload, register_auth_backend, unregister_auth_backend
from xcore.kernel.api.auth import get_current_principal, resolve_principal_from_request

# SQL repositories (needs the `db` extra; resolved lazily)
from xcore.sdk import BaseAsyncRepository, BaseSyncRepository

# ── Only with the optional xcdk package: pip install "XCoreRuntime[sdk]" ─
from xcore.sdk import AutoMixin
from xcore.sdk import cron, interval, ScheduledMixin           # scheduler
from xcore.sdk import on_event, on_hook, EventMixin, HookMixin # events
from xcore.sdk import health_check, traced, counted, timed, ObservabilityMixin
from xcore.sdk import cached, invalidate                       # cache
from xcore.sdk import BaseRedisRepository, BaseMongoRepository

# Custom Services
from xcore.services.base import BaseService, BaseServiceProvider, ServiceStatus
from xcore.services.container import ServiceContainer

# Execution Modes
from xcore.sdk import ExecutionMode  # trusted, sandboxed, ephemeral

# Errors
from xcore.sdk import PermissionDenied
```

---

## 18. Performance Profile and Tooling

`scripts/profile_runtime.py` (stdlib only: `cProfile`, `tracemalloc`) complements `scripts/benchmarks.py`.

```bash
python scripts/profile_runtime.py calls                    # hot-path latency (best of 7 rounds) + cProfile
python scripts/profile_runtime.py calls --tracing null     # isolate the OTel SDK cost (no-op exporter)
python scripts/profile_runtime.py calls --mode sandboxed   # IPC round trip
python scripts/profile_runtime.py boot --plugins 50        # cold start vs number of plugins
python scripts/profile_runtime.py memory --n 10000 --cap spans,ipc,perm   # growth per call, bisect containers
python scripts/profile_runtime.py reload                   # reload latency, memory per reload, GC pause
python scripts/profile_runtime.py imports                  # cold import cost of xcore and its deps
python scripts/profile_runtime.py workers --plugins 4      # RSS of live sandbox workers (Linux)
```

Rules for reading results: machines are noisy, trust the **best round**; `cProfile` inflates tight
loops a lot (a 0.5 ms reload shows as 28 ms under it), use it for the shape only. `scripts/benchmarks.py`
is timing-only; its `concurrent_*` p95/p99 are synthetic (`mean × 1.3` / `× 1.5`) and its
`logging.enabled: false` key does not exist (`LoggingConfig` has no `enabled`, INFO logs still print).

Reference numbers (Python 3.14, noisy desktop, October 2026; re-measure before quoting):

| What | Value |
|---|---|
| `plugins.call()` ping, trusted, observability off | ~40-50 µs p50 |
| + DEBUG logging / Prometheus metrics | +1-2 µs / +5 µs |
| + OpenTelemetry tracing, no-op exporter | roughly +30 to +60 µs |
| + OpenTelemetry console exporter (synchronous, dev only) | about +140 µs |
| Sandboxed call (IPC round trip) | ~130 µs p50 |
| Trusted boot | ~50 ms fixed + ~2.7 ms per plugin |
| Sandboxed worker | ~54 MB RSS each, 200+ ms to start (CPU-bound on `import xcore`) |
| Trusted reload | 0.3-0.6 ms |
| `import xcore` | ~335 ms, ~59 MB (was ~585 ms / 83 MB before #307) |

---

## 19. Known Issues (open)

- **#305** — `Tracer._spans` is an unbounded list appended on every plugin call, even with tracing
  disabled (~560 B per call, about 4.9 GB/day at 100 req/s). The other audit containers are bounded
  `deque(maxlen=…)`: `Supervisor._ipc_audit` and `PermissionEngine._audit_log` (100 000 each), the
  EventBus emission log (10 000). Same issue: manifests are parsed with pure-Python `yaml.safe_load`
  although libyaml is installed (`CSafeLoader` is ~9x faster).
- **#306** — every sandbox worker re-imports the whole `xcore` package (fastapi, celery when installed,
  OpenTelemetry): ~335 ms and ~55 MB per worker even after #307.
- **Tenancy** — non-PostgreSQL adapters are not isolated and nothing warns at boot (debug log only).
- **Plugin discovery** — directory scan only; no entry-point mechanism for pip-installed plugins.
