/* Lane League. One circuit, supercars only. */

export const SCORING = {
  timeLimit: 60,
  coin: 100,
  hurdle: 25,
  finish: 250,
  perSecond: 8,
};

export function tally(result) {
  const coins = result.coins || 0;
  const hurdles = result.hurdles || 0;
  const finished = !!result.finished;
  const timeLeft = Math.max(0, result.timeLeft || 0);
  const lab = coins * SCORING.coin;
  const pen = hurdles * SCORING.hurdle;
  const finish = finished ? SCORING.finish : 0;
  const clock = finished ? Math.round(timeLeft * SCORING.perSecond) : 0;
  return {
    lab,
    pen,
    finish,
    clock,
    total: Math.max(0, lab + finish + clock - pen),
    coins,
    hurdles,
    finished,
    timeLeft,
    coinTotal: result.coinTotal || 0,
  };
}

const HALF_ROAD_M = 4;

const PALETTES = {
  supercar: {
    sky: "#1b1612",
    ground: "#3c4632",
    roadA: "#2c2926",
    roadB: "#34302c",
    rumbleA: "#c5341b",
    rumbleB: "#f3ead7",
    fog: "#1b1612",
    sun: "#e2b034",
  },
  bus: {
    sky: "#d5e6f2",
    ground: "#7d8c5e",
    roadA: "#4e545b",
    roadB: "#454b52",
    rumbleA: "#e2b034",
    rumbleB: "#f7f1e4",
    fog: "#d5e6f2",
    sun: "#f4efe6",
  },
  plane: {
    sky: "#9fd0ea",
    ground: "#d7ecf6",
    roadA: "#f4f7fb",
    roadB: "#e7eef5",
    rumbleA: "#e2b034",
    rumbleB: "#1d4e89",
    fog: "#9fd0ea",
    sun: "#fff8ea",
  },
};

