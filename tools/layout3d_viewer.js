// 3D 佈置圖：由 build_layout3d.py 把 window.LAYOUT（CAD 讀出的牆、功能房、設備）和 3D 庫的程式一起放進同一個頁面。
// 設備先找 3D 庫裡建立模型的函式，找不到的用這裡內建的外觀。
(function () {
  "use strict";
  // 等 3D 庫（可能是 module，會晚一點執行）都跑完再開始
  const go = () => setTimeout(start, 0);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
  function start() {
  const D = window.LAYOUT, THREE = window.THREE;
  const raf = window.__rafOrig || window.requestAnimationFrame.bind(window);
  const LANG = /[?&]lang=en/.test(location.search) ? "en" : "zh";
  const TX = {
    zh: { title: "3D 佈置圖", lines: "線體", other: "未分類設備", walls: "牆壁／隔間", rooms: "功能房", props: "功能房設備", labels: "房間名稱", show: "顯示",
      view3d: "立體", top: "俯視", fit: "全部", lib: "3D 庫模型 {n} 種", libNone: "沒有找到 3D 庫的模型，使用內建外觀", count: "{n} 台",
      model: "外觀：{m}", builtin: "內建", hint: "左鍵旋轉、右鍵平移、滾輪縮放" },
    en: { title: "3D layout", lines: "Lines", other: "Unassigned equipment", walls: "Walls / partitions", rooms: "Rooms", props: "Room equipment", labels: "Room names", show: "Show",
      view3d: "3D", top: "Top", fit: "Fit", lib: "{n} models from the 3D library", libNone: "No 3D library models found; using built-in shapes", count: "{n}",
      model: "Model: {m}", builtin: "built-in", hint: "Left drag to rotate, right drag to pan, scroll to zoom" },
  }[LANG];
  const t = (k, v = {}) => TX[k].replace(/\{(\w+)\}/g, (_, n) => v[n] ?? "");

  // ---------- page ----------
  const css = document.createElement("style");
  css.textContent = `
    html, body { margin: 0; height: 100%; overflow: hidden; background: #e9edf1; font: 14px/1.4 "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif; color: #17222e; }
    canvas:not(#v3d) { display: none !important; }
    #v3d { display: block; width: 100vw; height: 100vh; }
    .ui { position: fixed; left: 12px; top: 12px; width: 240px; max-height: calc(100vh - 24px); overflow: auto; background: rgba(28,42,57,.94); color: #e9eef3;
      border-radius: 12px; padding: 12px 14px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
    .ui h1 { font-size: 16px; margin: 0 0 8px; }
    .ui h2 { font-size: 12px; font-weight: 600; color: #9fb0c1; margin: 12px 0 4px; }
    .ui label { display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; }
    .ui label small { margin-left: auto; color: #9fb0c1; }
    .ui input { accent-color: #f2b01e; width: 16px; height: 16px; }
    .ui .sw { width: 12px; height: 12px; border-radius: 3px; flex: none; }
    .ui .btns { display: flex; gap: 6px; margin-top: 10px; }
    .ui button { flex: 1; border: 1px solid #3a4f66; background: #26384b; color: #e9eef3; border-radius: 8px; padding: 6px 0; cursor: pointer; font: inherit; }
    .ui button:hover { background: #31475e; }
    .ui .note { color: #9fb0c1; font-size: 12px; margin-top: 10px; }
    .tip3 { position: fixed; pointer-events: none; background: #1c2a39; color: #e9eef3; border-radius: 8px; padding: 6px 10px; font-size: 13px; box-shadow: 0 4px 14px rgba(0,0,0,.3); }
    .tip3 b { display: block; font-size: 14px; }
    @media (max-width: 600px) { .ui { width: auto; right: 12px; max-height: 40vh; } }`;
  document.head.appendChild(css);

  // ---------- 3D library ----------
  const norm = (s) => String(s || "").toLowerCase().replace(/^(create|make|build|gen|new)(?=[a-z0-9_])/i, "").replace(/[^a-z0-9一-鿿]+/g, "");
  const KIND_RE = (D.kinds || []).map(([k, p]) => [k, new RegExp(p, "i")]);
  const kindOf = (s) => (KIND_RE.find(([, re]) => re.test(s || "")) || ["generic"])[0];
  const isObj = (o) => o && o.isObject3D;
  function discover() {
    const reg = new Map();  // key → { label, make }
    const skip = /scene|render|camera|light|ground|floor|grid|ui|panel|button|menu|control|gui|anim|loop|init|setup|load|update|resize|tick|select|highlight|label|text|sprite|material|texture|geometry|helper|tween|event|handler|list|card|thumb|preview|show|toggle|canvas|dom|element|html|wall|room|layout|factory$/i;
    const tryMake = (fn, self) => {
      try {
        let o = /^class\b/.test(Function.prototype.toString.call(fn)) ? new fn() : fn.call(self);
        if (o && typeof o.then === "function") return null;
        if (o && !isObj(o) && isObj(o.mesh)) o = o.mesh;
        if (o && !isObj(o) && isObj(o.group)) o = o.group;
        if (o && !isObj(o) && isObj(o.object)) o = o.object;
        return isObj(o) ? o : null;
      } catch (e) { return null; }
    };
    const add = (name, fn, self) => {
      const key = norm(name);
      if (!key || reg.has(key) || skip.test(String(name).replace(/^(create|make|build|gen|new)/i, ""))) return;
      const proto = tryMake(fn, self);
      if (proto) reg.set(key, { label: String(name).replace(/^(create|make|build|gen|new)(?=[A-Z_])/, ""), proto, kind: kindOf(name) });
    };
    // only names the library itself declares (never browser built-ins or the three.js namespace)
    const isNative = (f) => { try { return /\[native code\]/.test(Function.prototype.toString.call(f)); } catch (e) { return true; } };
    for (const n of new Set(D.libNames || [])) {
      if (n === "THREE" || n === "LAYOUT") continue;
      let v;
      try { v = window[n] !== undefined ? window[n] : (0, eval)(n); } catch (e) { continue; }
      if (v === THREE || (typeof v === "function" && isNative(v))) continue;
      if (typeof v === "function") {
        if (/^(create|make|build|gen|new)[A-Z_0-9]/.test(n) || /^class\b/.test(Function.prototype.toString.call(v))) add(n, v, window);
      } else if (v && typeof v === "object" && v !== window && !v.nodeType && !isObj(v)) {
        // catalogs: { Reflow: () => …, … } or [{ name, create }, …]
        let entries;
        try { entries = Array.isArray(v) ? v.map(x => [x && (x.name || x.id || x.key || x.type || x.label), x]) : Object.entries(v); } catch (e) { continue; }
        if (entries.length < 2 || entries.length > 600) continue;
        for (const [name, x] of entries) {
          if (typeof name !== "string") continue;
          const fn = typeof x === "function" ? x : x && (x.create || x.build || x.make || x.fn || x.factory || x.builder || x.model);
          if (typeof fn === "function") add(name, fn, x);
          else if (isObj(x)) reg.has(norm(name)) || reg.set(norm(name), { label: name, proto: x, kind: kindOf(name) });
        }
      }
    }
    return reg;
  }
  const LIB = discover();
  const libKeys = [...LIB.keys()];
  function pickModel(eq) {
    if (!LIB.size) return null;
    const want = norm(eq.model);
    if (want) {
      if (LIB.has(want)) return LIB.get(want);
      const k = libKeys.find(k => k.includes(want) || want.includes(k));
      if (k) return LIB.get(k);
    }
    const nm = norm(eq.name) + " " + norm(eq.block);
    let best = null;
    for (const k of libKeys) if (k.length >= 3 && nm.includes(k) && (!best || k.length > best.length)) best = k;
    if (best) return LIB.get(best);
    return [...LIB.values()].find(m => m.kind === eq.kind && eq.kind !== "generic") || null;
  }

  // ---------- built-in shapes (used when the 3D library has nothing that fits) ----------
  const MAT = {};
  const mat = (c, o = {}) => MAT[c + JSON.stringify(o)] ||= new THREE.MeshStandardMaterial({ color: c, roughness: .65, metalness: .1, ...o });
  const BOX = new THREE.BoxGeometry(1, 1, 1), CYL = new THREE.CylinderGeometry(.5, .5, 1, 16);
  function box(g, w, h, d, c, x = 0, y = 0, z = 0, o) { const m = new THREE.Mesh(BOX, mat(c, o)); m.scale.set(w, h, d); m.position.set(x, y + h / 2, z); g.add(m); return m; }
  function cyl(g, r, h, c, x = 0, y = 0, z = 0) { const m = new THREE.Mesh(CYL, mat(c)); m.scale.set(r * 2, h, r * 2); m.position.set(x, y + h / 2, z); g.add(m); return m; }
  function tower(g, x, y, z) { cyl(g, .04, .3, "#555", x, y, z); ["#2bb24c", "#f2b01e", "#d9412b"].forEach((c, i) => cyl(g, .05, .07, c, x, y + .3 + i * .07, z)); }
  const SHAPES = {
    mounter(g, w, d) { box(g, w, 1.25, d, "#e6e8eb"); box(g, w * .9, .3, d * .8, "#2b3a4a", 0, 1.25, 0, { opacity: .85, transparent: true }); box(g, w, .08, d * .15, "#2f6db0", 0, .9, d * .43); tower(g, w * .4, 1.55, -d * .35); },
    printer(g, w, d) { box(g, w, 1.1, d, "#f1f2f3"); box(g, w * .95, .35, d * .9, "#cfd5db", 0, 1.1, 0); box(g, w * .6, .05, d * .2, "#2f6db0", 0, .8, d * .46); tower(g, w * .4, 1.45, -d * .35); },
    reflow(g, w, d) { box(g, w, .95, d, "#d7dbe0"); box(g, w * .98, .3, d * .9, "#8a96a3", 0, .95, 0); for (let i = -1; i <= 1; i++) cyl(g, .07, .35, "#9aa3ad", i * w * .3, 1.25, 0); },
    aoi(g, w, d) { box(g, w, 1.0, d, "#eceef0"); box(g, w * .9, .5, d * .85, "#24323f", 0, 1.0, 0, { opacity: .8, transparent: true }); tower(g, w * .4, 1.5, -d * .35); },
    spi(g, w, d) { SHAPES.aoi(g, w, d); },
    wave(g, w, d) { box(g, w, 1.1, d, "#d9dde1"); box(g, w * .9, .35, d * .85, "#7d8995", 0, 1.1, 0); },
    dispenser(g, w, d) { box(g, w, 1.2, d, "#f4f5f6"); box(g, w * .85, .45, d * .8, "#30404f", 0, 1.2, 0, { opacity: .8, transparent: true }); },
    loader(g, w, d) { box(g, w, .7, d, "#c9ced4"); box(g, w * .8, .7, d * .8, "#8592a0", 0, .7, 0); },
    buffer(g, w, d) { box(g, w, .85, d, "#d3d8dd"); box(g, w, .06, d * .7, "#33414f", 0, .85, 0); },
    conveyor(g, w, d) { for (const a of [-.45, .45]) for (const b of [-.4, .4]) box(g, .05, .75, .05, "#8a939c", a * w, 0, b * d);
      box(g, w, .08, d, "#2f3b47", 0, .75, 0); box(g, w, .1, .04, "#b9c1c9", 0, .78, d / 2); box(g, w, .1, .04, "#b9c1c9", 0, .78, -d / 2); },
    tester(g, w, d) { box(g, w, 1.7, d, "#e3e6e9"); box(g, w * .9, .5, .02, "#1d2833", 0, .9, d / 2 + .01); tower(g, w * .35, 1.7, 0); },
    robot(g, w, d) { box(g, w, .8, d, "#d2d7dc"); cyl(g, .12, .4, "#f2b01e", 0, .8, 0); box(g, .1, .1, Math.min(.7, d * .6), "#f2b01e", 0, 1.15, Math.min(.35, d * .3)); },
    rack(g, w, d) { const h = 2.2; for (const x of [-.48, .48]) for (const z of [-.45, .45]) box(g, .05, h, .05, "#3d5a80", x * w, 0, z * d);
      for (let i = 0; i < 4; i++) box(g, w, .04, d, "#c8a35a", 0, .3 + i * .6, 0); },
    cart(g, w, d) { box(g, w, .15, d, "#7f8a96", 0, .2, 0); box(g, .04, .8, d * .8, "#7f8a96", -w / 2, .35, 0); for (const x of [-.4, .4]) for (const z of [-.4, .4]) cyl(g, .05, .1, "#222", x * w, .05, z * d); },
    bench(g, w, d) { box(g, w, .05, d, "#b9c3cc", 0, .78, 0); for (const x of [-.45, .45]) for (const z of [-.4, .4]) box(g, .04, .78, .04, "#6b7682", x * w, 0, z * d);
      box(g, Math.min(.5, w * .4), .32, .03, "#1d2833", 0, .83, -d * .3); },
    wrap(g, w, d) { cyl(g, Math.min(w, d) * .45, .15, "#8a939c"); box(g, .1, 2.2, .1, "#8a939c", w * .45, 0, 0); },
    generic(g, w, d) { box(g, w, 1.1, d, "#cfd6dd"); box(g, w * .8, .03, d * .8, "#eef1f4", 0, 1.1, 0); },
  };
  function builtin(kind, w, d) { const g = new THREE.Group(); (SHAPES[kind] || SHAPES.generic)(g, Math.max(w, .2), Math.max(d, .2)); return g; }

  // fit a library model to the CAD footprint (w along the block's x, d along its y)
  const protoBox = new Map();
  function fitted(entry, w, d) {
    let b = protoBox.get(entry);
    if (!b) { const bb = new THREE.Box3().setFromObject(entry.proto); b = { min: bb.min.clone(), size: bb.getSize(new THREE.Vector3()), c: bb.getCenter(new THREE.Vector3()) }; protoBox.set(entry, b); }
    const o = entry.proto.clone(true), wrap = new THREE.Group(), inner = new THREE.Group();
    inner.add(o);
    o.position.set(-b.c.x, -b.min.y, -b.c.z);
    const turn = (b.size.x >= b.size.z) !== (w >= d);
    if (turn) inner.rotation.y = Math.PI / 2;
    const mx = turn ? b.size.z : b.size.x, mz = turn ? b.size.x : b.size.z;
    const sx = w / Math.max(mx, 1e-6), sz = d / Math.max(mz, 1e-6);
    let sy = Math.sqrt(sx * sz);
    const h = b.size.y * sy; if (h > 3.5) sy *= 3.5 / h; if (h < .3 && h > 0) sy *= .3 / h;
    wrap.scale.set(sx, sy, sz);
    wrap.add(inner);
    return wrap;
  }

  // ---------- scene ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.domElement.id = "v3d";
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#e9edf1");
  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, .1, 20000);
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.screenSpacePanning = false; controls.maxPolarAngle = Math.PI / 2.05;
  scene.add(new THREE.HemisphereLight("#ffffff", "#b8c2cc", .85));
  const sun = new THREE.DirectionalLight("#ffffff", .55); sun.position.set(1, 2, 1.5); scene.add(sun);

  // bounds
  const xs = [], zs = [];
  D.walls.forEach(w => { xs.push(w[0], w[2]); zs.push(w[1], w[3]); });
  D.equip.forEach(e => { xs.push(e.x); zs.push(e.z); });
  D.rooms.forEach(r => r.pts.forEach(p => { xs.push(p[0]); zs.push(p[1]); }));
  if (!xs.length) { xs.push(0, 10); zs.push(0, 10); }
  const q = (a, f) => { const v = a.slice().sort((x, y) => x - y); return v[Math.floor((v.length - 1) * f)]; };
  const B = { x0: q(xs, .005), x1: q(xs, .995), z0: q(zs, .005), z1: q(zs, .995) };
  const C = { x: (B.x0 + B.x1) / 2, z: (B.z0 + B.z1) / 2 }, SPAN = Math.max(B.x1 - B.x0, B.z1 - B.z0, 10);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(SPAN * 1.3, SPAN * 1.3), mat("#f7f8f9"));
  ground.rotation.x = -Math.PI / 2; ground.position.set(C.x, -.01, C.z); scene.add(ground);

  const layers = { walls: new THREE.Group(), rooms: new THREE.Group(), props: new THREE.Group(), labels: new THREE.Group() };
  Object.values(layers).forEach(g => scene.add(g));

  // walls & columns (one instanced mesh each)
  if (D.walls.length) {
    const wm = new THREE.InstancedMesh(BOX, mat("#c3cbd3"), D.walls.length), m4 = new THREE.Matrix4(), qn = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
    D.walls.forEach((w, i) => {
      const L = Math.hypot(w[2] - w[0], w[3] - w[1]), a = Math.atan2(-(w[3] - w[1]), w[2] - w[0]);
      qn.setFromAxisAngle(up, a);
      m4.compose(new THREE.Vector3((w[0] + w[2]) / 2, D.wall_h / 2, (w[1] + w[3]) / 2), qn, new THREE.Vector3(L + D.wall_t, D.wall_h, D.wall_t));
      wm.setMatrixAt(i, m4);
    });
    layers.walls.add(wm);
  }
  if (D.cols.length) {
    const cm = new THREE.InstancedMesh(BOX, mat("#9aa6b2"), D.cols.length), m4 = new THREE.Matrix4();
    D.cols.forEach((c, i) => { m4.compose(new THREE.Vector3(c[0], D.wall_h / 2, c[1]), new THREE.Quaternion(), new THREE.Vector3(Math.max(c[2], .2), D.wall_h, Math.max(c[3], .2))); cm.setMatrixAt(i, m4); });
    layers.walls.add(cm);
  }

  // rooms: tinted floor, name, and things that belong in that kind of room
  const ROOM_COLOR = { warehouse: "#f3d9a4", office: "#cfe0f3", meeting: "#d9d2f0", lab: "#cdeedd", server: "#d5dbe2", rest: "#f6dccf", wc: "#dbeef2", utility: "#e3e3d3", other: "#e4e7ea" };
  function sprite(text, size) {
    const c = document.createElement("canvas"), ctx = c.getContext("2d"), fs = 48;
    ctx.font = `600 ${fs}px "Microsoft JhengHei", "Segoe UI", sans-serif`;
    const w = Math.ceil(ctx.measureText(text).width) + 24; c.width = w; c.height = fs + 20;
    ctx.font = `600 ${fs}px "Microsoft JhengHei", "Segoe UI", sans-serif`;
    ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.fillRect(0, 0, w, c.height);
    ctx.fillStyle = "#1d2833"; ctx.textBaseline = "middle"; ctx.fillText(text, 12, c.height / 2);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
    s.scale.set(size * w / c.height, size, 1); s.renderOrder = 10;
    return s;
  }
  const inPoly = (x, z, pts) => { let ins = false; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) { const [xi, zi] = pts[i], [xj, zj] = pts[j]; if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / ((zj - zi) || 1e-9) + xi) ins = !ins; } return ins; };
  function fillRoom(r) {
    const xs = r.pts.map(p => p[0]), zs = r.pts.map(p => p[1]);
    const x0 = Math.min(...xs) + 1, x1 = Math.max(...xs) - 1, z0 = Math.min(...zs) + 1, z1 = Math.max(...zs) - 1;
    if (x1 - x0 < 1.5 || z1 - z0 < 1.5) return;
    const spec = { warehouse: ["rack", 2.4, 1.0, 3.2, 3.0], office: ["bench", 1.4, .7, 3.0, 2.8], meeting: ["bench", 3, 1.2, 5, 4], lab: ["bench", 1.8, .75, 3, 2.6],
      server: ["tester", .6, 1.0, 1.4, 2.4], rest: ["bench", 1.2, 1.2, 3, 3], utility: ["tank", 1.4, 1.4, 3, 3] }[r.kind];
    if (!spec) return;
    const [kind, w, d, stepX, stepZ] = spec;
    let n = 0;
    for (let x = x0 + w / 2; x <= x1 - w / 2 && n < 220; x += stepX) for (let z = z0 + d / 2; z <= z1 - d / 2 && n < 220; z += stepZ) {
      if (!inPoly(x, z, r.pts)) continue;
      let g;
      if (kind === "tank") { g = new THREE.Group(); cyl(g, w / 2, 2.2, "#b8c4cf"); cyl(g, w / 2 * .98, .1, "#8a96a3", 0, 2.2, 0); }
      else g = builtin(kind, w, d);
      g.position.set(x, 0, z); layers.props.add(g); n++;
      if (r.kind === "meeting") break;
    }
  }
  for (const r of D.rooms) {
    const sh = new THREE.Shape(r.pts.map(p => new THREE.Vector2(p[0], -p[1])));
    const m = new THREE.Mesh(new THREE.ShapeGeometry(sh), mat(ROOM_COLOR[r.kind] || ROOM_COLOR.other, { transparent: true, opacity: .55 }));
    m.rotation.x = -Math.PI / 2; m.position.y = .005; layers.rooms.add(m);
    if (r.name) {
      const cx = r.pts.reduce((a, p) => a + p[0], 0) / r.pts.length, cz = r.pts.reduce((a, p) => a + p[1], 0) / r.pts.length;
      const s = sprite(r.name, Math.max(.6, Math.min(2.2, Math.sqrt(r.area) / 8))); s.position.set(cx, D.wall_h + .6, cz); layers.labels.add(s);
    }
    fillRoom(r);
  }
  if (!D.rooms.length) for (const rt of D.room_texts) { const s = sprite(rt.text, 1); s.position.set(rt.x, D.wall_h + .6, rt.z); layers.labels.add(s); }

  // equipment, grouped by line
  const lineKeys = [...new Set(D.equip.map(e => e.line || ""))].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
  const lineGroups = {}, lineCount = {};
  lineKeys.forEach(k => { lineGroups[k] = new THREE.Group(); scene.add(lineGroups[k]); lineCount[k] = 0; });
  const LINE_COLOR = { bianca: "#2f6db0", strata: "#2a9d8f" };
  let fromLib = 0;
  for (const e of D.equip) {
    const entry = pickModel(e);
    let obj;
    try { obj = entry ? fitted(entry, e.w, e.d) : null; } catch (err) { obj = null; }
    if (obj) fromLib++; else obj = builtin(e.kind, e.w, e.d);
    const holder = new THREE.Group();
    holder.add(obj);
    holder.position.set(e.x, 0, e.z);
    holder.rotation.y = e.rot * Math.PI / 180;
    holder.userData.eq = { ...e, modelName: entry ? entry.label : "" };
    if (e.line && LINE_COLOR[e.line]) {  // thin coloured base plate shows which line a machine belongs to
      const plate = new THREE.Mesh(BOX, mat(LINE_COLOR[e.line])); plate.scale.set(e.w + .1, .02, e.d + .1); plate.position.y = .01; holder.add(plate);
    }
    lineGroups[e.line || ""].add(holder);
    lineCount[e.line || ""]++;
  }

  // ---------- controls panel ----------
  const ui = document.createElement("div"); ui.className = "ui";
  const lineLabel = (k) => k ? (D.lines?.[k] || k) : t("other");
  const row = (id, label, on, extra = "", sw = "") => `<label><input type="checkbox" id="${id}" ${on ? "checked" : ""}>${sw ? `<span class="sw" style="background:${sw}"></span>` : ""}<span>${label}</span><small>${extra}</small></label>`;
  ui.innerHTML = `<h1>${t("title")}</h1>
    <h2>${t("lines")}</h2>${lineKeys.map((k, i) => row("ln" + i, lineLabel(k), true, t("count", { n: lineCount[k] }), LINE_COLOR[k] || "#9aa6b2")).join("")}
    <h2>${t("show")}</h2>${row("tgWalls", t("walls"), true)}${row("tgRooms", t("rooms"), true)}${row("tgProps", t("props"), true)}${row("tgLabels", t("labels"), true)}
    <div class="btns"><button id="b3d">${t("view3d")}</button><button id="bTop">${t("top")}</button><button id="bFit">${t("fit")}</button></div>
    <div class="note">${LIB.size ? t("lib", { n: LIB.size }) + `（${fromLib}／${D.equip.length}）` : t("libNone")}<br>${t("hint")}</div>`;
  document.body.appendChild(ui);
  lineKeys.forEach((k, i) => ui.querySelector("#ln" + i).onchange = (ev) => { lineGroups[k].visible = ev.target.checked; });
  ui.querySelector("#tgWalls").onchange = (ev) => { layers.walls.visible = ev.target.checked; };
  ui.querySelector("#tgRooms").onchange = (ev) => { layers.rooms.visible = ev.target.checked; };
  ui.querySelector("#tgProps").onchange = (ev) => { layers.props.visible = ev.target.checked; };
  ui.querySelector("#tgLabels").onchange = (ev) => { layers.labels.visible = ev.target.checked; };
  function view(top) {
    controls.target.set(C.x, 0, C.z);
    if (top) camera.position.set(C.x, SPAN * 1.05, C.z + .01);
    else camera.position.set(C.x - SPAN * .35, SPAN * .55, C.z + SPAN * .65);
    controls.update();
  }
  ui.querySelector("#b3d").onclick = () => view(false);
  ui.querySelector("#bTop").onclick = () => view(true);
  ui.querySelector("#bFit").onclick = () => view(false);
  view(false);

  // ---------- hover ----------
  const tip = document.createElement("div"); tip.className = "tip3"; tip.hidden = true; document.body.appendChild(tip);
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let last = 0;
  renderer.domElement.addEventListener("pointermove", (ev) => {
    const now = performance.now(); if (now - last < 60) return; last = now;
    mouse.set(ev.clientX / window.innerWidth * 2 - 1, -(ev.clientY / window.innerHeight) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const vis = lineKeys.filter(k => lineGroups[k].visible).map(k => lineGroups[k]);
    const hit = ray.intersectObjects(vis, true)[0];
    let o = hit && hit.object; while (o && !o.userData.eq) o = o.parent;
    if (!o) { tip.hidden = true; return; }
    const e = o.userData.eq;
    tip.innerHTML = `<b>${e.name.replace(/</g, "&lt;")}</b>${lineLabel(e.line)} · ${e.w.toFixed(1)} × ${e.d.toFixed(1)} m<br>${t("model", { m: e.modelName || t("builtin") })}`;
    tip.hidden = false; tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, ev.clientX + 14) + "px"; tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - 10) + "px";
  });
  renderer.domElement.addEventListener("pointerleave", () => { tip.hidden = true; });

  window.addEventListener("resize", () => { camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix(); renderer.setSize(window.innerWidth, window.innerHeight); });
  // 3D 庫自己加到頁面上的東西（展示用的畫面、按鈕）藏起來
  const mine = new Set([renderer.domElement, ui, tip]);
  const hideOthers = () => [...document.body.children].forEach(el => { if (!mine.has(el) && el.tagName !== "SCRIPT" && el.tagName !== "STYLE") el.style.display = "none"; });
  hideOthers(); setTimeout(hideOthers, 1500);
  (function loop() { controls.update(); renderer.render(scene, camera); raf(loop); })();
  }
})();
