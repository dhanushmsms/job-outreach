"""
Obscura headless browser integration.
Obscura renders JavaScript via V8 (CDP-compatible, lighter than Chrome).
Playwright connects to it over CDP and returns fully-rendered HTML.
ScrapeGraphAI then receives that HTML as a string source for Claude extraction.
"""

import logging
import os
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

OBSCURA_PORT = 9222
OBSCURA_WS   = f"http://127.0.0.1:{OBSCURA_PORT}"
OBSCURA_BIN  = os.path.expanduser("~/.local/bin/obscura")

_obscura_proc: Optional[subprocess.Popen] = None


# ── Process management ─────────────────────────────────────────────────────────

def is_obscura_running() -> bool:
    """Check if Obscura is already listening on its port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", OBSCURA_PORT), timeout=1):
            return True
    except OSError:
        return False


def start_obscura() -> bool:
    """Start Obscura as a background process. Returns True if started/already running."""
    global _obscura_proc

    if is_obscura_running():
        logger.info("Obscura already running on port %d", OBSCURA_PORT)
        return True

    bin_path = OBSCURA_BIN
    if not os.path.exists(bin_path):
        # Try PATH
        bin_path = shutil.which("obscura")
        if not bin_path:
            logger.error("Obscura binary not found. Run: bash install_obscura.sh")
            return False

    try:
        _obscura_proc = subprocess.Popen(
            [bin_path, "serve", "--port", str(OBSCURA_PORT), "--stealth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait up to 5s for it to be ready
        for _ in range(10):
            time.sleep(0.5)
            if is_obscura_running():
                logger.info("Obscura started (pid=%d)", _obscura_proc.pid)
                return True

        logger.error("Obscura started but not listening after 5s")
        return False
    except Exception as e:
        logger.error("Failed to start Obscura: %s", e)
        return False


def stop_obscura() -> None:
    """Stop the Obscura process we started."""
    global _obscura_proc
    if _obscura_proc and _obscura_proc.poll() is None:
        _obscura_proc.send_signal(signal.SIGTERM)
        try:
            _obscura_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _obscura_proc.kill()
        logger.info("Obscura stopped")
    _obscura_proc = None


@contextmanager
def obscura_session():
    """Context manager: start Obscura, yield, stop if we started it."""
    we_started = False
    if not is_obscura_running():
        we_started = start_obscura()
    try:
        yield is_obscura_running()
    finally:
        if we_started:
            stop_obscura()


# ── Page fetching via Playwright + Obscura CDP ─────────────────────────────────

def fetch_page(
    url: str,
    wait_for: str = "networkidle",
    timeout_ms: int = 20_000,
    scroll: bool = True,
) -> Optional[str]:
    """
    Fetch a URL using Obscura + Playwright. Returns fully-rendered HTML.
    Falls back to None if Obscura isn't available.

    Args:
        url:        Page to load
        wait_for:   Playwright wait state — "networkidle" | "domcontentloaded" | "load"
        timeout_ms: Navigation timeout in ms
        scroll:     Scroll to bottom to trigger lazy-loaded content
    """
    if not is_obscura_running():
        logger.warning("Obscura not running — skipping browser fetch for %s", url)
        return None

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(OBSCURA_WS)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until=wait_for, timeout=timeout_ms)
            except Exception:
                # Some pages never reach networkidle — grab what we have
                pass

            if scroll:
                # Scroll to bottom to load lazy content (job listings etc.)
                page.evaluate("""
                    () => new Promise(resolve => {
                        let total = 0;
                        const step = () => {
                            window.scrollBy(0, 600);
                            total += 600;
                            if (total < document.body.scrollHeight) {
                                setTimeout(step, 200);
                            } else { resolve(); }
                        };
                        step();
                    })
                """)
                page.wait_for_timeout(800)

            html = page.content()
            context.close()
            browser.close()
            logger.info("Obscura fetched %d chars from %s", len(html), url)
            return html

    except Exception as e:
        logger.error("Obscura/Playwright fetch failed for %s: %s", url, e)
        return None


def fetch_pages(urls: list[str], **kwargs) -> dict[str, Optional[str]]:
    """Fetch multiple URLs, reusing the same Obscura connection."""
    if not is_obscura_running():
        return {url: None for url in urls}

    results = {}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(OBSCURA_WS)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )

            for url in urls:
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    if kwargs.get("scroll", True):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(600)
                    results[url] = page.content()
                    logger.info("Fetched %s (%d chars)", url, len(results[url]))
                except Exception as e:
                    logger.warning("Failed to fetch %s: %s", url, e)
                    results[url] = None
                finally:
                    page.close()
                time.sleep(0.8)   # polite pause between pages

            context.close()
            browser.close()

    except Exception as e:
        logger.error("Batch fetch failed: %s", e)
        for url in urls:
            if url not in results:
                results[url] = None

    return results
