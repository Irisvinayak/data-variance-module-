# System overview

A one-page orientation. Start here, then follow the links.

> **History note.** This file previously described a single-tenant,
> `D:\Repo5.5`-only system with an iframe-embedded UI. That predated the
> 5.5/6.0 host-profile work and had become actively misleading — it never
> mentioned `HostProfile`, tenancy or iDEAL 6.0, which are now central. It has
> been rewritten. The detail it used to carry now lives in
> [architecture.md](architecture.md) and [functionality.md](functionality.md).

## In one paragraph

The Data Variance Module computes period-over-period variance for iDEAL
regulatory returns and answers natural-language questions about the same data.
It is a FastAPI backend plus a React SPA, embedded in an existing iDEAL host
application that supplies the caller's identity and owns the XML metadata
repository and Oracle database this module reads. It never writes.

## The shape of it

```
 iDEAL host app (.NET)            this module
 ─────────────────────            ───────────────────────────────────────────
 authenticates the user  ──►  loginId / tenantId on every request
 owns the repository     ──►  backend/hosts/  resolves paths per host version
 owns the database       ──►  backend/data/   SELECT-only
                              backend/nlp/    embeddings + LLM, read-only
                              frontend/       one build per host vdir
```

## The five things worth knowing on day one

1. **One codebase serves two host applications** — iDEAL 5.5 (single-tenant)
   and 6.0 (multi-tenant). `VERSION` in the root `.env` is the only switch.
2. **Every host difference lives in `backend/hosts/`.** If you are writing
   `if is_legacy_mode():` anywhere else, it belongs in a `HostProfile`.
3. **Route handlers are `def`, not `async def`** — on purpose. They do blocking
   work and must run in FastAPI's threadpool; as coroutines one slow request
   froze the entire process.
4. **NLP imports inside handlers are function-local** — on purpose. They keep a
   ~1.3 GB embedding model out of deployments that only use the manual routes.
5. **Authorization is enforced before the LLM sees anything**, not after. The
   candidate shortlist is filtered to the user's allowed returns first.

Each of these has bitten someone already; the reasoning is recorded next to the
code as well as here.

## Where to go next

| Question | Document |
|---|---|
| How does a request flow? What are the layers? | [architecture.md](architecture.md) |
| What does the product actually do? | [functionality.md](functionality.md) |
| What do the endpoints accept and return? | [models.md](models.md) |
| Where do logs go? How do I audit an AI answer? | [logging.md](logging.md) |
| Why are 5.5 and 6.0 the way they are? | [integration-plan.md](integration-plan.md) |
| How do I run it? | [../README.md](../README.md) |

`docs/samples/` holds example NL and user queries.

## Status

- iDEAL 5.5 — in production.
- iDEAL 6.0 — integrated; see [integration-plan.md](integration-plan.md) §6 for
  the remaining open questions, notably the role/permission `OptionId` mapping,
  which is wired but not yet callable.
