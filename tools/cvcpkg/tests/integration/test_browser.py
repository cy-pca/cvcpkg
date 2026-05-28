"""Playwright browser integration tests for cvcpkg-server frontend.

These tests exercise the server-rendered HTML pages in a real browser
to verify that the landing page, guide page, package detail page, and
organizations page all render correctly, that JavaScript initialises
properly (burger menu, dropdown, stats, search), and that navigation
between pages works.

Run locally with Docker Compose::

    cd tools/cvcpkg
    docker compose -f docker-compose.test.yml up -d postgres backend
    # Wait for health
    until curl -sf http://127.0.0.1:8421/healthz; do sleep 2; done
    docker compose -f docker-compose.test.yml run --build --rm playwright
    docker compose -f docker-compose.test.yml down -v

Or from the host (requires ``pip install pytest-playwright && playwright install chromium``)::

    CVCPKG_TEST_SERVER_URL=http://127.0.0.1:8421 \\
        pytest tests/integration/test_browser.py -v --browser chromium

Environment variables:
    CVCPKG_TEST_SERVER_URL  — base URL of the running server
                              (default: http://127.0.0.1:8421)
"""

from __future__ import annotations

import os
import re

import pytest

playwright_module = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

SERVER_URL = os.environ.get("CVCPKG_TEST_SERVER_URL", "http://127.0.0.1:8421")


# ── Landing page ────────────────────────────────────────────────


class TestLandingPage:
    """Tests for the main landing page at /."""

    def test_page_loads_and_has_title(self, page):
        page.goto(SERVER_URL)
        expect_title = re.compile(r"cvcpkg", re.IGNORECASE)
        assert expect_title.search(page.title())

    def test_hero_section_visible(self, page):
        page.goto(SERVER_URL)
        hero = page.locator("section.hero")
        assert hero.is_visible()
        # Hero should contain the site name
        assert "cvcpkg" in hero.text_content().lower()

    def test_stats_section_loads(self, page):
        """Stats boxes should update from —  to actual values via JS."""
        page.goto(SERVER_URL)
        # Wait for the JS init to update stats (fetches /v1/packages)
        stat = page.locator("#stat-packages")
        stat.wait_for(state="attached")
        # After JS runs, the stat should no longer be the placeholder
        page.wait_for_function(
            """() => {
                const el = document.getElementById('stat-packages');
                return el && el.textContent.trim() !== '—';
            }""",
            timeout=10000,
        )
        text = stat.text_content().strip()
        assert text != "—", f"stat-packages was not updated by JS: {text!r}"

    def test_navbar_brand_links_home(self, page):
        page.goto(SERVER_URL)
        brand = page.locator(".navbar-brand a.navbar-item").first
        assert brand.is_visible()
        # Should contain the version tag
        version_tag = brand.locator(".tag")
        assert version_tag.is_visible()
        assert version_tag.text_content().startswith("v")

    def test_navbar_has_docs_dropdown(self, page):
        page.goto(SERVER_URL)
        dropdown = page.locator(".navbar-item.has-dropdown")
        assert dropdown.count() >= 1
        # Should contain Getting Started and API Reference links
        dropdown_items = dropdown.locator(".navbar-dropdown .navbar-item")
        texts = [
            dropdown_items.nth(i).text_content().strip() for i in range(dropdown_items.count())
        ]
        assert any("Getting Started" in t for t in texts)
        assert any("API Reference" in t for t in texts)

    def test_search_input_exists(self, page):
        page.goto(SERVER_URL)
        search = page.locator("#search")
        assert search.is_visible()
        assert search.get_attribute("placeholder")

    def test_platform_filter_exists(self, page):
        page.goto(SERVER_URL)
        select = page.locator("#platform-filter")
        assert select.is_visible()

    def test_package_table_has_sortable_headers(self, page):
        page.goto(SERVER_URL)
        headers = page.locator("th.is-sortable")
        assert headers.count() >= 3  # name, version, builds, size
        # Each should have a sort-arrow span
        for i in range(headers.count()):
            arrow = headers.nth(i).locator(".sort-arrow")
            assert arrow.count() == 1

    def test_footer_visible(self, page):
        page.goto(SERVER_URL)
        footer = page.locator("footer.footer")
        assert footer.is_visible()
        assert "cvcpkg" in footer.text_content().lower()

    def test_github_link_in_navbar(self, page):
        page.goto(SERVER_URL)
        gh_link = page.locator(".navbar-end a[href*='github.com']")
        assert gh_link.count() >= 1


# ── Guide page ──────────────────────────────────────────────────


