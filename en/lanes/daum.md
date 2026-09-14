# KEO — Daum and Kakao search, and the AI Summary

Bundled into one cell with Naver, neither one is visible. **The crawler tokens differ, the
ownership proof differs, the registration flow differs, and a different model writes the AI
summary.** That is why this is its own lane.

> Why it was split, and what leaked while it was bundled, is in the
> [decision record](../../docs/decisions/2026-09-11-keo-lane.md).

Daum's user base skews older, as a general observation.
**If your target customers include middle-aged and older users, this lane is real demand, not a
side channel.**

> ⚠️ **Do not call Daum "Kakao search."** Upstage acquired it in May 2026, and the AI Summary
> runs on Upstage's **Solar**, not a Kakao model. Only Kakao Map and Kakao Talk Channel remain
> with Kakao, which means **the local data is joined by a contract between two companies** — if
> that link breaks, section 3 below is void. Re-check it quarterly.

---

## 1. Foundation — it rides on work you already do

There is no extra technical work. SSR, sitemaps and structured data from `seo.md` apply to Daum
unchanged. Only these three are Daum-specific.

- [ ] **Daum search registration** — `register.search.daum.net`. Only the **top page** of the
      primary domain is accepted (no subpages). If the submitted title differs from the site's
      actual `<title>`, it is rejected. Ten minutes, free
- [ ] **Daum Webmaster Tools** — `webmaster.daum.net`. Access is by **site URL + PIN**, not an
      account, and ownership is proven by adding a single `#DaumWebMasterTool:<hash>` comment
      line to robots.txt. ⚠️ It **resets 24 months** after you accept the terms, so set a renewal
      reminder. It reports crawl, index and impression/click stats, and has been in Beta for years
- [ ] Put **both Daum crawler tokens** in robots.txt. The official documentation says `DAUM`;
      industry practice says `Daumoa`. robots.txt applies **only the single most specific UA
      group**, so if you list `Daumoa` alone, requests arriving as `DAUM` fall through to the `*`
      group

      ```
      User-agent: DAUM
      User-agent: Daumoa
      Allow: /
      ```

The robots.txt draft from `tools/generate.py` includes both tokens. `tools/crawl.py` raises
`DAUM_CRAWLER_BLOCKED` (critical) if either one is blocked.

### The tool does not judge ownership verification

Naver has a `naver-site-verification` meta tag, so a crawl can at least suspect a missing
connection. Daum uses **PIN and a robots comment, so a crawl can prove neither "registered" nor
"unregistered."** Turning an absence into a finding would mean writing down *not observed* as
*something is wrong*, so this item lives in the **human-checked** column of `ops/coverage.md`.

---

## 2. Kakao-ecosystem content — Daum's favoured zone

What Daum favours is **Tistory** — its own registration guide says so outright: *"Tistory and Daum
Blog appear in search automatically, without submitting a registration request."*

⚠️ **Do not expect Brunch or Cafe to carry you.** In September 2026 measurements the top Unified
Web results were dominated by `blog.naver.com` instead, and neither Brunch nor Daum Cafe appeared
at all. Daum also indexes Naver content — the reverse is not true. If you already run a Naver
blog, part of the Daum side is covered for free.

The same inside/outside two-track logic from `naver.md` holds here:

- [ ] **Tistory** — if you pick one open-web channel, this one buys reach into the global engines
      **and** Daum visibility at the same time (the double effect in the `geo.md` channel map).
      If Daum matters to you, Tistory is the first candidate
      · Attaching a **custom domain** to Tistory keeps your brand domain while still riding the
        favoured indexing path. Measured evidence: a custom domain, not a `.tistory.com` one, is
        indexed under Daum's internal class `gsid=tstory-*`
      · An individual Tistory blog's robots.txt blocks only admin paths and **does not block AI
        crawlers** — the exact opposite of Naver Blog, which blocks GPTBot, ClaudeBot and
        PerplexityBot outright
- [ ] The original always lives on your own domain; the Kakao ecosystem gets a summary plus a link

---

## 3. Kakao Talk touchpoints — reach outside search

Older users meet brands **inside Kakao Talk** more often than through search:

- [ ] **Kakao Talk Channel** — open and maintain one. The description uses the approved official
      wording **verbatim**; hours and contact details match the website
- [ ] **Kakao Map business listing** — address, phone and hours must match the website.
      Kakao Map **reviews and ratings are also a reputation surface** → `reputation.md`

---

## 4. Measurement

- [ ] Add **Daum Unified Search queries** to the query set, split into brand and non-brand
- [ ] Measure **Daum AI Summary** citations — detect firing with the block marker
      `disp-attr="AIO"`, then classify sources from the payload's `sources[].url` and the `gsid`
      prefix (`tstory-` Tistory / `nvblg_` Naver Blog / `cafe-` Daum Cafe / `web-` general open
      web / empty = Daum's own properties). The September 2026 distribution was mostly Tistory
      and Daum's own properties, with **pure open web in the minority** — your own domain alone
      is competing for a narrow slot

`tools/collect.py` does the unattended collection. The access traps are collected in the "Daum"
section of `ops/measure-playbook.md`; two of them you must simply remember:

```
⚠️ curl's default UA gets you 162 bytes. Send a browser UA or use headless.
⚠️ The page encoding is EUC-KR. Reading it as UTF-8 produces garbage.
```

### Before you write down "zero"

When Daum shows "no citations", **suspect the response first.** A sandboxed browser once returned
a reduced SERP, and the real browser showed the site present at the same moment. The response
sanity gate in `collect.py` records that case as `unmeasured` — **not zero.**
Full rule in `ops/measure.md`, "cross-checking a zero".

---

## 5. This lane's blanks

The KEO rows in `ops/coverage.md` are the source of truth. Tool-filled and human-filled cells
split like this.

| Surface | Who | How |
|---|---|---|
| Daum web search and AI Summary citations | tool | `python tools/collect.py <audit.json>` |
| Crawler tokens allowed | tool | `audit.sh` · `crawl.py` |
| Daum Webmaster Tools registration | **human** | `webmaster.daum.net` — renew every 24 months |
| Kakao Map NAP | **human** | confirm address, phone and hours match |
