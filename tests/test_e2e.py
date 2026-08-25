import os

import pytest

if os.getenv("RUN_E2E") != "1":
    pytest.skip("Playwright browser test is opt-in locally", allow_module_level=True)

from playwright.sync_api import expect


def test_initial_view_contains_only_human_vs_ai_controls(page, live_server_url):
    page.goto(live_server_url)
    expect(page.locator("#human-agent-field")).to_be_visible()
    expect(page.locator(".ai-field")).to_have_count(4)
    for field in page.locator(".ai-field").all():
        expect(field).to_be_hidden()


def test_winning_line_appears_for_completed_game(page, live_server_url):
    page.goto(live_server_url)
    page.get_by_role("button", name="Local players").click()
    for index in (0, 3, 1, 4, 2):
        page.locator(".cell").nth(index).click()
    expect(page.locator(".winning-line.row-0")).to_be_visible()


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


def test_ai_vs_ai_displays_non_draw_score(page, live_server_url):
    page.goto(live_server_url)
    page.get_by_role("button", name="AI vs AI").click()
    page.locator("#x-agent").select_option("random")
    page.locator("#o-agent").select_option("minimax")
    page.locator("#series-count").select_option("10")
    page.locator("#seed").fill("42")
    page.get_by_role("button", name="Run series").click()

    page.locator("#scoreboard:not(.hidden)").wait_for()
    expect(page.locator("#x-wins")).to_have_text("0")
    expect(page.locator("#o-wins")).to_have_text("9")
    expect(page.locator("#draws")).to_have_text("1")


def test_new_human_game_enables_board_after_side_changes(page, live_server_url):
    page.goto(live_server_url)
    page.evaluate("Math.random = () => 0.9")
    page.get_by_role("button", name="New game").click()
    expect(page.locator(".cell.x")).to_have_count(1)

    page.evaluate("Math.random = () => 0")
    page.get_by_role("button", name="New game").click()

    expect(page.locator(".cell").first).to_be_enabled()
    expect(page.locator(".cell.x, .cell.o")).to_have_count(0)


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
