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
let rovingCellIndex = 0;

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
  renderAgentAvailability();
  updateSettingsSummary();
}

function renderAgentAvailability() {
  const details = $('#agent-availability');
  const list = $('#unavailable-agents');
  list.replaceChildren();
  for (const [id, capability] of capabilities) {
    if (capability.available) continue;
    const item = document.createElement('li');
    item.textContent = `${agentName(id)}: ${t(`unavailable.${capability.reason || 'unavailable'}`)}`;
    list.appendChild(item);
  }
  details.classList.toggle('hidden', mode === 'local' || list.children.length === 0);
}

function updateSettingsSummary() {
  const {boardSize, winLength} = selectedRules();
  if (!boardSize || !winLength) return;
  $('#settings-summary').textContent = t('settingsSummary', {size: boardSize, win: winLength});
}

function setSettingsExpanded(expanded) {
  $('#settings-toggle').setAttribute('aria-expanded', String(expanded));
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
  updateSettingsSummary();
  updatePreferenceControls();
  refreshDynamicContent();
}

function render() {
  const focusedCell = boardElement.contains(document.activeElement) ? document.activeElement : null;
  if (focusedCell?.dataset.index) rovingCellIndex = Number(focusedCell.dataset.index);
  boardElement.innerHTML = '';
  const size = activeRules.boardSize;
  rovingCellIndex = Math.min(rovingCellIndex, size * size - 1);
  const gap = size === 9 ? 1 : size === 5 ? 2 : 3;
  boardElement.style.setProperty('--board-size', size);
  boardElement.style.setProperty('--board-min-size', `${size * 24 + (size - 1) * gap + 2}px`);
  boardElement.style.setProperty('--board-gap', `${gap}px`);
  boardElement.style.setProperty('--mark-size', size === 9
    ? 'clamp(1.15rem, 4vw, 2.6rem)'
    : size === 5 ? 'clamp(2.1rem, 6vw, 4rem)' : 'clamp(3rem, 8vw, 6rem)');
  board.flat().forEach((value, index) => {
    const cell = document.createElement('button');
    const row = Math.floor(index / size);
    const column = index % size;
    const blocked = busy || finished || value !== 0 || mode === 'ai'
      || (mode === 'human' && currentPlayer !== humanPlayer);
    const isWinningCell = winningLine?.some(([lineRow, lineColumn]) => lineRow === row && lineColumn === column);
    cell.className = `cell ${value === 1 ? 'x' : value === -1 ? 'o' : ''}${isWinningCell ? ' winning' : ''}`;
    cell.textContent = value === 1 ? 'X' : value === -1 ? 'O' : '';
    cell.dataset.index = String(index);
    cell.tabIndex = index === rovingCellIndex ? 0 : -1;
    cell.setAttribute('aria-disabled', String(blocked));
    cell.ariaLabel = t(value === 0 ? 'cellEmpty' : isWinningCell ? 'cellWinning' : 'cellOccupied', {
      row: row + 1, column: column + 1, mark: value === 1 ? 'X' : 'O',
    });
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
      rovingCellIndex = nextRow * size + nextColumn;
      cell.tabIndex = -1;
      const nextCell = boardElement.querySelectorAll('.cell')[rovingCellIndex];
      nextCell.tabIndex = 0;
      nextCell.focus();
    };
    boardElement.appendChild(cell);
  });
  if (focusedCell) boardElement.children[rovingCellIndex].focus({preventScroll: true});
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

function updateSeriesMeta(data, count = data.games.length) {
  $('#series-meta').textContent = t('seriesMeta', {
    seed: data.seed,
    count,
    games: t(count === 1 ? 'gameCountOne' : 'gameCountMany'),
    size: data.board_size || 3,
    win: data.win_length || 3,
  });
  if (Object.values(data.agent_profiles || {}).some(profile => profile.seed_reproducible === false)) {
    $('#series-meta').textContent += ` · ${t('remoteSeedNote')}`;
  }
}

function updateScoreboard(summary) {
  $('#x-wins').textContent = summary.x_wins;
  $('#o-wins').textContent = summary.o_wins;
  $('#draws').textContent = summary.draws;
}

