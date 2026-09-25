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


@pytest.mark.parametrize("width", [320, 375])
def test_nine_by_nine_board_fits_a_phone_and_settings_can_collapse(page, live_server_url, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(live_server_url)
    page.get_by_role("button", name="Local players").click()

    toggle = page.locator("#settings-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#board-size")).to_be_hidden()
    toggle.click()
    page.locator("#board-size").select_option("9")
    expect(page.locator(".cell")).to_have_count(81)
    toggle.click()
    expect(page.locator("#board-size")).to_be_hidden()
    expect(page.locator("#algorithm-note")).to_be_visible()

    dimensions = page.evaluate("""() => {
        const board = document.querySelector('#board');
        const viewport = document.querySelector('.board-scroll');
        const cell = document.querySelector('.cell');
        return {
            pageWidth: document.documentElement.scrollWidth,
            boardWidth: board.getBoundingClientRect().width,
            viewportWidth: viewport.getBoundingClientRect().width,
            cellWidth: cell.getBoundingClientRect().width,
        };
    }""")
    assert dimensions["pageWidth"] <= width
    assert dimensions["boardWidth"] <= dimensions["viewportWidth"]
    assert dimensions["cellWidth"] >= 24


def test_narrow_zoom_scrolls_only_the_board_without_shrinking_cells(page, live_server_url):
    page.set_viewport_size({"width": 220, "height": 800})
    page.goto(live_server_url)
    page.get_by_role("button", name="Local players").click()
    page.locator("#settings-toggle").click()
    page.locator("#board-size").select_option("9")
    expect(page.locator(".cell")).to_have_count(81)

    dimensions = page.evaluate("""() => ({
        pageWidth: document.documentElement.scrollWidth,
        boardWidth: document.querySelector('#board').getBoundingClientRect().width,
        viewportWidth: document.querySelector('.board-scroll').getBoundingClientRect().width,
        cellWidth: document.querySelector('.cell').getBoundingClientRect().width,
    })""")
    assert dimensions["pageWidth"] <= 220
    assert dimensions["boardWidth"] > dimensions["viewportWidth"]
    assert dimensions["cellWidth"] >= 24


def test_board_keyboard_navigation_retains_focus_after_a_move(page, live_server_url):
    page.goto(live_server_url)
    page.get_by_role("button", name="Local players").click()
    cells = page.locator(".cell")
    cells.first.focus()
    page.keyboard.press("ArrowRight")
    expect(cells.nth(1)).to_be_focused()
    page.keyboard.press("Enter")
    expect(cells.nth(1)).to_be_focused()
    expect(cells.nth(1)).to_have_attribute("aria-label", "Row 1, column 2: X")
    page.keyboard.press("ArrowLeft")
    expect(cells.first).to_be_focused()
    page.keyboard.press("Enter")
    expect(cells.first).to_have_text("O")


def test_phone_opens_series_settings_and_explains_unavailable_agents(page, live_server_url):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(live_server_url)
    toggle = page.locator("#settings-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")

    page.get_by_role("button", name="AI vs AI").click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#x-agent")).to_be_visible()
    page.locator("#board-size").select_option("5")
    availability = page.locator("#agent-availability")
    expect(availability).to_be_visible()
    availability.locator("summary").click()
    expect(page.locator("#unavailable-agents")).to_contain_text(
        "DQN: Available only for classic 3×3."
    )


def test_larger_board_disables_classic_only_agents(page, live_server_url):
    page.goto(live_server_url)
    expect(page.locator("#board-size option")).to_have_count(3)
    expect(page.locator("#board-size option")).to_have_text(["3×3", "5×5", "9×9"])
    page.locator("#board-size").select_option("5")

    expect(page.locator("#human-agent option[value=dqn]")).to_have_attribute("disabled", "")
    expect(page.locator("#human-agent option[value=random]")).not_to_have_attribute("disabled", "")
    expect(page.locator("#human-agent option[value=rules]")).not_to_have_attribute("disabled", "")
    expect(page.locator("#human-agent")).to_have_value("random")
    expect(page.locator("#algorithm-note")).to_contain_text("Available for these rules: Random, Rules, MCTS.")


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


@pytest.mark.parametrize("ending", ["complete", "failure", "stop"])
def test_jev_classic_series_is_incremental_and_preserves_completed_games(page, live_server_url, ending):
    def capabilities(route):
        response = route.fetch()
        payload = response.json()
        jev = next(agent for agent in payload["agents"] if agent["id"] == "jev")
        jev.update(available=True, reason=None)
        route.fulfill(response=response, json=payload)

    page.route("**/api/agents?*", capabilities)
    page.add_init_script("""
        Math.random = () => 0;
        window.moveRequests = 0;
        window.batchRequests = 0;
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (url, options) => {
            if (url === '/api/matches') window.batchRequests++;
            if (url !== '/api/move') return nativeFetch(url, options);
            window.moveRequests++;
            if (window.moveRequests > 7 && window.seriesEnding === 'failure') {
                return new Response(JSON.stringify({detail: {code: 'budget_exhausted'}}), {status: 503});
            }
            if (window.moveRequests > 7 && window.seriesEnding === 'stop') {
                return new Promise((resolve, reject) => options.signal.addEventListener(
                    'abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true}));
            }
            const board = JSON.parse(options.body).board.flat();
            const index = board.findIndex(value => value === 0);
            return new Response(JSON.stringify({move: {
                row: Math.floor(index / 3), column: index % 3,
                player: board.filter(Boolean).length % 2 === 0 ? 1 : -1,
            }, metadata: {seed_reproducible: false, model: 'jev-1.13.0'}}), {status: 200});
        };
    """)
    page.goto(live_server_url)
    page.evaluate("ending => window.seriesEnding = ending", ending)
    page.get_by_role("button", name="AI vs AI").click()
    page.locator("#x-agent").select_option("jev")
    page.locator("#o-agent").select_option("jev")
    page.locator("#series-count").select_option("3")
    page.get_by_role("button", name="Run series").click()
    if ending == "stop":
        page.wait_for_function("window.moveRequests === 8")
        page.locator("#stop-series").click()
    expect(page.locator("#scoreboard")).to_be_visible()
    assert page.evaluate("window.batchRequests") == 0
    expect(page.locator("#x-wins")).to_have_text("3" if ending == "complete" else "1")
    expect(page.locator("#o-wins")).to_have_text("0")
    expect(page.locator("#draws")).to_have_text("0")
    assert page.evaluate("replay.data.games[0].moves[0].metadata.model") == "jev-1.13.0"
    if ending != "complete":
        assert page.evaluate("replay.data.games.length") == 1
        assert page.evaluate("replay.data.interrupted_game.game") == 2
        expect(page.locator("#stop-series")).to_be_hidden()
        page.get_by_role("button", name="Step", exact=True).click()
        expect(page.locator(".cell.x")).to_have_count(1)
