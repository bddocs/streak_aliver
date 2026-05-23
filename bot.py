import os
import sys
import json
import time
import random
import traceback
import urllib.request
import urllib.parse
import html
from playwright.sync_api import sync_playwright


# Global list to store all logged print statements (Activity Log)
activity_log = []

# Safe print wrapper to handle Unicode encoding errors in Windows terminal
def safe_print(*args, **kwargs):
    import builtins
    safe_args = []
    for arg in args:
        if isinstance(arg, str):
            safe_args.append(arg.encode('ascii', 'backslashreplace').decode('ascii'))
        else:
            safe_args.append(str(arg).encode('ascii', 'backslashreplace').decode('ascii'))
            
    # Capture the output in our activity log
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    msg = sep.join(safe_args) + end
    activity_log.append(msg)
    
    builtins.print(*safe_args, **kwargs)

print = safe_print

# Global dictionary to store correct option indices mapped by 1-based question number
correct_options = {}
# Global dictionary to store decoded answer texts mapped by 1-based question number
decoded_answers_text = {}

def send_telegram_notification(success, error=None, tb=None, subject=None, chapter=None, q_count=0):
    import builtins
    token = os.environ.get("TELEGRAM_BOT_TOKEN") 
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    subject_esc = html.escape(str(subject or 'Unknown'))
    chapter_esc = html.escape(str(chapter or 'Unknown'))
    
    # 1. Build the status and details sections, pre-truncating large details to ensure HTML tags close properly
    if success:
        status_part = "✅ <b>Chorcha Quiz Bot: Success!</b>\n\n"
        ans_json = json.dumps(decoded_answers_text, indent=4, ensure_ascii=False)
        if len(ans_json) > 1500:
            ans_json = ans_json[:1400] + "\n...[answers truncated]..."
        ans_json_esc = html.escape(ans_json)
        details_part = (
            f"<b>Subject:</b> {subject_esc}\n"
            f"<b>Chapter:</b> {chapter_esc}\n"
            f"<b>Questions Answered:</b> {q_count}\n\n"
            f"<b>Decoded Answers:</b>\n"
            f"<pre>{ans_json_esc}</pre>\n\n"
        )
    else:
        status_part = "❌ <b>Chorcha Quiz Bot: Failure!</b>\n\n"
        err_esc = html.escape(str(error or 'Unknown Error'))
        tb_str = str(tb or 'No traceback available.')
        if len(tb_str) > 1500:
            tb_str = tb_str[:1400] + "\n...[traceback truncated]..."
        tb_esc = html.escape(tb_str)
        details_part = (
            f"<b>Subject:</b> {subject_esc}\n"
            f"<b>Chapter:</b> {chapter_esc}\n"
            f"<b>Questions Answered:</b> {q_count}\n\n"
            f"<b>Error:</b> <code>{err_esc}</code>\n\n"
            f"<b>Traceback:</b>\n"
            f"<pre>{tb_esc}</pre>\n\n"
        )

    # 2. Get activity log, truncate it to fit within Telegram's limit (leaving safe headroom)
    activity_log_str = "".join(activity_log)
    base_len = len(status_part) + len(details_part) + len("📋 <b>Activity Log:</b>\n<pre></pre>")
    available_space = 3900 - base_len
    if available_space < 300:
        available_space = 300
        
    if len(activity_log_str) > available_space:
        activity_log_str = "...\n" + activity_log_str[-available_space:]
        
    activity_log_esc = html.escape(activity_log_str)
    
    # 3. Assemble complete message
    message = (
        f"{status_part}"
        f"{details_part}"
        f"📋 <b>Activity Log:</b>\n"
        f"<pre>{activity_log_esc}</pre>"
    )

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read()
    except Exception as e:
        builtins.print(f"Failed to send Telegram message: {e}")

def decode_string(encoded_str, key):
    if not encoded_str or not isinstance(encoded_str, str):
        return encoded_str

    decoded = []

    for i, char in enumerate(encoded_str):
        cp = ord(char)
        kc = ord(key[i % len(key)])

        decoded_char = chr((cp - kc + 65536) % 65536)
        decoded.append(decoded_char)

    return ''.join(decoded)