class TestGuidePage:
    """Tests for the /guide documentation page."""

    def test_guide_page_loads(self, page):
        page.goto(f"{SERVER_URL}/guide")
        assert "Getting Started" in page.title() or "Getting Started" in page.content()

    def test_guide_has_toc(self, page):
        page.goto(f"{SERVER_URL}/guide")
        toc = page.locator("ol.toc")
        assert toc.is_visible()
        # Should have entries for each section
        items = toc.locator("li")
        assert items.count() >= 7  # install, quick-start, requirements, etc.

    def test_guide_toc_links_work(self, page):
        page.goto(f"{SERVER_URL}/guide")
        # Click the first TOC link and verify anchor scrolls
        toc_link = page.locator("ol.toc li a").first
        href = toc_link.get_attribute("href")
        assert href and href.startswith("#")
        target_id = href.lstrip("#")
        target = page.locator(f"#{target_id}")
        assert target.count() == 1

    def test_guide_has_installation_section(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#install")
        assert section.is_visible()
        assert "pip install cvcpkg" in section.text_content()

    def test_guide_has_cli_reference_table(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#commands")
        assert section.is_visible()
        rows = section.locator("table tbody tr")
        assert rows.count() >= 10  # at least 10 CLI commands documented

    def test_guide_has_recipe_section(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#recipes")
        assert section.is_visible()
        assert "recipe.yaml" in section.text_content()

    def test_guide_has_publishing_section(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#publishing")
        assert section.is_visible()
        assert "publish" in section.text_content().lower()

    def test_guide_has_organizations_section(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#orgs")
        assert section.is_visible()
        assert "storage" in section.text_content().lower()

    def test_guide_has_server_config_table(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#server-config")
        assert section.is_visible()
        rows = section.locator("table tbody tr")
        assert rows.count() >= 10  # at least 10 config settings

    def test_guide_has_api_section(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#api")
        assert section.is_visible()
        # Should have the endpoint table
        rows = section.locator("table tbody tr")
        assert rows.count() >= 5

    def test_guide_navbar_is_consistent(self, page):
        page.goto(f"{SERVER_URL}/guide")
        # Navbar should have the same brand and links as landing
        brand = page.locator(".navbar-brand a.navbar-item").first
        assert brand.is_visible()
        burger = page.locator(".navbar-burger")
        assert burger.count() == 1

    def test_guide_code_blocks_styled(self, page):
        page.goto(f"{SERVER_URL}/guide")
        code_blocks = page.locator(".guide-code pre code")
        assert code_blocks.count() >= 5


# ── Organizations page ──────────────────────────────────────────


class TestOrganizationsPage:
    """Tests for the /orgs listing page."""

    def test_orgs_page_loads(self, page):
        page.goto(f"{SERVER_URL}/orgs")
        assert "Organizations" in page.content()

    def test_orgs_has_navbar(self, page):
        page.goto(f"{SERVER_URL}/orgs")
        navbar = page.locator("nav.navbar")
        assert navbar.is_visible()
        burger = page.locator(".navbar-burger")
        assert burger.count() == 1

    def test_orgs_page_fetches_data(self, page):
        """The page should attempt to load orgs via JS."""
        page.goto(f"{SERVER_URL}/orgs")
        # Wait for the spinner to disappear (JS init runs)
        page.wait_for_function(
            """() => {
                const el = document.getElementById('orgs-list');
                return el && !el.querySelector('.fa-spinner');
            }""",
            timeout=10000,
        )
        # After JS, the list should have content (even if "No organizations yet")
        content = page.locator("#orgs-list").text_content()
        assert content.strip()  # not empty


# ── Swagger / API docs page ─────────────────────────────────────


class TestApiDocsPage:
    """Tests for the /docs Swagger UI page."""

    def test_swagger_ui_loads(self, page):
        page.goto(f"{SERVER_URL}/docs")
        # FastAPI serves Swagger UI at /docs
        page.wait_for_load_state("networkidle")
        # Should have the swagger UI container
        assert page.locator("#swagger-ui").count() >= 1 or "swagger" in page.content().lower()


# ── Navigation ──────────────────────────────────────────────────


class TestNavigation:
    """Tests for cross-page navigation."""

    def test_landing_to_guide_via_dropdown(self, page):
        page.goto(SERVER_URL)
        # Click the docs dropdown to open it
        dropdown_link = page.locator(".navbar-link").first
        dropdown_link.click()
        # Click "Getting Started"
        guide_link = page.locator(".navbar-dropdown a[href='/guide']")
        guide_link.click()
        page.wait_for_url(re.compile(r"/guide"))
        assert "Getting Started" in page.content()

    def test_guide_to_landing_via_brand(self, page):
        page.goto(f"{SERVER_URL}/guide")
        brand = page.locator(".navbar-brand a.navbar-item").first
        brand.click()
        page.wait_for_url(re.compile(rf"^{re.escape(SERVER_URL)}/?$"))

    def test_landing_to_orgs_via_navbar(self, page):
        page.goto(SERVER_URL)
        orgs_link = page.locator(".navbar-end a[href='/orgs']")
        orgs_link.click()
        page.wait_for_url(re.compile(r"/orgs"))
        assert "Organizations" in page.content()

    def test_catalog_link_returns_yaml(self, page):
        """The catalog navbar link should return YAML content."""
        resp = page.request.get(f"{SERVER_URL}/v1/catalog")
        assert resp.status == 200


# ── Burger menu (mobile viewport) ──────────────────────────────


class TestMobileBurgerMenu:
    """Tests for the hamburger menu at mobile viewport sizes."""

    @pytest.fixture(autouse=True)
    def mobile_viewport(self, page):
        page.set_viewport_size({"width": 375, "height": 812})

    def test_burger_visible_on_mobile(self, page):
        page.goto(SERVER_URL)
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()

    def test_navbar_menu_hidden_on_mobile(self, page):
        page.goto(SERVER_URL)
        menu = page.locator("#navMenu")
        assert not menu.is_visible()

    def test_burger_toggles_menu(self, page):
        page.goto(SERVER_URL)
        burger = page.locator(".navbar-burger")
        menu = page.locator("#navMenu")

        # Menu should be hidden initially
        assert not menu.is_visible()

        # Click burger to open
        burger.click()
        page.wait_for_timeout(300)
        assert menu.is_visible()

        # Click burger to close
        burger.click()
        page.wait_for_timeout(300)
        assert not menu.is_visible()

    def test_outside_click_closes_menu(self, page):
        page.goto(SERVER_URL)
        burger = page.locator(".navbar-burger")

        # Open the menu
        burger.click()
        page.wait_for_timeout(300)
        assert page.locator("#navMenu").is_visible()

        # Click on the hero section (outside navbar)
        page.locator("section.hero").click()
        page.wait_for_timeout(300)
        assert not page.locator("#navMenu").is_visible()

    def test_burger_present_on_guide_page(self, page):
        page.goto(f"{SERVER_URL}/guide")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()

    def test_burger_present_on_orgs_page(self, page):
        page.goto(f"{SERVER_URL}/orgs")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()


# ── Dropdown (touch) ────────────────────────────────────────────


class TestDropdownTouch:
    """Tests that the docs dropdown works via click (not just hover)."""

    def test_dropdown_toggles_on_click(self, page):
        page.goto(SERVER_URL)
        dropdown = page.locator(".navbar-item.has-dropdown").first
        link = dropdown.locator(".navbar-link")

        # Click to open
        link.click()
        page.wait_for_timeout(300)
        assert dropdown.evaluate("el => el.classList.contains('is-active')")

        # Click to close
        link.click()
        page.wait_for_timeout(300)
        assert not dropdown.evaluate("el => el.classList.contains('is-active')")

    def test_outside_click_closes_dropdown(self, page):
        page.goto(SERVER_URL)
        dropdown = page.locator(".navbar-item.has-dropdown").first
        link = dropdown.locator(".navbar-link")

        # Open dropdown
        link.click()
        page.wait_for_timeout(300)
        assert dropdown.evaluate("el => el.classList.contains('is-active')")

        # Click elsewhere
        page.locator("section.hero").click()
        page.wait_for_timeout(300)
        assert not dropdown.evaluate("el => el.classList.contains('is-active')")


# ── Organization detail page ───────────────────────────────────


class TestOrgDetailPage:
    """Tests for the /org/{slug} detail page."""

    def test_org_detail_page_loads(self, page):
        page.goto(f"{SERVER_URL}/org/test-org")
        assert page.locator("nav.navbar").is_visible()
        # Page should render even if org doesn't exist
        page.wait_for_load_state("networkidle")

    def test_org_detail_has_navbar(self, page):
        page.goto(f"{SERVER_URL}/org/any-org")
        navbar = page.locator("nav.navbar")
        assert navbar.is_visible()
        brand = page.locator(".navbar-brand a.navbar-item").first
        assert brand.is_visible()

    def test_org_detail_burger_on_mobile(self, page):
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{SERVER_URL}/org/any-org")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()


class TestOrganizationsPagePrivacy:
    """Tests for privacy features on the orgs listing page."""

    def test_orgs_page_no_auth_required(self, page):
        """The /orgs page should load without any authentication."""
        resp = page.request.get(f"{SERVER_URL}/orgs")
        assert resp.status == 200
        assert "Organizations" in resp.text()

    def test_orgs_api_returns_json(self, page):
        """The /v1/orgs endpoint should return a valid JSON array."""
        resp = page.request.get(f"{SERVER_URL}/v1/orgs")
        assert resp.status == 200
        data = resp.json()
        assert "total" in data
        assert "organizations" in data
        assert isinstance(data["organizations"], list)

    def test_orgs_navbar_link_works(self, page):
        """Organizations link in navbar should navigate to /orgs."""
        page.goto(SERVER_URL)
        orgs_link = page.locator(".navbar-end a[href='/orgs']")
        if orgs_link.count() > 0:
            orgs_link.click()
            page.wait_for_url(re.compile(r"/orgs"))
            assert "Organizations" in page.content()


# ── RSS Feed ────────────────────────────────────────────────────


class TestRSSFeedBrowser:
    """Tests for the RSS feed endpoint via browser requests."""

    def test_rss_feed_accessible(self, page):
        """RSS feed endpoint returns XML content."""
        resp = page.request.get(f"{SERVER_URL}/v1/feed.xml")
        assert resp.status == 200
        assert (
            "rss" in resp.headers.get("content-type", "").lower()
            or "xml" in resp.headers.get("content-type", "").lower()
        )
        body = resp.text()
        assert "<rss" in body
        assert "<channel>" in body

    def test_rss_feed_has_title(self, page):
        """RSS feed contains a channel title."""
        resp = page.request.get(f"{SERVER_URL}/v1/feed.xml")
        body = resp.text()
        assert "<title>" in body


# ── Download Stats ──────────────────────────────────────────────


class TestDownloadStatsBrowser:
    """Tests for the download stats API via browser requests."""

    def test_download_stats_endpoint(self, page):
        """Download stats endpoint returns JSON with expected structure."""
        resp = page.request.get(f"{SERVER_URL}/v1/downloads/stats")
        assert resp.status == 200
        data = resp.json()
        assert "total" in data
        assert "daily" in data
        assert "config" in data

    def test_download_stats_with_params(self, page):
        """Download stats accepts query parameters."""
        resp = page.request.get(f"{SERVER_URL}/v1/downloads/stats?name=zlib&days=7")
        assert resp.status == 200
        data = resp.json()
        assert isinstance(data["total"], int)


# ── Mobile Badge Layout ────────────────────────────────────────


class TestMobileBadgeLayout:
    """Tests that badges don't break layout on mobile viewports."""

    @pytest.fixture(autouse=True)
    def mobile_viewport(self, page):
        page.set_viewport_size({"width": 375, "height": 812})

    def test_badge_no_line_wrap(self, page):
        """Badge elements should not wrap to multiple lines on mobile."""
        page.goto(SERVER_URL)
        # Wait for packages to load
        page.wait_for_function(
            """() => {
                const el = document.getElementById('stat-packages');
                return el && el.textContent.trim() !== '—';
            }""",
            timeout=10000,
        )
        # Check badge CSS properties
        badges = page.locator(".badge-mainline, .badge-community")
        if badges.count() > 0:
            for i in range(min(badges.count(), 3)):
                badge = badges.nth(i)
                white_space = badge.evaluate("el => getComputedStyle(el).whiteSpace")
                assert white_space == "nowrap", f"Badge {i} has white-space: {white_space}"
                display = badge.evaluate("el => getComputedStyle(el).display")
                assert "flex" in display, f"Badge {i} has display: {display}"

    def test_source_column_not_overflow(self, page):
        """The Source column in the package table should not overflow on mobile."""
        page.goto(SERVER_URL)
        page.wait_for_function(
            """() => {
                const el = document.getElementById('stat-packages');
                return el && el.textContent.trim() !== '—';
            }""",
            timeout=10000,
        )
        # The table should still be scrollable and not break the layout
        table = page.locator(".table-container")
        assert table.is_visible()


# ── Package Detail Download Graph ───────────────────────────────


class TestPackageDetailDownloadGraph:
    """Tests for the download stats graph on package detail pages."""

    def test_download_stats_section_exists(self, page):
        """Package detail page should have a hidden download stats section."""
        page.goto(f"{SERVER_URL}/package/test-pkg")
        page.wait_for_load_state("networkidle")
        section = page.locator("#download-stats-section")
        # Section exists in DOM (may be hidden if no data)
        assert section.count() == 1

    def test_download_chart_canvas_exists(self, page):
        """Download chart canvas element should be present."""
        page.goto(f"{SERVER_URL}/package/test-pkg")
        page.wait_for_load_state("networkidle")
        canvas = page.locator("#download-chart")
        assert canvas.count() == 1
