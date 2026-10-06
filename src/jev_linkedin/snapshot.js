/* Adapted from browser-use/jev-ultrafast (MIT); see vendor/jev_ultrafast/LICENSE. */
(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids: new WeakMap(), nodes: new Map(), next: 1};
  const limits = {text: 12000, actions: 250, groupText: 2200};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e, cache.next++);
    const id = cache.ids.get(e); cache.nodes.set(id, e); return id;
  };
  for (const [id, e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const headingSelector = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
  const headingLevel = e => Number(e.getAttribute('aria-level')) || Number(e.tagName.slice(1)) || 2;
  const excludedRoots = new Set();
  // Read the complete DOM so a recommendation heading remains effective after it
  // scrolls out of view. Never remove nodes or blacklist an enclosing main region.
  for (const heading of document.querySelectorAll(headingSelector)) {
    if (!/^people also viewed$/i.test(clean(heading.textContent))) continue;
    let wrapper = heading;
    while (wrapper.parentElement && !wrapper.parentElement.matches('main,body,html,[role="main"]') &&
        clean(wrapper.parentElement.textContent) === clean(heading.textContent)) wrapper = wrapper.parentElement;
    const parent = wrapper.parentElement;
    if (!parent) continue;
    const peers = [...parent.querySelectorAll(headingSelector)].filter(h =>
      h !== heading && !wrapper.contains(h) && headingLevel(h) <= headingLevel(heading));
    if (!parent.matches('main,body,html,[role="main"]') && !peers.length) {
      excludedRoots.add(parent);
    } else {
      // Flat or shared wrappers: exclude only this heading's following sibling
      // range, ending at the next heading at the same or higher level.
      for (let node = wrapper; node; node = node.nextElementSibling) {
        const headings = [node, ...node.querySelectorAll(headingSelector)].filter(h => h.matches(headingSelector));
        if (node !== wrapper && headings.some(h => headingLevel(h) <= headingLevel(heading))) break;
        excludedRoots.add(node);
      }
    }
  }
  const excluded = e => [...excludedRoots].some(root => root === e || root.contains(e));
  const labelText = e => {
    const walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); const words = []; let node;
    while ((node = walker.nextNode())) if (!excluded(node.parentElement)) words.push(node.textContent);
    return words.join(' ');
  };
  const safe = e => !['password', 'file', 'hidden'].includes(e.type);
  const visible = e => !!e?.isConnected && !excluded(e) && !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  // Clip against every scrolling ancestor, not just the outer window.
  const clipped = (e, r = e.getBoundingClientRect()) => {
    if (!visible(e) || !r.width || !r.height) return null;
    let left = Math.max(0, r.left), top = Math.max(0, r.top);
    let right = Math.min(innerWidth, r.right), bottom = Math.min(innerHeight, r.bottom);
    for (let p = e.parentElement; p; p = p.parentElement) {
      const css = getComputedStyle(p), pr = p.getBoundingClientRect();
      if (/(auto|scroll|hidden|clip)/.test(css.overflowX)) {
        left = Math.max(left, pr.left); right = Math.min(right, pr.right);
      }
      if (/(auto|scroll|hidden|clip)/.test(css.overflowY)) {
        top = Math.max(top, pr.top); bottom = Math.min(bottom, pr.bottom);
      }
    }
    return right > left && bottom > top ? {left, top, right, bottom} : null;
  };
  const name = (e, seen = new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => name(document.getElementById(id), seen)).filter(Boolean).join(' ');
    return clean(labelled || e.getAttribute('aria-label') ||
      [...(e.labels || [])].map(l => name(l, seen)).join(' ') ||
      (['button', 'submit', 'reset'].includes(e.type) ? e.value : '') ||
      e.getAttribute('alt') || (e.tagName !== 'INPUT' ? labelText(e) : '') ||
      e.getAttribute('title') || e.getAttribute('placeholder'));
  };
  const roles = ['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],' +
    roles.map(r => `[role="${r}"]`).join(',');
  const role = e => {
    if (roles.includes(e.getAttribute('role'))) return e.getAttribute('role');
    if (['BUTTON','SUMMARY'].includes(e.tagName)) return 'button';
    if (e.tagName === 'A') return 'link';
    if (e.tagName === 'SELECT') return 'combobox';
    if (e.tagName === 'TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName === 'INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type === 'search') return 'searchbox';
      if (e.type === 'number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  const profileURL = href => {
    try {
      const u = new URL(href, location.href);
      return /(^|\.)linkedin\.com$/.test(u.hostname) && /^\/in\/[^/]+\/?$/.test(u.pathname)
        ? u.origin + u.pathname.replace(/\/$/, '') : null;
    } catch { return null; }
  };
  const excluded_profile_urls = [...new Set([...excludedRoots].flatMap(root =>
    [...(root.matches('a[href]') ? [root] : []), ...root.querySelectorAll('a[href]')]
      .map(a => profileURL(a.href)).filter(Boolean)))];
  const profileLinks = e => [...e.querySelectorAll('a[href]')]
    .filter(a => clipped(a) && profileURL(a.href));
  const urlsIn = e => new Set(profileLinks(e).map(a => profileURL(a.href)));
  const companyRegions = [];
  if (/^\/company\//.test(location.pathname)) {
    const companyTitle = clean(document.title.split(/\s*[:|]\s*/)[0]).toLowerCase();
    for (const heading of document.querySelectorAll(headingSelector)) {
      if (!(clean(heading.textContent).toLowerCase() === companyTitle || heading.matches('h1'))) continue;
      let region = heading.parentElement;
      for (let parent = region?.parentElement; parent; parent = parent.parentElement) {
        if (parent.matches('main,body,html,[role="main"]')) break;
        const otherHeadings = [...parent.querySelectorAll(headingSelector)].filter(h =>
          h !== heading && headingLevel(h) <= headingLevel(heading) &&
          clean(h.textContent).toLowerCase() !== companyTitle);
        if (otherHeadings.length) break;
        region = parent;
        if (parent.matches('section,article,header')) break;
      }
      if (region && !companyRegions.some(existing => existing.contains(region))) companyRegions.push(region);
    }
  }
  const broadScope = e => e.matches('main,body,html,[role="main"]') ||
    !!e.querySelector('main,[role="main"]') ||
    [...e.querySelectorAll('h1,h2,[role="heading"][aria-level="2"]')].length > 1;
  const scopeFor = e => {
    const overlay = e.closest('dialog,[role="dialog"],[role="menu"]');
    if (overlay) return overlay;
    const company = companyRegions.find(region => region.contains(e));
    if (company) return company;
    const semantic = e.closest('article,li,tr,[role="row"]');
    if (semantic && !broadScope(semantic)) return semantic;
    // Stop at content boundaries. A profile link elsewhere on the page must not
    // make a post, toolbar, or document-level overlay into a person card.
    let candidate = null;
    for (let p = e.parentElement, depth = 0; p && depth < 7; p = p.parentElement, depth++) {
      if (broadScope(p) || companyRegions.some(region => p.contains(region))) break;
      const count = urlsIn(p).size;
      if (count > 1) break;
      if (count === 1) candidate = p;
      if (p.matches('section,form')) break;
    }
    const nearby = e.closest('form,section');
    const fallback = nearby && !broadScope(nearby) && !companyRegions.some(region => nearby.contains(region))
      ? nearby : e.parentElement;
    return candidate || (fallback && !broadScope(fallback) &&
      !companyRegions.some(region => fallback.contains(region)) ? fallback : e);
  };
  const visibleText = (root, budget, omitControlLabels = false) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const range = document.createRange(); const pieces = []; let total = 0, stored = 0, n;
    while ((n = walker.nextNode())) {
      const p = n.parentElement, value = clean(n.textContent);
      if (!value || !p || p.closest('script,style,noscript,template,svg') || !visible(p)) continue;
      if (omitControlLabels && p.closest('button,[role="button"],input,select,textarea,[role="menuitem"]')) continue;
      range.selectNodeContents(n);
      if (![...range.getClientRects()].some(r => clipped(p, r))) continue;
      total += value.length + 1;
      if (stored < budget) { pieces.push(value); stored += value.length + 1; }
    }
    return {text: pieces.join('\n').slice(0, budget), omitted_characters: Math.max(0, total - budget)};
  };
  const scrollable = e => e !== document.scrollingElement && clipped(e) &&
    /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 2;
  const dialogs = [...document.querySelectorAll('dialog[open],[role="dialog"],[role="alertdialog"]')].filter(e => clipped(e));
  const activeDialog = dialogs.at(-1);
  const main = document.querySelector('main,[role="main"]') || document.body;
  const menus = [...document.querySelectorAll('[role="menu"]')].filter(e => clipped(e));
  const roots = [...new Set([activeDialog, ...menus, main, document.body].filter(Boolean))];
  const regions = [...document.querySelectorAll('body *')].filter(scrollable);
  const serial = e => [identity(e), e.scrollTop, e.scrollLeft, e.scrollHeight, e.clientHeight];
  cache.pageKey = () => [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e => [identity(e), e.value, e.checked, e.selectedIndex, e.disabled, e.readOnly]),
    regions.filter(e => e.isConnected).map(serial)];
  cache.guard = e => {
    if (!e?.isConnected || !clipped(e)) return null;
    const scope = scopeFor(e);
    return [identity(e), role(e), name(e), e.value ?? null, e.checked ?? null,
      e.selectedIndex ?? null, e.readOnly ?? null, e.matches(':disabled'),
      e.getAttribute('aria-disabled'), e.getAttribute('aria-expanded'),
      e.getAttribute('aria-checked'), e.getAttribute('aria-selected'), e.getAttribute('href'),
      scope?.innerText || '', [...(scope?.querySelectorAll('a[href]') || [])].map(a => a.getAttribute('href')),
      e.scrollTop, e.scrollLeft, e.scrollHeight, e.clientHeight];
  };
  function companyHeader(scope) {
    return companyRegions.includes(scope);
  }
  const groups = [], groupMap = new Map();
  const groupFor = e => {
    const scope = scopeFor(e);
    if (!groupMap.has(scope)) {
      const content = visibleText(scope, limits.groupText, true);
      const group = {id: `g${groups.length + 1}`, type: scope.matches('dialog,[role="dialog"],[role="alertdialog"]') ? 'dialog' :
        scope.matches('[role="menu"]') ? 'menu' : companyHeader(scope) ? 'company' :
        urlsIn(scope).size === 1 ? 'person' : 'context',
        text: content.text, omitted_characters: content.omitted_characters,
        profile_urls: [...urlsIn(scope)], controls: []};
      group.kind = group.type;
      groups.push(group); groupMap.set(scope, group);
    }
    return groupMap.get(scope);
  };
  const elements = [...new Set(roots.flatMap(root => [...root.querySelectorAll(selector)]))];
  const actions = []; let omittedActions = 0;
  for (const e of elements) {
    if (!safe(e) || !clipped(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    if (activeDialog && !activeDialog.contains(e) && !menus.some(m => m.contains(e))) continue;
    const r = e.getBoundingClientRect(), clip = clipped(e), rname = role(e);
    if (!rname || (rname === 'gridcell' && e.querySelector('button,[role="button"]'))) continue;
    // Require an exposed point; the executor rechecks geometry before touching the page.
    const hit = document.elementFromPoint((clip.left + clip.right) / 2, (clip.top + clip.bottom) / 2);
    if (!hit || !(hit === e || e.contains(hit))) continue;
    const group = groupFor(e);
    const base = {node: identity(e), role: rname, label: name(e) || rname,
      href: e.getAttribute('href') ? e.href : null, context: group.id,
      rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value = e.getAttribute('aria-' + key); if (value !== null) base[key] = value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked = String(e.checked);
    const add = action => {
      if (actions.length >= limits.actions) { omittedActions++; return; }
      action.id = `e${actions.length + 1}`; actions.push(action); group.controls.push(action.id);
    };
    if (e.tagName === 'SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        add({...base, kind: 'select', value: o.value,
          current_value: [...e.selectedOptions].map(o => o.label).join(', '), label: `${base.label} → ${o.label}`});
    } else {
      const editable = !e.readOnly && e.getAttribute('aria-readonly') !== 'true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
        (rname === 'combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value = 'value' in e ? String(e.value) : e.isContentEditable ? e.innerText : '';
      add({...base, kind: editable ? 'fill' : 'click', value});
      if (editable) add({...base, kind: 'click', value, label: 'Open ' + base.label});
    }
  }
  const state = (...scopes) => {
    const labels = scopes.flatMap(scope => [...scope.querySelectorAll(selector)])
      .filter(e => clipped(e)).map(e => name(e));
    // Missing controls are unknown, never proof of an absent relationship.
    const has = pattern => labels.some(label => pattern.test(label));
    return {pending: has(/^pending(?:\b|$)/i) ? true : has(/^connect(?:\b|$)/i) ? false : null,
      following: has(/^(following|unfollow)(?:\b|$)/i) ? true : has(/^follow(?:\b|$)/i) ? false : null,
      connected: has(/^remove connection$/i) ? true : has(/^(connect|pending)(?:\b|$)/i) ? false : null};
  };
  const people = [];
  for (const [scope, group] of groupMap) if (group.type === 'person') {
    const a = profileLinks(scope)[0];
    people.push({url: group.profile_urls[0], name: name(a), headline: group.text,
      evidence: group.text, group: group.id, ...state(scope)});
  }
  let profile = null;
  const url = profileURL(location.href), h1 = [...main.querySelectorAll('h1')].find(e => clipped(e));
  if (url && h1) {
    // Use the header's own section to avoid inferring status from recommendation cards.
    const header = h1.closest('section,article,header') || h1.parentElement;
    const evidence = visibleText(header, limits.groupText, true);
    const ownedIds = new Set([...header.querySelectorAll('[aria-controls],[aria-owns]')]
      .filter(e => clipped(e)).flatMap(e =>
        [e.getAttribute('aria-controls'), e.getAttribute('aria-owns')].join(' ').split(/\s+/)));
    const ownedMenus = menus.filter(menu => menu.id && ownedIds.has(menu.id));
    profile = {url, name: name(h1), headline: evidence.text, evidence: evidence.text,
      omitted_characters: evidence.omitted_characters,
      associated_menu_evidence: ownedMenus.map(menu => visibleText(menu, limits.groupText).text),
      ...state(header, ...ownedMenus)};
  }
  const notices = [...document.querySelectorAll('[role="alert"],[role="status"],[aria-live]:not([aria-live="off"])')]
    .filter(e => clipped(e)).map(e => visibleText(e, 2000).text).filter(Boolean);
  const scroll_regions = [];
  const addScroll = (id, node, y, height, viewport, rect, label) => {
    const delta = Math.max(1, Math.floor(viewport * 0.67));
    scroll_regions.push({id, node, y, height, viewport, rect, label});
    const base = {kind: 'scroll', ...(node ? {node, rect} : {})};
    if (y + viewport < height - 2) actions.push({...base, id: `${id}_down`, label: `Scroll down: ${label}`, delta});
    if (y > 0) actions.push({...base, id: `${id}_up`, label: `Scroll up: ${label}`, delta: -delta});
  };
  if (!activeDialog) addScroll('scroll', null, scrollY, document.documentElement.scrollHeight, innerHeight, null, 'page');
  for (const e of regions) {
    if (activeDialog && !activeDialog.contains(e) && e !== activeDialog) continue;
    const r = e.getBoundingClientRect(), node = identity(e);
    addScroll(`scroll_${node}`, node, e.scrollTop, e.scrollHeight, e.clientHeight,
      {x: r.x, y: r.y, w: r.width, h: r.height}, name(e).slice(0, 100) || 'scrollable region');
  }
  actions.push({id: 'wait', kind: 'wait', label: 'Wait for the page to update'});
  const textParts = [], seenText = new Set(); let textBudget = limits.text, omittedText = 0;
  for (const root of roots) {
    const chunk = visibleText(root, limits.text);
    const unique = chunk.text.split('\n').filter(line => !seenText.has(line) && seenText.add(line)).join('\n');
    textParts.push(unique.slice(0, textBudget));
    omittedText += Math.max(0, unique.length - textBudget) + chunk.omitted_characters;
    textBudget = Math.max(0, textBudget - unique.length);
  }
  const text = textParts.filter(Boolean).join('\n'), page_key = cache.pageKey(), guards = {};
  for (const a of actions) if (a.node && !(a.node in guards)) guards[a.node] = cache.guard(cache.nodes.get(a.node));
  const semantics = actions.map(({rect, ...action}) => action);
  const marker = [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    document.title, text, semantics, page_key[6], page_key[7], groups, people, profile, notices];
  return {url: location.href, title: document.title, w: innerWidth, h: innerHeight, text,
    scroll: {y: scrollY, height: document.documentElement.scrollHeight}, actions, marker, page_key, guards,
    groups, people, profile, notices, scroll_regions, excluded_profile_urls, omitted_actions: omittedActions,
    truncation: {omitted_actions: omittedActions, omitted_text_characters: omittedText,
      groups_with_omitted_text: groups.filter(g => g.omitted_characters > 0).map(g => g.id), limits}};
})()