def get_option_index(decoded_ans):
    ans = decoded_ans.strip().upper()
    if not ans:
        return 0
        
    # Map options (A -> 0, B -> 1, C -> 2, D -> 3)
    if "A" in ans:
        return 0
    if "B" in ans:
        return 1
    if "C" in ans:
        return 2
    if "D" in ans:
        return 3
        
    # Map numerical options (1 -> 0, 2 -> 1, 3 -> 2, 4 -> 3)
    if "1" in ans:
        return 0
    if "2" in ans:
        return 1
    if "3" in ans:
        return 2
    if "4" in ans:
        return 3
        
    # Fallback to first character check
    char = ans[0] if len(ans) > 0 else ''
    if char in ['A', '1']:
        return 0
    if char in ['B', '2']:
        return 1
    if char in ['C', '3']:
        return 2
    if char in ['D', '4']:
        return 3
        
    return 0

def handle_response(response):
    # Intercept the response of /exam/quick
    if "mujib.chorcha.net/exam/quick" in response.url and response.request.method == "POST":
        print("Intercepted /exam/quick response!")
        headers = response.headers
        x_chorcha_id = headers.get("x-chorcha-id")
        if not x_chorcha_id:
            print("x-chorcha-id not found in response headers.")
            return
            
        try:
            body = response.json()
            questions = body.get("data", {}).get("questions", [])
            print(f"Successfully loaded {len(questions)} questions from response.")
            
            notes_dict = {}
            
            # Use index of list for correct order since q.get("order") contains float rankings
            for index, q in enumerate(questions):
                encoded_ans = q.get("answer")
                decoded_ans = decode_string(encoded_ans, x_chorcha_id)
                
                # Determine correct option index (0 to 3)
                opt_idx = get_option_index(decoded_ans)
                
                # 1-based question number for mapping
                q_num = index + 1
                notes_dict[q_num] = decoded_ans.strip()
                correct_options[q_num] = opt_idx
                decoded_answers_text[q_num] = decoded_ans.strip()
                
            # Print the well-formatted JSON as requested by the user
            print("Decoded answers mapping [note 1]:")
            print(json.dumps(notes_dict, indent=4, ensure_ascii=False))
            
        except Exception as e:
            print(f"Error parsing quick practice response: {e}")

def load_auth_cookies(context, auth_file_path):
    if not os.path.exists(auth_file_path):
        print(f"Warning: Authentication file '{auth_file_path}' not found!")
        return False
        
    print(f"Loading authentication cookies from '{auth_file_path}'...")
    try:
        with open(auth_file_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            
        playwright_cookies = []
        for cookie in cookies:
            p_cookie = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": cookie["domain"],
                "path": cookie["path"]
            }
            if "expirationDate" in cookie:
                p_cookie["expires"] = int(cookie["expirationDate"])
            if "httpOnly" in cookie:
                p_cookie["httpOnly"] = cookie["httpOnly"]
            if "secure" in cookie:
                p_cookie["secure"] = cookie["secure"]
            if "sameSite" in cookie and cookie["sameSite"] is not None:
                same_site = str(cookie["sameSite"]).capitalize()
                if same_site in ["Lax", "Strict", "None"]:
                    p_cookie["sameSite"] = same_site
            playwright_cookies.append(p_cookie)
            
        context.add_cookies(playwright_cookies)
        print("Cookies successfully injected!")
        return True
    except Exception as e:
        print(f"Error loading cookies: {e}")
        return False

