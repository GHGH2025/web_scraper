# Scraper providers

`ScraperEngine` owns shared Playwright lifecycle and saved browser sessions.
Each website implements `ScraperProvider` with its own authentication, listing
collection, and detail extraction.

```python
from scraper_engine import ScraperEngine
from providers import RezzieProvider

engine = ScraperEngine(RezzieProvider())
cards = engine.scrape()
details = engine.extract(cards)
```

The Rezzie provider opens `/buyer/dashboard`, reuses the saved Playwright
session at `scraper/.session/rezzie.json`, and reads `REZZIE_EMAIL` and
`REZZIE_PASSWORD` from `scraper/.env`. It is run headlessly in local and EC2
environments.

Run the live smoke test from Git Bash, WSL, or Linux:

```bash
cd scraper
./test_rezzie.sh
./test_rezzie.sh --limit 2
```

The test writes cards and sampled details to `data/rezzie_test.json` by
default. Use `--cards-only` to skip detail-page extraction.

To add a website:

1. Add `providers/<site>.py` and subclass `ScraperProvider`.
2. Implement the three provider hooks and use a unique `name` and session file.
3. Register the class in `providers/registry.py`.
4. Add a provider-specific command/job only when its scraping behavior is ready.

Florida Off Market's current commands remain unchanged while their logic is
migrated into `FloridaOffMarketProvider` in a follow-up step.
