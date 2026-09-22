import asyncio
import json
import sys
from types import SimpleNamespace

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from ai.jev import JevError, JevService, PROMPT_VERSION
from game.state import GameRules, GameState
from web.jev_budget import BudgetLedger, MODEL, RESERVATION, MONTHLY_LIMIT, LedgerUnavailable


@pytest.fixture
def ledger(tmp_path):
    result = BudgetLedger.initialize(tmp_path / "usage.sqlite3")
    result.reconcile(0)
    return result


def response_for(payload, **overrides):
    choices = payload["questions"]["move"]["criteria"]
    selected = next(iter(choices))
    data = {"model": MODEL, "usage": {"input_tokens": 100, "output_tokens": 10},
            "answers": {"move": {"type": "choice", "choice": selected, "confidence": .1,
                                  "probabilities": {key: float(key == selected) for key in choices}}}}
    data.update(overrides)
    return data


def client_with_handler(handler):
    return AsyncTypeSafeClient(api_key="test-only-not-a-real-key", model=MODEL,
                               retry=RetryPolicy(max_retries=0), transport=httpx2.MockTransport(handler))


@pytest.mark.parametrize("size,length", [(n, k) for n in (3, 5, 9) for k in range(3, n + 1)])
def test_real_sdk_contract_and_accounting_for_every_variant(ledger, size, length):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == MODEL
        assert payload["state"]["board_size"] == size
        assert payload["state"]["marks_to_win"] == length
        assert payload["state"]["player_to_move"] == "X"
        assert payload["state"]["opponent"] == "O"
        assert payload["state"]["cell_symbols"]["."] == "An empty cell where the current player may move."
        assert payload["state"]["coordinate_system"]["row_origin"] == 1
        instructions = payload["questions"]["move"]["instructions"]
        assert any("`board_rows`" in item for item in instructions["inspect"])
        assert any("best achievable game result" in item for item in instructions["decision_objective"])
        assert any("best for them" in item for item in instructions["decision_objective"])
        assert instructions["output_constraint"].startswith("Return exactly one option")
        assert len(payload["questions"]["move"]["criteria"]) == size * size
        assert payload["questions"]["move"]["criteria"]["r1c1"] == (
            "Place X in the currently empty legal cell at row 1, column 1."
        )
        return httpx2.Response(200, json=response_for(payload))
    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            move, metadata = await service.select_move(GameState(rules=GameRules(size, length)))
            assert move == (0, 0)
            assert metadata["model"] == MODEL
            assert metadata["prompt_version"] == PROMPT_VERSION == "jev-game-v2"
            assert not metadata["seed_reproducible"]
            assert ledger.status()["used_nano_usd"] == 4200
        finally:
            await service.aclose()
    asyncio.run(exercise())
    assert len(requests) == 1


def test_prompt_identifies_o_as_the_player_and_x_as_the_opponent():
    state = GameState.from_board([[1, 0, 0], [0, 0, 0], [0, 0, 0]])

    context, questions, moves = JevService.question(state)

    assert context["player_to_move"] == "O"
    assert context["opponent"] == "X"
    assert context["board_rows"] == ["X . .", ". . .", ". . ."]
    assert set(questions["move"].criteria) == set(moves)
    assert questions["move"].criteria["r1c2"] == (
        "Place O in the currently empty legal cell at row 1, column 2."
    )


@pytest.mark.parametrize("status", [401, 429, 529, 500])
def test_upstream_failures_do_not_retry_or_leak_and_keep_reservation(ledger, status):
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx2.Response(status, json={"error": "SECRET-UPSTREAM-TEXT"}, headers={"Retry-After": "1"})
    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            with pytest.raises(JevError) as exc:
                await service.select_move(GameState())
            assert exc.value.status == 503
            assert str(exc.value) == "provider_unavailable"
            assert ledger.status()["used_nano_usd"] == RESERVATION
        finally:
            await service.aclose()
    asyncio.run(exercise())
    assert len(attempts) == 1


