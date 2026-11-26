"""Self-check for scraper helpers. Run: python test_scraper_helpers.py"""

from db import looks_like_street_address
from providers.rezzie import RezzieProvider


def main() -> None:
    assert looks_like_street_address("123 Main Street")
    assert looks_like_street_address("450 NE 31st St, Miami, FL 33137")
    assert not looks_like_street_address("NEW DEAL | MIAMI")
    assert not looks_like_street_address("")

    assert RezzieProvider._is_marketing_home("https://rezzie.com/")
    assert RezzieProvider._is_marketing_home("https://www.rezzie.com")
    assert not RezzieProvider._is_marketing_home("https://rezzie.com/buyer/dashboard")
    assert not RezzieProvider._is_marketing_home("https://rezzie.com/buyer/property/abc")

    print("ok")


if __name__ == "__main__":
    main()
