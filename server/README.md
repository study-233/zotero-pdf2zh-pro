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
