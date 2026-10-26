// Runs the page's inline script in a tiny fake DOM for many user agents and asserts the behaviour.
// usage: node tests/test_js.js <path-to-index.html>
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];
const ORDER = /data-order="([^"]*)"/.exec(html)[1];
const ORDER_IOS = /data-order-ios="([^"]*)"/.exec(html)[1];
const PLATS = ['ios', 'android', 'windows', 'macos'];
const APPS = ['hiddify', 'happ', 'incy'];

function run({ ua, touch = 0, path, hash = '' }) {
  const out = { platform: null, navigated: null, focused: null, regionForced: false, order: {}, badges: {}, rec: null, detailsOpened: null };
  const mkEl = (attrs = {}) => ({
    _attrs: { ...attrs }, getAttribute(k) { return this._attrs[k] ?? null; }, setAttribute(k, v) { this._attrs[k] = v; }, addEventListener() {},
  });
  const dl = PLATS.map(p => Object.assign(mkEl({ 'data-p': p }), { classList: { toggle(c, on) { if (c === 'on' && on) out.platform = p; } } }));
  const chips = PLATS.map(p => mkEl({ 'data-p': p }));
  const recs = ['ios', 'other'].map(k => Object.assign(mkEl({ 'data-rec': k }), { classList: { toggle(c, on) { if (c === 'on' && on) out.rec = k; } } }));
  const apps = mkEl({ 'data-order': ORDER, 'data-order-ios': ORDER_IOS });
  const card = id => {
    const badge = { textContent: 'static' };
    out.badges[id] = badge;
    return Object.assign(mkEl({ id }), { style: { set order(v) { out.order[id] = v; } }, classList: { add(c) { if (c === 'focus') out.focused = id; } }, querySelector: s => (s === '.badge' ? badge : null), scrollIntoView() {} });
  };
  const cards = Object.fromEntries(APPS.map(a => ['c-' + a, card(a)]));
  const region = { classList: { add(c) { if (c === 'force') out.regionForced = true; } }, scrollIntoView() {} };
  const m = path.replace(/^\/+|\/+$/g, '');
  const ctx = {
    document: {
      documentElement: { className: '', _a: {}, setAttribute(k, v) { this._a[k] = v; } },
      querySelectorAll: s => (s === '.dl' ? dl : s === '.chip' ? chips : s === '.rec' ? recs : []),
      querySelector: () => null,
      getElementById: id => {
        if (id === 'copy-url') return null;
        if (id === 'apps') return apps;
        if (id === 'region') return region;
        if (cards[id]) return cards[id];
        if (id.startsWith('s-') && APPS.includes(id.slice(2))) { const d = { open: false }; Object.defineProperty(d, 'open', { set(v) { out.detailsOpened = id; }, get() { return false; } }); return d; }
        return null;
      },
      createElement: () => ({}), body: { appendChild() {}, removeChild() {} },
    },
    navigator: { userAgent: ua, maxTouchPoints: touch },
    location: { pathname: path, hash, get href() { return 'https://x' + path; }, set href(v) { out.navigated = v; } },
    sessionStorage: { getItem: () => null, setItem() {} },
    setTimeout: fn => { fn(); return 0; },
  };
  vm.runInNewContext(script, ctx);
  void m;
  out.dataPlat = ctx.document.documentElement._a['data-plat'] || null;
  out.badgeText = Object.fromEntries(Object.entries(out.badges).map(([k, v]) => [k, v.textContent]));
  return out;
}

const UA = {
  iphoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  iphoneChrome: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.0.0 Mobile/15E148 Safari/604.1',
  iphoneWebview: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
  iphoneTelegramUA: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Telegram/10.0',
  ipadDesktopSafari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  androidChrome: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
  androidWebview: 'Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.0.0 Mobile Safari/537.36',
  winChrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  macSafari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  linuxFirefox: 'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0',
};

let pass = 0, fail = 0;
function t(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  got=${JSON.stringify(got)} want=${JSON.stringify(want)}`}`);
}
const pick = (r, ...k) => Object.fromEntries(k.map(x => [x, r[x]]));
const first = r => Object.entries(r.order).sort((x, y) => x[1] - y[1])[0][0];

