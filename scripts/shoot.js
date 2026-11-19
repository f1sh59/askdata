// 用 puppeteer-core 直连本机 Edge，按精确 CSS 视口截图并测量溢出。
// Edge 的 --window-size 在 Windows 上有最小宽度（约 496px），
// 所以 390 这种手机宽度只能靠自动化协议设定。
const puppeteer = require("puppeteer-core");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

async function shoot({ width, height, out, url, mobile }) {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--disable-gpu", "--hide-scrollbars", "--no-first-run"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width, height, deviceScaleFactor: 1, isMobile: !!mobile, hasTouch: !!mobile });
    await page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
    await new Promise((r) => setTimeout(r, 1200));

    const report = await page.evaluate(() => {
      const root = document.documentElement;
      const vw = root.clientWidth;
      const sw = Math.max(root.scrollWidth, document.body.scrollWidth);
      const offenders = [];
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && Math.round(r.right) > vw + 1) {
          const cls = String(el.className || "").split(" ")[0];
          offenders.push(`${el.tagName.toLowerCase()}${cls ? "." + cls : ""}→${Math.round(r.right)}`);
        }
      }
      return { vw, sw, offenders: offenders.slice(0, 12), total: offenders.length };
    });

    await page.screenshot({ path: out });
    return report;
  } finally {
    await browser.close();
  }
}

(async () => {
  const url = process.argv[2] || "http://127.0.0.1:8777/";
  const specs = [
    { width: 390, height: 844, out: "mobile-390.png", mobile: true, label: "iPhone 12/13/14" },
    { width: 360, height: 800, out: "mobile-360.png", mobile: true, label: "安卓常见宽度" },
    { width: 768, height: 1024, out: "tablet-768.png", mobile: true, label: "iPad 竖屏" },
  ];

  for (const spec of specs) {
    const r = await shoot({ ...spec, url });
    const ok = r.sw <= r.vw + 1;
    console.log(
      `${spec.label.padEnd(16)} ${String(spec.width).padStart(3)}px  视口 ${r.vw}  scrollWidth ${r.sw}  ` +
        `${ok ? "无横向溢出" : "溢出 " + (r.sw - r.vw) + "px"}  越界元素 ${r.total}  → ${spec.out}`
    );
    if (r.offenders.length) console.log("   越界: " + r.offenders.join(", "));
  }
})();
