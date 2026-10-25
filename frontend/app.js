import { mountRace, tally, SCORING } from "./game.js?v=28";

const state = {
  catalog: [],
  modes: [],
  mode: "supercar",
  vehicleId: "",
  raceCtl: null,
  muted: false,
  practice: false,
  name: localStorage.getItem("lane-name") || "",
};

const $ = (id) => document.getElementById(id);

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("is-on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("is-on"), 2800);
}

function toMph(mps) {
  return Math.round((Number(mps) || 0) * 2.236936);
}

function clock(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const min = Math.floor(s / 60);
  const rem = (s - min * 60).toFixed(2).padStart(5, "0");
  return `${min}:${rem}`;
}

function show(view) {
  if (view !== "race" && state.raceCtl) {
    state.raceCtl.destroy();
    state.raceCtl = null;
  }
  document.body.classList.toggle("is-racing", view === "race");
  document.body.classList.toggle("is-gate", view === "gate");
  if (view === "gate") $("gate-name").focus();
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("is-on", el.id === `view-${view}`));
  document.querySelectorAll("[data-nav]").forEach((el) => el.classList.toggle("is-on", el.dataset.nav === view));
}

document.body.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-nav]");
  if (!nav) return;
  event.preventDefault();
  show(nav.dataset.nav);
});

function selectedVehicle() {
  return state.catalog.find((car) => car.id === state.vehicleId);
}

function carsInGarage() {
  return state.catalog.filter((car) => car.mode === "supercar");
}

function band(car, cars, key) {
  const sorted = [...cars].sort((a, b) => a[key] - b[key]);
  const index = sorted.findIndex((item) => item.id === car.id);
  const last = sorted.length - 1;
  if (index === last) return "the highest in the garage";
  if (index === 0) return "the lowest in the garage";
  if (index >= last * 0.66) return "high for this garage";
  if (index <= last * 0.33) return "low for this garage";
  return "in the middle of the garage";
}

function radarVertex(index, count, scale) {
  const angle = -Math.PI / 2 + (index * 2 * Math.PI) / count;
  const reach = 78 * Math.max(0, scale);
  return [120 + Math.cos(angle) * reach, 120 + Math.sin(angle) * reach];
}

function radarSvg(rows, { labels = true, live = false } = {}) {
  const ring = (scale) => rows.map((_, index) => radarVertex(index, rows.length, scale).map((n) => n.toFixed(1)).join(",")).join(" ");
  const shape = rows.map((row, index) => radarVertex(index, rows.length, Math.max(0.08, row.width / row.max)).map((n) => n.toFixed(1)).join(",")).join(" ");
  const spokes = rows.map((_, index) => {
    const [x, y] = radarVertex(index, rows.length, 1);
    return `<line x1="120" y1="120" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"></line>`;
  }).join("");
  const names = labels ? rows.map((row, index) => {
    const [x, y] = radarVertex(index, rows.length, 1.34);
    const anchor = x < 112 ? "end" : x > 128 ? "start" : "middle";
    const text = row.label === "Pace" ? row.value : row.label;
    return `<text class="radar-label${row.label === "Pace" ? " is-pace" : ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}">${text}</text>`;
  }).join("") : "";
  const needle = live
    ? `<line id="hud-speed-needle" x1="120" y1="120" x2="120" y2="120"></line><circle id="hud-speed-dot" cx="120" cy="120" r="5"></circle>`
    : "";
  return `<svg class="radar${labels ? "" : " is-mini"}" viewBox="-16 -8 272 256" role="img" aria-label="Hexagon of this car. The top point is speed.">
    <polygon class="radar-ring" points="${ring(1)}"></polygon>
    <polygon class="radar-ring" points="${ring(0.66)}"></polygon>
    <polygon class="radar-ring" points="${ring(0.33)}"></polygon>
    ${spokes}
    <polygon class="radar-shape" points="${shape}"></polygon>
    ${needle}
    ${names}
  </svg>`;
}

