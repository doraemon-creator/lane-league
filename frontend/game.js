/* Lane League. One road, one car, one minute. */

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
  const timeLeft = finished ? Math.max(0, result.timeLeft || 0) : 0;
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

const PALETTE = {
  sky: "#1b1612",
  ground: "#3c4632",
  roadA: "#2c2926",
  roadB: "#34302c",
  rumbleA: "#c5341b",
  rumbleB: "#f3ead7",
  fog: "#1b1612",
  sun: "#e2b034",
};

export function mountRace(canvas, payload, hooks) {
  const ctx = canvas.getContext("2d");
  const vehicle = payload.vehicle;
  const track = payload.track;
  const mods = payload.modifiers;
  const segments = track.segments;
  const segmentM = track.segment_m;
  const total = segments.length;
  const pickups = (track.pickups || []).map((item) => ({ ...item, hit: false, passed: false }));
  const coinTotal = pickups.filter((item) => item.kind === "coin").length;
  const keys = new Set();
  let width = 1;
  let height = 1;
  let floor = 0;
  let raf = 0;
  let last = performance.now();
  let audio = null;
  let muted = false;
  let destroyed = false;

  const state = {
    phase: "hold",
    countdown: 3,
    seg: 0,
    x: 0,
    speed: 0,
    time: 0,
    samples: [],
    sampleAcc: 0,
    finished: false,
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
    // The control bar sits over the bottom of the canvas. Keep the car's hood above it.
    const controls = parseFloat(getComputedStyle(canvas.parentElement).getPropertyValue("--controls-h")) || 0;
    floor = Math.min(height * 0.3, controls + 16);
  }

  function round(n) {
    return Math.round(n * 10000) / 10000;
  }

  // The trail the server recounts. One point every 0.1 s, plus one on every frame that passes a coin or hurdle.
  function sample() {
    const prev = state.samples[state.samples.length - 1];
    const point = { t: round(state.time), seg: round(state.seg), x: round(state.x), speed: round(state.speed) };
    if (prev && point.t <= prev.t) {
      Object.assign(prev, point, { t: prev.t });
      return;
    }
    state.samples.push(point);
  }

  function sweep(from, to) {
    let crossed = false;
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
      crossed = true;
    });
    return crossed;
  }

  function step(dt) {
    const prevSeg = state.seg;
    const seg = segments[Math.min(total - 1, Math.floor(state.seg))];
    const steer = (keys.has("right") ? 1 : 0) - (keys.has("left") ? 1 : 0);
    const throttle = keys.has("w") || keys.has("up") ? 1 : 0;
    const brake = keys.has("s") || keys.has("down") ? 1 : 0;

    const grip = vehicle.grip * mods.grip;
    if (throttle) state.speed += vehicle.accel * mods.accel * dt;
    if (brake) state.speed -= vehicle.brake * dt;
    state.speed -= state.speed * 0.05 * dt;
    const off = Math.abs(state.x) + vehicle.body > 1;
    if (off) state.speed -= 24 * dt;
    const cap = vehicle.max_speed * (off ? 0.62 : 1);
    state.speed = Math.max(0, Math.min(cap, state.speed));

    const speedRatio = vehicle.max_speed ? state.speed / vehicle.max_speed : 0;
    state.x += steer * vehicle.steer * dt * (0.5 + 0.5 * speedRatio);
    const push = (seg.curve || 0) * 2.05 * speedRatio * speedRatio * (1.22 - grip) * dt;
    state.x += push;
    state.steerInput = steer;
    state.braking = brake;
    state.slip = Math.abs(push) / Math.max(0.016, dt);
    if (mods.wind) state.x += Math.sin(state.time * 1.4) * mods.wind * 0.1 * dt;
    state.x = Math.max(-1.65, Math.min(1.65, state.x));
    state.seg += (state.speed / segmentM) * dt;
    state.time += dt;
    const crossed = sweep(prevSeg, state.seg);

    state.sampleAcc += dt;
    if (state.sampleAcc >= 0.1 || crossed) {
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
    state.time = Math.min(state.time, SCORING.timeLimit);
    sample();
    state.speed = 0;
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

  function drawCockpit() {
    const lean = -state.x * 18;
    const wide = 0.62 + (vehicle.body || 0.12) * 0.9;
    const nose = height * (0.045 + (vehicle.body || 0.12) * 0.05);
    const base = height - floor;
    ctx.save();
    ctx.translate(width / 2 + lean, base);
    ctx.fillStyle = vehicle.color || "#b42318";
    ctx.beginPath();
    ctx.moveTo(-width * 0.22 * wide, floor + 6);
    ctx.lineTo(-width * 0.2 * wide, 6);
    ctx.lineTo(-width * 0.06 * wide, -nose);
    ctx.lineTo(width * 0.06 * wide, -nose);
    ctx.lineTo(width * 0.2 * wide, 6);
    ctx.lineTo(width * 0.22 * wide, floor + 6);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, 4);
    ctx.lineTo(0, -nose + 6);
    ctx.stroke();
    ctx.restore();
  }

  function draw() {
    const horizon = height * 0.2;
    ctx.fillStyle = PALETTE.sky;
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = PALETTE.ground;
    ctx.fillRect(0, horizon, width, height - horizon);
    ctx.fillStyle = PALETTE.sun;
    ctx.beginPath();
    ctx.arc(width * 0.78, horizon * 0.55, 18, 0, Math.PI * 2);
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
      const y = horizon + ((height - floor * 0.6) * 0.68) / z;
      const cx = width / 2 + bend * half * 0.8 - state.x * half;
      rows.push({ index, n, y, half, cx, scale: half / 90 });
    }

    for (let i = rows.length - 1; i > 0; i -= 1) {
      const far = rows[i];
      const near = rows[i - 1];
      if (far.y >= near.y) continue;
      const fog = Math.min(0.82, far.n / 48);
      ctx.fillStyle = mix(PALETTE.ground, PALETTE.fog, fog);
      trap(far.cx, far.y, far.half * 8, near.cx, near.y, near.half * 8);
      const rumble = far.index % 4 < 2 ? PALETTE.rumbleA : PALETTE.rumbleB;
      ctx.fillStyle = mix(rumble, PALETTE.fog, fog);
      trap(far.cx, far.y, far.half * 1.18, near.cx, near.y, near.half * 1.18);
      const asphalt = far.index % 2 ? PALETTE.roadA : PALETTE.roadB;
      ctx.fillStyle = mix(asphalt, PALETTE.fog, fog);
      trap(far.cx, far.y, far.half, near.cx, near.y, near.half);
      if (far.index % 6 === 0) {
        ctx.fillStyle = mix("#f3ead7", PALETTE.fog, fog);
        trap(far.cx, far.y, far.half * 0.06, near.cx, near.y, near.half * 0.06);
      }
      if (segments[far.index].gate) {
        ctx.fillStyle = mix("#e2b034", PALETTE.fog, fog * 0.5);
        const gateHalf = far.half * 0.62;
        ctx.fillRect(far.cx - gateHalf - 4, far.y - far.half * 0.45, 6, far.half * 0.5);
        ctx.fillRect(far.cx + gateHalf, far.y - far.half * 0.45, 6, far.half * 0.5);
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
    const timeLeft = state.phase === "countdown" ? SCORING.timeLimit : Math.max(0, SCORING.timeLimit - state.time);
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
      progress: Math.min(1, state.seg / total),
      place: `Coins ${state.coins}/${coinTotal} · hurdles ${state.hurdles}`,
      coins: state.coins,
      hurdles: state.hurdles,
      score: state.coins * SCORING.coin - state.hurdles * SCORING.hurdle,
      slip: state.slip,
      steer: state.steerInput,
      braking: state.braking,
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
    } else if (state.phase === "run" && dt > 0) {
      step(dt);
      if (audio && !muted) {
        audio.osc.frequency.value = 36 + vehicle.accel * 0.8 + state.speed * (0.55 + vehicle.body * 4);
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
    setMuted(next) {
      muted = next;
      if (audio) audio.gain.gain.value = next ? 0 : 0.018;
    },
  };
}
