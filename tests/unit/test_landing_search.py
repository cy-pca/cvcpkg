"""The landing page is a fast brand page; package discovery lives on /search.

The landing page used to run a full ``/v1/search`` (every variant in the
registry, with facets and a size rollup) on first paint, which got slow once
the catalog grew past a few thousand packages.  These tests pin the split so a
future edit cannot quietly reintroduce the slow auto-load:

* the landing HTML carries no package table and references no ``/v1/search``;
* the search page carries the table but rests in an empty state, and its
  ``init()`` only queries when there is a query or a filter (or a ``?q=`` deep
  link).
"""

from __future__ import annotations

import pytest

from cvcpkg.server import landing


class TestLandingIsFastAndBranded:
    def test_landing_has_no_package_table(self):
        html = landing.landing_html()
        assert 'id="pkg-body"' not in html
        assert 'id="load-more"' not in html

    def test_landing_never_calls_search_or_deps(self):
        html = landing.landing_html()
        # The whole point: no registry scan on the front page.
        assert "/v1/search" not in html
        assert "/v1/deps" not in html
        assert "runSearch" not in html

    def test_landing_does_one_cheap_fetch(self):
        html = landing.landing_html()
        assert "fetch('/healthz')" in html

    def test_landing_search_box_navigates_to_search_page(self):
        html = landing.landing_html()
        assert 'action="/search"' in html
        assert 'name="q"' in html

    def test_landing_is_branded(self):
        html = landing.landing_html()
        assert landing.brand_banner_href() in html  # navbar logo
        assert landing.brand_hero_href() in html  # hero graphic
        assert "install.sh" in html  # one-line install
        # brand fonts + neon palette are wired in the head/CSS
        assert "Gentium+Book+Basic" in html and "VT323" in html


class TestSearchPageIsLazy:
    def test_search_page_has_the_table(self):
        html = landing.search_html()
        assert 'id="pkg-body"' in html

    def test_search_page_table_starts_empty(self):
        html = landing.search_html()
        # No spinner rows baked into the tbody -> it renders an empty rest state
        # from JS, and does not imply an in-flight request.
        assert '<tbody id="pkg-body"></tbody>' in html

    def test_search_init_is_query_gated(self):
        # The shared search JS only searches when there is criteria.
        assert "function maybeSearch" in landing._LANDING_JS
        assert "showSearchEmptyState" in landing._LANDING_JS
        assert "_hasCriteria" in landing._LANDING_JS
        # init reads a ?q= deep link rather than always running a search.
        assert "URLSearchParams(location.search)" in landing._LANDING_JS

    def test_recipe_meta_is_lazy(self):
        # /v1/deps must not be fetched on load; only before the first search.
        assert "ensureRecipeMeta" in landing._LANDING_JS


@pytest.mark.parametrize(
    "name",
    ["cvcpkg-icon.png", "cvcpkg-banner.png", "cvcpkg-hero.png", "cvcpkg-icon-180.png"],
)
def test_brand_images_are_bundled(name):
    data, media = landing.brand_asset(name)
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), name
    assert media == "image/png"
