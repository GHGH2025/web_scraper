"""Self-check for scraper helpers. Run: python test_scraper_helpers.py"""

from db import looks_like_street_address
from extract_fom import keep_image_url, pick_address, pick_price_text
from scrape_fom import _is_fom_host, _page_block_reason, login_url_ok


def main() -> None:
    assert _is_fom_host("https://floridaoffmarket.mysharetribe.com/s")
    assert not _is_fom_host("https://www.mysharetribe.com/")
    assert not _is_fom_host("https://example.com/s")

    assert login_url_ok("https://floridaoffmarket.mysharetribe.com/s")
    assert login_url_ok("https://floridaoffmarket.mysharetribe.com/l/deal/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert not login_url_ok("https://floridaoffmarket.mysharetribe.com/login")
    assert not login_url_ok("https://www.mysharetribe.com/")

    assert _page_block_reason("Please complete the captcha") == "captcha"
    assert _page_block_reason("Invalid password. Try again") == "Invalid password"
    assert _page_block_reason("Welcome back to Florida Off Market") is None

    assert pick_price_text("$230 earnest money. Price: $230,000") == "$230,000"
    assert pick_price_text("$85 application, asking $365,000") == "$365,000"
    assert pick_price_text("Earnest $230") == "$230"
    assert pick_price_text("No dollars here") is None

    assert looks_like_street_address("123 Main Street")
    assert looks_like_street_address("450 NE 31st St, Miami, FL 33137")
    assert not looks_like_street_address("NEW DEAL | MIAMI")
    assert not looks_like_street_address("")
    assert pick_address("NEW DEAL | MIAMI", "Location\n450 NE 31st St, Miami, FL 33137") == "450 NE 31st St, Miami, FL 33137"
    assert pick_address("NEW DEAL | MIAMI", "no street here") is None

    assert keep_image_url("https://cdn.example/photo.jpg")
    assert not keep_image_url("data:image/gif;base64,xxxx")
    assert not keep_image_url("https://facebook.com/icon.png")
    assert not keep_image_url("https://cdn.example/pixel.png", "1", "1")
    print("ok")


if __name__ == "__main__":
    main()