function metricRows(car, cars) {
  const mph = toMph(car.max_speed);
  return [
    {
      label: "Pace",
      value: `${mph} mph`,
      width: car.max_speed,
      max: Math.max(...cars.map((item) => item.max_speed)),
      text: `Pace is the top speed: ${mph} mph, ${band(car, cars, "max_speed")}. The throttle cannot push past it. More pace shortens a straight. It also makes a bend push harder, because that push grows with speed.`,
    },
    {
      label: "Launch",
      value: String(car.accel),
      width: car.accel,
      max: Math.max(...cars.map((item) => item.accel)),
      text: `Launch is ${car.accel}, ${band(car, cars, "accel")}. This is how much speed the car adds each second while ↑ is held. A high launch leaves the line faster and climbs back after a hurdle cuts the speed. It does not raise the top speed.`,
    },
    {
      label: "Brakes",
      value: String(car.brake),
      width: car.brake,
      max: Math.max(...cars.map((item) => item.brake)),
      text: `Brakes are ${car.brake}, ${band(car, cars, "brake")}. This is how fast speed falls while ↓ is held. Use them before a hurdle or a tight bend. A low number means you have to lift off earlier.`,
    },
    {
      label: "Steering",
      value: car.steer.toFixed(2),
      width: car.steer,
      max: Math.max(...cars.map((item) => item.steer)),
      text: `Steering is ${car.steer.toFixed(2)}, ${band(car, cars, "steer")}. This is how fast the nose turns. The turn is weaker when the car is slow and stronger as pace rises. A low number has to begin the turn before the bend arrives.`,
    },
    {
      label: "Grip",
      value: car.grip.toFixed(2),
      width: car.grip,
      max: Math.max(...cars.map((item) => item.grip)),
      text: `Grip is ${car.grip.toFixed(2)}, ${band(car, cars, "grip")}. In a bend the road pushes the car sideways, and grip shrinks that push. Low grip slides toward the grass if you stay flat. High grip stays on the line.`,
    },
    {
      label: "Width",
      value: car.body.toFixed(2),
      width: car.body,
      max: Math.max(...cars.map((item) => item.body)),
      text: `Width is ${car.body.toFixed(2)}, ${band(car, cars, "body")}. This is how much of the lane the body fills. A wider car touches the grass sooner, and the grass cuts the speed.`,
    },
  ];
}

