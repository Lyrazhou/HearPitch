"""Optional browser interaction checks for the integrated spectrogram editor."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["HEARPITCH_HOME"] = str((Path(__file__).parent / ".editor-browser-data").resolve())

from fastapi.testclient import TestClient  # noqa: E402
from hearpitch_core.app import create_app  # noqa: E402
from hearpitch_core.transcription import create_project_from_audio  # noqa: E402


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Install playwright and a Chromium browser to run this optional UI test.") from exc

    sample = Path(__file__).parent / "sample_scale.wav"
    if not sample.exists():
        raise SystemExit("Run tests/generate_sample_audio.py first.")
    project = create_project_from_audio(sample, title="Editor Browser", prefer_basic_pitch=False)
    app = create_app()
    from uvicorn import Config, Server
    server = Server(Config(app, host="127.0.0.1", port=8767, log_level="error"))
    import threading
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        import time
        for _ in range(100):
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:8767/api/health", timeout=1).read()
                break
            except Exception:
                time.sleep(.1)
        with sync_playwright() as playwright:
            chromium_path = os.environ.get("HEARPITCH_CHROMIUM", "/usr/bin/chromium")
            browser = playwright.chromium.launch(headless=True, executable_path=chromium_path, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:8767/?project={project['project']['id']}")
            page.wait_for_load_state("networkidle")
            page.evaluate("""async (id) => {
              const response = await fetch(`/api/projects/${encodeURIComponent(id)}`);
              const data = await response.json();
              window.__editorProject = data;
            }""", project["project"]["id"])
            # Exercise a loaded project through the same renderProject entry point.
            page.evaluate("renderProject(window.__editorProject)")
            page.wait_for_selector(".spectrogram-note")
            assert page.locator("#spectrogramImage").get_attribute("src").startswith("/api/projects/")
            page.locator("#timeZoomIn").click()
            assert float(page.locator("#timeZoom").input_value()) > 1
            page.locator("#pitchZoomIn").click()
            assert float(page.locator("#pitchZoom").input_value()) > 1
            page.locator("#meterSequence").fill("4,2")
            page.locator("#meterSequence").press("Enter")
            page.locator("#beatMode").select_option("digits")
            page.locator("#tapTempoButton").click()
            page.locator("#audioPlayer").evaluate("el => { el.currentTime = 0.8; }")
            page.keyboard.press("1")
            assert page.locator("#tapReadout").inner_text().startswith("打拍已启动")
            assert page.locator(".beat-marker.bar-marker").count() == 1
            assert page.locator(".spectrogram-note").count() >= 4
            page.locator("#tapTempoButton").click()
            page.locator("#beatMode").select_option("space")
            page.locator("#tapTempoButton").click()
            page.keyboard.press("Space")
            assert "已记录" in page.locator("#tapReadout").inner_text()
            page.locator("#keySelect").select_option("D")
            key_context = page.locator("#scoreContext").inner_text()
            assert "调性：D " in key_context and "（用户设定）" in key_context, key_context
            page.locator(".spectrogram-note").first.click()
            assert page.locator("#canvasSelection").inner_text().startswith("试听")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=3)
    print("Editor browser checks passed")


if __name__ == "__main__":
    main()
