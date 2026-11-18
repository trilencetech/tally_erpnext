# TallyBridge – Tally connection (context for Claude Code)

This project talks to clients' **TallyPrime** (port 9000, on their Windows PCs) through **TallyBridge**:
a relay on our CloudClusters Node server plus an agent exe on each client PC.
Use this file as the source of truth when writing code that reads from or posts to Tally.

```
Our app ──HTTPS──► Relay (nodejsclusters-210330-0.cloudclusters.net) ◄══WSS (outbound)══ TallyBridgeAgent.exe ──► TallyPrime :9000
```

- The client PC connects **out** to the relay. We never connect into the client network.
- The relay forwards the request body **unchanged** to `http://127.0.0.1:9000` on that PC and returns Tally's response **unchanged**.
- So any request that works in **TallyPrime API Explorer** or with a direct POST to port 9000 works here.

---

## 1. API contract

Base URL: `https://nodejsclusters-210330-0.cloudclusters.net`

### Call Tally
```
POST /tally/{agentId}
X-API-Key: <that client's key>
Content-Type: text/xml; charset=utf-8        (application/json for TallyPrime JSON API)
X-Timeout: 120                                (optional, seconds; default 120)

<ENVELOPE>...</ENVELOPE>
```
Response: Tally's raw body and status (normally `200`, `text/xml`).

### List online agents for a key
```
GET /tally-agents
X-API-Key: <key>
→ [{"agent_id","name","host","version","tally_alive","connected_since","last_seen","in_flight"}]
```
Times are epoch milliseconds. `tally_alive` is refreshed every 30 seconds by the agent.

### Health (no auth)
`GET /tally-health` → `{"ok":true}`

### Errors (JSON body)
| Status | Body | Meaning | Code should |
|---|---|---|---|
| 401 | `{"detail":"invalid API key"}` | Wrong or missing key | Fail, configuration error. Don't retry |
| 403 | `{"detail":"API key not allowed for agent '…'"}` | Key belongs to another client | Fail. Don't retry |
| 503 | `{"detail":"agent '…' is offline"}` | Client PC off or agent stopped | Retry later or queue. Show "Tally offline" |
| 503 | `{"error":"Tally is not reachable …"}` | Agent up, TallyPrime closed or port off | Retry later. Show "Open TallyPrime" |
| 504 | `{"error":"Tally request timed out"}` or `{"detail":"no response from agent"}` | Slow report | Raise `X-Timeout`, use smaller date ranges |
| 502 | `{"detail":"agent disconnected"}` | Connection dropped mid-request | Retry once (only for read/export requests) |
| 200 + `<LINEERROR>` / `<ERRORS>1` in XML | Tally-level error | Bad TDL, wrong company, validation | Parse and surface the message |

---

## 2. Clients (agent ids and key names)

Each client has an **agent id** (one per Tally PC, used in the URL) and **its own API key**, which is restricted to that client's agents.
Naming convention: agent `<client>-plateflow`, key name `<client>-trilence`.

| Client | Agent id | Key name | Key env var |
|---|---|---|---|
| Shreedr Corp | `shreejidr-plateflow` | `shreejidr-trilence` | `TALLY_KEY_SHREEJIDR` |
| (next client) | `<client>-plateflow` | `<client>-trilence` | `TALLY_KEY_<CLIENT>` |

**Rules for code:**
- Never hardcode keys. Read them from env/config or a per-client DB record (agent id + key stored together).
- Never send a key to the browser or front-end. Tally calls happen server-side only.
- Pick the key from the same client record as the agent id. Never use one client's key for another.

---

## 3. How to call it (reference code)

### Node.js
```js
const TALLY_BASE = process.env.TALLY_RELAY_URL || "https://nodejsclusters-210330-0.cloudclusters.net";

async function tallyRequest(agentId, apiKey, xml, { timeoutSec = 120 } = {}) {
  const res = await fetch(`${TALLY_BASE}/tally/${encodeURIComponent(agentId)}`, {
    method: "POST",
    headers: {
      "Content-Type": "text/xml; charset=utf-8",
      "X-API-Key": apiKey,
      "X-Timeout": String(timeoutSec),
    },
    body: xml,
    signal: AbortSignal.timeout((timeoutSec + 15) * 1000),
  });
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`Tally ${agentId}: ${res.status} ${text.slice(0, 300)}`);
    err.status = res.status;
    throw err;
  }
  return text; // raw Tally XML
}
```

### Python / Frappe
```python
import requests

def tally_request(agent_id: str, api_key: str, xml: str, timeout: int = 120) -> str:
    r = requests.post(
        f"https://nodejsclusters-210330-0.cloudclusters.net/tally/{agent_id}",
        data=xml.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "X-API-Key": api_key, "X-Timeout": str(timeout)},
        timeout=timeout + 15,
    )
    if r.status_code != 200:
        raise Exception(f"Tally {agent_id}: {r.status_code} {r.text[:300]}")
    return r.content.decode("utf-8", errors="replace")
```

### Same-server shortcut
Code running **on the CloudClusters server itself** can call `http://127.0.0.1:3001/tally/{agentId}` (same headers) and skip the trip out through HTTPS and nginx.

---

## 4. Tally request samples (XML)

