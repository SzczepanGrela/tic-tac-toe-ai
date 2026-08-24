import os

import pytest

if os.getenv("RUN_E2E") != "1":
    pytest.skip("Playwright browser test is opt-in locally", allow_module_level=True)

from playwright.sync_api import expect


def test_ai_vs_ai_replay_controls(page, live_server_url):
    page.goto(live_server_url)
    page.get_by_role("button", name="AI vs AI").click()
    page.locator("#series-count").select_option("3")
    page.locator("#seed").fill("42")
    page.get_by_role("button", name="Run series").click()
    page.locator("#scoreboard:not(.hidden)").wait_for()
    expect(page.locator("#series-meta")).to_contain_text("Seed 42")
    page.get_by_role("button", name="Pause").click()
    page.get_by_role("button", name="Step").click()


def test_theme_and_language_are_saved_without_resetting_game(page, live_server_url):
    page.goto(live_server_url)
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    page.get_by_role("button", name="Local players").click()
    page.locator(".cell").first.click()
    expect(page.locator(".cell").first).to_have_text("X")

    page.locator("#lang-pl").click()
    expect(page.locator("html")).to_have_attribute("lang", "pl")
    expect(page.get_by_role("button", name="Nowa gra")).to_be_visible()
    expect(page.locator(".cell").first).to_have_text("X")

    page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    cookies = {cookie["name"]: cookie for cookie in page.context.cookies()}
    assert cookies["tictactoe_lang"]["value"] == "pl"
    assert cookies["tictactoe_theme"]["value"] == "dark"
    assert cookies["tictactoe_lang"]["sameSite"] == "Lax"

    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", "pl")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.get_by_role("button", name="Nowa gra")).to_be_visible()


def test_invalid_preferences_fall_back_to_defaults(page, live_server_url):
    page.context.add_cookies([
        {"name": "tictactoe_lang", "value": "xx", "url": live_server_url},
        {"name": "tictactoe_theme", "value": "neon", "url": live_server_url},
    ])
    page.goto(live_server_url)
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
