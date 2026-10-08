# zotero-pdf2zh-pro

Local Python server for the Zotero `zotero-pdf2zh-pro` plugin.

```bash
uv tool install --python 3.13 zotero-pdf2zh-pro
zotero-pdf2zh-pro
```

The default service URL is `http://127.0.0.1:8890`.

The server verifies HTTPS using the operating system's trusted certificates,
including trusted proxy CAs on macOS and Windows. Certificate and hostname
verification remain enabled. For Docker, install any required private CA in
the container's trust store; the host's trust store is not inherited.

## Reading memory and short text (since v1.8.0)

`POST /translation-lookup` accepts `documentFingerprint` (the original PDF SHA-256),
`text`, optional one-based `page`, `side` (`source` by default, or `translation`),
and optional `targetLang`. It returns `matched: false`, or `matched: true` with
`matchType` (`exact` / `contained`), `source`, `translation`, `page`, `paragraphId`,
`targetLang`, and `fromTranslationMemory: true`. A contained match returns the whole
paragraph, not word alignment. The route never constructs or calls a provider.

`translation-memory.sqlite3` lives directly in the configured data directory.
Completed PDF outputs sync succeeded, nonempty, quality-approved-or-unrejected
paragraphs from their task checkpoint. Corrected text replaces earlier text.
Existing completed/incomplete task checkpoints are also read at startup; deleting
a task does not remove memory. Legacy placeholders that cannot be restored are replaced with `⟦原排版内容⟧`
and flagged with `formattingIncomplete: true`; their prose still matches without a provider call.
The recovery database remains exclusively the task retry checkpoint.

`POST /translate-text` accepts `text`, optional `context` (up to 12,000 characters),
`documentFingerprint`, one-based `page`, `source`, `target`, `mode`
(`lookup`, `translate`, `explain`), `service`, `llm_api`, and `glossaryEntries`.
The first two modes check memory and glossary before cached results or a provider.
Explicit `explain` requests may use a provider even on a memory hit; the real stored
paragraph pair supplies the context. No PDF task or font preparation is created.

Optional `selectionProvider` is `profile` (legacy default) or `bing`; optional
`memoryPolicy` is `paragraph` (legacy default) or `exact`. With `exact`, contained
memory does not stop translation; clients can retain it separately as folded reference.
The Reader explicitly sends both fields.

Bing requires no model configuration or key, and sends only selected text and
language pair to `https://www.bing.com/ttranslatev3` after obtaining session parameters
from `https://www.bing.com/translator`. It splits losslessly at
UTF-8 boundaries up to 1,000 bytes (within the web translator’s 1,000-character limit), prefers sentence/whitespace boundaries, and starts
at most one request per second across workers. The whole translation has a 45-second
deadline. Quota errors (`provider_quota`, 429), invalid/empty responses and timeouts
are not cached; partial translations never count as success. There is no automatic
fallback to the model. Free-service cache keys contain only selected text, language
and provider; no paper context or credentials are sent.

The profile text provider supports Codex and existing presets resolving to OpenAISettings,
including Chat Completions and Responses. A fixed API protocol needs no health-check
request; `auto` negotiates only when a model request is actually needed.
Responses contain `translation` or `explanation`, `provider`, `model` when available,
and `cached`. Errors have a fixed `code`: `empty_text`, `invalid_request`,
`invalid_config`, `unsupported_selection_provider`, `provider_timeout` (504),
`provider_error` / `empty_output` (502), or `selection_busy` (429).
Requests wait at most 45 seconds; an already-running HTTP operation may finish later,
with at most two worker requests active. Duplicate in-flight requests share work,
except individually cancellable Codex requests described below.
A 128-entry, one-hour memory cache fronts `selection-cache.sqlite3` in the server data directory.
Persistent translation/context results are capped at 20,000 least-recently-used entries; personal
word entries never expire. Only result data, digests and timestamps are stored, not credentials or
raw provider configuration. Model translation keys include context and output-affecting options;
context/explanation keys additionally include the document fingerprint.

