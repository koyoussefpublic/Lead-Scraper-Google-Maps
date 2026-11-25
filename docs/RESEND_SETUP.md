# Resend Email Outreach Setup Guide

CarteFidele CRM uses the official [Resend](https://resend.com) API for email outreach, with `contact@cartefidele.com` as the default `Reply-To` address.

---

## 1. Domain & DNS Verification (Recommended Subdomain)

To protect your primary domain reputation, send outreach from a dedicated subdomain such as `outreach.cartefidele.com` (e.g., `hello@outreach.cartefidele.com`) while keeping `contact@cartefidele.com` as `RESEND_REPLY_TO`.

1. Log in to **Resend** → **Domains** → **Add Domain** (`outreach.cartefidele.com`).
2. Add the DNS records provided by Resend to your DNS provider:
   - **DKIM** (`TXT` records for cryptographic signing)
   - **SPF** (`TXT` / `MX` record authorizing Resend to send on behalf of the subdomain)
   - **DMARC** (`TXT` record at `_dmarc.outreach.cartefidele.com`, e.g. `v=DMARC1; p=none; rua=mailto:contact@cartefidele.com`)
3. Wait for Resend to mark the domain **Verified** before enabling production sending.

---

## 2. Environment Variables

```env
APP_ENV=production
ALLOW_EXTERNAL_MESSAGING=true
RESEND_API_KEY=re_your_secret_key
RESEND_FROM_EMAIL=CarteFidele <hello@outreach.cartefidele.com>
RESEND_REPLY_TO=contact@cartefidele.com
RESEND_WEBHOOK_SECRET=whsec_optional_signing_secret
```

> **Local Development Safety**: When `APP_ENV=development` or `ALLOW_EXTERNAL_MESSAGING=false`, the CRM simulates email sends locally and never calls the Resend API.

---

## 3. Webhooks (Delivery, Bounce & Complaint Handling)

Configure a Resend webhook pointing to:
```text
https://your-crm-domain.up.railway.app/api/webhooks/resend
```
Select events:
- `email.delivered`
- `email.bounced` (automatically adds the address to the CRM suppression list)
- `email.complained` (automatically adds the address to the CRM suppression list)
- `email.delivery_delayed`