def test_invalid_move_is_not_replaced_by_another_agent(ledger):
    def handler(request):
        data = response_for(json.loads(request.content))
        data["answers"]["move"]["choice"] = "r99c99"
        return httpx2.Response(200, json=data)
    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            with pytest.raises(JevError, match="provider_response_invalid") as exc:
                await service.select_move(GameState())
            assert exc.value.diagnostic == "choice_not_in_criteria"
            assert ledger.status()["used_nano_usd"] == 4200
        finally:
            await service.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("probabilities", {"r1c1": 1.0}, "probability_key_mismatch"),
        ("probabilities", None, "probability_sum_invalid"),
        ("confidence", 2.0, "confidence_invalid"),
    ],
)
def test_invalid_response_diagnostics_are_controlled(ledger, field, value, diagnostic):
    def handler(request):
        payload = json.loads(request.content)
        data = response_for(payload)
        if field == "probabilities" and value is None:
            choices = payload["questions"]["move"]["criteria"]
            data["answers"]["move"][field] = {key: 0.1 for key in choices}
        else:
            data["answers"]["move"][field] = value
        return httpx2.Response(200, json=data)

    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            with pytest.raises(JevError, match="provider_response_invalid") as exc:
                await service.select_move(GameState())
            assert exc.value.diagnostic == diagnostic
            assert str(exc.value) == "provider_response_invalid"
        finally:
            await service.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("values", "valid"),
    [([0.33, 0.33, 0.33], True),
     ([0.33, 0.33, 0.32], False),
     ([0.34, 0.34, 0.33], True),
     ([0.34, 0.34, 0.34], False)],
)
def test_probability_sum_includes_decimal_boundary_for_large_board(ledger, values, valid):
    def handler(request):
        payload = json.loads(request.content)
        data = response_for(payload)
        probabilities = data["answers"]["move"]["probabilities"]
        for key, value in zip(probabilities, values):
            probabilities[key] = value
        return httpx2.Response(200, json=data)

    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            state = GameState(rules=GameRules(9, 3))
            if valid:
                move, _ = await service.select_move(state)
                assert move == (0, 0)
            else:
                with pytest.raises(JevError) as exc:
                    await service.select_move(state)
                assert exc.value.diagnostic == "probability_sum_invalid"
        finally:
            await service.aclose()

    asyncio.run(exercise())


def test_unknown_usage_keeps_maximum_charge(ledger):
    def handler(request):
        return httpx2.Response(200, json=response_for(json.loads(request.content), usage={}))
    async def exercise():
        service = JevService(client_with_handler(handler), ledger)
        try:
            await service.select_move(GameState())
            assert ledger.status()["used_nano_usd"] == RESERVATION
        finally:
            await service.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_retain_reservation(ledger, cancel):
    async def exercise():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.sleep(60)
        service = JevService(client_with_handler(handler), ledger, timeout=.05)
        try:
            task = asyncio.create_task(service.select_move(GameState()))
            await entered.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(JevError) as exc:
                    await task
                assert exc.value.status == 504
            assert ledger.status()["used_nano_usd"] == RESERVATION
            async with service.slot():
                pass
        finally:
            await service.aclose()
    asyncio.run(exercise())


def test_budget_exhaustion_prevents_any_provider_call(ledger):
    ledger.reconcile(MONTHLY_LIMIT)
    async def exercise():
        def handler(request):
            pytest.fail("Provider must not be contacted")
        service = JevService(client_with_handler(handler), ledger)
        try:
            assert (await service.capability())["reason"] == "budget_exhausted"
            with pytest.raises(JevError, match="budget_exhausted"):
                await service.select_move(GameState())
        finally:
            await service.aclose()
    asyncio.run(exercise())


def test_single_legal_move_does_not_call_provider(ledger):
    async def exercise():
        def handler(request):
            pytest.fail("No call for a forced move")
        service = JevService(client_with_handler(handler), ledger)
        try:
            state = GameState.from_board([[1, -1, 1], [1, -1, -1], [-1, 1, 0]])
            move, meta = await service.select_move(state)
            assert move == (2, 2)
            assert meta["model"] is None
            assert ledger.status()["used_nano_usd"] == 0
        finally:
            await service.aclose()
    asyncio.run(exercise())


