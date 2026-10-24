// Runs the page's inline script in a tiny fake DOM for many user agents and asserts the behaviour.
// usage: node tests/test_js.js <path-to-index.html>
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];
const HAPP = /href="(happ:\/\/routing\/onadd\/[^"]+)"/.exec(html)[1];
const INCY = /href="(incy:\/\/routing\/onadd\/[^"]+)"/.exec(html)[1];

function run({ ua, touch = 0, path, session = {} }) {
  const out = { platform: null, navigated: null, focused: null, opened: null };
  const mkEl = (attrs = {}) => ({
    dataset: attrs, _attrs: { ...attrs }, classList: { toggle(c, on) { if (c === 'on' && on) out.platform = attrs.p; }, add(c) { if (c === 'focus') out.focused = attrs.id; } },
    getAttribute(k) { return this._attrs[k] ?? null; }, setAttribute(k, v) { this._attrs[k] = v; }, addEventListener() {},
  });
  const dl = ['ios', 'android', 'windows', 'macos'].map(p => mkEl({ p, 'data-p': p }));
  const chips = ['ios', 'android', 'windows', 'macos'].map(p => mkEl({ p, 'data-p': p }));
  const autoEls = { happ: Object.assign(mkEl({}), { href: HAPP }), incy: Object.assign(mkEl({}), { href: INCY }) };
  const m = path.replace(/^\/+|\/+$/g, '');
  const store = { ...session };
  const ctx = {
    document: {
      documentElement: { className: '', _a: {}, setAttribute(k, v) { this._a[k] = v; } },
      querySelectorAll: s => (s === '.dl' ? dl : s === '.chip' ? chips : []),
      querySelector: s => { const k = /data-auto="(\w+)"/.exec(s); return k ? autoEls[k[1]] : null; },
      getElementById: id => {
        if (id === 'copy-url') return null;
        if (id.startsWith('c-') && ['hiddify', 'happ', 'incy'].includes(id.slice(2))) return Object.assign(mkEl({ id }), { scrollIntoView() {} });
        if (id.startsWith('s-')) return { open: false };
        return null;
      },
      createElement: () => ({}), body: { appendChild() {}, removeChild() {} },
    },
    navigator: { userAgent: ua, maxTouchPoints: touch },
    location: { pathname: path, get href() { return 'https://x' + path; }, set href(v) { out.navigated = v; } },
    sessionStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    setTimeout: fn => { fn(); return 0; },
  };
  vm.runInNewContext(script, ctx);
  out.opened = out.navigated === HAPP ? 'happ' : out.navigated === INCY ? 'incy' : null;
  out.dataPlat = ctx.document.documentElement._a['data-plat'] || null;
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

t('iPhone Safari /happ -> ios, auto-opens happ', pick(run({ ua: UA.iphoneSafari, path: '/happ' }), 'platform', 'opened'), { platform: 'ios', opened: 'happ' });
t('iPhone Chrome /incy -> ios, auto-opens incy', pick(run({ ua: UA.iphoneChrome, path: '/incy' }), 'platform', 'opened'), { platform: 'ios', opened: 'incy' });
t('iOS in-app webview (no Safari token) /happ -> NO auto-open', pick(run({ ua: UA.iphoneWebview, path: '/happ' }), 'platform', 'opened'), { platform: 'ios', opened: null });
t('iOS UA with Telegram token /happ -> NO auto-open', run({ ua: UA.iphoneTelegramUA, path: '/happ' }).opened, null);
t('iPadOS "desktop" Safari with touch -> ios', run({ ua: UA.ipadDesktopSafari, touch: 5, path: '/' }).platform, 'ios');
t('Android Chrome /happ -> android, auto-opens happ', pick(run({ ua: UA.androidChrome, path: '/happ' }), 'platform', 'opened'), { platform: 'android', opened: 'happ' });
t('Android WebView (; wv) /incy -> NO auto-open', pick(run({ ua: UA.androidWebview, path: '/incy' }), 'platform', 'opened'), { platform: 'android', opened: null });
t('Windows Chrome / -> windows, no auto-open on root', pick(run({ ua: UA.winChrome, path: '/' }), 'platform', 'opened'), { platform: 'windows', opened: null });
t('Windows Chrome /incy -> auto-opens incy', run({ ua: UA.winChrome, path: '/incy' }).opened, 'incy');
t('Mac Safari (no touch) -> macos', run({ ua: UA.macSafari, path: '/' }).platform, 'macos');
t('Linux Firefox -> falls back to windows', run({ ua: UA.linuxFirefox, path: '/' }).platform, 'windows');
t('/hiddify focuses hiddify card, never auto-opens', pick(run({ ua: UA.androidChrome, path: '/hiddify' }), 'focused', 'opened'), { focused: 'c-hiddify', opened: null });
t('trailing slash /happ/ still focuses + opens', pick(run({ ua: UA.androidChrome, path: '/happ/' }), 'focused', 'opened'), { focused: 'c-happ', opened: 'happ' });
t('second visit in same session -> no repeated auto-open', run({ ua: UA.androidChrome, path: '/happ', session: { 'ro-happ': '1' } }).opened, null);
t('unknown path /xyz -> no focus, no auto-open', pick(run({ ua: UA.androidChrome, path: '/xyz' }), 'focused', 'opened'), { focused: null, opened: null });

// the iOS-only region help is shown through <html data-plat="ios"> (see CSS .js[data-plat="ios"] .ios-only)
t('iPhone -> data-plat=ios (region help becomes visible)', run({ ua: UA.iphoneSafari, path: '/' }).dataPlat, 'ios');
t('iPad desktop-UA with touch -> data-plat=ios', run({ ua: UA.ipadDesktopSafari, touch: 5, path: '/' }).dataPlat, 'ios');
t('Android -> data-plat=android (region help stays hidden)', run({ ua: UA.androidChrome, path: '/' }).dataPlat, 'android');
t('Windows -> data-plat=windows', run({ ua: UA.winChrome, path: '/' }).dataPlat, 'windows');
t('the page CSS hides .ios-only unless data-plat=ios (and shows it without JS)', [/\.ios-only\{display:block\}/.test(html), /\.js \.ios-only\{display:none\}/.test(html), /\.js\[data-plat="ios"\] \.ios-only\{display:block\}/.test(html)], [true, true, true]);

console.log(`JS RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
