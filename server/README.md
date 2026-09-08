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
translation are still unresolved; these results are not automatically imported
into Zotero.

`POST /tasks/{taskId}/repair` accepts incomplete, completed, and cancelled tasks
and returns the snapshot for the new attempt. Active tasks return HTTP 409.
Repair preserves validated translations and requests the remaining paragraphs,
using QPS 2 and concurrency 4 by default. Pure URL footnotes are preserved without
translation; ordinary body text containing a URL still requires translation.

Paragraph checkpoints are stored in `paragraph-recovery.json` inside the task
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
