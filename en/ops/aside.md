# Selective Aside use

## Choose the tool

- Use existing tools for code changes, public document retrieval, HTTP audits, sitemap/robots checks, and aggregation. Do not invoke Aside or check its installation/session for those tasks alone.
- Prefer Aside for actual AI web UI work requiring authentication when it has the relevant signed-in session. Verify the current screen when needed; sessions expire.
- Use an existing suitable browser when the session is only there or Aside is unavailable. Do not force installation, cookie migration, or sign-in.
- Read account consoles only within the authorized site, account, and read scope. Publishing or settings changes follow their own task authorization.

## Execute and record

Read `aside --help` and `aside guide repl` when use is needed. Prefer REPL for direct observation and fixed steps; use `aside exec` only when delegating a complex task is beneficial. MCP is optional; CLI access also works.

Follow the [measurement playbook](measure-playbook.md). Keep the exact query, run, locale, actual web product, search state, and login state fixed. Verify neutral mode and input before submission. Record remaining effects of account memory or personalization as observation limits.

Prepare slots with `collect <audit.json> --browser`. Use `collect --record` only after confirming the completed response and actual citation URLs. Authentication, CAPTCHA, or response inspection failures remain `unmeasured`, not zero citations. Include the execution tool, observation timestamp, and evidence location in `--note`, without tokens, cookies, or unnecessary account information. Match the current measurement plan's query and run.

Aside reasoning through a ChatGPT subscription is different from querying the ChatGPT website. Do not record the Aside agent's answer as a citation observation from the target web product. Continue using `measure report` for aggregation.

## Optional Codex CLI connection

MCP registration is unnecessary when CLI invocation already works. If the user requests registration, inspect existing server entries and back up global configuration before changing it. Verify the actual installation path. This example uses the default Windows location:

```powershell
codex mcp add aside -- "$env:LOCALAPPDATA\Aside\CLI\current\aside.exe" mcp --host local
```

Check tool discovery and a read-only call from a new Codex session after registration. Configuration success is distinct from a successful call. Manage subscription authentication through Aside OAuth; do not copy credentials into the repository.

## Verification limits

On 2026-09-26, one Windows environment passed MCP initialization, tool discovery, and a tab-count call; signed-in new-chat screens were confirmed for four AI web products. This does not establish other users' sessions or citation collection success rates. End-to-end query/citation accuracy and time savings have not been compared.

- [Aside developer tools](https://docs.aside.com/help/developers)
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