t('iPhone Safari -> ios', run({ ua: UA.iphoneSafari, path: '/' }).platform, 'ios');
t('iPhone Chrome -> ios', run({ ua: UA.iphoneChrome, path: '/' }).platform, 'ios');
t('iOS in-app webview -> ios', run({ ua: UA.iphoneWebview, path: '/' }).platform, 'ios');
t('iOS UA with Telegram token -> ios', run({ ua: UA.iphoneTelegramUA, path: '/' }).platform, 'ios');
t('iPadOS "desktop" Safari with touch -> ios', run({ ua: UA.ipadDesktopSafari, touch: 5, path: '/' }).platform, 'ios');
t('Android Chrome -> android', run({ ua: UA.androidChrome, path: '/' }).platform, 'android');
t('Android WebView -> android', run({ ua: UA.androidWebview, path: '/' }).platform, 'android');
t('Windows Chrome -> windows', run({ ua: UA.winChrome, path: '/' }).platform, 'windows');
t('Mac Safari (no touch) -> macos', run({ ua: UA.macSafari, path: '/' }).platform, 'macos');
t('Linux Firefox -> falls back to windows', run({ ua: UA.linuxFirefox, path: '/' }).platform, 'windows');

// one instruction as in the cabinet: INCY first on the iPhone, Hiddify first on the other platforms; the "Рекомендуем" badge follows the first app
t('iPhone: INCY goes first and carries the badge', [first(run({ ua: UA.iphoneSafari, path: '/' })), run({ ua: UA.iphoneSafari, path: '/' }).badgeText], ['incy', { hiddify: '', happ: '', incy: 'Рекомендуем' }]);
t('Android: Hiddify goes first and carries the badge', [first(run({ ua: UA.androidChrome, path: '/' })), run({ ua: UA.androidChrome, path: '/' }).badgeText], ['hiddify', { hiddify: 'Рекомендуем', happ: '', incy: '' }]);
t('Windows: Hiddify first', first(run({ ua: UA.winChrome, path: '/' })), 'hiddify');
t('iPhone shows the iOS recommendation, the others the general one', [run({ ua: UA.iphoneSafari, path: '/' }).rec, run({ ua: UA.macSafari, path: '/' }).rec], ['ios', 'other']);

// short links only scroll to the card of the app and open its steps: nothing opens an app or navigates away
for (const a of APPS) {
  const r = run({ ua: UA.androidChrome, path: '/' + a });
  t(`/${a} focuses the card and opens its steps, never navigates`, pick(r, 'focused', 'detailsOpened', 'navigated'), { focused: a, detailsOpened: 's-' + a, navigated: null });
}
t('trailing slash /happ/ still focuses', run({ ua: UA.androidChrome, path: '/happ/' }).focused, 'happ');
t('unknown path /xyz -> no focus', pick(run({ ua: UA.androidChrome, path: '/xyz' }), 'focused', 'navigated'), { focused: null, navigated: null });
t('the root page never navigates', run({ ua: UA.iphoneSafari, path: '/' }).navigated, null);

// the full region instruction (linked from the cabinet and the bot) opens by #region on every platform
t('#region on Android forces the region help open', run({ ua: UA.androidChrome, path: '/', hash: '#region' }).regionForced, true);
t('#region on Windows forces the region help open', run({ ua: UA.winChrome, path: '/', hash: '#region' }).regionForced, true);
t('no hash: the region help is not forced', run({ ua: UA.androidChrome, path: '/' }).regionForced, false);

// the iOS-only region help is shown through <html data-plat="ios"> (see CSS .js[data-plat="ios"] .ios-only)
t('iPhone -> data-plat=ios (region help becomes visible)', run({ ua: UA.iphoneSafari, path: '/' }).dataPlat, 'ios');
t('iPad desktop-UA with touch -> data-plat=ios', run({ ua: UA.ipadDesktopSafari, touch: 5, path: '/' }).dataPlat, 'ios');
t('Android -> data-plat=android (region help stays hidden)', run({ ua: UA.androidChrome, path: '/' }).dataPlat, 'android');
t('Windows -> data-plat=windows', run({ ua: UA.winChrome, path: '/' }).dataPlat, 'windows');
t('the page CSS hides .ios-only unless data-plat=ios or forced (and shows it without JS)', [/\.ios-only\{display:block\}/.test(html), /\.js \.ios-only\{display:none\}/.test(html), /\.js\[data-plat="ios"\] \.ios-only\{display:block\}/.test(html), /\.js \.ios-only\.force\{display:block\}/.test(html)], [true, true, true, true]);

console.log(`JS RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
