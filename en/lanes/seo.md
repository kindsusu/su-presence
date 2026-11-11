# SEO — Technical Groundwork, Item by Item

The goal is content that search engines and services can fetch and understand reliably.
Providing core copy and metadata in raw HTML improves access across crawlers with different rendering support.

This lane is shared groundwork for discovery. Google's AI search features use ordinary Search
indexing and snippet eligibility, so check Googlebot access and indexing as separate evidence.

## 1. What the crawler actually sees

- [ ] Do key pages open **without login?** Content behind an auth wall is not indexed.
      If you cannot open everything, at least SSR a teaser (first paragraph, key figures)
      and gate the rest
- [ ] Does the HTML from `curl -sL <url>` contain the core body? For SPA/CSR apps, consider
      SSR/SSG/prerendering. Google can render JavaScript, so sparse raw HTML alone does not prove
      that indexing is impossible; inspect the rendered result and Search Console too. For the
      fields to compare and how to report them, see
      [render comparison](../ops/evidence.md#javascript-render-comparison--raw-next-to-rendered)
- [ ] ⚠️ **The CSR bailout trap**: even on an SSR framework, certain hooks or APIs can drop
      a whole page to client rendering (e.g. `useSearchParams` without Suspense in Next.js).
      **Re-check representative pages with curl on every deploy** — a sudden drop in body
      text volume is an incident

## 2. noindex accident check (highest priority)

```bash
curl -sL  https://example.com | grep -oiE '<meta[^>]*robots[^>]*>'
curl -sIL https://example.com | grep -i 'x-robots-tag'
```

A staging `noindex` shipped to production voids every other optimization.
Check the **meta tag and the HTTP header — both**.

## 3. Sitemap

- [ ] sitemap.xml exists and is referenced from robots.txt
- [ ] Are **all** detail pages (products, posts, items) in it? Listing-pages-only is a common miss
- [ ] Shard **before** you exceed 50k URLs / 50MB (sitemap index + parts).
      The moment you pass the limit, the whole file is silently ignored
- [ ] When you create a new content type, **adding it to the sitemap is part of shipping.**
      A forgotten type sits outside the index for months

## 4. Meta

- [ ] Title 50–60 chars — **halve it for CJK content (25–30 chars)**: core keyword front,
      brand back
- [ ] Description 150–160 chars — **70–80 for CJK**: a sentence giving a reason to click
      (do not put disclaimers here — it only kills CTR)
- ℹ️ The real limit is **rendered width in the result snippet**, not character count. A CJK
      character is roughly twice as wide as a Latin one, so Latin limits get truncated.
      For mixed strings, budget against the shorter limit
- [ ] ⚠️ Measure **characters, not bytes**. In bash, `${#var}` counts UTF-8 bytes, so one CJK
      character reads as 3 — use `printf '%s' "$t" | wc -m`
- [ ] Titles and descriptions should describe each page's distinct content. Repetition is an
      improvement signal, not proof of duplicate content; inspect body content and canonical too
- [ ] OG image: the face people see when sharing. Per-type dynamic generation is ideal

## 5. Structured data (JSON-LD)

- [ ] Structured data is not required for search or AI eligibility. Add a type such as Article,
      Product, FAQPage, BreadcrumbList, Organization, or LocalBusiness only when the page supports it
- [ ] Represent content users can see or otherwise verify accurately. Match meaning and material
      values; harmless formatting differences do not require character-for-character identity
- [ ] `@id` discipline: the same entity uses the same @id sitewide. Re-declaring Organization
      on every page splits the entity (a direct hit to the LLMO lane) — declare once globally
      and reference it
- [ ] Validate **after deploy** with Google Rich Results Test or a schema.org validator

## 6. URL and response hygiene

- [ ] canonical: parameter variants and duplicate paths point at one canonical
- [ ] For multilingual sites, hreflang must be reciprocal (one-sided is void)
- [ ] A page that does not exist must answer 404, never 200 — soft 404s eat crawl budget
      for nothing
- [ ] ⚠️ **The 404 bake trap**: in ISR/CDN cache layers, a 404 from a transient failure can be
      baked for hours. On a data-fetch failure, throw (retry) instead of returning 404 —
      "not found" and "could not fetch" are different things
- [ ] Redirect chains within one hop

## 7. Performance and assets

- [ ] Images in WebP/AVIF with explicit width/height (CLS)
- [ ] Preload the LCP target (hero image, fonts)
- [ ] Losslessly optimize logos and icons — a several-hundred-KB logo shipped on every page
      is weight you pay for on every single request

## 8. Indexing acceleration

- [ ] IndexNow: ping new and updated pages on publish (consumed by Bing, Naver, Yandex family)
- [ ] Google does not support IndexNow — compete on sitemap `lastmod` accuracy instead
- [ ] For bulk publishing, **build the ping into the publish pipeline** — a manual ping will
      always lapse
