/* EduMark v0.7.0 — nạp NGAY SAU Tailwind CDN (01-CONTRACTS §1).
   1) Bảng màu Tailwind qua biến CSS (--tw-<họ>-<sắc>, định nghĩa trong theme.css)
   2) Áp theme Sáng/Tối từ localStorage['theme'] trước khi trang vẽ (tránh nháy). */
(function () {
  var FAMILIES = ['slate', 'indigo', 'sky', 'cyan', 'teal', 'emerald', 'amber', 'rose', 'violet', 'purple'];
  var SHADES = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
  var colors = {};
  FAMILIES.forEach(function (f) {
    colors[f] = {};
    SHADES.forEach(function (s) { colors[f][s] = 'rgb(var(--tw-' + f + '-' + s + ') / <alpha-value>)'; });
  });
  // Giữ alias `brand` của v0.6 (trỏ về họ indigo = blue hành động).
  colors.brand = { 50: colors.indigo['50'], 500: colors.indigo['500'], 600: colors.indigo['600'], 700: colors.indigo['700'] };

  if (window.tailwind) {
    window.tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: colors,
          fontFamily: {
            sans: ['"Be Vietnam Pro"', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
            mono: ['"JetBrains Mono"', 'ui-monospace', 'Consolas', 'monospace'],
          },
        },
      },
    };
  }

  var theme = 'light';
  try { theme = localStorage.getItem('theme') === 'dark' ? 'dark' : 'light'; } catch (e) { theme = 'light'; }
  document.documentElement.classList.toggle('dark', theme === 'dark');

  // Dùng chung cho cả Desktop & Mobile.
  window.emSetTheme = function (next) {
    var isDark = next === 'dark';
    document.documentElement.classList.toggle('dark', isDark);
    try { localStorage.setItem('theme', isDark ? 'dark' : 'light'); } catch (e) { /* private mode: bỏ qua */ }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', isDark ? '#0f172a' : '#DCE4EC');
    return isDark;
  };
  window.emIsDark = function () { return document.documentElement.classList.contains('dark'); };
})();