function scoreThroughReplay() {
  if (!replay) return;
  const completed = replay.game + Number(replay.move >= replay.data.games[replay.game].moves.length);
  const summary = {x_wins: 0, o_wins: 0, draws: 0};
  for (const game of replay.data.games.slice(0, completed)) {
    if (game.winner === 1) summary.x_wins++;
    else if (game.winner === -1) summary.o_wins++;
    else summary.draws++;
  }
  updateScoreboard(summary);
}

function refreshDynamicContent() {
  if (!board) return;
  setStatus(statusState.key, statusState.params);
  $('#start').textContent = t(mode === 'ai' ? 'runSeries' : 'newGame');
  if (replay) {
    const complete = replay.game === replay.data.games.length - 1 && replay.move >= replay.data.games[replay.game].moves.length;
    $('#pause').textContent = t(complete ? 'replay' : replay.playing ? 'pause' : 'resume');
    updateSeriesMeta(replay.data);
  } else if (seriesCalculation?.data) {
    updateSeriesMeta(seriesCalculation.data);
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
  if (busy || finished || board[row][column] || mode === 'ai'
    || (mode === 'human' && currentPlayer !== humanPlayer)) return;
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
  rovingCellIndex = 0;
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
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, milliseconds);
    signal.addEventListener('abort', abort, {once: true});
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

function newSeriesData(seed) {
  return {
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
}

function startLiveSeries(data) {
  updateScoreboard(data.summary);
  updateSeriesMeta(data);
  $('#scoreboard').classList.remove('hidden');
  $('#replay-controls').classList.add('calculating');
  $('#replay-controls').classList.remove('hidden');
  $('#pause').textContent = t('pause');
  $('#stop-series').classList.remove('hidden');
}

function recordGame(data, game) {
  data.games.push(game);
  if (game.winner === 1) data.summary.x_wins++;
  else if (game.winner === -1) data.summary.o_wins++;
  else data.summary.draws++;
  updateScoreboard(data.summary);
  updateSeriesMeta(data);
}

function showLiveGame(game, data, total) {
  board = Array.from({length: activeRules.boardSize}, () => Array(activeRules.boardSize).fill(0));
  for (const move of game.moves) board[move.row][move.column] = move.player;
  winningLine = result().line;
  setStatus('gameFinished', {
    current: game.game, total,
    x: data.summary.x_wins, o: data.summary.o_wins, draws: data.summary.draws,
  });
  render();
}

async function* jsonLines(response) {
  if (!response.body) throw new Error('stream body unavailable');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (line.trim()) yield JSON.parse(line);
      }
    }
    if (buffer.trim()) throw new Error('incomplete stream response');
  } finally {
    reader.releaseLock();
  }
}

async function calculateStreamingSeries(count, data, controller, revision) {
  const response = await fetch('/api/matches/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    signal: controller.signal,
    body: JSON.stringify({
      x_algorithm: data.x_algorithm,
      o_algorithm: data.o_algorithm,
      games: count,
      seed: data.seed,
    }),
  });
  if (response.status === 429) {
    const error = new Error('series rate limited');
    error.retryAfter = response.headers.get('Retry-After') || 1;
    throw error;
  }
  if (!response.ok) throw new Error(`series HTTP ${response.status}`);
  let complete = false;
  for await (const event of jsonLines(response)) {
    if (revision !== gameRevision || mode !== 'ai') throw new DOMException('Aborted', 'AbortError');
    if (event.type === 'error') throw responseError({detail: event.detail});
    if (event.type === 'complete') {
      complete = true;
      break;
    }
    if (event.type !== 'game' || event.game?.game !== data.games.length + 1) {
      throw new Error('invalid series stream');
    }
    await waitForSeriesTurn(controller.signal);
    recordGame(data, event.game);
    showLiveGame(event.game, data, count);
    if (data.games.length < count) await waitWithSignal(Number($('#speed').value), controller.signal);
  }
  if (!complete || data.games.length !== count) throw new Error('incomplete series stream');
  return data;
}

