# Client Onboarding Checklist — Warehouse Logistics Chatbot

Use this per client, once the adapter-based pipeline (from
`legacy-wms-integration-spec.md`) is built. Most of onboarding is now
"answer these questions → fill in a config file," not custom development.

---

## 1. Discovery call — questions to ask the client

### About their WMS / data source
- [ ] What WMS product and version are you running? (Determines which
      adapter applies — or whether a new one needs to be built.)
- [ ] Can you export data to a file (CSV/XML/EDI/JSON) on a schedule, or
      is a direct database connection possible?
- [ ] If file export: where does it land — SFTP, a shared drive, email
      attachment, something else? Who controls that folder?
- [ ] If DB access: what's the database engine (Oracle/SQL
      Server/DB2/AS400/other)? Can they provision a **read-only** user or
      read replica — not a live connection into their production system?
- [ ] If EDI: is it delivered via AS2, VAN, or another mechanism? Do they
      have a copy of a sample file you can look at?
- [ ] How often does their data actually change? (Determines poll
      interval — hourly vs. nightly vs. real-time-ish.)
- [ ] Can you get a **sample export** (even a small, anonymized one)
      before building anything? Don't build against assumptions — build
      against real field names and formats.

### About their data
- [ ] What fields do they track per SKU? (Compare against your canonical
      schema's alias table — flag any fields you don't currently map.)
- [ ] Do they have safety stock / reorder point / lead time data
      already, or does that live somewhere else (spreadsheet, buyer's
      head, ERP)? Math tools are only as good as the inputs.
- [ ] Single warehouse, or multiple locations/sites? Does data need to
      be scoped so one site's staff can't see another's?
- [ ] Do they have supplier cost data (order cost, holding cost, MOQ) or
      will EOQ/newsvendor calculations need estimated defaults?

### About their users
- [ ] Who will actually use the chatbot — warehouse floor staff,
      planners, both? (Affects tone/complexity of responses and which
      chat surface makes sense.)
- [ ] Preferred chat surface: standalone web chat, Slack, Teams, or
      embedded in something they already use daily?
- [ ] Any authentication requirement (SSO, existing login system) the
      chat frontend needs to integrate with?
- [ ] Any data sensitivity/compliance requirements (who's allowed to see
      cost data vs. just stock counts)?

### About expectations
- [ ] What questions do they actually want answered? Get 5–10 concrete
      example questions from them — this tells you which calculation
      tools (#8 in the build spec) actually matter for this client,
      rather than guessing.
- [ ] How fresh does the data need to feel to them? Is "as of last
      night's batch" acceptable, or do they expect near-real-time?

---

## 2. What you need from them (access/logistics)

- [ ] Sample data export (see above) — get this before writing any
      adapter config
- [ ] Read-only credentials or file-drop access, scoped to only what's
      needed (never request write access to their WMS)
- [ ] A technical contact on their side who can troubleshoot export
      issues if the feed breaks
- [ ] Sign-off on where the data will be stored (your infrastructure —
      be ready to answer questions about hosting, retention, security)
- [ ] Confirmation of any compliance requirements (e.g. data residency)
      before you decide where to host their instance

---

## 3. What you do internally, per client

- [ ] Fill in an adapter config (from the build spec's config pattern)
      based on discovery answers — this is usually the whole
      "integration," not custom code, if an adapter for their mechanism
      already exists
- [ ] Run the adapter against their sample export first, in a test
      environment, before touching their live system
- [ ] Check the sample against the canonical schema/alias table — flag
      and resolve any unmapped fields with the client before go-live
- [ ] Confirm which calculation tools are relevant based on their
      example questions (§1) — no need to enable stockpyl functions they
      won't use, but note nothing extra needs "building" per client
      since the tool registry is shared
- [ ] Set up tenant/warehouse scoping if multi-site (guardrails from the
      build spec, #5)
- [ ] Configure the chosen chat surface and auth for this client
- [ ] Set the staleness threshold/freshness expectation to match what
      they told you in discovery

---

## 4. Before go-live

- [ ] Run the adapter against a real (not sample) pull from their system
      and verify record counts look right
- [ ] Ask the client to review a handful of real answers (stock levels,
      one ROP/EOQ calculation) against numbers they know are correct
- [ ] Confirm dead-letter/failure alerting is pointed at someone who
      will actually notice if their feed breaks
- [ ] Walk the actual end users (not just your technical contact)
      through a few example questions before full rollout
- [ ] Agree on a support path: who do their users contact if the
      chatbot gives a wrong or confusing answer?

---

## 5. After go-live

- [ ] Check ingestion logs after the first few real cycles — confirm no
      silent failures
- [ ] Revisit their example question list after a couple of weeks of
      real usage — are people asking things the current tool set can't
      answer? That's your signal for what to expand next (ties back to
      build spec §8's "expand based on real demand" principle)
