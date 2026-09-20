const AGENT_IDS = ['random', 'rules', 'minimax', 'mcts', 'q_learning', 'dqn', 'imitation', 'reinforce', 'jev'];
const BOARD_SIZES = [3, 5, 9];
const SUPPORTED_LANGUAGES = ['en', 'pl'];
const SUPPORTED_THEMES = ['light', 'dark'];
const $ = selector => document.querySelector(selector);
const boardElement = $('#board');
const statusElement = $('#status');
const noteElement = $('#algorithm-note');

let language = preference('tictactoe_lang', SUPPORTED_LANGUAGES, 'en');
let theme = preference('tictactoe_theme', SUPPORTED_THEMES, 'light');
let translations = {};
let capabilities = new Map(AGENT_IDS.map(id => [id, {available: true, reason: null}]));
let capabilityReleaseRevision = 'unknown';
let mode = 'human';
let activeRules = {boardSize: 3, winLength: 3};
let board;
let currentPlayer;
let humanPlayer;
let busy;
let finished;
let winningLine = null;
let replay = null;
let replayTimer = null;
let seriesCalculation = null;
let activeController = null;
let gameRevision = 0;
let capabilityRevision = 0;
let statusState = {key: 'ready', params: {}};

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function setCookie(name, value, days = 365) {
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)};expires=${expires};path=/;SameSite=Lax`;
}

function preference(name, allowed, fallback) {
  const value = getCookie(name);
  return allowed.includes(value) ? value : fallback;
}

function t(key, params = {}) {
  let value = translations[key] || key;
  for (const [name, replacement] of Object.entries(params)) {
    value = value.replaceAll(`{${name}}`, String(replacement));
  }
  return value;
}

function setStatus(key, params = {}) {
  statusState = {key, params};
  statusElement.textContent = t(key, params);
}

const agentName = id => t(`agent.${id}.name`);

function selectedRules() {
  return {
    boardSize: Number($('#board-size').value),
    winLength: Number($('#win-length').value),
  };
}

function populateBoardSizes() {
  for (const size of BOARD_SIZES) {
    $('#board-size').add(new Option(`${size}×${size}`, String(size)));
  }
  $('#board-size').value = '3';
}

function populateWinLengths(preferred) {
  const select = $('#win-length');
  const size = Number($('#board-size').value);
  select.innerHTML = '';
  for (let length = 3; length <= size; length++) {
    select.add(new Option(String(length), String(length)));
  }
  select.value = String(Math.min(size, Math.max(3, preferred)));
}

function populateAgentSelectors() {
  for (const id of ['human-agent', 'x-agent', 'o-agent']) {
    const select = $('#' + id);
    const selected = select.value;
    select.innerHTML = '';
    for (const agentId of AGENT_IDS) {
      const capability = capabilities.get(agentId);
      const option = new Option(agentName(agentId), agentId);
      option.disabled = capability ? !capability.available : true;
      if (option.disabled) option.title = t(`unavailable.${capability?.reason || 'unavailable'}`);
      select.add(option);
    }
    const selectedOption = [...select.options].find(option => option.value === selected && !option.disabled);
    const fallback = [...select.options].find(option => !option.disabled);
    if (selectedOption) select.value = selected;
    else if (fallback) select.value = fallback.value;
  }
}

async function refreshCapabilities() {
  const rules = selectedRules();
  const revision = ++capabilityRevision;
  try {
    const response = await fetch(`/api/agents?board_size=${rules.boardSize}&win_length=${rules.winLength}`);
    if (!response.ok) throw new Error(`capabilities HTTP ${response.status}`);
    const data = await response.json();
    const current = selectedRules();
    if (revision !== capabilityRevision || current.boardSize !== rules.boardSize || current.winLength !== rules.winLength) {
      return null;
    }
    capabilityReleaseRevision = data.revision || 'unknown';
    capabilities = new Map(data.agents.map(agent => [agent.id, agent]));
    populateAgentSelectors();
    return true;
  } catch (error) {
    if (revision !== capabilityRevision) return null;
    console.error('Could not load agent capabilities', error);
    capabilities = new Map(AGENT_IDS.map(id => [id, {available: false, reason: 'unavailable'}]));
    populateAgentSelectors();
    setStatus('capabilityError');
    return false;
  }
}

function translateStaticContent() {
  document.querySelectorAll('[data-i18n]').forEach(element => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(element => {
    element.setAttribute('aria-label', t(element.dataset.i18nAria));
  });
  document.title = t('title');
  $('#meta-description').content = t('description');
}

function updatePreferenceControls() {
  document.documentElement.lang = language;
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll('[data-language]').forEach(button => {
    const active = button.dataset.language === language;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  const button = $('#theme-toggle');
  const dark = theme === 'dark';
  const label = button.querySelector('[data-i18n]');
  button.setAttribute('aria-pressed', String(dark));
  button.setAttribute('aria-label', t(dark ? 'switchToLight' : 'switchToDark'));
  button.title = t(dark ? 'switchToLight' : 'switchToDark');
  button.querySelector('.theme-icon').textContent = dark ? '☀' : '☾';
  label.dataset.i18n = dark ? 'lightTheme' : 'darkTheme';
  label.textContent = t(label.dataset.i18n);
  setCookie('tictactoe_lang', language);
  setCookie('tictactoe_theme', theme);
}

async function loadLanguage(next) {
  const safe = SUPPORTED_LANGUAGES.includes(next) ? next : 'en';
  try {
    const response = await fetch(`/locales/${safe}.json`);
    if (!response.ok) throw new Error();
    translations = await response.json();
    language = safe;
  } catch (error) {
    if (safe !== 'en') return loadLanguage('en');
    console.error('Could not load translations', error);
    translations = {};
    language = 'en';
  }
  translateStaticContent();
  populateAgentSelectors();
  updatePreferenceControls();
  refreshDynamicContent();
}

function winningClass(segment) {
  const [start, end] = [segment[0], segment[segment.length - 1]];
  if (start[0] === end[0]) return `row-${start[0]}`;
  if (start[1] === end[1]) return `column-${start[1]}`;
  return end[1] > start[1] ? 'diagonal-down' : 'diagonal-up';
}

function addWinningLine(segment) {
  const namespace = 'http://www.w3.org/2000/svg';
  const overlay = document.createElementNS(namespace, 'svg');
  const line = document.createElementNS(namespace, 'polygon');
  const start = {x: segment[0][1] + 0.5, y: segment[0][0] + 0.5};
  const last = segment[segment.length - 1];
  const end = {x: last[1] + 0.5, y: last[0] + 0.5};
  const length = Math.hypot(end.x - start.x, end.y - start.y);
  const offset = {
    x: -((end.y - start.y) / length) * 0.07,
    y: ((end.x - start.x) / length) * 0.07,
  };
  overlay.classList.add('winning-overlay');
  overlay.setAttribute('viewBox', `0 0 ${activeRules.boardSize} ${activeRules.boardSize}`);
  overlay.setAttribute('preserveAspectRatio', 'none');
  overlay.setAttribute('aria-hidden', 'true');
  line.classList.add('winning-line', winningClass(segment));
  line.setAttribute('points', [
    `${start.x + offset.x},${start.y + offset.y}`,
    `${end.x + offset.x},${end.y + offset.y}`,
    `${end.x - offset.x},${end.y - offset.y}`,
    `${start.x - offset.x},${start.y - offset.y}`,
  ].join(' '));
  overlay.appendChild(line);
  boardElement.appendChild(overlay);
}

function render() {
  boardElement.innerHTML = '';
  const size = activeRules.boardSize;
  boardElement.style.setProperty('--board-size', size);
  boardElement.style.setProperty('--board-min-size', `${size * 43}px`);
  boardElement.style.setProperty('--board-gap', `${Math.max(2, 9 - size)}px`);
  boardElement.style.setProperty('--cell-radius', `${Math.max(5, 16 - size)}px`);
  boardElement.style.setProperty('--mark-size', `${Math.max(1.15, 4.5 - size * 0.35)}rem`);
  board.flat().forEach((value, index) => {
    const cell = document.createElement('button');
    const row = Math.floor(index / size);
    const column = index % size;
    cell.className = `cell ${value === 1 ? 'x' : value === -1 ? 'o' : ''}`;
    cell.textContent = value === 1 ? 'X' : value === -1 ? 'O' : '';
    cell.ariaLabel = t('cell', {row: row + 1, column: column + 1});
    cell.disabled = busy || finished || value !== 0 || mode === 'ai' || (mode === 'human' && currentPlayer !== humanPlayer);
    cell.onclick = () => play(row, column);
    cell.onkeydown = event => {
      const deltas = {
        ArrowUp: [-1, 0],
        ArrowDown: [1, 0],
        ArrowLeft: [0, -1],
        ArrowRight: [0, 1],
      };
      const delta = deltas[event.key];
      if (!delta) return;
      event.preventDefault();
      const nextRow = Math.max(0, Math.min(size - 1, row + delta[0]));
      const nextColumn = Math.max(0, Math.min(size - 1, column + delta[1]));
      boardElement.querySelectorAll('.cell')[nextRow * size + nextColumn].focus();
    };
    boardElement.appendChild(cell);
  });
  if (winningLine) addWinningLine(winningLine);
  let note;
  if (mode === 'ai') note = t('playsAs', {x: agentName($('#x-agent').value), o: agentName($('#o-agent').value)});
  else if (mode === 'local') note = t('twoPlayers');
  else note = t(`agent.${$('#human-agent').value}.description`);
  const available = [...capabilities.entries()]
    .filter(([, capability]) => capability.available)
    .map(([agentId]) => agentName(agentId));
  if (mode !== 'local' && available.length > 0 && available.length < AGENT_IDS.length) {
    note += ` ${t('availableAgents', {agents: available.join(', ')})}`;
  }
  noteElement.textContent = note;
}

function updateSeriesMeta() {
  if (!replay) return;
  const count = replay.data.games.length;
  $('#series-meta').textContent = t('seriesMeta', {
    seed: replay.data.seed,
    count,
    games: t(count === 1 ? 'gameCountOne' : 'gameCountMany'),
    size: replay.data.board_size || 3,
    win: replay.data.win_length || 3,
  });
  if (Object.values(replay.data.agent_profiles || {}).some(profile => profile.seed_reproducible === false)) {
    $('#series-meta').textContent += ` · ${t('remoteSeedNote')}`;
  }
}

function refreshDynamicContent() {
  if (!board) return;
  setStatus(statusState.key, statusState.params);
  $('#start').textContent = t(mode === 'ai' ? 'runSeries' : 'newGame');
  if (replay) {
    const complete = replay.game === replay.data.games.length - 1 && replay.move >= replay.data.games[replay.game].moves.length;
    $('#pause').textContent = t(complete ? 'replay' : replay.playing ? 'pause' : 'resume');
    updateSeriesMeta();
  }
  render();
}

function result(position = board, rules = activeRules) {
  const directions = [[0, 1], [1, 0], [1, 1], [1, -1]];
  for (let row = 0; row < rules.boardSize; row++) {
    for (let column = 0; column < rules.boardSize; column++) {
      const player = position[row][column];
      if (!player) continue;
      for (const [rowDelta, columnDelta] of directions) {
        const segment = [];
        for (let offset = 0; offset < rules.winLength; offset++) {
          const nextRow = row + offset * rowDelta;
          const nextColumn = column + offset * columnDelta;
          if (nextRow < 0 || nextRow >= rules.boardSize || nextColumn < 0 || nextColumn >= rules.boardSize) break;
          if (position[nextRow][nextColumn] !== player) break;
          segment.push([nextRow, nextColumn]);
        }
        if (segment.length === rules.winLength) return {winner: player, line: segment};
      }
    }
  }
  return {winner: position.flat().every(Boolean) ? 0 : null, line: null};
}

function finish() {
  const gameResult = result();
  if (gameResult.winner === null) return false;
  finished = true;
  winningLine = gameResult.line;
  if (gameResult.winner === 0) setStatus('draw');
  else if (mode === 'local') setStatus('playerWins', {player: gameResult.winner === 1 ? 'X' : 'O'});
  else setStatus(gameResult.winner === humanPlayer ? 'youWin' : 'aiWins');
  render();
  return true;
}

function play(row, column) {
  if (busy || finished || board[row][column]) return;
  board[row][column] = currentPlayer;
  currentPlayer *= -1;
  if (finish()) return;
  if (mode === 'local') {
    setStatus('playerTurn', {player: currentPlayer === 1 ? 'X' : 'O'});
    render();
  } else {
    requestAiMove();
  }
}

function cancelActiveRequest() {
  if (activeController) activeController.abort();
  activeController = null;
}

async function requestMove(position, algorithm, seed, signal) {
  return fetch('/api/move', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    signal,
    body: JSON.stringify({
      board: position,
      algorithm,
      seed,
      board_size: activeRules.boardSize,
      win_length: activeRules.winLength,
    }),
  });
}

function isLegalReturnedMove(position, move, player) {
  return move
    && Number.isInteger(move.row)
    && Number.isInteger(move.column)
    && move.player === player
    && move.row >= 0
    && move.row < activeRules.boardSize
    && move.column >= 0
    && move.column < activeRules.boardSize
    && position[move.row][move.column] === 0;
}

function responseError(data) {
  const error = new Error('move request failed');
  error.code = typeof data?.detail === 'object' ? data.detail.code : null;
  return error;
}

function providerErrorKey(error, fallback) {
  const key = `jevError.${error.code}`;
  return translations[key] ? key : fallback;
}

async function requestAiMove() {
  const revision = gameRevision;
  cancelActiveRequest();
  const controller = new AbortController();
  activeController = controller;
  busy = true;
  setStatus('aiThinking');
  render();
  try {
    const response = await requestMove(board, $('#human-agent').value, undefined, controller.signal);
    const data = await response.json();
    if (revision !== gameRevision || mode !== 'human') return;
    if (response.status === 429) {
      busy = false;
      finished = true;
      setStatus('moveRateLimited', {seconds: response.headers.get('Retry-After') || 1});
      render();
      return;
    }
    if (!response.ok) throw responseError(data);
    if (!isLegalReturnedMove(board, data.move, currentPlayer)) throw new Error('illegal move response');
    board[data.move.row][data.move.column] = data.move.player;
    currentPlayer *= -1;
    busy = false;
    if (!finish()) setStatus('yourTurn', {player: currentPlayer === 1 ? 'X' : 'O'});
  } catch (error) {
    if (error.name === 'AbortError' || revision !== gameRevision || mode !== 'human') return;
    busy = false;
    finished = true;
    setStatus(providerErrorKey(error, 'moveError'));
  } finally {
    if (activeController === controller) activeController = null;
  }
  render();
}

function resetBoard() {
  cancelActiveRequest();
  gameRevision++;
  activeRules = selectedRules();
  board = Array.from({length: activeRules.boardSize}, () => Array(activeRules.boardSize).fill(0));
  currentPlayer = 1;
  busy = false;
  finished = false;
  winningLine = null;
  render();
}

function newHumanGame() {
  stopReplay();
  humanPlayer = Math.random() < 0.5 ? 1 : -1;
  resetBoard();
  setStatus(humanPlayer === 1 ? 'youStart' : 'aiStarts');
  if (humanPlayer === -1) requestAiMove();
}

function newLocalGame() {
  stopReplay();
  resetBoard();
  humanPlayer = 1;
  setStatus('playerTurn', {player: 'X'});
}

function seriesSeed() {
  const value = $('#seed').value.trim();
  if (!value) return crypto.getRandomValues(new Uint32Array(1))[0];
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error('invalid seed');
  return parsed;
}

function deriveSeed(base, game, ply) {
  let value = (base >>> 0) ^ Math.imul(game + 1, 0x9e3779b1) ^ Math.imul(ply + 1, 0x85ebca6b);
  value ^= value >>> 16;
  value = Math.imul(value, 0x7feb352d);
  value ^= value >>> 15;
  value = Math.imul(value, 0x846ca68b);
  return (value ^ (value >>> 16)) >>> 0;
}

function replayAgentProfiles() {
  const selected = [$('#x-agent').value, $('#o-agent').value];
  return Object.fromEntries(selected.map(agentId => {
    const capability = capabilities.get(agentId) || {};
    return [agentId, {
      policy_version: capability.policy_version || 'unknown',
      work_profile: capability.work_profile || 'unknown',
      seed_reproducible: capability.seed_reproducible !== false,
    }];
  }));
}

function waitWithSignal(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    }, {once: true});
  });
}

function wakeSeriesCalculation() {
  if (seriesCalculation?.wake) {
    const wake = seriesCalculation.wake;
    seriesCalculation.wake = null;
    wake();
  }
}

async function waitForSeriesTurn(signal) {
  if (!seriesCalculation || !seriesCalculation.paused) return;
  if (seriesCalculation.steps > 0) {
    seriesCalculation.steps--;
    return;
  }
  await new Promise((resolve, reject) => {
    const resume = () => {
      signal.removeEventListener('abort', abort);
      resolve();
    };
    const abort = () => {
      if (seriesCalculation?.wake === resume) seriesCalculation.wake = null;
      reject(new DOMException('Aborted', 'AbortError'));
    };
    seriesCalculation.wake = resume;
    signal.addEventListener('abort', abort, {once: true});
  });
  return waitForSeriesTurn(signal);
}

async function incrementalMove(position, algorithm, seed, controller, revision) {
  while (true) {
    const response = await requestMove(position, algorithm, seed, controller.signal);
    const data = await response.json();
    if (revision !== gameRevision || mode !== 'ai') throw new DOMException('Aborted', 'AbortError');
    if (response.status !== 429) {
      if (!response.ok) throw responseError(data);
      return data;
    }
    const retryAfter = Math.max(1, Math.min(30, Number(response.headers.get('Retry-After')) || 1));
    setStatus('seriesRateLimited', {seconds: retryAfter});
    await waitWithSignal(retryAfter * 1000, controller.signal);
    setStatus('simulating');
  }
}

async function calculateIncrementalSeries(count, seed, controller, revision) {
  const data = {
    seed,
    x_algorithm: $('#x-agent').value,
    o_algorithm: $('#o-agent').value,
    board_size: activeRules.boardSize,
    win_length: activeRules.winLength,
    server_revision: capabilityReleaseRevision,
    agent_profiles: replayAgentProfiles(),
    games: [],
    summary: {x_wins: 0, o_wins: 0, draws: 0},
  };
  seriesCalculation.data = data;
  for (let game = 0; game < count; game++) {
    const position = Array.from({length: activeRules.boardSize}, () => Array(activeRules.boardSize).fill(0));
    const moves = [];
    data.interrupted_game = {game: game + 1, moves, board_size: activeRules.boardSize, win_length: activeRules.winLength};
    let player = 1;
    while (result(position).winner === null) {
      await waitForSeriesTurn(controller.signal);
      const algorithm = player === 1 ? data.x_algorithm : data.o_algorithm;
      const response = await incrementalMove(position, algorithm, deriveSeed(seed, game, moves.length), controller, revision);
      const move = response.move;
      if (!isLegalReturnedMove(position, move, player)) throw new Error('illegal move response');
      position[move.row][move.column] = player;
      moves.push({row: move.row, column: move.column, player, metadata: response.metadata || null});
      player *= -1;
      if (seriesCalculation?.paused) setStatus('seriesPaused');
      else setStatus('moveProgress', {current: game + 1, total: count, move: moves.length});
    }
    const winner = result(position).winner;
    if (winner === 1) data.summary.x_wins++;
    else if (winner === -1) data.summary.o_wins++;
    else data.summary.draws++;
    data.games.push({
      game: game + 1,
      winner,
      moves,
      board_size: activeRules.boardSize,
      win_length: activeRules.winLength,
    });
    delete data.interrupted_game;
  }
  return data;
}

function showSeries(data, playing) {
  if (!data?.games.length) return;
  replay = {data, game: 0, move: 0, playing};
  $('#x-wins').textContent = data.summary.x_wins;
  $('#o-wins').textContent = data.summary.o_wins;
  $('#draws').textContent = data.summary.draws;
  updateSeriesMeta();
  $('#scoreboard').classList.remove('hidden');
  $('#replay-controls').classList.remove('hidden', 'calculating');
  $('#pause').textContent = t(playing ? 'pause' : 'replay');
  loadReplayGame();
  if (playing) scheduleReplay();
}

async function newAiSeries() {
  stopReplay();
  resetBoard();
  busy = true;
  finished = true;
  setStatus('simulating');
  render();
  const revision = gameRevision;
  const controller = new AbortController();
  activeController = controller;
  try {
    const count = Number($('#series-count').value);
    const seed = seriesSeed();
    let data;
    const agents = [$('#x-agent').value, $('#o-agent').value];
    if (activeRules.boardSize === 3 && activeRules.winLength === 3
        && agents.every(id => capabilities.get(id)?.series_mode !== 'incremental')) {
      const response = await fetch('/api/matches', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        signal: controller.signal,
        body: JSON.stringify({
          x_algorithm: $('#x-agent').value,
          o_algorithm: $('#o-agent').value,
          games: count,
          seed,
        }),
      });
      data = await response.json();
      if (response.status === 429) {
        busy = false;
        setStatus('seriesRateLimited', {seconds: response.headers.get('Retry-After') || 1});
        render();
        return;
      }
      if (!response.ok) throw new Error();
      data.server_revision = capabilityReleaseRevision;
      data.agent_profiles = replayAgentProfiles();
    } else {
      seriesCalculation = {paused: false, steps: 0, wake: null};
      $('#replay-controls').classList.add('calculating');
      $('#replay-controls').classList.remove('hidden');
      $('#pause').textContent = t('pause');
      $('#stop-series').classList.remove('hidden');
      data = await calculateIncrementalSeries(count, seed, controller, revision);
    }
    if (revision !== gameRevision || mode !== 'ai') return;
    seriesCalculation = null;
    busy = false;
    showSeries(data, true);
  } catch (error) {
    if (revision !== gameRevision || mode !== 'ai') return;
    const stopped = seriesCalculation?.stopRequested;
    if (error.name === 'AbortError' && !stopped) return;
    const partial = seriesCalculation?.data;
    busy = false;
    finished = true;
    seriesCalculation = null;
    $('#replay-controls').classList.add('hidden');
    $('#replay-controls').classList.remove('calculating');
    showSeries(partial, false);
    setStatus(stopped ? 'seriesStopped' : providerErrorKey(error, 'seriesError'));
    render();
  } finally {
    if (activeController === controller) {
      activeController = null;
      $('#stop-series').classList.add('hidden');
    }
  }
}

function loadReplayGame() {
  const game = replay.data.games[replay.game];
  activeRules = {
    boardSize: game.board_size || replay.data.board_size || 3,
    winLength: game.win_length || replay.data.win_length || 3,
  };
  board = Array.from({length: activeRules.boardSize}, () => Array(activeRules.boardSize).fill(0));
  winningLine = null;
  replay.move = 0;
  setStatus('gameProgress', {current: replay.game + 1, total: replay.data.games.length});
  render();
}

function advanceReplay(schedule = true) {
  if (!replay) return;
  const game = replay.data.games[replay.game];
  if (replay.move < game.moves.length) {
    const move = game.moves[replay.move++];
    board[move.row][move.column] = move.player;
    if (replay.move === game.moves.length) winningLine = result().line;
    setStatus('moveProgress', {current: replay.game + 1, total: replay.data.games.length, move: replay.move});
    render();
  } else if (replay.game + 1 < replay.data.games.length) {
    replay.game++;
    loadReplayGame();
  } else {
    replay.playing = false;
    $('#pause').textContent = t('replay');
    setStatus('seriesCompleteSummary', {x: replay.data.summary.x_wins, o: replay.data.summary.o_wins, draws: replay.data.summary.draws});
    render();
    return;
  }
  if (schedule && replay.playing) scheduleReplay();
}

function scheduleReplay() {
  clearTimeout(replayTimer);
  replayTimer = setTimeout(() => advanceReplay(), Number($('#speed').value));
}

function stopReplay() {
  clearTimeout(replayTimer);
  replay = null;
  if (seriesCalculation) {
    seriesCalculation.paused = false;
    wakeSeriesCalculation();
    seriesCalculation = null;
  }
  $('#replay-controls').classList.add('hidden');
  $('#replay-controls').classList.remove('calculating');
  $('#scoreboard').classList.add('hidden');
  $('#stop-series').classList.add('hidden');
}

function toggleReplay() {
  if (seriesCalculation) {
    seriesCalculation.paused = !seriesCalculation.paused;
    if (seriesCalculation.paused) {
      setStatus('seriesPaused');
    } else {
      setStatus('simulating');
      wakeSeriesCalculation();
    }
    $('#pause').textContent = t(seriesCalculation.paused ? 'resume' : 'pause');
    return;
  }
  if (!replay) return;
  if (replay.game === replay.data.games.length - 1 && replay.move >= replay.data.games[replay.game].moves.length) {
    replay.game = 0;
    loadReplayGame();
  }
  replay.playing = !replay.playing;
  $('#pause').textContent = t(replay.playing ? 'pause' : 'resume');
  if (replay.playing) scheduleReplay();
  else clearTimeout(replayTimer);
}

function setMode(next) {
  mode = next;
  document.querySelectorAll('.mode-tabs button').forEach(button => {
    button.classList.toggle('active', button.dataset.mode === mode);
  });
  const aiMode = mode === 'ai';
  document.querySelectorAll('.ai-field').forEach(field => {
    field.style.display = aiMode ? 'grid' : 'none';
  });
  $('#human-agent-field').style.display = mode === 'human' ? 'grid' : 'none';
  $('#start').textContent = t(aiMode ? 'runSeries' : 'newGame');
  if (aiMode) {
    stopReplay();
    resetBoard();
    finished = true;
    setStatus('chooseAgents');
    render();
  } else {
    start();
  }
}

function start() {
  if (mode === 'human') newHumanGame();
  else if (mode === 'ai') newAiSeries();
  else newLocalGame();
}

async function rulesChanged(sizeChanged) {
  stopReplay();
  cancelActiveRequest();
  gameRevision++;
  if (sizeChanged) populateWinLengths(Math.min(Number($('#board-size').value), 5));
  const loaded = await refreshCapabilities();
  if (mode === 'local') {
    start();
    return;
  }
  if (loaded !== true) return;
  if (mode === 'ai') {
    resetBoard();
    finished = true;
    setStatus('chooseAgents');
    render();
  } else {
    start();
  }
}

async function initialize() {
  document.documentElement.dataset.theme = theme;
  populateBoardSizes();
  populateWinLengths(3);
  document.querySelectorAll('.mode-tabs button').forEach(button => {
    button.onclick = () => setMode(button.dataset.mode);
  });
  document.querySelectorAll('[data-language]').forEach(button => {
    button.onclick = () => loadLanguage(button.dataset.language);
  });
  $('#theme-toggle').onclick = () => {
    theme = theme === 'light' ? 'dark' : 'light';
    updatePreferenceControls();
  };
  $('#board-size').onchange = () => rulesChanged(true);
  $('#win-length').onchange = () => rulesChanged(false);
  $('#start').onclick = start;
  $('#pause').onclick = toggleReplay;
  $('#stop-series').onclick = () => {
    if (!seriesCalculation) return;
    seriesCalculation.stopRequested = true;
    activeController?.abort();
  };
  $('#step').onclick = () => {
    if (seriesCalculation) {
      seriesCalculation.paused = true;
      seriesCalculation.steps++;
      $('#pause').textContent = t('resume');
      wakeSeriesCalculation();
    } else if (replay) {
      replay.playing = false;
      $('#pause').textContent = t('resume');
      clearTimeout(replayTimer);
      advanceReplay(false);
    }
  };
  $('#speed').onchange = () => {
    if (replay?.playing) scheduleReplay();
  };
  ['human-agent', 'x-agent', 'o-agent'].forEach(id => {
    $('#' + id).onchange = render;
  });
  await loadLanguage(language);
  if (!await refreshCapabilities()) {
    setMode('local');
    $('#board-size').disabled = false;
    $('#win-length').disabled = false;
    return;
  }
  $('#human-agent').value = 'minimax';
  $('#x-agent').value = 'minimax';
  $('#o-agent').value = 'dqn';
  setMode('human');
  $('#board-size').disabled = false;
  $('#win-length').disabled = false;
}

initialize();