async function calculateIncrementalSeries(count, data, controller, revision) {
  for (let game = 0; game < count; game++) {
    const position = Array.from({length: activeRules.boardSize}, () => Array(activeRules.boardSize).fill(0));
    board = position.map(row => row.slice());
    winningLine = null;
    setStatus('gameProgress', {current: game + 1, total: count});
    render();
    const moves = [];
    data.interrupted_game = {game: game + 1, moves, board_size: activeRules.boardSize, win_length: activeRules.winLength};
    let player = 1;
    while (result(position).winner === null) {
      await waitForSeriesTurn(controller.signal);
      const algorithm = player === 1 ? data.x_algorithm : data.o_algorithm;
      const response = await incrementalMove(position, algorithm, deriveSeed(data.seed, game, moves.length), controller, revision);
      const move = response.move;
      if (!isLegalReturnedMove(position, move, player)) throw new Error('illegal move response');
      position[move.row][move.column] = player;
      moves.push({row: move.row, column: move.column, player, metadata: response.metadata || null});
      player *= -1;
      board = position.map(row => row.slice());
      if (seriesCalculation?.paused) setStatus('seriesPaused');
      else setStatus('moveProgress', {current: game + 1, total: count, move: moves.length});
      render();
    }
    const winner = result(position).winner;
    const trace = {
      game: game + 1,
      winner,
      moves,
      board_size: activeRules.boardSize,
      win_length: activeRules.winLength,
    };
    recordGame(data, trace);
    showLiveGame(trace, data, count);
    delete data.interrupted_game;
    if (game + 1 < count) await waitWithSignal(Number($('#speed').value), controller.signal);
  }
  return data;
}

function finishLiveSeries(data, total = data.games.length) {
  const last = data.games.length - 1;
  replay = {data, game: last, move: data.games[last].moves.length, playing: false};
  showLiveGame(data.games[last], data, total);
  $('#replay-controls').classList.remove('hidden', 'calculating');
  $('#pause').textContent = t('replay');
  setStatus('seriesCompleteSummary', {
    x: data.summary.x_wins, o: data.summary.o_wins, draws: data.summary.draws,
  });
  render();
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
    const data = newSeriesData(seed);
    const agents = [$('#x-agent').value, $('#o-agent').value];
    const incremental = activeRules.boardSize !== 3 || activeRules.winLength !== 3
      || agents.some(id => capabilities.get(id)?.series_mode === 'incremental');
    seriesCalculation = {paused: false, steps: 0, wake: null, data, count};
    startLiveSeries(data);
    if (incremental) await calculateIncrementalSeries(count, data, controller, revision);
    else await calculateStreamingSeries(count, data, controller, revision);
    if (revision !== gameRevision || mode !== 'ai') return;
    seriesCalculation = null;
    busy = false;
    finishLiveSeries(data);
  } catch (error) {
    if (revision !== gameRevision || mode !== 'ai') return;
    const stopped = seriesCalculation?.stopRequested;
    if (error.name === 'AbortError' && !stopped) return;
    const partial = seriesCalculation?.data;
    const total = seriesCalculation?.count;
    busy = false;
    finished = true;
    seriesCalculation = null;
    if (partial?.games.length) finishLiveSeries(partial, total);
    else {
      $('#replay-controls').classList.add('hidden');
      $('#replay-controls').classList.remove('calculating');
      $('#scoreboard').classList.add('hidden');
    }
    setStatus(stopped ? 'seriesStopped' : error.retryAfter
      ? 'seriesRateLimited' : providerErrorKey(error, 'seriesError'),
    error.retryAfter ? {seconds: error.retryAfter} : {});
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
  scoreThroughReplay();
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
    scoreThroughReplay();
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
    const active = button.dataset.mode === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  const aiMode = mode === 'ai';
  setSettingsExpanded(aiMode);
  document.querySelectorAll('.ai-field').forEach(field => {
    field.style.display = aiMode ? 'grid' : 'none';
  });
  $('#human-agent-field').style.display = mode === 'human' ? 'grid' : 'none';
  $('#start').textContent = t(aiMode ? 'runSeries' : 'newGame');
  renderAgentAvailability();
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
  updateSettingsSummary();
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
  $('#settings-toggle').onclick = () => {
    setSettingsExpanded($('#settings-toggle').getAttribute('aria-expanded') !== 'true');
  };
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
