"""Fix wrong fields and fill remaining gaps on ASOS page 2."""
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp("http://localhost:9222")
ctx = browser.contexts[0]

page = None
for p in ctx.pages:
    if "oneclick-ui" in p.url:
        page = p
        break

if not page:
    print("ERROR: form tab not found")
    pw.stop()
    exit(1)

print(f"Page: {page.title()[:60]}")

combos = page.get_by_role("combobox").all()
radios = page.get_by_role("radio").all()
spins = page.get_by_role("spinbutton").all()

# --- FIX 1: Sponsorship radio should be No (radio[5], val="0") ---
print("\n--- Fix: Sponsorship = No ---")
# Radio[4] is checked (val=1, Yes) but should be No
# Radio[5] is val=0 (No) — click it
r5 = radios[5]
r5.click()
page.wait_for_timeout(500)
print(f"  Radio[5] clicked (No). Checked: {r5.is_checked()}")

# --- FIX 2: Gender Identity = Male (combo[10] shows Female) ---
print("\n--- Fix: Gender Identity = Male ---")
gender_combo = combos[10]
gender_combo.click()
page.wait_for_timeout(300)
# Clear and type "Male"
gender_combo.fill("")
page.wait_for_timeout(200)
gender_combo.fill("Male")
page.wait_for_timeout(1000)
# Select first option
option = page.get_by_role("option").first
if option.count() > 0:
    option.click()
    print(f"  Selected option. Val: {gender_combo.input_value()}")
else:
    gender_combo.press("ArrowDown")
    page.wait_for_timeout(200)
    gender_combo.press("Enter")
    print(f"  ArrowDown+Enter. Val: {gender_combo.input_value()}")
page.wait_for_timeout(300)

# --- FILL 1: Pronouns (combo[0]) = He/Him ---
print("\n--- Fill: Pronouns = He/Him ---")
pronouns = combos[0]
if not pronouns.input_value().strip():
    pronouns.click()
    page.wait_for_timeout(300)
    pronouns.fill("He")
    page.wait_for_timeout(1000)
    option = page.get_by_role("option").first
    if option.count() > 0:
        option.click()
    else:
        pronouns.press("ArrowDown")
        page.wait_for_timeout(200)
        pronouns.press("Enter")
    print(f"  Pronouns set to: {pronouns.input_value()}")
    page.wait_for_timeout(300)

# --- FILL 2: Eligibility basis (combo[1]) = Visa ---
print("\n--- Fill: Eligibility = Visa ---")
elig = combos[1]
if not elig.input_value().strip():
    elig.click()
    page.wait_for_timeout(300)
    elig.fill("Visa")
    page.wait_for_timeout(1000)
    option = page.get_by_role("option").first
    if option.count() > 0:
        option.click()
    else:
        elig.press("ArrowDown")
        page.wait_for_timeout(200)
        elig.press("Enter")
    print(f"  Eligibility set to: {elig.input_value()}")
    page.wait_for_timeout(300)

# --- FILL 3: Age Range (combo[6]) = 25-34 ---
print("\n--- Fill: Age Range ---")
age = combos[6]
if not age.input_value().strip():
    age.click()
    page.wait_for_timeout(300)
    age.fill("25")
    page.wait_for_timeout(1000)
    option = page.get_by_role("option").first
    if option.count() > 0:
        option.click()
    else:
        age.press("ArrowDown")
        page.wait_for_timeout(200)
        age.press("Enter")
    print(f"  Age set to: {age.input_value()}")
    page.wait_for_timeout(300)

# --- FILL 4: Criminal offence radio (radio[6-7]) = No ---
print("\n--- Fill: Criminal offence = No ---")
# Radio[7] is val="0" (No)
r7 = radios[7]
r7.click()
page.wait_for_timeout(500)
print(f"  Radio[7] clicked (No). Checked: {r7.is_checked()}")

# --- FILL 5: Salary spinbutton = 22000 ---
print("\n--- Fill: Salary = 22000 ---")
if spins:
    salary = spins[0]
    salary.click()
    salary.fill("22000")
    salary.press("Tab")
    page.wait_for_timeout(300)
    print(f"  Salary set to: {salary.input_value()}")

# --- CLEANUP: Clear N/A from conditional fields ---
print("\n--- Cleanup: Clear N/A from conditional fields ---")
texts = page.get_by_role("textbox").all()
for i, t in enumerate(texts):
    try:
        val = t.input_value(timeout=500)
        if val == "N/A":
            t.fill("")
            print(f"  Cleared N/A from text[{i}]")
    except Exception:
        pass

# --- Screenshot ---
screenshot_path = "/tmp/asos_page2_fixed.png"
page.wait_for_timeout(1000)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot saved: {screenshot_path}")

# Final status check
print("\n=== Final Status ===")
combos = page.get_by_role("combobox").all()
for i, cb in enumerate(combos):
    try:
        val = cb.input_value(timeout=500)
        if val:
            print(f"  combo[{i}] = \"{val[:40]}\"")
    except Exception:
        pass

radios = page.get_by_role("radio").all()
for i, r in enumerate(radios):
    try:
        if r.is_checked():
            val = r.evaluate("el => el.getAttribute('value')")
            print(f"  radio[{i}] checked, val=\"{val}\"")
    except Exception:
        pass

pw.stop()
