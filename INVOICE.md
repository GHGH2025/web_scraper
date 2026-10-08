# Invoice — Website Scraping

| | |
|--|--|
| **Bill To** | Richie |
| **Project** | Florida Off Market + Rezzie website scrapers |
| **Invoice date** | September 10, 2026 |
| **Reference** | Website Scraping Effort Estimate (30–40 hours) |
| **Hours billed** | **40** |

This invoice is for the upper end of the approved estimate. Each website required its own custom scraping logic (structure, authentication, and behavior), so the work was not copied from one site to the other.

---

## Summary

| # | Item | Hours |
|---|------|------:|
| 1 | FloridaOffMarket — new wholesaler integration, custom scrape, and pipeline posting | 15 |
| 2 | Rezzie.com — custom scrape, dynamic wholesaler creation, and locked-listing handling | 25 |
| | **Total** | **40** |

Hourly rate and dollar total: *fill in as agreed.*

---

## 1. FloridaOffMarket — 15 hours

**Quoted:** 10–15 hours

### Scope delivered

**New wholesaler integration**

- Added Florida Off Market as a distinct web source in the existing listing pipeline (separate from email and WhatsApp).
- Mapped each deal to a wholesaler record so scraped properties can be linked the same way as other inventory.
- Used the site’s “About the Wholesaler” profile when present, and AI contact enrichment when the page only had a name or bio.

**Custom scraping logic**

- Signed in to the Sharetribe marketplace (`floridaoffmarket.mysharetribe.com`) with saved browser session reuse.
- Handled stale sessions (auto re-login), cookie/banner dismissal, and blocked/CAPTCHA pages instead of failing silently.
- Collected search cards across pagination, with optional county filter.
- Opened each deal page and extracted property fields, including:
  - Address, asking price, beds, baths, sq ft, lot size, year built
  - Estimated ARV and estimated repairs
  - Occupancy, pool, garage/carport, transaction type, payment methods
  - Description, photos, and wholesaler name/bio

**Process and post to Podio, WordPress, and WhatsApp**

- Wrote every extracted listing to MongoDB `raw` (full audit copy).
- Queued new addresses into `filtered` using a 30-day cross-source address gate so recent email/WhatsApp deals are not re-posted.
- Wired the queue into the existing listing agent so approved web deals follow the same Podio, WordPress, and WhatsApp path as other listings.
- Web listings stay off those channels until publication is turned on (`web_publish_enabled`), so nothing posts until review.

---

## 2. Rezzie.com — 25 hours

**Quoted:** 20–25 hours

### Scope delivered

**Dynamic wholesaler creation**

- Read structured seller contact from each property (name, role, company, email, phone).
- Created a new wholesaler in the local directory when that seller email did not already exist.
- Flagged the record for Podio so a matching Wholesellers item can be created/linked when the deal is published.
- Rejected a Rezzie listing before it entered the pipeline if no usable seller email was available (web listings must be traceable to a wholesaler).

**Custom scraping logic**

- Built a separate authenticated buyer-dashboard scraper (not reused from Florida Off Market).
- Handled Rezzie’s in-page “Authentication required” modal (sign-in from the protected dashboard, not only `/login`).
- Reused a saved Playwright session; re-authenticated when a property page bounced back to login.
- Collected listings from JavaScript **Details** buttons (the live dashboard does not expose ordinary property links), including multi-page “Next” pagination.
- Extracted each property’s full detail page, including:
  - Address/title, purchase price, estimated rehab, total investment, ARV
  - Deal type, price per sq ft, closing date
  - Beds, baths, sq ft, year built, lot size, property type/condition
  - Description, features, condition details, seller contact, and photos

**Account-upgrade / locked listings**

- Rezzie hides some property details behind an account upgrade. Extra time went into finding a reliable approach:
  - Detect authentication walls and upgrade/lock redirects instead of treating them as empty pages.
  - Extract every listing the current buyer account can open.
  - Skip or error locked items cleanly so one blocked deal does not stop the rest of the run.
- Daily job continues with all accessible inventory; locked details stay unavailable until the account has access.

---

## Shared work included in the hours above

- Shared scraper engine (browser lifecycle, saved sessions, per-site providers).
- Daily headless run at **1:00 AM America/New_York** for both sites; one site failing does not skip the other (one retry, then continue).
- Deployed on the existing listing-agent host as `richie-web-scraper.service`.
- Source tagging (`florida_off_market` / `rezzie`) through raw → filtered → parsed listing for review and metrics.

---

## Notes

This matches the original estimate: custom logic per site, Florida Off Market at the top of the 10–15 hour range, and Rezzie at the top of the 20–25 hour range because of dashboard auth, JavaScript listing cards, dynamic wholesaler creation, and locked/upgrade listings.

Publication to Podio, WordPress, and WhatsApp uses the existing listing pipeline. Web deals are collected and processed automatically; they are not sent to those channels until a listing is approved for publication.
