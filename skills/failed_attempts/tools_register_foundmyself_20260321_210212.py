import sys
import json
import time
import random
from playwright.sync_api import sync_playwright, Page, Browser
from pathlib import Path

def run(input_data: dict) -> dict:
    email = input_data.get("email", "")
    password = input_data.get("password", "")
    first_name = input_data.get("first_name", "")
    last_name = input_data.get("last_name", "")
    birth_month = input_data.get("birth_month", "")
    birth_day = input_data.get("birth_day", "")
    birth_year = input_data.get("birth_year", "")
    gender = input_data.get("gender", "")

    result = {
        "success": False,
        "result": "Registration failed - no confirmation received"
    }

    browser = None
    page = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            
            page.goto("https://www.foundmyself.com/register", wait_until="networkidle", timeout=30000)
            time.sleep(2)

            page.wait_for_selector("input, select, button", state="visible", timeout=10000)

            def human_type(element, text):
                for char in text:
                    element.type(char, delay=random.randint(50, 150))

            first_name_input = page.query_selector("input#firstName, input[name='firstName'], input[placeholder*='First']")
            if first_name_input:
                human_type(first_name_input, first_name)
            time.sleep(random.uniform(0.3, 0.7))

            last_name_input = page.query_selector("input#lastName, input[name='lastName'], input[placeholder*='Last']")
            if last_name_input:
                human_type(last_name_input, last_name)
            time.sleep(random.uniform(0.3, 0.7))

            email_input = page.query_selector("input#email, input[name='email'], input[type='email']")
            if email_input:
                human_type(email_input, email)
            time.sleep(random.uniform(0.3, 0.7))

            password_input = page.query_selector("input#password, input[name='password'], input[type='password']")
            if password_input:
                human_type(password_input, password)
            time.sleep(random.uniform(0.3, 0.7))

            month_mapping = {
                "january": "January", "february": "February", "march": "March",
                "april": "April", "may": "May", "june": "June",
                "july": "July", "august": "August", "september": "September",
                "october": "October", "november": "November", "december": "December"
            }
            birth_month_normalized = birth_month.lower().strip() if birth_month else ""
            month_value = month_mapping.get(birth_month_normalized, birth_month)

            month_select = page.query_selector("select#birthMonth, select[name='birthMonth'], select:has-text('Month')")
            if month_select:
                month_select.select_option(label=month_value)
            time.sleep(random.uniform(0.2, 0.5))

            day_input = page.query_selector("input#birthDay, input[name='birthDay']")
            if day_input:
                day_input.fill(str(birth_day))
            time.sleep(random.uniform(0.2, 0.5))

            year_input = page.query_selector("input#birthYear, input[name='birthYear']")
            if year_input:
                year_input.fill(str(birth_year))
            time.sleep(random.uniform(0.2, 0.5))

            gender_select = page.query_selector("select#gender, select[name='gender']")
            if gender_select:
                gender_select.select_option(label=gender)
            time.sleep(random.uniform(0.3, 0.7))

            captcha_iframe = page.query_selector("iframe[src*='recaptcha'], div[class*='captcha'], div[data-sitekey]")
            if captcha_iframe:
                result["success"] = False
                result["result"] = "CAPTCHA detected. Cannot complete registration automatically."
                browser.close()
                return result

            register_button = page.query_selector("button:has-text('Register'), button:has-text('Sign Up'), button:has-text('Create Account'), button[type='submit']")
            if register_button:
                register_button.click()
            time.sleep(3)

            current_url = page.url.lower()
            page_content = page.content().lower()

            success_indicators = ["welcome", "dashboard", "account created", "confirm", "success", "verification"]
            error_indicators = ["error", "exist", "invalid", "required", "failed", "already"]

            is_success = any(indicator in current_url for indicator in ["dashboard", "welcome", "success"]) or \
                         any(indicator in page_content for indicator in success_indicators)

            is_error = any(indicator in page_content for indicator in error_indicators)

            if is_success and not is_error:
                result["success"] = True
                result["result"] = "Registration successful."
            elif is_error:
                error_msg = "Registration failed with error."
                error_elements = page.query_selector_all(".error, .alert, [class*='error'], [class*='alert']")
                for elem in error_elements:
                    text = elem.inner_text()
                    if text:
                        error_msg = text.strip()
                        break
                result["success"] = False
                result["result"] = f"Error: {error_msg}"
            else:
                result["success"] = False
                result["result"] = "An unexpected error occurred during registration. Please check manually."

            browser.close()
            return result

    except Exception as e:
        if browser:
            try:
                browser.close()
            except:
                pass
        result["success"] = False
        result["result"] = f"Exception occurred: {str(e)}"
        return result

if __name__ == "__main__":
    try:
        input_json = sys.argv[1] if len(sys.argv) > 1 else "{}"
        input_data = json.loads(input_json)
    except:
        input_data = {}

    output = run(input_data)
    print(json.dumps(output))
</toolcall>