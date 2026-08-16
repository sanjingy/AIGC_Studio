/**
 * 对比度复测脚本 —— 粘进浏览器控制台运行，或用无头浏览器执行。
 *
 * 为什么需要它：`getComputedStyle` 现在原样返回 `oklch(...)` 字符串，
 * 拿正则当 RGB 解析会得到完全错误的结果（实际测出过"正文对比度 1.1"这种
 * 荒谬数字）。必须让浏览器自己解析——canvas fillStyle + getImageData。
 *
 * 改动任何颜色令牌后都要重跑，输出里不能出现 ✗。
 */
(() => {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 1;
  const ctx = cv.getContext("2d", { willReadFrequently: true });

  const rgb = (color) => {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, 1, 1);
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2]];
  };
  const lin = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const lum = (p) => 0.2126 * lin(p[0]) + 0.7152 * lin(p[1]) + 0.0722 * lin(p[2]);
  const ratio = (a, b) => {
    const v = [lum(rgb(a)), lum(rgb(b))].sort((x, y) => y - x);
    return +((v[0] + 0.05) / (v[1] + 0.05)).toFixed(2);
  };

  const MIN = 4.5; // WCAG AA 小号文字

  function probe() {
    const s = getComputedStyle(document.documentElement);
    const t = (n) => s.getPropertyValue(n).trim();
    const bg = t("--bg");
    const su = t("--surface");
    const s2 = t("--surface-2");
    const out = {};
    let failed = 0;
    const add = (label, fg, back) => {
      const v = ratio(fg, back);
      if (v < MIN) failed++;
      out[label] = v < MIN ? `${v} ✗` : v;
    };

    add("正文/底", t("--fg"), bg);
    add("muted/面", t("--fg-muted"), su);
    add("subtle/面", t("--fg-subtle"), su);
    add("subtle/面2", t("--fg-subtle"), s2);
    add("primary/面", t("--primary"), su);
    add("running/面", t("--running"), su);
    add("success/面", t("--success"), su);
    add("danger/面", t("--danger"), su);
    add("primary/软", t("--primary"), t("--primary-soft"));
    add("running/软", t("--running"), t("--running-soft"));
    add("success/软", t("--success"), t("--success-soft"));
    add("danger/软", t("--danger"), t("--danger-soft"));
    add("主按钮字/底", t("--primary-fg"), t("--primary"));

    return { out, failed };
  }

  const root = document.documentElement;
  const wasDark = root.classList.contains("dark");

  root.classList.remove("dark");
  const light = probe();
  root.classList.add("dark");
  const dark = probe();
  root.classList.toggle("dark", wasDark);

  const total = light.failed + dark.failed;
  console.table({ 亮色: light.out, 暗色: dark.out });
  console.log(total === 0 ? "✅ 全部达标" : `❌ ${total} 项低于 ${MIN}:1`);
  return { light: light.out, dark: dark.out, failed: total };
})();