function renderSheet(cars) {
  const car = selectedVehicle();
  const sheet = $("sheet");
  if (!car) {
    sheet.innerHTML = `
      <p class="kicker">The numbers</p>
      <h2>Pick a car to read its own numbers.</h2>
      <p>Pace is top speed. Launch is how fast that speed arrives. Brakes throw speed away. Steering is how quickly the nose turns. Grip is how hard a bend can push you off line. Width is how soon the body finds the grass.</p>
    `;
    return;
  }
  const rows = metricRows(car, cars);
  sheet.innerHTML = `
    <div class="sheet-grid">
      <div>
        ${car.image ? `<img class="pit-photo" src="${car.image}" alt="" />` : ""}
        <p class="kicker">${car.brand}</p>
        <h2>${car.model}</h2>
        <p>${car.character || car.line}</p>
      </div>
      <div>
        ${radarSvg(rows)}
        <p class="radar-caption">A hexagon, one corner for each number. Farther from the middle means higher in this garage. The top corner is speed.</p>
        ${rows.map((row) => `
          <div class="metric">
            <h3>${row.label} <span>${row.value}</span></h3>
            <div class="meter"><span style="width:${(row.width / row.max) * 100}%"></span></div>
            <p>${row.text}</p>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderGarage() {
  const cars = carsInGarage();
  $("cars").innerHTML = cars.map((car) => `
    <button type="button" class="car ${car.id === state.vehicleId ? "is-on" : ""}" data-id="${car.id}">
      ${car.image
        ? `<img class="car-photo" src="${car.image}" alt="${car.brand} ${car.model}" />`
        : `<div class="swatch" style="background:${car.color}"></div>`}
      <span class="kicker">${car.brand}</span>
      <h2>${car.model}</h2>
      <p>${car.line}</p>
      <div class="car-foot">
        <div>
          <p class="pace"><b>${toMph(car.max_speed)}</b><span>mph</span></p>
          <p class="mini">Launch ${car.accel} · grip ${car.grip.toFixed(2)}</p>
        </div>
        ${radarSvg(metricRows(car, cars), { labels: false })}
      </div>
    </button>
  `).join("");
  $("cars").querySelectorAll(".car").forEach((button) => {
    button.addEventListener("click", () => {
      state.vehicleId = button.dataset.id;
      renderGarage();
    });
  });
  renderSheet(cars);
  const picked = !!state.vehicleId;
  $("drive").disabled = false;
  $("practice").disabled = false;
  $("drive").classList.toggle("is-ready", picked);
  $("garage-status").textContent = picked
    ? "Car selected. Drive for rank counts. Practice does not."
    : "Select a car above, then press Drive for rank.";
  $("garage-status").classList.toggle("is-alert", !picked);
}

const TRACK_LENGTH = 200;

function pickupPlan() {
  const coins = [];
  const hurdles = [];
  for (let seg = 14; seg < TRACK_LENGTH - 8; seg += 13) coins.push(seg);
  for (let seg = 20; seg < TRACK_LENGTH - 10; seg += 18) hurdles.push(seg);
  return { coins, hurdles };
}

function buildTrack() {
  const segments = [];
  let curve = 0;
  let hold = 0;
  for (let i = 0; i < TRACK_LENGTH; i += 1) {
    if (hold <= 0) {
      const pick = [-1, -0.55, 0, 0, 0.55, 1][Math.floor(Math.random() * 6)];
      curve = pick;
      hold = 8 + Math.floor(Math.random() * 8);
    }
    hold -= 1;
    segments.push({ curve, altitude: 0, gate: i % 18 === 9 });
  }
  const plan = pickupPlan();
  const pickups = [];
  plan.coins.forEach((seg, index) => {
    pickups.push({ seg, x: index % 2 ? -0.55 : 0.55, kind: "coin" });
  });
  plan.hurdles.forEach((seg) => {
    pickups.push({ seg, x: 0, kind: "hurdle" });
  });
  return { name: "Launch Pad", segments, segment_m: 16, pickups, time_limit: SCORING.timeLimit };
}

function localRace(vehicle) {
  const track = buildTrack();
  return {
    vehicle,
    track,
    modifiers: { grip: 1, accel: 1, wind: 0, glare: 0, pace: 1 },
    card: {
      match: "solo",
      rival_name: "",
      rival_cruise_mps: 0,
      mistake_rate: 0,
      line: "center",
      radio_open: state.practice
        ? practiceLesson(null)
        : "Steer into the coins. Dodge the hurdles.",
      track_name: track.name,
      weather: "Clear",
    },
  };
}

function needlePoint(fraction) {
  const t = Math.max(0, Math.min(1, fraction));
  const angle = (-210 + t * 240) * Math.PI / 180;
  return [100 + Math.cos(angle) * 44, 86 + Math.sin(angle) * 44];
}

function mountDash(car) {
  document.documentElement.style.setProperty("--car", car.color);
  document.documentElement.style.setProperty("--trim", car.trim || "#1c1712");
  const maxMph = toMph(car.max_speed);
  $("dash-brand").textContent = car.brand;
  $("dash-model").textContent = car.model;
  $("dash-cap").textContent = `Top ${maxMph}`;
  $("dash-feel").textContent = car.line;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((mark) => {
    const angle = (-210 + mark * 240) * Math.PI / 180;
    const x1 = 100 + Math.cos(angle) * 50;
    const y1 = 86 + Math.sin(angle) * 50;
    const x2 = 100 + Math.cos(angle) * 60;
    const y2 = 86 + Math.sin(angle) * 60;
    const lx = 100 + Math.cos(angle) * 34;
    const ly = 86 + Math.sin(angle) * 34;
    const label = Math.round(maxMph * mark);
    const hot = mark === 1 ? " is-hot" : "";
    return `<line class="tick${hot}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"></line><text class="tick-label${hot}" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle">${label}</text>`;
  }).join("");
  $("speedo").innerHTML = `${ticks}<line id="speedo-needle" x1="100" y1="86" x2="62" y2="108"></line><circle cx="100" cy="86" r="4"></circle>`;
  $("dash-read").textContent = "0 mph";
}

function feelLine(car, hud) {
  const off = Math.abs(hud.x) + car.body > 1;
  if (off) return car.body > 0.15 ? "The body is wide. The grass has it." : "Off the road. The grass is cutting the speed.";
  if (hud.slip > 0.8) return `Sliding. Grip is ${car.grip.toFixed(2)}, so a flat bend shoves it.`;
  if (hud.braking) return `Brakes are ${car.brake}. The speed is coming off.`;
  if (car.max_speed && hud.speed > car.max_speed * 0.9) return `At this car's own pace, ${toMph(car.max_speed)} mph.`;
  if (hud.steer && car.steer < 1.3) return "The nose is slow. Begin the turn early.";
  if (hud.steer && car.steer > 1.75) return "The nose answers as soon as the wheel turns.";
  return car.line;
}

function practiceLesson(hud) {
  const plan = pickupPlan();
  const next = hud && hud.next;
  if (next && next.dist < 12 && next.kind === "hurdle") {
    return `Hurdle ahead. It is the red and white barrier on the racing line. Hit it and the car slows down. Each one takes ${SCORING.hurdle} points off at the end.`;
  }
  if (next && next.dist < 12) {
    return `Coin ahead. It is the gold disc off the racing line. Steer into it for ${SCORING.coin} points. There are ${plan.coins.length}.`;
  }
  return `Practice. A coin is a gold disc off the racing line, worth ${SCORING.coin} points. A hurdle is a red and white barrier on the racing line: it slows the car and costs ${SCORING.hurdle} points. This score stays off the board.`;
}

function paintHud(hud) {
  const car = selectedVehicle();
  const needle = $("hud-speed-needle");
  const dot = $("hud-speed-dot");
  if (car && needle && dot) {
    const fastest = Math.max(...carsInGarage().map((item) => item.max_speed), 1);
    const used = car.max_speed ? Math.max(0, Math.min(1, hud.speed / car.max_speed)) : 0;
    const [x, y] = radarVertex(0, 6, (car.max_speed / fastest) * used);
    needle.setAttribute("x2", x.toFixed(1));
    needle.setAttribute("y2", y.toFixed(1));
    dot.setAttribute("cx", x.toFixed(1));
    dot.setAttribute("cy", y.toFixed(1));
  }
  const mph = toMph(hud.speed);
  const cap = car ? Math.max(1, toMph(car.max_speed)) : 1;
  const speedNeedle = $("speedo-needle");
  if (speedNeedle) {
    const [x, y] = needlePoint(mph / cap);
    speedNeedle.setAttribute("x2", x.toFixed(1));
    speedNeedle.setAttribute("y2", y.toFixed(1));
  }
  if ($("dash-read")) $("dash-read").textContent = `${mph} mph`;
  if ($("wheel")) $("wheel").style.transform = `rotate(${(hud.steer || 0) * 34}deg)`;
  if (car && $("dash-feel")) $("dash-feel").textContent = feelLine(car, hud);
  $("hud-time").textContent = clock(hud.timeLeft);
  $("hud-time").classList.toggle("is-low", hud.phase === "run" && hud.timeLeft < 15);
  $("hud-speed").textContent = `${mph} mph`;
  if (state.practice) $("hud-radio").textContent = practiceLesson(hud);
  $("hud-place").textContent = hud.place;
  $("hud-extra").textContent = `${hud.score} pts`;
  $("hud-bar").style.width = `${hud.progress * 100}%`;
  const hint = $("hud-hint");
  hint.hidden = state.practice || hud.phase === "countdown" || !(hud.phase === "run" && hud.hint);
  if (!hint.hidden) hint.textContent = "Hold ↑ to drive · ← → steer into a coin";
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[char]);
}

