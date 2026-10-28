"""Ask Gemini through Google's Antigravity CLI (`agy`), signed in with the owner's Google AI Pro account.

(The old Gemini CLI no longer accepts Google sign-in since 18 June 2026; `agy` replaces it.)

One-time setup, in your own PowerShell:
    irm https://antigravity.google/cli/install.ps1 | iex       # Google's official installer
    agy -p "say hi"      # a browser opens; approve, paste the code back within ~30 s. Credentials are then cached.

Then:   python publisher/gemini_ask.py "Reply with the single word: ok"
The brand instructions (brand/gem_instructions.md) are added to every prompt, so it answers as the Gem would.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BRAND = Path(__file__).resolve().parent / "brand"


class GeminiError(RuntimeError):
    pass


def find_agy():
    """Path of the Antigravity CLI: $AGY_EXE (for tests), PATH, or its default install folder."""
    for cand in (os.environ.get("AGY_EXE"), shutil.which("agy"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")):
        if cand and Path(cand).exists():
            return cand
    raise GeminiError("Antigravity CLI (agy) not found. Install it in PowerShell: irm https://antigravity.google/cli/install.ps1 | iex")


def brand_prefix():
    text = (BRAND / "gem_instructions.md").read_text(encoding="utf-8")
    return ("Follow these instructions exactly. Answer ONLY with what is asked, no introduction, no markdown fences, "
            "and do not use any tools or edit any files.\n\n" + text + "\n\n---\nREQUEST:\n")


def ask(prompt, timeout=330, retries=2):
    exe = find_agy()
    full = brand_prefix() + prompt
    last = ""
    with tempfile.TemporaryDirectory() as scratch:  # the agent must not touch the project
        for _ in range(retries + 1):
            try:
                proc = subprocess.run(
                    ([sys.executable] if exe.endswith(".py") else []) + [exe, "-p", full, "--output-format", "json", "--print-timeout", "5m"],
                    capture_output=True, text=True, encoding="utf-8", cwd=scratch, timeout=timeout,
                    env={**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"},
                )
            except subprocess.TimeoutExpired:
                last = "timed out"
                continue
            out, err = (proc.stdout or "").strip(), (proc.stderr or "").strip()
            blob = f"{out}\n{err}".lower()
            if "authentication required" in blob or "not signed in" in blob or "sign in" in blob and not out:
                raise GeminiError("agy is not signed in. Run `agy -p \"say hi\"` once in PowerShell and approve in the browser.")
            text = out
            try:
                env = json.loads(out)
                if str(env.get("status") or "ok").lower() not in ("ok", "success", "completed", "done"):
                    last = f"status {env.get('status')}: {str(env.get('response', ''))[:200]}"
                    continue
                text = str(env.get("response", "")).strip()
            except (json.JSONDecodeError, AttributeError):
                pass  # plain text output
            if proc.returncode == 0 and text:
                return text
            last = err or out or f"exit code {proc.returncode}"
    raise GeminiError(f"Gemini call failed: {last[:300]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(ask(" ".join(sys.argv[1:]) or "Reply with the single word: ok"))
