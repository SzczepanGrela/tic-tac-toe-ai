import json
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


def test_nine_by_nine_local_game_detects_an_off_center_win(page, live_server_url):
    page.goto(live_server_url)
    page.get_by_role("button", name="Local players").click()
    page.locator("#board-size").select_option("9")

    expect(page.locator("#win-length")).to_have_value("5")
    expect(page.locator(".cell")).to_have_count(81)
    page.locator(".cell").first.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    expect(page.locator(".cell").nth(10)).to_be_focused()
    for index in (12, 0, 13, 1, 14, 3, 15, 4, 16):
        page.locator(".cell").nth(index).click()

    expect(page.get_by_role("status")).to_have_text("Player X wins!")
    expect(page.locator(".winning-line.row-1")).to_be_visible()


def test_larger_board_disables_classic_only_agents(page, live_server_url):
    page.goto(live_server_url)
    expect(page.locator("#board-size option")).to_have_count(3)
    expect(page.locator("#board-size option")).to_have_text(["3×3", "5×5", "9×9"])
    page.locator("#board-size").select_option("5")

    expect(page.locator("#human-agent option[value=dqn]")).to_have_attribute("disabled", "")
    expect(page.locator("#human-agent option[value=random]")).not_to_have_attribute("disabled", "")
    expect(page.locator("#human-agent option[value=rules]")).not_to_have_attribute("disabled", "")
    expect(page.locator("#human-agent")).to_have_value("random")
    expect(page.locator("#algorithm-note")).to_contain_text("Available for these rules: Random, Rules.")


def test_previous_ai_response_cannot_change_a_reset_board(page, live_server_url):
    page.add_init_script("""
        Math.random = () => 0.9;
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (url, options) => {
            if (url !== '/api/move') return nativeFetch(url, options);
            return new Promise(resolve => {
                window.resolvePendingAi = () => resolve(new Response(
                    JSON.stringify({move: {row: 0, column: 0, player: 1}}),
                    {status: 200, headers: {'Content-Type': 'application/json'}},
                ));
            });
        };
    """)
    page.goto(live_server_url)
    expect(page.get_by_role("status")).to_have_text("AI is thinking…")

    page.get_by_role("button", name="Local players").click()
    page.evaluate("window.resolvePendingAi()")
    page.wait_for_timeout(50)

    expect(page.locator(".cell.x, .cell.o")).to_have_count(0)
    expect(page.get_by_role("status")).to_have_text("Player X's turn")


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


def test_larger_ai_series_uses_incremental_moves_and_replays(page, live_server_url):
    requests = []

    def choose_first_legal(route):
        payload = json.loads(route.request.post_data)
        requests.append(payload)
        board = payload["board"]
        player = 1 if sum(value != 0 for row in board for value in row) % 2 == 0 else -1
        row, column = next(
            (row_index, column_index)
            for row_index, row_values in enumerate(board)
            for column_index, value in enumerate(row_values)
            if value == 0
        )
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"move": {"row": row, "column": column, "player": player}}),
        )

    page.add_init_script("Math.random = () => 0")
    page.route("**/api/move", choose_first_legal)
    page.goto(live_server_url)
    page.get_by_role("button", name="AI vs AI").click()
    page.locator("#board-size").select_option("5")
    page.locator("#x-agent").select_option("random")
    page.locator("#o-agent").select_option("rules")
    page.locator("#series-count").select_option("1")
    page.locator("#seed").fill("123")
    page.get_by_role("button", name="Run series").click()

    page.locator("#scoreboard:not(.hidden)").wait_for()
    expect(page.locator("#series-meta")).to_contain_text("5×5, 5 in a row")
    expect(page.locator(".cell")).to_have_count(25)
    assert requests
    assert all(request["board_size"] == 5 for request in requests)
    assert all(request["win_length"] == 5 for request in requests)


def test_larger_series_pause_and_step_control_move_requests(page, live_server_url):
    page.add_init_script("""
        Math.random = () => 0;
        window.moveRequests = 0;
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (url, options) => {
            if (url !== '/api/move') return nativeFetch(url, options);
            const payload = JSON.parse(options.body);
            const board = payload.board;
            const marks = board.flat().filter(Boolean).length;
            const index = board.flat().findIndex(value => value === 0);
            const size = payload.board_size;
            window.moveRequests++;
            return new Promise(resolve => setTimeout(() => resolve(new Response(
                JSON.stringify({move: {
                    row: Math.floor(index / size),
                    column: index % size,
                    player: marks % 2 === 0 ? 1 : -1,
                }}),
                {status: 200, headers: {'Content-Type': 'application/json'}},
            )), 60));
        };
    """)
    page.goto(live_server_url)
    page.get_by_role("button", name="AI vs AI").click()
    page.locator("#board-size").select_option("5")
    page.locator("#x-agent").select_option("random")
    page.locator("#o-agent").select_option("rules")
    page.locator("#series-count").select_option("1")
    page.get_by_role("button", name="Run series").click()

    page.wait_for_function("window.moveRequests >= 1")
    page.get_by_role("button", name="Pause").click()
    requests_at_pause = page.evaluate("window.moveRequests")
    page.wait_for_timeout(180)
    assert page.evaluate("window.moveRequests") == requests_at_pause
    expect(page.get_by_role("status")).to_have_text("Series paused")

    page.get_by_role("button", name="Step").click()
    page.wait_for_function(f"window.moveRequests === {requests_at_pause + 1}")
    page.wait_for_timeout(100)
    assert page.evaluate("window.moveRequests") == requests_at_pause + 1

    page.get_by_role("button", name="Resume").click()
    page.locator("#scoreboard:not(.hidden)").wait_for()


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
