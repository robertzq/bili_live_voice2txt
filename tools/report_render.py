"""把 Markdown 复盘报告渲染成适合发给主播的图片。

优先使用 Playwright + Chromium/Chrome 截图；Mermaid 代码块会被真正渲染成图。
Mermaid 脚本会缓存到 tools/.cache/，离线时复用缓存。
"""

from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CACHE_DIR = TOOLS_DIR / ".cache"
MERMAID_CACHE = CACHE_DIR / "mermaid.min.js"
MERMAID_URLS = (
    "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",
    "https://unpkg.com/mermaid@11/dist/mermaid.min.js",
)

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)

MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(.*?)```", re.S)


class RenderError(RuntimeError):
    pass


def _ensure_mermaid_js() -> str | None:
    if MERMAID_CACHE.is_file() and MERMAID_CACHE.stat().st_size > 100_000:
        try:
            return MERMAID_CACHE.read_text(encoding="utf-8")
        except OSError:
            pass

    try:
        import requests
    except ImportError:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url in MERMAID_URLS:
        try:
            response = requests.get(url, timeout=20)
        except requests.RequestException:
            continue
        if response.ok and len(response.content) > 100_000:
            try:
                MERMAID_CACHE.write_bytes(response.content)
            except OSError:
                pass
            return response.content.decode("utf-8")
    return None


def _extract_mermaid(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def repl(match: re.Match[str]) -> str:
        blocks.append(match.group(1).strip("\n"))
        return f"\n\nMERMAIDPLACEHOLDER{len(blocks) - 1}\n\n"

    return MERMAID_BLOCK.sub(repl, text), blocks


def _markdown_to_html(text: str) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise RenderError(
            "缺少 markdown 依赖，请运行: pip install -r requirements-tools.txt"
        ) from exc

    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )


def _inject_mermaid(body_html: str, blocks: list[str]) -> str:
    for index, code in enumerate(blocks):
        node = f'<div class="mermaid-wrap"><pre class="mermaid">{html.escape(code)}</pre></div>'
        body_html = re.sub(
            rf"<p>\s*MERMAIDPLACEHOLDER{index}\s*</p>",
            node,
            body_html,
        )
        body_html = body_html.replace(f"MERMAIDPLACEHOLDER{index}", node)
    return body_html


CSS = """
:root {
  --ink: #1e293b;
  --muted: #64748b;
  --line: #e2e8f0;
  --accent: #6366f1;
  --accent-2: #06b6d4;
  --soft: #f8fafc;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink);
  background:
    radial-gradient(circle at 12% 8%, rgba(99, 102, 241, 0.16), transparent 34%),
    radial-gradient(circle at 88% 4%, rgba(6, 182, 212, 0.14), transparent 30%),
    linear-gradient(180deg, #eef2ff 0%, #f1f5f9 46%, #e2e8f0 100%);
  -webkit-font-smoothing: antialiased;
  font-size: 16px;
  line-height: 1.85;
  padding: 48px 28px 64px;
}
.page {
  max-width: 1000px;
  margin: 0 auto;
  background: #ffffff;
  border-radius: 26px;
  padding: 58px 66px 46px;
  box-shadow: 0 30px 80px rgba(15, 23, 42, 0.18), 0 2px 8px rgba(15, 23, 42, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.8);
}
h1 {
  font-size: 34px;
  line-height: 1.3;
  margin: 0 0 10px;
  letter-spacing: -0.5px;
  background: linear-gradient(120deg, #4f46e5 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
h1 + p { color: var(--muted); margin-top: 0; }
h2 {
  font-size: 23px;
  margin: 42px 0 14px;
  padding-left: 14px;
  border-left: 5px solid var(--accent);
  line-height: 1.4;
  color: #1e1b4b;
}
h3 {
  font-size: 18px;
  margin: 26px 0 10px;
  color: #334155;
}
h4 { font-size: 16px; margin: 20px 0 8px; }
p { margin: 10px 0; }
ul, ol { padding-left: 24px; margin: 10px 0; }
li { margin: 6px 0; }
strong { color: #312e81; font-weight: 700; }
a { color: #4f46e5; text-decoration: none; }
hr { border: none; border-top: 1px dashed var(--line); margin: 30px 0; }
blockquote {
  margin: 16px 0;
  padding: 14px 18px;
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.08), rgba(6, 182, 212, 0.05));
  border-left: 4px solid var(--accent);
  border-radius: 0 12px 12px 0;
  color: #334155;
}
blockquote p { margin: 4px 0; }
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 18px 0 22px;
  font-size: 14.5px;
  border: 1px solid var(--line);
  border-radius: 14px;
  overflow: hidden;
}
th, td {
  padding: 11px 14px;
  text-align: left;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
th {
  background: #eef2ff;
  color: #3730a3;
  font-weight: 700;
  white-space: nowrap;
}
tr:nth-child(even) td { background: #fafbff; }
tr:last-child td { border-bottom: none; }
code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 1px 6px;
  font-size: 0.9em;
  color: #be123c;
}
pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 18px 20px;
  border-radius: 14px;
  overflow-x: auto;
  font-size: 13.5px;
  line-height: 1.7;
}
pre code { background: transparent; border: none; color: inherit; padding: 0; }
.mermaid-wrap {
  margin: 20px 0;
  padding: 22px;
  background: linear-gradient(180deg, #ffffff, #f8fafc);
  border: 1px solid var(--line);
  border-radius: 18px;
  text-align: center;
  overflow-x: auto;
}
.mermaid-wrap pre.mermaid {
  background: transparent;
  color: var(--ink);
  padding: 0;
  margin: 0;
  text-align: center;
  border-radius: 0;
  font-size: 14px;
}
.mermaid-wrap svg { max-width: 100%; height: auto; }
.page.layout-newspaper {
  max-width: none;
  padding: 42px 46px 30px;
}
.layout-newspaper .report-body {
  column-gap: 32px;
  column-rule: 1px solid #dbe3ef;
  font-size: 17px;
  line-height: 1.8;
}
.layout-newspaper .report-body > h1:first-child {
  column-span: all;
  margin: 0 0 16px;
  font-size: 31px;
}
.layout-newspaper .report-body > blockquote:first-of-type {
  column-span: all;
  margin-top: 0;
}
.layout-newspaper .report-body h2 {
  break-after: avoid;
  margin-top: 26px;
  font-size: 21px;
}
.layout-newspaper .report-body h3 {
  break-after: avoid;
  font-size: 17px;
}
.layout-newspaper .report-body table,
.layout-newspaper .report-body pre,
.layout-newspaper .report-body blockquote,
.layout-newspaper .report-body .mermaid-wrap {
  break-inside: avoid;
}
.layout-newspaper .report-body table {
  font-size: 12.5px;
  line-height: 1.5;
}
.layout-newspaper .report-body th,
.layout-newspaper .report-body td {
  padding: 6px 7px;
}
.layout-newspaper .report-body th {
  white-space: normal;
}
.layout-newspaper .report-body .mermaid-wrap {
  column-span: all;
  padding: 16px;
  margin-top: 24px;
}
.layout-newspaper .report-body .mermaid-wrap pre.mermaid {
  font-size: 12px;
}
.layout-newspaper .footer {
  margin-top: 24px;
}
.footer {
  margin-top: 44px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
  color: #94a3b8;
  font-size: 12.5px;
  text-align: right;
}
"""


def _wrap_document(
    body_html: str,
    title: str,
    mermaid_js: str | None,
    layout: str = "single",
    columns: int = 4,
) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    if mermaid_js:
        mermaid = f"""
<script>{mermaid_js}</script>
<script>
  window.__mermaidDone = false;
  (function () {{
    try {{
      mermaid.initialize({{
        startOnLoad: false,
        securityLevel: 'loose',
        theme: 'base',
        themeVariables: {{
          fontFamily: '"PingFang SC", "Microsoft YaHei", sans-serif',
          primaryColor: '#eef2ff',
          primaryTextColor: '#1e293b',
          primaryBorderColor: '#818cf8',
          lineColor: '#94a3b8',
          secondaryColor: '#ecfeff',
          tertiaryColor: '#fff7ed'
        }}
      }});
      var done = function () {{ window.__mermaidDone = true; }};
      mermaid.run({{ querySelector: '.mermaid' }}).then(done).catch(function (e) {{
        console.error('mermaid render failed', e);
        done();
      }});
    }} catch (e) {{
      console.error('mermaid init failed', e);
      window.__mermaidDone = true;
    }}
  }})();
</script>
"""
    else:
        mermaid = "<script>window.__mermaidDone = true;</script>"

    safe_title = html.escape(title or "直播复盘报告")
    newspaper = layout == "newspaper"
    page_class = "page layout-newspaper" if newspaper else "page"
    body_style = f' style="column-count: {max(2, columns)};"' if newspaper else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{CSS}</style>
</head>
<body>
<main class="{page_class}">
<div class="report-body"{body_style}>
{body_html}
</div>
<div class="footer">由 DeepSeek × bili-whisper 自动生成 · {generated}</div>
</main>
{mermaid}
</body>
</html>"""


def _launch_browser(playwright):
    errors: list[str] = []
    for kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - try next launch strategy
            errors.append(f"{kwargs or 'bundled'}: {exc}")
    for path in CHROME_PATHS:
        if Path(path).is_file():
            try:
                return playwright.chromium.launch(executable_path=path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")
    detail = "\n".join(errors[-3:])
    raise RenderError(
        "无法启动浏览器渲染图片。请安装 Playwright Chromium：\n"
        "  pip install playwright && python -m playwright install chromium\n"
        f"详细信息：\n{detail}"
    )


def build_html(
    markdown_text: str,
    title: str = "",
    layout: str = "single",
    columns: int = 4,
) -> str:
    text, mermaid_blocks = _extract_mermaid(markdown_text)
    body = _markdown_to_html(text)
    body = _inject_mermaid(body, mermaid_blocks)
    mermaid_js = _ensure_mermaid_js() if mermaid_blocks else None
    return _wrap_document(body, title, mermaid_js, layout=layout, columns=columns)


def render_markdown_to_png(
    markdown_text: str,
    out_path: str | Path,
    title: str = "",
    width: int | None = None,
    layout: str = "single",
    columns: int = 4,
    scale: float | None = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if width is None:
        width = 2400 if layout == "newspaper" else 1080
    if scale is None:
        scale = 1.5 if layout == "newspaper" else 2.0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError(
            "缺少 Playwright 依赖，请运行: pip install -r requirements-tools.txt\n"
            "然后执行: python -m playwright install chromium"
        ) from exc

    html_doc = build_html(markdown_text, title, layout=layout, columns=columns)

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            page = browser.new_page(
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
            )
            page.set_content(html_doc, wait_until="load", timeout=60_000)
            try:
                page.wait_for_function(
                    "window.__mermaidDone === true", timeout=25_000
                )
            except Exception:  # noqa: BLE001 - 图片优先，超时也继续截图
                pass
            page.wait_for_timeout(500)
            page.screenshot(path=str(out_path), full_page=True)
        finally:
            browser.close()

    return out_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="把 Markdown 报告渲染成图片")
    parser.add_argument("markdown", type=Path, help="Markdown 文件")
    parser.add_argument("-o", "--out", type=Path, default=None, help="输出 PNG")
    parser.add_argument("--title", default="", help="HTML 标题")
    parser.add_argument(
        "--layout",
        choices=["newspaper", "single"],
        default="newspaper",
        help="newspaper=四栏横向版式，single=单栏长图",
    )
    parser.add_argument("--columns", type=int, default=4, help="报纸版式的栏数")
    args = parser.parse_args(argv)

    out = args.out or args.markdown.with_suffix(".png")
    try:
        result = render_markdown_to_png(
            args.markdown.read_text(encoding="utf-8"),
            out,
            title=args.title or args.markdown.stem,
            layout=args.layout,
            columns=args.columns,
        )
    except (OSError, RenderError) as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 1
    print(f"已生成图片: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