Generating Codex profile requests may include a unique `requestId` containing
16–128 URL-safe letters, digits, underscores or hyphens. The plugin generates a
cryptographically random ID and sends `POST /cancel-text` with `{"requestId": "..."}`
when the selection closes, is replaced, or reaches its HTTP deadline. Cancellation
returns `{"status": "ok", "cancelled": true}` when marked; already finished requests
return `cancelled: false`. A cancellation that arrives before generation is retained
for up to 60 seconds (at most 128 recent IDs). Active and recently completed duplicate
IDs return `duplicate_request_id` (409); invalid or non-Codex IDs on generation return
`invalid_request_id` (400). Cancelled generation returns `selection_cancelled` (409).
These requests use separate turns, interrupt the matching Codex generation, and check
cancellation before saving results. Existing clients without request IDs remain compatible.

`mode: dictionary` returns `entry` (headword, senses, optional usage, aiGenerated), a plain-text
`translation`, model provenance, `createdAt`, `formatVersion` and `saved`. It requires a short
selection (at most 3 words / 100 characters). Its key preserves case and includes only the normalized
selection, language pair and schema/prompt version, allowing reuse across papers and models.
`mode: context` requires nonempty document fingerprint and surrounding context different from the
selection; it bypasses document memory and uses the current model. Both modes require `selectionProvider: profile`.

`allowGenerate: false` performs a read-only cache lookup, returning `status: miss` without invoking
any provider. `cachePolicy: refresh` bypasses saved results, memory and glossary; successful results
replace the corresponding cache entry, failures preserve the prior entry. Repeated concurrent
refreshes share work, and older results cannot overwrite newer refreshes or repopulate cleared caches.
`saved: false` indicates the result is usable but could not be persisted.

`POST /selection-capabilities` returns `selectionLearning: true`; the same capability is advertised
by `/health`. New clients verify it before using these fields. `POST /selection-cache/clear` clears
translation/context caches but preserves personal dictionary entries and document memory. Existing
request modes remain supported. Empty/invalid AI dictionary output is rejected as `invalid_output`;
missing usable context is `context_unavailable`.

