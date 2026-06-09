// ─── External link handler ───────────────────────────────────────────────────
// Open all external-origin links in the system browser instead of inside the app.
document.addEventListener('click', function (e) {
  const link = e.target.closest('a[href]');
  if (!link) return;

  const href = link.getAttribute('href');
  if (!href || href.startsWith('#') || href.startsWith('javascript')) return;

  try {
    const url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin) {
      e.preventDefault();
      e.stopPropagation();
      openExternal(url.href);
    }
  } catch (_) {}
}, true);

function openExternal(url) {
  if (window.__TAURI__?.shell) {
    window.__TAURI__.shell.open(url);
  } else if (window.__TAURI__?.opener) {
    window.__TAURI__.opener.openUrl(url);
  } else {
    window.open(url, '_blank');
  }
}

// ─── Update checker ──────────────────────────────────────────────────────────
// __APP_VERSION__ is replaced at build time by the release workflow. When this
// app is built locally (not via CI), the placeholder is left in place and the
// updater is a no-op.
const CURRENT_VERSION = '__APP_VERSION__';
const REPO = 'Artechsolutions-arts/virchow_rag';
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const RELEASES_API = `https://api.github.com/repos/${REPO}/releases/latest`;
const CHECK_INTERVAL_MS = 60 * 60 * 1000; // hourly

function isNewerVersion(latest, current) {
  const parse = (v) => v.replace(/^v/, '').split('.').map((n) => parseInt(n, 10) || 0);
  try {
    const l = parse(latest);
    const c = parse(current);
    for (let i = 0; i < Math.max(l.length, c.length); i++) {
      const li = l[i] || 0;
      const ci = c[i] || 0;
      if (li !== ci) return li > ci;
    }
    return false;
  } catch (_) {
    return false;
  }
}

function showUpdateBanner(latestVersion) {
  if (document.getElementById('vw-update-banner')) return;
  if (localStorage.getItem('vw-update-dismissed') === latestVersion) return;
  if (!document.body) {
    document.addEventListener('DOMContentLoaded', () => showUpdateBanner(latestVersion), { once: true });
    return;
  }

  const youHave =
    CURRENT_VERSION === '__APP_VERSION__' || !CURRENT_VERSION
      ? 'local build'
      : CURRENT_VERSION;

  const banner = document.createElement('div');
  banner.id = 'vw-update-banner';
  banner.style.cssText = [
    'position:fixed', 'top:0', 'left:0', 'right:0',
    'z-index:2147483647',
    'background:linear-gradient(90deg,#0ea5e9,#6366f1)',
    'color:#fff',
    'padding:10px 16px',
    'display:flex', 'align-items:center', 'justify-content:center', 'gap:12px',
    'font-size:13px',
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif',
    'box-shadow:0 2px 8px rgba(0,0,0,0.15)'
  ].join(';');
  banner.innerHTML = `
    <span>A new version of Virchows Wiki is available: <strong>${latestVersion}</strong> (you have ${youHave})</span>
    <button id="vw-update-download" style="background:#fff;color:#0ea5e9;border:none;padding:6px 14px;border-radius:6px;font-weight:600;cursor:pointer;font-size:12px;">Download</button>
    <button id="vw-update-dismiss" style="background:transparent;color:#fff;border:1px solid rgba(255,255,255,0.4);padding:6px 10px;border-radius:6px;cursor:pointer;font-size:12px;">Later</button>
  `;
  document.body.appendChild(banner);

  document.getElementById('vw-update-download').addEventListener('click', () => {
    openExternal(RELEASES_PAGE);
  });
  document.getElementById('vw-update-dismiss').addEventListener('click', () => {
    localStorage.setItem('vw-update-dismissed', latestVersion);
    banner.remove();
  });
}

async function checkForUpdates() {
  // Locally-built DMGs leave CURRENT_VERSION as the literal placeholder.
  // Treat those as "unversioned" — the comparator already returns true
  // for any real tag vs the placeholder, so the banner shows and prompts
  // the user to install a properly CI-stamped release.
  if (!CURRENT_VERSION) return;
  try {
    const res = await fetch(RELEASES_API, {
      headers: { 'Accept': 'application/vnd.github+json' },
      cache: 'no-store'
    });
    if (!res.ok) return;
    const data = await res.json();
    const latest = data.tag_name;
    if (latest && isNewerVersion(latest, CURRENT_VERSION)) {
      showUpdateBanner(latest);
    }
  } catch (_) {}
}

setTimeout(checkForUpdates, 3000);
setInterval(checkForUpdates, CHECK_INTERVAL_MS);

// ─── Live auto-reload of the web app ──────────────────────────────────────────
// The deploy workflow writes the current git commit to /build-version.txt on
// every push. We poll it every 30s; if it changes from the value we observed
// at app start, we briefly show a toast and reload the WebView so the user
// sees new features without having to quit and reopen the app.
const BUILD_VERSION_URL = `${window.location.origin}/build-version.txt`;
const VERSION_POLL_MS = 30 * 1000;
let _firstSeenVersion = null;

async function _fetchBuildVersion() {
  try {
    const res = await fetch(BUILD_VERSION_URL, { cache: 'no-store' });
    if (!res.ok) return null;
    const text = (await res.text()).trim();
    return text || null;
  } catch (_) {
    return null;
  }
}

function _showReloadToast() {
  if (document.getElementById('vw-reload-toast')) return;
  const toast = document.createElement('div');
  toast.id = 'vw-reload-toast';
  toast.style.cssText = [
    'position:fixed', 'bottom:20px', 'left:50%', 'transform:translateX(-50%)',
    'z-index:2147483647',
    'background:#0f172a', 'color:#fff',
    'padding:10px 16px', 'border-radius:10px',
    'box-shadow:0 8px 24px rgba(0,0,0,0.25)',
    'font-size:13px',
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif'
  ].join(';');
  toast.textContent = 'New version detected — reloading...';
  if (document.body) document.body.appendChild(toast);
}

async function pollForLiveReload() {
  const current = await _fetchBuildVersion();
  if (!current) return;
  if (_firstSeenVersion === null) {
    _firstSeenVersion = current;
    return;
  }
  if (current !== _firstSeenVersion) {
    _showReloadToast();
    setTimeout(() => window.location.reload(), 1500);
  }
}

setTimeout(pollForLiveReload, 5000);
setInterval(pollForLiveReload, VERSION_POLL_MS);
