# Task 15: Runner `chrome-pw` Subcommand

**Files:**
- Modify: `jobpulse/runner.py`

**Why:** Convenience command to launch Chrome with `--remote-debugging-port=9222` and a separate profile. Without this, the user has to remember the long Chrome launch command.

---

- [ ] **Step 1: Find the subcommand dispatch in runner.py**

Look for the `if __name__` or `match`/`if-elif` chain that dispatches subcommands like `daemon`, `multi-bot`, `ext-bridge`, etc.

- [ ] **Step 2: Add the `chrome-pw` subcommand**

```python
    elif command == "chrome-pw":
        import subprocess
        import sys
        import os

        profile_dir = os.path.expanduser("~/.chrome-playwright-profile")
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        port = os.environ.get("PLAYWRIGHT_CDP_PORT", "9222")

        if not os.path.exists(chrome_path):
            print(f"Chrome not found at {chrome_path}")
            sys.exit(1)

        print(f"Launching Chrome with CDP on port {port}")
        print(f"Profile: {profile_dir}")
        print("First run: log into ATS platforms manually. Sessions persist.")

        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"Chrome launched. Playwright can connect at http://localhost:{port}")
```

- [ ] **Step 3: Update the help text / usage string**

Find the usage/help string in runner.py and add:

```
python -m jobpulse.runner chrome-pw     # Launch Chrome with CDP for Playwright engine
```

- [ ] **Step 4: Test manually**

Run: `python -m jobpulse.runner chrome-pw`
Expected: Chrome launches with a separate profile. Visit `http://localhost:9222/json` in browser — should return JSON with tab info.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/runner.py
git commit -m "feat(runner): chrome-pw subcommand — launch Chrome with CDP for Playwright

Starts Chrome with --remote-debugging-port=9222 and a separate profile
directory. One-time manual login to ATS platforms, then sessions persist."
```
