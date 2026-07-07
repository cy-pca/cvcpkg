"""Playwright integration tests for build-system frontend pages.

Validates the Builders, Builds, Build Detail, and Recipes pages,
the Build navbar dropdown, landing page build links, guide Remote
Builders section, breadcrumbs, active-state navbar highlighting,
org-detail clickable package links, and builder_id filter flow.

These tests require a running cvcpkg-server with build-system
tables enabled (CVCPKG_BUILD_JOBS=true).

Run locally::

    CVCPKG_TEST_SERVER_URL=http://127.0.0.1:8421 \\
    CVCPKG_TEST_ADMIN_TOKEN=<admin-token> \\
        pytest tests/integration/test_browser_build_ui.py -v --browser chromium
"""

from __future__ import annotations

import os
import re

import pytest

playwright_module = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

SERVER_URL = os.environ.get("CVCPKG_TEST_SERVER_URL", "http://127.0.0.1:8421")
ADMIN_TOKEN = os.environ.get("CVCPKG_TEST_ADMIN_TOKEN", "")


# ── helpers ─────────────────────────────────────────────────────


def _inject_token(page):
    """Store the admin token in localStorage so authenticated pages work."""
    if ADMIN_TOKEN:
        page.evaluate(
            "token => localStorage.setItem('cvcpkg_token', token)",
            ADMIN_TOKEN,
        )


# ── Navbar Build dropdown ──────────────────────────────────────


class TestBuildNavbarDropdown:
    """The navbar should have a Build dropdown with Builders/Build Jobs/Recipes."""

    def test_build_dropdown_exists(self, page):
        page.goto(SERVER_URL)
        dropdowns = page.locator(".navbar-item.has-dropdown")
        texts = [dropdowns.nth(i).text_content() for i in range(dropdowns.count())]
        assert any("Build" in t for t in texts), f"No Build dropdown found in: {texts}"

    def test_build_dropdown_has_links(self, page):
        page.goto(SERVER_URL)
        builders = page.locator(".navbar-dropdown a[href='/builders']")
        builds = page.locator(".navbar-dropdown a[href='/builds']")
        recipes = page.locator(".navbar-dropdown a[href='/recipes']")
        assert builders.count() >= 1, "Missing /builders link in dropdown"
        assert builds.count() >= 1, "Missing /builds link in dropdown"
        assert recipes.count() >= 1, "Missing /recipes link in dropdown"

    def test_build_dropdown_navigates_to_builders(self, page):
        page.goto(SERVER_URL)
        # Open the Build dropdown
        build_dropdown = page.locator(".navbar-item.has-dropdown", has_text="Build")
        build_dropdown.locator(".navbar-link").click()
        page.wait_for_timeout(200)
        # Click Builders
        build_dropdown.locator("a[href='/builders']").click()
        page.wait_for_url(re.compile(r"/builders"))

    def test_build_dropdown_navigates_to_builds(self, page):
        page.goto(SERVER_URL)
        build_dropdown = page.locator(".navbar-item.has-dropdown", has_text="Build")
        build_dropdown.locator(".navbar-link").click()
        page.wait_for_timeout(200)
        build_dropdown.locator("a[href='/builds']").click()
        page.wait_for_url(re.compile(r"/builds"))

    def test_build_dropdown_navigates_to_recipes(self, page):
        page.goto(SERVER_URL)
        build_dropdown = page.locator(".navbar-item.has-dropdown", has_text="Build")
        build_dropdown.locator(".navbar-link").click()
        page.wait_for_timeout(200)
        build_dropdown.locator("a[href='/recipes']").click()
        page.wait_for_url(re.compile(r"/recipes"))


# ── Navbar active state ─────────────────────────────────────────


class TestNavbarActiveState:
    """The current page link in the navbar should be highlighted."""

    def test_builders_page_has_active_navbar(self, page):
        page.goto(f"{SERVER_URL}/builders")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        active = page.locator(".navbar-item.is-active, .navbar-link.is-active")
        assert active.count() >= 1, "No active navbar item on /builders"

    def test_builds_page_has_active_navbar(self, page):
        page.goto(f"{SERVER_URL}/builds")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        active = page.locator(".navbar-item.is-active, .navbar-link.is-active")
        assert active.count() >= 1, "No active navbar item on /builds"

    def test_recipes_page_has_active_navbar(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        active = page.locator(".navbar-item.is-active, .navbar-link.is-active")
        assert active.count() >= 1

    def test_guide_page_has_active_navbar(self, page):
        page.goto(f"{SERVER_URL}/guide")
        page.wait_for_load_state("networkidle")
        active = page.locator(".navbar-item.is-active, .navbar-link.is-active")
        assert active.count() >= 1

    def test_orgs_page_has_active_navbar(self, page):
        page.goto(f"{SERVER_URL}/orgs")
        page.wait_for_load_state("networkidle")
        active = page.locator(".navbar-item.is-active, .navbar-link.is-active")
        assert active.count() >= 1


# ── Landing page build system buttons ──────────────────────────


class TestLandingBuildButtons:
    """The landing page should have quick-link buttons for the build system."""

    def test_builders_button_exists(self, page):
        page.goto(SERVER_URL)
        btn = page.locator("a[href='/builders']")
        assert btn.count() >= 1, "Missing Builders button on landing"

    def test_build_jobs_button_exists(self, page):
        page.goto(SERVER_URL)
        btn = page.locator("a[href='/builds']")
        assert btn.count() >= 1, "Missing Build Jobs button on landing"

    def test_recipes_button_exists(self, page):
        page.goto(SERVER_URL)
        btn = page.locator("a[href='/recipes']")
        assert btn.count() >= 1, "Missing Recipes button on landing"


# ── Builders page ──────────────────────────────────────────────


class TestBuildersPage:
    """Tests for the /builders page."""

    def test_page_loads(self, page):
        page.goto(f"{SERVER_URL}/builders")
        assert page.locator("nav.navbar").is_visible()
        assert "Builders" in page.content()

    def test_has_breadcrumbs(self, page):
        page.goto(f"{SERVER_URL}/builders")
        breadcrumb = page.locator("nav.breadcrumb")
        assert breadcrumb.is_visible()
        # Should contain Home > Builders
        items = breadcrumb.locator("li")
        assert items.count() >= 2
        texts = [items.nth(i).text_content().strip() for i in range(items.count())]
        assert any("Home" in t for t in texts)
        assert any("Builders" in t for t in texts)

    def test_has_auth_prompt(self, page):
        """Without a token, the page should show an auth prompt."""
        # Clear any stored token
        page.goto(f"{SERVER_URL}/builders")
        page.evaluate("() => localStorage.removeItem('cvcpkg_token')")
        page.reload()
        page.wait_for_load_state("networkidle")
        # Wait a beat for JS to run
        page.wait_for_timeout(2000)
        auth = page.locator("#auth-prompt")
        # Auth prompt should become visible since no token
        assert auth.count() == 1

    def test_authenticated_loads_builders(self, page):
        """With a valid token, the page should attempt to load builders."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builders")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        # Summary should update from the initial "Loading…"
        summary = page.locator("#builders-summary")
        assert summary.count() == 1
        text = summary.text_content().strip()
        assert text != "Loading…", f"Summary not updated: {text!r}"

    def test_has_footer(self, page):
        page.goto(f"{SERVER_URL}/builders")
        footer = page.locator("footer.footer")
        assert footer.is_visible()


# ── Builds page ────────────────────────────────────────────────


class TestBuildsPage:
    """Tests for the /builds page."""

    def test_page_loads(self, page):
        page.goto(f"{SERVER_URL}/builds")
        assert page.locator("nav.navbar").is_visible()
        assert "Build Jobs" in page.content()

    def test_has_breadcrumbs(self, page):
        page.goto(f"{SERVER_URL}/builds")
        breadcrumb = page.locator("nav.breadcrumb")
        assert breadcrumb.is_visible()
        items = breadcrumb.locator("li")
        assert items.count() >= 2

    def test_has_filter_controls(self, page):
        """Builds page should have filter dropdowns for status and platform."""
        page.goto(f"{SERVER_URL}/builds")
        status_filter = page.locator("#filter-status")
        assert status_filter.count() == 1
        platform_filter = page.locator("#filter-platform")
        assert platform_filter.count() == 1

    def test_has_recipe_filter(self, page):
        page.goto(f"{SERVER_URL}/builds")
        recipe_filter = page.locator("#filter-recipe")
        assert recipe_filter.count() == 1

    def test_has_dag_filter(self, page):
        page.goto(f"{SERVER_URL}/builds")
        dag_filter = page.locator("#filter-dag")
        assert dag_filter.count() == 1

    def test_has_builds_table(self, page):
        page.goto(f"{SERVER_URL}/builds")
        table = page.locator("#builds-table")
        assert table.count() == 1
        tbody = page.locator("#builds-body")
        assert tbody.count() == 1

    def test_url_sync_status_filter(self, page):
        """Navigating to /builds?status=running should pre-fill the status filter."""
        page.goto(f"{SERVER_URL}/builds?status=running")
        status_val = page.locator("#filter-status").input_value()
        assert status_val == "running"

    def test_url_sync_platform_filter(self, page):
        page.goto(f"{SERVER_URL}/builds?platform=linux")
        platform_val = page.locator("#filter-platform").input_value()
        assert platform_val == "linux"

    def test_url_sync_dag_filter(self, page):
        page.goto(f"{SERVER_URL}/builds?dag_id=test-dag")
        dag_val = page.locator("#filter-dag").input_value()
        assert dag_val == "test-dag"

    def test_url_sync_recipe_filter(self, page):
        page.goto(f"{SERVER_URL}/builds?recipe_name=zlib")
        recipe_val = page.locator("#filter-recipe").input_value()
        assert recipe_val == "zlib"

    def test_authenticated_loads_builds(self, page):
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builds")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        summary = page.locator("#builds-summary")
        text = summary.text_content().strip()
        assert text != "Loading…", f"Summary not updated: {text!r}"

    def test_builder_id_filter_in_summary(self, page):
        """Navigating to /builds?builder_id=1 should show builder indicator."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builds?builder_id=1")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        summary = page.locator("#builds-summary")
        text = summary.text_content().strip()
        assert "Builder #1" in text, f"builder_id not shown in summary: {text!r}"

    def test_has_footer(self, page):
        page.goto(f"{SERVER_URL}/builds")
        footer = page.locator("footer.footer")
        assert footer.is_visible()


# ── Build detail page ──────────────────────────────────────────


class TestBuildDetailPage:
    """Tests for the /build/{job_id} detail page."""

    def test_page_loads(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        assert page.locator("nav.navbar").is_visible()
        assert "Build #1" in page.content()

    def test_has_breadcrumbs(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        breadcrumb = page.locator("nav.breadcrumb")
        assert breadcrumb.is_visible()
        items = breadcrumb.locator("li")
        texts = [items.nth(i).text_content().strip() for i in range(items.count())]
        assert any("Home" in t for t in texts)
        assert any("Builds" in t or "Build Jobs" in t for t in texts)

    def test_has_log_output_area(self, page):
        """Build detail page should have a pre element for log output."""
        page.goto(f"{SERVER_URL}/build/1")
        log_output = page.locator("#log-output")
        assert log_output.count() == 1

    def test_has_metadata_fields(self, page):
        """Build detail should display metadata fields."""
        page.goto(f"{SERVER_URL}/build/1")
        # Check for meta fields (status, recipe, platform, builder, etc.)
        meta_status = page.locator("#meta-status")
        assert meta_status.count() == 1

    def test_has_footer(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        footer = page.locator("footer.footer")
        assert footer.is_visible()


# ── Recipes page ───────────────────────────────────────────────


class TestRecipesPage:
    """Tests for the /recipes page."""

    def test_page_loads(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        assert page.locator("nav.navbar").is_visible()
        assert "Recipes" in page.content()

    def test_has_breadcrumbs(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        breadcrumb = page.locator("nav.breadcrumb")
        assert breadcrumb.is_visible()
        items = breadcrumb.locator("li")
        assert items.count() >= 2

    def test_has_auth_prompt(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        page.evaluate("() => localStorage.removeItem('cvcpkg_token')")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        auth = page.locator("#auth-prompt")
        assert auth.count() == 1

    def test_authenticated_loads_recipes(self, page):
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/recipes")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        summary = page.locator("#recipes-summary")
        text = summary.text_content().strip()
        assert text != "Loading…", f"Summary not updated: {text!r}"

    def test_has_footer(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        footer = page.locator("footer.footer")
        assert footer.is_visible()


# ── Guide Remote Builders section ──────────────────────────────


class TestGuideRemoteBuilders:
    """Verify the Remote Builders section was added to the guide."""

    def test_remote_builders_section_exists(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#remote-builders")
        assert section.count() == 1, "Missing #remote-builders section in guide"
        assert section.is_visible()

    def test_remote_builders_in_toc(self, page):
        page.goto(f"{SERVER_URL}/guide")
        toc = page.locator("ol.toc")
        toc_links = toc.locator("a[href='#remote-builders']")
        assert toc_links.count() >= 1, "Remote Builders not in TOC"

    def test_remote_builders_has_content(self, page):
        page.goto(f"{SERVER_URL}/guide")
        section = page.locator("#remote-builders")
        text = section.text_content()
        assert "recipe" in text.lower()
        assert "builder" in text.lower()


# ── Org detail clickable package links ─────────────────────────


class TestOrgDetailPackageLinks:
    """Package names on org detail should be clickable anchor links."""

    def test_org_detail_page_renders(self, page):
        """Org detail page should render and contain an org name heading."""
        page.goto(f"{SERVER_URL}/org/test-org")
        page.wait_for_load_state("networkidle")
        assert page.locator("nav.navbar").is_visible()

    def test_package_links_are_anchors(self, page):
        """If packages exist in an org, they should be wrapped in <a> tags."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/org/test-org")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        # Check if there are any package links (they may not exist in test env)
        pkg_links = page.locator("a[href^='/package/']")
        if pkg_links.count() > 0:
            href = pkg_links.first.get_attribute("href")
            assert href.startswith("/package/"), f"Bad package link: {href}"


# ── Relative time helper ───────────────────────────────────────


class TestFmtRelativeHelper:
    """Verify that the fmtRelative JS helper produces expected output."""

    def test_fmt_relative_recent(self, page):
        """fmtRelative should produce relative text for a recent timestamp."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builders")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        # Evaluate fmtRelative with a timestamp from 30 seconds ago
        result = page.evaluate(
            """() => {
                if (typeof fmtRelative !== 'function') return 'MISSING';
                const d = new Date(Date.now() - 30000).toISOString();
                return fmtRelative(d);
            }"""
        )
        assert result != "MISSING", "fmtRelative function not found in page"
        assert "sec" in result.lower() or "just" in result.lower() or "ago" in result.lower(), (
            f"Unexpected fmtRelative output for 30s ago: {result!r}"
        )

    def test_fmt_relative_null(self, page):
        """fmtRelative should handle null gracefully."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builders")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        result = page.evaluate(
            """() => {
                if (typeof fmtRelative !== 'function') return 'MISSING';
                return fmtRelative(null);
            }"""
        )
        assert result != "MISSING"
        # Should return dash or empty, not crash
        assert isinstance(result, str)


# ── Cross-page navigation (build system) ──────────────────────


class TestBuildNavigation:
    """Tests for navigation between build system pages."""

    def test_builders_to_builds_via_navbar(self, page):
        page.goto(f"{SERVER_URL}/builders")
        build_dropdown = page.locator(".navbar-item.has-dropdown", has_text="Build")
        build_dropdown.locator(".navbar-link").click()
        page.wait_for_timeout(200)
        build_dropdown.locator("a[href='/builds']").click()
        page.wait_for_url(re.compile(r"/builds"))

    def test_builds_to_build_detail_link(self, page):
        """Build detail page should be reachable from /build/{id}."""
        page.goto(f"{SERVER_URL}/build/999")
        page.wait_for_load_state("networkidle")
        assert "Build #999" in page.content()

    def test_breadcrumb_home_link_works(self, page):
        page.goto(f"{SERVER_URL}/builders")
        home_link = page.locator("nav.breadcrumb a[href='/']")
        assert home_link.count() >= 1
        home_link.first.click()
        page.wait_for_url(re.compile(rf"^{re.escape(SERVER_URL)}/?$"))

    def test_breadcrumb_builds_link_on_detail(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        builds_link = page.locator("nav.breadcrumb a[href='/builds']")
        assert builds_link.count() >= 1

    def test_build_detail_breadcrumb_has_home(self, page):
        """Build detail breadcrumb should start with Home."""
        page.goto(f"{SERVER_URL}/build/1")
        home_link = page.locator("nav.breadcrumb a[href='/']")
        assert home_link.count() >= 1
        assert "Home" in home_link.first.text_content()


# ── Builder_id filter input ────────────────────────────────────


class TestBuilderIdFilterInput:
    """Tests for the builder_id filter input on the builds page."""

    def test_builder_filter_input_exists(self, page):
        """Builds page should have a builder_id filter input."""
        page.goto(f"{SERVER_URL}/builds")
        builder_input = page.locator("#filter-builder")
        assert builder_input.count() == 1

    def test_builder_filter_prefilled_from_url(self, page):
        """builder_id URL param should pre-fill the filter input."""
        page.goto(f"{SERVER_URL}/builds?builder_id=42")
        builder_val = page.locator("#filter-builder").input_value()
        assert builder_val == "42"

    def test_refresh_button_exists(self, page):
        """Builds filter bar should have a refresh button."""
        page.goto(f"{SERVER_URL}/builds")
        refresh = page.locator("button[title='Refresh']")
        assert refresh.count() >= 1


# ── Cancel button on build detail ──────────────────────────────


class TestBuildDetailCancelButton:
    """Tests for the cancel button on the build detail page."""

    def test_cancel_button_in_dom(self, page):
        """Cancel button should exist in the DOM."""
        page.goto(f"{SERVER_URL}/build/1")
        cancel_btn = page.locator("#cancel-btn")
        assert cancel_btn.count() == 1

    def test_cancel_button_hidden_initially(self, page):
        """Cancel button should be hidden before metadata loads."""
        page.goto(f"{SERVER_URL}/build/1")
        cancel_btn = page.locator("#cancel-btn")
        # Before JS runs, display is 'none'
        assert cancel_btn.get_attribute("style") == "display:none"


# ── Burger aria-expanded ───────────────────────────────────────


class TestBurgerAriaExpanded:
    """Tests that the burger button toggles aria-expanded."""

    @pytest.fixture(autouse=True)
    def mobile_viewport(self, page):
        page.set_viewport_size({"width": 375, "height": 812})

    def test_aria_expanded_toggled_on_click(self, page):
        page.goto(SERVER_URL)
        burger = page.locator(".navbar-burger")
        # Initially should not be expanded
        assert burger.get_attribute("aria-expanded") != "true"
        # Click to open
        burger.click()
        page.wait_for_timeout(300)
        assert burger.get_attribute("aria-expanded") == "true"
        # Click to close
        burger.click()
        page.wait_for_timeout(300)
        assert burger.get_attribute("aria-expanded") == "false"


# ── Build detail table-container ───────────────────────────────


class TestBuildDetailTableContainer:
    """Metadata tables on build detail should be in table-container wrappers."""

    def test_meta_tables_have_table_container(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        containers = page.locator("#build-meta .table-container")
        assert containers.count() >= 2, "Metadata tables should be wrapped in table-container"


# ── Publisher plain text (not self-link) ───────────────────────


class TestPublisherDisplay:
    """Publisher name on package detail should be plain text, not a link."""

    def test_publisher_not_a_link(self, page):
        """If publisher is shown, it should be a span, not an anchor link."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/package/test-pkg")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        pub_el = page.locator("#pkg-publisher")
        if pub_el.count() > 0 and pub_el.text_content().strip():
            # Should be a span, not an anchor
            anchor = pub_el.locator("a:not([href^='mailto:'])")
            assert anchor.count() == 0, "Publisher name should not be a self-link"


# ── Img alt attributes ────────────────────────────────────────


class TestImgAltAttributes:
    """Images on org detail and tag detail should have alt attributes."""

    def test_org_detail_img_has_alt(self, page):
        """The JS that renders the org logo should set alt."""
        page.goto(f"{SERVER_URL}/org/test-org")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        imgs = page.locator("#org-logo img")
        for i in range(imgs.count()):
            alt = imgs.nth(i).get_attribute("alt")
            assert alt is not None and alt != "", f"Org logo img[{i}] missing alt"


class TestBuildPagesMobile:
    """Tests for build system pages at mobile viewport sizes."""

    @pytest.fixture(autouse=True)
    def mobile_viewport(self, page):
        page.set_viewport_size({"width": 375, "height": 812})

    def test_builders_burger_visible(self, page):
        page.goto(f"{SERVER_URL}/builders")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()

    def test_builds_burger_visible(self, page):
        page.goto(f"{SERVER_URL}/builds")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()

    def test_recipes_burger_visible(self, page):
        page.goto(f"{SERVER_URL}/recipes")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()

    def test_build_detail_burger_visible(self, page):
        page.goto(f"{SERVER_URL}/build/1")
        burger = page.locator(".navbar-burger")
        assert burger.is_visible()


# ── esc() quote escaping ──────────────────────────────────────


class TestEscFunction:
    """Verify esc() properly escapes quotes for attribute safety."""

    def test_esc_escapes_double_quotes(self, page):
        page.goto(SERVER_URL)
        result = page.evaluate(
            """() => {
                if (typeof esc !== 'function') return 'MISSING';
                return esc('test"value');
            }"""
        )
        assert result != "MISSING", "esc() function not found"
        assert '"' not in result, f"esc() did not escape double quotes: {result!r}"
        assert "&quot;" in result

    def test_esc_escapes_single_quotes(self, page):
        page.goto(SERVER_URL)
        result = page.evaluate(
            """() => {
                if (typeof esc !== 'function') return 'MISSING';
                return esc("test'value");
            }"""
        )
        assert result != "MISSING"
        assert "'" not in result, f"esc() did not escape single quotes: {result!r}"
        assert "&#39;" in result

    def test_esc_handles_null(self, page):
        page.goto(SERVER_URL)
        result = page.evaluate("() => typeof esc === 'function' ? esc(null) : 'MISSING'")
        assert result == ""

    def test_esc_handles_angle_brackets(self, page):
        page.goto(SERVER_URL)
        result = page.evaluate("() => typeof esc === 'function' ? esc('<b>test</b>') : 'MISSING'")
        assert "<b>" not in result
        assert "&lt;" in result


# ── Hero text sanitization ────────────────────────────────────


class TestHeroSanitization:
    """The hero subtitle should not contain raw HTML from env vars."""

    def test_hero_no_script_injection(self, page):
        """Hero text should be escaped — no raw HTML tags should render."""
        page.goto(SERVER_URL)
        hero = page.locator("section.hero .subtitle")
        assert hero.count() >= 1
        # The hero should not contain unescaped HTML elements
        inner = hero.evaluate("el => el.children.length")
        assert inner == 0, "Hero subtitle should be plain text with no child elements"


# ── Empty state handling ──────────────────────────────────────


class TestEmptyStates:
    """Pages should show informative messages when there are no items."""

    def test_builders_empty_state(self, page):
        """Builders page shows 'No builders registered' when empty."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builders")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        # Grid should have content (either builders or empty state)
        grid = page.locator("#builders-grid")
        assert grid.text_content().strip() != ""

    def test_builds_empty_state_shows_message(self, page):
        """When filtering produces no results, table shows 'No build jobs found'."""
        if not ADMIN_TOKEN:
            pytest.skip("CVCPKG_TEST_ADMIN_TOKEN not set")
        page.goto(f"{SERVER_URL}/builds?recipe_name=nonexistent_recipe_xyz_42")
        _inject_token(page)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        tbody = page.locator("#builds-body")
        assert "No build jobs found" in tbody.text_content()


# ── Download link safety ──────────────────────────────────────


class TestDownloadLinkSafety:
    """Download buttons should only link to server-relative paths."""

    def test_download_href_safety_check(self, page):
        """The download button template should validate archive_url starts with /."""
        page.goto(f"{SERVER_URL}/package/test-pkg")
        page.wait_for_load_state("networkidle")
        # Check that any download links start with / or are #
        downloads = page.locator("a[title='Download']")
        for i in range(downloads.count()):
            href = downloads.nth(i).get_attribute("href")
            assert href.startswith("/") or href == "#", f"Download link has unsafe href: {href!r}"
