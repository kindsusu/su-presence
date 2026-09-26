# NEO — Naver Search and AI Briefing

For a service targeting the Korean market, a large share of search traffic comes from Naver.
Global SEO and GEO guides do not cover it at all. Two goals: ① Naver search visibility
② **AI Briefing citation** (Naver's AI summary at the top of results naming your page as a source).

## 1. Search Advisor — lay this down first

- [ ] Register the site at searchadvisor.naver.com and verify ownership
      (requires the operator's account — guide the steps, do not attempt it yourself)
- [ ] Submit the sitemap; confirm robots.txt **allows Yeti** (Naver's crawler)
- [ ] **Read Webmaster Tools on a weekly rhythm**: content impression/click trends plus
      per-query clicks, CTR, and position. This is where you see which questions drive traffic
- [ ] Request collection: for important new pages, a manual collection request right after
      registration accelerates pickup

## 2. Winning an AI Briefing citation

Naver AI Briefing attaches source chips **at the paragraph level.** Conditions for a cited page:

- [ ] **Structured facts**: item-value grids (price / term / conditions …) that machines read
      easily. **Label-value structure gets cited over prose blocks**
- [ ] **Primary-source signals**: the page states it is based on an official source, with the
      original link alongside
- [ ] **Freshness**: whichever page stands up fastest after an event takes the citation
- [ ] **Mobile optimization**: Naver is mobile-first. The essentials must be visible on the
      first phone screen, before any scrolling
- [ ] Verification: search the target keyword **in the Naver app** and record the actual source chips

## 3. Two-track blogging (inside / outside)

Naver favors content **inside** its own ecosystem, while accurate data must live on **your own
domain**. The answer is both tracks:

- **inside** — brand blog: builds trust and dwell time inside Naver. What ranks in search and
  SmartBlock is often the blog post
- **outside** — your own domain: the ledger of facts. What AI Briefing cites is ultimately the
  structured data page
- Link naturally from blog posts to your data pages, but **no link spamming**

When the site page and blog post address the same question, use the
[content operations guide](../ops/content.md) to record the canonical URL, sources, and
channel-specific summary in the brief. Before posting, check how headings, tables, links,
and images render in Naver Blog's editor and inspect the mobile preview. Record changed
facts and the actual modification date rather than duplicating the source verbatim or
changing only its date.

### Why two tracks — each channel reaches different engines

Naver Blog is a historically closed platform that has long shut out external crawlers, so
citation measurements repeatedly show it **barely surfacing in the global generative engines
(ChatGPT, Gemini, Claude lines).** Open-web blogs (Tistory and the like) do reach those
engines. Measurements also repeatedly show that in Korea, **blogs, wikis, and communities —
not press articles — are the center of AI citation**: transplanting a global PR-centric
strategy tends not to work here.

⚠️ These observations vary by category and point in time. **Measure with your own vertical's
queries** (`measure.md` §2) — someone else's measurement is a directional hypothesis, never
your number.

> **Decide first whose answer you want to be; pick channels after that.**
> The moment you choose a channel, you have also chosen which engines you can reach.

- Targeting Naver search and AI Briefing → Naver Blog (inside)
- Targeting ChatGPT and Gemini → **open-web channels are mandatory.** Stack everything inside
  Naver and you do not exist for those engines
- ⚠️ Uncontrollable wiki-type sites (Namuwiki and similar) carry a high citation share in Korea —
  put their accuracy and freshness on **continuous monitoring** (`reputation.md` §3)

## 4. Additional items for local and offline businesses

- [ ] Register on Naver Place and keep details accurate. Hours, phone, and address **must match
      the website** — a mismatch is entity fragmentation, the same problem as `llmo.md` §1
- [ ] With multiple branches, build per-branch pages plus LocalBusiness JSON-LD
- [ ] Dedicated landings for "region + service" search questions

## 5. What not to do (Naver is especially sensitive)

- ❌ **Automated neighbor-adding and reciprocal commenting** — a primary target of the spam
  filter. Getting caught leads to low-quality classification for the blog. If Naver is your
  largest traffic channel, this is gambling that channel
- ❌ Mass repetition of identical phrasing (comments, posts) — a spam signal in itself
- ❌ Repeated edit-and-delete cycles after publishing — it erodes document trust score

## 6. Measurement

- Snapshot Webmaster Tools impressions and clicks weekly (there is no public API, so do it
  manually or collect it yourself)
- AI Briefing citation is confirmable **only by searching in the app** — it does not appear in logs
- Watch for the weekend-dip pattern: weekday-shaped topics going quiet on weekends is normal,
  and is evidence of real demand

## 7. Daum / Kakao — where the older demographic passes through

The common observation is that Daum's search audience skews older. **If your target
customers include that demographic, this section is not "secondary" — it is a real-demand
channel.** Raise its priority accordingly. (Promote it to its own lane if demand keeps
growing.)

### Foundation — rides on work you already do

- [ ] **Register with Daum Search** — Daum has no webmaster console, just a simple
      submission form. Register the primary domain with a Kakao account (10 minutes, free)
- [ ] When writing an explicit robots.txt, add one line allowing Daum's crawler, **Daumoa**
- [ ] No extra technical work: `seo.md`'s SSR, sitemap, and structured data apply to
      Daum as-is

### Kakao-ecosystem content — Daum's preferred blocks

Daum's unified search tends to surface its own ecosystem (**Tistory, Brunch, cafes**) as
blocks. The same inside/outside two-track logic as Naver applies:

- [ ] **Tistory** — pick it as your open-web channel and you get global-engine reach
      **and Daum exposure at once** (the double effect in `geo.md`'s channel map).
      If Daum matters to you, Tistory becomes the first-choice open-web channel
- [ ] The original always lives on your own domain; Kakao-ecosystem posts carry a
      summary + link (same rule as §3)

### KakaoTalk touchpoints — reach outside of search

Older users often meet a brand **inside KakaoTalk** rather than through search:

- [ ] **KakaoTalk Channel** — set up and maintain it; the description is the official
      wording fixed in Phase 2, **verbatim**; hours and contacts match the website
- [ ] **Kakao Map business info** — address, phone, and hours must match the website
      (same logic as the place listings in §4). Kakao Map **reviews and ratings are also
      a reputation surface** → `reputation.md`

### Measurement

- [ ] Add **Daum unified-search queries** to the measurement set, split brand/non-brand
- ℹ️ Which index Kakao's AI answers from is not publicly settled — keep it a quarterly
      observation target, not a fact