function ordinal(n) {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] || "th"}`;
}

function publicBoard(rows) {
  return (rows || []).filter((row) => !row.house).map((row, index) => ({ ...row, rank: index + 1 }));
}

function renderBoard(rows) {
  const board = publicBoard(rows);
  $("board").innerHTML = board.length
    ? `<table><thead><tr><th>Rank</th><th>Name</th><th>Points</th><th>Races</th></tr></thead><tbody>
        ${board.map((row) => `<tr class="${row.name === state.name ? "is-you" : ""}"><td>${row.rank}</td><td>${escapeHtml(row.name)}</td><td>${row.points}</td><td>${row.races}</td></tr>`).join("")}
      </tbody></table>`
    : "<p>No ranked drives yet.</p>";
}

async function loadBoard() {
  try {
    const response = await fetch("/api/league");
    if (!response.ok) return;
    const data = await response.json();
    renderBoard(data.standings);
  } catch {
    $("board").textContent = "The board is not reachable right now.";
  }
}

function usageBits(usage) {
  const row = usage || {};
  const cost = Number(row.cost_usd || 0);
  return `Input ${row.input_tokens || 0} · cached input ${row.cache_read_tokens || 0} · output ${row.output_tokens || 0} · $${cost.toFixed(6)}`;
}

function traceList(events) {
  if (!events || !events.length) return "";
  return `<ul class="trace">${events.map((event) => `<li><b>${escapeHtml(event.agent)}</b> ${escapeHtml(event.title)} — ${escapeHtml(event.detail)}</li>`).join("")}</ul>`;
}

async function closeReset(scored, data, endedEarly) {
  const response = await fetch("/api/reset/close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      race_id: data.race_id,
      name: state.name,
      coins: scored.coins,
      hurdles: scored.hurdles,
      finished: scored.finished,
      time_left: scored.timeLeft,
      ended_early: endedEarly,
    }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "The clerk could not close that minute.");
  return payload;
}

async function finish(data, result) {
  const scored = tally(result || {});
  const endedEarly = !!(result && result.endedEarly);
  const clockLine = scored.finished
    ? `Finished with ${clock(scored.timeLeft)} still on the clock.`
    : endedEarly
      ? "Ended early. The score is what you had when you stopped."
      : "The clock hit zero before the finish line.";
  let rankLine = "Practice. No model was called, and this score stays off the board.";
  let crew = "";
  if (!state.practice && data.race_id) {
    try {
      const saved = await closeReset(scored, data, endedEarly);
      renderBoard(saved.standings);
      const clerk = saved.clerk || {};
      const posted = saved.scored ? saved.scored.points : scored.total;
      rankLine = clerk.rank_line
        ? escapeHtml(clerk.rank_line)
        : `${escapeHtml(state.name)} now has ${saved.total} points.`;
      const openUsage = data.crewUsage || {};
      const closeUsage = saved.usage || {};
      crew = `
        <h2>${escapeHtml(clerk.headline || "Minute closed.")}</h2>
        <p>${escapeHtml(clerk.body || "")}</p>
        <p>The server posted ${posted}. The clerk said ${clerk.model_said ?? "—"}.${clerk.corrected ? " Those numbers did not match, so the board kept the server's." : ""}</p>
        <p><b>Before the minute.</b> ${usageBits(openUsage.total)}</p>
        <p><b>After the minute.</b> ${usageBits(closeUsage)}</p>
        ${traceList([...(data.crewTrace || []), ...(saved.trace || [])])}
      `;
    } catch (error) {
      rankLine = escapeHtml(error.message);
    }
  }
  show("results");
  $("results").innerHTML = `
    <p class="kicker">${state.practice ? "Practice" : "One minute"}</p>
    <h1>${escapeHtml(data.vehicle.brand)} ${escapeHtml(data.vehicle.model)}</h1>
    <p class="points">${scored.total}</p>
    <p class="lede">${scored.coins} coin${scored.coins === 1 ? "" : "s"} collected. Race total ${scored.total}. ${clockLine}</p>
    <p>${rankLine}</p>
    <ul class="trace">
      <li><b>Coins</b> ${scored.coins} of ${scored.coinTotal} × ${SCORING.coin} = ${scored.lab}</li>
      <li><b>Hurdles</b> ${scored.hurdles} hit × −${SCORING.hurdle} = −${scored.pen}</li>
      <li><b>Finish</b> ${scored.finished ? `+${scored.finish} for crossing the line in time` : endedEarly ? "0 — ended before the line" : "0 — the clock ran out"}</li>
      <li><b>Time left</b> ${scored.finished ? `+${scored.clock} (${SCORING.perSecond} points for each second left)` : "0"}</li>
    </ul>
    ${crew}
    <p>Each coin is ${SCORING.coin} points. A hurdle takes ${SCORING.hurdle} away and slows the car. Crossing the line inside ${SCORING.timeLimit} seconds adds ${SCORING.finish}, then ${SCORING.perSecond} more for every second still on the clock.</p>
    <div class="hero-actions">
      <button type="button" class="solid" id="again">${state.practice ? "Practice again" : "Drive again"}</button>
      <button type="button" class="ghost" id="back">Garage</button>
    </div>
  `;
  $("again").onclick = () => drive(data.vehicle, state.practice);
  $("back").onclick = () => show("garage");
}

async function drive(vehicle = selectedVehicle(), practice = false) {
  if (!vehicle) {
    $("garage-status").textContent = "Select a car above, then press Drive for rank.";
    $("garage-status").classList.add("is-alert");
    document.querySelector("#cars .car")?.scrollIntoView({ behavior: "smooth", block: "center" });
    toast("Select a car first.");
    return;
  }
  state.practice = practice;
  if (!practice && !state.name) {
    toast("Enter your name before a ranked drive.");
    show("gate");
    return;
  }
  const data = localRace(vehicle);
  if (!practice) {
    $("drive").disabled = true;
    $("garage-status").textContent = "The steward is clearing one minute. Stay here.";
    $("garage-status").classList.remove("is-alert");
    try {
      const response = await fetch("/api/race/prepare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.name,
          vehicle_id: vehicle.id,
          match: "ai",
          temperament: "local",
          note: ($("mind").value || "").trim(),
        }),
      });
      const prep = await response.json().catch(() => ({}));
      if (!response.ok || prep.ok === false) {
        $("garage-status").textContent = prep.reason || prep.detail || "The minute did not open.";
        $("garage-status").classList.add("is-alert");
        toast(prep.agent_called === false ? "Stopped before any model call." : "The crew could not open that minute.");
        return;
      }
      data.race_id = prep.race_id;
      data.crewTrace = prep.trace || [];
      data.crewUsage = prep.usage || {};
      const coach = prep.card.coach_note || "";
      const radio = prep.card.radio_open || "";
      data.card.radio_open = [coach, radio].filter(Boolean).join(" ") || data.card.radio_open;
      data.card.track_name = prep.card.track_name || data.card.track_name;
      data.card.weather = prep.card.weather || "Clear";
    } catch (error) {
      $("garage-status").textContent = "The crew is not reachable. Practice still works.";
      $("garage-status").classList.add("is-alert");
      return;
    } finally {
      $("drive").disabled = false;
    }
  }
  show("race");
  window.scrollTo(0, 0);
  $("hud-name").textContent = `${vehicle.brand} ${vehicle.model}`;
  $("hud-trait").textContent = vehicle.line;
  $("hud-track").textContent = data.card.track_name;
  $("hud-radio").textContent = data.card.radio_open;
  $("hud-time").textContent = clock(SCORING.timeLimit);
  $("hud-speed").textContent = "0 mph";
  $("hud-place").textContent = "Coins 0";
  $("hud-extra").textContent = "0 pts";
  $("hud-mode").hidden = !practice;
  $("hud-bar").style.width = "0%";
  $("hud-radar").innerHTML = radarSvg(metricRows(vehicle, carsInGarage()), { live: true });
  mountDash(vehicle);
  state.raceCtl = mountRace($("track"), data, {
    onFrame: (hud) => paintHud(hud),
    onFinish: (_samples, result) => finish(data, result),
  });
  state.raceCtl.start();
  state.raceCtl.setMuted(state.muted);
}

$("drive").addEventListener("click", () => drive(selectedVehicle(), false));
$("practice").addEventListener("click", () => drive(selectedVehicle(), true));

const NAME_OK = /^[A-Za-z][A-Za-z .'\-]{1,22}$/;
$("gate-name").value = state.name;
$("gate").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = $("gate-name").value.trim();
  if (!NAME_OK.test(name)) {
    toast("Use 2–23 letters.");
    return;
  }
  state.name = name;
  localStorage.setItem("lane-name", name);
  $("driver-chip").textContent = name;
  show("garage");
});
$("show-board").addEventListener("click", () => {
  show("garage");
  $("board-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("drill").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("drill-text").value.trim();
  const out = $("drill-out");
  if (!text) {
    out.textContent = "Type something to check.";
    return;
  }
  const response = await fetch("/api/safety/drill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: state.name, text, field: "brief" }),
  });
  const data = await response.json().catch(() => ({}));
  const firewall = data.firewall || {};
  out.classList.toggle("is-stop", firewall.allowed === false);
  out.textContent = firewall.allowed
    ? "That text can go to the crew as a feeling. No points moved."
    : `${firewall.reason} Agent called: ${data.agent_called}. Points moved: ${data.points_moved}.`;
});

document.querySelectorAll(".touch button[data-hold]").forEach((button) => {
  const hold = (down) => {
    if (state.raceCtl) state.raceCtl.press(button.dataset.hold, down);
  };
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    hold(true);
  });
  button.addEventListener("pointerup", () => hold(false));
  button.addEventListener("pointerleave", () => hold(false));
});

$("mute").addEventListener("click", () => {
  state.muted = !state.muted;
  if (state.raceCtl) state.raceCtl.setMuted(state.muted);
  $("mute").textContent = state.muted ? "Muted" : "Sound";
});

$("retire").addEventListener("click", () => show("garage"));
$("end-race").addEventListener("click", () => {
  if (state.raceCtl) state.raceCtl.endEarly();
});

const FALLBACK = {
  modes: [{ id: "supercar", title: "Supercar" }],
  vehicles: [
    { id: "ferrari-296", mode: "supercar", brand: "Ferrari", model: "296 GTB", line: "Sharp nose, low grip.", character: "It turns now and slides if you stay flat.", color: "#b42318", image: "/static/cars/ferrari-296.jpg", max_speed: 80, accel: 26, brake: 34, steer: 1.85, grip: 0.7, body: 0.11 },
    { id: "porsche-taycan", mode: "supercar", brand: "Porsche", model: "Taycan", line: "The strongest launch.", character: "It is at pace before the others clear the pad.", color: "#e8e4dc", image: "/static/cars/porsche-taycan.jpg", max_speed: 76, accel: 32, brake: 35, steer: 1.5, grip: 0.87, body: 0.15 },
    { id: "bentley-continental", mode: "supercar", brand: "Bentley", model: "Continental GT", line: "The slowest and the widest.", character: "Stable, then the clock runs out.", color: "#1f4d3a", image: "/static/cars/bentley-continental.jpg", max_speed: 68, accel: 16, brake: 26, steer: 1.05, grip: 0.9, body: 0.18 },
  ],
};

async function loadCatalog() {
  try {
    const response = await fetch("/api/catalog");
    if (!response.ok) throw new Error("catalog");
    const data = await response.json();
    state.catalog = data.vehicles.filter((car) => car.mode === "supercar");
    state.modes = data.modes.filter((mode) => mode.id === "supercar");
  } catch {
    state.catalog = FALLBACK.vehicles;
    state.modes = FALLBACK.modes;
  }
  renderGarage();
}

loadCatalog();
loadBoard();
