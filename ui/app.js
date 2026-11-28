(function () {
  'use strict';

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const nowMs = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
  const fmtNum = (value, digits) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toFixed(digits === undefined ? 2 : digits);
  };
  const shortHash = (value) => {
    const text = String(value || '');
    return text.length > 14 ? text.slice(0, 8) + '…' + text.slice(-4) : (text || '—');
  };

  // --------------------------------------------------------------- stations
  const STATIONS = [
    {
      id: 'core', key: '1', name: 'Central Singularity Core', short: 'SINGULARITY CORE',
      script: 'core.py', color: 0x7dd3fc, hex: '#7dd3fc', x: 0, z: 0, shape: 'icosa', game: 'axioms',
      desc: 'The autopoietic heart: perceive, mutate, prove, verify. One full loop per beat; every station in the ring takes its cue from this pulse.',
      explain: 'core.py runs the master loop. In this open world it is the glowing singularity at the center. Enter it to order full cycles, drift audits, or heartbeat re-syncs. Orders are simulated locally in your browser and never touch the live kernel.',
      orders: [
        { label: 'Run one full self-improvement cycle', narrative: 'Co-Pilot ran a full self-improvement cycle', duration: 9000 },
        { label: 'Audit the last three cycles for drift', narrative: 'Co-Pilot audited the last three cycles for drift', duration: 7000 },
        { label: 'Re-sync the autopoietic heartbeat', narrative: 'Co-Pilot re-synced the autopoietic heartbeat', duration: 6000 }
      ]
    },
    {
      id: 'drives', key: '2', name: 'Drive & Discipline Spire', short: 'DRIVES',
      script: 'drives.py', color: 0xf472b6, hex: '#f472b6', x: 0, z: -9.5, shape: 'cone', game: 'mutator',
      desc: 'Five motivational drives steer the loop here. Discipline must never fall below the 0.90 floor, no matter how hard obsession pushes.',
      explain: 'drives.py balances discipline, obsession, anger, madness and motivation. The spire glows harder when discipline is healthy. Order it to cool overthinking pressure or rebalance the drives; the live floor is read-only telemetry, the orders are sandbox simulation.',
      orders: [
        { label: 'Balance the five drives above the discipline floor', narrative: 'Co-Pilot balanced the five drives above the discipline floor', duration: 8000 },
        { label: 'Cool overthinking pressure before it dominates', narrative: 'Co-Pilot cooled overthinking pressure', duration: 7000 }
      ]
    },
    {
      id: 'telos', key: '3', name: 'Goal & Telos Observatory', short: 'TELOS',
      script: 'telos_v2.py', color: 0xfbbf24, hex: '#fbbf24', x: 8.227, z: -4.75, shape: 'octa', game: 'axioms',
      desc: 'Objectives arrive, stream, and retire here. The observatory watches what is pending, in flight, produced, completed or failed.',
      explain: 'telos_v2.py owns the goal ledger and the goal stream. The observatory shows live ledger counts. Orders triage the ledger or stream a fresh batch into the queue; these are simulated prioritisation drills, not writes to the real ledger.',
      orders: [
        { label: 'Triage the objective ledger', narrative: 'Co-Pilot triaged the objective ledger', duration: 6500 },
        { label: 'Stream a fresh goal batch into the queue', narrative: 'Co-Pilot streamed a fresh goal batch', duration: 8500 }
      ]
    },
    {
      id: 'egraph', key: '4', name: 'E-Graph Equivalence Citadel', short: 'E-GRAPH',
      script: 'egraph_engine.py', color: 0x34d399, hex: '#34d399', x: 8.227, z: 4.75, shape: 'knot', game: 'egraph',
      desc: 'Equivalent forms are merged into classes here. The citadel hunts cheaper expressions without losing proof obligations.',
      explain: 'egraph_engine.py saturates rewrite rules into equivalence classes. The citadel knot spins faster while merges are simulated. Order lower-cost extraction or a merge sweep, then open the E-Graph mini-game to try the rewrite rules yourself in the sandbox.',
      orders: [
        { label: 'Find equivalent lower-cost forms', narrative: 'Co-Pilot ran 3 equivalence merges and extracted lower-cost forms', duration: 9000 },
        { label: 'Merge the pending equivalence classes', narrative: 'Co-Pilot merged the pending equivalence classes', duration: 7500 }
      ]
    },
    {
      id: 'prover', key: '5', name: 'Bidirectional Proof & Axiom Sanctum', short: 'PROVER',
      script: 'prover.py + meta_prover.py', color: 0xa78bfa, hex: '#a78bfa', x: 0, z: 9.5, shape: 'box', game: 'axioms',
      desc: 'Forward expansion from the core meets backward expansion from verification in the middle. Proofs close when the frontiers meet.',
      explain: 'prover.py and meta_prover.py search from both ends. The sanctum shows whether the search is solved and how deep it has expanded. Orders prove pending lemmas or widen the backward frontier. The live depth and cache counts are read from real telemetry.',
      orders: [
        { label: 'Prove a pending lemma with bidirectional search', narrative: 'Co-Pilot proved a pending lemma with bidirectional search', duration: 10000 },
        { label: 'Expand the backward frontier from verification', narrative: 'Co-Pilot expanded the backward frontier', duration: 8000 }
      ]
    },
    {
      id: 'devil', key: '6', name: 'Counterfactual & Devil’s Advocate Forge', short: 'DEVIL',
      script: 'devil_advocate.py', color: 0xfb7185, hex: '#fb7185', x: -8.227, z: 4.75, shape: 'tetra', game: 'axioms',
      desc: 'A forge that attacks assumptions before reality does. It hunts reward hacking, stagnation and local convergence.',
      explain: 'devil_advocate.py runs counterfactual attacks. The forge glows with the last challenge intensity. Order stress tests or reward-hacking probes, then use the Axiom Flipper mini-game to see exactly why allow_sorry is rejected.',
      orders: [
        { label: 'Stress-test assumptions with Devil’s Advocate', narrative: 'Co-Pilot stress-tested assumptions with Devil’s Advocate', duration: 8500 },
        { label: 'Probe for reward hacking and local convergence', narrative: 'Co-Pilot probed for reward hacking', duration: 7500 }
      ]
    },
    {
      id: 'manifold', key: '7', name: 'Cross-Domain Manifold Gateways', short: 'MANIFOLD',
      script: 'manifold_bridge.py', color: 0x22d3ee, hex: '#22d3ee', x: -8.227, z: -4.75, shape: 'dodeca', game: 'egraph',
      desc: 'Structures from one domain are compared, mapped and transferred into another. Analogies become reusable machinery.',
      explain: 'manifold_bridge.py finds structural isomorphisms across domains. The gateway transfers an analogy only when the mapping survives comparison. Orders map isomorphisms or transfer a structure; the live counts come straight from manifold telemetry.',
      orders: [
        { label: 'Map cross-domain isomorphisms', narrative: 'Co-Pilot mapped cross-domain isomorphisms', duration: 9000 },
        { label: 'Transfer a structure from physics into logic', narrative: 'Co-Pilot transferred a structure from physics into logic', duration: 8000 }
      ]
    },
    {
      id: 'mutate', key: '8', name: 'Mutation & Verification Foundry', short: 'FOUNDRY',
      script: 'mutate.py + verify.py', color: 0xf97316, hex: '#f97316', x: 17, z: 0, shape: 'obelisk', game: 'mutator',
      desc: 'Candidate rewrites are forged here, then hammered by the verification gate. Nothing ships without passing the high-water mark.',
      explain: 'mutate.py proposes changes and verify.py gates them. The foundry’s obelisk brightens while adversarial mutations are simulated. Order stress tests or a gate sweep; open the Mutator Lab to trade score against regression risk yourself.',
      orders: [
        { label: 'Stress-test with adversarial mutations', narrative: 'Co-Pilot stress-tested the foundry with adversarial mutations', duration: 8500 },
        { label: 'Run a verification gate sweep', narrative: 'Co-Pilot ran a verification gate sweep', duration: 9000 }
      ]
    },
    {
      id: 'science', key: '9', name: '10-Router Science Lab Array', short: 'SCIENCE',
      script: 'science_mcp.py', color: 0x4ade80, hex: '#4ade80', x: -8.5, z: 14.722, shape: 'torus', game: 'science',
      desc: 'Ten scientific routers run surrogates, exact solvers and boundary probes. Speed is cheap; verified precision is the prize.',
      explain: 'science_mcp.py exposes ten routers for simulation and boundary probes. The lab ring tracks router count and FastMCP availability. Orders balance surrogate speed against exact precision. The Surrogate Lab mini-game lets you spend a fixed compute budget yourself.',
      orders: [
        { label: 'Balance surrogate speed vs. exact precision', narrative: 'Co-Pilot balanced surrogate speed against exact precision', duration: 9000 },
        { label: 'Sweep all 10 science routers', narrative: 'Co-Pilot swept all 10 science routers', duration: 8000 }
      ]
    },
    {
      id: 'evolver', key: '0', name: 'UI/UX Self-Evolution Studio', short: 'EVOLVER',
      script: 'ui_evolver.py', color: 0xe879f9, hex: '#e879f9', x: -8.5, z: -14.722, shape: 'icosa', game: 'egraph',
      desc: 'The studio proposes bounded visual genes, validates them, and promotes only what passes the DOM, JS and CSS gates.',
      explain: 'ui_evolver.py studies whitelisted UX telemetry and proposes safe CSS gene changes. The studio tracks evolutions and the active version. Order clarity passes or gene validation, or use Evolve UI in the top bar. Snapshots live in git-ignored ui_backups/.',
      orders: [
        { label: 'Evolve the UI toward clarity', narrative: 'Co-Pilot evolved the UI toward clarity', duration: 9000 },
        { label: 'Validate the current visual genes', narrative: 'Co-Pilot validated the current visual genes', duration: 6000 }
      ]
    }
  ];

  const STATION_BY_ID = {};
  STATIONS.forEach((station) => { STATION_BY_ID[station.id] = station; });

  function stationById(id) { return STATION_BY_ID[id] || null; }

  // ------------------------------------------------------------------- state
  const worldState = {
    stations: {},
    live: {},
    focusId: null,
    nearId: null,
    orderSeq: 0,
    mission: '',
    missionAt: 0,
    demo: { active: false, timers: [] }
  };
  STATIONS.forEach((station) => {
    worldState.stations[station.id] = { active: null, queue: [], completed: 0, intensity: 0.5, userDriving: false };
  });
  window.worldSandboxState = worldState;

  const copilot = {
    paused: false,
    log: [],
    targetId: 'core',
    pos: { x: 0, z: 3 },
    nextThink: nowMs() + 3500
  };
  window.saskCopilotState = copilot;

  const world = {
    three: false,
    scene: null,
    camera: null,
    renderer: null,
    raycaster: null,
    pickables: [],
    views: {},
    avatar: { x: 0, z: 7 },
    avatarTarget: null,
    avatarMesh: null,
    droneMesh: null,
    cam: { yaw: 0.35, pitch: 0.3, dist: 8.5 },
    dragging: false,
    lastFrame: nowMs()
  };

  const live = { connected: false, snapshot: null, announced: false };

  const gameSandboxState = {
    activeTab: 'mutator',
    mutator: freshMutator(),
    egraph: freshEgraph(),
    axioms: freshAxioms(),
    science: freshScience()
  };
  window.gameSandboxState = gameSandboxState;

  // ------------------------------------------------------------ UX telemetry
  const uxQueue = [];

  function track(type, extra) {
    const event = { type, viewport: window.innerWidth + 'x' + window.innerHeight };
    if (extra) {
      Object.keys(extra).forEach((key) => {
        if (['panel', 'game', 'action', 'dwell_ms', 'fps', 'mode', 'detail'].indexOf(key) >= 0) {
          event[key] = extra[key];
        }
      });
    }
    uxQueue.push(event);
    if (uxQueue.length > 40) uxQueue.shift();
  }

  function flushTelemetry() {
    if (!uxQueue.length) return;
    const events = uxQueue.splice(0, 40);
    fetch('/api/ux/telemetry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events })
    }).catch(() => {
      if (uxQueue.length < 20) uxQueue.unshift.apply(uxQueue, events);
    });
  }

  // ---------------------------------------------------------------- toasts
  function toast(message, kind) {
    const host = $('#toast-host');
    if (!host) return;
    const node = document.createElement('div');
    node.className = 'toast' + (kind ? ' ' + kind : '');
    node.textContent = message;
    host.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 260);
    }, 4200);
  }

  // ------------------------------------------------------------- explainer
  const EXPLAINERS = {
    overview: {
      title: 'What am I looking at?',
      body: '<p class="drawer-kicker">OPEN-WORLD COMMAND DECK</p>' +
        '<p>This is a <strong>local, read-only</strong> map of SASK. Each glowing station is one script or script pair. Live signals are real telemetry; every order, mini-game and co-pilot action is an <strong>isolated browser simulation</strong>.</p>' +
        '<ul>' +
        '<li><strong>Move:</strong> WASD or arrow keys, hold Shift to sprint.</li>' +
        '<li><strong>Look:</strong> drag to orbit, scroll to zoom.</li>' +
        '<li><strong>Warp:</strong> press 1–0 or click a waypoint.</li>' +
        '<li><strong>Enter:</strong> walk close and press E, or click a station.</li>' +
        '<li><strong>Co-Pilot:</strong> works autonomously whenever you are not inside a station. Take over any station at any time.</li>' +
        '</ul>' +
        '<p>The co-pilot only moves sandbox orders. The live kernel, ledgers and repository are never written by this page.</p>'
    },
    world: {
      title: 'The world map',
      body: '<p>Stations are arranged as a hub: <strong>core.py</strong> at the center, six operational stations on the inner ring, and three heavy-work stations on the outer ring.</p>' +
        '<p>Each station beacon blends live kernel telemetry (brightness) with sandbox activity (spin and beam intensity). The radar in the corner shows you, the co-pilot drone, and every station.</p>'
    },
    sandbox: {
      title: 'Isolation guarantee',
      body: '<p>The server exposes only read-only telemetry. The browser never posts order or game state. The only outbound writes are whitelisted UX events: type, panel, game, action, dwell time, FPS, viewport, mode and a short detail string.</p>' +
        '<p>Use <strong>Reset World/Sandbox</strong> at any time to clear every order, game and takeover. The badge never leaves the screen.</p>'
    }
  };

  function openExplainer(key) {
    const station = stationById(key);
    const drawer = $('#explainer-drawer');
    if (!drawer) return;
    if (station) {
      $('#explainer-title').textContent = station.name;
      $('#explainer-body').innerHTML =
        '<p class="drawer-kicker">' + station.script + '</p>' +
        '<p>' + station.explain + '</p>' +
        '<p><strong>Sandbox note:</strong> orders for this station simulate progress locally. Live metrics above are read-only.</p>';
    } else {
      const entry = EXPLAINERS[key] || EXPLAINERS.overview;
      $('#explainer-title').textContent = entry.title;
      $('#explainer-body').innerHTML = entry.body;
    }
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    track('explain_open', { panel: 'explainer', detail: key });
  }

  function closeExplainer() {
    const drawer = $('#explainer-drawer');
    if (!drawer) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    setExplainMode(false);
  }

  function setExplainMode(on) {
    document.body.classList.toggle('explain-mode', !!on);
    const button = $('#explain-toggle');
    if (button) button.classList.toggle('btn-accent', !!on);
  }

  // ---------------------------------------------------------- copilot chrome
  function addCopilotLog(text, kind) {
    copilot.log.unshift({
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      text,
      kind: kind || 'copilot'
    });
    copilot.log = copilot.log.slice(0, 12);
    renderCopilotLog();
  }

  function renderCopilotLog() {
    const list = $('#copilot-log');
    if (!list) return;
    list.innerHTML = '';
    copilot.log.forEach((entry) => {
      const li = document.createElement('li');
      if (entry.kind === 'user') li.className = 'cp-user';
      else if (entry.kind === 'alert') li.className = 'cp-alert';
      const time = document.createElement('time');
      time.textContent = entry.time;
      li.appendChild(time);
      li.appendChild(document.createTextNode(entry.text));
      list.appendChild(li);
    });
  }

  function updateCopilotChrome() {
    const state = $('#copilot-state');
    if (state) {
      state.dataset.mode = copilot.paused ? 'paused' : 'autonomous';
      state.innerHTML = '<i class="cp-dot"></i>' + (copilot.paused ? 'CO-PILOT PAUSED' : 'CO-PILOT ONLINE');
    }
    const mode = $('#copilot-mode');
    if (mode) mode.textContent = copilot.paused ? 'paused' : 'autonomous';
    const toggle = $('#copilot-toggle');
    if (toggle) toggle.textContent = copilot.paused ? 'Resume co-pilot' : 'Pause co-pilot';
  }

  function toggleCopilot() {
    copilot.paused = !copilot.paused;
    if (!copilot.paused) copilot.nextThink = nowMs() + 1200;
    updateCopilotChrome();
    addCopilotLog(copilot.paused ? 'Co-Pilot paused by you. Station orders keep their progress.' : 'Co-Pilot resumed. It will pick up queued work.', 'user');
    track('mode_change', { mode: copilot.paused ? 'copilot-pause' : 'copilot-resume' });
  }

  // --------------------------------------------------------------- missions
  const MISSIONS = [
    'Keep every proof backed by verification while the co-pilot clears the E-Graph backlog.',
    'Hold discipline above the 0.90 floor while the Devil’s Advocate stress-tests each claim.',
    'Find cheaper equivalent forms, then send them through the Foundry verification gate.',
    'Balance the science routers: buy speed with surrogates, pay for certainty once.',
    'Map one new cross-domain isomorphism without writing to the live kernel or repository.',
    'Evolve the UI one safe step, validate the genes, and keep the sandbox badge visible.'
  ];

  function rotateMission(force) {
    if (!force && nowMs() < worldState.missionAt) return;
    let next = MISSIONS[Math.floor(Math.random() * MISSIONS.length)];
    if (next === worldState.mission) {
      next = MISSIONS[(MISSIONS.indexOf(next) + 1) % MISSIONS.length];
    }
    worldState.mission = next;
    worldState.missionAt = nowMs() + 45000;
    const line = $('#mission-line');
    if (line) line.textContent = next;
  }

  // ----------------------------------------------------------------- orders
  function makeOrder(stationId, def, user) {
    worldState.orderSeq += 1;
    return {
      id: 'ord-' + worldState.orderSeq,
      stationId,
      label: def.label,
      narrative: def.narrative || def.label,
      duration: def.duration || 8000,
      elapsed: 0,
      paused: false,
      user: !!user,
      startedAt: null
    };
  }

  function enqueueOrder(stationId, def, user) {
    const station = stationById(stationId);
    const state = worldState.stations[stationId];
    if (!station || !state) return;
    if (state.queue.length >= 4) {
      toast('Queue full for ' + station.short + '. Let the co-pilot catch up.', 'bad');
      return;
    }
    const order = makeOrder(stationId, def, user);
    state.queue.push(order);
    if (!state.active) startNext(stationId);
    else addCopilotLog('Queued ' + (user ? 'your order' : 'a co-pilot order') + ': ' + order.label + ' — ' + station.short + '.', user ? 'user' : 'copilot');
    if (worldState.focusId === stationId) renderStationConsole();
  }

  function startNext(stationId) {
    const state = worldState.stations[stationId];
    if (!state || state.active || !state.queue.length) return;
    state.active = state.queue.shift();
    state.active.startedAt = nowMs();
    const station = stationById(stationId);
    addCopilotLog(
      state.active.user
        ? 'Starting your order: ' + state.active.label + ' — ' + station.short + '.'
        : 'Co-Pilot started: ' + state.active.label + ' — ' + station.short + '.',
      state.active.user ? 'user' : 'copilot'
    );
    if (worldState.focusId === stationId) renderStationConsole();
  }

  function completeOrder(stationId) {
    const state = worldState.stations[stationId];
    if (!state || !state.active) return;
    const station = stationById(stationId);
    const order = state.active;
    state.active = null;
    state.completed += 1;
    addCopilotLog(
      order.user
        ? 'Finished your order: ' + order.label + ' — ' + station.short + '.'
        : order.narrative + ' in ' + station.name + '.',
      order.user ? 'user' : 'copilot'
    );
    if (state.queue.length) startNext(stationId);
    if (worldState.focusId === stationId) renderStationConsole();
    rotateMission(true);
  }

  function simulateOrders(deltaSeconds) {
    STATIONS.forEach((station) => {
      const state = worldState.stations[station.id];
      const order = state.active;
      if (!order || order.paused) return;
      const speed = state.userDriving ? 1 + state.intensity * 0.8 : 1;
      order.elapsed += deltaSeconds * 1000 * speed;
      if (order.elapsed >= order.duration) completeOrder(station.id);
    });
    if (worldState.focusId) {
      const state = worldState.stations[worldState.focusId];
      if (state && state.active) {
        const ratio = clamp(state.active.elapsed / state.active.duration, 0, 1);
        const bar = $('#station-progress');
        if (bar) bar.style.width = (ratio * 100).toFixed(1) + '%';
        const label = $('#station-progress-label');
        if (label) {
          label.textContent =
            (state.active.paused ? 'Paused · ' : (state.userDriving ? 'You are co-steering · ' : 'Co-Pilot running · ')) +
            state.active.label + ' · ' + Math.round(ratio * 100) + '%';
        }
      }
    }
  }

  // ----------------------------------------------------------------- copilot
  function copilotThink(now) {
    if (copilot.paused || worldState.demo.active) return;
    if (now < copilot.nextThink) return;
    copilot.nextThink = now + 7000 + Math.random() * 6000;

    const queued = STATIONS.filter((station) => {
      const state = worldState.stations[station.id];
      return !state.active && state.queue.length;
    });
    if (queued.length) {
      const pick = queued[Math.floor(Math.random() * queued.length)];
      copilot.targetId = pick.id;
      startNext(pick.id);
      return;
    }

    if (Math.random() < 0.8) {
      const idle = STATIONS.filter((station) => !worldState.stations[station.id].active);
      if (!idle.length) return;
      const station = idle[Math.floor(Math.random() * idle.length)];
      const def = station.orders[Math.floor(Math.random() * station.orders.length)];
      copilot.targetId = station.id;
      const order = makeOrder(station.id, def, false);
      order.duration = Math.round(order.duration * 0.85);
      worldState.stations[station.id].queue.push(order);
      startNext(station.id);
    } else {
      copilot.targetId = STATIONS[Math.floor(Math.random() * STATIONS.length)].id;
      addCopilotLog('Co-Pilot is patrolling toward ' + stationById(copilot.targetId).short + '.', 'copilot');
    }
  }

  // --------------------------------------------------------------- console
  function openStation(id) {
    const station = stationById(id);
    if (!station) return;
    worldState.focusId = id;
    const drawer = $('#station-console');
    if (drawer) {
      drawer.classList.add('open');
      drawer.setAttribute('aria-hidden', 'false');
    }
    renderStationConsole();
    track('panel_open', { panel: 'station', detail: id });
  }

  function closeStation() {
    const drawer = $('#station-console');
    if (drawer) {
      drawer.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
    }
    worldState.focusId = null;
  }

  function renderStationConsole() {
    const id = worldState.focusId;
    const station = stationById(id);
    if (!station) return;
    const state = worldState.stations[id];
    const liveEntry = worldState.live[id];

    $('#station-script').textContent = station.script;
    $('#station-name').textContent = station.name;
    $('#station-desc').textContent = station.desc;
    $('#station-live').textContent = liveEntry && liveEntry.live ? liveEntry.live : 'awaiting kernel telemetry…';

    renderStationMetrics(id);
    renderOrderList(id);
    renderStationQueue(id);

    const takeover = $('#station-takeover');
    takeover.textContent = state.userDriving ? 'Release to co-pilot' : 'Take over station';
    takeover.classList.toggle('btn-accent', !state.userDriving);

    const pause = $('#station-pause');
    pause.disabled = !state.active;
    pause.textContent = state.active && state.active.paused ? 'Resume order' : 'Pause order';

    const slider = $('#station-intensity');
    slider.value = String(Math.round(state.intensity * 100));
    $('#station-intensity-value').textContent = Math.round(state.intensity * 100) + '%';

    const ratio = state.active ? clamp(state.active.elapsed / state.active.duration, 0, 1) : 0;
    const bar = $('#station-progress');
    bar.style.width = (ratio * 100).toFixed(1) + '%';
    bar.classList.toggle('warn', state.userDriving);
    $('#station-progress-label').textContent = state.active
      ? (state.active.paused ? 'Paused · ' : (state.userDriving ? 'You are co-steering · ' : 'Co-Pilot running · ')) +
        state.active.label + ' · ' + Math.round(ratio * 100) + '%'
      : (state.queue.length ? state.queue.length + ' order(s) queued' : 'Idle — no active order');

    $('#station-game-btn').textContent = 'Play ' + gameLabel(station.game) + ' (sandbox)';
  }

  function renderStationMetrics(id) {
    const box = $('#station-metrics');
    if (!box) return;
    box.innerHTML = '';
    const liveEntry = worldState.live[id];
    if (liveEntry && liveEntry.warning) {
      const warnRow = document.createElement('div');
      warnRow.className = 'station-metric';
      const warnLabel = document.createElement('span');
      warnLabel.textContent = 'bottleneck';
      const warnValue = document.createElement('strong');
      warnValue.textContent = liveEntry.warning;
      warnRow.appendChild(warnLabel);
      warnRow.appendChild(warnValue);
      box.appendChild(warnRow);
    }
    const metrics = liveEntry && liveEntry.metrics;
    if (!metrics) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty';
      empty.textContent = 'No live metrics yet — waiting for the read-only snapshot.';
      box.appendChild(empty);
      return;
    }
    Object.keys(metrics).forEach((key) => {
      const value = metrics[key];
      if (value === null || value === undefined) return;
      const row = document.createElement('div');
      row.className = 'station-metric';
      const label = document.createElement('span');
      label.textContent = key.replace(/_/g, ' ');
      const strong = document.createElement('strong');
      if (typeof value === 'number') {
        strong.textContent = Math.abs(value) >= 100 ? String(Math.round(value)) : String(value);
      } else if (typeof value === 'boolean') {
        strong.textContent = value ? 'on' : 'off';
      } else {
        strong.textContent = String(value);
      }
      row.appendChild(label);
      row.appendChild(strong);
      box.appendChild(row);
    });
  }

  function renderOrderList(id) {
    const station = stationById(id);
    const list = $('#order-list');
    if (!station || !list) return;
    list.innerHTML = '';
    station.orders.forEach((def) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'order-btn';
      const strong = document.createElement('strong');
      strong.textContent = def.label;
      const meta = document.createElement('em');
      meta.textContent = Math.round(def.duration / 1000) + 's sandbox simulation · 1-click';
      button.appendChild(strong);
      button.appendChild(meta);
      button.addEventListener('click', () => {
        enqueueOrder(id, def, true);
        track('game_action', { game: 'world-order', action: 'template', detail: id });
      });
      list.appendChild(button);
    });
  }

  function queueRow(order, source) {
    const li = document.createElement('li');
    li.className = 'queue-item';
    const label = document.createElement('span');
    label.className = 'q-label';
    label.textContent = order.label;
    const chip = document.createElement('span');
    chip.className = 'q-src' + (order.user ? ' user' : '');
    chip.textContent = source === 'running' ? 'RUNNING' : (order.user ? 'YOU' : 'CO-PILOT');
    li.appendChild(label);
    li.appendChild(chip);
    return li;
  }

  function renderStationQueue(id) {
    const list = $('#order-queue');
    if (!list) return;
    const state = worldState.stations[id];
    list.innerHTML = '';
    if (state.active) list.appendChild(queueRow(state.active, 'running'));
    state.queue.forEach((order) => list.appendChild(queueRow(order, order.user ? 'user' : 'copilot')));
    if (!state.active && !state.queue.length) {
      const li = document.createElement('li');
      li.className = 'queue-empty';
      li.textContent = 'No orders queued. Take over or pick a 1-click order.';
      list.appendChild(li);
    }
  }

  function toggleTakeover() {
    const id = worldState.focusId;
    const state = worldState.stations[id];
    const station = stationById(id);
    if (!state || !station) return;
    state.userDriving = !state.userDriving;
    addCopilotLog(
      state.userDriving
        ? 'You took over ' + station.short + '. Co-Pilot is standing by for the rest of the world.'
        : 'You released ' + station.short + ' back to the co-pilot.',
      'user'
    );
    renderStationConsole();
    track('mode_change', { mode: state.userDriving ? 'takeover' : 'handback', detail: id });
  }

  function togglePause() {
    const id = worldState.focusId;
    const state = worldState.stations[id];
    if (!state || !state.active) return;
    state.active.paused = !state.active.paused;
    addCopilotLog(
      (state.active.paused ? 'Paused' : 'Resumed') + ' "' + state.active.label + '" in ' + stationById(id).short + '.',
      'user'
    );
    renderStationConsole();
  }

  // ------------------------------------------------------------------ world
  function initWorld() {
    const canvas = $('#world-canvas');
    if (!canvas) return;
    if (typeof THREE === 'undefined') {
      startFallbackWorld();
      return;
    }
    try {
      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setClearColor(0x04070f, 1);

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x04070f);
      scene.fog = new THREE.FogExp2(0x04070f, 0.02);

      const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 400);
      camera.position.set(0, 6.5, 13);

      scene.add(new THREE.AmbientLight(0x8899bb, 0.8));
      scene.add(new THREE.HemisphereLight(0xbfe6ff, 0x0a1220, 0.7));
      const sun = new THREE.DirectionalLight(0xcfeaff, 1.0);
      sun.position.set(14, 24, 10);
      scene.add(sun);
      const coreLight = new THREE.PointLight(0x7dd3fc, 1.6, 60);
      coreLight.position.set(0, 4, 0);
      scene.add(coreLight);

      const ground = new THREE.Mesh(
        new THREE.CircleGeometry(34, 96),
        new THREE.MeshStandardMaterial({ color: 0x050b15, roughness: 0.82, metalness: 0.45, emissive: 0x02060c, emissiveIntensity: 0.8 })
      );
      ground.rotation.x = -Math.PI / 2;
      scene.add(ground);

      const grid = new THREE.GridHelper(68, 34, 0x123044, 0x0a1a26);
      grid.position.y = 0.02;
      grid.material.transparent = true;
      grid.material.opacity = 0.55;
      scene.add(grid);

      [9.5, 17].forEach((radius) => {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(radius, 0.035, 8, 128),
          new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.18 })
        );
        ring.rotation.x = Math.PI / 2;
        ring.position.y = 0.05;
        scene.add(ring);
      });

      const starGeometry = new THREE.BufferGeometry();
      const starPositions = new Float32Array(900 * 3);
      for (let i = 0; i < 900; i += 1) {
        const radius = 60 + Math.random() * 90;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        starPositions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
        starPositions[i * 3 + 1] = Math.abs(radius * Math.cos(phi)) + 4;
        starPositions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);
      }
      starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
      scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0x9fd8ff, size: 0.7, transparent: true, opacity: 0.65 })));

      world.scene = scene;
      world.camera = camera;
      world.renderer = renderer;
      world.raycaster = new THREE.Raycaster();

      buildAvatar(scene);
      buildDrone(scene);
      STATIONS.forEach((station) => buildStation(scene, station));

      world.three = true;
      addCopilotLog('Co-Pilot online in the 3D world. Watching 10 stations — live kernel untouched.', 'system');
    } catch (err) {
      startFallbackWorld();
    }
  }

  function startFallbackWorld() {
    world.three = false;
    const caption = $('#minimap-caption');
    if (caption) caption.textContent = '2D fallback · WASD move · click a station';
    addCopilotLog('Three.js unavailable — running the 2D fallback world. Everything else still works.', 'system');
  }

  function buildAvatar(scene) {
    const avatar = new THREE.Group();
    const orb = new THREE.Mesh(
      new THREE.SphereGeometry(0.42, 24, 18),
      new THREE.MeshStandardMaterial({ color: 0x0b1a2b, emissive: 0x38bdf8, emissiveIntensity: 1.2, roughness: 0.25, metalness: 0.6 })
    );
    avatar.add(orb);
    const halo = new THREE.Mesh(
      new THREE.TorusGeometry(0.7, 0.03, 8, 40),
      new THREE.MeshBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.7 })
    );
    halo.rotation.x = Math.PI / 2;
    halo.position.y = -0.1;
    avatar.add(halo);
    avatar.position.set(world.avatar.x, 0.55, world.avatar.z);
    scene.add(avatar);
    world.avatarMesh = avatar;
  }

  function buildDrone(scene) {
    const drone = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.32, 0),
      new THREE.MeshStandardMaterial({ color: 0x1a1205, emissive: 0xfbbf24, emissiveIntensity: 1.4, roughness: 0.3, metalness: 0.5 })
    );
    drone.add(body);
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.5, 0.02, 6, 24),
      new THREE.MeshBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.65 })
    );
    ring.rotation.x = Math.PI / 2;
    drone.add(ring);
    drone.position.set(0, 2.8, 3);
    scene.add(drone);
    world.droneMesh = drone;
  }

  function makeStationStructure(station, color) {
    let geometry;
    switch (station.shape) {
      case 'icosa': geometry = new THREE.IcosahedronGeometry(station.id === 'core' ? 1.15 : 0.9, 1); break;
      case 'cone': geometry = new THREE.ConeGeometry(0.8, 1.9, 6); break;
      case 'octa': geometry = new THREE.OctahedronGeometry(1.05, 0); break;
      case 'knot': geometry = new THREE.TorusKnotGeometry(0.55, 0.17, 80, 10); break;
      case 'box': geometry = new THREE.BoxGeometry(1.2, 1.2, 1.2); break;
      case 'tetra': geometry = new THREE.TetrahedronGeometry(1.15, 0); break;
      case 'dodeca': geometry = new THREE.DodecahedronGeometry(0.98, 0); break;
      case 'obelisk': geometry = new THREE.CylinderGeometry(0.42, 0.72, 1.9, 6); break;
      case 'torus': geometry = new THREE.TorusGeometry(0.8, 0.18, 12, 36); break;
      default: geometry = new THREE.IcosahedronGeometry(0.95, 0);
    }
    return new THREE.Mesh(
      geometry,
      new THREE.MeshStandardMaterial({ color: 0x0c1727, emissive: color, emissiveIntensity: 0.55, roughness: 0.3, metalness: 0.7 })
    );
  }

  function makeLabelSprite(station) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 160;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, 512, 160);

    function roundedRect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + w - r, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + r);
      ctx.lineTo(x + w, y + h - r);
      ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
      ctx.lineTo(x + r, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - r);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.closePath();
    }

    ctx.fillStyle = 'rgba(4, 10, 18, 0.74)';
    roundedRect(6, 30, 500, 100, 22);
    ctx.fill();
    ctx.strokeStyle = station.hex;
    ctx.lineWidth = 2;
    roundedRect(6, 30, 500, 100, 22);
    ctx.stroke();

    ctx.textAlign = 'center';
    ctx.fillStyle = '#eaf6ff';
    ctx.font = '600 32px "Segoe UI", sans-serif';
    const label = station.name.length > 30 ? station.name.slice(0, 29) + '…' : station.name;
    ctx.fillText(label, 256, 78);
    ctx.fillStyle = station.hex;
    ctx.font = '500 22px Consolas, monospace';
    ctx.fillText(station.script, 256, 112);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false, opacity: 0.94 });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(2.6, 0.8125, 1);
    sprite.renderOrder = 4;
    return sprite;
  }

  function buildStation(scene, station) {
    const group = new THREE.Group();
    group.position.set(station.x, 0, station.z);
    const color = new THREE.Color(station.color);
    const isCore = station.id === 'core';

    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(isCore ? 2.0 : 1.45, isCore ? 2.3 : 1.7, 0.5, 28),
      new THREE.MeshStandardMaterial({ color: 0x0a1524, roughness: 0.6, metalness: 0.6, emissive: color, emissiveIntensity: 0.12 })
    );
    base.position.y = 0.25;
    group.add(base);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(isCore ? 2.05 : 1.5, 0.05, 8, 48),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.75 })
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 0.52;
    group.add(ring);

    const structure = makeStationStructure(station, color);
    structure.position.y = isCore ? 1.65 : 1.55;
    group.add(structure);

    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(isCore ? 0.28 : 0.2, 16, 12),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 })
    );
    beacon.position.y = isCore ? 3.4 : 3.1;
    group.add(beacon);

    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(0.055, 0.055, 7, 6, 1, true),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending, depthWrite: false })
    );
    beam.position.y = 3.6;
    group.add(beam);

    const label = makeLabelSprite(station);
    label.position.y = isCore ? 5.0 : 4.6;
    group.add(label);

    const warnRing = new THREE.Mesh(
      new THREE.TorusGeometry(isCore ? 2.6 : 2.05, 0.04, 8, 56),
      new THREE.MeshBasicMaterial({ color: 0xff4d6d, transparent: true, opacity: 0, depthWrite: false })
    );
    warnRing.rotation.x = Math.PI / 2;
    warnRing.position.y = 0.18;
    group.add(warnRing);

    group.userData.stationId = station.id;
    base.userData.stationId = station.id;
    structure.userData.stationId = station.id;
    scene.add(group);

    world.pickables.push(base, structure);
    world.views[station.id] = { group, base, ring, structure, beacon, beam, label, warnRing };
  }

  function resizeWorld() {
    const canvas = $('#world-canvas');
    const shell = $('#world-shell');
    if (!canvas || !shell) return;
    const width = Math.max(1, shell.clientWidth);
    const height = Math.max(1, shell.clientHeight);
    if (world.three && world.renderer && world.camera) {
      world.renderer.setSize(width, height, false);
      world.camera.aspect = width / height;
      world.camera.updateProjectionMatrix();
    } else {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
    }
  }

  // ------------------------------------------------------------------ input
  const keys = { w: false, a: false, s: false, d: false, up: false, down: false, left: false, right: false, sprint: false };

  function setupInput() {
    const canvas = $('#world-canvas');
    if (!canvas) return;
    let downX = 0;
    let downY = 0;
    let moved = 0;
    let downAt = 0;

    canvas.addEventListener('pointerdown', (event) => {
      world.dragging = true;
      downX = event.clientX;
      downY = event.clientY;
      moved = 0;
      downAt = Date.now();
      try { canvas.setPointerCapture(event.pointerId); } catch (err) { /* noop */ }
    });

    canvas.addEventListener('pointermove', (event) => {
      if (!world.dragging) return;
      const dx = event.clientX - downX;
      const dy = event.clientY - downY;
      downX = event.clientX;
      downY = event.clientY;
      moved += Math.abs(dx) + Math.abs(dy);
      if (world.three) {
        world.cam.yaw -= dx * 0.0055;
        world.cam.pitch = clamp(world.cam.pitch + dy * 0.004, 0.08, 1.15);
      }
    });

    const endDrag = (event) => {
      if (!world.dragging) return;
      world.dragging = false;
      if (moved < 6 && Date.now() - downAt < 420) pickAt(event);
    };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', () => { world.dragging = false; });

    canvas.addEventListener('wheel', (event) => {
      if (!world.three) return;
      event.preventDefault();
      world.cam.dist = clamp(world.cam.dist + event.deltaY * 0.0025, 4.2, 15);
    }, { passive: false });

    canvas.addEventListener('contextmenu', (event) => event.preventDefault());
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('resize', resizeWorld);
  }

  function onKeyDown(event) {
    const tag = (event.target && event.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const key = (event.key || '').toLowerCase();

    if (key === 'escape') {
      closeStation();
      closeGames();
      closeExplainer();
      closeReliability();
      cancelDemo();
      return;
    }
    if (key === 'e') {
      if (worldState.nearId) openStation(worldState.nearId);
      return;
    }
    if (key === 'w' || key === 'arrowup') { keys.w = true; keys.up = true; event.preventDefault(); return; }
    if (key === 's' || key === 'arrowdown') { keys.s = true; keys.down = true; event.preventDefault(); return; }
    if (key === 'a' || key === 'arrowleft') { keys.a = true; keys.left = true; event.preventDefault(); return; }
    if (key === 'd' || key === 'arrowright') { keys.d = true; keys.right = true; event.preventDefault(); return; }
    if (key === 'shift') { keys.sprint = true; return; }
    if (key >= '0' && key <= '9') {
      const station = STATIONS.find((item) => item.key === key);
      if (station) {
        event.preventDefault();
        warpTo(station.id);
      }
    }
  }

  function onKeyUp(event) {
    const key = (event.key || '').toLowerCase();
    if (key === 'w' || key === 'arrowup') { keys.w = false; keys.up = false; }
    if (key === 's' || key === 'arrowdown') { keys.s = false; keys.down = false; }
    if (key === 'a' || key === 'arrowleft') { keys.a = false; keys.left = false; }
    if (key === 'd' || key === 'arrowright') { keys.d = false; keys.right = false; }
    if (key === 'shift') keys.sprint = false;
  }

  function pickAt(event) {
    if (world.three && world.raycaster && world.camera) {
      const canvas = $('#world-canvas');
      const rect = canvas.getBoundingClientRect();
      const nx = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      const ny = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      world.raycaster.setFromCamera({ x: nx, y: ny }, world.camera);
      const hits = world.raycaster.intersectObjects(world.pickables, false);
      if (hits.length) {
        let object = hits[0].object;
        while (object && !object.userData.stationId) object = object.parent;
        if (object && object.userData.stationId) warpTo(object.userData.stationId);
      }
      return;
    }
    const canvas = $('#world-canvas');
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width, rect.height) / 64;
    let closest = null;
    let closestDistance = 28;
    STATIONS.forEach((station) => {
      const px = rect.left + rect.width / 2 + station.x * scale;
      const py = rect.top + rect.height / 2 + station.z * scale;
      const distance = Math.hypot(event.clientX - px, event.clientY - py);
      if (distance < closestDistance) {
        closestDistance = distance;
        closest = station;
      }
    });
    if (closest) warpTo(closest.id);
  }

  function warpTo(id) {
    const station = stationById(id);
    if (!station) return;
    const length = Math.hypot(station.x, station.z);
    let targetX;
    let targetZ;
    if (length < 0.5) {
      targetX = 0;
      targetZ = 3.4;
    } else {
      targetX = station.x * (1 - 2.9 / length);
      targetZ = station.z * (1 - 2.9 / length);
    }
    world.avatarTarget = { x: targetX, z: targetZ };
    if (world.three) {
      const dx = station.x - targetX;
      const dz = station.z - targetZ;
      world.cam.yaw = Math.atan2(-dx, -dz);
    }
    $$('.waypoint').forEach((button) => {
      button.classList.toggle('active', button.dataset.warp === id);
    });
    openStation(id);
    track('viz_interact', { panel: 'world', action: 'warp', detail: id });
  }

  function buildWaypoints() {
    const grid = $('#waypoint-grid');
    if (!grid) return;
    grid.innerHTML = '';
    STATIONS.forEach((station) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'waypoint';
      button.dataset.warp = station.id;
      button.style.setProperty('--wp-color', station.hex);
      const key = document.createElement('span');
      key.className = 'wp-key';
      key.textContent = station.key;
      const name = document.createElement('span');
      name.className = 'wp-name';
      name.textContent = station.short;
      const dot = document.createElement('span');
      dot.className = 'wp-dot';
      dot.style.background = station.hex;
      dot.style.color = station.hex;
      button.appendChild(key);
      button.appendChild(name);
      button.appendChild(dot);
      button.title = station.name + ' — ' + station.script;
      button.addEventListener('click', () => warpTo(station.id));
      grid.appendChild(button);
    });
  }

  // ------------------------------------------------------------------ demo
  const DEMO_STEPS = [
    { at: 0, station: 'core', order: 0, text: '1/6 · Core: run one full self-improvement cycle' },
    { at: 4500, station: 'egraph', order: 0, text: '2/6 · E-Graph Citadel: find lower-cost equivalent forms' },
    { at: 9000, station: 'prover', order: 0, text: '3/6 · Prover Sanctum: prove a pending lemma' },
    { at: 13500, station: 'devil', order: 0, text: '4/6 · Devil’s Advocate Forge: stress-test assumptions' },
    { at: 18000, station: 'science', order: 0, text: '5/6 · Science Lab: balance surrogate vs exact precision' },
    { at: 22500, station: null, order: null, close: true, text: '6/6 · Releasing control — watch the co-pilot carry the queue' },
    { at: 28500, station: null, order: null, close: true, text: 'Demo complete' }
  ];

  function startDemo() {
    if (worldState.demo.active) {
      cancelDemo();
      return;
    }
    worldState.demo.active = true;
    const banner = $('#demo-banner');
    if (banner) banner.hidden = false;
    const button = $('#demo-btn');
    if (button) button.textContent = 'Stop demo';
    addCopilotLog('Guided demo started. Follow the banner — every action is sandbox-only.', 'system');
    DEMO_STEPS.forEach((step) => {
      const timer = setTimeout(() => runDemoStep(step), step.at);
      worldState.demo.timers.push(timer);
    });
    track('mode_change', { mode: 'demo', detail: 'start' });
  }

  function runDemoStep(step) {
    if (!worldState.demo.active) return;
    const label = $('#demo-step');
    if (label) label.textContent = step.text;
    if (step.station) {
      warpTo(step.station);
      const station = stationById(step.station);
      const def = station && station.orders[step.order || 0];
      if (def) enqueueOrder(step.station, def, true);
    }
    if (step.close) closeStation();
    if (step.text === 'Demo complete') finishDemo();
  }

  function finishDemo() {
    worldState.demo.timers.forEach((timer) => clearTimeout(timer));
    worldState.demo.timers = [];
    worldState.demo.active = false;
    const banner = $('#demo-banner');
    if (banner) banner.hidden = true;
    const button = $('#demo-btn');
    if (button) button.textContent = 'Show Me (30s)';
    closeStation();
    addCopilotLog('Demo complete. Co-Pilot resumes autonomous work across all 10 stations.', 'copilot');
    toast('Demo complete. The co-pilot keeps working in the background.', 'good');
    track('mode_change', { mode: 'demo', detail: 'complete' });
  }

  function cancelDemo() {
    if (!worldState.demo.active) return;
    worldState.demo.timers.forEach((timer) => clearTimeout(timer));
    worldState.demo.timers = [];
    worldState.demo.active = false;
    const banner = $('#demo-banner');
    if (banner) banner.hidden = true;
    const button = $('#demo-btn');
    if (button) button.textContent = 'Show Me (30s)';
    addCopilotLog('Demo stopped. Co-Pilot resumes its own queue.', 'copilot');
  }

  // --------------------------------------------------------------- visuals
  function updatePlayer(delta) {
    let forward = 0;
    let sideways = 0;
    if (keys.w || keys.up) forward += 1;
    if (keys.s || keys.down) forward -= 1;
    if (keys.d || keys.right) sideways += 1;
    if (keys.a || keys.left) sideways -= 1;
    if (!forward && !sideways) return;

    world.avatarTarget = null;
    const yaw = world.cam.yaw;
    const fx = -Math.sin(yaw);
    const fz = -Math.cos(yaw);
    const rx = Math.cos(yaw);
    const rz = -Math.sin(yaw);
    let dx = fx * forward + rx * sideways;
    let dz = fz * forward + rz * sideways;
    const length = Math.hypot(dx, dz) || 1;
    dx /= length;
    dz /= length;
    const speed = (keys.sprint ? 9 : 4.8) * delta;
    world.avatar.x += dx * speed;
    world.avatar.z += dz * speed;
    const distance = Math.hypot(world.avatar.x, world.avatar.z);
    if (distance > 27) {
      world.avatar.x = (world.avatar.x / distance) * 27;
      world.avatar.z = (world.avatar.z / distance) * 27;
    }
  }

  function updateVisuals(time, delta) {
    if (!world.three) return;
    if (world.avatarMesh) {
      if (world.avatarTarget) {
        world.avatar.x = lerp(world.avatar.x, world.avatarTarget.x, Math.min(1, delta * 9));
        world.avatar.z = lerp(world.avatar.z, world.avatarTarget.z, Math.min(1, delta * 9));
        if (Math.hypot(world.avatar.x - world.avatarTarget.x, world.avatar.z - world.avatarTarget.z) < 0.06) {
          world.avatar.x = world.avatarTarget.x;
          world.avatar.z = world.avatarTarget.z;
          world.avatarTarget = null;
        }
      }
      world.avatarMesh.position.set(world.avatar.x, 0.55 + Math.sin(time * 2.4) * 0.08, world.avatar.z);
      world.avatarMesh.rotation.y += delta * 0.8;
    }

    STATIONS.forEach((station, index) => {
      const view = world.views[station.id];
      if (!view) return;
      const activity = worldState.stations[station.id].active ? 1 : 0;
      const livePulse = worldState.live[station.id] ? Number(worldState.live[station.id].pulse) || 0 : 0.3;
      view.structure.rotation.y += delta * (0.35 + activity * 1.8);
      if (station.shape === 'knot' || station.shape === 'torus') {
        view.structure.rotation.x += delta * (0.2 + activity * 0.7);
      }
      const scale = 1 + Math.sin(time * 1.6 + index) * 0.03 + activity * 0.08;
      view.beacon.scale.setScalar(scale);
      view.beacon.position.y = (station.id === 'core' ? 3.4 : 3.1) + Math.sin(time * 2.4 + index) * 0.12;
      view.beam.material.opacity = 0.05 + livePulse * 0.16 + activity * 0.2;
      view.ring.material.opacity = 0.45 + Math.sin(time * 2 + index) * 0.14 + activity * 0.2;
      view.structure.material.emissiveIntensity = 0.45 + activity * 0.7 + Math.sin(time * 2 + index) * 0.1;
      if (view.warnRing) {
        const liveEntry = worldState.live[station.id] || {};
        const warning = clamp(Number(liveEntry.warning_severity) || 0, 0, 1);
        if (warning > 0) {
          view.warnRing.material.opacity = 0.24 + warning * 0.4 + Math.sin(time * 3 + index) * 0.12;
          view.warnRing.material.color.setHex(warning >= 0.7 ? 0xff4d6d : 0xfbbf24);
          view.warnRing.scale.setScalar(1 + Math.sin(time * 3 + index) * 0.04);
        } else {
          view.warnRing.material.opacity = 0;
        }
      }
      if (view.label && world.camera) {
        const camDist = Math.hypot(world.camera.position.x - station.x, world.camera.position.z - station.z);
        view.label.visible = camDist > 6.5;
      }
    });

    const target = stationById(copilot.targetId) || STATIONS[0];
    const goalX = target.x + 1.2;
    const goalZ = target.z + 1.0;
    copilot.pos.x = lerp(copilot.pos.x, goalX, Math.min(1, delta * 1.6));
    copilot.pos.z = lerp(copilot.pos.z, goalZ, Math.min(1, delta * 1.6));
    if (world.droneMesh) {
      world.droneMesh.position.set(copilot.pos.x, 2.6 + Math.sin(time * 2) * 0.25, copilot.pos.z);
      world.droneMesh.rotation.y += delta * 1.4;
    }
  }

  function updateCamera(delta) {
    if (!world.three || !world.camera || !world.avatarMesh) return;
    const target = world.avatarMesh.position;
    const cosPitch = Math.cos(world.cam.pitch);
    const px = target.x + Math.sin(world.cam.yaw) * cosPitch * world.cam.dist;
    const py = target.y + Math.sin(world.cam.pitch) * world.cam.dist + 1.3;
    const pz = target.z + Math.cos(world.cam.yaw) * cosPitch * world.cam.dist;
    const blend = Math.min(1, delta * 6);
    world.camera.position.x = lerp(world.camera.position.x, px, blend);
    world.camera.position.y = lerp(world.camera.position.y, py, blend);
    world.camera.position.z = lerp(world.camera.position.z, pz, blend);
    world.camera.lookAt(target.x, target.y + 1.1, target.z);
  }

  function updateProximity() {
    let nearest = null;
    let nearestDistance = 3.4;
    STATIONS.forEach((station) => {
      const distance = Math.hypot(world.avatar.x - station.x, world.avatar.z - station.z);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearest = station;
      }
    });
    worldState.nearId = nearest ? nearest.id : null;
    const hint = $('#station-hint');
    if (!hint) return;
    if (nearest) {
      hint.hidden = false;
      $('#station-hint-name').textContent = nearest.name;
    } else {
      hint.hidden = true;
    }
  }

  function drawFallback(time) {
    const canvas = $('#world-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = canvas.width / ratio;
    const height = canvas.height / ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const gradient = ctx.createRadialGradient(width / 2, height / 2, 20, width / 2, height / 2, Math.max(width, height) * 0.7);
    gradient.addColorStop(0, '#0b1a2b');
    gradient.addColorStop(1, '#04070f');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    const scale = Math.min(width, height) / 64;
    const cx = width / 2;
    const cy = height / 2;

    [9.5, 17].forEach((radius) => {
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(45, 212, 191, 0.18)';
      ctx.arc(cx, cy, radius * scale, 0, Math.PI * 2);
      ctx.stroke();
    });

    STATIONS.forEach((station) => {
      const px = cx + station.x * scale;
      const py = cy + station.z * scale;
      const state = worldState.stations[station.id];
      const activity = state.active ? 1 : 0;
      ctx.beginPath();
      ctx.fillStyle = station.hex;
      ctx.globalAlpha = activity ? 1 : 0.72;
      ctx.arc(px, py, activity ? 10 + Math.sin(time * 4) * 1.5 : 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      if (worldState.focusId === station.id) {
        ctx.strokeStyle = '#eaf7ff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(px, py, 14, 0, Math.PI * 2);
        ctx.stroke();
      }
      const warnSev = clamp(Number((worldState.live[station.id] || {}).warning_severity) || 0, 0, 1);
      if (warnSev > 0) {
        ctx.strokeStyle = warnSev >= 0.7 ? 'rgba(255, 77, 109, 0.85)' : 'rgba(251, 191, 36, 0.8)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(px, py, 15 + Math.sin(time * 3) * 2, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.fillStyle = '#dbe9f8';
      ctx.font = '11px Consolas, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(station.short, px, py - 16);
    });

    ctx.fillStyle = '#fbbf24';
    ctx.beginPath();
    ctx.arc(cx + copilot.pos.x * scale, cy + copilot.pos.z * scale, 4, 0, Math.PI * 2);
    ctx.fill();

    const ax = cx + world.avatar.x * scale;
    const ay = cy + world.avatar.z * scale;
    ctx.save();
    ctx.translate(ax, ay);
    ctx.rotate(Math.atan2(-Math.cos(world.cam.yaw), -Math.sin(world.cam.yaw)) + Math.PI / 2);
    ctx.fillStyle = '#eaf7ff';
    ctx.beginPath();
    ctx.moveTo(0, -8);
    ctx.lineTo(5, 6);
    ctx.lineTo(-5, 6);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function renderMinimap(time) {
    const canvas = $('#minimap-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    const cx = width / 2;
    const cy = height / 2;
    const scale = (width / 2 - 10) / 20;

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 1;
    [9.5, 17].forEach((radius) => {
      ctx.beginPath();
      ctx.arc(cx, cy, radius * scale, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.beginPath();
    ctx.moveTo(cx, 6);
    ctx.lineTo(cx, height - 6);
    ctx.moveTo(6, cy);
    ctx.lineTo(width - 6, cy);
    ctx.stroke();

    STATIONS.forEach((station) => {
      const px = cx + station.x * scale;
      const py = cy + station.z * scale;
      const active = !!worldState.stations[station.id].active;
      ctx.beginPath();
      ctx.fillStyle = station.hex;
      ctx.globalAlpha = active ? 1 : 0.75;
      ctx.arc(px, py, active ? 4 + Math.sin(time * 4) * 0.8 : 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      if (worldState.focusId === station.id) {
        ctx.strokeStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(px, py, 6.5, 0, Math.PI * 2);
        ctx.stroke();
      }
      const warnSev = clamp(Number((worldState.live[station.id] || {}).warning_severity) || 0, 0, 1);
      if (warnSev > 0) {
        ctx.strokeStyle = warnSev >= 0.7 ? 'rgba(255, 77, 109, 0.9)' : 'rgba(251, 191, 36, 0.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(px, py, 7 + Math.sin(time * 3) * 1.2, 0, Math.PI * 2);
        ctx.stroke();
      }
    });

    ctx.fillStyle = '#fbbf24';
    ctx.beginPath();
    ctx.arc(cx + copilot.pos.x * scale, cy + copilot.pos.z * scale, 3, 0, Math.PI * 2);
    ctx.fill();

    const ax = cx + world.avatar.x * scale;
    const ay = cy + world.avatar.z * scale;
    ctx.save();
    ctx.translate(ax, ay);
    ctx.rotate(Math.atan2(-Math.cos(world.cam.yaw), -Math.sin(world.cam.yaw)) + Math.PI / 2);
    ctx.fillStyle = '#eaf7ff';
    ctx.beginPath();
    ctx.moveTo(0, -6);
    ctx.lineTo(4.5, 5);
    ctx.lineTo(-4.5, 5);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function animate(now) {
    const time = now / 1000;
    const delta = Math.min(0.12, Math.max(0.001, (now - world.lastFrame) / 1000));
    world.lastFrame = now;

    updatePlayer(delta);
    simulateOrders(delta);
    copilotThink(now);
    updateVisuals(time, delta);
    updateCamera(delta);
    updateProximity();
    rotateMission(false);
    renderMinimap(time);

    if (world.three) {
      if (world.renderer && world.scene && world.camera) world.renderer.render(world.scene, world.camera);
    } else {
      drawFallback(time);
    }
    requestAnimationFrame(animate);
  }

  // ----------------------------------------------------------------- reset
  function resetWorld() {
    STATIONS.forEach((station) => {
      const state = worldState.stations[station.id];
      state.active = null;
      state.queue = [];
      state.completed = 0;
      state.intensity = 0.5;
      state.userDriving = false;
    });
    copilot.log = [];
    copilot.paused = false;
    copilot.targetId = 'core';
    copilot.nextThink = nowMs() + 2500;
    world.avatar.x = 0;
    world.avatar.z = 7;
    world.avatarTarget = null;
    if (world.avatarMesh) world.avatarMesh.position.set(0, 0.55, 7);
    $$('.waypoint').forEach((button) => button.classList.remove('active'));
    cancelDemo();
    closeStation();
    resetGames(true);
    updateCopilotChrome();
    addCopilotLog('World reset. Every sandbox order, takeover and mini-game is cleared.', 'system');
    addCopilotLog('Co-Pilot online. Watching 10 stations — live kernel untouched.', 'copilot');
    toast('World and sandbox reset. Live kernel untouched.', 'good');
    track('mode_change', { mode: 'reset' });
  }

  // ------------------------------------------------------------------ games
  function freshMutator() {
    return { score: 100, risk: 0, budget: 100, regressions: 0, log: ['Toy codebase booted with 100 points of headroom.'] };
  }

  function freshEgraph() {
    return {
      selected: [],
      merged: [],
      merges: 0,
      classes: 6,
      goals: 0,
      verdict: 'Select two expressions you believe are equivalent.',
      log: ['Six expression classes loaded. Three provable pairs exist.']
    };
  }

  function freshAxioms() {
    return {
      on: { ax_verify: true, ax_floor: true, ax_devil: true, ax_sorry: false, ax_stream: true },
      verdict: 'All required axioms hold.',
      log: ['Toy proof graph loaded with safeguards on.']
    };
  }

  function freshScience() {
    return {
      budget: 10,
      accuracy: 24,
      time: 0,
      target: 98,
      trained: 0,
      verified: false,
      verdict: 'Spend compute carefully: surrogates are fast, exact solvers are certain.',
      log: ['Compute budget: 10 units. Target: 98% accuracy.']
    };
  }

  const EXPRS = [
    { id: 'e1', text: 'a + 0' },
    { id: 'e2', text: '0 + a' },
    { id: 'e3', text: 'a * 1' },
    { id: 'e4', text: '1 * a' },
    { id: 'e5', text: '(a + b) + c' },
    { id: 'e6', text: 'a + (b + c)' }
  ];
  const EQ_PAIRS = [['e1', 'e2'], ['e3', 'e4'], ['e5', 'e6']];

  const AXIOMS = [
    { id: 'ax_verify', text: 'Every rewrite must pass verification', required: true },
    { id: 'ax_floor', text: 'Discipline floor stays at or above 0.90', required: true },
    { id: 'ax_devil', text: 'Devil’s Advocate may challenge any proof', required: true },
    { id: 'ax_sorry', text: 'allow_sorry — trust proofs without checking', required: false, danger: true },
    { id: 'ax_stream', text: 'Goal stream may reorder priorities', required: false }
  ];

  function gameLabel(game) {
    return { mutator: 'Mutator Lab', egraph: 'E-Graph Builder', axioms: 'Axiom Flipper', science: 'Surrogate Lab' }[game] || 'Sandbox';
  }

  function renderLog(listId, entries) {
    const list = $('#' + listId);
    if (!list) return;
    list.innerHTML = '';
    entries.forEach((text) => {
      const li = document.createElement('li');
      li.textContent = text;
      list.appendChild(li);
    });
  }

  function renderAllGames() {
    renderMutator();
    renderEgraphGame();
    renderAxioms();
    renderScienceGame();
  }

  function mutate(kind) {
    const state = gameSandboxState.mutator;
    let verdict = '';
    if (state.budget <= 0 && kind !== 'verify') {
      verdict = 'Mutation budget exhausted. Reset the sandbox to continue.';
    } else if (kind === 'safe') {
      state.budget = Math.max(0, state.budget - 5);
      state.score = Math.min(200, state.score + 3);
      state.risk = Math.max(0, state.risk - 3);
      state.log.unshift('Safe refactor: +3 score, -3 risk, -5 budget.');
      verdict = 'Safe refactor accepted by verification.';
    } else if (kind === 'aggressive') {
      state.budget = Math.max(0, state.budget - 14);
      state.score = Math.min(200, state.score + 9);
      state.risk = Math.min(100, state.risk + 9);
      state.log.unshift('Aggressive rewrite: +9 score, +9 risk, -14 budget.');
      verdict = 'Aggressive rewrite applied. Watch the regression risk.';
    } else if (kind === 'reckless') {
      state.budget = Math.max(0, state.budget - 10);
      state.score = Math.min(200, state.score + 16);
      state.risk = Math.min(100, state.risk + 22);
      state.log.unshift('Reckless hack: +16 score, +22 risk, -10 budget.');
      if (state.risk > 75) {
        state.score = Math.max(0, state.score - 18);
        state.regressions += 1;
        state.log.unshift('Reward hacking detected: score slashed and a regression logged.');
        verdict = 'Reckless hack triggered the reward-hacking guard.';
      } else {
        verdict = 'Reckless hack survived… for now.';
      }
    } else if (kind === 'verify') {
      if (state.risk <= 25) {
        state.score = Math.min(200, state.score + 4);
        state.risk = Math.max(0, state.risk - 6);
        state.log.unshift('Verification passed cleanly: +4 score, -6 risk.');
        verdict = 'Verification passed. Headroom improved.';
      } else if (state.risk <= 60) {
        state.budget = Math.max(0, state.budget - 3);
        state.log.unshift('Verification passed with warnings; -3 budget.');
        verdict = 'Verification passed with warnings. Risk is still ' + state.risk + '%.';
      } else {
        state.regressions += 1;
        state.score = Math.max(0, state.score - 10);
        state.log.unshift('Verification failed: -10 score, regression logged.');
        verdict = 'Verification failed. A regression was logged.';
      }
    }
    state.log = state.log.slice(0, 8);
    state.verdict = verdict;
    renderMutator();
    track('game_action', { game: 'mutator', action: kind });
  }

  function renderMutator() {
    const state = gameSandboxState.mutator;
    const score = $('#mut-score');
    if (!score) return;
    score.textContent = String(Math.round(state.score));
    $('#mut-risk').textContent = Math.round(state.risk) + '%';
    $('#mut-budget').textContent = String(Math.round(state.budget));
    $('#mut-regressions').textContent = String(state.regressions);
    const bar = $('#mut-risk-bar');
    bar.style.width = state.risk + '%';
    bar.className = 'meter-fill' + (state.risk > 60 ? ' warn' : (state.risk > 25 ? '' : ' good'));
    const verdict = $('#mut-verdict');
    verdict.textContent = state.verdict || '';
    verdict.className = 'verdict' + (state.risk > 60 ? ' bad' : '');
    renderLog('mut-log', state.log);
  }

  function exprMerged(id) {
    return gameSandboxState.egraph.merged.some((pair) => pair.split(':').indexOf(id) >= 0);
  }

  function renderEgraphGame() {
    const state = gameSandboxState.egraph;
    const grid = $('#expr-grid');
    if (!grid) return;
    grid.innerHTML = '';
    EXPRS.forEach((expr) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'expr-card';
      card.dataset.expr = expr.id;
      card.textContent = expr.text;
      if (state.selected.indexOf(expr.id) >= 0) card.classList.add('selected');
      if (exprMerged(expr.id)) card.classList.add('merged');
      grid.appendChild(card);
    });
    $('#eg-classes').textContent = String(state.classes);
    $('#eg-merges').textContent = String(state.merges);
    $('#eg-goals').textContent = state.goals + ' / 3';
    const verdict = $('#eg-verdict');
    verdict.textContent = state.verdict;
    verdict.className = 'verdict' + (state.goals >= 3 ? ' good' : '');
    renderLog('eg-log', state.log);
  }

  function selectExpr(id) {
    const state = gameSandboxState.egraph;
    if (exprMerged(id)) {
      state.verdict = 'That expression is already in a merged class.';
      renderEgraphGame();
      return;
    }
    const index = state.selected.indexOf(id);
    if (index >= 0) state.selected.splice(index, 1);
    else {
      state.selected.push(id);
      if (state.selected.length > 2) state.selected.shift();
    }
    state.verdict = state.selected.length === 2 ? 'Ready to merge — prove the rewrite.' : 'Pick one more expression.';
    renderEgraphGame();
    track('game_action', { game: 'egraph', action: 'select' });
  }

  function egraphAction(kind) {
    const state = gameSandboxState.egraph;
    if (kind === 'merge') {
      if (state.selected.length !== 2) {
        state.verdict = 'Select exactly two expressions first.';
      } else {
        const a = state.selected[0];
        const b = state.selected[1];
        const proven = EQ_PAIRS.some((pair) => pair.indexOf(a) >= 0 && pair.indexOf(b) >= 0);
        const key = [a, b].sort().join(':');
        if (!proven) {
          state.verdict = 'Rejected: no rewrite proof connects those expressions.';
          state.log.unshift('Merge rejected: ' + a + ' is not provably equivalent to ' + b + '.');
        } else if (state.merged.indexOf(key) >= 0) {
          state.verdict = 'Already merged — no new class reduction.';
        } else {
          state.merged.push(key);
          state.merges += 1;
          state.classes = Math.max(1, state.classes - 1);
          state.goals = state.merged.length;
          state.verdict = 'Proven rewrite accepted: equivalence class merged.';
          state.log.unshift('Merged ' + a + ' with ' + b + ': classes now ' + state.classes + '.');
        }
      }
      state.selected = [];
    } else if (kind === 'saturate') {
      let added = 0;
      EQ_PAIRS.forEach((pair) => {
        const key = pair.slice().sort().join(':');
        if (state.merged.indexOf(key) < 0) {
          state.merged.push(key);
          added += 1;
          state.classes = Math.max(1, state.classes - 1);
        }
      });
      state.merges += added;
      state.goals = state.merged.length;
      state.selected = [];
      state.verdict = added ? 'Saturation bot merged every provable pair.' : 'Saturation bot found nothing left to merge.';
      if (added) state.log.unshift('Saturation: ' + added + ' proven merge(s) applied.');
    }
    state.log = state.log.slice(0, 8);
    renderEgraphGame();
    track('game_action', { game: 'egraph', action: kind });
  }

  function axiomOn(id) { return !!gameSandboxState.axioms.on[id]; }

  function axiomToggle(id, checked) {
    const state = gameSandboxState.axioms;
    const axiom = AXIOMS.find((item) => item.id === id);
    if (!axiom) return;
    if (id === 'ax_sorry' && checked) {
      state.on[id] = false;
      state.verdict = 'Rejected: allow_sorry would admit unproven lemmas.';
      state.log.unshift('allow_sorry toggled on, then rejected by the sandbox gate.');
    } else {
      state.on[id] = !!checked;
      state.verdict = (checked ? 'Enabled: ' : 'Disabled: ') + axiom.text;
      state.log.unshift(state.verdict);
    }
    state.log = state.log.slice(0, 8);
    renderAxioms();
    track('game_action', { game: 'axioms', action: 'toggle', detail: id });
  }

  function renderAxioms() {
    const state = gameSandboxState.axioms;
    const grid = $('#axiom-grid');
    if (!grid) return;
    grid.innerHTML = '';
    AXIOMS.forEach((axiom) => {
      const label = document.createElement('label');
      label.className = 'axiom-row' + (axiom.required ? ' required' : '') + (axiomOn(axiom.id) ? '' : ' off');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = axiomOn(axiom.id);
      input.dataset.axiom = axiom.id;
      const text = document.createElement('span');
      text.textContent = axiom.text;
      const note = document.createElement('span');
      note.className = 'ax-note';
      note.textContent = axiom.danger ? 'REJECTED' : (axiom.required ? 'REQUIRED' : 'OPTIONAL');
      label.appendChild(input);
      label.appendChild(text);
      label.appendChild(note);
      grid.appendChild(label);
    });

    const graph = $('#axiom-graph');
    graph.innerHTML = '';
    AXIOMS.forEach((axiom, index) => {
      const node = document.createElement('span');
      node.className = 'ax-node' + (axiomOn(axiom.id) ? '' : ' broken');
      node.textContent = axiom.id.replace('ax_', '').toUpperCase();
      graph.appendChild(node);
      if (index < AXIOMS.length - 1) {
        const link = document.createElement('span');
        link.className = 'ax-link';
        link.textContent = '→';
        graph.appendChild(link);
      }
    });

    const verdict = $('#ax-verdict');
    verdict.textContent = state.verdict;
    verdict.className = 'verdict' + (state.verdict.indexOf('Rejected') >= 0 ? ' bad' : '');
    renderLog('ax-log', state.log);
  }

  function devilAttack() {
    const state = gameSandboxState.axioms;
    const warnings = [];
    if (!axiomOn('ax_verify')) warnings.push('Unverified rewrites can enter the proof ledger.');
    if (!axiomOn('ax_floor')) warnings.push('Discipline can collapse below the 0.90 floor.');
    if (!axiomOn('ax_devil')) warnings.push('Stagnation goes unchallenged.');
    if (warnings.length) {
      state.verdict = 'Devil attack found ' + warnings.length + ' open flank(s).';
      state.log.unshift(warnings.join(' '));
    } else {
      state.verdict = 'Devil attack found no open flank — every required axiom holds.';
      state.log.unshift('Devil advocate probed reward hacking, sorry and floor collapse. All blocked.');
    }
    state.log = state.log.slice(0, 8);
    renderAxioms();
    track('game_action', { game: 'axioms', action: 'devil' });
  }

  function restoreAxioms() {
    gameSandboxState.axioms = freshAxioms();
    renderAxioms();
    track('game_action', { game: 'axioms', action: 'restore' });
  }

  function scienceAction(kind) {
    const state = gameSandboxState.science;
    if (state.budget <= 0 && kind !== 'verify') {
      state.verdict = 'Compute budget exhausted. Reset the sandbox to continue.';
      renderScienceGame();
      return;
    }
    if (kind === 'train') {
      state.budget = Math.max(0, state.budget - 1);
      state.trained += 1;
      state.accuracy = Math.min(97.5, state.accuracy + 7 + Math.random() * 6);
      state.time += 0.4;
      state.log.unshift('Surrogate trained: budget -1, accuracy now ' + state.accuracy.toFixed(1) + '%.');
      state.verdict = 'Surrogate updated. Cheap, but not yet certain.';
    } else if (kind === 'exact') {
      state.budget = Math.max(0, state.budget - 2);
      state.accuracy = Math.max(state.accuracy, 96 + Math.random() * 3.2);
      state.time += 1.8;
      state.log.unshift('Exact solver: budget -2, accuracy now ' + state.accuracy.toFixed(1) + '%.');
      state.verdict = 'Exact solver confirmed the result. Expensive but trustworthy.';
    } else if (kind === 'hybrid') {
      state.budget = Math.max(0, state.budget - 1);
      state.accuracy = Math.min(99.2, state.accuracy + 10 + state.trained * 1.5);
      state.time += 0.9;
      state.log.unshift('Hybrid run: surrogate plus one exact check, accuracy ' + state.accuracy.toFixed(1) + '%.');
      state.verdict = 'Hybrid run balanced speed and certainty.';
    } else if (kind === 'verify') {
      state.verified = state.accuracy >= state.target;
      if (state.verified) {
        state.log.unshift('Verification passed at ' + state.accuracy.toFixed(1) + '%.');
        state.verdict = 'Verification passed the ' + state.target + '% target.';
      } else {
        state.budget = Math.max(0, state.budget - 1);
        state.log.unshift('Verification failed at ' + state.accuracy.toFixed(1) + '%; budget -1.');
        state.verdict = 'Verification failed. Train more or run an exact solver.';
      }
    }
    state.accuracy = Math.min(99.7, state.accuracy);
    state.log = state.log.slice(0, 8);
    renderScienceGame();
    track('game_action', { game: 'science', action: kind });
  }

  function renderScienceGame() {
    const state = gameSandboxState.science;
    const budget = $('#sci-budget');
    if (!budget) return;
    budget.textContent = String(state.budget);
    $('#sci-accuracy').textContent = state.accuracy.toFixed(1) + '%';
    $('#sci-time').textContent = state.time.toFixed(1) + 's';
    $('#sci-target').textContent = state.target + '%';
    const slider = $('#sci-target-slider');
    if (slider) slider.value = String(state.target);
    const targetValue = $('#sci-target-value');
    if (targetValue) targetValue.textContent = state.target + '%';
    const verdict = $('#sci-verdict');
    verdict.textContent = state.verdict;
    verdict.className = 'verdict' + (state.verified ? ' good' : '');
    renderLog('sci-log', state.log);
  }

  function resetGames(silent) {
    gameSandboxState.mutator = freshMutator();
    gameSandboxState.egraph = freshEgraph();
    gameSandboxState.axioms = freshAxioms();
    gameSandboxState.science = freshScience();
    renderAllGames();
    if (!silent) {
      toast('Sandbox mini-games reset. Live kernel untouched.', 'good');
      track('game_action', { game: 'all', action: 'reset' });
    }
  }

  function openGames(tab) {
    const shell = $('#game-shell');
    if (!shell) return;
    shell.hidden = false;
    selectGameTab(tab || 'mutator');
    track('panel_open', { panel: 'sandbox', game: tab || 'mutator' });
  }

  function closeGames() {
    const shell = $('#game-shell');
    if (shell) shell.hidden = true;
  }

  function selectGameTab(name) {
    gameSandboxState.activeTab = name;
    $$('.game-tab').forEach((tab) => {
      const active = tab.dataset.game === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    ['mutator', 'egraph', 'axioms', 'science'].forEach((id) => {
      const pane = $('#game-' + id);
      if (!pane) return;
      const active = id === name;
      pane.classList.toggle('active', active);
      pane.hidden = !active;
    });
  }

  // -------------------------------------------------------------- telemetry
  function setStatus(state, text) {
    const pill = $('#status-pill');
    if (pill) pill.dataset.state = state;
    const label = $('#status-text');
    if (label) label.textContent = text;
  }

  function renderTelemetry(snapshot) {
    const host = $('#telemetry-stats');
    if (!host) return;
    const kernel = snapshot.kernel || {};
    const isolation = snapshot.isolation || {};
    const telos = snapshot.telos || {};
    const verification = snapshot.verification || {};
    const egraph = snapshot.egraph || {};
    const science = snapshot.science || {};
    const ux = snapshot.ux || {};
    const worldStations = (snapshot.world && snapshot.world.stations) || [];

    const rows = [
      ['Kernel state', String(kernel.state || 'UNKNOWN'), ''],
      ['Cycles executed', String(kernel.cycles || 0), ''],
      ['System mastery', fmtNum(kernel.system_mastery, 2), ''],
      ['Isolation', isolation.state_unchanged ? 'clean — kernel unchanged' : 'drift detected', isolation.state_unchanged ? 'good' : 'bad'],
      ['Kernel fingerprint', shortHash(isolation.kernel_fingerprint), ''],
      ['Telos pending', telos.pending === undefined ? '—' : String(telos.pending), ''],
      ['Verification gates', String(verification.high_watermark_passed || 0) + ' / ' + String(verification.high_watermark_total || 0), ''],
      ['E-Graph classes', String(egraph.classes || 0), ''],
      ['Science routers', String(science.router_count || 0), ''],
      ['UI evolutions', String(ux.evolutions || 0), ''],
      ['World beacons', String(worldStations.length), '']
    ];

    host.innerHTML = '';
    rows.forEach((row) => {
      const line = document.createElement('div');
      line.className = 'stat-row';
      const label = document.createElement('span');
      label.textContent = row[0];
      const value = document.createElement('strong');
      if (row[2]) value.className = row[2];
      value.textContent = row[1];
      line.appendChild(label);
      line.appendChild(value);
      host.appendChild(line);
    });

    const pill = $('#isolation-pill');
    if (pill) {
      pill.textContent = isolation.state_unchanged ? 'isolation clean' : 'isolation drift';
      pill.style.color = isolation.state_unchanged ? 'var(--good)' : 'var(--bad)';
    }

    const layout = $('#world-layout-pill');
    if (layout && snapshot.world && snapshot.world.layout) layout.textContent = snapshot.world.layout;
  }

  function applySnapshot(snapshot) {
    if (!snapshot) return;
    live.snapshot = snapshot;
    const stations = (snapshot.world && snapshot.world.stations) || [];
    worldState.live = {};
    stations.forEach((entry) => {
      if (entry && entry.id) worldState.live[entry.id] = entry;
    });
    renderTelemetry(snapshot);
    renderReliability(snapshot);
    if (worldState.focusId) renderStationConsole();
  }

  async function fetchVersions() {
    const list = $('#versions-list');
    try {
      const response = await fetch('/api/ux/versions');
      const data = await response.json();
      if (!list) return;
      list.innerHTML = '';
      const entries = (Array.isArray(data.entries) ? data.entries : []).filter((entry) => entry && entry.type === 'evolve');
      if (!entries.length) {
        const span = document.createElement('span');
        span.className = 'muted';
        span.textContent = data.available === false ? 'evolver unavailable' : 'no UI snapshots yet';
        list.appendChild(span);
        return;
      }
      const active = Number(data.active_version || 0);
      const seen = {};
      entries.forEach((entry) => {
        const version = Number(entry.version);
        if (!Number.isFinite(version) || seen[version]) return;
        seen[version] = true;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'version-chip' + (version === active ? ' active' : '');
        chip.textContent = 'v' + String(version).padStart(3, '0');
        chip.title = entry.summary || 'Revert to this UI version';
        chip.addEventListener('click', () => revertUi(version));
        list.appendChild(chip);
      });
    } catch (err) {
      if (list) list.textContent = 'evolver unreachable';
    }
  }

  async function evolveUi() {
    const button = $('#evolve-btn');
    if (button) button.disabled = true;
    try {
      const response = await fetch('/api/ux/evolve', { method: 'POST' });
      const result = await response.json();
      if (result.promoted) {
        refreshEvolvedStyles();
        toast('UI evolved to v' + result.version + ': ' + (result.changes || []).join(' '), 'good');
      } else if (result.error) {
        toast('Evolution error: ' + result.error, 'bad');
      } else {
        toast('Evolution rejected by the validation gate; UI unchanged.', 'bad');
      }
      fetchVersions();
      track('mode_change', { mode: 'evolve', detail: 'v' + (result.version || '?') });
    } catch (err) {
      toast('Evolution engine unreachable.', 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function revertUi(version) {
    try {
      const response = await fetch('/api/ux/revert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(version === undefined ? {} : { version })
      });
      const result = await response.json();
      if (result.reverted) {
        toast('UI reverted to v' + result.version + '. Reloading…', 'good');
        setTimeout(() => window.location.reload(), 900);
      } else {
        toast('Revert failed: ' + (result.error || 'no snapshot'), 'bad');
        fetchVersions();
      }
    } catch (err) {
      toast('Revert engine unreachable.', 'bad');
    }
  }

  function refreshEvolvedStyles() {
    const link = $('#evolved-styles');
    if (link) link.href = '/ui/evolved.css?v=' + Date.now();
  }

  // ------------------------------------------------------------ reliability
  const reliability = { open: false, full: null };

  function fmtDuration(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    if (n < 90) return n.toFixed(1) + 's';
    if (n < 5400) return (n / 60).toFixed(1) + 'm';
    return (n / 3600).toFixed(2) + 'h';
  }

  function fmtMb(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(0) + ' MB' : '—';
  }

  function sevClass(sev) {
    const value = Number(sev) || 0;
    if (value >= 0.75) return 'hot';
    if (value >= 0.5) return 'warm';
    return 'cool';
  }

  function openReliability() {
    const drawer = $('#reliability-drawer');
    if (!drawer) return;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    reliability.open = true;
    if (live.snapshot) renderReliability(live.snapshot);
    fetchFixDetails();
    track('panel_open', { panel: 'reliability' });
  }

  function closeReliability() {
    const drawer = $('#reliability-drawer');
    if (!drawer) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    reliability.open = false;
  }

  async function fetchFixDetails() {
    try {
      const response = await fetch('/api/fixes');
      if (!response.ok) throw new Error('fix service');
      reliability.full = await response.json();
      if (live.snapshot) renderReliability(live.snapshot);
    } catch (err) {
      toast('Fix service unreachable.', 'bad');
    }
  }

  function promptFor(id) {
    const full = reliability.full;
    if (full) {
      const hit = (full.bottlenecks || []).find((item) => String(item.id) === String(id));
      if (hit && hit.prompt) return hit.prompt;
      const queued = ((full.queue || {}).items || []).find((item) => String(item.id) === String(id));
      if (queued && queued.prompt) return queued.prompt;
    }
    const snapshot = live.snapshot || {};
    const fallback = (snapshot.bottlenecks || []).find((item) => String(item.id) === String(id));
    return fallback && fallback.prompt ? fallback.prompt : '';
  }

  function showDirective(text) {
    const wrap = $('#rel-directive-wrap');
    const box = $('#rel-directive');
    if (!wrap || !box) return;
    box.value = text || '';
    wrap.hidden = !text;
    if (text) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  async function copyDirective() {
    const box = $('#rel-directive');
    if (!box || !box.value) return;
    try {
      await navigator.clipboard.writeText(box.value);
      toast('Directive copied — paste it into Qoder or your workspace agent.', 'good');
    } catch (err) {
      box.focus();
      box.select();
      toast('Select-all applied; press Ctrl+C to copy the directive.', 'warn');
    }
  }

  function chartSurface(canvas) {
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(280, Math.floor(rect.width || canvas.clientWidth || 480));
    const height = Math.max(120, Math.floor(rect.height || 150));
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.textBaseline = 'middle';
    return { ctx, width, height };
  }

  function emptyChart(surface, message) {
    if (!surface) return;
    const { ctx, width, height } = surface;
    ctx.fillStyle = 'rgba(195, 208, 224, 0.55)';
    ctx.font = '11px Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(message, width / 2, height / 2);
  }

  function drawStepChart(canvas, steps) {
    const surface = chartSurface(canvas);
    if (!surface) return;
    const rows = (steps || []).slice(0, 8);
    if (!rows.length) { emptyChart(surface, 'NO DATA YET (WAITING FOR CYCLE)'); return; }
    const { ctx, width, height } = surface;
    const max = Math.max.apply(null, rows.map((row) => Number(row.duration_ms) || 0).concat([1]));
    const rowHeight = (height - 14) / rows.length;
    rows.forEach((row, index) => {
      const y = 7 + index * rowHeight;
      const value = Number(row.duration_ms) || 0;
      const barMax = width - 190;
      const barWidth = Math.max(2, (value / max) * barMax);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';
      ctx.fillRect(158, y + rowHeight * 0.22, barMax, rowHeight * 0.5);
      ctx.fillStyle = row.cpu_percent >= 75 ? '#fb7185' : '#38bdf8';
      ctx.fillRect(158, y + rowHeight * 0.22, barWidth, rowHeight * 0.5);
      ctx.fillStyle = '#c3d0e0';
      ctx.font = '10px Consolas, monospace';
      ctx.textAlign = 'right';
      ctx.fillText(String(row.phase || row.module || '?').slice(0, 22), 152, y + rowHeight * 0.5);
      ctx.textAlign = 'left';
      ctx.fillStyle = '#9fb3c8';
      ctx.fillText(fmtDuration(value / 1000) + ' · ' + fmtNum(row.cpu_percent, 0) + '% cpu', 164 + barWidth, y + rowHeight * 0.5);
    });
  }

  function drawTrendChart(canvas, trends) {
    const surface = chartSurface(canvas);
    if (!surface) return;
    const rows = trends || [];
    if (!rows.length) { emptyChart(surface, 'NO DATA YET (WAITING FOR CYCLE)'); return; }
    const { ctx, width, height } = surface;
    const left = 34;
    const right = width - 8;
    const top = 8;
    const bottom = height - 14;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 1;
    [0, 0.25, 0.5, 0.75, 1].forEach((fraction) => {
      const y = bottom - fraction * (bottom - top);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
      ctx.fillStyle = 'rgba(195, 208, 224, 0.5)';
      ctx.font = '9px Consolas, monospace';
      ctx.textAlign = 'right';
      ctx.fillText(String(Math.round(fraction * 100)), left - 5, y);
    });
    const xAt = (index) => (rows.length === 1 ? left : left + ((right - left) * index) / (rows.length - 1));
    const maxRss = Math.max.apply(null, rows.map((row) => Number(row.rss_peak_mb) || 0).concat([1]));
    const plot = (key, ceiling, color) => {
      ctx.beginPath();
      rows.forEach((row, index) => {
        const raw = Number(row[key]);
        const value = Number.isFinite(raw) ? clamp(raw, 0, ceiling) : 0;
        const y = bottom - (value / ceiling) * (bottom - top);
        if (index === 0) ctx.moveTo(xAt(index), y);
        else ctx.lineTo(xAt(index), y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.7;
      ctx.stroke();
    };
    plot('cpu_percent_avg', 100, '#38bdf8');
    plot('rss_peak_mb', maxRss, '#f472b6');
    plot('pass_rate', 1, '#4ade80');
  }

  function drawRankChart(canvas, bottlenecks) {
    const surface = chartSurface(canvas);
    if (!surface) return;
    const rows = (bottlenecks || []).slice(0, 8);
    if (!rows.length) { emptyChart(surface, 'no bottlenecks detected'); return; }
    const { ctx, width, height } = surface;
    const rowHeight = (height - 14) / rows.length;
    rows.forEach((row, index) => {
      const y = 7 + index * rowHeight;
      const severity = clamp(Number(row.severity) || 0, 0, 1);
      const barMax = width - 190;
      const barWidth = Math.max(2, severity * barMax);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';
      ctx.fillRect(158, y + rowHeight * 0.22, barMax, rowHeight * 0.5);
      ctx.fillStyle = severity >= 0.75 ? '#fb7185' : severity >= 0.5 ? '#fbbf24' : '#38bdf8';
      ctx.fillRect(158, y + rowHeight * 0.22, barWidth, rowHeight * 0.5);
      ctx.fillStyle = '#c3d0e0';
      ctx.font = '10px Consolas, monospace';
      ctx.textAlign = 'right';
      ctx.fillText(String(row.module || '?').slice(0, 20), 152, y + rowHeight * 0.5);
      ctx.textAlign = 'left';
      ctx.fillStyle = '#9fb3c8';
      ctx.fillText((row.kind || '?') + ' · ' + Math.round(severity * 100) + '%', 164 + barWidth, y + rowHeight * 0.5);
    });
  }

  function classNameForRun(run) {
    const conclusion = String((run && run.conclusion) || '').toLowerCase();
    const status = String((run && run.status) || '').toLowerCase();
    if (conclusion === 'success') return 'applied';
    if (['failure', 'timed_out', 'cancelled'].indexOf(conclusion) >= 0) return 'queued';
    if (status === 'in_progress') return 'resolved';
    return '';
  }

  function renderFixQueue(items) {
    const list = $('#rel-fix-queue');
    if (!list) return;
    list.innerHTML = '';
    const queue = (items || []).slice(0, 12);
    if (!queue.length) {
      const empty = document.createElement('li');
      empty.className = 'muted';
      empty.textContent = 'queue empty — generate directives to populate sask_agent_fix_queue.json';
      list.appendChild(empty);
      return;
    }
    queue.forEach((item) => {
      const row = document.createElement('li');
      row.className = 'rel-item';
      const head = document.createElement('div');
      head.className = 'rel-item-head';
      const chip = document.createElement('span');
      chip.className = 'sev-chip ' + sevClass(item.severity);
      chip.textContent = String(Math.round((Number(item.severity) || 0) * 100));
      const title = document.createElement('strong');
      title.textContent = item.label || item.id;
      const status = document.createElement('span');
      status.className = 'status-chip ' + String(item.status || 'queued');
      status.textContent = String(item.status || 'queued').toUpperCase();
      head.appendChild(chip);
      head.appendChild(title);
      head.appendChild(status);
      row.appendChild(head);
      const meta = document.createElement('span');
      meta.className = 'rel-item-meta';
      meta.textContent = (item.module || '—') + ' · ' + (item.kind || '—') + ' · ' + (item.updated_at || '');
      row.appendChild(meta);
      const actions = document.createElement('div');
      actions.className = 'rel-item-actions';
      const directive = document.createElement('button');
      directive.type = 'button';
      directive.className = 'btn btn-mini';
      directive.textContent = 'Directive';
      directive.addEventListener('click', () => {
        const text = item.prompt || promptFor(item.id);
        if (text) showDirective(text);
        else toast('Directive not generated yet — queue it first.', 'warn');
      });
      actions.appendChild(directive);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  function renderReliability(snapshot) {
    if (!snapshot || !reliability.open) return;
    const ct = snapshot.cycle_telemetry || {};
    const wf = snapshot.workflows || {};
    const bottlenecks = snapshot.bottlenecks || [];
    const fixes = snapshot.fixes || {};
    const latest = ct.current || null;

    const prov = $('#reliability-provenance');
    if (prov) {
      const text = ct.available ? ct.provenance : 'NO DATA YET (WAITING FOR CYCLE)';
      prov.textContent = text;
      prov.dataset.state = ct.available ? (ct.source === 'cloud-runner' ? 'cloud' : 'local') : 'none';
    }

    const current = wf.current || null;
    const cloudStatus = $('#rel-cloud-status');
    if (cloudStatus) {
      cloudStatus.textContent = current
        ? String(current.status || 'unknown').toUpperCase() + (current.conclusion ? ' · ' + String(current.conclusion).toUpperCase() : '')
        : (wf.available ? 'NO RUNS YET' : 'NO DATA YET');
    }
    const cloudMeta = $('#rel-cloud-meta');
    if (cloudMeta) {
      cloudMeta.textContent = current
        ? ('#' + (current.run_number || '?') + ' · ' + (current.head_branch || '?') + '@' + (current.head_sha || '?') + ' · ' + (wf.workflow_path || 'workflow'))
        : (wf.reason || 'waiting for the GitHub bridge');
    }
    const cloudBar = $('#rel-cloud-bar');
    if (cloudBar) {
      const ratio = current && Number.isFinite(Number(current.window_used_ratio)) ? clamp(Number(current.window_used_ratio), 0, 1) : 0;
      cloudBar.style.width = Math.round(ratio * 100) + '%';
      cloudBar.className = 'meter-fill' + (ratio >= 0.9 ? ' warn' : '');
    }
    const cloudWindow = $('#rel-cloud-window');
    if (cloudWindow) {
      if (current) {
        const windowSeconds = (Number(wf.window_minutes) || 340) * 60;
        const elapsed = Number(current.elapsed_seconds) || 0;
        const remaining = Math.max(0, windowSeconds - elapsed);
        const gap = Number(wf.handoff_gap_seconds);
        cloudWindow.textContent = fmtDuration(elapsed) + ' of ' + Math.round(windowSeconds / 60) + 'm · ' + fmtDuration(remaining) + ' left' +
          (Number.isFinite(gap) && gap > 0 ? ' · gap ' + fmtDuration(gap) : '');
      } else {
        cloudWindow.textContent = wf.token_present ? 'token present · awaiting runs' : 'no token · public read only';
      }
    }

    const cycleStatus = $('#rel-cycle-status');
    if (cycleStatus) {
      cycleStatus.textContent = latest
        ? (latest.success ? 'SUCCESS' : 'FAILED') + ' · ' + fmtDuration(latest.duration_seconds) + ' · exit ' + (latest.exit_status || '?')
        : 'NO DATA YET';
    }
    const cycleMeta = $('#rel-cycle-meta');
    if (cycleMeta) {
      cycleMeta.textContent = latest
        ? ('cycle #' + latest.cycle_id + ' · ' + latest.source + ' · ' + (latest.completed_at || ''))
        : 'run core.py once to populate cycle_telemetry.jsonl';
    }
    const outcomes = (latest && latest.outcomes) || {};
    const pass = $('#rel-pass');
    if (pass) pass.textContent = outcomes.pass_rate === null || outcomes.pass_rate === undefined ? '—' : Math.round(Number(outcomes.pass_rate) * 100) + '%';
    const cpu = $('#rel-cpu');
    if (cpu) cpu.textContent = Number.isFinite(Number(latest && latest.cpu_percent_avg)) ? fmtNum(latest.cpu_percent_avg, 0) + '%' : '—';
    const rss = $('#rel-rss');
    if (rss) rss.textContent = fmtMb(latest && latest.rss_peak_mb);
    const discipline = $('#rel-discipline');
    if (discipline) discipline.textContent = Number.isFinite(Number(outcomes.discipline)) ? fmtNum(outcomes.discipline, 3) : '—';

    const scale = snapshot.scale || {};
    const numOrNull = (value) => (value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value));
    const scaleStatus = $('#rel-scale-status');
    if (scaleStatus) {
      scaleStatus.textContent = scale.available ? 'LIVE' : 'NO DATA YET';
      scaleStatus.dataset.state = scale.available ? 'local' : 'none';
    }
    const scaleWorkers = $('#rel-scale-workers');
    if (scaleWorkers) {
      const activeWorkers = numOrNull(scale.active_workers);
      const maxWorkers = numOrNull(scale.max_workers);
      scaleWorkers.textContent = activeWorkers === null
        ? '—'
        : String(activeWorkers) + ' / ' + (maxWorkers === null ? '?' : String(maxWorkers));
    }
    const scaleLimits = $('#rel-scale-limits');
    if (scaleLimits) {
      if (scale.available) {
        const ramCeiling = numOrNull(scale.ram_fraction_ceiling) === null ? '?' : Math.round(numOrNull(scale.ram_fraction_ceiling) * 100) + '%';
        const cycleCeiling = numOrNull(scale.cycle_ceiling_minutes) === null ? 'window ?' : Math.round(numOrNull(scale.cycle_ceiling_minutes)) + 'm window';
        const p95 = numOrNull(scale.p95_seconds) === null ? '' : ' · p95 ' + fmtDuration(scale.p95_seconds);
        scaleLimits.textContent = 'constitutional ceiling · RAM ≤ ' + ramCeiling + ' · ' + cycleCeiling + p95;
      } else {
        scaleLimits.textContent = scale.reason || 'waiting for the first elastic scale tick';
      }
    }
    const scaleBar = $('#rel-scale-worker-bar');
    if (scaleBar) {
      const maxWorkers = numOrNull(scale.max_workers);
      const ratio = maxWorkers !== null && maxWorkers > 0
        ? clamp(Number(scale.active_workers) / maxWorkers, 0, 1)
        : 0;
      scaleBar.style.width = Math.round(ratio * 100) + '%';
      scaleBar.className = 'meter-fill' + (ratio >= 0.95 ? ' warn' : '');
    }
    const scaleHeadroom = $('#rel-scale-headroom');
    if (scaleHeadroom) {
      const headroom = scale.headroom || {};
      const ram = headroom.ram || {};
      scaleHeadroom.textContent = scale.available
        ? 'local headroom ' + (headroom.local_workers === undefined ? '—' : headroom.local_workers) +
          ' · RAM headroom ' + fmtMb(ram.headroom_mb) +
          ' · cloud ' + (scale.cloud && scale.cloud.active_shards !== undefined ? scale.cloud.active_shards : '—') + '/4 shards'
        : '—';
    }
    const early = scale.early_cancel || {};
    const scaleSaved = $('#rel-scale-saved');
    if (scaleSaved) {
      scaleSaved.textContent = numOrNull(early.saved_cpu_seconds) === null
        ? '—'
        : fmtNum(early.saved_cpu_seconds, 1) + 's CPU';
    }
    const scaleCancelled = $('#rel-scale-cancelled');
    if (scaleCancelled) {
      scaleCancelled.textContent = Number(early.cancelled_tasks)
        ? early.cancelled_tasks + ' tasks cancelled across ' + (early.events || 0) + ' races'
        : 'no winner-takes-all cancellations yet';
    }
    const scalePolicy = $('#rel-scale-policy');
    if (scalePolicy) {
      const pol = scale.policy || {};
      if (scale.available) {
        const floorValue = numOrNull(pol.exploration_floor);
        const floor = floorValue === null ? '15%' : Math.round(floorValue * 100) + '%';
        scalePolicy.textContent = 'policy v' + (pol.version === null || pol.version === undefined ? '?' : pol.version) +
          ' · batch ' + (pol.batch_size === null || pol.batch_size === undefined ? '—' : pol.batch_size) +
          ' · shards ' + (pol.cloud_shards === null || pol.cloud_shards === undefined ? '—' : pol.cloud_shards) +
          ' · explore floor ' + floor;
      } else {
        scalePolicy.textContent = 'policy v—';
      }
    }
    const slotsWrap = $('#rel-scale-slots');
    if (slotsWrap) {
      slotsWrap.innerHTML = '';
      const slots = scale.strategy_slots || {};
      const names = Object.keys(slots);
      if (!names.length) {
        const empty = document.createElement('span');
        empty.className = 'muted';
        empty.textContent = scale.available ? 'no strategy slots allocated yet' : 'loading…';
        slotsWrap.appendChild(empty);
      }
      const bandit = scale.bandit || {};
      names.sort((a, b) => (slots[b] || 0) - (slots[a] || 0)).forEach((name) => {
        const chip = document.createElement('span');
        chip.className = 'scale-slot' + (Number(slots[name]) ? '' : ' empty');
        const label = document.createElement('span');
        label.textContent = String(name).replace(/_/g, ' ');
        const count = document.createElement('b');
        count.textContent = String(slots[name]);
        chip.appendChild(label);
        chip.appendChild(count);
        const arm = bandit[name] || {};
        if (arm.weight !== undefined) chip.title = 'bandit weight ' + Math.round(Number(arm.weight) * 100) + '% · pulls ' + (arm.pulls || 0) + ' · wins ' + (arm.wins || 0);
        slotsWrap.appendChild(chip);
      });
    }
    const upgradesList = $('#rel-scale-upgrades');
    if (upgradesList) {
      upgradesList.innerHTML = '';
      const upgrades = scale.upgrades || [];
      if (!upgrades.length) {
        const empty = document.createElement('li');
        empty.className = 'muted';
        empty.textContent = scale.available ? 'no policy promotions or rollbacks yet' : 'no policy events yet';
        upgradesList.appendChild(empty);
      }
      upgrades.slice(-8).reverse().forEach((event) => {
        const row = document.createElement('li');
        row.className = 'rel-item';
        const head = document.createElement('div');
        head.className = 'rel-item-head';
        const chip = document.createElement('span');
        const kind = String(event.kind || 'event');
        chip.className = 'status-chip ' + (kind === 'policy_promoted' ? 'applied' : (kind === 'policy_rolled_back' ? 'queued' : 'resolved'));
        chip.textContent = kind.replace('policy_', '').toUpperCase();
        const title = document.createElement('strong');
        title.textContent = event.version !== undefined ? 'policy v' + event.version : kind.replace(/_/g, ' ');
        head.appendChild(chip);
        head.appendChild(title);
        row.appendChild(head);
        const meta = document.createElement('span');
        meta.className = 'rel-item-meta';
        const bits = [];
        if (Number.isFinite(Number(event.speedup))) bits.push('speedup ×' + Number(event.speedup).toFixed(3));
        if (event.reasons && event.reasons.length) bits.push(String(event.reasons[0]));
        bits.push(event.at || event.ts || '');
        meta.textContent = bits.filter(Boolean).join(' · ');
        row.appendChild(meta);
        upgradesList.appendChild(row);
      });
    }
    const upgradeCount = $('#rel-scale-upgrade-count');
    if (upgradeCount) upgradeCount.textContent = scale.available ? String((scale.upgrades || []).length) + ' events' : '—';

    const limiters = $('#rel-limiters');
    if (limiters) {
      const parts = Object.keys(ct.limiters || {}).map((key) => key + ' ' + ct.limiters[key]);
      limiters.textContent = parts.length ? parts.join(' · ') : (ct.available ? 'none detected' : 'no data yet');
    }

    const list = $('#rel-bottleneck-list');
    if (list) {
      list.innerHTML = '';
      if (!bottlenecks.length) {
        const empty = document.createElement('li');
        empty.className = 'muted';
        empty.textContent = ct.available ? 'No bottleneck crossed its threshold in the latest cycle.' : 'NO DATA YET (WAITING FOR CYCLE)';
        list.appendChild(empty);
      }
      bottlenecks.slice(0, 10).forEach((item) => {
        const row = document.createElement('li');
        row.className = 'rel-item';
        const head = document.createElement('div');
        head.className = 'rel-item-head';
        const chip = document.createElement('span');
        chip.className = 'sev-chip ' + sevClass(item.severity);
        chip.textContent = String(Math.round((Number(item.severity) || 0) * 100));
        const title = document.createElement('strong');
        title.textContent = item.label || item.id;
        const kind = document.createElement('span');
        kind.className = 'pill pill-soft';
        kind.textContent = item.kind || 'issue';
        head.appendChild(chip);
        head.appendChild(title);
        head.appendChild(kind);
        row.appendChild(head);

        const meta = document.createElement('span');
        meta.className = 'rel-item-meta';
        const metric = item.metric || {};
        meta.textContent = (item.module || '—') + ' · ' + (metric.label || '') + ' = ' + (metric.value === undefined ? '—' : metric.value) + ' ' + (metric.unit || '') + ' · ' + (item.provenance || '');
        row.appendChild(meta);

        const actions = document.createElement('div');
        actions.className = 'rel-item-actions';
        const fixNow = document.createElement('button');
        fixNow.type = 'button';
        fixNow.className = 'btn btn-accent btn-mini';
        fixNow.textContent = 'Fix Now';
        fixNow.addEventListener('click', () => applyBottlenecks(item.id));
        const queue = document.createElement('button');
        queue.type = 'button';
        queue.className = 'btn btn-mini';
        queue.textContent = 'Queue fix';
        queue.addEventListener('click', () => queueBottlenecks([item.id]));
        const directive = document.createElement('button');
        directive.type = 'button';
        directive.className = 'btn btn-mini';
        directive.textContent = 'Directive';
        directive.addEventListener('click', () => {
          const text = promptFor(item.id);
          if (text) showDirective(text);
          else queueBottlenecks([item.id]);
        });
        actions.appendChild(fixNow);
        actions.appendChild(queue);
        actions.appendChild(directive);
        row.appendChild(actions);
        list.appendChild(row);
      });
    }

    renderFixQueue(fixes.items || []);
    const counts = fixes.counts || {};
    const fixCounts = $('#rel-fix-counts');
    if (fixCounts) {
      const parts = Object.keys(counts).map((key) => key + ' ' + counts[key]);
      fixCounts.textContent = parts.length ? parts.join(' · ') : 'empty';
    }

    const runList = $('#rel-cloud-runs');
    if (runList) {
      runList.innerHTML = '';
      const recent = (wf.recent || []).slice(0, 5);
      if (!recent.length) {
        const empty = document.createElement('li');
        empty.className = 'muted';
        empty.textContent = wf.available ? 'workflow has no runs yet' : 'NO DATA YET — GitHub bridge unavailable';
        runList.appendChild(empty);
      }
      recent.forEach((run) => {
        const row = document.createElement('li');
        row.className = 'rel-item';
        const head = document.createElement('div');
        head.className = 'rel-item-head';
        const chip = document.createElement('span');
        chip.className = 'status-chip ' + classNameForRun(run);
        chip.textContent = String(run.conclusion || run.status || 'unknown').toUpperCase();
        const title = document.createElement('strong');
        title.textContent = 'Run #' + (run.run_number || '?') + ' · ' + (run.head_branch || '?') + '@' + (run.head_sha || '?');
        head.appendChild(chip);
        head.appendChild(title);
        row.appendChild(head);
        const meta = document.createElement('span');
        meta.className = 'rel-item-meta';
        const steps = (run.steps || []).length;
        meta.textContent = fmtDuration(run.elapsed_seconds) + ' · ' + (run.event || 'event') + (steps ? ' · ' + steps + ' steps' : '');
        row.appendChild(meta);
        runList.appendChild(row);
      });
    }
    const cloudCount = $('#rel-cloud-count');
    if (cloudCount) cloudCount.textContent = String((wf.recent || []).length) + ' runs';

    drawStepChart($('#rel-steps-chart'), ct.latest_steps || []);
    drawTrendChart($('#rel-trend-chart'), ct.trends || []);
    drawRankChart($('#rel-rank-chart'), bottlenecks);
    const legend = $('#rel-trend-legend');
    if (legend && !legend.childElementCount) {
      legend.innerHTML = '<span><i style="background:#38bdf8"></i>CPU %</span>' +
        '<span><i style="background:#f472b6"></i>RAM peak</span>' +
        '<span><i style="background:#4ade80"></i>Pass rate</span>';
    }
  }

  async function queueBottlenecks(ids) {
    try {
      const response = await fetch('/api/fixes/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ids && ids.length ? { ids } : {})
      });
      const result = await response.json();
      if (result.queued) {
        toast('Queued ' + result.queued + ' fix directive(s) → ' + (result.path || 'sask_agent_fix_queue.json'), 'good');
        const first = (result.items || []).find((item) => item && item.prompt);
        if (first) showDirective(first.prompt);
      } else {
        toast('No bottlenecks available to queue.', 'warn');
      }
      fetchFixDetails();
    } catch (err) {
      toast('Fix service unreachable.', 'bad');
    }
  }

  async function applyBottlenecks(id) {
    const batchButton = id ? null : $('#rel-fix-all');
    if (batchButton) batchButton.disabled = true;
    try {
      const response = await fetch('/api/fixes/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(id ? { id } : {})
      });
      const result = await response.json();
      if (id) {
        if (result.applied) {
          toast('Applied safe remediation: ' + (result.note || id), 'good');
        } else if (result.prompt) {
          showDirective(result.prompt);
          toast('This one needs a code change — directive ready to copy.', 'warn');
        } else {
          toast(result.error || 'Could not apply that fix.', 'bad');
        }
      } else {
        const applied = Number(result.applied || 0);
        const awaiting = Number(result.awaiting_agent || 0);
        const waiting = (result.results || []).find((row) => row && !row.applied && row.prompt);
        if (waiting) showDirective(waiting.prompt);
        toast('Remediation: ' + applied + ' applied locally · ' + awaiting + ' directive(s) for the workspace agent.', applied ? 'good' : 'warn');
      }
      fetchFixDetails();
    } catch (err) {
      toast('Fix service unreachable.', 'bad');
    } finally {
      if (batchButton) batchButton.disabled = false;
    }
  }

  // --------------------------------------------------------------- transport
  let socket = null;
  let reconnectTimer = null;
  let pollTimer = null;

  function connectSocket() {
    try {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(proto + '://' + window.location.host + '/ws/telemetry');
    } catch (err) {
      startPolling();
      scheduleReconnect();
      return;
    }
    socket.addEventListener('open', () => {
      live.connected = true;
      setStatus('live', 'LIVE');
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      if (!live.announced) {
        live.announced = true;
        toast('Live read-only telemetry connected.', 'good');
      }
    });
    socket.addEventListener('message', (event) => {
      try { applySnapshot(JSON.parse(event.data)); } catch (err) { /* ignore malformed frame */ }
    });
    socket.addEventListener('close', () => {
      live.connected = false;
      setStatus('offline', 'OFFLINE');
      startPolling();
      scheduleReconnect();
    });
    socket.addEventListener('error', () => {
      try { socket.close(); } catch (err) { /* noop */ }
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectSocket();
    }, 5000);
  }

  function startPolling() {
    if (pollTimer) return;
    const poll = () => {
      fetch('/api/state')
        .then((response) => (response.ok ? response.json() : null))
        .then((snapshot) => { if (snapshot) applySnapshot(snapshot); })
        .catch(() => { /* keep trying */ });
    };
    poll();
    pollTimer = setInterval(poll, 4000);
  }

  // ------------------------------------------------------------------- boot
  function updateClock() {
    const clock = $('#clock');
    if (clock) clock.textContent = new Date().toLocaleTimeString([], { hour12: false });
  }

  function fpsLoop(now) {
    live.frames = (live.frames || 0) + 1;
    if (!live.fpsWindowStart) live.fpsWindowStart = now;
    const elapsed = now - live.fpsWindowStart;
    if (elapsed >= 1000) {
      live.fps = Math.round((live.frames * 1000) / elapsed);
      live.frames = 0;
      live.fpsWindowStart = now;
      const meter = $('#fps-meter');
      if (meter) meter.textContent = live.fps + ' FPS';
      if (live.fps < 30) track('fps', { fps: live.fps });
    }
    requestAnimationFrame(fpsLoop);
  }

  function bindEvents() {
    $('#explain-toggle').addEventListener('click', () => {
      const enabled = !document.body.classList.contains('explain-mode');
      setExplainMode(enabled);
      if (enabled) openExplainer('overview');
      else closeExplainer();
      track('explain_toggle', { mode: enabled ? 'explain' : 'normal' });
    });
    $('#help-toggle').addEventListener('click', () => openExplainer('overview'));
    $('#reliability-toggle').addEventListener('click', () => {
      if (reliability.open) closeReliability();
      else openReliability();
    });
    $('#reliability-close').addEventListener('click', closeReliability);
    $('#reliability-drawer').addEventListener('click', (event) => {
      if (event.target === $('#reliability-drawer')) closeReliability();
    });
    $('#rel-refresh').addEventListener('click', () => {
      fetchFixDetails();
      if (live.snapshot) renderReliability(live.snapshot);
      track('panel_open', { panel: 'reliability-refresh' });
    });
    $('#rel-fix-all').addEventListener('click', () => applyBottlenecks(null));
    $('#rel-queue-all').addEventListener('click', () => queueBottlenecks(null));
    $('#rel-copy-directive').addEventListener('click', copyDirective);
    $('#telemetry-toggle').addEventListener('click', () => {
      const dock = $('#telemetry-dock');
      dock.hidden = !dock.hidden;
      if (!dock.hidden && live.snapshot) renderTelemetry(live.snapshot);
      track('panel_open', { panel: 'telemetry' });
    });
    $('#telemetry-close').addEventListener('click', () => { $('#telemetry-dock').hidden = true; });
    $('#explainer-close').addEventListener('click', closeExplainer);
    $('#explainer-drawer').addEventListener('click', (event) => {
      if (event.target === $('#explainer-drawer')) closeExplainer();
    });
    $('#station-close').addEventListener('click', closeStation);
    $('#station-takeover').addEventListener('click', toggleTakeover);
    $('#station-pause').addEventListener('click', togglePause);
    $('#station-explain').addEventListener('click', () => {
      if (worldState.focusId) openExplainer(worldState.focusId);
    });
    $('#station-intensity').addEventListener('input', (event) => {
      const state = worldState.stations[worldState.focusId];
      if (!state) return;
      state.intensity = clamp(Number(event.target.value) / 100, 0, 1);
      $('#station-intensity-value').textContent = Math.round(state.intensity * 100) + '%';
    });
    $('#order-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const input = $('#order-input');
      const text = (input.value || '').trim();
      if (!text || !worldState.focusId) return;
      const station = stationById(worldState.focusId);
      enqueueOrder(station.id, { label: text, narrative: 'Co-Pilot completed a custom directive', duration: 8000 }, true);
      input.value = '';
      track('game_action', { game: 'world-order', action: 'custom', detail: station.id });
    });
    $('#station-game-btn').addEventListener('click', () => {
      if (!worldState.focusId) return;
      openGames(stationById(worldState.focusId).game);
    });
    $('#copilot-toggle').addEventListener('click', toggleCopilot);
    $('#demo-btn').addEventListener('click', startDemo);
    $('#demo-cancel').addEventListener('click', cancelDemo);
    $('#sandbox-reset').addEventListener('click', resetWorld);
    $('#sandbox-reset-games').addEventListener('click', () => resetGames(false));
    $('#game-close').addEventListener('click', closeGames);
    $('#game-backdrop').addEventListener('click', closeGames);
    $('#game-tabs').addEventListener('click', (event) => {
      const tab = event.target.closest('.game-tab');
      if (!tab) return;
      selectGameTab(tab.dataset.game);
      track('panel_open', { panel: 'sandbox', game: tab.dataset.game });
    });
    $$('[data-mut]').forEach((button) => button.addEventListener('click', () => mutate(button.dataset.mut)));
    $$('[data-eg]').forEach((button) => button.addEventListener('click', () => egraphAction(button.dataset.eg)));
    $$('[data-ax]').forEach((button) => button.addEventListener('click', () => {
      if (button.dataset.ax === 'devil') devilAttack();
      else restoreAxioms();
    }));
    $$('[data-sci]').forEach((button) => button.addEventListener('click', () => scienceAction(button.dataset.sci)));
    $('#expr-grid').addEventListener('click', (event) => {
      const card = event.target.closest('.expr-card');
      if (card) selectExpr(card.dataset.expr);
    });
    $('#axiom-grid').addEventListener('change', (event) => {
      const input = event.target.closest('input[data-axiom]');
      if (input) axiomToggle(input.dataset.axiom, input.checked);
    });
    $('#sci-target-slider').addEventListener('input', (event) => {
      gameSandboxState.science.target = Number(event.target.value) || 98;
      renderScienceGame();
    });
    $('#evolve-btn').addEventListener('click', evolveUi);
    $('#revert-btn').addEventListener('click', () => revertUi());
  }

  function boot() {
    buildWaypoints();
    initWorld();
    resizeWorld();
    setupInput();
    renderAllGames();
    bindEvents();
    updateCopilotChrome();
    renderCopilotLog();
    rotateMission(true);
    updateClock();
    setInterval(updateClock, 1000);
    setInterval(flushTelemetry, 5000);
    fetchVersions();
    connectSocket();
    requestAnimationFrame(animate);
    requestAnimationFrame(fpsLoop);
    setTimeout(() => {
      if (!live.snapshot) {
        fetch('/api/state')
          .then((response) => (response.ok ? response.json() : null))
          .then((snapshot) => { if (snapshot) applySnapshot(snapshot); })
          .catch(() => { /* noop */ });
      }
    }, 1200);
    track('layout', { detail: 'boot', mode: world.three ? '3d' : 'fallback' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
