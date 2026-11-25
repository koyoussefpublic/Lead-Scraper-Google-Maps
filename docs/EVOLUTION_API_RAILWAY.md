# Self-Hosted Evolution API Deployment Guide (Railway)

This guide explains how to deploy your own **Evolution API** instance on **Railway** using the official Docker image (`evoapicloud/evolution-api:latest`) so your CarteFidele CRM can connect to your business WhatsApp number via Baileys QR code.

> **Important**: Evolution API runs as a **separate Railway service**, completely independent of the Lead Scraper / CRM container and never installed on your local Windows PC.

---

## 1. Architecture Overview

```text
CarteFidele Scraper & CRM (Railway Service 1)
   │
   │  HTTPS REST API (apikey header)
   ▼
Your Self-Hosted Evolution API (Railway Service 2: evoapicloud/evolution-api:latest)
   │
   │  Baileys WhatsApp Web session (QR code pairing)
   ▼
Your Business WhatsApp Number
```

> **Note on Baileys vs Official Meta Cloud API**: Baileys connects via WhatsApp Web. It does not require Meta Business Manager credentials, but WhatsApp Web automation carries account/policy limits and cannot guarantee against rate limits or account restrictions. Avoid unsolicited bulk messaging and only message eligible, consented contacts.

---

## 2. Create the Evolution API Service on Railway

1. Open your Railway project and click **+ New** → **Database** → **Add PostgreSQL** (recommended for persistent instances, messages, contacts, and chats).
2. Optionally add **Redis** (**+ New** → **Database** → **Add Redis**) if you want Redis caching enabled.
3. Click **+ New** → **Docker Image** and enter:
   ```text
   evoapicloud/evolution-api:latest
   ```
4. Generate a public HTTPS domain under **Settings → Networking → Generate Domain** (e.g., `https://evolution-api-production-xxxx.up.railway.app`).

---

## 3. Configure Evolution API Environment Variables (in Railway)

Set these variables on the **Evolution API** Railway service (verify against the [official Evolution API repository](https://github.com/evolution-foundation/evolution-api)):

| Variable | Example / Value | Notes |
|---|---|---|
| `SERVER_PORT` | `8080` | Port Evolution API listens on (match Railway Target Port) |
| `SERVER_URL` | `https://your-evolution-domain.up.railway.app` | Public HTTPS URL of this service |
| `AUTHENTICATION_API_KEY` | `<strong-random-32-char-secret>` | Master API key; never expose in browser |
| `DATABASE_PROVIDER` | `postgresql` | Persistent database |
| `DATABASE_CONNECTION_URI` | `${{Postgres.DATABASE_URL}}` | Reference your Railway PostgreSQL service |
| `DATABASE_SAVE_DATA_INSTANCE` | `true` | Persist WhatsApp instance metadata |
| `DATABASE_SAVE_DATA_NEW_MESSAGE` | `true` | Persist messages |
| `DATABASE_SAVE_DATA_CONTACTS` | `true` | Persist contacts |
| `DATABASE_SAVE_DATA_CHATS` | `true` | Persist chats |
| `CACHE_REDIS_ENABLED` | `true` (or `false`) | Enable if you added Railway Redis |
| `CACHE_REDIS_URI` | `${{Redis.REDIS_URL}}` | Reference Railway Redis if enabled |
| `CACHE_REDIS_SAVE_INSTANCES` | `true` | Persist instance cache in Redis |

### Persistent Session Storage
- **Never rely on ephemeral container filesystem storage** for WhatsApp sessions.
- Attach a **Railway Volume** to the Evolution API service for its session/instance store (check the official Evolution API Docker configuration for the current `/evolution/instances` volume path) alongside PostgreSQL persistence.

---

## 4. Connect Your Business WhatsApp Number via QR Code

1. Deploy the Evolution API service and verify its health endpoint in your browser or `curl`:
   ```bash
   curl https://your-evolution-domain.up.railway.app/
   ```
2. Configure your **CarteFidele Scraper & CRM** Railway service with:
   ```env
   APP_ENV=production
   ALLOW_EXTERNAL_MESSAGING=true
   WHATSAPP_ENABLED=true
   WHATSAPP_PROVIDER=evolution
   EVOLUTION_API_ENABLED=true
   EVOLUTION_API_BASE_URL=https://your-evolution-domain.up.railway.app
   EVOLUTION_API_KEY=<same-AUTHENTICATION_API_KEY>
   EVOLUTION_API_INSTANCE=cartefidele-main
   EVOLUTION_WEBHOOK_SECRET=<optional-shared-secret>
   ```
3. Open your CRM at `https://your-crm-domain.up.railway.app/crm` → **WhatsApp & Email** tab.
4. Click **Get QR Code**, open WhatsApp on your business phone → **Linked Devices** → **Link a Device**, and scan the QR code.
5. Configure Evolution API webhooks to point to:
   `https://your-crm-domain.up.railway.app/api/webhooks/evolution`
   so incoming replies and delivery updates appear automatically in your CRM Unified Inbox.