def test_queue_is_bounded_and_has_a_deadline():
    async def exercise():
        service = JevService(queue_timeout=.05)
        async with service.slot(), service.slot():
            tasks = [asyncio.create_task(service.slot().__aenter__()) for _ in range(4)]
            await asyncio.sleep(.01)
            with pytest.raises(JevError, match="queue_full"):
                async with service.slot():
                    pass
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(result, JevError) and result.status == 429 for result in results)
        assert service._waiting == 0
    asyncio.run(exercise())


def test_simultaneous_burst_cannot_bypass_queue_limit():
    async def exercise():
        service = JevService(queue_timeout=.05)
        release = asyncio.Event()
        active = []
        async def work():
            async with service.slot():
                active.append(True)
                await release.wait()
        tasks = [asyncio.create_task(work()) for _ in range(20)]
        await asyncio.sleep(.01)
        assert len(active) == 2
        assert service._waiting == 4
        assert sum(task.done() for task in tasks) == 14
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(value, JevError) for value in results) == 14
        assert len(active) == 6
    asyncio.run(exercise())


def test_evaluator_uses_evaluation_budget_and_does_not_score_partial_games():
    from web.jev_evaluate import evaluate, tactical_positions
    for size in (3, 5, 9):
        for length in range(3, size + 1):
            for _, state, expected in tactical_positions(size, length):
                assert not state.is_game_over()
                assert expected in state.get_available_moves()
                GameState.from_board(state.board, state.rules)
    records = []
    class Service:
        calls = 0
        async def select_move(self, state, *, kind):
            assert kind == "evaluation"
            self.calls += 1
            if self.calls == 5:
                raise JevError("budget_exhausted")
            return state.get_available_moves()[0], {}
    with pytest.raises(JevError, match="budget_exhausted"):
        asyncio.run(evaluate(Service(), records.append, games=1, variants=[(3, 3)]))
    assert records[-1]["type"] == "interrupted_game"
    assert not any(record["type"] == "game" for record in records)


def test_paid_evaluator_only_runs_selected_variants(tmp_path, monkeypatch):
    from web import jev_evaluate

    class Service:
        ledger = SimpleNamespace(status=lambda: {"available": True})

        async def reason(self):
            return None

        async def aclose(self):
            pass

    selected = []

    async def record_selection(_service, _emit, *, games, variants):
        selected.append((games, variants))

    monkeypatch.setattr(jev_evaluate.JevService, "from_environment", Service)
    monkeypatch.setattr(jev_evaluate, "evaluate", record_selection)
    output = tmp_path / "evaluation.jsonl"
    monkeypatch.setattr(sys, "argv", ["jev_evaluate", "--confirm-paid-evaluation",
                                       "--output", str(output), "--games-per-side", "1",
                                       "--variant", "9:3", "--variant", "9:4"])

    assert jev_evaluate.main() == 0
    assert selected == [(1, [(9, 3), (9, 4)])]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[0]["variants"] == ["9:3", "9:4"]
    assert records[-1]["type"] == "complete"


@pytest.mark.parametrize("selection", [["--variant", "9:10"], ["--variant", "9"],
                                           ["--variant", "9:3", "--variant", "9:3"]])
def test_invalid_variant_selection_stops_before_paid_client(tmp_path, monkeypatch, selection):
    from web import jev_evaluate

    def unexpected_client():
        pytest.fail("Invalid selection must not initialize the paid client")

    monkeypatch.setattr(jev_evaluate.JevService, "from_environment", unexpected_client)
    monkeypatch.setattr(sys, "argv", ["jev_evaluate", "--confirm-paid-evaluation",
                                       "--output", str(tmp_path / "evaluation.jsonl"), *selection])
    with pytest.raises(SystemExit) as exc:
        jev_evaluate.main()
    assert exc.value.code == 2
