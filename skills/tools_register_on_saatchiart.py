import time
import random
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

def human_delay(min_ms=100, max_ms=300):
    """Generate a random delay to simulate human typing speed."""
    return random.uniform(min_ms, max_ms) / 1000

def run(input_data: dict) -> dict:
    """
    Register a new user on Saatchi Art.
    
    Args:
        input_data: dict with keys 'email', 'password', 'first_name', 'last_name'
    
    Returns:
        dict with keys 'success' (bool) and 'result' (str)
    """
    email = input_data.get('email', '')
    password = input_data.get('password', '')
    first_name = input_data.get('first_name', '')
    last_name = input_data.get('last_name', '')
    
    if not email or not password or not first_name or not last_name:
        return {
            'success': False,
            'result': 'Missing required fields: email, password, first_name, last_name'
        }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            timezone_id="America/Los_Angeles",
            locale="en-US"
        )
        page = context.new_page()
        
        try:
            page.goto('https://www.saatchiart.com/register', timeout=30000)
            page.wait_for_load_state('domcontentloaded', timeout=15000)
            time.sleep(human_delay(500, 1000))
            
            first_name_field = page.locator('input[name="firstName"], input[id="firstName"], input[aria-label*="First name"], input[placeholder*="First"]').first
            first_name_field.wait_for(state='visible', timeout=10000)
            first_name_field.click()
            time.sleep(human_delay(100, 200))
            first_name_field.fill('')
            for char in first_name:
                first_name_field.type(char, delay=int(human_delay(50, 150)))
            
            time.sleep(human_delay(200, 400))
            
            last_name_field = page.locator('input[name="lastName"], input[id="lastName"], input[aria-label*="Last name"], input[placeholder*="Last"]').first
            last_name_field.wait_for(state='visible', timeout=10000)
            last_name_field.click()
            time.sleep(human_delay(100, 200))
            last_name_field.fill('')
            for char in last_name:
                last_name_field.type(char, delay=int(human_delay(50, 150)))
            
            time.sleep(human_delay(200, 400))
            
            email_field = page.locator('input[name="email"], input[type="email"], input[id="email"]').first
            email_field.wait_for(state='visible', timeout=10000)
            email_field.click()
            time.sleep(human_delay(100, 200))
            email_field.fill('')
            for char in email:
                email_field.type(char, delay=int(human_delay(50, 150)))
            
            time.sleep(human_delay(200, 400))
            
            password_field = page.locator('input[name="password"], input[type="password"], input[id="password"]').first
            password_field.wait_for(state='visible', timeout=10000)
            password_field.click()
            time.sleep(human_delay(100, 200))
            password_field.fill('')
            for char in password:
                password_field.type(char, delay=int(human_delay(50, 150)))
            
            time.sleep(human_delay(200, 400))
            
            confirm_password_field = page.locator('input[name="confirmPassword"], input[name="confirm_password"], input[id="confirmPassword"]').first
            confirm_password_field.wait_for(state='visible', timeout=10000)
            confirm_password_field.click()
            time.sleep(human_delay(100, 200))
            confirm_password_field.fill('')
            for char in password:
                confirm_password_field.type(char, delay=int(human_delay(50, 150)))
            
            time.sleep(human_delay(300, 500))
            
            captcha_iframe = page.locator('iframe[src*="captcha"], .captcha-container, div[id*="captcha"], [data-sitekey]')
            if captcha_iframe.count() > 0:
                return {
                    'success': False,
                    'result': 'CAPTCHA detected'
                }
            
            submit_button = page.locator('button[type="submit"], button:has-text("Sign Up"), button:has-text("Create Account"), button:has-text("Register"), input[type="submit"]').first
            submit_button.wait_for(state='visible', timeout=10000)
            submit_button.click()
            
            time.sleep(human_delay(1000, 2000))
            
            try:
                page.wait_for_url('**/account/**', timeout=10000)
                return {
                    'success': True,
                    'result': 'Registration successful'
                }
            except PlaywrightTimeoutError:
                pass
            
            error_messages = page.locator('.error, .error-message, .alert-error, [role="alert"], .field-error')
            if error_messages.count() > 0:
                error_text = error_messages.first.text_content(timeout=5000)
                if error_text:
                    if 'email' in error_text.lower() and 'exist' in error_text.lower():
                        return {
                            'success': False,
                            'result': 'Email already exists'
                        }
                    return {
                        'success': False,
                        'result': error_text.strip()
                    }
            
            success_indicators = page.locator('.success, .alert-success, [role="status"]:has-text("success"), h1:has-text("Welcome"), h2:has-text("success")')
            if success_indicators.count() > 0:
                return {
                    'success': True,
                    'result': 'Registration successful'
                }
            
            current_url = page.url
            if 'account' in current_url.lower() or 'welcome' in current_url.lower():
                return {
                    'success': True,
                    'result': 'Registration successful'
                }
            
            return {
                'success': False,
                'result': 'Could not confirm registration status'
            }
            
        except PlaywrightTimeoutError as e:
            return {
                'success': False,
                'result': f'Page load timeout: {str(e)}'
            }
        except Exception as e:
            error_msg = str(e)
            if 'captcha' in error_msg.lower():
                return {
                    'success': False,
                    'result': 'CAPTCHA detected'
                }
            return {
                'success': False,
                'result': f'Registration failed: {error_msg}'
            }
        finally:
            browser.close()
    
    return {
        'success': False,
        'result': 'Unexpected error occurred'
    }