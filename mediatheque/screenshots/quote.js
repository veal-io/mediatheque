// Find a link's highlighted quote (#:~:text=start,end), scroll it to the middle of the
// screen and flash a highlight over it that fades away. Chrome's own text-fragment
// highlight is avoided by loading the page without the fragment, so nothing lingers
// into the screenshot. Returns "found", "hidden" (in the page but not shown, e.g.
// behind a sign-in wall) or "missing".
(f) => {
  const fold = s => s.toLowerCase().replace(/[‘’‛′]/g, "'").replace(/[“”‟″]/g, '"');
  const norm = s => fold(s.replace(/\s+/g, ' ').trim());
  const shown = new Map();
  const isShown = el => {
    if (!shown.has(el)) {
      const b = el.getBoundingClientRect();
      shown.set(el, b.width > 2 && b.height > 2 && getComputedStyle(el).visibility !== 'hidden');
    }
    return shown.get(el);
  };
  const find = visibleOnly => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p || p.closest('script,style,noscript,template')) return NodeFilter.FILTER_REJECT;
        if (visibleOnly && !isShown(p)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const chars = [], map = [];
    let space = true;
    for (let n; (n = walker.nextNode());) {
      const s = n.nodeValue;
      for (let i = 0; i < s.length; i++) {
        let c = s[i];
        if (/\s/.test(c)) { if (space) continue; c = ' '; space = true; } else space = false;
        chars.push(c); map.push([n, i]);
      }
    }
    const hay = fold(chars.join(''));
    const start = norm(f.start);
    if (!start) return null;
    let from = 0;
    if (f.prefix) { const p = hay.indexOf(norm(f.prefix)); if (p >= 0) from = p; }
    let si = hay.indexOf(start, from);
    if (si < 0) si = hay.indexOf(start);
    if (si < 0) return null;
    let ei = si + start.length;
    if (f.end) {
      const e = hay.indexOf(norm(f.end), ei);
      if (e >= 0 && e - si < 8000) ei = e + norm(f.end).length;
    }
    const range = document.createRange();
    range.setStart(map[si][0], map[si][1]);
    range.setEnd(map[ei - 1][0], map[ei - 1][1] + 1);
    return range;
  };

  const range = find(true);
  if (!range) return find(false) ? 'hidden' : 'missing';

  const first = range.startContainer.parentElement;
  first.scrollIntoView({block: 'center'});
  const rects = [...range.getClientRects()].filter(r => r.width > 1 && r.height > 1);
  if (rects.length) {
    const top = Math.min(...rects.map(r => r.top)), bottom = Math.max(...rects.map(r => r.bottom));
    scrollBy(0, (top + bottom) / 2 - innerHeight / 2);  // centre the whole passage, not its first line
  }
  const layer = document.createElement('div');
  layer.className = '__mt-flash';
  layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none;' +
    'transition:opacity .6s ease;opacity:1;';
  for (const r of range.getClientRects()) {
    if (r.width < 1 || r.height < 1) continue;
    const d = document.createElement('div');
    d.style.cssText = `position:absolute;left:${r.left + scrollX - 2}px;top:${r.top + scrollY - 1}px;` +
      `width:${r.width + 4}px;height:${r.height + 2}px;background:rgba(255,226,0,.55);mix-blend-mode:multiply;`;
    layer.append(d);
  }
  document.documentElement.append(layer);
  setTimeout(() => { layer.style.opacity = '0'; }, 1100);
  setTimeout(() => layer.remove(), 1800);
  return 'found';
}
