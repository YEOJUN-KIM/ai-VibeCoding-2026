"""Generator/static contract for the PAPER-MVP page. No DB, no server, no file writes."""
import re
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "auto_trader/static/paper-mvp.html"
HIDE_RULE = ".topbar .right span:first-child{display:none}"


def generate_in_memory():
    """Run the generator with its single write captured in memory (nothing touches disk)."""
    captured = {}

    def fake_write(self, data, *a, **k):
        captured[Path(self).name] = data
        return len(data)

    with patch.object(Path, "write_text", fake_write):
        runpy.run_path(str(ROOT / "scripts/build_paper_mvp_page.py"), run_name="__main__")
    return captured["paper-mvp.html"]


def badge_hidden_at(css_html, width):
    """True if any rule active at this viewport width sets display:none on a .topbar .right span."""
    css = "".join(re.findall(r"<style>(.*?)</style>", css_html, flags=re.S))
    hidden = False
    pos = 0
    pattern = re.compile(r"@media\(max-width:(\d+)px\)\{|([^{}@]+)\{([^{}]*)\}")
    # flatten: top-level rules, and media blocks tracked by brace depth
    depth_stack = []
    i = 0
    while i < len(css):
        m = re.compile(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{").match(css, i)
        if m:
            depth_stack.append(int(m.group(1)))
            i = m.end()
            continue
        if css[i] == "}" and depth_stack:
            depth_stack.pop()
            i += 1
            continue
        m = re.compile(r"([^{}@]+)\{([^{}]*)\}").match(css, i)
        if m:
            active = all(width <= limit for limit in depth_stack)
            if active and ".topbar .right span" in m.group(1) and re.search(r"display\s*:\s*none", m.group(2)):
                hidden = True
            i = m.end()
            continue
        i += 1
    return hidden


class PaperMvpPageContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = generate_in_memory()
        cls.static = STATIC.read_text(encoding="utf-8")

    def test_generator_never_emits_badge_hide_rule(self):
        self.assertNotIn("span:first-child{display:none}", self.generated)

    def test_static_page_has_no_badge_hide_rule(self):
        self.assertNotIn("span:first-child{display:none}", self.static)

    def test_badge_visible_at_all_widths(self):
        for width in (1440, 1000, 390):
            with self.subTest(width=width):
                self.assertFalse(badge_hidden_at(self.generated, width))
                self.assertFalse(badge_hidden_at(self.static, width))

    def test_detector_catches_the_old_rule(self):
        bad = "<style>@media(max-width:1200px){" + HIDE_RULE[:-1] + "}</style>"
        self.assertTrue(badge_hidden_at(bad, 1000))
        self.assertTrue(badge_hidden_at(bad, 390))
        self.assertFalse(badge_hidden_at(bad, 1440))


if __name__ == "__main__":
    unittest.main()
