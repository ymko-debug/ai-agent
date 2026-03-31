import selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
import time
import random

def run(input_data: dict) -> dict:
    first_name = input_data.get("first_name", "")
    last_name = input_data.get("last_name", "")
    email = input_data.get("email", "")
    password = input_data.get("password", "")
    registration_url = input_data.get("registration_url", "https://www.saatchiart.com/signup")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(10)

        driver.get(registration_url)
        time.sleep(2)

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "form"))
        )

        def human_type(element, text):
            for char in text:
                element.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

        first_name_field = None
        last_name_field = None
        email_field = None
        password_field = None
        submit_button = None

        try:
            first_name_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "first-name")) or
                EC.presence_of_element_located((By.NAME, "firstName")) or
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='First'], input[id*='first']"))
            )
        except:
            pass

        if not first_name_field:
            try:
                labels = driver.find_elements(By.TAG_NAME, "label")
                for label in labels:
                    if "first" in label.text.lower():
                        label_for = label.get_attribute("for")
                        if label_for:
                            first_name_field = driver.find_element(By.ID, label_for)
                        break
            except:
                pass

        if not first_name_field:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                placeholder = inp.get_attribute("placeholder") or ""
                if "first" in placeholder.lower():
                    first_name_field = inp
                    break

        if first_name_field:
            human_type(first_name_field, first_name)
        else:
            return {"success": False, "result": "Error: Could not locate first name field"}

        try:
            last_name_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "last-name")) or
                EC.presence_of_element_located((By.NAME, "lastName")) or
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Last'], input[id*='last']"))
            )
        except:
            pass

        if not last_name_field:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                if inp != first_name_field:
                    placeholder = inp.get_attribute("placeholder") or ""
                    if "last" in placeholder.lower():
                        last_name_field = inp
                        break

        if last_name_field:
            human_type(last_name_field, last_name)
        else:
            return {"success": False, "result": "Error: Could not locate last name field"}

        try:
            email_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "email")) or
                EC.presence_of_element_located((By.NAME, "email")) or
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
            )
        except:
            pass

        if not email_field:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                inp_type = inp.get_attribute("type") or ""
                if inp_type == "email":
                    email_field = inp
                    break

        if email_field:
            human_type(email_field, email)
        else:
            return {"success": False, "result": "Error: Could not locate email field"}

        try:
            password_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "password")) or
                EC.presence_of_element_located((By.NAME, "password")) or
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
            )
        except:
            pass

        if not password_field:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                inp_type = inp.get_attribute("type") or ""
                if inp_type == "password":
                    password_field = inp
                    break

        if password_field:
            human_type(password_field, password)
        else:
            return {"success": False, "result": "Error: Could not locate password field"}

        time.sleep(1)

        buttons = driver.find_elements(By.TAG_NAME, "button")
        for button in buttons:
            button_text = button.text.lower()
            if "sign up" in button_text or "create" in button_text or "register" in button_text or "submit" in button_text:
                submit_button = button
                break

        if not submit_button:
            anchors = driver.find_elements(By.TAG_NAME, "a")
            for anchor in anchors:
                anchor_text = anchor.text.lower()
                if "sign up" in anchor_text or "create" in anchor_text:
                    submit_button = anchor
                    break

        if not submit_button:
            return {"success": False, "result": "Error: Could not locate submit button"}

        submit_button.click()
        time.sleep(3)

        page_source = driver.page_source.lower()
        current_url = driver.current_url

        captcha_indicators = [
            "captcha", "recaptcha", "g-recaptcha", "verify you're not a robot",
            "prove you're not a robot", "check the box to continue"
        ]
        captcha_found = any(indicator in page_source for indicator in captcha_indicators)

        if captcha_found:
            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    src = iframe.get_attribute("src") or ""
                    if "recaptcha" in src or "captcha" in src:
                        return {"success": False, "result": "Error: CAPTCHA detected - reCAPTCHA iframe found on the page."}
            except:
                pass
            return {"success": False, "result": "Error: CAPTCHA detected - CAPTCHA element found on the page."}

        success_indicators = [
            "registration successful", "welcome", "account created",
            "please check your email", "verify your email", "thank you for signing up"
        ]
        success_found = any(indicator in page_source for indicator in success_indicators)

        if success_found or "login" in current_url or "account" in current_url:
            return {"success": True, "result": "Registration successful - account created."}

        error_indicators = [
            "email already exists", "email is already registered",
            "invalid email", "password is too weak", "password must be",
            "required field", "cannot be empty"
        ]
        for indicator in error_indicators:
            if indicator in page_source:
                return {"success": False, "result": f"Error: {indicator.capitalize()} - User provided information failed validation."}

        if "signup" not in current_url and "register" not in current_url:
            return {"success": True, "result": "Registration appears successful - redirected to different page."}

        return {"success": False, "result": "Error: Unexpected state - could not determine registration outcome."}

    except TimeoutException:
        return {"success": False, "result": "Error: Page load timeout - registration page took too long to load."}
    except NoSuchElementException as e:
        return {"success": False, "result": f"Error: Element not found - {str(e)}"}
    except ElementClickInterceptedException:
        return {"success": False, "result": "Error: Element click intercepted - submit button was blocked."}
    except Exception as e:
        return {"success": False, "result": f"Error: Unexpected error occurred - {str(e)}"}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass