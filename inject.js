// Open all external links in the system browser instead of inside the app
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
      if (window.__TAURI__?.shell) {
        window.__TAURI__.shell.open(url.href);
      } else if (window.__TAURI__?.opener) {
        window.__TAURI__.opener.openUrl(url.href);
      } else {
        window.open(url.href, '_blank');
      }
    }
  } catch (_) {}
}, true);