def run_quiz():
    print("Starting Playwright quiz automation script...")
    
    # Path to auth.json
    auth_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.json")
    
    selected_sub_name = "None"
    selected_chap_name = "None"
    question_count = 0
    
    try:
        with sync_playwright() as p:
            # Launch browser in headless mode if specified via environment variable or in CI/GitHub Actions
            is_headless = os.environ.get("HEADLESS", "false").lower() == "true" or os.environ.get("CI") is not None
            print(f"Launching browser (headless={is_headless})...")
            browser = p.chromium.launch(
                headless=is_headless,
                args=[] if is_headless else ["--start-maximized"]
            )
            
            # Create a new browser context
            if is_headless:
                # Set a standard desktop viewport size in headless mode
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800}
                )
            else:
                context = browser.new_context(
                    no_viewport=True  # Allows the browser to maximize fully
                )
            
            # Load the cookies from auth.json
            load_auth_cookies(context, auth_file_path)
            
            page = context.new_page()
            
            # Register network response listener before click actions
            page.on("response", handle_response)
            
            # Navigation and initialization loop
            failed_chapters = set() # set of (subject_name, chapter_name)
            failed_subjects = set() # set of subject_name
            
            quiz_started = False
            max_attempts = 10
            attempt = 0
            
            while not quiz_started and attempt < max_attempts:
                attempt += 1
                print(f"\n--- Navigation Attempt {attempt}/{max_attempts} ---")
                
                # Clear correct_options for a fresh attempt
                correct_options.clear()
                decoded_answers_text.clear()
                
                # Navigate to practice exam page safely with timeout handling
                print("Navigating to practice exam page...")
                try:
                    # Use wait_until="domcontentloaded" to avoid hanging on slow analytics / heavy media
                    page.goto("https://chorcha.net/practice-exam", wait_until="domcontentloaded", timeout=30000)
                    # Wait for network idle up to 5 seconds, but ignore timeouts/errors if it takes longer
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"Navigation timed out or failed: {e}. Retrying...")
                    continue
                    
                # Verify login status
                login_btn = page.locator('text="লগইন", text="Login"')
                if login_btn.first.is_visible():
                    print("\n*** ACTION REQUIRED ***")
                    print("The session cookies in auth.json may have expired or are invalid.")
                    if is_headless:
                        raise RuntimeError("Session expired/invalid, and cannot log in manually in headless environment.")
                    print("Please log into your Chorcha account manually in the browser window.")
                    print("The script will wait for you to log in...")
                    while login_btn.first.is_visible():
                        page.wait_for_timeout(1000)
                    print("Login detected! Proceeding with automation...\n")
                    page.wait_for_timeout(2000)
                    
                # 1. Click on target subject (Bangla/Krishi/Hisab) or fall back to ICT
                print("Locating subjects...")
                subjects = page.locator('main h3')
                try:
                    subjects.first.wait_for(state="visible", timeout=10000)
                except Exception:
                    print("Failed to locate subjects. Retrying...")
                    continue
                    
                sub_count = subjects.count()
                if sub_count == 0:
                    print("No subjects found. Retrying...")
                    continue
                    
                # Search subject names to identify ICT subject only
                ict_subject = None
                
                for idx in range(sub_count):
                    try:
                        text = subjects.nth(idx).inner_text().strip()
                        if "তথ্য ও যোগাযোগ প্রযুক্তি" in text:
                            ict_subject = (idx, text)
                            break
                    except Exception:
                        pass
                
                if ict_subject is not None and ict_subject[1] not in failed_subjects:
                    selected_sub_idx = ict_subject[0]
                    selected_sub_name = ict_subject[1]
                    print(f"Selected ICT: {repr(selected_sub_name)} (index {selected_sub_idx})")
                else:
                    if ict_subject is None:
                        print("ICT subject ('তথ্য ও যোগাযোগ প্রযুক্তি') not found on the page!")
                    else:
                        print("ICT subject failed previously. Resetting failure history...")
                        failed_chapters.clear()
                        failed_subjects.clear()
                    continue
                    
                selected_subject = subjects.nth(selected_sub_idx)
                selected_subject.scroll_into_view_if_needed()
                selected_subject.click()
                
                # Wait for the chapter list page to render (the subject name becomes an h2 title)
                try:
                    page.locator('main h2').wait_for(state="visible", timeout=10000)
                except Exception:
                    print("Failed to load chapters page (h2 not found). Retrying...")
                    failed_subjects.add(selected_sub_name)
                    continue
                page.wait_for_timeout(500)
                
                # 2. Click on a random chapter / expand accordion if needed
                print("Locating chapters...")
                try:
                    page.locator('main h3').first.wait_for(state="visible", timeout=10000)
                except Exception:
                    print("No chapters/groups visible for this subject. Retrying...")
                    failed_subjects.add(selected_sub_name)
                    continue
                    
                top_h3s = page.locator('main h3').all()
                if len(top_h3s) == 0:
                    print("Zero chapters/groups found. Retrying...")
                    failed_subjects.add(selected_sub_name)
                    continue
                    
                # Filter top_h3s to exclude failed ones
                unfailed_h3s = []
                for h3 in top_h3s:
                    try:
                        h3_text = h3.inner_text().strip()
                        if (selected_sub_name, h3_text) not in failed_chapters:
                            unfailed_h3s.append((h3, h3_text))
                    except Exception:
                        pass
                
                if len(unfailed_h3s) == 0:
                    print("All chapters under this subject failed. Resetting failure history...")
                    failed_chapters.clear()
                    failed_subjects.clear()
                    continue
                    
                selected_choice = random.choice(unfailed_h3s)
                selected_h3 = selected_choice[0]
                selected_h3_text = selected_choice[1]
                
                selected_h3.scroll_into_view_if_needed()
                print(f"Clicking chapter/group '{repr(selected_h3_text)}'...")
                selected_h3.click()
                selected_chap_name = selected_h3_text
                
                # 3. Click on Quick Practice (দ্রুত প্র্যাকটিস) button
                print("Locating Mode selection dialog...")
                quick_practice_btn = page.locator('button:has-text("দ্রুত প্র্যাকটিস")')
                
                # Wait up to 2 seconds for either the dialog to open or the accordion to expand
                start_time = time.time()
                while (time.time() - start_time) < 2.0:
                    if quick_practice_btn.is_visible():
                        break
                    all_current_h3s = page.locator('main h3').all()
                    top_header_texts = [h.inner_text().strip() for h in top_h3s]
                    has_visible_subs = False
                    for h in all_current_h3s:
                        try:
                            h_text = h.inner_text().strip()
                            if h_text not in top_header_texts and h.is_visible():
                                has_visible_subs = True
                                break
                        except Exception:
                            pass
                    if has_visible_subs:
                        break
                    page.wait_for_timeout(100)
                
                # Check if modal opened directly
                if not quick_practice_btn.is_visible():
                    # If not visible, it might be an accordion that expanded.
                    all_current_h3s = page.locator('main h3').all()
                    sub_chapters = []
                    top_header_texts = [h.inner_text().strip() for h in top_h3s]
                    
                    for h in all_current_h3s:
                        try:
                            h_text = h.inner_text().strip()
                            if h_text not in top_header_texts and h.is_visible():
                                # Check if this specific sub-chapter has failed previously
                                if (selected_sub_name, h_text) not in failed_chapters:
                                    sub_chapters.append(h)
                        except Exception:
                            pass
                    
                    if len(sub_chapters) > 0:
                        sub_idx = random.randint(0, len(sub_chapters) - 1)
                        selected_sub = sub_chapters[sub_idx]
                        selected_chap_name = selected_sub.inner_text().strip()
                        selected_sub.scroll_into_view_if_needed()
                        print(f"Detected expanded accordion. Clicking sub-chapter '{repr(selected_chap_name)}' (index {sub_idx})...")
                        selected_sub.click()
                        # Wait up to 2 seconds for the dialog to open
                        try:
                            quick_practice_btn.wait_for(state="visible", timeout=2000)
                        except Exception:
                            pass
                    else:
                        print("No unfailed sub-chapters found. Resetting failure history...")
                        failed_chapters.clear()
                        failed_subjects.clear()
                        continue
                
                try:
                    quick_practice_btn.wait_for(state="visible", timeout=8000)
                    print("Clicking Quick Practice button...")
                    quick_practice_btn.click()
                    
                    # Wait up to 5 seconds dynamically for correct_options to populate
                    start_time = time.time()
                    while len(correct_options) == 0 and (time.time() - start_time) < 5.0:
                        page.wait_for_timeout(100)
                    
                    if len(correct_options) > 0:
                        quiz_started = True
                    else:
                        print("No questions loaded from API. Retrying with a different subject/chapter...")
                        failed_chapters.add((selected_sub_name, selected_chap_name))
                            
                except Exception as e:
                    print(f"Failed to click quick practice: {e}. Taking diagnostic screenshot...")
                    page.screenshot(path=f"mode_error_attempt_{attempt}.png")
                    failed_chapters.add((selected_sub_name, selected_chap_name))
                    page.wait_for_timeout(1000)
                    
            if not quiz_started:
                raise RuntimeError("Could not start the quiz after maximum navigation attempts.")
            
            # 4. Wait for quiz page to load and start answering questions
            page.wait_for_timeout(1000)
            print("Quiz started! Answering questions...")
            
            question_count = 0
            waiting_count = 0
            while True:
                # Check if quiz is finished (Skip / Finish stats screen or Go Ahead is visible)
                skip_btn = page.locator('button:has-text("স্কিপ করো")')
                go_ahead_btn = page.locator('button:has-text("এগিয়ে যাও")')
                
                if skip_btn.is_visible():
                    print("\nQuiz completed! Reached the stats screen (with skip button).")
                    break
                if go_ahead_btn.is_visible():
                    print("\nQuiz completed! Reached the stats screen (directly to go ahead).")
                    break
                    
                # Locate option buttons on page
                options = page.locator('button.rounded-xl.border')
                
                if options.count() > 0:
                    waiting_count = 0
                    question_count += 1
                    
                    # Get the correct option index from the intercepted answers dictionary
                    # Default to index 0 if not found
                    target_idx = correct_options.get(question_count, 0)
                    
                    # Check for out-of-bounds safety
                    if target_idx >= options.count():
                        target_idx = 0
                        
                    print(f"Question {question_count}: Clicking option index {target_idx}...")
                    try:
                        options.nth(target_idx).click()
                    except Exception as e:
                        print(f"Failed to click option {target_idx}: {e}. Retrying with first option...")
                        try:
                            options.first.click()
                        except Exception:
                            pass
                    
                    # Dynamically wait for next/finish button to become visible (up to 1.5s)
                    next_btn = page.locator('button:has-text("পরের প্রশ্ন"), button:has-text("শেষ করো")')
                    try:
                        next_btn.wait_for(state="visible", timeout=1500)
                    except Exception:
                        pass
                    
                    if next_btn.is_visible():
                        print("Clicking next / finish button...")
                        try:
                            next_btn.click()
                            # Wait for next_btn to become hidden (meaning we transitioned to the next question)
                            next_btn.wait_for(state="hidden", timeout=1500)
                        except Exception:
                            pass
                    
                    # Tiny pause to let DOM settle
                    page.wait_for_timeout(150)
                else:
                    # If no options found, wait a bit or check if we finished
                    page.wait_for_timeout(1000)
                    waiting_count += 1
                    
                    if skip_btn.is_visible():
                        print("\nQuiz completed! Reached the stats screen (with skip button).")
                        break
                    if go_ahead_btn.is_visible():
                        print("\nQuiz completed! Reached the stats screen (directly to go ahead).")
                        break
                    
                    # If we've been waiting too long, perform diagnostics
                    if waiting_count >= 15:
                        print("Stuck waiting for 15 seconds. Checking status...")
                        if go_ahead_btn.is_visible():
                            print("Found 'এগিয়ে যাও' button during diagnostic check. Breaking.")
                            break
                        if skip_btn.is_visible():
                            print("Found 'স্কিপ করো' button during diagnostic check. Breaking.")
                            break
                        waiting_count = 0
                    
                    print("Waiting for options or quiz screen to load...")
                    
            # 5. Click the "স্কিপ করো" button on the score screen if visible
            skip_btn = page.locator('button:has-text("স্কিপ করো")')
            go_ahead_btn = page.locator('button:has-text("এগিয়ে যাও")')
            if skip_btn.is_visible():
                print("Clicking skip button...")
                try:
                    skip_btn.scroll_into_view_if_needed()
                    skip_btn.click()
                    # Wait for go ahead button to become visible after skip
                    go_ahead_btn.wait_for(state="visible", timeout=5000)
                except Exception as e:
                    print(f"Error handling skip button: {e}")
            
            # 6. Click the "এগিয়ে যাও" button to complete the flow and return to subject page
            print("Locating go ahead button...")
            try:
                go_ahead_btn.wait_for(state="visible", timeout=10000)
                print("Clicking go ahead button...")
                go_ahead_btn.click()
                # Wait for navigation back to complete
                page.wait_for_timeout(1500)
            except Exception as e:
                print(f"Failed to click go ahead button: {e}")
            
            print("\nAll steps completed successfully!")
            print("Closing browser...")
            browser.close()
            
            # Send Telegram success notification
            send_telegram_notification(
                success=True,
                subject=selected_sub_name,
                chapter=selected_chap_name,
                q_count=question_count
            )
            
    except BaseException as e:
        tb_text = traceback.format_exc()
        print(f"\nExecution failed: {e}")
        print(tb_text)
        
        # Send Telegram failure notification
        send_telegram_notification(
            success=False,
            error=e,
            tb=tb_text,
            subject=selected_sub_name,
            chapter=selected_chap_name,
            q_count=question_count
        )
        
        raise e

if __name__ == "__main__":
    run_quiz()
