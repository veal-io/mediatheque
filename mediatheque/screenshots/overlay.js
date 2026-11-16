// Mediatheque capture overlay, injected into each article page.
// The page is driven from Python through window.__mt: aim() waits for a click (or a key),
// hide() removes every trace before the screenshot, review() asks Y / A / N.
(cfg) => {
  if (window.__mt) { window.__mt.update(cfg); return; }

  const Z = 2147483647;
  const MAC = /Mac/.test(navigator.platform);
  const FONT = '12px "Microsoft Sans Serif", Tahoma, sans-serif';
  const root = document.createElement('div');
  root.style.cssText = `all:initial;position:fixed;inset:0;z-index:${Z};pointer-events:none;`;
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;box-sizing:border-box;border:2px dashed #000080;' +
    'outline:1px solid #fff;box-shadow:0 0 0 100vmax rgba(0,0,0,.25);pointer-events:none;display:none;';
  const hud = document.createElement('div');
  hud.style.cssText = `position:fixed;left:0;right:0;bottom:0;display:flex;gap:18px;align-items:center;` +
    `padding:5px 10px;background:#c0c0c0;color:#000;font:${FONT};white-space:nowrap;overflow:hidden;` +
    `border-top:1px solid #dfdfdf;box-shadow:inset 0 1px #fff, 0 -1px #808080;pointer-events:none;`;
  const where = document.createElement('b');
  const name = document.createElement('span');
  name.style.cssText = 'overflow:hidden;text-overflow:ellipsis;flex:0 1 auto;min-width:0;';
  const note = document.createElement('span');
  note.style.cssText = 'color:#c00000;font-weight:bold;';
  const keys = document.createElement('span');
  keys.style.cssText = 'margin-left:auto;color:#000;';
  // Starts the screen recorder. The only part of the bar you can click.
  const recBtn = document.createElement('button');
  recBtn.textContent = '\u25CF Record Screen';
  recBtn.style.cssText = `flex:0 0 auto;font:bold ${FONT};color:#c00000;background:#c0c0c0;padding:1px 8px;` +
    'border:1px solid;border-color:#fff #000 #000 #fff;pointer-events:auto;';
  recBtn.style.setProperty('cursor', 'pointer', 'important');
  hud.append(where, name, note, recBtn, keys);
  // The script passage this shot is for, so it's clear what to capture. Part of the
  // overlay, so hide() takes it away before every screenshot along with the box and bar.
  const ctxPanel = document.createElement('div');
  ctxPanel.style.cssText = `position:fixed;left:50%;transform:translateX(-50%);top:8px;box-sizing:border-box;` +
    `width:min(1100px, calc(100vw - 24px));padding:7px 12px 9px;background:#ffffe1;color:#000;` +
    `border:1px solid #000;box-shadow:2px 2px 0 rgba(0,0,0,.35);font:${FONT};pointer-events:none;display:none;` +
    `transition:opacity .12s;`;
  const ctxHead = document.createElement('div');
  ctxHead.style.cssText = 'font-weight:bold;color:#000080;margin-bottom:3px;';
  const ctxBody = document.createElement('div');
  ctxBody.style.cssText = 'display:flex;gap:14px;align-items:stretch;';
  const ctxText = document.createElement('div');
  ctxText.style.cssText = 'flex:3 1 0;min-width:0;font:16px/1.35 Georgia, "Times New Roman", serif;' +
    'max-height:8.1em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;';
  // The editor's note on the highlight, side by side with the passage.
  const ctxNote = document.createElement('div');
  ctxNote.style.cssText = 'flex:2 1 0;min-width:0;padding-left:12px;border-left:2px solid #c8b450;' +
    'font:15px/1.35 Georgia, "Times New Roman", serif;color:#4a3000;white-space:pre-wrap;' +
    'max-height:8.1em;overflow:hidden;';
  const ctxNoteHead = document.createElement('div');
  ctxNoteHead.style.cssText = `font:bold ${FONT};color:#806000;margin-bottom:2px;white-space:normal;`;
  ctxNoteHead.textContent = 'Your note';
  const ctxNoteText = document.createElement('div');
  ctxNote.append(ctxNoteHead, ctxNoteText);
  ctxBody.append(ctxText, ctxNote);
  ctxPanel.append(ctxHead, ctxBody);
  root.append(box, ctxPanel, hud);
  const style = document.createElement('style');
  style.textContent = 'html.__mt-aim, html.__mt-aim * { cursor: none !important; }';
  document.documentElement.append(style, root);

  const startW = Math.min(innerWidth * 0.5, 1100);  // the box starts 4:1
  const st = {
    cfg, w: cfg.w || startW, h: cfg.h || startW / 4, x: innerWidth / 2, y: innerHeight / 2,
    shift: false, mode: 'idle', browse: false, frozen: null, resolve: null, clutter: [], ctxHidden: false,
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const k = s => `<span style="display:inline-block;padding:0 4px;margin-right:3px;border:1px solid;` +
    `border-color:#fff #000 #000 #fff;background:#c0c0c0">${s}</span>`;

  function rect() {
    if (st.frozen) return st.frozen;
    if (st.shift) return {x: 0, y: 0, w: innerWidth, h: innerHeight};
    const w = Math.min(st.w, innerWidth), h = Math.min(st.h, innerHeight);
    return {x: clamp(st.x - w / 2, 0, innerWidth - w), y: clamp(st.y - h / 2, 0, innerHeight - h), w, h};
  }
  function draw() {
    const r = rect();
    const show = st.mode === 'review' || (st.mode === 'aim' && !st.browse);
    Object.assign(box.style, {left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px',
                              display: show ? 'block' : 'none',
                              borderColor: st.mode === 'review' ? '#008000' : '#000080'});
    document.documentElement.classList.toggle('__mt-aim', st.mode === 'aim' && !st.browse);
    placeContext(show ? r : null);
  }
  // Keep the passage out of the way of the box: top of the window, or just above the bar
  // when the box reaches up there. Faded right down if the box covers both (full screen).
  function placeContext(r) {
    const has = !!st.cfg.context && !st.ctxHidden && st.mode !== 'idle';
    ctxPanel.style.display = has ? 'block' : 'none';
    if (!has) return;
    const ph = ctxPanel.offsetHeight, bottomTop = innerHeight - hud.offsetHeight - 8 - ph;
    const hits = top => r && r.y < top + ph + 4 && r.y + r.h > top - 4;
    let top = 8, faded = false;
    if (hits(8)) {
      if (!hits(bottomTop)) top = bottomTop;
      else faded = true;
    }
    ctxPanel.style.top = top + 'px';
    ctxPanel.style.opacity = faded ? '0.18' : '1';
  }
  function setHud() {
    where.textContent = `Article ${st.cfg.index} of ${st.cfg.total}`;
    name.textContent = st.cfg.name || '';
    ctxHead.textContent = st.cfg.label ? `Highlight ${st.cfg.label}: capture for this passage` : 'Capture for this passage';
    ctxText.textContent = st.cfg.context ? `“${st.cfg.context}”` : '';
    ctxNoteText.textContent = st.cfg.note || '';
    ctxNote.style.display = st.cfg.note ? 'block' : 'none';
    const hKey = st.cfg.context ? `${k('H')}${st.ctxHidden ? 'show' : 'hide'} passage ` : '';
    recBtn.style.display = st.mode === 'aim' && !st.browse ? '' : 'none';
    if (st.mode === 'review') {
      keys.innerHTML = `Keep this shot? ${k('Y')}keep, next article ${k('A')}keep, another here ${k('N')}retake ${k('R')}reset page ${hKey}`;
    } else if (st.browse) {
      keys.innerHTML = `<b>Browse mode:</b> use the page normally (sign in, close popups). ${k('B')}back to capturing`;
    } else {
      keys.innerHTML = `${k('Click')}snap ${k('Wheel')}scroll ${k(MAC ? 'Option+Wheel' : 'Alt+Wheel')}height ` +
        `${k(MAC ? 'Cmd+Wheel' : 'Ctrl+Wheel')}width ` +
        `${k('Shift')}full screen ${k('V')}record ${hKey}${k('B')}browse ${k('C')}clutter ${k('R')}reset ${k('S')}skip ${k('Esc')}finish`;
    }
  }
  function finish(value) {
    const r = st.resolve;
    st.resolve = null;
    if (r) r(value);
  }
  const typing = () => {
    const a = document.activeElement;
    return a && (a.isContentEditable || /^(input|textarea|select)$/i.test(a.tagName));
  };

  function toggleClutter() {
    if (st.clutter.length) {
      for (const [el, prop, old] of st.clutter) el.style.setProperty(prop, old);
      st.clutter = [];
      return;
    }
    for (const el of document.querySelectorAll('body *')) {
      const pos = getComputedStyle(el).position;
      if (pos === 'fixed') { st.clutter.push([el, 'display', el.style.display]); el.style.setProperty('display', 'none', 'important'); }
      else if (pos === 'sticky') { st.clutter.push([el, 'visibility', el.style.visibility]); el.style.setProperty('visibility', 'hidden', 'important'); }
    }
  }

  addEventListener('mousemove', e => { st.x = e.clientX; st.y = e.clientY; if (st.mode === 'aim') draw(); }, true);
  addEventListener('resize', draw);

  // Alt+Wheel sets the height, Ctrl+Wheel the width. The wheel listener is only attached
  // while Alt or Ctrl is held: any blocking wheel listener makes the browser wait on the
  // page's scripts before every scroll step, so plain scrolling stays the browser's own
  // smooth scrolling.
  // On a Mac, Cmd works like Ctrl here (Ctrl+scroll is the system's zoom when Accessibility
  // zoom is on), and the step follows how far the wheel moved: a trackpad sends many tiny
  // events, which at a fixed step per event would race. Windows keeps one step per notch.
  const widthKey = e => e.ctrlKey || (MAC && e.metaKey);
  const notches = e => {
    if (!MAC) return 1;
    const px = Math.abs(e.deltaY) * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
    return clamp(px / 100, 0.05, 1.5);
  };
  const onWheel = e => {
    if (st.mode !== 'aim' || st.browse || !(e.altKey || widthKey(e))) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (!e.deltaY) return;
    // Wheel up grows the box. Width moves in smaller steps: the box is about 4x wider than
    // tall, so the same percentage would be about 4x the pixels.
    const grow = e.deltaY < 0;
    const n = notches(e);
    if (widthKey(e)) st.w = clamp(st.w * (grow ? 1.02 : 1 / 1.02) ** n, 60, innerWidth);
    else st.h = clamp(st.h * (grow ? 1.08 : 1 / 1.08) ** n, 20, innerHeight);
    draw();
  };
  let wheelHooked = false;
  const hookWheel = on => {
    if (on === wheelHooked) return;
    wheelHooked = on;
    if (on) addEventListener('wheel', onWheel, {capture: true, passive: false});
    else removeEventListener('wheel', onWheel, {capture: true});
  };
  const aiming = () => st.mode === 'aim' && !st.browse;

  for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'auxclick', 'contextmenu']) {
    addEventListener(type, e => {
      if (st.mode === 'idle' || st.browse) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (e.composedPath().includes(recBtn)) {
        if (type === 'click') finish({type: 'record'});
        return;
      }
      // pointerdown, not mousedown: cancelling pointerdown stops the browser sending mousedown at all.
      if (type === 'pointerdown' && e.button === 0 && st.mode === 'aim') {
        const r = rect();
        st.frozen = r;
        finish({type: 'shot', x: r.x + scrollX, y: r.y + scrollY, w: r.w, h: r.h, vx: r.x, vy: r.y,
                boxW: st.w, boxH: st.h, dpr: devicePixelRatio});
      }
    }, true);
  }

  addEventListener('keydown', e => {
    if (e.key === 'Shift') { st.shift = true; draw(); return; }
    if (e.key === 'Alt' || e.key === 'Control' || (MAC && e.key === 'Meta')) {
      hookWheel(true);
      if (e.key === 'Alt' && aiming()) e.preventDefault();  // don't let Alt open the browser menu
      return;
    }
    if (st.mode === 'idle' || (typing() && st.browse)) return;
    if (MAC && e.metaKey) return;  // Cmd+R, Cmd+C… stay the browser's own shortcuts
    const key = e.key.toLowerCase();
    let handled = true;
    if (key === 'h' && !st.browse && st.cfg.context) {
      st.ctxHidden = !st.ctxHidden; setHud(); draw();
    } else if (st.mode === 'review') {
      if (key === 'y') finish('next');
      else if (key === 'a') finish('more');
      else if (key === 'n' || key === 'escape') finish('retake');
      else if (key === 'r') finish('reset');
      else handled = false;
    } else if (key === 'b') {
      st.browse = !st.browse; setHud(); draw();
    } else if (st.browse) {
      handled = false;
    } else if (key === 'v') {
      finish({type: 'record'});
    } else if (key === 'c') {
      toggleClutter();
    } else if (key === 'r') {
      finish({type: 'reset'});
    } else if (key === 's') {
      finish({type: 'skip'});
    } else if (key === 'escape') {
      finish({type: 'quit'});
    } else {
      handled = false;
    }
    if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  addEventListener('keyup', e => {
    if (e.key === 'Shift') { st.shift = false; draw(); }
    if (e.key === 'Alt' || e.key === 'Control' || (MAC && e.key === 'Meta')) {
      hookWheel(e.altKey || widthKey(e));  // still holding another one?
      if (e.key === 'Alt' && aiming()) e.preventDefault();
    }
  }, true);
  addEventListener('blur', () => { st.shift = false; hookWheel(false); draw(); });

  window.__mt = {
    update(c) { st.cfg = c; setHud(); draw(); },
    message(text) { note.textContent = text || ''; },
    aim() {
      st.mode = 'aim'; st.frozen = null; root.style.display = '';
      setHud(); draw();
      return new Promise(res => { st.resolve = res; });
    },
    hide() {
      root.style.display = 'none';
      document.documentElement.classList.remove('__mt-aim');
      document.querySelectorAll('.__mt-flash').forEach(e => e.remove());
      return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    },
    review() {
      st.mode = 'review'; root.style.display = '';
      setHud(); draw();
      return new Promise(res => { st.resolve = res; });
    },
    idle() { st.mode = 'idle'; st.frozen = null; draw(); },
  };
  setHud();
}