Set `<SVCURRENTCOMPANY>` when the client has more than one company open. The name must match exactly.

**List companies**
```xml
<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>CL</ID></HEADER>
<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
<TDL><TDLMESSAGE><COLLECTION NAME="CL"><TYPE>Company</TYPE><FETCH>NAME</FETCH></COLLECTION></TDLMESSAGE></TDL>
</DESC></BODY></ENVELOPE>
```

**Ledgers with group and closing balance**
```xml
<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>Ledgers</ID></HEADER>
<BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVCURRENTCOMPANY>Company Name</SVCURRENTCOMPANY>
</STATICVARIABLES>
<TDL><TDLMESSAGE><COLLECTION NAME="Ledgers"><TYPE>Ledger</TYPE><FETCH>NAME,PARENT,CLOSINGBALANCE</FETCH></COLLECTION></TDLMESSAGE></TDL>
</DESC></BODY></ENVELOPE>
```

**Vouchers for a period**
```xml
<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>Vch</ID></HEADER>
<BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVFROMDATE>20260401</SVFROMDATE><SVTODATE>20260430</SVTODATE>
</STATICVARIABLES>
<TDL><TDLMESSAGE><COLLECTION NAME="Vch"><TYPE>Voucher</TYPE>
  <FETCH>DATE,VOUCHERTYPENAME,VOUCHERNUMBER,PARTYLEDGERNAME,AMOUNT,NARRATION</FETCH>
</COLLECTION></TDLMESSAGE></TDL>
</DESC></BODY></ENVELOPE>
```

**Import (create) a voucher**: `TALLYREQUEST` = `Import`, `TYPE` = `Data`, `ID` = `Vouchers`, with `<TALLYMESSAGE><VOUCHER ...>` in the body.
Check the response for `<CREATED>`, `<ALTERED>`, `<ERRORS>` and `<LINEERROR>`. More samples: TallyPrime API Explorer (https://tallysolutions.com/tallyprime-api-explorer/).

---

## 5. Behaviour to design around

- **One request at a time per PC.** The agent queues requests (`max_parallel = 1`), because Tally handles them one by one. Don't fire many calls in parallel to one client. Batch the work, or run it in a single worker per client.
- **Large exports are slow.** Split by month and raise `X-Timeout` (max sensible is about 300 s). The body limit is 64 MB.
- **Client PCs go offline** at night or on power cuts. Treat 503 as normal: queue the sync and retry with backoff. Check `GET /tally-agents` before bulk jobs.
- **Imports are not idempotent.** Never auto-retry an `Import` after 502/504 without first checking whether the voucher was created (look it up by voucher number).
- **Encoding.** Requests and responses are UTF-8. Gujarati and Hindi names pass through intact.
- Tally must have the **right company open**. Otherwise exports come back empty or with errors.

---

## 6. Server-side layout (for ops tasks)

CloudClusters Node container. **Only `/cloudclusters` survives a container restart.**

| Item | Location |
|---|---|
| Relay code | `/cloudclusters/tally-relay/` (`tally-relay.js`, `relay-server.js`, `relay-admin.js`) |
| Relay process | `node relay-server.js` → `127.0.0.1:3001`, log `relay.log` (started with `nohup`, no pm2) |
| Clients + keys | `/cloudclusters/tally-relay/relay_config.json` (chmod 600, **never commit**, backup `/cloudclusters/relay_config.backup.json`) |
| Nginx rules | `/cloudclusters/tally-relay/nginx-tally.inc`, included from `/cloudclusters/config/nginx/sample.conf` |
| WhatsApp API | `/cloudclusters/wpp_service/wpp_server.js` on port 21465. Don't touch it |
| File Server uploads | land in `/cloudclusters/demo/`. Move them from there |

Admin commands (run in `/cloudclusters/tally-relay`, no restart needed):
```bash
export RELAY_PUBLIC_WSS=wss://nodejsclusters-210330-0.cloudclusters.net/agent/ws
node relay-admin.js add-agent <client>-plateflow "<Client Name>"   # prints agent_config.ini
node relay-admin.js add-key   <client>-trilence <client>-plateflow # prints tb_... once
node relay-admin.js list | disable-agent <id> | enable-agent <id> | remove-key <name>
```
Restart the relay after a container restart:
`cd /cloudclusters/tally-relay && nohup node relay-server.js > relay.log 2>&1 &`

Client PC: `C:\TallyBridge\` holds `TallyBridgeAgent.exe`, `agent_config.ini`, `tally_agent.log`, and `install_agent.bat` / `status_agent.bat` / `uninstall_agent.bat`. It runs as the startup task `TallyBridgeAgent`.

---

## 7. Known open items

- The relay and `wpp_server.js` are **not auto-started** after a container restart yet. Only `/cloudclusters/demo/index.js` is started by the platform.
- Retry and queueing for offline clients is not built into the relay. The calling app must handle it (see section 5).

## 8. Don'ts

- Don't expose port 9000 or the Tally PC publicly. Always go through the relay.
- Don't log full API keys or agent tokens.
- Don't edit `/etc/nginx/*`. Changes there are lost on restart; use `/cloudclusters/config/nginx/`.
- Don't run the relay in cluster mode or as multiple instances. Agent connections are held in one process's memory.
