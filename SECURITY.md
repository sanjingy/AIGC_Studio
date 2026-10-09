# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue, pull request or discussion for security problems.

Report privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability** (GitHub private vulnerability reporting). Include:

- the affected component (for example `apps/api/modules/billing`, the local runner, the web app);
- steps to reproduce or a proof of concept against a local deployment;
- the impact you expect (data exposure across organizations, credit/billing manipulation,
  credential disclosure, server-side request forgery, and so on).

Please test only against your own local instance started from this repository. Do not probe
deployments you do not operate, and do not include real API keys or personal data in reports.

We aim to acknowledge reports within 7 days. This is a pre-1.0 project maintained on a
best-effort basis; there is no bug bounty.

## Supported versions

Only the latest commit on `main` receives security fixes.

## Areas we consider security-sensitive

- Tenant isolation: every user-facing query is scoped by `org_id`; cross-tenant access must
  return 404.
- The credits ledger (`reserve` / `settle` / `release`, idempotency keys, row locks).
- Stored provider credentials (BYOK keys are encrypted at the application layer) and log redaction.
- Outbound calls to user-supplied endpoints (public-host checks, no redirects).
- YAML-only third-party Agents and Skills and their allow-lists.
- The local runner bridge token and its three endpoints.