Reader usage and manual verification: [user guide](../docs/user-guide.md#selection-translation).

## Model discovery

`POST /list-models` accepts `apiUrl`, optional `apiKey`, and optional
`apiProtocol` (`auto`, `chat_completions`, or `responses`). It strips a recognized
translation endpoint suffix and requests `<base>/models`, preserving custom
paths and never adding `/v1`. A successful response is
`{"status":"ok","models":["model-id"]}`; an empty list is valid.

Discovery uses a 15-second timeout and does not follow redirects, persist keys,
or echo upstream response bodies in errors. Errors return `status` and `message`
with HTTP 400 for invalid input, 502 for provider failures, or 504 for timeout.
Clients should allow manual model entry on failure. `/health` advertises
`supportsModelDiscovery: true`; existing translation endpoints are unchanged.

## Codex CLI provider

`/health.capabilities.codexCli: true` means this server includes the adapter; it does
not mean the CLI is installed, signed in, or permitted to use a particular model.
The first supported CLI version is `0.153.4`. Install and sign in separately as
the same operating-system user that runs the Python service. macOS and Windows
are the initial manual acceptance targets; Docker does not include Codex or a login.

Use `service: "codex"` with `llm_api.model` (default `gpt-6-luna`), optional
`llm_api.cliPath`, and optional `llm_api.reasoningEffort`. An omitted effort uses
the model default, while `"none"` is an explicit effort. The provider uses Standard
speed; API keys, API URLs, Responses options and OpenAI reasoning switches do not
configure this provider. Profiles keep an explicit model; unavailable models fail
with an actionable error instead of silently selecting a different one.

`POST /list-models` also accepts `{"service":"codex","cliPath":"codex"}`. It returns
`models` plus `modelDetails`, whose entries contain `id`, `displayName`,
`defaultReasoningEffort`, and `supportedReasoningEfforts` (an array of strings).
Catalog visibility is not proof of account entitlement. Discovery performs no
generation. `/validate-config` checks readiness without a model call when
`liveTest: false`; `liveTest: true` performs one short translation and reports its
result through the existing `liveTest` and `diagnostics` fields. `resolvedProtocol`
is null for Codex.

Codex profiles also accept `llm_api.proxyMode`: `inherit` (default), `manual`, or
`direct`. In manual mode `llm_api.proxyUrl` is an HTTP(S) proxy URL with no
credentials, path, query or fragment; for example `http://127.0.0.1:7897`.
Use the HTTP/Mixed listener of a local proxy application, not a SOCKS-only port.
`/list-models` accepts the same `proxyMode` and `proxyUrl` fields at the top level.
`/health.capabilities.codexProxy` and `/selection-capabilities.codexProxy` advertise
support, so clients can refuse unsupported explicit proxy choices on older servers.

Only the app-server child environment changes: manual mode sets both cases of
HTTP_PROXY, HTTPS_PROXY and ALL_PROXY, and replaces NO_PROXY with loopback hosts;
direct mode removes inherited proxy addresses and sets NO_PROXY to `*` in both
cases. Inherit mode preserves the service environment as it was at startup.
The parent service and system settings are untouched. Clients are shared only
within the same process, CLI path and proxy configuration; changing proxy starts
an independent connection without interrupting other configurations' requests.
Proxy changes do not invalidate translation caches. Invalid settings produce
`codex_invalid_proxy` with a fixed message that does not echo the supplied URL.

Each client lazily shares an owned stdio app-server, bounded to two active
generations. Requests use isolated temporary conversations. Only completed final
answers enter output validation and caching. Cancellation interrupts the turn;
an unresponsive owned process is recycled. The adapter never copies login tokens
or closes the shared client after a selection request. Context/dictionary output
uses the same structured validators as other profile providers, and selection
cache identity includes the model and reasoning effort.

Normal server shutdown and SIGTERM cancel selection work and close owned Codex
clients before exiting. On Windows each app-server belongs to a kill-on-close
Job Object, so forced termination of the Python service also stops its owned
Codex process tree.

Codex selection failures return a safe `code` and fixed local `message`, including `codex_not_installed`,
`codex_incompatible`, `codex_not_logged_in`, `codex_model_unavailable`,
`codex_invalid_reasoning`, `codex_isolation_failed`, `codex_timeout`,
`codex_process_exited`, `codex_protocol_error`, `codex_quota_exhausted`, and
`codex_request_failed`. Full-document/configuration errors contain a safe
explanation. Ambiguous failures are not automatically replayed. Token usage is
reported when supplied by the CLI and otherwise remains unknown.

## Translation protocols and request options

The optional `llm_api.apiProtocol` field accepts `auto`, `chat_completions`, or
`responses`; `llm_api.requestOptions` accepts a JSON object of extra request
parameters. `/health` advertises `supportedApiProtocols`, and `/validate-config`
returns `resolvedProtocol`. Older servers support the Chat Completions fallback;
Responses and extra request options require a compatible server update.

New task metrics omit `cost`. Token and upstream cache values may be `null`, with
`availability` indicating `unavailable`, `partial`, or `complete`. Legacy cost
fields are ignored when reading historical records.

## Translation completeness and repair

Task details and SSE events expose `translationSummary`, `failedParagraphs`, and
`canRepair`. The `incomplete` status indicates that paragraphs requiring
translation are still unresolved. A terminal task with an existing PDF exposes
`canDownloadResult: true`; its PDF can be downloaded and is automatically imported
into Zotero with an incomplete label. Repair creates a new attachment and preserves
previous versions and annotations. Older servers without this field retain the
completed-only import behavior. Import retries do not trigger translation requests.

`POST /tasks/{taskId}/repair` accepts incomplete, completed, and cancelled tasks
and returns the snapshot for the new attempt. Active tasks return HTTP 409.
Repair preserves validated translations and requests the remaining paragraphs,
using QPS 2 and concurrency 4 by default. Pure URL footnotes are preserved without
translation; ordinary body text containing a URL still requires translation.

Paragraph checkpoints are stored in `paragraph-recovery.sqlite3` inside the task
directory, allowing repair after a server restart. Deleting a task also removes
its checkpoints. JSON structure, paragraph IDs, nonempty translations, and
placeholders are validated before translations are cached.

## Runtime and data paths

Run `zotero-pdf2zh-pro --help` for supported options: `--host`, `--port`,
`--log-level`, `--data-dir`, and `--log-file`. The default listener is
`127.0.0.1:8890`. `/health` reports the effective `workspace.path`, writability,
free space, versions, and task counts. Inspect that path before backing up data
or uninstalling; its location depends on the installation method.

The corresponding environment variables are `PDF2ZH_HOST`, `PDF2ZH_PORT`,
`PDF2ZH_LOG_LEVEL`, `PDF2ZH_DATA_DIR`, and `PDF2ZH_LOG_FILE`. By default, task data
is stored in `translates` alongside the installed server module. The optional
log file rotates at 10 MiB with three backups.

For the Windows and macOS installation walkthrough, see the [main tutorial](../README.md).

## On-demand glossary packs

The server manages optional English-to-Simplified-Chinese glossary packs under
`<effective-data-dir>/glossaries/`. Installation packages contain catalog metadata
only. Downloads and update checks are explicit actions from Zotero preferences;
installed packs work offline. Each translation task stores the resolved terms and
source versions, so retries and repairs survive pack updates or removal.

`/health` advertises `capabilities.glossaryPacks`. `GET /glossaries` is local-only;
`POST /glossaries/check-updates` refreshes the catalog. Download, cancel and remove
operations use `/glossaries/<id>/download`, `/glossaries/<id>/cancel`, and
`DELETE /glossaries/<id>`. Translation requests may include `glossaryPacks` with
`id`, `version`, and `sha256` alongside custom `glossaryEntries`.

See [glossary downloads](../docs/glossary-downloads.md) for storage, API and merge
rules, and [glossary sources](../docs/glossary-sources.md) for attribution.

## Task diagnostics and bounded cancellation

`/health.capabilities` advertises `diagnosticsExport`, `boundedCancellation`, and `detailedTaskProgress`. `GET /diagnostics` and `GET /tasks/{taskId}/diagnostics` return versioned, size-bounded, allowlisted JSON; they never export request payloads, paper content, keys or raw historical logs. Structured rotating logs live under `<data-dir>/diagnostics/`. The plugin combines this snapshot with its own records into a local ZIP.

Each attempt runs in a spawn process, contained by a POSIX process group or Windows Job Object. Cancellation escalates at 10/12 seconds and reports cleanup failure at 15 seconds rather than waiting indefinitely. A failed cleanup pauses the queue. Stalls alone only trigger diagnostics, never automatic cancellation. See [the owning specification](../docs/task-diagnostics-and-cancellation.md) for fields, retention, recovery and acceptance.


## Provider audit and configuration schema v2

The plugin exposes 19 providers. `openaicompatible`, `tencentmechinetranslation`,
and `dify` are removed. Explicit unknown or removed service IDs return a
validation error; they cannot select the free default engine. Omitting `service`
on legacy PDF/validation requests still selects `siliconflowfree`.

`POST /list-models` accepts `service` (`openai` by default), `apiUrl`, `apiKey`,
and `apiProtocol`. Supported HTTP catalogs: OpenAI, DeepSeek, Gemini, Grok, Groq,
and SiliconFlow. SiliconFlow requests include `sub_type=chat`. Provider URLs
are used only when a named preset omits the URL; explicit custom URLs are kept.
Codex discovery remains an app-server operation.

Azure Translator accepts `llm_api.azureRegion`: absent retains `chinaeast2`,
empty omits the region header. SDK 2.0 handles global endpoints; the documented
sovereign endpoints retain the v3 wire format. Azure OpenAI uses `/openai/v1`
and a deployment name, with no forced sampling parameters. An explicit
`extraData.azure_openai_api_version` retains the legacy Azure client.

See the [dated audit](../docs/provider-audit-2026-10-03.md) for official sources,
profile and backup removal rules, offline contracts, and live acceptance steps.