export function mountRace(canvas, payload, hooks) {
  const ctx = canvas.getContext("2d");
  const vehicle = payload.vehicle;
  const track = payload.track;
  const mods = payload.modifiers;
  const card = payload.card;
  const mode = vehicle.mode;
  const palette = PALETTES[mode];
  const segments = track.segments;
  const segmentM = track.segment_m;
  const total = segments.length;
  const pickups = (track.pickups || []).map((item) => ({ ...item, hit: false, passed: false }));
  const coinTotal = pickups.filter((item) => item.kind === "coin").length;
  const keys = new Set();
  let width = 1;
  let height = 1;
  let raf = 0;
  let last = performance.now();
  let opponent = null;
  let audio = null;
  let muted = false;
  let destroyed = false;

  const state = {
    phase: "hold",
    countdown: 3,
    seg: 0,
    x: 0,
    speed: mode === "plane" ? Math.max(vehicle.stall_speed + 8, vehicle.max_speed * 0.55) : 0,
    altitude: mode === "plane" ? segments[0].altitude : 0,
    time: 0,
    samples: [],
    sampleAcc: 0,
    finished: false,
    stalling: false,
    rivalSeg: 0,
    rivalX: 0,
    coins: 0,
    slip: 0,
    steerInput: 0,
    braking: 0,
    hurdles: 0,
  };

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    width = Math.max(2, rect.width);
    height = Math.max(2, rect.height);
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function round(n) {
    return Math.round(n * 1000) / 1000;
  }

  function sample(extra = 0) {
    const prev = state.samples[state.samples.length - 1];
    let t = state.time + extra;
    if (prev && t <= prev.t) t = prev.t + 0.05;
    state.samples.push({
      t: round(t),
      seg: round(state.seg),
      x: round(state.x),
      alt: round(state.altitude),
      speed: round(state.speed),
    });
  }

  function sweep(from, to) {
    pickups.forEach((item) => {
      if (item.passed || item.seg <= from || item.seg > to) return;
      const gap = Math.abs(state.x - item.x);
      if (item.kind === "coin" && gap < 0.42) {
        item.hit = true;
        state.coins += 1;
      } else if (item.kind === "hurdle" && gap < 0.24) {
        item.hit = true;
        state.hurdles += 1;
        state.speed *= 0.72;
      }
      item.passed = true;
    });
  }

  function step(dt) {
    const prevSeg = state.seg;
    const index = Math.min(total - 1, Math.floor(state.seg));
    const seg = segments[index];
    const steer = (keys.has("right") ? 1 : 0) - (keys.has("left") ? 1 : 0);
    let throttle = 0;
    let brake = 0;
    let pitch = 0;
    if (mode === "plane") {
      throttle = keys.has("w") ? 1 : 0;
      brake = keys.has("s") ? 1 : 0;
      pitch = (keys.has("up") ? 1 : 0) - (keys.has("down") ? 1 : 0);
    } else {
      throttle = keys.has("w") || keys.has("up") ? 1 : 0;
      brake = keys.has("s") || keys.has("down") ? 1 : 0;
    }

    const grip = vehicle.grip * mods.grip;
    if (throttle) state.speed += vehicle.accel * mods.accel * dt;
    if (brake) state.speed -= vehicle.brake * dt;
    state.speed -= state.speed * 0.05 * dt;
    const off = Math.abs(state.x) + vehicle.body > 1;
    if (off) state.speed -= 24 * dt;
    const cap = vehicle.max_speed * (off ? 0.62 : 1);
    state.speed = Math.max(0, Math.min(cap, state.speed));

    state.stalling = mode === "plane" && state.speed < vehicle.stall_speed;
    if (state.stalling) state.altitude -= 0.4 * dt;

    const speedRatio = vehicle.max_speed ? state.speed / vehicle.max_speed : 0;
    state.x += steer * vehicle.steer * dt * (mode === "plane" ? 1 : 0.5 + 0.5 * speedRatio);
    const push = (seg.curve || 0) * 2.05 * speedRatio * speedRatio * (1.22 - grip) * dt;
    state.x += push;
    state.steerInput = steer;
    state.braking = brake;
    state.slip = Math.abs(push) / Math.max(0.016, dt);
    if (mods.wind) {
      const gust = Math.sin(state.time * 1.4);
      state.x += gust * mods.wind * (mode === "plane" ? 0.04 : 0.1) * dt;
      if (mode === "plane") state.altitude += gust * mods.wind * 0.22 * dt;
    }
    if (mode === "plane") {
      state.altitude += pitch * vehicle.pitch * dt;
      state.altitude = Math.max(-1.25, Math.min(1.25, state.altitude));
    }
    state.x = Math.max(-1.65, Math.min(1.65, state.x));
    state.seg += (state.speed / segmentM) * dt;
    state.time += dt;
    sweep(prevSeg, state.seg);

    if (card.match === "ai") {
      const wobble = Math.sin(state.time * 0.65) * card.mistake_rate * 0.08;
      state.rivalSeg += (card.rival_cruise_mps / segmentM) * (1 + wobble) * dt;
      const rivalIndex = Math.min(total - 1, Math.max(0, Math.floor(state.rivalSeg)));
      const lineBias = card.line === "inside" ? 0.22 : card.line === "outside" ? -0.18 : 0;
      const target = -(segments[rivalIndex].curve || 0) * 0.28 + lineBias;
      state.rivalX += (target - state.rivalX) * Math.min(1, dt * 1.6);
    }

    state.sampleAcc += dt;
    if (state.sampleAcc >= 0.1) {
      state.sampleAcc = 0;
      sample();
    }

    const timeLeft = Math.max(0, SCORING.timeLimit - state.time);
    if (!state.finished && (state.seg >= total || timeLeft <= 0)) {
      conclude(state.seg >= total, false);
    }
  }

  function conclude(crossed, endedEarly) {
    if (destroyed || state.finished) return;
    if (crossed) state.seg = total;
    state.speed = 0;
    sample(0.02);
    state.finished = true;
    state.phase = "done";
    const timeLeft = Math.max(0, SCORING.timeLimit - state.time);
    hooks.onFinish(state.samples.slice(), {
      time: state.time,
      timeLeft: crossed ? timeLeft : 0,
      finished: crossed,
      endedEarly,
      coins: state.coins,
      hurdles: state.hurdles,
      coinTotal,
    });
  }

  function rgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function mix(from, to, t) {
    const a = rgb(from);
    const b = rgb(to);
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  }

  function trap(x1, y1, w1, x2, y2, w2) {
    ctx.beginPath();
    ctx.moveTo(x1 - w1, y1);
    ctx.lineTo(x2 - w2, y2);
    ctx.lineTo(x2 + w2, y2);
    ctx.lineTo(x1 + w1, y1);
    ctx.closePath();
    ctx.fill();
  }

  function projectAhead(leadSeg) {
    const ahead = leadSeg - state.seg;
    if (ahead < 0.35 || ahead > 68) return null;
    const frac = state.seg - Math.floor(state.seg);
    const z = (ahead) * segmentM;
    const scale = (height * 0.58) / Math.max(2, z);
    return { ahead, scale, z };
  }

  function drawSprite(x, y, scale, color, kind) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.fillStyle = color;
    if (kind === "bus") {
      ctx.fillRect(-28, -58, 56, 96);
      ctx.fillStyle = "#d5e6f2";
      for (let i = 0; i < 4; i += 1) ctx.fillRect(-20, -48 + i * 20, 40, 10);
    } else if (kind === "plane") {
      ctx.beginPath();
      ctx.moveTo(0, -70);
      ctx.lineTo(14, 10);
      ctx.lineTo(62, 24);
      ctx.lineTo(14, 20);
      ctx.lineTo(8, 58);
      ctx.lineTo(0, 48);
      ctx.lineTo(-8, 58);
      ctx.lineTo(-14, 20);
      ctx.lineTo(-62, 24);
      ctx.lineTo(-14, 10);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(0, -64);
      ctx.lineTo(22, -16);
      ctx.lineTo(20, 36);
      ctx.lineTo(0, 48);
      ctx.lineTo(-20, 36);
      ctx.lineTo(-22, -16);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#16120e";
      ctx.fillRect(-12, -30, 24, 16);
    }
    ctx.restore();
  }

  function drawCoin(x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.beginPath();
    ctx.ellipse(0, 10, 18, 6, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#e2b034";
    ctx.beginPath();
    ctx.arc(0, -8, 26, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#8c6b32";
    ctx.stroke();
    ctx.fillStyle = "#fff6d8";
    ctx.font = "700 15px Fraunces, Georgia, serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("COIN", 0, -8);
    ctx.restore();
  }

  function fillRound(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
    ctx.fill();
  }

  function drawCar(x, y, scale, color) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.beginPath();
    ctx.ellipse(0, 24, 36, 10, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#121212";
    fillRound(-32, -4, 13, 30, 3);
    fillRound(19, -4, 13, 30, 3);
    ctx.fillStyle = "#4a4a4a";
    ctx.fillRect(-30, 0, 9, 7);
    ctx.fillRect(21, 0, 9, 7);
    ctx.fillStyle = color || "#b42318";
    ctx.beginPath();
    ctx.moveTo(0, -72);
    ctx.bezierCurveTo(30, -64, 34, -18, 30, 10);
    ctx.lineTo(28, 36);
    ctx.quadraticCurveTo(0, 48, -28, 36);
    ctx.lineTo(-30, 10);
    ctx.bezierCurveTo(-34, -18, -30, -64, 0, -72);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.16)";
    ctx.beginPath();
    ctx.moveTo(-6, -62);
    ctx.quadraticCurveTo(-22, -28, -20, 8);
    ctx.lineTo(-10, 6);
    ctx.quadraticCurveTo(-12, -26, -2, -54);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#10161c";
    ctx.beginPath();
    ctx.moveTo(0, -54);
    ctx.lineTo(13, -30);
    ctx.lineTo(11, -4);
    ctx.lineTo(-11, -4);
    ctx.lineTo(-13, -30);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#c4552a";
    ctx.fillRect(-8, -28, 6, 16);
    ctx.fillRect(2, -28, 6, 16);
    ctx.fillStyle = "#1c1712";
    ctx.beginPath();
    ctx.arc(0, -14, 3.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#efe4d2";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(0, -14, 2.1, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "rgba(0,0,0,0.2)";
    ctx.fillRect(-16, 4, 32, 14);
    ctx.fillStyle = "#ff2a1f";
    ctx.beginPath();
    ctx.arc(-14, 20, 5.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(14, 20, 5.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#ffd7c2";
    ctx.beginPath();
    ctx.arc(-14, 20, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(14, 20, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#f4efe6";
    ctx.fillRect(-7, 26, 14, 7);
    ctx.fillStyle = "#c5b8a8";
    ctx.beginPath();
    ctx.arc(-7, 38, 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(7, 38, 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawCockpit() {
    const lean = -state.x * 18;
    const paint = vehicle.color || "#b42318";
    const leather = vehicle.trim || "#1c1712";
    const wide = 0.72 + (vehicle.body || 0.12) * 0.9;
    const nose = height * (0.035 + (vehicle.body || 0.12) * 0.05);
    ctx.save();
    ctx.translate(width / 2 + lean, height);
    ctx.fillStyle = paint;
    ctx.beginPath();
    ctx.moveTo(-width * 0.28 * wide, 6);
    ctx.lineTo(-width * 0.07 * wide, -nose);
    ctx.lineTo(width * 0.07 * wide, -nose);
    ctx.lineTo(width * 0.28 * wide, 6);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, 4);
    ctx.lineTo(0, -nose + 6);
    ctx.stroke();
    ctx.fillStyle = leather;
    ctx.fillRect(-width * 0.34, -14, width * 0.68, 28);
    ctx.restore();
  }

  function drawHurdle(x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    for (let i = 0; i < 7; i += 1) {
      ctx.fillStyle = i % 2 ? "#f4efe6" : "#c5341b";
      ctx.fillRect(-42 + i * 12, -48, 12, 62);
    }
    ctx.fillStyle = "#1c1712";
    ctx.fillRect(-44, 12, 88, 6);
    ctx.fillRect(-40, -14, 80, 20);
    ctx.fillStyle = "#f7f1e6";
    ctx.font = "700 13px Fraunces, Georgia, serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("HURDLE", 0, -4);
    ctx.restore();
  }

  function draw() {
    const horizon = height * (mode === "plane" ? 0.42 : 0.2);
    ctx.fillStyle = palette.sky;
    ctx.fillRect(0, 0, width, height);
    if (mode !== "plane") {
      ctx.fillStyle = palette.ground;
      ctx.fillRect(0, horizon, width, height - horizon);
    }
    ctx.fillStyle = palette.sun;
    ctx.beginPath();
    ctx.arc(width * 0.78, horizon * 0.55, mode === "bus" ? 28 : 18, 0, Math.PI * 2);
    ctx.fill();
    if (mods.glare) {
      ctx.fillStyle = "rgba(255, 244, 214, 0.28)";
      ctx.beginPath();
      ctx.arc(width * 0.78, horizon * 0.55, 90, 0, Math.PI * 2);
      ctx.fill();
    }

    const base = Math.floor(state.seg);
    const frac = state.seg - base;
    let bend = 0;
    let bendStep = -((segments[base % total]?.curve || 0) * frac * 0.08);
    const rows = [];
    for (let n = 1; n <= 48; n += 1) {
      const index = (base + n) % total;
      const curve = segments[index].curve || 0;
      bend += bendStep;
      bendStep += curve * 0.08;
      const z = Math.max(0.7, n - frac);
      const half = (width * 0.95) / z;
      const y = horizon + (height * 0.68) / z;
      const cx = width / 2 + bend * half * 0.8 - state.x * half;
      rows.push({ index, n, y, half, cx, scale: half / 90 });
    }

    for (let i = rows.length - 1; i > 0; i -= 1) {
      const far = rows[i];
      const near = rows[i - 1];
      if (far.y >= near.y) continue;
      const fog = Math.min(0.82, far.n / 48);
      if (mode !== "plane") {
        ctx.fillStyle = mix(palette.ground, palette.fog, fog);
        trap(far.cx, far.y, far.half * 8, near.cx, near.y, near.half * 8);
      }
      const rumble = far.index % 4 < 2 ? palette.rumbleA : palette.rumbleB;
      ctx.fillStyle = mix(rumble, palette.fog, fog);
      trap(far.cx, far.y, far.half * 1.18, near.cx, near.y, near.half * 1.18);
      const asphalt = far.index % 2 ? palette.roadA : palette.roadB;
      ctx.fillStyle = mix(asphalt, palette.fog, fog);
      trap(far.cx, far.y, far.half, near.cx, near.y, near.half);
      if (far.index % 6 === 0) {
        ctx.fillStyle = mix("#f3ead7", palette.fog, fog);
        trap(far.cx, far.y, far.half * 0.06, near.cx, near.y, near.half * 0.06);
      }
      if (segments[far.index].gate) {
        ctx.fillStyle = mix("#e2b034", palette.fog, fog * 0.5);
        const gateHalf = far.half * 0.62;
        ctx.fillRect(far.cx - gateHalf - 4, far.y - far.half * 0.45, 6, far.half * 0.5);
        ctx.fillRect(far.cx + gateHalf, far.y - far.half * 0.45, 6, far.half * 0.5);
        if (mode === "plane") {
          const alt = segments[far.index].altitude;
          const gy = far.y - alt * far.half * 0.35;
          ctx.fillRect(far.cx - gateHalf, gy, gateHalf * 2, 3);
        }
      }
    }

    pickups.forEach((item) => {
      if (item.passed) return;
      const ahead = item.seg - state.seg;
      if (ahead < 0.45 || ahead > 46) return;
      const row = rows.find((entry) => Math.abs(entry.n - ahead) < 0.85);
      if (!row) return;
      const px = row.cx + item.x * row.half;
      const sc = Math.max(0.85, Math.min(1.7, row.scale * 0.85));
      const py = row.y - sc * 18;
      if (item.kind === "coin") drawCoin(px, py, sc);
      else drawHurdle(px, py, sc);
    });

    if (state.seg < 8) {
      ctx.fillStyle = "#c5341b";
      ctx.fillRect(width / 2 - 78, height * 0.9, 156, 12);
      ctx.fillStyle = "#f3ead7";
      for (let i = 0; i < 4; i += 1) ctx.fillRect(width / 2 - 78 + i * 40, height * 0.9, 16, 12);
    }

    drawCockpit();

    if (state.phase === "countdown") {
      ctx.fillStyle = "rgba(20, 16, 12, 0.35)";
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#f7f1e6";
      ctx.font = "700 120px Fraunces, Georgia, serif";
      ctx.textAlign = "center";
      ctx.fillText(state.countdown > 0 ? String(Math.max(1, Math.ceil(state.countdown))) : "GO", width / 2, height * 0.48);
    }
  }

  function hud() {
    const rivalSeg = card.match === "human" && opponent ? opponent.seg : state.rivalSeg;
    const gapM = (rivalSeg - state.seg) * segmentM;
    const timeLeft = state.phase === "countdown"
      ? SCORING.timeLimit
      : Math.max(0, SCORING.timeLimit - state.time);
    const live = state.coins * SCORING.coin;
    let next = null;
    pickups.forEach((item) => {
      if (item.passed || item.seg <= state.seg) return;
      const dist = item.seg - state.seg;
      if (!next || dist < next.dist) next = { kind: item.kind, dist };
    });
    hooks.onFrame({
      time: state.time,
      timeLeft,
      speed: state.speed,
      seg: state.seg,
      x: state.x,
      altitude: state.altitude,
      progress: Math.min(1, state.seg / total),
      place: `Coins ${state.coins}/${coinTotal} · hurdles ${state.hurdles}`,
      coins: state.coins,
      hurdles: state.hurdles,
      score: live,
      slip: state.slip,
      steer: state.steerInput,
      braking: state.braking,
      gapM,
      stalling: state.stalling,
      finished: state.finished,
      phase: state.phase,
      hint: state.speed < 8,
      next,
    });
  }

  function frame(now) {
    if (destroyed) return;
    const dt = Math.min(0.033, (now - last) / 1000);
    last = now;
    if (state.phase === "countdown") {
      state.countdown -= dt;
      if (state.countdown <= 0) {
        state.phase = "run";
        state.time = 0;
        state.samples = [];
        sample();
      }
    } else if (state.phase === "run") {
      step(dt);
      if (audio && !muted) {
        const base = 36 + vehicle.accel * 0.8;
        audio.osc.frequency.value = base + state.speed * (0.55 + vehicle.body * 4);
      }
    }
    draw();
    hud();
    raf = requestAnimationFrame(frame);
  }

  function onKey(event, down) {
    const map = {
      ArrowLeft: "left",
      ArrowRight: "right",
      ArrowUp: "up",
      ArrowDown: "down",
      KeyA: "left",
      KeyD: "right",
      KeyW: "w",
      KeyS: "s",
    };
    const name = map[event.code];
    if (!name) return;
    if (down) event.preventDefault();
    if (down) keys.add(name);
    else keys.delete(name);
  }
  const down = (event) => onKey(event, true);
  const up = (event) => onKey(event, false);
  window.addEventListener("keydown", down);
  window.addEventListener("keyup", up);
  window.addEventListener("resize", resize);

  function press(name, isDown) {
    if (isDown) keys.add(name);
    else keys.delete(name);
  }

  function start() {
    if (state.phase !== "hold") return;
    state.phase = "countdown";
    state.countdown = 3;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        const actx = new Ctx();
        const osc = actx.createOscillator();
        const gain = actx.createGain();
        osc.type = vehicle.grip >= 0.9 ? "triangle" : vehicle.grip < 0.72 ? "sawtooth" : "square";
        osc.frequency.value = 60;
        gain.gain.value = 0.018;
        osc.connect(gain).connect(actx.destination);
        osc.start();
        audio = { ctx: actx, osc, gain };
      }
    } catch {
      audio = null;
    }
  }

  function destroy() {
    destroyed = true;
    cancelAnimationFrame(raf);
    window.removeEventListener("keydown", down);
    window.removeEventListener("keyup", up);
    window.removeEventListener("resize", resize);
    if (audio) {
      try {
        audio.osc.stop();
        audio.ctx.close();
      } catch {
        /* already stopped */
      }
    }
  }

  resize();
  raf = requestAnimationFrame(frame);

  return {
    start,
    destroy,
    press,
    endEarly() {
      conclude(false, true);
    },
    setOpponent(next) {
      opponent = next;
    },
    setMuted(next) {
      muted = next;
      if (audio) audio.gain.gain.value = next ? 0 : 0.018;
    },
  };
}
