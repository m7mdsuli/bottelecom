import logging
import json
import os
import random
import sqlite3
import pandas as pd
import html
import time
from telegram.helpers import escape_markdown
import zipfile
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import BadRequest
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import asyncio
import time

# ------------------- إعدادات البوت الآمنة -------------------
# يتم تحميل القيم من ملف .env أو من متغيرات بيئة الخادم
load_dotenv()
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or 0)
ENABLE_WEB_DASHBOARD = os.getenv("ENABLE_WEB_DASHBOARD", "false").lower() in ("1", "true", "yes")
TEST_MODE = False
MAINTENANCE_MODE = os.getenv("MAINTENANCE_MODE", "false").lower() in ("1", "true", "yes")

# ملف قاعدة البيانات
# NOTE: On Render, SQLite database persists in the filesystem.
# However, for production, consider using PostgreSQL database service on Render
# and update DB_FILE to use PostgreSQL connection string.
# The exams data is now stored in the database (dynamic_exams table) instead of JSON files,
# which ensures data persistence across deployments.
DB_FILE = "user_progress.db"
MENUS_FILE = "menus.json"
EXAMS_FILE = "exams.json"  # Kept for backward compatibility, but data is now in database

# إعداد السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ------------------- السيرفر الوهمي (لوحة تحكم الويب) -------------------
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if not ENABLE_WEB_DASHBOARD:
            self.send_error(403, "Dashboard disabled.")
            return
        if self.path.startswith('/download_db'):
            # Simple password check via query parameter ?password=...
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query or "")
            supplied_password = params.get("password", [""])[0]

            if not ADMIN_PASSWORD:
                self.send_error(403, "Download disabled: ADMIN_PASSWORD is not set.")
                return

            if supplied_password != ADMIN_PASSWORD:
                self.send_error(401, "Unauthorized: invalid password.")
                return

            if os.path.exists(DB_FILE):
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(DB_FILE)}"')
                self.end_headers()
                with open(DB_FILE, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Database file not found")
            return

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        progress_rows_html = ""
        lab_rows_html = ""
        mazen_rows_html = ""
        try:
            if os.path.exists(DB_FILE):
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                # Fetch User Progress (الجدول القديم)
                cursor.execute("SELECT user_id, first_name, difficulty, current_question, score FROM user_progress ORDER BY score DESC") 
                rows = cursor.fetchall()

                if not rows:
                     progress_rows_html = "<tr><td colspan='5' style='text-align:center'>لا توجد بيانات حتى الآن</td></tr>"
                else:
                    for row in rows:
                        user_id = row[0]
                        name = row[1] if row[1] else "غير معروف"
                        diff = row[2] if row[2] else "-"
                        q_num = row[3]
                        score = row[4]
                        
                        progress_rows_html += f"""
                        <tr>
                            <td>{user_id}</td>
                            <td>{name}</td>
                            <td>{diff}</td>
                            <td>{q_num}</td>
                            <td><strong>{score}</strong></td>
                        </tr>
                        """
                
                # Fetch Lab Results (الجدول الجديد)
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lab_results'")
                if cursor.fetchone():
                    cursor.execute("SELECT user_id, first_name, video1, video2, video2_mini, video3, video4 FROM lab_results")
                    lab_rows = cursor.fetchall()
                    
                    if not lab_rows:
                        lab_rows_html = "<tr><td colspan='7' style='text-align:center'>لا توجد نتائج مخبر حتى الآن</td></tr>"
                    else:
                        for row in lab_rows:
                            l_user_id = row[0]
                            l_name = row[1] if row[1] else "غير معروف"
                            v1 = row[2]
                            v2 = row[3]
                            v2m = row[4]
                            v3 = row[5]
                            v4 = row[6]
                            
                            lab_rows_html += f"""
                            <tr>
                                <td>{l_user_id}</td>
                                <td>{l_name}</td>
                                <td>{v1}</td>
                                <td>{v2}</td>
                                <td>{v2m}</td>
                                <td>{v3}</td>
                                <td>{v4}</td>
                            </tr>
                            """
                else:
                    lab_rows_html = "<tr><td colspan='7' style='text-align:center'>جدول النتائج غير موجود</td></tr>"
                
                # Fetch Mazen Results
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mazen_results'")
                if cursor.fetchone():
                    cursor.execute("SELECT user_id, first_name, id1, id2, id3, id4, id5, id6 FROM mazen_results")
                    mazen_rows = cursor.fetchall()
                    
                    if not mazen_rows:
                        mazen_rows_html = "<tr><td colspan='8' style='text-align:center'>لا توجد نتائج لاختبار مازن حتى الآن</td></tr>"
                    else:
                        for row in mazen_rows:
                            m_user_id = row[0]
                            m_name = row[1] if row[1] else "غير معروف"
                            scores_td = "".join([f"<td>{s}</td>" for s in row[2:]])
                            
                            mazen_rows_html += f"""
                            <tr>
                                <td>{m_user_id}</td>
                                <td>{m_name}</td>
                                {scores_td}
                            </tr>
                            """
                else:
                    mazen_rows_html = "<tr><td colspan='8' style='text-align:center'>جدول النتائج غير موجود</td></tr>"

                conn.close()
            else:
                progress_rows_html = "<tr><td colspan='5' style='text-align:center'>لم يتم إنشاء قاعدة البيانات بعد.</td></tr>"
                lab_rows_html = "<tr><td colspan='7' style='text-align:center'>لم يتم إنشاء قاعدة البيانات بعد.</td></tr>"
                mazen_rows_html = "<tr><td colspan='8' style='text-align:center'>لم يتم إنشاء قاعدة البيانات بعد.</td></tr>"
        except Exception as e:
            progress_rows_html = f"<tr><td colspan='5'>خطأ في القراءة: {e}</td></tr>"
            lab_rows_html = f"<tr><td colspan='7'>خطأ في القراءة: {e}</td></tr>"
            mazen_rows_html = f"<tr><td colspan='8'>خطأ في القراءة: {e}</td></tr>"

        html_content = f"""
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>نتائج اختبار الاتصالات</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 900px; margin: 0 auto; background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ text-align: center; color: #2c3e50; margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                th, td {{ padding: 12px 15px; text-align: right; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #34495e; color: white; }}
                tr:nth-child(even) {{ background-color: #f8f9fa; }}
                tr:hover {{ background-color: #e2e6ea; }}
                .refresh-btn {{ display: block; width: fit-content; margin: 20px auto; padding: 10px 20px; background: #27ae60; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; transition: 0.3s; }}
                .refresh-btn:hover {{ background: #219150; transform: scale(1.05); }}
                .status {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📊 لوحة تقدم المشاركين</h1>
                <div style="display: flex; justify-content: center; gap: 10px; margin: 20px 0;">
                    <a href="/" class="refresh-btn" style="margin: 0;">🔄 تحديث القائمة</a>
                    <a href="/download_db" class="refresh-btn" style="margin: 0; background-color: #2980b9;">📥 تحميل قاعدة البيانات</a>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>ID المستخدم</th>
                            <th>الاسم</th>
                            <th>المستوى</th>
                            <th>وصل للسؤال</th>
                            <th>النتيجة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {progress_rows_html}
                    </tbody>
                </table>

                <h2 style="text-align:center; color:#2c3e50; margin-top:40px;">🧪 نتائج اختبارات المخبر</h2>
                <table>
                    <thead>
                        <tr>
                            <th>ID المستخدم</th>
                            <th>الاسم</th>
                            <th>فيديو 1</th>
                            <th>فيديو 2</th>
                            <th>فيديو 2 (مصغر)</th>
                            <th>فيديو 3</th>
                            <th>فيديو 4</th>
                        </tr>
                    </thead>
                    <tbody>
                        {lab_rows_html}
                    </tbody>
                </table>

                <h2 style="text-align:center; color:#2c3e50; margin-top:40px;">📚 نتائج اختبار مازن</h2>
                <table>
                    <thead>
                        <tr>
                            <th>ID المستخدم</th>
                            <th>الاسم</th>
                            <th>الوحدة 1</th>
                            <th>الوحدة 2</th>
                            <th>الوحدة 3</th>
                            <th>الوحدة 4</th>
                            <th>الوحدة 5</th>
                            <th>الوحدة 6</th>
                        </tr>
                    </thead>
                    <tbody>
                        {mazen_rows_html}
                    </tbody>
                </table>
                <p class="status">Bot Status: Online ✅ | Port: {os.environ.get("PORT", 8080)}</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html_content.encode('utf-8'))

def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"Web Dashboard listening on port {port}")
    server.serve_forever()

# ------------------- دوال تحميل الأسئلة والعبارات -------------------

def load_phrases(file_path):
    try:
        df = pd.read_csv(file_path, encoding='utf-8')
        return df['Phrase_Text'].tolist()
    except FileNotFoundError:
        logging.warning(f"Phrase file not found: {file_path}. Using default phrases.")
        return []
    except Exception as e:
        logging.error(f"Error loading phrase file {file_path}: {e}")
        return []

def load_mazen_test_data():
    mazen_texts = {}
    mazen_srd = {}
    
    # Load texts from textlevels.csv
    try:
        df = pd.read_csv('textlevels.csv', encoding='utf-8')
        df.columns = [c.strip().lower() for c in df.columns]
        for _, row in df.iterrows():
            id_val, level, text = row['id'], row['level'], row['text']
            if id_val not in mazen_texts:
                mazen_texts[id_val] = {}
            mazen_texts[id_val][level] = text
        logging.info(f"Successfully loaded Mazen test texts for {len(mazen_texts)} IDs.")
    except FileNotFoundError:
        logging.error("Error: textlevels.csv not found.")
    except Exception as e:
        logging.error(f"An error occurred while loading textlevels.csv: {e}")

    # Load narrative questions from idxsrd.csv files
    for i in range(1, 7):  # Assuming ids 1 to 6
        file_path = f'id{i}srd.csv'
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, keep_default_na=False)
            df.columns = [str(c).strip().lower() for c in df.columns]
            srd_questions = []
            for _, row in df.iterrows():
                srd_questions.append({
                    "question": row['question'],
                    "answer": row['answer']
                })
            mazen_srd[i] = srd_questions
            logging.info(f"Successfully loaded {len(srd_questions)} narrative questions for id{i}.")
        except FileNotFoundError:
            # It's okay if some files don't exist, just means the test ends there.
            logging.warning(f"Narrative question file not found: {file_path}")
        except Exception as e:
            logging.error(f"An error occurred while loading {file_path}: {e}")
            
    return mazen_texts, mazen_srd

def load_all_questions():
    levels = ['easy', 'medium', 'hard']
    question_sets = {}
    
    for level in levels:
        file_path = f"{level.capitalize()}_Level.csv"
        try:
            df = pd.read_csv(file_path, encoding='utf-8', dtype={'Correct_Answer': str})
            questions = []
            for index, row in df.iterrows():
                try:
                    correct_option_str = row['Correct_Answer'].replace('Option_', '')
                    correct_index = ['A', 'B', 'C', 'D'].index(correct_option_str)
                    questions.append({
                        "q": row['Question'],
                        "options": [row['Option_A'], row['Option_B'], row['Option_C'], row['Option_D']],
                        "correct": correct_index,
                        "expl": row['Explanation_Feedback']
                    })
                except Exception as e:
                    logging.error(f"Error processing row {index+2} in {file_path}: {e}")
            question_sets[level] = questions
            logging.info(f"Successfully loaded {len(questions)} questions for level: {level}")
        except FileNotFoundError:
            logging.error(f"Error: The file {file_path} was not found.")
            question_sets[level] = []
        except Exception as e:
            logging.error(f"An error occurred while loading {file_path}: {e}")
            question_sets[level] = []

    # Load video 1 quiz
    try:
        video1_file_path = os.path.join('video1', 'exam.csv')
        df = pd.read_csv(video1_file_path, encoding='utf-8')
        questions = []
        for index, row in df.iterrows():
            try:
                options = [row['option_a'], row['option_b'], row['option_c'], row['option_d']]
                correct_answer_text = row['correct_answer']
                correct_index = options.index(correct_answer_text)

                option_explanations = [
                    row.get('explanation_a', ''),
                    row.get('explanation_b', ''),
                    row.get('explanation_c', ''),
                    row.get('explanation_d', '')
                ]
                
                questions.append({
                    "q": row['question'],
                    "options": options,
                    "correct": correct_index,
                    "expl": row.get('correct_explanation', ''),
                    "idea_expl": row.get('concept_explanation', ''),
                    "option_explanations": option_explanations
                })
            except Exception as e:
                logging.error(f"Error processing row {index+2} in {video1_file_path}: {e}")
        question_sets['video1'] = questions
        logging.info(f"Successfully loaded {len(questions)} questions for level: video1")
    except FileNotFoundError:
        logging.error(f"Error: The file {video1_file_path} was not found.")
        question_sets['video1'] = []
    except Exception as e:
        logging.error(f"An error occurred while loading {video1_file_path}: {e}")
        question_sets['video1'] = []

    # Load video 2 mini quiz
    try:
        video2_mini_file_path = os.path.join('video2', 'mini_exam.csv')
        df = pd.read_csv(video2_mini_file_path, encoding='utf-8')
        questions = []
        option_cols = ['option_a', 'option_b', 'option_c', 'option_d']
        for index, row in df.iterrows():
            try:
                options = [row['option_a'], row['option_b'], row['option_c'], row['option_d']]
                correct_answer_col_name = row['correct_answer']
                correct_index = option_cols.index(correct_answer_col_name)

                option_explanations = [
                    row.get('explanation_a', ''),
                    row.get('explanation_b', ''),
                    row.get('explanation_c', ''),
                    row.get('explanation_d', '')
                ]
                
                questions.append({
                    "q": row['question'],
                    "options": options,
                    "correct": correct_index,
                    "expl": row.get('correct_explanation', ''),
                    "idea_expl": row.get('concept_explanation', ''),
                    "option_explanations": option_explanations
                })
            except Exception as e:
                logging.error(f"Error processing row {index+2} in {video2_mini_file_path}: {e}")
        question_sets['video2_mini'] = questions
        logging.info(f"Successfully loaded {len(questions)} questions for level: video2_mini")
    except FileNotFoundError:
        # This is expected for now for the main video2 quiz
        question_sets['video2_mini'] = []
    except Exception as e:
        logging.error(f"An error occurred while loading {video2_mini_file_path}: {e}")
        question_sets['video2_mini'] = []

    # Load video 2 main quiz
    try:
        video2_file_path = os.path.join('video2', 'exam.csv')
        df = pd.read_csv(video2_file_path, encoding='utf-8')
        questions = []
        option_cols = ['option_a', 'option_b', 'option_c', 'option_d']
        for index, row in df.iterrows():
            try:
                options = [row['option_a'], row['option_b'], row['option_c'], row['option_d']]
                correct_answer_col_name = row['correct_answer']
                correct_index = option_cols.index(correct_answer_col_name)

                option_explanations = [
                    row.get('explanation_a', ''),
                    row.get('explanation_b', ''),
                    row.get('explanation_c', ''),
                    row.get('explanation_d', '')
                ]
                
                questions.append({
                    "q": row['question'],
                    "options": options,
                    "correct": correct_index,
                    "expl": row.get('correct_explanation', ''),
                    "idea_expl": row.get('concept_explanation', ''),
                    "option_explanations": option_explanations
                })
            except Exception as e:
                logging.error(f"Error processing row {index+2} in {video2_file_path}: {e}")
        question_sets['video2'] = questions
        logging.info(f"Successfully loaded {len(questions)} questions for level: video2")
    except FileNotFoundError:
        question_sets['video2'] = []
    except Exception as e:
        logging.error(f"An error occurred while loading {video2_file_path}: {e}")
        question_sets['video2'] = []

    # Load video 3 main quiz
    try:
        video3_file_path = os.path.join('video3', 'exam.csv')
        df = pd.read_csv(video3_file_path, encoding='utf-8')
        questions = []
        option_cols = ['option_a', 'option_b', 'option_c', 'option_d']
        for index, row in df.iterrows():
            try:
                options = [row['option_a'], row['option_b'], row['option_c'], row['option_d']]
                correct_answer_col_name = row['correct_answer']
                correct_index = option_cols.index(correct_answer_col_name)

                option_explanations = [
                    row.get('explanation_a', ''),
                    row.get('explanation_b', ''),
                    row.get('explanation_c', ''),
                    row.get('explanation_d', '')
                ]
                
                questions.append({
                    "q": row['question'],
                    "options": options,
                    "correct": correct_index,
                    "expl": row.get('correct_explanation', ''),
                    "idea_expl": row.get('concept_explanation', ''),
                    "option_explanations": option_explanations
                })
            except Exception as e:
                logging.error(f"Error processing row {index+2} in {video3_file_path}: {e}")
        question_sets['video3'] = questions
        logging.info(f"Successfully loaded {len(questions)} questions for level: video3")
    except FileNotFoundError:
        question_sets['video3'] = []
    except Exception as e:
        logging.error(f"An error occurred while loading {video3_file_path}: {e}")
        question_sets['video3'] = []

    # Load video 4 main quiz
    try:
        video4_file_path = os.path.join('video4', 'exam.csv')
        df = pd.read_csv(video4_file_path, encoding='utf-8')
        questions = []
        option_cols = ['option_a', 'option_b', 'option_c', 'option_d']
        for index, row in df.iterrows():
            try:
                options = [row['option_a'], row['option_b'], row['option_c'], row['option_d']]
                correct_answer_col_name = row['correct_answer']
                correct_index = option_cols.index(correct_answer_col_name)

                option_explanations = [
                    row.get('explanation_a', ''),
                    row.get('explanation_b', ''),
                    row.get('explanation_c', ''),
                    row.get('explanation_d', '')
                ]
                
                questions.append({
                    "q": row['question'],
                    "options": options,
                    "correct": correct_index,
                    "expl": row.get('correct_explanation', ''),
                    "idea_expl": row.get('concept_explanation', ''),
                    "option_explanations": option_explanations
                })
            except Exception as e:
                logging.error(f"Error processing row {index+2} in {video4_file_path}: {e}")
        question_sets['video4'] = questions
        logging.info(f"Successfully loaded {len(questions)} questions for level: video4")
    except FileNotFoundError:
        question_sets['video4'] = []
    except Exception as e:
        logging.error(f"An error occurred while loading {video4_file_path}: {e}")
        question_sets['video4'] = []
        
    # Load Mazen test multiple choice quizzes
    for i in range(1, 7): # Assuming ids 1 to 6
        file_path = f"id{i}.csv"
        level_name = f"mazin_id{i}"
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, usecols=range(12), keep_default_na=False)
            df.columns = [str(c).strip().lower() for c in df.columns]
            questions = []
            for index, row in df.iterrows():
                try:
                    options = [str(row.get('option_a', '')), str(row.get('option_b', '')), str(row.get('option_c', '')), str(row.get('option_d', ''))]
                    correct_option_char = str(row.get('correct_answer', '')).strip().upper()
                    # Try to extract first character if it's a full text
                    if len(correct_option_char) > 1:
                        # If it's a full text, try to find which option matches
                        option_texts = [
                            str(row.get('option_a', '')).strip(),
                            str(row.get('option_b', '')).strip(),
                            str(row.get('option_c', '')).strip(),
                            str(row.get('option_d', '')).strip()
                        ]
                        # Try to find matching option
                        correct_index = None
                        for idx, opt_text in enumerate(option_texts):
                            if opt_text == correct_option_char or opt_text.upper() == correct_option_char:
                                correct_index = idx
                                break
                        # If not found, try first character
                        if correct_index is None and len(correct_option_char) > 0:
                            first_char = correct_option_char[0]
                            if first_char in ['A', 'B', 'C', 'D']:
                                correct_index = ['A', 'B', 'C', 'D'].index(first_char)
                        if correct_index is None:
                            raise ValueError(f"Could not determine correct answer from: {correct_option_char}")
                    else:
                        correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char)

                    option_explanations = [
                        str(row.get('explanation_a', '')),
                        str(row.get('explanation_b', '')),
                        str(row.get('explanation_c', '')),
                        str(row.get('explanation_d', ''))
                    ]
                    
                    questions.append({
                        "q": str(row.get('question', '')),
                        "options": options,
                        "correct": correct_index,
                        "expl": str(row.get('correct_explanation', '')),
                        "idea_expl": str(row.get('concept_explanation', '')),
                        "option_explanations": option_explanations
                    })
                except Exception as e:
                    logging.error(f"Error processing row {index+2} in {file_path}: {e}")
            question_sets[level_name] = questions
            logging.info(f"Successfully loaded {len(questions)} questions for Mazen test level: {level_name}")
        except FileNotFoundError:
            logging.warning(f"Mazen test quiz file not found: {file_path}")
            question_sets[level_name] = []
        except KeyError as e:
            logging.error(f"Column {e} not found in {file_path}. Check CSV format.")
            question_sets[level_name] = []
        except Exception as e:
            logging.error(f"An error occurred while loading {file_path}: {e}")
            question_sets[level_name] = []
            
    return question_sets

# ------------------- إدارة الرسائل التوضيحية (تنظيف عند بدء الاختبار) -------------------

def add_cleanup_msg(context: ContextTypes.DEFAULT_TYPE, msg_id: int | None):
    if not msg_id:
        return
    bucket = context.user_data.setdefault('cleanup_msgs', [])
    if msg_id not in bucket:
        bucket.append(msg_id)

async def clear_cleanup_msgs(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    bucket = context.user_data.pop('cleanup_msgs', [])
    for mid in bucket:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            continue
# ------------------- إدارة القوائم الديناميكية -------------------

def default_menus():
    return {
        "main_menu": {
            "columns": 2,
            "buttons": [
                {"text": "اختبار مذاكرة أ.مازن", "callback": "mazin_test"},
                {"text": "إختبارات سابقة 📚", "callback": "previous_tests"}
            ]
        }
    }

def load_menus(conn=None):
    """Load menus from database if conn provided, otherwise from JSON file."""
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT menu_data FROM menus WHERE menu_id = 'main_menu'")
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    if "main_menu" in data:
                        logging.info("Loaded menus from database")
                        return data
                except Exception as e:
                    logging.error(f"Failed to parse menus from database: {e}")
        except Exception as e:
            logging.error(f"Failed to load menus from database: {e}")
    
    # Fallback to JSON file
    if os.path.exists(MENUS_FILE):
        try:
            with open(MENUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "main_menu" in data:
                    # Migrate to database if conn is available
                    if conn:
                        try:
                            save_menus(data, conn)
                            logging.info("Migrated menus from JSON to database")
                        except Exception as e:
                            logging.error(f"Failed to migrate menus to database: {e}")
                    return data
        except Exception as e:
            logging.error(f"Failed to load menus.json: {e}")
    return default_menus()

def save_menus(menus, conn=None):
    """Save menus to database if conn provided, otherwise to JSON file."""
    if conn:
        try:
            from datetime import datetime
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            menu_data = json.dumps(menus, ensure_ascii=False, indent=2)
            
            cursor.execute('''
                INSERT OR REPLACE INTO menus (menu_id, menu_data, updated_at)
                VALUES (?, ?, ?)
            ''', ('main_menu', menu_data, now))
            conn.commit()
            logging.info("Saved menus to database")
            return
        except Exception as e:
            logging.error(f"Failed to save menus to database: {e}")
    
    # Fallback to JSON file
    try:
        with open(MENUS_FILE, "w", encoding="utf-8") as f:
            json.dump(menus, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save menus.json: {e}")

# ------------------- Dynamic Exams Management -------------------

def default_exams():
    return {}

def load_exams(conn=None):
    """Load exams from database if conn provided, otherwise from JSON file."""
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT exam_id, button_text, question_type, explanation_file, explanation_file_id, mcq_file, mcq_file_id, narrative_file, narrative_file_id, mcq_files_by_id, mcq_file_ids_by_id, narrative_files_by_id, narrative_file_ids_by_id, media_attachments, is_hidden FROM dynamic_exams")
            rows = cursor.fetchall()
            exams = {}
            for row in rows:
                exam_id = row[0]
                exam = {
                    "button_text": row[1],
                    "question_type": row[2],
                }
                if row[3]:  # explanation_file
                    exam["explanation_file"] = row[3]
                if row[4]:  # explanation_file_id
                    exam["explanation_file_id"] = row[4]
                if row[5]:  # mcq_file
                    exam["mcq_file"] = row[5]
                if row[6]:  # mcq_file_id
                    exam["mcq_file_id"] = row[6]
                if row[7]:  # narrative_file
                    exam["narrative_file"] = row[7]
                if row[8]:  # narrative_file_id
                    exam["narrative_file_id"] = row[8]
                if row[9]:  # mcq_files_by_id (JSON string)
                    try:
                        exam["mcq_files_by_id"] = json.loads(row[9]) if row[9] else {}
                    except:
                        exam["mcq_files_by_id"] = {}
                if row[10]:  # mcq_file_ids_by_id (JSON string)
                    try:
                        exam["mcq_file_ids_by_id"] = json.loads(row[10]) if row[10] else {}
                    except:
                        exam["mcq_file_ids_by_id"] = {}
                if row[11]:  # narrative_files_by_id (JSON string)
                    try:
                        exam["narrative_files_by_id"] = json.loads(row[11]) if row[11] else {}
                    except:
                        exam["narrative_files_by_id"] = {}
                if row[12]:  # narrative_file_ids_by_id (JSON string)
                    try:
                        exam["narrative_file_ids_by_id"] = json.loads(row[12]) if row[12] else {}
                    except:
                        exam["narrative_file_ids_by_id"] = {}
                if row[13]:  # media_attachments (JSON string)
                    try:
                        exam["media_attachments"] = json.loads(row[13]) if row[13] else {}
                    except:
                        exam["media_attachments"] = {}
                if row[14]:  # is_hidden
                    exam["is_hidden"] = bool(row[14])
                exams[exam_id] = exam
            if exams:
                logging.info(f"Loaded {len(exams)} exams from database")
                return exams
        except Exception as e:
            logging.error(f"Failed to load exams from database: {e}")
    
    # Fallback to JSON file
    if os.path.exists(EXAMS_FILE):
        try:
            with open(EXAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # If we have conn, migrate data to database
                if conn and data:
                    migrate_exams_to_db(conn, data)
                return data
        except Exception as e:
            logging.error(f"Failed to load exams.json: {e}")
    return default_exams()

def save_exams(exams, conn=None):
    """Save exams to database if conn provided, otherwise to JSON file."""
    if conn:
        try:
            from datetime import datetime
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            for exam_id, exam_data in exams.items():
                # Convert dict fields to JSON strings
                mcq_files_by_id = json.dumps(exam_data.get("mcq_files_by_id", {}), ensure_ascii=False)
                mcq_file_ids_by_id = json.dumps(exam_data.get("mcq_file_ids_by_id", {}), ensure_ascii=False)
                narrative_files_by_id = json.dumps(exam_data.get("narrative_files_by_id", {}), ensure_ascii=False)
                narrative_file_ids_by_id = json.dumps(exam_data.get("narrative_file_ids_by_id", {}), ensure_ascii=False)
                media_attachments = json.dumps(exam_data.get("media_attachments", {}), ensure_ascii=False)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO dynamic_exams 
                    (exam_id, button_text, question_type, explanation_file, explanation_file_id, 
                     mcq_file, mcq_file_id, narrative_file, narrative_file_id, 
                     mcq_files_by_id, mcq_file_ids_by_id, narrative_files_by_id, narrative_file_ids_by_id, 
                     media_attachments, is_hidden, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    exam_id,
                    exam_data.get("button_text", ""),
                    exam_data.get("question_type", "narrative"),
                    exam_data.get("explanation_file"),
                    exam_data.get("explanation_file_id"),
                    exam_data.get("mcq_file"),
                    exam_data.get("mcq_file_id"),
                    exam_data.get("narrative_file"),
                    exam_data.get("narrative_file_id"),
                    mcq_files_by_id,
                    mcq_file_ids_by_id,
                    narrative_files_by_id,
                    narrative_file_ids_by_id,
                    media_attachments,
                    1 if exam_data.get("is_hidden", False) else 0,
                    now
                ))
            conn.commit()
            logging.info(f"Saved {len(exams)} exams to database")
            return
        except Exception as e:
            logging.error(f"Failed to save exams to database: {e}")
    
    # Fallback to JSON file
    try:
        with open(EXAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(exams, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save exams.json: {e}")

def migrate_exams_to_db(conn, exams_data):
    """Migrate exams from JSON to database."""
    try:
        from datetime import datetime
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        for exam_id, exam_data in exams_data.items():
            # Check if exam already exists in database
            cursor.execute("SELECT exam_id FROM dynamic_exams WHERE exam_id = ?", (exam_id,))
            if cursor.fetchone():
                continue  # Skip if already exists
            
            # Convert dict fields to JSON strings
            mcq_files_by_id = json.dumps(exam_data.get("mcq_files_by_id", {}), ensure_ascii=False)
            narrative_files_by_id = json.dumps(exam_data.get("narrative_files_by_id", {}), ensure_ascii=False)
            media_attachments = json.dumps(exam_data.get("media_attachments", {}), ensure_ascii=False)
            
            cursor.execute('''
                INSERT INTO dynamic_exams 
                (exam_id, button_text, question_type, explanation_file, explanation_file_id, 
                 mcq_file, mcq_file_id, narrative_file, narrative_file_id, 
                 mcq_files_by_id, narrative_files_by_id, media_attachments, is_hidden, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                exam_id,
                exam_data.get("button_text", ""),
                exam_data.get("question_type", "narrative"),
                exam_data.get("explanation_file"),
                exam_data.get("explanation_file_id"),
                exam_data.get("mcq_file"),
                exam_data.get("mcq_file_id"),
                exam_data.get("narrative_file"),
                exam_data.get("narrative_file_id"),
                mcq_files_by_id,
                narrative_files_by_id,
                media_attachments,
                1 if exam_data.get("is_hidden", False) else 0,
                now,
                now
            ))
        conn.commit()
        logging.info(f"Migrated {len(exams_data)} exams from JSON to database")
    except Exception as e:
        logging.error(f"Failed to migrate exams to database: {e}")

def get_db_conn_from_context(context):
    """Helper function to get db_conn from context if available."""
    if context and hasattr(context, 'bot_data') and 'db_conn' in context.bot_data:
        return context.bot_data['db_conn']
    return None

async def load_csv_from_telegram(bot, file_id, file_path=None):
    """Load CSV file from Telegram using file_id. If file_path is provided, also save to disk.
    Returns pandas DataFrame or None if error."""
    try:
        from io import BytesIO
        file = await bot.get_file(file_id)
        file_buffer = BytesIO()
        await file.download_to_memory(file_buffer)
        file_buffer.seek(0)
        
        # Read CSV from buffer first
        import pandas as pd
        df = pd.read_csv(file_buffer, encoding='utf-8')
        
        # Also save to disk if file_path is provided (after reading to avoid issues)
        if file_path:
            file_buffer.seek(0)
            os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else '.', exist_ok=True)
            with open(file_path, 'wb') as f:
                f.write(file_buffer.read())
        
        return df
    except Exception as e:
        logging.error(f"Error loading CSV from Telegram (file_id: {file_id}): {e}")
        return None

async def load_dynamic_exam(exam_id, conn=None, bot=None):
    """Load a dynamic exam's data from CSV files.
    Tries to load from disk first, then from Telegram using file_id if available.
    bot parameter is optional - if provided, will try to load missing files from Telegram."""
    debug_log("load_dynamic_exam", "Function called", {"exam_id": exam_id, "has_conn": conn is not None, "has_bot": bot is not None}, "G")
    exams = load_exams(conn)
    exam = exams.get(exam_id)
    if not exam:
        debug_log("load_dynamic_exam", "Exam not found in exams", {"exam_id": exam_id, "available_exams": list(exams.keys())}, "G")
        return None, None, None
    
    debug_log("load_dynamic_exam", "Exam found", {
        "exam_id": exam_id,
        "button_text": exam.get("button_text"),
        "question_type": exam.get("question_type"),
        "explanation_file": exam.get("explanation_file"),
        "mcq_file": exam.get("mcq_file"),
        "narrative_file": exam.get("narrative_file"),
        "mcq_files_by_id": exam.get("mcq_files_by_id"),
        "narrative_files_by_id": exam.get("narrative_files_by_id")
    }, "G")
    
    exam_data = {
        "texts": {},
        "mcq_questions": [],
        "narrative_questions": []
    }
    
    # Load explanation texts (similar to textlevels.csv)
    # Method 1: Try loading from disk
    explanation_file = exam.get("explanation_file")
    explanation_file_id = exam.get("explanation_file_id")
    debug_log("load_dynamic_exam", "Checking explanation file", {
        "explanation_file": explanation_file,
        "explanation_file_id": explanation_file_id,
        "file_exists": os.path.exists(explanation_file) if explanation_file else False
    }, "G")
    
    explanation_loaded = False
    if explanation_file and os.path.exists(explanation_file):
        try:
            df = pd.read_csv(explanation_file, encoding='utf-8')
            df.columns = [c.strip().lower() for c in df.columns]
            for _, row in df.iterrows():
                id_val = row.get('id', 1)
                level = row.get('level', 1)
                text = row.get('text', '')
                logging.info(f"Loading text: id={id_val}, level={level}, text_length={len(text)}")
                if id_val not in exam_data["texts"]:
                    exam_data["texts"][id_val] = {}
                exam_data["texts"][id_val][level] = text
            logging.info(f"Loaded explanation texts for exam {exam_id} from disk: {exam_data['texts']}")
            explanation_loaded = True
        except Exception as e:
            logging.error(f"Error loading explanation file from disk for exam {exam_id}: {e}")
    
    # Method 2: If file not found on disk, try loading from Telegram using file_id
    if not explanation_loaded and explanation_file_id and bot:
        logging.info(f"Explanation file not found on disk, trying to load from Telegram using file_id: {explanation_file_id}")
        try:
            df = await load_csv_from_telegram(bot, explanation_file_id, explanation_file)
            if df is not None:
                df.columns = [c.strip().lower() for c in df.columns]
                for _, row in df.iterrows():
                    id_val = row.get('id', 1)
                    level = row.get('level', 1)
                    text = row.get('text', '')
                    logging.info(f"Loading text from Telegram: id={id_val}, level={level}, text_length={len(text)}")
                    if id_val not in exam_data["texts"]:
                        exam_data["texts"][id_val] = {}
                    exam_data["texts"][id_val][level] = text
                logging.info(f"Loaded explanation texts for exam {exam_id} from Telegram: {exam_data['texts']}")
                explanation_loaded = True
        except Exception as e:
            logging.error(f"Error loading explanation file from Telegram for exam {exam_id}: {e}")
    elif not explanation_loaded and explanation_file_id:
        logging.warning(f"Explanation file not found on disk for exam {exam_id}, file_id available: {explanation_file_id}, but bot not provided for Telegram download")
    
    # Load questions based on type - supports both MCQ and Narrative like Mazen test
    question_type = exam.get("question_type", "narrative")  # "mcq", "narrative", or "both"
    debug_log("load_dynamic_exam", "Question type", {"question_type": question_type}, "G")
    
    # Load MCQ questions if available - support both single file and files by ID
    mcq_files_by_id = exam.get("mcq_files_by_id", {})
    debug_log("load_dynamic_exam", "MCQ files by ID", {"mcq_files_by_id": mcq_files_by_id}, "G")
    if mcq_files_by_id:
        # Load MCQ questions for each ID
        mcq_file_ids_by_id = exam.get("mcq_file_ids_by_id", {})
        for question_id, mcq_file in mcq_files_by_id.items():
            debug_log("load_dynamic_exam", "Checking MCQ file", {
                "question_id": question_id,
                "mcq_file": mcq_file,
                "file_exists": os.path.exists(mcq_file) if mcq_file else False
            }, "G")
            mcq_file_id = mcq_file_ids_by_id.get(question_id) if mcq_file_ids_by_id else None
            
            # Method 1: Try loading from disk
            if mcq_file and os.path.exists(mcq_file):
                try:
                    df = pd.read_csv(mcq_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, usecols=range(12), keep_default_na=False)
                    df.columns = [str(c).strip().lower() for c in df.columns]
                    for index, row in df.iterrows():
                        try:
                            options = [
                                str(row.get('option_a', '')),
                                str(row.get('option_b', '')),
                                str(row.get('option_c', '')),
                                str(row.get('option_d', ''))
                            ]
                            correct_option_char = str(row.get('correct_answer', '')).strip().upper()
                            # Try to extract first character if it's a full text
                            if len(correct_option_char) > 1:
                                # If it's a full text, try to find which option matches
                                option_texts = [
                                    str(row.get('option_a', '')).strip(),
                                    str(row.get('option_b', '')).strip(),
                                    str(row.get('option_c', '')).strip(),
                                    str(row.get('option_d', '')).strip()
                                ]
                                # Try to find matching option
                                correct_index = None
                                for idx, opt_text in enumerate(option_texts):
                                    if opt_text == correct_option_char or opt_text.upper() == correct_option_char:
                                        correct_index = idx
                                        break
                                # If not found, try first character
                                if correct_index is None and len(correct_option_char) > 0:
                                    first_char = correct_option_char[0]
                                    if first_char in ['A', 'B', 'C', 'D']:
                                        correct_index = ['A', 'B', 'C', 'D'].index(first_char)
                                if correct_index is None:
                                    raise ValueError(f"Could not determine correct answer from: {correct_option_char}")
                            else:
                                correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char)
                            
                            option_explanations = [
                                str(row.get('explanation_a', '')),
                                str(row.get('explanation_b', '')),
                                str(row.get('explanation_c', '')),
                                str(row.get('explanation_d', ''))
                            ]
                            
                            exam_data["mcq_questions"].append({
                                "q": str(row.get('question', '')),
                                "options": options,
                                "correct": correct_index,
                                "expl": str(row.get('correct_explanation', '')),
                                "idea_expl": str(row.get('concept_explanation', '')),
                                "option_explanations": option_explanations,
                                "id": question_id  # Store which ID this question belongs to
                            })
                        except Exception as e:
                            logging.error(f"Error processing MCQ row {index+2} in {mcq_file}: {e}")
                    logging.info(f"Loaded MCQ questions for ID {question_id} from {mcq_file}")
                except Exception as e:
                    logging.error(f"Error loading MCQ file {mcq_file} for ID {question_id}: {e}")
            # Method 2: If file not found on disk, try loading from Telegram using file_id
            elif mcq_file_id and bot:
                logging.info(f"MCQ file not found on disk for ID {question_id}, trying to load from Telegram using file_id: {mcq_file_id}")
                try:
                    df = await load_csv_from_telegram(bot, mcq_file_id, mcq_file)
                    if df is not None:
                        df.columns = [str(c).strip().lower() for c in df.columns]
                        for index, row in df.iterrows():
                            try:
                                options = [
                                    str(row.get('option_a', '')),
                                    str(row.get('option_b', '')),
                                    str(row.get('option_c', '')),
                                    str(row.get('option_d', ''))
                                ]
                                correct_option_char = str(row.get('correct_answer', '')).strip().upper()
                                # Try to extract first character if it's a full text
                                if len(correct_option_char) > 1:
                                    # If it's a full text, try to find which option matches
                                    option_texts = [
                                        str(row.get('option_a', '')).strip(),
                                        str(row.get('option_b', '')).strip(),
                                        str(row.get('option_c', '')).strip(),
                                        str(row.get('option_d', '')).strip()
                                    ]
                                    # Try to find matching option
                                    correct_index = None
                                    for idx, opt_text in enumerate(option_texts):
                                        if opt_text == correct_option_char or opt_text.upper() == correct_option_char:
                                            correct_index = idx
                                            break
                                    # If not found, try first character
                                    if correct_index is None and len(correct_option_char) > 0:
                                        first_char = correct_option_char[0]
                                        if first_char in ['A', 'B', 'C', 'D']:
                                            correct_index = ['A', 'B', 'C', 'D'].index(first_char)
                                    if correct_index is None:
                                        raise ValueError(f"Could not determine correct answer from: {correct_option_char}")
                                else:
                                    correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char)
                                
                                option_explanations = [
                                    str(row.get('explanation_a', '')),
                                    str(row.get('explanation_b', '')),
                                    str(row.get('explanation_c', '')),
                                    str(row.get('explanation_d', ''))
                                ]
                                
                                exam_data["mcq_questions"].append({
                                    "q": str(row.get('question', '')),
                                    "options": options,
                                    "correct": correct_index,
                                    "expl": str(row.get('correct_explanation', '')),
                                    "idea_expl": str(row.get('concept_explanation', '')),
                                    "option_explanations": option_explanations,
                                    "id": question_id
                                })
                            except Exception as e:
                                logging.error(f"Error processing MCQ row {index+2} from Telegram: {e}")
                        logging.info(f"Loaded MCQ questions for ID {question_id} from Telegram")
                except Exception as e:
                    logging.error(f"Error loading MCQ file from Telegram for ID {question_id}: {e}")
            elif mcq_file_id:
                logging.warning(f"MCQ file not found on disk for ID {question_id}, file_id available: {mcq_file_id}, but bot not provided for Telegram download")
    
    mcq_file = exam.get("mcq_file")
    mcq_file_id = exam.get("mcq_file_id")
    debug_log("load_dynamic_exam", "Checking single MCQ file", {
        "mcq_file": mcq_file,
        "mcq_file_id": mcq_file_id,
        "file_exists": os.path.exists(mcq_file) if mcq_file else False
    }, "G")
    # Method 1: Try loading from disk
    if mcq_file and os.path.exists(mcq_file):
        try:
            df = pd.read_csv(mcq_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, usecols=range(12), keep_default_na=False)
            df.columns = [str(c).strip().lower() for c in df.columns]
            for index, row in df.iterrows():
                try:
                    options = [
                        str(row.get('option_a', '')),
                        str(row.get('option_b', '')),
                        str(row.get('option_c', '')),
                        str(row.get('option_d', ''))
                    ]
                    correct_option_char = str(row.get('correct_answer', '')).strip().upper()
                    # Try to extract first character if it's a full text
                    if len(correct_option_char) > 1:
                        # If it's a full text, try to find which option matches
                        option_texts = [
                            str(row.get('option_a', '')).strip(),
                            str(row.get('option_b', '')).strip(),
                            str(row.get('option_c', '')).strip(),
                            str(row.get('option_d', '')).strip()
                        ]
                        # Try to find matching option
                        correct_index = None
                        for idx, opt_text in enumerate(option_texts):
                            if opt_text == correct_option_char or opt_text.upper() == correct_option_char:
                                correct_index = idx
                                break
                        # If not found, try first character
                        if correct_index is None and len(correct_option_char) > 0:
                            first_char = correct_option_char[0]
                            if first_char in ['A', 'B', 'C', 'D']:
                                correct_index = ['A', 'B', 'C', 'D'].index(first_char)
                        if correct_index is None:
                            raise ValueError(f"Could not determine correct answer from: {correct_option_char}")
                    else:
                        correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char)
                    
                    option_explanations = [
                        str(row.get('explanation_a', '')),
                        str(row.get('explanation_b', '')),
                        str(row.get('explanation_c', '')),
                        str(row.get('explanation_d', ''))
                    ]
                    
                    exam_data["mcq_questions"].append({
                        "q": str(row.get('question', '')),
                        "options": options,
                        "correct": correct_index,
                        "expl": str(row.get('correct_explanation', '')),
                        "idea_expl": str(row.get('concept_explanation', '')),
                        "option_explanations": option_explanations,
                        "id": 1  # Default ID for single file
                    })
                except Exception as e:
                    logging.error(f"Error processing MCQ row {index+2} in {mcq_file}: {e}")
            logging.info(f"Loaded {len(exam_data['mcq_questions'])} MCQ questions from disk for exam {exam_id}")
        except Exception as e:
            logging.error(f"Error loading MCQ file from disk for exam {exam_id}: {e}")
    # Method 2: If file not found on disk, try loading from Telegram using file_id
    elif mcq_file_id and bot:
        logging.info(f"MCQ file not found on disk, trying to load from Telegram using file_id: {mcq_file_id}")
        try:
            df = await load_csv_from_telegram(bot, mcq_file_id, mcq_file)
            if df is not None:
                df.columns = [str(c).strip().lower() for c in df.columns]
                for index, row in df.iterrows():
                    try:
                        options = [
                            str(row.get('option_a', '')),
                            str(row.get('option_b', '')),
                            str(row.get('option_c', '')),
                            str(row.get('option_d', ''))
                        ]
                        correct_option_char = str(row.get('correct_answer', '')).strip()
                        correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char.upper())
                        
                        option_explanations = [
                            str(row.get('explanation_a', '')),
                            str(row.get('explanation_b', '')),
                            str(row.get('explanation_c', '')),
                            str(row.get('explanation_d', ''))
                        ]
                        
                        exam_data["mcq_questions"].append({
                            "q": str(row.get('question', '')),
                            "options": options,
                            "correct": correct_index,
                            "expl": str(row.get('correct_explanation', '')),
                            "idea_expl": str(row.get('concept_explanation', '')),
                            "option_explanations": option_explanations,
                            "id": 1
                        })
                    except Exception as e:
                        logging.error(f"Error processing MCQ row {index+2} from Telegram: {e}")
                logging.info(f"Loaded {len(exam_data['mcq_questions'])} MCQ questions from Telegram for exam {exam_id}")
        except Exception as e:
            logging.error(f"Error loading MCQ file from Telegram for exam {exam_id}: {e}")
    elif mcq_file_id:
        logging.warning(f"MCQ file not found on disk for exam {exam_id}, file_id available: {mcq_file_id}, but bot not provided for Telegram download")
    
    # Load narrative questions if available - support both single file and files by ID
    narrative_files_by_id = exam.get("narrative_files_by_id", {})
    debug_log("load_dynamic_exam", "Narrative files by ID", {"narrative_files_by_id": narrative_files_by_id}, "G")
    if narrative_files_by_id:
        # Load narrative questions for each ID
        for question_id, narrative_file in narrative_files_by_id.items():
            debug_log("load_dynamic_exam", "Checking narrative file", {
                "question_id": question_id,
                "narrative_file": narrative_file,
                "file_exists": os.path.exists(narrative_file) if narrative_file else False
            }, "G")
            narrative_file_ids_by_id = exam.get("narrative_file_ids_by_id", {})
            narrative_file_id = narrative_file_ids_by_id.get(question_id) if narrative_file_ids_by_id else None
            
            # Method 1: Try loading from disk
            if narrative_file and os.path.exists(narrative_file):
                try:
                    df = pd.read_csv(narrative_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, keep_default_na=False)
                    df.columns = [str(c).strip().lower() for c in df.columns]
                    for _, row in df.iterrows():
                        exam_data["narrative_questions"].append({
                            "question": row.get('question', ''),
                            "answer": row.get('answer', ''),
                            "id": question_id  # Store which ID this question belongs to
                        })
                    logging.info(f"Loaded narrative questions for ID {question_id} from disk: {narrative_file}")
                except Exception as e:
                    logging.error(f"Error loading narrative file from disk {narrative_file} for ID {question_id}: {e}")
            # Method 2: If file not found on disk, try loading from Telegram using file_id
            elif narrative_file_id and bot:
                logging.info(f"Narrative file not found on disk for ID {question_id}, trying to load from Telegram using file_id: {narrative_file_id}")
                try:
                    df = await load_csv_from_telegram(bot, narrative_file_id, narrative_file)
                    if df is not None:
                        df.columns = [str(c).strip().lower() for c in df.columns]
                        for _, row in df.iterrows():
                            exam_data["narrative_questions"].append({
                                "question": row.get('question', ''),
                                "answer": row.get('answer', ''),
                                "id": question_id
                            })
                        logging.info(f"Loaded narrative questions for ID {question_id} from Telegram")
                except Exception as e:
                    logging.error(f"Error loading narrative file from Telegram for ID {question_id}: {e}")
            elif narrative_file_id:
                logging.warning(f"Narrative file not found on disk for ID {question_id}, file_id available: {narrative_file_id}, but bot not provided for Telegram download")
    
    narrative_file = exam.get("narrative_file")
    narrative_file_id = exam.get("narrative_file_id")
    # Method 1: Try loading from disk
    if narrative_file and os.path.exists(narrative_file):
        try:
            df = pd.read_csv(narrative_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, keep_default_na=False)
            df.columns = [str(c).strip().lower() for c in df.columns]
            for _, row in df.iterrows():
                exam_data["narrative_questions"].append({
                    "question": row.get('question', ''),
                    "answer": row.get('answer', ''),
                    "id": 1  # Default ID for legacy files
                })
            logging.info(f"Loaded {len(exam_data['narrative_questions'])} narrative questions from disk for exam {exam_id}")
        except Exception as e:
            logging.error(f"Error loading narrative file from disk for exam {exam_id}: {e}")
    # Method 2: If file not found on disk, try loading from Telegram using file_id
    elif narrative_file_id and bot:
        logging.info(f"Narrative file not found on disk, trying to load from Telegram using file_id: {narrative_file_id}")
        try:
            df = await load_csv_from_telegram(bot, narrative_file_id, narrative_file)
            if df is not None:
                df.columns = [str(c).strip().lower() for c in df.columns]
                for _, row in df.iterrows():
                    exam_data["narrative_questions"].append({
                        "question": row.get('question', ''),
                        "answer": row.get('answer', ''),
                        "id": 1
                    })
                logging.info(f"Loaded {len(exam_data['narrative_questions'])} narrative questions from Telegram for exam {exam_id}")
        except Exception as e:
            logging.error(f"Error loading narrative file from Telegram for exam {exam_id}: {e}")
    elif narrative_file_id:
        logging.warning(f"Narrative file not found on disk for exam {exam_id}, file_id available: {narrative_file_id}, but bot not provided for Telegram download")
    
    # Legacy support: old format with single questions_file
    questions_file = exam.get("questions_file")
    if questions_file and os.path.exists(questions_file) and not mcq_file and not narrative_file:
        try:
            if question_type == "mcq":
                df = pd.read_csv(questions_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, usecols=range(12), keep_default_na=False)
                df.columns = [str(c).strip().lower() for c in df.columns]
                for index, row in df.iterrows():
                    try:
                        options = [str(row.get('option_a', '')), str(row.get('option_b', '')), str(row.get('option_c', '')), str(row.get('option_d', ''))]
                        correct_option_char = str(row.get('correct_answer', '')).strip()
                        correct_index = ['A', 'B', 'C', 'D'].index(correct_option_char.upper())
                        option_explanations = [str(row.get('explanation_a', '')), str(row.get('explanation_b', '')), str(row.get('explanation_c', '')), str(row.get('explanation_d', ''))]
                        exam_data["mcq_questions"].append({
                            "q": str(row.get('question', '')),
                            "options": options,
                            "correct": correct_index,
                            "expl": str(row.get('correct_explanation', '')),
                            "idea_expl": str(row.get('concept_explanation', '')),
                            "option_explanations": option_explanations
                        })
                    except Exception as e:
                        logging.error(f"Error processing MCQ row {index+2} in {questions_file}: {e}")
                logging.info(f"Loaded {len(exam_data['mcq_questions'])} MCQ questions for exam {exam_id}")
            else:
                df = pd.read_csv(questions_file, encoding='utf-8-sig', on_bad_lines='skip', dtype=str, keep_default_na=False)
                df.columns = [str(c).strip().lower() for c in df.columns]
                for _, row in df.iterrows():
                    exam_data["narrative_questions"].append({
                        "question": row.get('question', ''),
                        "answer": row.get('answer', '')
                    })
                logging.info(f"Loaded {len(exam_data['narrative_questions'])} narrative questions for exam {exam_id}")
        except Exception as e:
            logging.error(f"Error loading questions file for exam {exam_id}: {e}")
    
    narrative_questions_count = len(exam_data.get('narrative_questions', []))
    mcq_questions_count = len(exam_data.get('mcq_questions', []))
    texts_count = sum(len(levels) for levels in exam_data.get('texts', {}).values())
    debug_log("load_dynamic_exam", "Returning exam data", {
        "exam_id": exam_id,
        "exam_exists": exam is not None,
        "exam_data_exists": exam_data is not None,
        "texts_count": texts_count,
        "texts_keys": list(exam_data.get('texts', {}).keys()),
        "narrative_questions_count": narrative_questions_count,
        "mcq_questions_count": mcq_questions_count,
        "question_type": question_type
    }, "G")
    logging.info(f"load_dynamic_exam returning for {exam_id}: exam={exam is not None}, exam_data={exam_data is not None}, narrative_questions_count={narrative_questions_count}, mcq_questions_count={mcq_questions_count}")
    return exam, exam_data, question_type

# ------------------- دوال قاعدة البيانات -------------------

def init_db(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            difficulty TEXT,
            current_question INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            answers TEXT DEFAULT '{}',
            question_msg_id INTEGER,
            status_msg_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lab_results (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            video1 INTEGER DEFAULT 0,
            video2 INTEGER DEFAULT 0,
            video2_mini INTEGER DEFAULT 0,
            video3 INTEGER DEFAULT 0,
            video4 INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mazen_results (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            id1 INTEGER DEFAULT 0,
            id2 INTEGER DEFAULT 0,
            id3 INTEGER DEFAULT 0,
            id4 INTEGER DEFAULT 0,
            id5 INTEGER DEFAULT 0,
            id6 INTEGER DEFAULT 0
        )
    ''')
    # Table for best scores (retry support)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS best_scores (
            user_id INTEGER,
            difficulty TEXT,
            best_score INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_attempt_date TEXT,
            PRIMARY KEY (user_id, difficulty)
        )
    ''')
    # Table for badges/achievements
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_badges (
            user_id INTEGER,
            badge_id TEXT,
            earned_date TEXT,
            PRIMARY KEY (user_id, badge_id)
        )
    ''')
    # Table for detailed exam statistics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exam_statistics (
            exam_id TEXT,
            user_id INTEGER,
            score INTEGER,
            total_questions INTEGER,
            completion_date TEXT,
            time_taken INTEGER,
            PRIMARY KEY (exam_id, user_id, completion_date)
        )
    ''')
    # Table for dynamic exams configuration
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dynamic_exams (
            exam_id TEXT PRIMARY KEY,
            button_text TEXT NOT NULL,
            question_type TEXT,
            explanation_file TEXT,
            explanation_file_id TEXT,
            mcq_file TEXT,
            mcq_file_id TEXT,
            narrative_file TEXT,
            narrative_file_id TEXT,
            mcq_files_by_id TEXT,
            mcq_file_ids_by_id TEXT,
            narrative_files_by_id TEXT,
            narrative_file_ids_by_id TEXT,
            media_attachments TEXT,
            is_hidden INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    # Table for exam file IDs (for file-based storage migration to file_id)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exam_file_ids (
            exam_id TEXT,
            file_type TEXT,
            file_id TEXT,
            question_id TEXT,
            media_key TEXT,
            PRIMARY KEY (exam_id, file_type, question_id, media_key)
        )
    ''')
    # Table for menus (to persist menu changes)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS menus (
            menu_id TEXT PRIMARY KEY,
            menu_data TEXT NOT NULL,
            updated_at TEXT
        )
    ''')
    # Table for exam buttons without explanation
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exam_no_explanation_buttons (
            button_id TEXT PRIMARY KEY,
            exam_id TEXT NOT NULL,
            button_text TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (exam_id) REFERENCES dynamic_exams(exam_id)
        )
    ''')
    conn.commit()

def get_user_state(user_id, first_name, conn):
    cursor = conn.cursor()
    cursor.execute("SELECT first_name, difficulty, current_question, score, answers, question_msg_id, status_msg_id FROM user_progress WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        answers = json.loads(row[4])
        state = {'first_name': row[0], 'difficulty': row[1], 'q_index': row[2], 'score': row[3], 'answers': answers, 'question_msg_id': row[5], 'status_msg_id': row[6]}
        if state['first_name'] != first_name:
            cursor.execute("UPDATE user_progress SET first_name = ? WHERE user_id = ?", (first_name, user_id))
            conn.commit()
            state['first_name'] = first_name
    else:
        state = {'first_name': first_name, 'difficulty': None, 'q_index': 0, 'score': 0, 'answers': {}, 'question_msg_id': None, 'status_msg_id': None}
        cursor.execute("INSERT INTO user_progress (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
        conn.commit()
    return state

def save_user_state(user_id, first_name, difficulty, q_index, score, answers, conn, question_msg_id=None, status_msg_id=None):
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_progress 
        SET first_name = ?, difficulty = ?, current_question = ?, score = ?, answers = ?, question_msg_id = ?, status_msg_id = ?
        WHERE user_id = ?
    ''', (first_name, difficulty, q_index, score, json.dumps(answers), question_msg_id, status_msg_id, user_id))
    conn.commit()

def has_incomplete_quiz(user_id, difficulty, conn):
    """Check if user has an incomplete quiz for this difficulty."""
    cursor = conn.cursor()
    cursor.execute("SELECT current_question, score FROM user_progress WHERE user_id = ? AND difficulty = ?", (user_id, difficulty))
    row = cursor.fetchone()
    if row and row[0] > 0:  # Has started but not finished
        return True, row[0], row[1]  # q_index, score
    return False, 0, 0

def reset_user_progress(user_id, difficulty, conn):
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_progress 
        SET difficulty = ?, current_question = 0, score = 0, answers = '{}', question_msg_id = NULL, status_msg_id = NULL
        WHERE user_id = ?
    ''', (difficulty, user_id))
    conn.commit()

def update_lab_score(user_id, first_name, difficulty, score, conn):
    cursor = conn.cursor()
    
    # Handle Mazen Results
    if difficulty.startswith('mazin_id'):
        col_name = difficulty.replace('mazin_', '') # e.g. id1
        if col_name in ['id1', 'id2', 'id3', 'id4', 'id5', 'id6']:
            cursor.execute("INSERT OR IGNORE INTO mazen_results (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
            cursor.execute("UPDATE mazen_results SET first_name = ? WHERE user_id = ?", (first_name, user_id))
            query = f"UPDATE mazen_results SET {col_name} = ? WHERE user_id = ?"
            cursor.execute(query, (score, user_id))
            conn.commit()
        return

    cursor.execute("INSERT OR IGNORE INTO lab_results (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    cursor.execute("UPDATE lab_results SET first_name = ? WHERE user_id = ?", (first_name, user_id))
    
    valid_columns = ['video1', 'video2', 'video2_mini', 'video3', 'video4']
    if difficulty in valid_columns:
        query = f"UPDATE lab_results SET {difficulty} = ? WHERE user_id = ?"
        cursor.execute(query, (score, user_id))
        conn.commit()

def update_best_score(user_id, difficulty, score, total_questions, conn):
    """Update best score and track attempts."""
    cursor = conn.cursor()
    from datetime import datetime
    
    # Get current best score
    cursor.execute("SELECT best_score, attempts FROM best_scores WHERE user_id = ? AND difficulty = ?", (user_id, difficulty))
    row = cursor.fetchone()
    
    current_best = row[0] if row else 0
    attempts = (row[1] if row else 0) + 1
    
    # Update if this is a better score
    if score > current_best:
        cursor.execute('''
            INSERT OR REPLACE INTO best_scores 
            (user_id, difficulty, best_score, total_questions, attempts, last_attempt_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, difficulty, score, total_questions, attempts, datetime.now().isoformat()))
    else:
        # Still update attempts and date
        cursor.execute('''
            INSERT OR REPLACE INTO best_scores 
            (user_id, difficulty, best_score, total_questions, attempts, last_attempt_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, difficulty, current_best, total_questions, attempts, datetime.now().isoformat()))
    conn.commit()
    return current_best, attempts

def get_best_score(user_id, difficulty, conn):
    """Get best score for a user and difficulty."""
    cursor = conn.cursor()
    cursor.execute("SELECT best_score, total_questions, attempts FROM best_scores WHERE user_id = ? AND difficulty = ?", (user_id, difficulty))
    row = cursor.fetchone()
    if row:
        return {'best_score': row[0], 'total_questions': row[1], 'attempts': row[2]}
    return None

def save_exam_statistics(exam_id, user_id, score, total_questions, time_taken, conn):
    """Save detailed exam statistics."""
    cursor = conn.cursor()
    from datetime import datetime
    cursor.execute('''
        INSERT INTO exam_statistics (exam_id, user_id, score, total_questions, completion_date, time_taken)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (exam_id, user_id, score, total_questions, datetime.now().isoformat(), time_taken))
    conn.commit()

def get_exam_statistics(exam_id, conn):
    """Get statistics for an exam."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            COUNT(*) as total_attempts,
            AVG(score) as avg_score,
            MAX(score) as max_score,
            MIN(score) as min_score,
            AVG(time_taken) as avg_time
        FROM exam_statistics 
        WHERE exam_id = ?
    ''', (exam_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return {
            'total_attempts': row[0],
            'avg_score': round(row[1], 2) if row[1] else 0,
            'max_score': row[2] or 0,
            'min_score': row[3] or 0,
            'avg_time': round(row[4], 2) if row[4] else 0
        }
    return None

def get_leaderboard(difficulty, limit=10, conn=None):
    """Get top users for a difficulty."""
    if not conn:
        return []
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, best_score, total_questions, attempts
        FROM best_scores
        WHERE difficulty = ?
        ORDER BY best_score DESC, attempts ASC
        LIMIT ?
    ''', (difficulty, limit))
    return cursor.fetchall()

def award_badge(user_id, badge_id, conn):
    """Award a badge to a user."""
    cursor = conn.cursor()
    from datetime import datetime
    cursor.execute('''
        INSERT OR IGNORE INTO user_badges (user_id, badge_id, earned_date)
        VALUES (?, ?, ?)
    ''', (user_id, badge_id, datetime.now().isoformat()))
    conn.commit()

def get_user_badges(user_id, conn):
    """Get all badges for a user."""
    cursor = conn.cursor()
    cursor.execute("SELECT badge_id, earned_date FROM user_badges WHERE user_id = ?", (user_id,))
    return cursor.fetchall()

def check_and_award_badges(user_id, difficulty, score, total_questions, conn):
    """Check if user qualifies for badges and award them."""
    percentage = (score / total_questions * 100) if total_questions > 0 else 0
    
    # Perfect score badge
    if score == total_questions:
        award_badge(user_id, f"perfect_{difficulty}", conn)
    
    # High score badges
    if percentage >= 90:
        award_badge(user_id, f"excellent_{difficulty}", conn)
    elif percentage >= 80:
        award_badge(user_id, f"good_{difficulty}", conn)
    
    # Completion badges
    award_badge(user_id, f"completed_{difficulty}", conn)

# ------------------- صلاحيات الأدمن -------------------

def is_admin_user(user_id: int) -> bool:
    return ADMIN_TELEGRAM_ID and str(user_id) == str(ADMIN_TELEGRAM_ID)

def is_allowed_user(user_id: int, simulate_as_user: bool = False) -> bool:
    if MAINTENANCE_MODE:
        return False if not is_admin_user(user_id) else True
    return True

def get_user_counts(conn):
    cursor = conn.cursor()
    def safe_count(query):
        try:
            row = cursor.execute(query).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    total_users = safe_count("SELECT COUNT(DISTINCT user_id) FROM user_progress")
    lab_users = safe_count("SELECT COUNT(DISTINCT user_id) FROM lab_results")
    mazen_users = safe_count("SELECT COUNT(DISTINCT user_id) FROM mazen_results")
    return {
        "total_users": total_users,
        "lab_users": lab_users,
        "mazen_users": mazen_users
    }

def get_db_meta():
    try:
        size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    except Exception:
        size_mb = 0
    try:
        mtime = os.path.getmtime(DB_FILE)
        last_update = time.ctime(mtime)
    except Exception:
        last_update = "غير معروف"
    return size_mb, last_update

def get_top_scores(conn, limit=5):
    cursor = conn.cursor()
    top = []
    try:
        rows = cursor.execute(
            "SELECT user_id, first_name, username, score, difficulty FROM user_progress ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
    except Exception:
        # Fallback if username column does not exist
        try:
            rows = cursor.execute(
                "SELECT user_id, first_name, NULL as username, score, difficulty FROM user_progress ORDER BY score DESC LIMIT ?",
                (limit,)
            ).fetchall()
        except Exception as e:
            logging.error(f"Failed to fetch top scores: {e}")
            rows = []
    for r in rows:
        user_id = r[0]
        name = r[1] or "مجهول"
        username = r[2] or None
        score = r[3]
        diff = r[4] or "-"
        top.append((user_id, name, username, score, diff))
    return top

def fetch_paginated_rows(conn, table, page=0, page_size=10):
    cursor = conn.cursor()
    offset = page * page_size
    rows = []
    total = 0
    try:
        total_row = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        total = total_row[0] if total_row else 0
        rows = cursor.execute(f"SELECT * FROM {table} LIMIT ? OFFSET ?", (page_size, offset)).fetchall()
        col_names = [desc[0] for desc in cursor.description] if cursor.description else []
        return rows, col_names, total
    except Exception as e:
        logging.error(f"Failed to fetch rows for {table}: {e}")
        return [], [], 0

def _user_link_md(user_id, name):
    safe_name = escape_markdown(name or "مجهول", version=2)
    link = escape_markdown(f"tg://user?id={user_id}", version=2)
    return f"[{safe_name}]({link})"

def _user_link_md_with_username(user_id, name, username=None):
    safe_name = escape_markdown(name or "مجهول", version=2)
    if username:
        user_clean = username.lstrip("@")
        link = escape_markdown(f"https://t.me/{user_clean}", version=2)
    else:
        link = escape_markdown(f"tg://user?id={user_id}", version=2)
    return f"[{safe_name}]({link})"

def _user_link_html(user_id, name, username=None):
    safe_name = html.escape(name or "مجهول")
    if username:
        user_clean = html.escape(username.lstrip("@"))
        link = f"https://t.me/{user_clean}"
    else:
        link = f"tg://user?id={user_id}"
    return f'<a href="{link}">{safe_name}</a>'

def format_rows_as_md(table, rows, col_names, page, page_size, total):
    if not rows:
        return "لا توجد بيانات."
    lines = []
    start_idx = page * page_size
    pages = (total + page_size - 1) // page_size if page_size else 1
    safe = lambda s: escape_markdown(str(s), version=2)

    for i, row in enumerate(rows, start=1):
        idx = start_idx + i
        idx_prefix = f"{idx}\\."
        data = dict(zip(col_names, row))
        username = data.get("username")
        if table == "user_progress":
            link = _user_link_md_with_username(data.get("user_id"), data.get("first_name"), username)
            score = safe(data.get("score", 0))
            diff = safe(data.get("difficulty") or "-")
            q = safe(data.get("current_question", 0))
            line = f"{idx_prefix} {link} • المستوى: {diff} • السؤال: {q} • النتيجة: {score}"
            lines.append(line)
        elif table == "lab_results":
            link = _user_link_md_with_username(data.get("user_id"), data.get("first_name"), username)
            v1 = safe(data.get("video1", 0) or 0)
            v2 = safe(data.get("video2", 0) or 0)
            v2m = safe(data.get("video2_mini", 0) or 0)
            v3 = safe(data.get("video3", 0) or 0)
            v4 = safe(data.get("video4", 0) or 0)
            line = f"{idx_prefix} {link} • V1:{v1} • V2:{v2} • V2\\-mini:{v2m} • V3:{v3} • V4:{v4}"
            lines.append(line)
        elif table == "mazen_results":
            link = _user_link_md_with_username(data.get("user_id"), data.get("first_name"), username)
            ids = [safe(data.get(f"id{i}", 0) or 0) for i in range(1, 7)]
            ids_text = " ".join([f"id{i}:{ids[i-1]}" for i in range(1,7)])
            line = f"{idx_prefix} {link} • {ids_text}"
            lines.append(line)
        else:
            parts = [f"{escape_markdown(str(c), version=2)}={escape_markdown(str(v), version=2)}" for c, v in zip(col_names, row)]
            line = f"{idx_prefix} " + " • ".join(parts)
            lines.append(line)

    footer = f"صفحة {page+1}\\/{pages} \\- إجمالي {total}"
    return "\n".join(lines + [footer])

def get_all_user_ids(conn):
    cursor = conn.cursor()
    try:
        rows = cursor.execute("SELECT DISTINCT user_id FROM user_progress").fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logging.error(f"Failed to fetch user ids: {e}")
        return []

# Rate-limited broadcast sender
async def send_broadcast_message(bot, user_ids, text, rate_limit_sleep=0.05):
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Broadcast to {uid} failed: {e}")
        await asyncio.sleep(rate_limit_sleep)
    return sent, failed

# ------------------- دوال البوت -------------------

def escape_v1_markdown(text: str) -> str:
    if not isinstance(text, str):
        return ""
    escape_chars = '_*`['
    return ''.join(['\\' + char if char in escape_chars else char for char in text])

async def check_subscription(user_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return chat_member.status not in ['left', 'kicked']
    except BadRequest:
        return False

async def send_subscription_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("اشترك في القناة 📢", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")],
        [InlineKeyboardButton("تم الاشتراك ✅", callback_data="check_sub")]
    ]
    text = "⚠️ **شرط الاستخدام:** يجب عليك الاشتراك في القناة أولاً:"
    reply_keyboard = get_start_reply_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        # Send reply keyboard separately
        try:
            await context.bot.send_message(
                chat_id=update.callback_query.from_user.id,
                text="استخدم الزر أدناه للعودة للبداية:",
                reply_markup=reply_keyboard
            )
        except Exception as e:
            logging.warning(f"Could not send reply keyboard: {e}")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        # Send reply keyboard separately
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="استخدم الزر أدناه للعودة للبداية:",
                reply_markup=reply_keyboard
            )
        except Exception as e:
            logging.warning(f"Could not send reply keyboard: {e}")

def build_menu_keyboard(menus, menu_id="main_menu", exams=None):
    """Build menu keyboard, filtering out hidden dynamic exams if exams dict is provided."""
    menu = menus.get(menu_id) or default_menus().get(menu_id)
    if not menu:
        return InlineKeyboardMarkup([])
    cols = max(1, int(menu.get("columns", 2) or 2))
    buttons = menu.get("buttons", [])
    
    # Filter out hidden dynamic exams
    if exams:
        filtered_buttons = []
        for btn in buttons:
            cb = btn.get("callback")
            if cb and cb.startswith("dynamic_exam_"):
                exam_id = cb.replace("dynamic_exam_", "")
                exam = exams.get(exam_id)
                if exam and exam.get("is_hidden", False):
                    continue  # Skip hidden exams
            filtered_buttons.append(btn)
        buttons = filtered_buttons
    
    rows = []
    row = []
    for btn in buttons:
        text = btn.get("text", "")
        cb = btn.get("callback")
        url = btn.get("url")
        submenu = btn.get("submenu", [])
        if submenu:
            # Button with submenu - use special callback
            submenu_id = btn.get("submenu_id", f"submenu_{hash(str(btn))}")
            row.append(InlineKeyboardButton(text + " ▶️", callback_data=f"show_submenu_{submenu_id}"))
        elif cb:
            row.append(InlineKeyboardButton(text, callback_data=cb))
        elif url:
            row.append(InlineKeyboardButton(text, url=url))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def get_start_reply_keyboard():
    """Create a Reply Keyboard with a 'بدء' button that sends /start command."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🏠 بدء")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_msg = f"أهلاً بك يا {user.first_name}! 👋\n\nاختر من القائمة للبدء."
    menus = context.bot_data.get("menus", default_menus())
    exams = context.bot_data.get("exams", {})
    keyboard = build_menu_keyboard(menus, "main_menu", exams)
    reply_keyboard = get_start_reply_keyboard()
    
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=keyboard)
        # Send a new message with reply keyboard for callback queries
        try:
            await context.bot.send_message(
                chat_id=update.callback_query.from_user.id,
                text="استخدم الزر أدناه للعودة للبداية:",
                reply_markup=reply_keyboard
            )
        except Exception as e:
            logging.warning(f"Could not send reply keyboard: {e}")
    else:
        await update.message.reply_text(welcome_msg, reply_markup=keyboard)
        # Send reply keyboard separately for regular messages
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="استخدم الزر أدناه للعودة للبداية:",
                reply_markup=reply_keyboard
            )
        except Exception as e:
            logging.warning(f"Could not send reply keyboard: {e}")

# ------------------- قوائم الأدمن داخل التليجرام -------------------

def build_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات المستخدمين", callback_data="admin_stats")],
        [InlineKeyboardButton("📄 عرض النتائج", callback_data="admin_results_view")],
        [InlineKeyboardButton("📚 عرض كل النتائج (تصفح)", callback_data="admin_results_browse")],
        [InlineKeyboardButton("🧩 تحرير القائمة الرئيسية", callback_data="admin_edit_main")],
        [InlineKeyboardButton("📝 إدارة الاختبارات الديناميكية", callback_data="admin_exams_manage")],
        [InlineKeyboardButton("🎓 إدارة اختبار مازن", callback_data="admin_mazen_manage")],
        [InlineKeyboardButton("🔄 إعادة تحميل البيانات", callback_data="admin_reload_data")],
        [InlineKeyboardButton("📣 إرسال إعلان", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton("📥 تحميل قاعدة البيانات", callback_data="admin_export_db")],
        [InlineKeyboardButton("📤 رفع قاعدة البيانات", callback_data="admin_import_db")],
        [InlineKeyboardButton("🛠️ تبديل وضع الصيانة", callback_data="admin_toggle_maint")]
    ])

def admin_back_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ لوحة الأدمن", callback_data="admin_menu")]])

async def send_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = "لوحة تحكم الأدمن"
    if update.callback_query:
        await update.callback_query.edit_message_text(menu_text, reply_markup=build_admin_keyboard())
    else:
        await update.message.reply_text(menu_text, reply_markup=build_admin_keyboard())

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.message.reply_text("❌ غير مسموح.")
        return
    await send_admin_menu(update, context)

async def handle_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    counts = get_user_counts(context.bot_data['db_conn'])
    size_mb, last_update = get_db_meta()
    text = (
        "📊 إحصائيات سريعة\n"
        f"- إجمالي المستخدمين: {counts['total_users']}\n"
        f"- مستخدمو المخبر: {counts['lab_users']}\n"
        f"- مستخدمو اختبار مازن: {counts['mazen_users']}\n"
        f"- حجم قاعدة البيانات: {size_mb:.2f} MB\n"
        f"- آخر تحديث للملف: {escape_markdown(str(last_update), version=2)}"
    )
    await update.callback_query.edit_message_text(text, reply_markup=build_admin_keyboard())

async def handle_admin_results_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    conn = context.bot_data['db_conn']
    counts = get_user_counts(conn)
    top = get_top_scores(conn, limit=5)
    top_lines_parts = []
    for i, (uid, name, username, score, diff) in enumerate(top):
        name_link = _user_link_html(uid, name, username)
        top_lines_parts.append(f"{i+1}. {name_link} — <code>{html.escape(str(score))}</code> ({html.escape(diff or '-')})")
    top_lines = "\n".join(top_lines_parts) if top_lines_parts else "لا يوجد نتائج."
    size_mb, last_update = get_db_meta()
    text = (
        "<b>📄 ملخص النتائج</b>\n"
        f"• إجمالي المستخدمين: <code>{counts['total_users']}</code>\n"
        f"• مخبر: <code>{counts['lab_users']}</code>\n"
        f"• مازن: <code>{counts['mazen_users']}</code>\n"
        "🏅 أعلى الدرجات:\n"
        f"{top_lines}\n"
        f"• حجم قاعدة البيانات: <code>{size_mb:.2f} MB</code>\n"
        f"• آخر تحديث للملف: <code>{html.escape(str(last_update))}</code>"
    )
    await update.callback_query.edit_message_text(text, reply_markup=build_admin_keyboard(), parse_mode="HTML")

async def handle_admin_results_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    selector = InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 user_progress", callback_data="admin_results_table_user")],
        [InlineKeyboardButton("🧪 lab_results", callback_data="admin_results_table_lab")],
        [InlineKeyboardButton("📚 mazen_results", callback_data="admin_results_table_mazen")],
        [InlineKeyboardButton("🔍 بحث بالمعرف/الاسم", callback_data="admin_results_search")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="admin_menu")]
    ])
    await update.callback_query.edit_message_text("اختر الجدول للتصفح:", reply_markup=selector)

async def handle_admin_results_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    context.user_data['admin_results'] = {"search": True}
    await update.callback_query.edit_message_text(
        "أرسل ID المستخدم أو جزء من الاسم/اليوزر وسيتم إظهار النتائج.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_results_browse")]])
    )

async def handle_admin_results_table(update: Update, context: ContextTypes.DEFAULT_TYPE, table_key: str):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    table_map = {
        "user": "user_progress",
        "lab": "lab_results",
        "mazen": "mazen_results"
    }
    table = table_map.get(table_key)
    if not table:
        await update.callback_query.answer("جدول غير معروف.", show_alert=True)
        return
    context.user_data['admin_results'] = {"table": table, "page": 0}
    await render_admin_results_page(update, context)

async def render_admin_results_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    state = context.user_data.get('admin_results', {})
    table = state.get('table')
    page = state.get('page', 0)
    if not table:
        await update.callback_query.answer("لا توجد جلسة تصفح نشطة.", show_alert=True)
        return
    page_size = context.user_data.get('admin_results', {}).get('page_size', 10) or 10
    rows, cols, total = fetch_paginated_rows(context.bot_data['db_conn'], table, page=page, page_size=page_size)
    text = f"📚 {escape_markdown(table, version=2)}\n" + format_rows_as_md(table, rows, cols, page, page_size, total)
    kb = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data="admin_results_prev"))
    if (page + 1) * page_size < total:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data="admin_results_next"))
    if nav_row:
        kb.append(nav_row)
    kb.append([
        InlineKeyboardButton("صفحة 5", callback_data="admin_ps_5"),
        InlineKeyboardButton("صفحة 10", callback_data="admin_ps_10"),
        InlineKeyboardButton("صفحة 20", callback_data="admin_ps_20"),
    ])
    kb.append([
        InlineKeyboardButton("↩️ رجوع", callback_data="admin_results_browse"),
        InlineKeyboardButton("لوحة الأدمن", callback_data="admin_menu"),
    ])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")

def main_menu_buttons(context):
    menus = context.bot_data.get("menus", default_menus())
    return menus.get("main_menu", {}).get("buttons", [])

def set_main_menu_buttons(context, buttons):
    menus = context.bot_data.get("menus", default_menus())
    if "main_menu" not in menus:
        menus["main_menu"] = {"columns": 2, "buttons": []}
    menus["main_menu"]["buttons"] = buttons
    context.bot_data["menus"] = menus
    conn = context.bot_data.get('db_conn')
    save_menus(menus, conn)

def render_main_menu_admin_view(menus):
    menu = menus.get("main_menu") or default_menus()["main_menu"]
    lines = []
    for idx, btn in enumerate(menu.get("buttons", [])):
        txt = btn.get("text", "")
        cb = btn.get("callback", "")
        url = btn.get("url", "")
        submenu = btn.get("submenu", [])
        if submenu:
            target = f"submenu: {len(submenu)} أزرار فرعية"
        else:
            target = f"cb:{cb}" if cb else f"url:{url}"
        lines.append(f"{idx}: {txt} ({target})")
    return "\n".join(lines) or "لا توجد أزرار حالياً."

async def handle_admin_edit_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    menus = context.bot_data.get("menus", default_menus())
    overview = render_main_menu_admin_view(menus)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة زر", callback_data="admin_main_add")],
        [InlineKeyboardButton("📁 إضافة قائمة فرعية", callback_data="admin_main_add_submenu")],
        [InlineKeyboardButton("📥 نقل زر إلى داخل زر", callback_data="admin_main_move_to_submenu")],
        [InlineKeyboardButton("✏️ إعادة تسمية", callback_data="admin_main_rename")],
        [InlineKeyboardButton("🔗 تغيير الإجراء", callback_data="admin_main_action")],
        [InlineKeyboardButton("🗑 حذف", callback_data="admin_main_delete")],
        [InlineKeyboardButton("🔼🔽 نقل", callback_data="admin_main_move")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="admin_menu")]
    ])
    await update.callback_query.edit_message_text(
        f"تحرير القائمة الرئيسية:\n{overview}",
        reply_markup=kb
    )
async def handle_admin_export_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    if not os.path.exists(DB_FILE):
        await update.callback_query.edit_message_text("⚠️ قاعدة البيانات غير موجودة.", reply_markup=build_admin_keyboard())
        return
    try:
        size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
        if size_mb > 45:  # Telegram doc limit ~50MB
            await update.callback_query.edit_message_text("⚠️ حجم الملف كبير جداً للإرسال.", reply_markup=build_admin_keyboard())
            return
    except Exception as e:
        logging.error(f"Failed to stat DB file: {e}")
        await update.callback_query.edit_message_text("حدث خطأ أثناء قراءة الملف.", reply_markup=build_admin_keyboard())
        return
    try:
        await context.bot.send_document(
            chat_id=user.id,
            document=open(DB_FILE, "rb"),
            filename=os.path.basename(DB_FILE),
            caption="📥 نسخة من قاعدة البيانات"
        )
    except Exception as e:
        logging.error(f"Failed to send DB: {e}")
        await update.callback_query.edit_message_text("حدث خطأ أثناء إرسال الملف.", reply_markup=build_admin_keyboard())
        return
    await update.callback_query.answer("✅ تم إرسال الملف في الخاص.", show_alert=True)

async def handle_admin_import_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt admin to upload database file."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    context.user_data['admin_importing_db'] = True
    
    await update.callback_query.edit_message_text(
        "📤 رفع قاعدة البيانات\n\n"
        "⚠️ تحذير: سيتم استبدال قاعدة البيانات الحالية بالملف المرفوع!\n\n"
        "أرسل ملف قاعدة البيانات (user_progress.db):\n"
        "أو اضغط 'إلغاء' للرجوع.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_menu")]
        ])
    )

async def handle_admin_import_db_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded database file."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    
    if not context.user_data.get('admin_importing_db'):
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ يرجى إرسال ملف قاعدة البيانات (.db)")
        return
    
    # Check if it's a database file
    file_name = update.message.document.file_name or ""
    if not file_name.endswith('.db'):
        await update.message.reply_text("❌ الملف يجب أن يكون ملف قاعدة بيانات (.db)")
        return
    
    try:
        # Download the file
        file = await context.bot.get_file(update.message.document.file_id)
        backup_file = f"{DB_FILE}.backup"
        
        # Create backup of current database
        if os.path.exists(DB_FILE):
            import shutil
            shutil.copy2(DB_FILE, backup_file)
            logging.info(f"Created backup of current database: {backup_file}")
        
        # Download new database
        await file.download_to_drive(DB_FILE)
        
        # Verify the database is valid by trying to connect
        test_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        test_conn.execute("SELECT 1")
        test_conn.close()
        
        # Reload database connection in bot_data
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        context.bot_data['db_conn'] = conn
        
        # Reload exams and menus from new database
        context.bot_data['exams'] = load_exams(conn)
        context.bot_data['menus'] = load_menus(conn)
        
        # Clear cached dynamic exam data
        if 'dynamic_exams_data' in context.bot_data:
            context.bot_data['dynamic_exams_data'] = {}
        
        context.user_data.pop('admin_importing_db', None)
        
        await update.message.reply_text(
            "✅ تم رفع قاعدة البيانات بنجاح!\n\n"
            "تم استبدال قاعدة البيانات الحالية بالملف المرفوع.\n"
            "تم إنشاء نسخة احتياطية من قاعدة البيانات القديمة.",
            reply_markup=build_admin_keyboard()
        )
        
        logging.info(f"Database imported successfully by admin {user.id}")
        
    except sqlite3.Error as e:
        # Restore backup if database is invalid
        if os.path.exists(backup_file):
            import shutil
            shutil.copy2(backup_file, DB_FILE)
            os.remove(backup_file)
        
        await update.message.reply_text(
            f"❌ خطأ: الملف المرفوع ليس قاعدة بيانات صالحة.\n\n"
            f"التفاصيل: {str(e)}\n\n"
            "تم استعادة قاعدة البيانات القديمة.",
            reply_markup=build_admin_keyboard()
        )
        context.user_data.pop('admin_importing_db', None)
        logging.error(f"Failed to import database: {e}")
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ حدث خطأ أثناء رفع قاعدة البيانات: {str(e)}",
            reply_markup=build_admin_keyboard()
        )
        context.user_data.pop('admin_importing_db', None)
        logging.error(f"Failed to import database: {e}")

async def export_user_progress_to_csv(conn):
    """Export user_progress table to CSV file and return the file path."""
    import csv
    from datetime import datetime
    
    cursor = conn.cursor()
    
    # Get all data from user_progress table
    try:
        cursor.execute("SELECT * FROM user_progress")
        rows = cursor.fetchall()
        column_names = [description[0] for description in cursor.description]
    except Exception as e:
        logging.error(f"Failed to fetch user_progress data: {e}")
        return None
    
    # Create CSV file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"user_progress_{timestamp}.csv"
    csv_path = os.path.join("exams", csv_filename)  # Save in exams folder
    
    # Ensure exams directory exists
    os.makedirs("exams", exist_ok=True)
    
    try:
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile)
            # Write header
            writer.writerow(column_names)
            # Write data
            for row in rows:
                writer.writerow(row)
        return csv_path
    except Exception as e:
        logging.error(f"Failed to create CSV file: {e}")
        return None

async def send_user_progress_to_admin(context: ContextTypes.DEFAULT_TYPE):
    """Send database file to admin every hour."""
    if not ADMIN_TELEGRAM_ID:
        logging.warning("ADMIN_TELEGRAM_ID not set, cannot send database")
        return
    
    if not os.path.exists(DB_FILE):
        logging.warning(f"Database file {DB_FILE} not found, cannot send")
        return
    
    try:
        # Check file size
        file_size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
        if file_size_mb > 45:  # Telegram doc limit ~50MB
            logging.warning(f"Database file too large ({file_size_mb:.2f} MB), skipping send")
            return
        
        # Send to admin
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        await context.bot.send_document(
            chat_id=ADMIN_TELEGRAM_ID,
            document=open(DB_FILE, "rb"),
            filename=os.path.basename(DB_FILE),
            caption=f"📥 نسخة من قاعدة البيانات - {timestamp}\n\nتم التصدير تلقائياً كل ساعة"
        )
        
        logging.info(f"Successfully sent database file to admin at {timestamp}")
    except Exception as e:
        logging.error(f"Error sending database to admin: {e}")

async def scheduled_user_progress_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job to send user_progress CSV to admin every hour."""
    await send_user_progress_to_admin(context)

async def handle_admin_exams_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    exams = load_exams()
    exam_list = []
    for exam_id, exam in exams.items():
        status = "✅" if exam.get("explanation_file") and exam.get("questions_file") else "⚠️"
        visibility = "👁️" if not exam.get("is_hidden", False) else "🙈"
        exam_list.append(f"{status} {visibility} {exam.get('button_text', exam_id)}")
    exam_text = "\n".join(exam_list) if exam_list else "لا توجد اختبارات حالياً."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء اختبار جديد", callback_data="admin_exam_create")],
        [InlineKeyboardButton("✏️ تعديل اختبار", callback_data="admin_exam_edit")],
        [InlineKeyboardButton("🗑 حذف اختبار", callback_data="admin_exam_delete")],
        [InlineKeyboardButton("👁️ إخفاء/إظهار اختبار", callback_data="admin_exam_toggle_visibility")],
        [InlineKeyboardButton("⚡ إضافة زر بدون شرح", callback_data="admin_exam_add_no_explanation")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="admin_menu")]
    ])
    await update.callback_query.edit_message_text(
        f"📝 إدارة الاختبارات الديناميكية:\n\n{exam_text}\n\n👁️ = ظاهر | 🙈 = مخفي",
        reply_markup=kb
    )

async def handle_admin_exam_toggle_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of exams to toggle visibility."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    if not exams:
        await update.callback_query.edit_message_text("لا توجد اختبارات.", reply_markup=admin_back_markup())
        return
    
    buttons = []
    for exam_id, exam in exams.items():
        visibility_icon = "👁️" if not exam.get("is_hidden", False) else "🙈"
        buttons.append([InlineKeyboardButton(
            f"{visibility_icon} {exam.get('button_text', exam_id)}",
            callback_data=f"admin_exam_visibility_select_{exam_id}"
        )])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")])
    
    await update.callback_query.edit_message_text(
        "👁️ اختر اختبار لتغيير حالة الإخفاء/الإظهار:\n\n👁️ = ظاهر | 🙈 = مخفي",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_admin_exam_visibility_select(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Show options to hide or show exam."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    is_hidden = exam.get("is_hidden", False)
    exam_name = exam.get('button_text', exam_id)
    
    if is_hidden:
        # Currently hidden, show option to show
        keyboard = [
            [InlineKeyboardButton("✅ إظهار الاختبار", callback_data=f"admin_exam_show_{exam_id}")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="admin_exam_toggle_visibility")]
        ]
        text = f"🙈 الاختبار '{exam_name}' مخفي حالياً.\n\nهل تريد إظهاره؟"
    else:
        # Currently visible, show option to hide
        keyboard = [
            [InlineKeyboardButton("🙈 إخفاء الاختبار", callback_data=f"admin_exam_hide_{exam_id}")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="admin_exam_toggle_visibility")]
        ]
        text = f"👁️ الاختبار '{exam_name}' ظاهر حالياً.\n\nهل تريد إخفاءه؟"
    
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_admin_exam_hide(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Hide an exam."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    # Hide the exam
    exam['is_hidden'] = True
    exams[exam_id] = exam
    conn = context.bot_data.get('db_conn')
    save_exams(exams, conn)
    
    # Reload exams in bot_data
    context.bot_data['exams'] = load_exams(conn)
    
    exam_name = exam.get('button_text', exam_id)
    await update.callback_query.edit_message_text(
        f"✅ تم إخفاء الاختبار '{exam_name}' بنجاح.\n\nالاختبار لن يظهر في القائمة الرئيسية للمستخدمين.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")]])
    )

async def handle_admin_exam_show(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Ask if admin wants to notify users when showing exam."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    exam_name = exam.get('button_text', exam_id)
    keyboard = [
        [InlineKeyboardButton("✅ نعم، إشعار المستخدمين", callback_data=f"admin_exam_notify_yes_{exam_id}")],
        [InlineKeyboardButton("❌ لا، إظهار فقط", callback_data=f"admin_exam_notify_no_{exam_id}")],
        [InlineKeyboardButton("↩️ رجوع", callback_data=f"admin_exam_visibility_select_{exam_id}")]
    ]
    
    await update.callback_query.edit_message_text(
        f"👁️ هل تريد إشعار المستخدمين عند إظهار الاختبار '{exam_name}'؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_admin_exam_notify_users(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Show exam and notify users."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    exam_name = exam.get('button_text', exam_id)
    
    # Show the exam
    exam['is_hidden'] = False
    exams[exam_id] = exam
    conn = context.bot_data.get('db_conn')
    save_exams(exams, conn)
    
    # Reload exams in bot_data
    context.bot_data['exams'] = load_exams(conn)
    
    # Notify all users
    conn = context.bot_data['db_conn']
    cursor = conn.cursor()
    try:
        # Get all user IDs from user_progress table
        user_ids = cursor.execute("SELECT DISTINCT user_id FROM user_progress").fetchall()
        notified_count = 0
        for (user_id,) in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 اختبار جديد متاح!\n\n📝 {exam_name}\n\nاستخدم /start للبدء!"
                )
                notified_count += 1
            except Exception as e:
                logging.warning(f"Could not notify user {user_id}: {e}")
        
        await update.callback_query.edit_message_text(
            f"✅ تم إظهار الاختبار '{exam_name}' بنجاح.\n\n📢 تم إشعار {notified_count} مستخدم.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")]])
        )
    except Exception as e:
        logging.error(f"Error notifying users: {e}")
        await update.callback_query.edit_message_text(
            f"✅ تم إظهار الاختبار '{exam_name}' بنجاح.\n\n⚠️ حدث خطأ في إشعار المستخدمين: {e}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")]])
        )

async def handle_admin_exam_show_final(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str, notify: bool = False):
    """Show exam without notifying users."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    exam_name = exam.get('button_text', exam_id)
    
    # Show the exam
    exam['is_hidden'] = False
    exams[exam_id] = exam
    conn = context.bot_data.get('db_conn')
    save_exams(exams, conn)
    
    # Reload exams in bot_data
    context.bot_data['exams'] = load_exams(conn)
    
    await update.callback_query.edit_message_text(
        f"✅ تم إظهار الاختبار '{exam_name}' بنجاح.\n\nالاختبار متاح الآن في القائمة الرئيسية.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")]])
    )

async def handle_admin_exam_add_no_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of exams to add a button without explanation."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    if not exams:
        await update.callback_query.edit_message_text("لا توجد اختبارات.", reply_markup=admin_back_markup())
        return
    
    buttons = []
    for exam_id, exam in exams.items():
        buttons.append([InlineKeyboardButton(
            f"⚡ {exam.get('button_text', exam_id)}",
            callback_data=f"admin_exam_no_explanation_select_{exam_id}"
        )])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")])
    
    await update.callback_query.edit_message_text(
        "⚡ اختر اختبار لإضافة زر بدون شرح:\n\nهذا الزر سيبدأ الاختبار مباشرة بدون عرض الشرح.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_admin_exam_select_no_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Ask for button text for no-explanation button."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    exam = exams.get(exam_id)
    if not exam:
        await update.callback_query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    context.user_data['admin_no_explanation'] = {
        'exam_id': exam_id,
        'step': 'ask_button_text'
    }
    
    await update.callback_query.edit_message_text(
        f"⚡ إضافة زر بدون شرح للاختبار: {exam.get('button_text', exam_id)}\n\n"
        f"أرسل اسم الزر الذي تريده:\n"
        f"مثال: 'اختبار سريع' أو 'اختبار مباشر'",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ رجوع", callback_data="admin_exam_add_no_explanation")]
        ])
    )

async def handle_admin_exam_save_no_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the no-explanation button."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    
    if 'admin_no_explanation' not in context.user_data:
        return
    
    state = context.user_data['admin_no_explanation']
    if state.get('step') != 'ask_button_text':
        return
    
    exam_id = state.get('exam_id')
    button_text = update.message.text.strip()
    
    if not button_text:
        await update.message.reply_text("❌ يرجى إرسال اسم الزر.")
        return
    
    # Generate button_id
    button_id = f"no_explanation_{exam_id}_{int(time.time())}"
    
    # Save to database
    conn = context.bot_data.get('db_conn')
    cursor = conn.cursor()
    from datetime import datetime
    now = datetime.now().isoformat()
    
    cursor.execute('''
        INSERT INTO exam_no_explanation_buttons (button_id, exam_id, button_text, created_at)
        VALUES (?, ?, ?, ?)
    ''', (button_id, exam_id, button_text, now))
    conn.commit()
    
    # Add button to main menu
    menus = context.bot_data.get("menus", default_menus())
    buttons = menus.get("main_menu", {}).get("buttons", [])
    buttons.append({
        "text": button_text,
        "callback": f"dynamic_exam_no_explanation_{exam_id}"
    })
    set_main_menu_buttons(context, buttons)
    
    # Clean up
    context.user_data.pop('admin_no_explanation', None)
    
    await update.message.reply_text(
        f"✅ تم إضافة الزر '{button_text}' بنجاح!\n\n"
        f"الزر متاح الآن في القائمة الرئيسية ويبدأ الاختبار مباشرة بدون شرح.",
        reply_markup=admin_back_markup()
    )

async def handle_admin_exam_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    context.user_data['admin_exam_create'] = {"step": "name"}
    await update.callback_query.edit_message_text(
        "📝 إنشاء اختبار جديد\n\nأرسل اسم الزر الذي سيظهر في القائمة الرئيسية:",
        reply_markup=admin_back_markup()
    )

async def handle_admin_exam_create_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive exam button name."""
    logging.info(f"handle_admin_exam_create_name called. User: {update.effective_user.id}, Text: {update.message.text}")
    user = update.effective_user
    if not is_admin_user(user.id):
        logging.warning(f"handle_admin_exam_create_name: User {user.id} is not admin")
        return
    button_text = update.message.text.strip()
    if not button_text:
        logging.warning(f"handle_admin_exam_create_name: Empty button text")
        await update.message.reply_text("❌ الاسم لا يمكن أن يكون فارغاً. أعد المحاولة:")
        return
    logging.info(f"handle_admin_exam_create_name: Saving button_text: {button_text}")
    context.user_data['admin_exam_create']['button_text'] = button_text
    context.user_data['admin_exam_create']['step'] = "explanation"
    await update.message.reply_text(
        "✅ تم حفظ اسم الزر.\n\nالآن أرسل ملف CSV للشرح (مثل textlevels.csv):\n"
        "يجب أن يحتوي على الأعمدة: id, level, text\n"
        "أو اكتب 'تخطي' لتخطي الشرح.",
        reply_markup=admin_back_markup()
    )
    logging.info(f"handle_admin_exam_create_name: Step changed to 'explanation'")

async def handle_admin_exam_create_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive explanation CSV file and analyze structure."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    if update.message.text and update.message.text.strip().lower() in ['تخطي', 'skip']:
        context.user_data['admin_exam_create']['step'] = "question_type"
        await update.message.reply_text(
            "✅ تم تخطي الشرح.\n\nاختر نوع الأسئلة:\n"
            "1️⃣ أسئلة من متعدد (MCQ)\n"
            "2️⃣ أسئلة مقالية (Narrative)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1️⃣ MCQ", callback_data="admin_exam_type_mcq")],
                [InlineKeyboardButton("2️⃣ Narrative", callback_data="admin_exam_type_narrative")],
                [InlineKeyboardButton("↩️ رجوع", callback_data="admin_menu")]
            ])
        )
        return
    if not update.message.document:
        await update.message.reply_text("❌ يرجى إرسال ملف CSV. أو اكتب 'تخطي' لتخطي الشرح.")
        return
    file = await context.bot.get_file(update.message.document.file_id)
    exam_id = f"exam_{int(time.time())}"
    explanation_file = f"exams/{exam_id}_explanation.csv"
    os.makedirs("exams", exist_ok=True)
    # Save file to disk (method 1)
    await file.download_to_drive(explanation_file)
    # Save file_id (method 2)
    explanation_file_id = update.message.document.file_id
    context.user_data['admin_exam_create']['explanation_file'] = explanation_file
    context.user_data['admin_exam_create']['explanation_file_id'] = explanation_file_id
    context.user_data['admin_exam_create']['exam_id'] = exam_id
    
    # Analyze the CSV structure
    try:
        df = pd.read_csv(explanation_file, encoding='utf-8')
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Group by ID and count levels
        structure = {}
        for _, row in df.iterrows():
            id_val = row.get('id', 1)
            level = row.get('level', 1)
            if id_val not in structure:
                structure[id_val] = []
            if level not in structure[id_val]:
                structure[id_val].append(level)
        
        # Sort levels for each ID
        for id_val in structure:
            structure[id_val].sort()
        
        # Store structure for media addition
        context.user_data['admin_exam_create']['explanation_structure'] = structure
        context.user_data['admin_exam_create']['media_attachments'] = {}
        
        # Build summary message
        summary_lines = []
        for id_val in sorted(structure.keys()):
            levels = structure[id_val]
            summary_lines.append(f"• ID {id_val}: {len(levels)} level(s) - Levels: {', '.join(map(str, levels))}")
        
        summary_text = "\n".join(summary_lines) if summary_lines else "لا توجد بيانات"

        keyboard = [
            [InlineKeyboardButton("✅ نعم", callback_data="admin_exam_media_yes")],
            [InlineKeyboardButton("❌ لا", callback_data="admin_exam_media_no")]
        ]
        keyboard.append(admin_back_markup().inline_keyboard[0])
        
        await update.message.reply_text(
            f"✅ تم حفظ ملف الشرح وتحليل البنية:\n\n"
            f"📊 ملخص البنية:\n{summary_text}\n\n"
            f"هل تريد إضافة صور/فيديوهات/روابط مع رسائل الشرح؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['admin_exam_create']['step'] = "ask_media"
        
    except Exception as e:
        logging.error(f"Error analyzing explanation file: {e}")
        await update.message.reply_text(
            f"⚠️ حدث خطأ في تحليل الملف: {e}\n\n"
            f"سيتم المتابعة بدون تحليل البنية.",
            reply_markup=admin_back_markup()
        )
        context.user_data['admin_exam_create']['step'] = "question_type"
        await handle_admin_exam_type_choice(update, context)

async def handle_admin_exam_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, question_type: str):
    """Handle question type selection - supports both MCQ and Narrative like Mazen test."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    if question_type == "mcq":
        # For MCQ, show ID buttons if structure exists
        structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
        if structure:
            # Show buttons for each ID
            keyboard = []
            for id_val in sorted(structure.keys()):
                keyboard.append([InlineKeyboardButton(f"📝 ID {id_val}", callback_data=f"admin_exam_mcq_id_{id_val}")])
            keyboard.append(admin_back_markup().inline_keyboard[0])
            
            context.user_data['admin_exam_create']['question_type'] = "mcq"
            context.user_data['admin_exam_create']['step'] = "select_mcq_id"
            await update.callback_query.edit_message_text(
                "✅ تم اختيار: أسئلة من متعدد (MCQ)\n\n"
                "اختر ID لإضافة ملف MCQ له:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # No structure, proceed directly
            context.user_data['admin_exam_create']['question_type'] = "mcq"
            context.user_data['admin_exam_create']['step'] = "mcq_questions"
            context.user_data['admin_exam_create']['current_question_id'] = None
            await update.callback_query.edit_message_text(
                "✅ تم اختيار: أسئلة من متعدد (MCQ)\n\n"
                "أرسل ملف CSV للأسئلة من متعدد:\n"
                "يجب أن يحتوي على: question, option_a, option_b, option_c, option_d, correct_answer, "
                "correct_explanation, concept_explanation, explanation_a, explanation_b, explanation_c, explanation_d\n\n"
                "💡 ملاحظة: بعد رفع ملف MCQ، يمكنك اختيار إضافة أسئلة مقالية أيضاً (مثل اختبار مازن)",
                reply_markup=admin_back_markup()
            )
    else:
        # Narrative only
        structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
        if structure:
            # Show buttons for each ID
            keyboard = []
            for id_val in sorted(structure.keys()):
                keyboard.append([InlineKeyboardButton(f"✍️ ID {id_val}", callback_data=f"admin_exam_narrative_id_{id_val}")])
            keyboard.append(admin_back_markup().inline_keyboard[0])
            
            context.user_data['admin_exam_create']['question_type'] = "narrative"
            context.user_data['admin_exam_create']['step'] = "select_narrative_id"
            await update.callback_query.edit_message_text(
                "✅ تم اختيار: أسئلة مقالية (Narrative)\n\n"
                "اختر ID لإضافة ملف Narrative له:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['admin_exam_create']['question_type'] = "narrative"
            context.user_data['admin_exam_create']['step'] = "narrative_questions"
            context.user_data['admin_exam_create']['current_question_id'] = None
            await update.callback_query.edit_message_text(
                "✅ تم اختيار: أسئلة مقالية (Narrative)\n\n"
                "أرسل ملف CSV للأسئلة المقالية:\n"
                "يجب أن يحتوي على: question, answer",
                reply_markup=admin_back_markup()
            )

async def handle_admin_exam_create_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive questions CSV file - supports both MCQ and Narrative like Mazen test."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    if not update.message.document:
        await update.message.reply_text("❌ يرجى إرسال ملف CSV للأسئلة.")
        return
    
    step = context.user_data['admin_exam_create'].get('step')
    
    if step == "mcq_questions":
        # Store MCQ file - check if it's for a specific ID
        file = await context.bot.get_file(update.message.document.file_id)
        if 'exam_id' not in context.user_data['admin_exam_create']:
            context.user_data['admin_exam_create']['exam_id'] = f"exam_{int(time.time())}"
        exam_id = context.user_data['admin_exam_create']['exam_id']
        question_id = context.user_data['admin_exam_create'].get('current_question_id')
        
        # Initialize questions_by_id if not exists
        if 'questions_by_id' not in context.user_data['admin_exam_create']:
            context.user_data['admin_exam_create']['questions_by_id'] = {}
        
        if question_id is not None:
            # Questions for specific ID
            mcq_file = f"exams/{exam_id}_mcq_id{question_id}.csv"
            os.makedirs("exams", exist_ok=True)
            # Save file to disk (method 1)
            await file.download_to_drive(mcq_file)
            # Save file_id (method 2)
            mcq_file_id = update.message.document.file_id
            if 'mcq_files_by_id' not in context.user_data['admin_exam_create']:
                context.user_data['admin_exam_create']['mcq_files_by_id'] = {}
            if 'mcq_file_ids_by_id' not in context.user_data['admin_exam_create']:
                context.user_data['admin_exam_create']['mcq_file_ids_by_id'] = {}
            context.user_data['admin_exam_create']['mcq_files_by_id'][question_id] = mcq_file
            context.user_data['admin_exam_create']['mcq_file_ids_by_id'][question_id] = mcq_file_id
            
            # Check if there are more IDs to process
            structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
            remaining_ids = [id_val for id_val in sorted(structure.keys()) 
                           if id_val not in context.user_data['admin_exam_create'].get('mcq_files_by_id', {})]
            
            if remaining_ids:
                # Show buttons for remaining IDs
                keyboard = []
                for id_val in remaining_ids:
                    keyboard.append([InlineKeyboardButton(f"📝 ID {id_val}", callback_data=f"admin_exam_mcq_id_{id_val}")])
                keyboard.append(admin_back_markup().inline_keyboard[0])
                
                await update.message.reply_text(
                    f"✅ تم حفظ ملف أسئلة MCQ لـ ID {question_id}.\n\n"
                    f"📋 IDs المتبقية: {', '.join(map(str, remaining_ids))}\n\n"
                    f"اختر ID آخر لإضافة ملف MCQ له:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['admin_exam_create']['step'] = "select_mcq_id"
            else:
                # All IDs processed, ask about narrative
                question_type = context.user_data['admin_exam_create'].get('question_type', 'mcq')
                if question_type == "both":
                    # Need narrative questions too - show ID buttons
                    structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
                    keyboard = []
                    for id_val in sorted(structure.keys()):
                        keyboard.append([InlineKeyboardButton(f"✍️ ID {id_val}", callback_data=f"admin_exam_narrative_id_{id_val}")])
                    keyboard.append(admin_back_markup().inline_keyboard[0])
                    
                    context.user_data['admin_exam_create']['step'] = "select_narrative_id"
                    await update.message.reply_text(
                        "✅ تم حفظ جميع ملفات MCQ.\n\n"
                        "الآن: اختر ID لإضافة ملف Narrative له:",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    # MCQ only, show yes/no buttons for adding narrative
                    keyboard = [
                        [InlineKeyboardButton("✅ نعم", callback_data="admin_exam_add_narrative_yes")],
                        [InlineKeyboardButton("❌ لا", callback_data="admin_exam_add_narrative_no")]
                    ]
                    keyboard.append(admin_back_markup().inline_keyboard[0])
                    
                    await update.message.reply_text(
                        "✅ تم حفظ جميع ملفات MCQ.\n\n"
                        "هل تريد إضافة أسئلة مقالية أيضاً؟ (مثل اختبار مازن)",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    context.user_data['admin_exam_create']['step'] = "ask_narrative"
        else:
            # No ID specified, use single file (legacy mode)
            mcq_file = f"exams/{exam_id}_mcq.csv"
            os.makedirs("exams", exist_ok=True)
            # Save file to disk (method 1)
            await file.download_to_drive(mcq_file)
            # Save file_id (method 2)
            mcq_file_id = update.message.document.file_id
            context.user_data['admin_exam_create']['mcq_file'] = mcq_file
            context.user_data['admin_exam_create']['mcq_file_id'] = mcq_file_id
            
            # Ask if they want to add narrative questions too
            question_type = context.user_data['admin_exam_create'].get('question_type', 'mcq')
            if question_type == "both":
                # Need narrative questions
                structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
                if structure:
                    # Show ID buttons
                    keyboard = []
                    for id_val in sorted(structure.keys()):
                        keyboard.append([InlineKeyboardButton(f"✍️ ID {id_val}", callback_data=f"admin_exam_narrative_id_{id_val}")])
                    keyboard.append(admin_back_markup().inline_keyboard[0])
                    
                    context.user_data['admin_exam_create']['step'] = "select_narrative_id"
                    await update.message.reply_text(
                        "✅ تم حفظ ملف أسئلة MCQ.\n\n"
                        "الآن: اختر ID لإضافة ملف Narrative له:",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    context.user_data['admin_exam_create']['step'] = "narrative_questions"
                    await update.message.reply_text(
                        "✅ تم حفظ ملف أسئلة MCQ.\n\n"
                        "أرسل ملف CSV للأسئلة المقالية:\n"
                        "يجب أن يحتوي على: question, answer",
                        reply_markup=admin_back_markup()
                    )
            else:
                # Show yes/no buttons
                keyboard = [
                    [InlineKeyboardButton("✅ نعم", callback_data="admin_exam_add_narrative_yes")],
                    [InlineKeyboardButton("❌ لا", callback_data="admin_exam_add_narrative_no")]
                ]
                keyboard.append(admin_back_markup().inline_keyboard[0])
                
                await update.message.reply_text(
                    "✅ تم حفظ ملف أسئلة MCQ.\n\n"
                    "هل تريد إضافة أسئلة مقالية أيضاً؟ (مثل اختبار مازن)",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['admin_exam_create']['step'] = "ask_narrative"

    if step == "narrative_questions":
        # Narrative only or additional narrative
        file = await context.bot.get_file(update.message.document.file_id)
        if 'exam_id' not in context.user_data['admin_exam_create']:
            context.user_data['admin_exam_create']['exam_id'] = f"exam_{int(time.time())}"
        exam_id = context.user_data['admin_exam_create']['exam_id']
        question_id = context.user_data['admin_exam_create'].get('current_question_id')
        
        if question_id is not None:
            # Questions for specific ID
            narrative_file = f"exams/{exam_id}_narrative_id{question_id}.csv"
            os.makedirs("exams", exist_ok=True)
            # Save file to disk (method 1)
            await file.download_to_drive(narrative_file)
            # Save file_id (method 2)
            narrative_file_id = update.message.document.file_id
            if 'narrative_files_by_id' not in context.user_data['admin_exam_create']:
                context.user_data['admin_exam_create']['narrative_files_by_id'] = {}
            if 'narrative_file_ids_by_id' not in context.user_data['admin_exam_create']:
                context.user_data['admin_exam_create']['narrative_file_ids_by_id'] = {}
            context.user_data['admin_exam_create']['narrative_files_by_id'][question_id] = narrative_file
            context.user_data['admin_exam_create']['narrative_file_ids_by_id'][question_id] = narrative_file_id
            
            # Check if there are more IDs to process
            structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
            remaining_ids = [id_val for id_val in sorted(structure.keys()) 
                           if id_val not in context.user_data['admin_exam_create'].get('narrative_files_by_id', {})]
            
            if remaining_ids:
                # Show buttons for remaining IDs
                keyboard = []
                for id_val in remaining_ids:
                    keyboard.append([InlineKeyboardButton(f"✍️ ID {id_val}", callback_data=f"admin_exam_narrative_id_{id_val}")])
                keyboard.append(admin_back_markup().inline_keyboard[0])
                
                await update.message.reply_text(
                    f"✅ تم حفظ ملف أسئلة Narrative لـ ID {question_id}.\n\n"
                    f"📋 IDs المتبقية: {', '.join(map(str, remaining_ids))}\n\n"
                    f"اختر ID آخر لإضافة ملف Narrative له:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['admin_exam_create']['step'] = "select_narrative_id"
            else:
                # All IDs processed, finalize
                await finalize_exam_creation(update, context)
        else:
            # No ID specified, use single file (legacy mode)
            narrative_file = f"exams/{exam_id}_narrative.csv"
            os.makedirs("exams", exist_ok=True)
            # Save file to disk (method 1)
            await file.download_to_drive(narrative_file)
            # Save file_id (method 2)
            narrative_file_id = update.message.document.file_id
            context.user_data['admin_exam_create']['narrative_file'] = narrative_file
            context.user_data['admin_exam_create']['narrative_file_id'] = narrative_file_id
            
            # Finalize exam creation
            await finalize_exam_creation(update, context)

    elif step == "ask_narrative":
        # Handle yes/no response
        text = update.message.text.strip().lower()
        if text in ['نعم', 'yes', 'y']:
            context.user_data['admin_exam_create']['step'] = "narrative_questions"
            await update.message.reply_text(
                "✅ سأضيف أسئلة مقالية.\n\n"
                "أرسل ملف CSV للأسئلة المقالية:\n"
                "يجب أن يحتوي على: question, answer",
                reply_markup=admin_back_markup()
            )
        elif text in ['لا', 'no', 'n']:
            await finalize_exam_creation(update, context)
        else:
            await update.message.reply_text("❌ يرجى الإجابة بـ 'نعم' أو 'لا'.")

async def finalize_exam_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalize exam creation and add to menu."""
    user = update.effective_user
    exam_create = context.user_data.get('admin_exam_create', {})
    exam_id = exam_create.get('exam_id', f"exam_{int(time.time())}")
    
    # Build exam data
    exam_data = {
        "button_text": exam_create['button_text'],
        "question_type": exam_create.get('question_type', 'narrative'),
        "is_hidden": False  # Default: visible
    }
    
    if 'explanation_file' in exam_create:
        exam_data["explanation_file"] = exam_create['explanation_file']
    if 'explanation_file_id' in exam_create:
        exam_data["explanation_file_id"] = exam_create['explanation_file_id']
    if 'media_attachments' in exam_create:
        exam_data["media_attachments"] = exam_create['media_attachments']
    
    # Handle questions by ID
    if 'mcq_files_by_id' in exam_create:
        exam_data["mcq_files_by_id"] = exam_create['mcq_files_by_id']
        if 'mcq_file_ids_by_id' in exam_create:
            exam_data["mcq_file_ids_by_id"] = exam_create['mcq_file_ids_by_id']
        exam_data["question_type"] = "mcq"  # Has MCQ
    elif 'mcq_file' in exam_create:
        exam_data["mcq_file"] = exam_create['mcq_file']
        if 'mcq_file_id' in exam_create:
            exam_data["mcq_file_id"] = exam_create['mcq_file_id']
        exam_data["question_type"] = "mcq"  # Has MCQ
    
    if 'narrative_files_by_id' in exam_create:
        exam_data["narrative_files_by_id"] = exam_create['narrative_files_by_id']
        if 'narrative_file_ids_by_id' in exam_create:
            exam_data["narrative_file_ids_by_id"] = exam_create['narrative_file_ids_by_id']
        if 'mcq_file' in exam_create or 'mcq_files_by_id' in exam_create:
            exam_data["question_type"] = "both"  # Has both MCQ and Narrative
        else:
            exam_data["question_type"] = "narrative"
    elif 'narrative_file' in exam_create:
        exam_data["narrative_file"] = exam_create['narrative_file']
        if 'narrative_file_id' in exam_create:
            exam_data["narrative_file_id"] = exam_create['narrative_file_id']
        if 'mcq_file' in exam_create or 'mcq_files_by_id' in exam_create:
            exam_data["question_type"] = "both"  # Has both MCQ and Narrative
        else:
            exam_data["question_type"] = "narrative"
    
    # Save exam
    conn = context.bot_data.get('db_conn')
    exams = load_exams(conn)
    exams[exam_id] = exam_data
    save_exams(exams, conn)
    
    # Add to main menu
    conn = context.bot_data.get('db_conn')
    menus = context.bot_data.get("menus", load_menus(conn))
    buttons = menus.get("main_menu", {}).get("buttons", [])
    buttons.append({
        "text": exam_data["button_text"],
        "callback": f"dynamic_exam_{exam_id}"
    })
    set_main_menu_buttons(context, buttons)
    
    # Reload exams in bot_data
    context.bot_data["exams"] = exams
    
    # Summary
    type_desc = {
        "mcq": "MCQ فقط",
        "narrative": "مقالية فقط",
        "both": "MCQ + مقالية (مثل مازن)"
    }.get(exam_data["question_type"], "غير معروف")
    
    context.user_data.pop('admin_exam_create', None)
    
    # Add preview button
    keyboard = [
        [InlineKeyboardButton("👁️ معاينة الاختبار", callback_data=f"admin_exam_preview_{exam_id}")],
        admin_back_markup().inline_keyboard[0]
    ]
    
    await update.message.reply_text(
        f"✅ تم إنشاء الاختبار بنجاح!\n\n"
        f"اسم الزر: {exam_data['button_text']}\n"
        f"نوع الأسئلة: {type_desc}\n"
        f"تم إضافة الزر إلى القائمة الرئيسية تلقائياً.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_admin_broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    context.user_data['admin_broadcast_pending'] = True
    await context.bot.send_message(
        chat_id=user.id,
        text="أرسل نص الإعلان وسيتم بثه لكل المستخدمين.",
        reply_markup=ForceReply(selective=True)
    )
    await update.callback_query.answer()

async def handle_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CSV file uploads for exam creation or database import."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    
    # Check if admin is importing database
    if context.user_data.get('admin_importing_db'):
        await handle_admin_import_db_file(update, context)
        return
    
    if 'admin_exam_create' not in context.user_data:
        return
    
    step = context.user_data['admin_exam_create'].get('step')
    if step == "explanation":
        await handle_admin_exam_create_explanation(update, context)
    elif step in ["mcq_questions", "narrative_questions", "questions"]:
        await handle_admin_exam_create_questions(update, context)
    elif step in ["media_photo", "media_video"]:
        await handle_admin_media(update, context)

async def handle_admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo/video uploads for exam media attachments."""
    user = update.effective_user
    if not is_admin_user(user.id):
        return
    
    if 'admin_exam_create' not in context.user_data:
        return
    
    step = context.user_data['admin_exam_create'].get('step')
    if step == "media_photo":
        await handle_admin_exam_media_photo(update, context)
    elif step == "media_video":
        await handle_admin_exam_media_video(update, context)

async def handle_admin_exam_media_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'yes' response for adding media."""
    user = update.effective_user
    if not is_admin_user(user.id):
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    # Check if this is a callback query or message
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.answer()
        message = update.callback_query.message
        edit_func = update.callback_query.edit_message_text
    else:
        message = update.message
        edit_func = update.message.reply_text
    
    # Proceed to media prompt
    await handle_admin_exam_media_prompt(update, context)

async def handle_admin_exam_media_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'no' response for adding media - skip to question type selection."""
    user = update.effective_user
    if not is_admin_user(user.id):
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    # Check if this is a callback query or message
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.answer()
        message = update.callback_query.message
        edit_func = update.callback_query.edit_message_text
    else:
        message = update.message
        edit_func = update.message.reply_text
    
    # Skip media, go to question type selection
    context.user_data['admin_exam_create']['step'] = "question_type"
    
    keyboard = [
        [InlineKeyboardButton("📝 أسئلة من متعدد (MCQ)", callback_data="admin_exam_type_mcq")],
        [InlineKeyboardButton("✍️ أسئلة مقالية (Narrative)", callback_data="admin_exam_type_narrative")],
        [InlineKeyboardButton("📝 + ✍️ كليهما (مثل مازن)", callback_data="admin_exam_type_both")]
    ]
    keyboard.append(admin_back_markup().inline_keyboard[0])
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            "✅ تم تخطي إضافة الوسائط.\n\n"
            "اختر نوع الأسئلة:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "✅ تم تخطي إضافة الوسائط.\n\n"
            "اختر نوع الأسئلة:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_admin_exam_media_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt admin to add media to explanation using buttons."""
    structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
    if not structure:
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("❌ لا توجد بنية للشرح. سيتم المتابعة بدون media.", reply_markup=admin_back_markup())
        else:
            await update.message.reply_text("❌ لا توجد بنية للشرح. سيتم المتابعة بدون media.")
        context.user_data['admin_exam_create']['step'] = "question_type"
        return
    
    keyboard = []
    row = []
    for id_val in sorted(structure.keys()):
        row.append(InlineKeyboardButton(f"ID {id_val}", callback_data=f"admin_media_id_{id_val}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("✅ انتهيت", callback_data="admin_media_finish")])
    
    text = "📸 إضافة وسائط للشرح:\n\nاختر ID لعرض مستوياته:"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    context.user_data['admin_exam_create']['step'] = "media_selection"

async def handle_admin_media_select_id(update: Update, context: ContextTypes.DEFAULT_TYPE, id_val: int):
    structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
    levels = structure.get(id_val, [])
    
    keyboard = []
    row = []
    media_attachments = context.user_data['admin_exam_create'].get('media_attachments', {})
    
    for level in sorted(levels):
        media_key = f"{id_val}_{level}"
        has_media = "✅ " if media_key in media_attachments else ""
        row.append(InlineKeyboardButton(f"{has_media}Level {level}", callback_data=f"admin_media_level_{id_val}_{level}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="admin_exam_media_yes")])
    
    await update.callback_query.edit_message_text(
        f"📸 ID {id_val}: اختر المستوى لإضافة/تعديل الوسائط:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_admin_media_select_level(update: Update, context: ContextTypes.DEFAULT_TYPE, id_val: int, level: int):
    context.user_data['admin_exam_create']['pending_media'] = {'id': id_val, 'level': level}
    
    keyboard = [
        [InlineKeyboardButton("🖼️ صورة", callback_data="admin_media_type_photo")],
        [InlineKeyboardButton("🎥 فيديو", callback_data="admin_media_type_video")],
        [InlineKeyboardButton("🔗 رابط", callback_data="admin_media_type_url")],
        [InlineKeyboardButton("↩️ رجوع للمستويات", callback_data=f"admin_media_id_{id_val}")]
    ]
    
    media_attachments = context.user_data['admin_exam_create'].get('media_attachments', {})
    media_key = f"{id_val}_{level}"
    current_media = ""
    if media_key in media_attachments:
        m_type = media_attachments[media_key]['type']
        current_media = f"\n\n⚠️ يوجد ميديا حالياً: {m_type}"
    
    await update.callback_query.edit_message_text(
        f"📸 ID {id_val} - Level {level}\n\nاختر نوع الوسائط:{current_media}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_admin_media_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str):
    pending = context.user_data['admin_exam_create'].get('pending_media', {})
    id_val = pending.get('id')
    level = pending.get('level')
    
    context.user_data['admin_exam_create']['step'] = f"media_upload_{media_type}"
    
    prompt = {
        "photo": "أرسل الصورة الآن:",
        "video": "أرسل الفيديو الآن:",
        "url": "أرسل الرابط الآن (http/https):"
    }.get(media_type, "أرسل الوسائط:")
    
    keyboard = [[InlineKeyboardButton("↩️ إلغاء", callback_data=f"admin_media_level_{id_val}_{level}")]]
    
    await update.callback_query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_admin_media_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data['admin_exam_create'].get('step', '')
    if not step.startswith("media_upload_"):
        return
    
    media_type = step.replace("media_upload_", "")
    pending = context.user_data['admin_exam_create'].get('pending_media', {})
    if not pending:
        await update.message.reply_text("❌ حدث خطأ في الجلسة. ابدأ من جديد.")
        return
    
    content = None
    if media_type == "photo":
        if update.message.photo:
            content = update.message.photo[-1].file_id
        elif update.message.document and update.message.document.mime_type and 'image' in update.message.document.mime_type:
            content = update.message.document.file_id
        else:
            await update.message.reply_text("❌ يرجى إرسال صورة.")
            return
    elif media_type == "video":
        if update.message.video:
            content = update.message.video.file_id
        elif update.message.document and update.message.document.mime_type and 'video' in update.message.document.mime_type:
            content = update.message.document.file_id
        else:
            await update.message.reply_text("❌ يرجى إرسال فيديو.")
            return
    elif media_type == "url":
        content = update.message.text.strip()
        if not content.startswith(('http://', 'https://')):
            await update.message.reply_text("❌ رابط غير صحيح.")
            return
            
    # Save
    media_key = f"{pending['id']}_{pending['level']}"
    if 'media_attachments' not in context.user_data['admin_exam_create']:
        context.user_data['admin_exam_create']['media_attachments'] = {}
    context.user_data['admin_exam_create']['media_attachments'][media_key] = {
        'type': media_type,
        'content': content
    }
    
    await update.message.reply_text(f"✅ تم حفظ {media_type}.")
    
    # Return to levels list
    structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
    id_val = pending['id']
    levels = structure.get(id_val, [])
    
    keyboard = []
    row = []
    media_attachments = context.user_data['admin_exam_create'].get('media_attachments', {})
    
    for level in sorted(levels):
        m_key = f"{id_val}_{level}"
        has_media = "✅ " if m_key in media_attachments else ""
        row.append(InlineKeyboardButton(f"{has_media}Level {level}", callback_data=f"admin_media_level_{id_val}_{level}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="admin_exam_media_yes")])
    
    await update.message.reply_text(
        f"📸 ID {id_val}: اختر المستوى لإضافة/تعديل الوسائط:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['admin_exam_create']['step'] = "media_selection"

async def handle_admin_media_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_exam_create']['step'] = "question_type"
    await update.callback_query.edit_message_text(
        "✅ تم إنهاء إضافة media.\n\nاختر نوع الأسئلة:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("1️⃣ MCQ", callback_data="admin_exam_type_mcq")],
            [InlineKeyboardButton("2️⃣ Narrative", callback_data="admin_exam_type_narrative")],
            [InlineKeyboardButton("📝 + ✍️ كليهما (مثل مازن)", callback_data="admin_exam_type_both")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="admin_menu")]
        ])
    )

async def handle_admin_broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        return False
    if not context.user_data.get('admin_broadcast_pending'):
        return False
    broadcast_text = update.message.text
    if not broadcast_text:
        return True
    context.user_data['admin_broadcast_pending'] = False
    user_ids = get_all_user_ids(context.bot_data['db_conn'])
    if not user_ids:
        await update.message.reply_text("لا يوجد مستخدمون لإرسال الإعلان.", reply_markup=admin_back_markup())
        return True
    await update.message.reply_text(f"يتم الإرسال إلى {len(user_ids)} مستخدم... قد يستغرق ذلك لحظات.", reply_markup=admin_back_markup())
    sent, failed = await send_broadcast_message(context.bot, user_ids, broadcast_text, rate_limit_sleep=0.05)
    await update.message.reply_text(f"اكتمل الإرسال ✅\nنجحت: {sent}\nفشلت: {failed}", reply_markup=admin_back_markup())
    return True

async def handle_admin_results_search_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_user(user.id):
        return False
    state = context.user_data.get('admin_results', {})
    if not state.get("search"):
        return False
    query = (update.message.text or "").strip()
    if not query:
        await update.message.reply_text("الرجاء إرسال ID أو نص للبحث.")
        return True
    conn = context.bot_data['db_conn']
    cursor = conn.cursor()
    results = []
    try:
        if query.isdigit():
            uid = int(query)
            for table in ["user_progress", "lab_results", "mazen_results"]:
                rows = cursor.execute(f"SELECT * FROM {table} WHERE user_id = ?", (uid,)).fetchall()
                if rows:
                    col_names = [d[0] for d in cursor.description]
                    results.append((table, rows, col_names))
        else:
            like_q = f"%{query}%"
            for table in ["user_progress", "lab_results", "mazen_results"]:
                rows = cursor.execute(
                    f"SELECT * FROM {table} WHERE first_name LIKE ? OR username LIKE ?",
                    (like_q, like_q)
                ).fetchall()
                if rows:
                    col_names = [d[0] for d in cursor.description]
                    results.append((table, rows, col_names))
    except Exception as e:
        logging.error(f"Search failed: {e}")

    if not results:
        await update.message.reply_text("لا توجد نتائج مطابقة.", reply_markup=admin_back_markup())
        return True

    messages = []
    for table, rows, cols in results:
        text = f"📚 نتائج {escape_markdown(table, version=2)}\n" + format_rows_as_md(table, rows[:10], cols, 0, 10, len(rows))
        messages.append(text)

    await update.message.reply_text("\n\n".join(messages), parse_mode="MarkdownV2", reply_markup=admin_back_markup())
    context.user_data['admin_results'] = {}
    return True

async def handle_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 'بدء' button press - redirects to /start command."""
    debug_log("handle_start_button", "Function called", {
        "user_id": update.effective_user.id,
        "text": update.message.text.strip() if update.message.text else None,
        "is_admin": is_admin_user(update.effective_user.id),
        "has_admin_exam_create": 'admin_exam_create' in context.user_data
    }, "D")
    
    # If admin is in exam creation flow, don't handle - let admin_text handle it
    if is_admin_user(update.effective_user.id) and 'admin_exam_create' in context.user_data:
        step = context.user_data['admin_exam_create'].get('step')
        logging.info(f"handle_start_button: Admin in exam creation flow, passing to admin_text handler. Step: {step}")
        debug_log("handle_start_button", "Admin in exam creation flow, returning False", {"step": step}, "D")
        return False  # Let admin_text handler process it
    
    if is_admin_user(update.effective_user.id) and 'admin_no_explanation' in context.user_data:
        await handle_admin_exam_save_no_explanation(update, context)
        return True
    
    # Only handle if it's the start button text
    text = update.message.text.strip()
    debug_log("handle_start_button", "Checking if text is start button", {"text": text, "is_start": text in ["بدء", "🏠 بدء"]}, "D")
    if text in ["بدء", "🏠 بدء"]:
        debug_log("handle_start_button", "Calling start function", {}, "D")
        await start(update, context)
        debug_log("handle_start_button", "start function completed", {}, "D")
        return True  # Stop further processing
    
    # For other text messages, don't handle (let other handlers process)
    debug_log("handle_start_button", "Not a start button, returning False", {}, "D")
    return False

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debug_log("handle_admin_text", "Function called", {
        "user_id": update.effective_user.id,
        "text": update.message.text[:50] if update.message.text else 'None'
    }, "F")
    logging.info(f"handle_admin_text called. User: {update.effective_user.id}, Text: {update.message.text[:50] if update.message.text else 'None'}")
    
    # If it's the start button, call start directly
    text = update.message.text.strip() if update.message.text else ""
    if text in ["بدء", "🏠 بدء"]:
        logging.info(f"handle_admin_text: Start button detected, calling start function")
        debug_log("handle_admin_text", "Start button detected, calling start", {}, "F")
        await start(update, context)
        debug_log("handle_admin_text", "start function completed", {}, "F")
        return True  # Stop further processing
    
    # Check for no-explanation button creation flow
    if 'admin_no_explanation' in context.user_data:
        await handle_admin_exam_save_no_explanation(update, context)
        return True
    
    # Check for exam creation flow
    if 'admin_exam_create' in context.user_data:
        step = context.user_data['admin_exam_create'].get('step')
        logging.info(f"handle_admin_text: Admin in exam creation flow, step: {step}")
        if step == "name":
            logging.info(f"handle_admin_text: Calling handle_admin_exam_create_name")
            await handle_admin_exam_create_name(update, context)
            return True
        elif step == "explanation":
            await handle_admin_exam_create_explanation(update, context)
            return True
        elif step == "questions":
            await handle_admin_exam_create_questions(update, context)
            return True
        elif step == "ask_mcq_id":
            # Receive ID for MCQ questions
            try:
                question_id = int(update.message.text.strip())
                structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
                if question_id not in structure:
                    await update.message.reply_text(
                        f"❌ ID {question_id} غير موجود في بنية الشرح.\n"
                        f"IDs المتاحة: {', '.join(map(str, sorted(structure.keys())))}\n"
                        f"أرسل ID صحيح:",
                        reply_markup=admin_back_markup()
                    )
                    return True
                context.user_data['admin_exam_create']['current_question_id'] = question_id
                context.user_data['admin_exam_create']['step'] = "mcq_questions"
                await update.message.reply_text(
                    f"✅ تم تحديد ID {question_id} لأسئلة MCQ.\n\n"
                    f"أرسل ملف CSV للأسئلة من متعدد:\n"
                    f"يجب أن يحتوي على: question, option_a, option_b, option_c, option_d, correct_answer, "
                    f"correct_explanation, concept_explanation, explanation_a, explanation_b, explanation_c, explanation_d",
                    reply_markup=admin_back_markup()
                )
                return True
            except ValueError:
                await update.message.reply_text(
                    "❌ يرجى إرسال رقم ID صحيح (مثال: 1)",
                    reply_markup=admin_back_markup()
                )
                return True
        elif step == "ask_narrative_id":
            # Receive ID for Narrative questions
            try:
                question_id = int(update.message.text.strip())
                structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
                if question_id not in structure:
                    await update.message.reply_text(
                        f"❌ ID {question_id} غير موجود في بنية الشرح.\n"
                        f"IDs المتاحة: {', '.join(map(str, sorted(structure.keys())))}\n"
                        f"أرسل ID صحيح:",
                        reply_markup=admin_back_markup()
                    )
                    return True
                context.user_data['admin_exam_create']['current_question_id'] = question_id
                context.user_data['admin_exam_create']['step'] = "narrative_questions"
                await update.message.reply_text(
                    f"✅ تم تحديد ID {question_id} لأسئلة Narrative.\n\n"
                    f"أرسل ملف CSV للأسئلة المقالية:\n"
                    f"يجب أن يحتوي على: question, answer",
                    reply_markup=admin_back_markup()
                )
                return True
            except ValueError:
                await update.message.reply_text(
                    "❌ يرجى إرسال رقم ID صحيح (مثال: 1)",
                    reply_markup=admin_back_markup()
                )
                return True
        elif step == "ask_media":
            # Handle yes/no text response (fallback if buttons weren't used)
            text = update.message.text.strip().lower()
            if text in ['نعم', 'yes', 'y', 'ن']:
                await handle_admin_exam_media_yes(update, context)
                return True
            elif text in ['لا', 'no', 'n', 'ل']:
                await handle_admin_exam_media_no(update, context)
                return True
            else:
                await update.message.reply_text(
                    "❌ يرجى اختيار 'نعم' أو 'لا' من الأزرار أعلاه.",
                    reply_markup=admin_back_markup()
                )
                return True
    # Route admin text to broadcast or search flows
    if not is_admin_user(update.effective_user.id):
        return
    # Pending menu edits
    pending = context.user_data.get("admin_edit_pending")
    if pending:
        action = pending.get("action")
        text = (update.message.text or "").strip()
        buttons = main_menu_buttons(context)
        if action == "add_main":
            if "|" not in text:
                await update.message.reply_text("اكتب بالشكل: النص|الإجراء (callback) أو الرابط")
            else:
                txt, target = [p.strip() for p in text.split("|", 1)]
                new_btn = {"text": txt}
                if target.startswith("http"):
                    new_btn["url"] = target
                else:
                    new_btn["callback"] = target
                buttons.append(new_btn)
                set_main_menu_buttons(context, buttons)
                await update.message.reply_text("✅ تمت الإضافة.")
        elif action == "rename_main":
            if "|" not in text:
                await update.message.reply_text("اكتب بالشكل: رقم|نص جديد", reply_markup=admin_back_markup())
            else:
                idx_str, new_txt = [p.strip() for p in text.split("|", 1)]
                if idx_str.isdigit() and int(idx_str) < len(buttons):
                    buttons[int(idx_str)]["text"] = new_txt
                    set_main_menu_buttons(context, buttons)
                    await update.message.reply_text("✅ تم التعديل.", reply_markup=admin_back_markup())
                else:
                    await update.message.reply_text("⚠️ رقم غير صالح.", reply_markup=admin_back_markup())
        elif action == "action_main":
            if "|" not in text:
                await update.message.reply_text("اكتب بالشكل: رقم|الإجراء الجديد أو الرابط", reply_markup=admin_back_markup())
            else:
                idx_str, target = [p.strip() for p in text.split("|", 1)]
                if idx_str.isdigit() and int(idx_str) < len(buttons):
                    btn = buttons[int(idx_str)]
                    btn.pop("callback", None)
                    btn.pop("url", None)
                    if target.startswith("http"):
                        btn["url"] = target
                    else:
                        btn["callback"] = target
                    set_main_menu_buttons(context, buttons)
                    await update.message.reply_text("✅ تم التعديل.", reply_markup=admin_back_markup())
                else:
                    await update.message.reply_text("⚠️ رقم غير صالح.", reply_markup=admin_back_markup())
        elif action == "delete_main":
            if not text.isdigit() or int(text) >= len(buttons):
                await update.message.reply_text("⚠️ رقم غير صالح.", reply_markup=admin_back_markup())
            else:
                del buttons[int(text)]
                set_main_menu_buttons(context, buttons)
                await update.message.reply_text("🗑 تم الحذف.", reply_markup=admin_back_markup())
        elif action == "move_main":
            if "|" not in text:
                await update.message.reply_text("اكتب بالشكل: رقمحالي|رقمجديد", reply_markup=admin_back_markup())
            else:
                a, b = [p.strip() for p in text.split("|", 1)]
                if a.isdigit() and b.isdigit() and int(a) < len(buttons) and int(b) < len(buttons):
                    btn = buttons.pop(int(a))
                    buttons.insert(int(b), btn)
                    set_main_menu_buttons(context, buttons)
                    await update.message.reply_text("✅ تم النقل.", reply_markup=admin_back_markup())
                else:
                    await update.message.reply_text("⚠️ أرقام غير صالحة.", reply_markup=admin_back_markup())
        elif action == "add_submenu":
            # Format: رقم|نص الزر الرئيسي (button_index already stored in pending)
            if "|" not in text:
                await update.message.reply_text("اكتب بالشكل: رقم|نص الزر الرئيسي", reply_markup=admin_back_markup())
            else:
                idx_str, main_text = [p.strip() for p in text.split("|", 1)]
                button_index = pending.get("button_index")
                if button_index is None or button_index >= len(buttons):
                    await update.message.reply_text("❌ خطأ: الزر غير موجود.", reply_markup=admin_back_markup())
                    context.user_data.pop("admin_edit_pending", None)
                    return
                btn = buttons[button_index]
                # Check if button already has submenu
                if "submenu" not in btn:
                    btn["submenu"] = []
                # Generate submenu_id if not exists
                if "submenu_id" not in btn:
                    import hashlib
                    btn["submenu_id"] = f"submenu_{hashlib.md5(str(btn).encode()).hexdigest()[:8]}"
                btn["text"] = main_text
                # Store submenu_id in context for adding submenu buttons
                context.user_data["admin_edit_pending"] = {
                    "action": "add_submenu_items",
                    "button_index": button_index,
                    "submenu_id": btn["submenu_id"]
                }
                set_main_menu_buttons(context, buttons)
                await update.message.reply_text(
                    f"✅ تم إنشاء قائمة فرعية للزر '{main_text}'.\n\n"
                    f"الآن أرسل الأزرار الفرعية بالشكل:\n"
                    f"النص|الإجراء (callback) أو الرابط\n"
                    f"لكل زر فرعي في سطر منفصل.\n"
                    f"أرسل 'تم' عند الانتهاء.",
                    reply_markup=admin_back_markup()
                )
                return  # Don't pop pending, continue to add_submenu_items
        elif action == "add_submenu_items":
            # Adding items to submenu
            button_index = pending.get("button_index")
            if button_index is None or button_index >= len(buttons):
                await update.message.reply_text("❌ خطأ: الزر غير موجود.", reply_markup=admin_back_markup())
                context.user_data.pop("admin_edit_pending", None)
                return
            
            if text.lower() in ['تم', 'done', 'انتهى']:
                # Finished adding submenu items
                context.user_data.pop("admin_edit_pending", None)
                set_main_menu_buttons(context, buttons)
                await update.message.reply_text("✅ تم حفظ القائمة الفرعية.", reply_markup=admin_back_markup())
            else:
                # Add submenu item
                if "|" not in text:
                    await update.message.reply_text("اكتب بالشكل: النص|الإجراء (callback) أو الرابط\nأو أرسل 'تم' عند الانتهاء.")
                else:
                    txt, target = [p.strip() for p in text.split("|", 1)]
                    submenu_item = {"text": txt}
                    if target.startswith("http"):
                        submenu_item["url"] = target
                    else:
                        submenu_item["callback"] = target
                    btn = buttons[button_index]
                    if "submenu" not in btn:
                        btn["submenu"] = []
                    btn["submenu"].append(submenu_item)
                    set_main_menu_buttons(context, buttons)
                    await update.message.reply_text(f"✅ تم إضافة '{txt}' للقائمة الفرعية.\nأرسل زر فرعي آخر أو 'تم' عند الانتهاء.")
                return  # Don't pop pending, continue adding items
        
        # Only pop if action is completed (not for add_submenu_items which continues)
        if action not in ["add_submenu_items"]:
            context.user_data.pop("admin_edit_pending", None)
            menus = context.bot_data.get("menus", default_menus())
            overview = render_main_menu_admin_view(menus)
            await update.message.reply_text(f"الحالة الحالية للقائمة:\n{overview}", reply_markup=admin_back_markup())
        return

    handled = await handle_admin_broadcast_receive(update, context)
    if handled:
        debug_log("handle_admin_text", "Broadcast handled, returning True", {}, "F")
        return True
    handled = await handle_admin_results_search_receive(update, context)
    if handled:
        debug_log("handle_admin_text", "Search handled, returning True", {}, "F")
        return True
    # No pending admin text action; let other handlers process
    debug_log("handle_admin_text", "No action handled, returning False to let other handlers process", {}, "F")
    return False

async def send_level_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_msg = f"أهلاً بك يا {user.first_name}! 📡\n\nاختر مستوى الصعوبة للبدء."
    keyboard = [
        [
            InlineKeyboardButton("صعب 🔥", callback_data="level_hard"),
            InlineKeyboardButton("متوسط 🧠", callback_data="level_medium"),
            InlineKeyboardButton("سهل ✅", callback_data="level_easy")
        ],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="previous_tests_back")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debug_log("start", "Function called", {
        "user_id": update.effective_user.id,
        "is_admin": is_admin_user(update.effective_user.id),
        "has_message": hasattr(update, 'message') and update.message is not None
    }, "E")
    
    user = update.effective_user

    conn = context.bot_data['db_conn']
    context.user_data.update(get_user_state(user.id, user.first_name, conn))
    debug_log("start", "User state loaded", {}, "E")
    
    # Maintenance mode gate
    if MAINTENANCE_MODE and not is_admin_user(user.id):
        debug_log("start", "Maintenance mode active for non-admin", {}, "E")
        await update.message.reply_text("🚧 البوت في وضع الصيانة حالياً. الرجاء المحاولة لاحقاً.")
        return

    # Admin quick choice (admin panel or continue as user)
    if is_admin_user(user.id):
        debug_log("start", "Admin user detected, showing admin choice", {}, "E")
        keyboard = [
            [InlineKeyboardButton("🔧 لوحة الأدمن", callback_data="admin_menu")],
            [InlineKeyboardButton("👤 استخدام كباقي المستخدمين", callback_data="main_menu")],
        ]
        reply_keyboard = get_start_reply_keyboard()
        debug_log("start", "Sending admin choice message", {}, "E")
        await update.message.reply_text("أهلاً أدمن! اختر وضع الدخول:", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("استخدم الزر أدناه للعودة للبداية:", reply_markup=reply_keyboard)
        debug_log("start", "Admin messages sent", {}, "E")
        return
    
    reply_keyboard = get_start_reply_keyboard()
    if await check_subscription(user.id, context):
        await send_main_menu(update, context) # Changed this line
    else:
        await send_subscription_prompt(update, context)
        # Send reply keyboard after subscription prompt
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="استخدم الزر أدناه للعودة للبداية:",
                reply_markup=reply_keyboard
            )
        except Exception as e:
            logging.warning(f"Could not send reply keyboard: {e}")

async def send_previous_tests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = "اختر أحد الاختبارات التالية:"
    keyboard = [
        [InlineKeyboardButton("اختبار نظري الاتصالات حديثة", callback_data="start_theory_test")],
        [InlineKeyboardButton("اختبار مخبر الاتصالات 🔬", callback_data="lab_test_menu")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text(menu_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def send_lab_test_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = "اختر أحد الفيديوهات:"
    keyboard = [
        [InlineKeyboardButton("الفيديو الأول 1️⃣", callback_data="video_1")],
        [InlineKeyboardButton("الفيديو الثاني 2️⃣", callback_data="video_2")],
        [InlineKeyboardButton("الفيديو الثالث 3️⃣", callback_data="video_3")],
        [InlineKeyboardButton("الفيديو الرابع 4️⃣", callback_data="video_4")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text(menu_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start_mazen_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initializes and starts the 'Mazen Test' flow."""
    context.user_data['mazen_test'] = {
        'current_id': 1,
        'text_level': 1,
        'state': 'intro_text' # intro_text, mcq_quiz, srd_quiz
    }
    # We need to send the first text, we'll use the callback query from the initial button press
    await send_mazen_text(update, context)

async def send_mazen_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a part of the Mazen test explanatory text."""
    query = update.callback_query
    mazen_state = context.user_data.get('mazen_test', {})
    
    current_id = mazen_state.get('current_id', 1)
    current_level = mazen_state.get('text_level', 1)
    
    all_texts = context.bot_data.get('mazen_texts', {})
    
    try:
        text_to_send = all_texts[current_id][current_level]
    except KeyError:
        await query.edit_message_text("عفواً، المحتوى غير متوفر حالياً.")
        # Optionally, reset or guide the user back
        return

    # Check if there is a next level for the current ID
    next_level_exists = (current_level + 1) in all_texts.get(current_id, {})
    
    if next_level_exists:
        # Update state for the next part of the text
        context.user_data['mazen_test']['text_level'] = current_level + 1
        keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data=f"mazin_continue_text")]]
    else:
        # This is the last text level for this ID, move to MCQ state
        context.user_data['mazen_test']['state'] = 'mcq_quiz'
        keyboard = [[InlineKeyboardButton("نكمل للاختبار أسئلة من متعدد 📝", callback_data=f"mazin_start_mcq")]]

    if query:
        # If it's a new test, query might not be the message we want to edit.
        # Let's edit the message that the 'mazin_test' button was on.
        await query.edit_message_text(text_to_send, reply_markup=InlineKeyboardMarkup(keyboard))
    else: # Should not happen in this flow, but as a fallback
        await update.effective_message.reply_text(text_to_send, reply_markup=InlineKeyboardMarkup(keyboard))


async def send_mazen_srd_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a narrative question for the Mazen test."""
    query = update.callback_query
    mazen_state = context.user_data.get('mazen_test', {})
    current_id = mazen_state.get('current_id', 1)
    q_index = mazen_state.get('srd_q_index', 0)

    srd_questions = context.bot_data['mazen_srd'].get(current_id, [])

    if q_index >= len(srd_questions):
        # Finished all narrative questions for this ID, move to the next ID flow
        await move_to_next_mazen_id(update, context)
        return

    question_data = srd_questions[q_index]
    question_text = f"💬 **السؤال السردي {q_index + 1}/{len(srd_questions)}**\n\n{question_data['question']}"
    
    keyboard = [[InlineKeyboardButton("🤔 اظهر الاجابة", callback_data="mazin_show_srd_answer")]]

    await query.edit_message_text(text=question_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_mazen_srd_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the answer to a narrative question and the 'next' button."""
    query = update.callback_query
    mazen_state = context.user_data.get('mazen_test', {})
    current_id = mazen_state.get('current_id', 1)
    q_index = mazen_state.get('srd_q_index', 0)

    srd_questions = context.bot_data['mazen_srd'].get(current_id, [])
    
    if q_index >= len(srd_questions): # Should not happen, but for safety
        await move_to_next_mazen_id(update, context)
        return

    question_data = srd_questions[q_index]
    full_text = f"💬 **السؤال:** {question_data['question']}\n\n💡 **الإجابة:** {question_data['answer']}"
    
    # Increment index for the next question
    context.user_data['mazen_test']['srd_q_index'] += 1

    if context.user_data['mazen_test']['srd_q_index'] < len(srd_questions):
        keyboard = [[InlineKeyboardButton("السؤال التالي ⬅️", callback_data="mazin_next_srd_q")]]
    else:
        keyboard = [[InlineKeyboardButton("نكمل شرح 📖", callback_data="mazin_finish_srd")]]
    
    await query.edit_message_text(text=full_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def move_to_next_mazen_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Moves the user to the next ID in the Mazen test flow or ends the test."""
    query = update.callback_query
    mazen_state = context.user_data.get('mazen_test', {})
    current_id = mazen_state.get('current_id', 1)
    
    next_id = current_id + 1
    
    all_texts = context.bot_data.get('mazen_texts', {})

    if next_id in all_texts:
        # There is a next ID to move to
        context.user_data['mazen_test'] = {
            'current_id': next_id,
            'text_level': 1,
            'state': 'intro_text'
        }
        
        final_text = f"✅ أحسنت! لقد أكملت الوحدة رقم {current_id}."
        keyboard = [[InlineKeyboardButton("نكمل شرح للوحدة التالية 📖", callback_data="mazin_continue_text")]]
        await query.edit_message_text(text=final_text, reply_markup=InlineKeyboardMarkup(keyboard))

    else:
        # All IDs are finished, end the test
        final_text = "🎉🎉🎉\n\n**لقد أكملت اختبار أ.مازن بنجاح!**\n\nأحسنت صنعاً، يمكنك الآن العودة للبداية."
        keyboard = [[InlineKeyboardButton("العودة للبداية ↩️", callback_data="main_menu")]]
        
        if 'mazen_test' in context.user_data:
            del context.user_data['mazen_test']
            
        await query.edit_message_text(text=final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_mazen_mcq_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the multiple-choice quiz for the current Mazen test ID."""
    query = update.callback_query
    user = query.from_user
    conn = context.bot_data['db_conn']
    mazen_state = context.user_data.get('mazen_test', {})
    current_id = mazen_state.get('current_id', 1)
    
    difficulty = f'mazin_id{current_id}'

    if not context.bot_data['questions'].get(difficulty) or not context.bot_data['questions'][difficulty]:
        await query.edit_message_text(f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
        return

    # Delete the message with the 'start mcq' button
    await query.delete_message()

    reset_user_progress(user.id, difficulty, conn)
    # We need to clear and update user_data for the quiz
    context.user_data.clear()
    state = get_user_state(user.id, user.first_name, conn)
    context.user_data.update(state)
    context.user_data['difficulty'] = difficulty
    # VERY IMPORTANT: Persist the mazen_test state across the quiz
    context.user_data['mazen_test'] = mazen_state 

    await send_question_view(update, context, is_new_quiz=True)

# ------------------- Dynamic Exam Runners -------------------

async def start_dynamic_exam(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Start a dynamic exam - similar to start_mazen_test."""
    query = update.callback_query
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    if not exam or not exam_data:
        await query.edit_message_text("❌ الاختبار غير موجود.")
        return
    
    # Initialize exam state
    context.user_data['dynamic_exam'] = {
        'exam_id': exam_id,
        'current_id': 1,
        'text_level': 1,
        'state': 'intro_text',
        'question_type': question_type
    }
    
    # Store exam data in context for later use
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data
    
    # Delete the button message first
    await query.delete_message()
    
    await send_dynamic_exam_text(update, context, exam_id)

async def start_dynamic_exam_no_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Start a dynamic exam directly without explanation - goes straight to questions."""
    query = update.callback_query
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    if not exam or not exam_data:
        await query.edit_message_text("❌ الاختبار غير موجود.")
        return
    
    # Initialize exam state - skip explanation, go directly to questions
    context.user_data['dynamic_exam'] = {
        'exam_id': exam_id,
        'current_id': 1,
        'text_level': 1,
        'state': 'mcq_quiz',  # Start directly with MCQ
        'question_type': question_type,
        'mcq_q_index': 0,
        'score': 0,
        'no_explanation': True  # Flag to indicate this exam started without explanation
    }
    
    # Store exam data in context for later use
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data
    
    # Don't send "no cheating" message for no-explanation exams (no explanation messages to delete)
    # Start MCQ quiz directly (handle_dynamic_exam_mcq_start will delete the button message)
    await handle_dynamic_exam_mcq_start(update, context, exam_id, None)

async def send_dynamic_exam_text(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Send explanation text for dynamic exam - similar to send_mazen_text."""
    debug_log("send_dynamic_exam_text", "Function called", {"exam_id": exam_id, "has_callback_query": update.callback_query is not None}, "F")
    logging.info(f"send_dynamic_exam_text called with exam_id: {exam_id}")
    query = update.callback_query
    if not query:
        debug_log("send_dynamic_exam_text", "No callback_query", {}, "F")
        logging.error("No callback_query in update!")
        return
    logging.info(f"query.data: {query.data}")
    exam_state = context.user_data.get('dynamic_exam', {})
    debug_log("send_dynamic_exam_text", "Exam state", {"exam_state_keys": list(exam_state.keys())}, "F")
    
    # Ensure dynamic_exam state exists and is valid
    if not exam_state or not exam_state.get('exam_id'):
        # Initialize if completely missing
        conn = context.bot_data.get('db_conn')
        exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
        if not exam:
            if query:
                try:
                    await query.answer("❌ الاختبار غير موجود.", show_alert=True)
                except:
                    pass
            return
        if not exam_data:
            exam_data = {"texts": {}, "mcq_questions": [], "narrative_questions": []}
        context.user_data['dynamic_exam'] = {
            'exam_id': exam_id,
            'current_id': 1,
            'text_level': 1,
            'state': 'intro_text',
            'question_type': question_type
        }
        if 'dynamic_exams_data' not in context.bot_data:
            context.bot_data['dynamic_exams_data'] = {}
        context.bot_data['dynamic_exams_data'][exam_id] = exam_data
        exam_state = context.user_data['dynamic_exam']
    elif exam_state.get('exam_id') != exam_id:
        # Different exam, reinitialize
        conn = context.bot_data.get('db_conn')
        exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
        if not exam:
            if query:
                try:
                    await query.answer("❌ الاختبار غير موجود.", show_alert=True)
                except:
                    pass
            return
        if not exam_data:
            exam_data = {"texts": {}, "mcq_questions": [], "narrative_questions": []}
        context.user_data['dynamic_exam'] = {
            'exam_id': exam_id,
            'current_id': 1,
            'text_level': 1,
            'state': 'intro_text',
            'question_type': question_type
        }
        if 'dynamic_exams_data' not in context.bot_data:
            context.bot_data['dynamic_exams_data'] = {}
        context.bot_data['dynamic_exams_data'][exam_id] = exam_data
        exam_state = context.user_data['dynamic_exam']
    
    current_id = exam_state.get('current_id', 1)
    current_level = exam_state.get('text_level', 1)

    # Debug logging
    logging.info(f"send_dynamic_exam_text: exam_id={exam_id}, current_id={current_id}, current_level={current_level}, exam_state={exam_state}")

    # Always reload exam data to ensure it's fresh
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    if not exam:
        if query:
            try:
                await query.answer("❌ الاختبار غير موجود.", show_alert=True)
            except:
                pass
        return

    if not exam_data:
        exam_data = {"texts": {}, "mcq_questions": [], "narrative_questions": []}

    # Store exam data in context for later use
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data

    all_texts = exam_data.get('texts', {})

    # Debug logging for texts
    logging.info(f"all_texts keys: {list(all_texts.keys()) if all_texts else 'empty'}")
    if all_texts and current_id in all_texts:
        logging.info(f"texts for current_id {current_id}: {list(all_texts[current_id].keys())}")
        logging.info(f"next_level_exists check: current_level={current_level}, next_level={current_level + 1}, exists={(current_level + 1) in all_texts.get(current_id, {})}")

    # Check if there are any texts at all
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    if not exam:
        if query:
            try:
                await query.answer("❌ الاختبار غير موجود.", show_alert=True)
            except:
                pass
        return
    
    if not exam_data:
        exam_data = {"texts": {}, "mcq_questions": [], "narrative_questions": []}
    
    # Store exam data in context for later use
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data
    
    all_texts = exam_data.get('texts', {})
    
    if not all_texts:
        # No explanation texts at all, go directly to questions
        logging.info("No explanation texts at all, going to quiz automatically")
        exam_state['state'] = 'mcq_quiz' if exam_state.get('question_type') == 'mcq' else 'srd_quiz'
        
        # Get exam data to check available questions
        exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id, {})
        mcq_questions = exam_data.get('mcq_questions', [])
        narrative_questions = exam_data.get('narrative_questions', [])
        
        has_mcq = len(mcq_questions) > 0
        has_narrative = len(narrative_questions) > 0
        
        # Start MCQ first if available, otherwise start Narrative
        if has_mcq:
            # Create a mock update object with effective_user
            if not hasattr(update, 'effective_user') or not update.effective_user:
                class MockUpdate:
                    def __init__(self, user):
                        self.effective_user = user
                        self.callback_query = type('MockQuery', (), {'from_user': user})()
                update = MockUpdate(query.from_user if query else update.effective_user)
            await handle_dynamic_exam_mcq_start(update, context, exam_id, None)
        elif has_narrative:
            # Create a mock update object with effective_user
            if not hasattr(update, 'effective_user') or not update.effective_user:
                class MockUpdate:
                    def __init__(self, user):
                        self.effective_user = user
                        self.callback_query = None
                update = MockUpdate(query.from_user if query else update.effective_user)
            await handle_dynamic_exam_srd_start(update, context, exam_id, None)
        return

    if current_id not in all_texts:
        # No texts for this specific ID, skip to next ID
        logging.info(f"No texts found for current_id {current_id}, all_texts keys: {list(all_texts.keys())}")
        all_ids = sorted(all_texts.keys())
        current_id_index = all_ids.index(current_id) if current_id in all_ids else -1
        if current_id_index >= 0 and current_id_index < len(all_ids) - 1:
            next_id = all_ids[current_id_index + 1]
            context.user_data['dynamic_exam']['current_id'] = next_id
            context.user_data['dynamic_exam']['text_level'] = 1
            keyboard = [[InlineKeyboardButton("نكمل شرح للوحدة التالية 📖", callback_data=f"dynamic_exam_continue_{exam_id}")]]
        else:
            # Last ID and no texts, end
            keyboard = [[InlineKeyboardButton("↩️ رجوع", callback_data="main_menu")]]
            if query:
                try:
                    await query.answer()
                    await context.bot.send_message(chat_id=query.message.chat_id, text="✅ انتهى الشرح!", reply_markup=InlineKeyboardMarkup(keyboard))
                except:
                    pass
            return

        # Send new message instead of editing
        if query:
            try:
                await query.answer()
                await context.bot.send_message(chat_id=query.message.chat_id, text="✅ جاهز للاختبار!", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logging.error(f"Error sending message in send_dynamic_exam_text: {e}")
        return
    
    try:
        text_to_send = all_texts[current_id][current_level]
        logging.info(f"Found text for ID {current_id}, level {current_level}: length={len(text_to_send)}")
    except KeyError:
        logging.error(f"Text not found for ID {current_id}, level {current_level}. Available: {all_texts.get(current_id, {})}")
        if query:
            try:
                await query.answer("⚠️ المحتوى غير متوفر حالياً.", show_alert=True)
            except:
                pass
        return
    
    # Check if there is a next level for the current ID
    current_id_texts = all_texts.get(current_id, {})
    next_level_exists = (current_level + 1) in current_id_texts

    logging.info(f"current_id_texts for id {current_id}: {list(current_id_texts.keys()) if current_id_texts else 'empty'}")
    logging.info(f"current_level: {current_level}, next_level_exists: {next_level_exists}")

    # Don't add button to explanation message - button will be in separate message
    keyboard = None
    
    if next_level_exists:
        # Update state for the next part of the text
        context.user_data['dynamic_exam']['text_level'] = current_level + 1
        has_mcq = False
        has_narrative = False
    else:
        # This is the last text level for current ID
        # Check what question types are available for this ID from pre-loaded data
        mcq_questions = exam_data.get('mcq_questions', [])
        narrative_questions = exam_data.get('narrative_questions', [])

        # Filter questions for current ID
        mcq_for_id = [q for q in mcq_questions if str(q.get('id', '')) == str(current_id)]
        narrative_for_id = [q for q in narrative_questions if str(q.get('id', '')) == str(current_id)]

        logging.info(f"MCQ questions for ID {current_id}: {len(mcq_for_id)} questions")
        logging.info(f"Narrative questions for ID {current_id}: {len(narrative_for_id)} questions")

        has_mcq = len(mcq_for_id) > 0
        has_narrative = len(narrative_for_id) > 0

        debug_log("send_dynamic_exam_text", f"End of text for ID {current_id}", {"has_mcq": has_mcq, "has_narrative": has_narrative, "mcq_count": len(mcq_for_id), "narrative_count": len(narrative_for_id)}, "F")

        logging.info(f"End of text for ID {current_id}: has_mcq={has_mcq}, has_narrative={has_narrative}")
        logging.info(f"MCQ count: {len(mcq_for_id)}, Narrative count: {len(narrative_for_id)}")

        # Set default text_to_send
        text_to_send = f"✅ انتهى شرح الوحدة {current_id}!"

        if has_mcq or has_narrative:
            debug_log("send_dynamic_exam_text", f"Will start quiz automatically for ID {current_id}", {"has_mcq": has_mcq, "has_narrative": has_narrative}, "F")
            logging.info(f"Has questions for ID {current_id}, will start quiz automatically")
            # Has questions for this ID, start quiz automatically
            logging.info(f"Found questions for ID {current_id}: MCQ={has_mcq}, Narrative={has_narrative}")

            # Send completion message
            completion_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ انتهى شرح الوحدة {current_id}!\n\n📝 الآن سنبدأ اختبار هذه الوحدة..."
            )
            # Add to cleanup messages so it gets deleted when moving to next unit
            add_cleanup_msg(context, completion_msg.message_id)

            # Start MCQ first if available, otherwise start Narrative
            if has_mcq:
                logging.info(f"Starting MCQ automatically for ID {current_id}")
                # Create a mock update object with effective_user for send_question_view
                if not hasattr(update, 'effective_user') or not update.effective_user:
                    class MockUpdate:
                        def __init__(self, user):
                            self.effective_user = user
                            self.callback_query = type('MockQuery', (), {'from_user': user})()
                    update = MockUpdate(query.from_user)
                await handle_dynamic_exam_mcq_start(update, context, exam_id, current_id)
            elif has_narrative:
                logging.info(f"Starting Narrative automatically for ID {current_id}")
                # Create a mock update object with effective_user for send_question_view
                if not hasattr(update, 'effective_user') or not update.effective_user:
                    class MockUpdate:
                        def __init__(self, user):
                            self.effective_user = user
                            self.callback_query = None
                    update = MockUpdate(query.from_user)
                await handle_dynamic_exam_srd_start(update, context, exam_id, current_id)
            return
        else:
            # No questions for this ID, check if there's a next ID
            logging.info(f"No questions found for ID {current_id}, checking next ID")
            all_ids = sorted(all_texts.keys())
            current_id_index = all_ids.index(current_id) if current_id in all_ids else -1
            next_id_exists = current_id_index >= 0 and current_id_index < len(all_ids) - 1
            
            if next_id_exists:
                # Move to next ID
                next_id = all_ids[current_id_index + 1]
                context.user_data['dynamic_exam']['current_id'] = next_id
                context.user_data['dynamic_exam']['text_level'] = 1
                keyboard = [[InlineKeyboardButton("نكمل شرح للوحدة التالية 📖", callback_data=f"dynamic_exam_continue_{exam_id}")]]
            else:
                # This is the last ID and no questions, end
                keyboard = [[InlineKeyboardButton("↩️ رجوع", callback_data="main_menu")]]
                if query:
                    try:
                        await query.answer()
                        await context.bot.send_message(chat_id=query.message.chat_id, text="✅ انتهى الشرح!", reply_markup=InlineKeyboardMarkup(keyboard))
                    except:
                        pass
                return
    
    # Check if there's media attached to this ID and level
    exam = load_exams().get(exam_id, {})
    media_attachments = exam.get('media_attachments', {})
    media_key = f"{current_id}_{current_level}"
    media_info = media_attachments.get(media_key)
    
    # Answer the callback query first
    if query:
        try:
            await query.answer()
        except:
            pass
    
    # Debug logging for keyboard
    logging.info(f"Sending message with keyboard: {keyboard}")

    # Always send new message for explanation text (don't edit previous messages)
    # Delete previous "نكمل" button message if it exists (when user clicks "نكمل")
    if query and query.message:
        try:
            # Delete the "نكمل" button message (not the explanation message)
            await query.delete_message()
            debug_log("send_dynamic_exam_text", "Deleted previous 'نكمل' button message", {"message_id": query.message.message_id}, "F")
        except Exception as e:
            debug_log("send_dynamic_exam_text", "Could not delete previous 'نكمل' message", {"error": str(e)}, "F")
            logging.debug(f"Could not delete previous 'نكمل' message: {e}")

    if media_info:
        media_type = media_info.get('type')
        media_content = media_info.get('content')
        
        if media_type == 'photo':
            # Send photo with caption
            sent_message = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=media_content,
                caption=text_to_send,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        elif media_type == 'video':
            # Send video with caption
            sent_message = await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=media_content,
                caption=text_to_send,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        elif media_type == 'url':
            # Send text with URL link
            text_with_url = f"{text_to_send}\n\n🔗 رابط إضافي: {media_content}"
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text_with_url,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        else:
            # Fallback to text
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text_to_send,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
    else:
        # No media, send text only (without button - button will be in separate message)
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id if query else update.effective_chat.id,
            text=text_to_send,
            reply_markup=None
        )
    
    # Add explanation message to cleanup list (will be deleted when quiz starts)
    add_cleanup_msg(context, sent_message.message_id)
    
    # Send "نكمل" button in a separate message (only if there's a next level)
    if next_level_exists:
        continue_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data=f"dynamic_exam_continue_{exam_id}")]]
        continue_msg = await context.bot.send_message(
            chat_id=query.message.chat_id if query else update.effective_chat.id,
            text="📖",
            reply_markup=InlineKeyboardMarkup(continue_keyboard)
        )
        # Don't add "نكمل" button message to cleanup - it will be deleted when user clicks it

async def handle_dynamic_exam_mcq_start(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str, question_id: int = None):
    """Start MCQ quiz for dynamic exam - clears all explanation messages."""
    debug_log("handle_dynamic_exam_mcq_start", "Function called", {"exam_id": exam_id, "question_id": question_id, "has_callback_query": update.callback_query is not None, "has_effective_user": hasattr(update, 'effective_user') and update.effective_user is not None}, "F")

    query = update.callback_query if update.callback_query else None
    user = query.from_user if query else update.effective_user
    conn = context.bot_data['db_conn']
    exam_state = context.user_data.get('dynamic_exam', {})

    debug_log("handle_dynamic_exam_mcq_start", "User extracted", {"user_id": user.id if user else None}, "F")

    # Reload exam data
    debug_log("handle_dynamic_exam_mcq_start", "About to reload exam data", {"exam_id": exam_id}, "F")
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    debug_log("handle_dynamic_exam_mcq_start", "Exam data reloaded", {"exam_found": exam is not None, "exam_data_found": exam_data is not None}, "F")
    if not exam or not exam_data:
        await query.answer("❌ الاختبار غير موجود.", show_alert=True)
        return

    # Get questions - filter by question_id if provided
    all_questions = exam_data.get('mcq_questions', [])
    debug_log("handle_dynamic_exam_mcq_start", "All questions count", {"all_questions_count": len(all_questions), "question_id": question_id, "sample_question_keys": list(all_questions[0].keys()) if all_questions and all_questions[0] else None, "sample_question_id": all_questions[0].get('id') if all_questions and all_questions[0] else None}, "F")

    # Check if this exam started without explanation
    exam_state = context.user_data.get('dynamic_exam', {})
    no_explanation = exam_state.get('no_explanation', False)
    
    if question_id is not None and not no_explanation:
        # Filter questions for this ID - convert both to string for comparison
        # Only filter if NOT no_explanation mode (in no_explanation mode, use all questions)
        question_id_str = str(question_id)
        questions = [q for q in all_questions if str(q.get('id', '')) == question_id_str]
        debug_log("handle_dynamic_exam_mcq_start", "Filtered questions", {"filtered_count": len(questions), "question_id": question_id, "question_id_str": question_id_str, "sample_filtered": questions[0] if questions else None}, "F")
        debug_log("handle_dynamic_exam_mcq_start", "All question IDs", {"all_ids": [q.get('id') for q in all_questions[:5]]}, "F")
    else:
        # Use all questions (either no question_id specified OR no_explanation mode)
        questions = all_questions
        if no_explanation:
            debug_log("handle_dynamic_exam_mcq_start", "Using all questions (no_explanation mode)", {"questions_count": len(questions)}, "F")
        else:
            debug_log("handle_dynamic_exam_mcq_start", "Using all questions", {"questions_count": len(questions)}, "F")

    if not questions:
        debug_log("handle_dynamic_exam_mcq_start", "No questions found after filtering", {"exam_data_keys": list(exam_data.keys()), "all_questions_count": len(all_questions), "question_id": question_id, "filtered_ids": [q.get('id') for q in all_questions[:5]] if all_questions else []}, "F")
        await query.answer("عذراً، أسئلة هذا الاختبار غير متاحة حالياً.", show_alert=True)
        return

    debug_log("handle_dynamic_exam_mcq_start", "Questions ready", {"final_questions_count": len(questions)}, "F")
    
    # Store questions in bot_data for the quiz system
    difficulty = f"dynamic_exam_{exam_id}"
    if 'questions' not in context.bot_data:
        context.bot_data['questions'] = {}
    context.bot_data['questions'][difficulty] = questions
    debug_log("handle_dynamic_exam_mcq_start", "Questions stored in bot_data", {"difficulty": difficulty, "stored_count": len(questions)}, "F")

    # Ensure user_id is in context.user_data for send_question_view
    context.user_data['user_id'] = user.id
    debug_log("handle_dynamic_exam_mcq_start", "User ID stored", {"user_id": user.id}, "F")

    # Delete the "Start Quiz" button message (only if called from callback)
    if query:
        await query.delete_message()
        debug_log("handle_dynamic_exam_mcq_start", "Deleted callback message", {}, "F")

    # Check if this exam started without explanation
    exam_state = context.user_data.get('dynamic_exam', {})
    no_explanation = exam_state.get('no_explanation', False)
    
    if not no_explanation:
        # Only clear explanation messages and send "no cheating" if there was explanation
        # Clear ALL explanation messages (this is when quiz starts)
        debug_log("handle_dynamic_exam_mcq_start", "About to clear cleanup messages", {}, "F")
        await clear_cleanup_msgs(context, user.id)
        debug_log("handle_dynamic_exam_mcq_start", "Cleanup messages cleared", {}, "F")
        
        # Delete old result message if exists
        if 'result_msg_id' in context.user_data and context.user_data['result_msg_id']:
            try:
                await context.bot.delete_message(chat_id=user.id, message_id=context.user_data['result_msg_id'])
                del context.user_data['result_msg_id']
                debug_log("handle_dynamic_exam_mcq_start", "Deleted old result message", {}, "F")
            except Exception as e:
                logging.warning(f"Could not delete old result message: {e}")
        
        # Send "no cheating" message like Mazen test
        debug_log("handle_dynamic_exam_mcq_start", "About to send 'no cheating' message", {"user_id": user.id}, "F")
        logging.info(f"Sending 'no cheating' message to user {user.id}")
        try:
            no_cheating_msg = await context.bot.send_message(chat_id=user.id, text="حذفتلك كلشي حتى ما تغش 😉\nيلا نبلش الاختبار!")
            context.user_data['no_cheating_msg_id'] = no_cheating_msg.message_id
            debug_log("handle_dynamic_exam_mcq_start", "Successfully sent 'no cheating' message", {"message_id": no_cheating_msg.message_id}, "F")
            logging.info("Successfully sent 'no cheating' message")
        except Exception as e:
            debug_log("handle_dynamic_exam_mcq_start", "Error sending 'no cheating' message", {"error": str(e)}, "F")
            logging.error(f"Error sending 'no cheating' message: {e}")
            return
    else:
        # No explanation, so no messages to delete and no "no cheating" message needed
        debug_log("handle_dynamic_exam_mcq_start", "No explanation mode - skipping cleanup and 'no cheating' message", {}, "F")
        logging.info(f"Starting MCQ for exam {exam_id} without explanation (no cleanup needed)")

    # Check if user has incomplete quiz
    logging.info(f"Checking for incomplete quiz for user {user.id}, difficulty {difficulty}")
    has_incomplete, saved_q_idx, saved_score = has_incomplete_quiz(user.id, difficulty, conn)
    logging.info(f"has_incomplete: {has_incomplete}, saved_q_idx: {saved_q_idx}, saved_score: {saved_score}")
    if has_incomplete:
        keyboard = [
            [InlineKeyboardButton("🔄 استكمال الاختبار السابق", callback_data=f"resume_quiz_{difficulty}")],
            [InlineKeyboardButton("🆕 بدء اختبار جديد", callback_data=f"retry_quiz_{difficulty}")]
        ]
        try:
            incomplete_msg = await context.bot.send_message(
                chat_id=user.id,
                text=f"⚠️ لديك اختبار غير مكتمل:\nالسؤال: {saved_q_idx + 1}\nالنتيجة حتى الآن: {saved_score}\n\nاختر:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            # Store message_id to delete it later after user choice
            context.user_data['incomplete_quiz_msg_id'] = incomplete_msg.message_id
            logging.info("Successfully sent incomplete quiz message")
        except Exception as e:
            logging.error(f"Error sending incomplete quiz message: {e}")
        return
        return
    
    reset_user_progress(user.id, difficulty, conn)
    logging.info("After reset_user_progress")
    # Preserve dynamic_exam state before clearing
    preserved_exam_state = context.user_data.get('dynamic_exam', {})
    context.user_data.clear()
    logging.info("After context.user_data.clear()")
    state = get_user_state(user.id, user.first_name, conn)
    logging.info(f"Got state: {state}")
    context.user_data.update(state)
    context.user_data['difficulty'] = difficulty
    # Restore preserved exam state (including no_explanation flag)
    context.user_data['dynamic_exam'] = preserved_exam_state if preserved_exam_state else exam_state
    context.user_data['quiz_start_time'] = time.time()  # Track time for statistics

    debug_log("handle_dynamic_exam_mcq_start", "About to call send_question_view", {"difficulty": difficulty, "questions_count": len(questions)}, "F")
    logging.info("About to call send_question_view")
    try:
        await send_question_view(update, context, is_new_quiz=True)
        logging.info("send_question_view completed successfully")
        debug_log("handle_dynamic_exam_mcq_start", "send_question_view completed successfully", {}, "F")
    except Exception as e:
        logging.error(f"Error in send_question_view: {e}")
        logging.error(f"Update type: {type(update)}")
        logging.error(f"Update attributes: {dir(update)}")
        if hasattr(update, 'effective_user'):
            logging.error(f"effective_user: {update.effective_user}")
        if hasattr(update, 'callback_query'):
            logging.error(f"callback_query: {update.callback_query}")
        # Fallback: try to send a simple message
        try:
            await context.bot.send_message(chat_id=user.id, text="❌ حدث خطأ في عرض السؤال. يرجى المحاولة مرة أخرى.")
        except Exception as e2:
            logging.error(f"Error sending fallback message: {e2}")

async def handle_dynamic_exam_srd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str, question_id: int = None):
    """Start narrative questions for dynamic exam - clears all explanation messages."""
    # #region agent log
    debug_log("handle_dynamic_exam_srd_start", "Function entry", {"exam_id": exam_id, "question_id": question_id, "has_callback_query": hasattr(update, 'callback_query') and update.callback_query is not None, "has_effective_user": hasattr(update, 'effective_user') and update.effective_user is not None}, "B")
    # #endregion
    debug_log("handle_dynamic_exam_srd_start", "Function called", {"exam_id": exam_id, "question_id": question_id, "has_callback_query": hasattr(update, 'callback_query') and update.callback_query is not None, "has_effective_user": hasattr(update, 'effective_user') and update.effective_user is not None}, "F")

    query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
    user = query.from_user if query else update.effective_user
    exam_state = context.user_data.get('dynamic_exam', {})

    debug_log("handle_dynamic_exam_srd_start", "User extracted", {"user_id": user.id if user else None}, "F")

    # Reload exam data
    debug_log("handle_dynamic_exam_srd_start", "About to reload exam data", {"exam_id": exam_id}, "F")
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    debug_log("handle_dynamic_exam_srd_start", "Exam data reloaded", {"exam_found": exam is not None, "exam_data_found": exam_data is not None, "narrative_questions_count": len(exam_data.get('narrative_questions', [])) if exam_data else 0}, "F")

    if not exam or not exam_data:
        debug_log("handle_dynamic_exam_srd_start", "Exam or exam_data not found", {"exam": exam is not None, "exam_data": exam_data is not None}, "F")
        if query:
            await query.answer("❌ الاختبار غير موجود.", show_alert=True)
        return
    
    # Store exam_data in bot_data for future use
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data
    debug_log("handle_dynamic_exam_srd_start", "Stored exam_data in bot_data", {"narrative_questions_count": len(exam_data.get('narrative_questions', []))}, "F")
    
    # Get questions - filter by question_id if provided
    all_questions = exam_data.get('narrative_questions', [])
    debug_log("handle_dynamic_exam_srd_start", "All narrative questions count", {"all_questions_count": len(all_questions), "question_id": question_id}, "F")

    # Check if this exam started without explanation
    no_explanation = exam_state.get('no_explanation', False)

    if question_id is not None and not no_explanation:
        # Filter questions for this ID - convert both to string for comparison
        # Only filter if NOT no_explanation mode (in no_explanation mode, use all questions)
        question_id_str = str(question_id)
        questions = [q for q in all_questions if str(q.get('id', '')) == question_id_str]
        debug_log("handle_dynamic_exam_srd_start", "Filtered narrative questions", {"filtered_count": len(questions), "question_id": question_id, "question_id_str": question_id_str}, "F")
    else:
        # Use all questions (either no question_id specified OR no_explanation mode)
        questions = all_questions
        if no_explanation:
            debug_log("handle_dynamic_exam_srd_start", "Using all narrative questions (no_explanation mode)", {"questions_count": len(questions)}, "F")
        else:
            debug_log("handle_dynamic_exam_srd_start", "Using all narrative questions", {"questions_count": len(questions)}, "F")
    
    if not questions:
        debug_log("handle_dynamic_exam_srd_start", "No narrative questions found", {"all_questions_count": len(all_questions), "question_id": question_id}, "F")
        if query:
            await query.answer("عذراً، أسئلة هذا الاختبار غير متاحة حالياً.", show_alert=True)
        return

    debug_log("handle_dynamic_exam_srd_start", "Narrative questions ready", {"final_questions_count": len(questions)}, "F")
    
    # Store filtered questions in exam_state
    exam_state['narrative_questions'] = questions
    debug_log("handle_dynamic_exam_srd_start", "Questions stored in exam_state", {"questions_count": len(questions)}, "F")

    # Delete the "Start Quiz" button message (only if called from callback)
    if query:
        try:
            await query.delete_message()
            debug_log("handle_dynamic_exam_srd_start", "Deleted callback message", {}, "F")
        except Exception as e:
            debug_log("handle_dynamic_exam_srd_start", "Could not delete callback message", {"error": str(e)}, "F")
            logging.warning(f"Could not delete callback message in handle_dynamic_exam_srd_start: {e}")

    # Clear ALL explanation messages (this is when quiz starts)
    # But skip if no_explanation mode (no explanation messages to delete)
    no_explanation = exam_state.get('no_explanation', False)
    if not no_explanation:
        debug_log("handle_dynamic_exam_srd_start", "About to clear cleanup messages", {}, "F")
        await clear_cleanup_msgs(context, user.id)
        debug_log("handle_dynamic_exam_srd_start", "Cleanup messages cleared", {}, "F")
    else:
        debug_log("handle_dynamic_exam_srd_start", "No explanation mode - skipping cleanup", {}, "F")
    
    # Delete old result message if exists (from MCQ quiz finish)
    if 'result_msg_id' in context.user_data and context.user_data['result_msg_id']:
        try:
            user_id = user.id if user else (query.from_user.id if query else None)
            if user_id:
                result_msg_id = context.user_data['result_msg_id']
                await context.bot.delete_message(chat_id=user_id, message_id=result_msg_id)
                del context.user_data['result_msg_id']
                debug_log("handle_dynamic_exam_srd_start", "Deleted old result message", {"message_id": result_msg_id}, "F")
                logging.info(f"Deleted result message {result_msg_id} before starting narrative quiz")
            else:
                logging.warning("Could not get user_id to delete result message")
        except Exception as e:
            debug_log("handle_dynamic_exam_srd_start", "Could not delete old result message", {"error": str(e)}, "F")
            logging.warning(f"Could not delete old result message: {e}")
    
    # Don't send "no cheating" message for narrative quiz - it's already sent for MCQ quiz
    debug_log("handle_dynamic_exam_srd_start", "Skipping 'no cheating' message (already sent for MCQ)", {}, "F")
    
    exam_state['srd_q_index'] = 0
    context.user_data['dynamic_exam'] = exam_state
    debug_log("handle_dynamic_exam_srd_start", "About to call send_dynamic_exam_srd_question", {"srd_q_index": 0}, "F")
    # #region agent log
    debug_log("handle_dynamic_exam_srd_start", "Before calling send_dynamic_exam_srd_question", {"exam_id": exam_id, "srd_q_index": 0}, "B")
    # #endregion
    try:
        await send_dynamic_exam_srd_question(update, context, exam_id)
        # #region agent log
        debug_log("handle_dynamic_exam_srd_start", "send_dynamic_exam_srd_question completed successfully", {"exam_id": exam_id}, "B")
        # #endregion
    except Exception as e:
        # #region agent log
        debug_log("handle_dynamic_exam_srd_start", "Exception in send_dynamic_exam_srd_question", {"exam_id": exam_id, "error": str(e), "error_type": type(e).__name__}, "B")
        # #endregion
        logging.error(f"Exception in send_dynamic_exam_srd_question: {e}", exc_info=True)
        raise
    debug_log("handle_dynamic_exam_srd_start", "send_dynamic_exam_srd_question completed", {}, "F")

async def send_dynamic_exam_srd_question(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Send narrative question for dynamic exam."""
    logging.info(f"send_dynamic_exam_srd_question called with exam_id={exam_id}")
    # #region agent log
    debug_log("send_dynamic_exam_srd_question", "Function entry", {"exam_id": exam_id}, "C")
    # #endregion
    debug_log("send_dynamic_exam_srd_question", "Function called", {"exam_id": exam_id}, "F")

    query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
    exam_state = context.user_data.get('dynamic_exam', {})
    current_id = exam_state.get('current_id', 1)
    q_index = exam_state.get('srd_q_index', 0)
    logging.info(f"send_dynamic_exam_srd_question: exam_state keys={list(exam_state.keys())}, current_id={current_id}, q_index={q_index}")

    debug_log("send_dynamic_exam_srd_question", "State extracted", {"current_id": current_id, "q_index": q_index, "exam_state_keys": list(exam_state.keys())}, "F")

    # Use filtered questions if available, otherwise load from exam_data
    srd_questions = exam_state.get('narrative_questions', [])
    debug_log("send_dynamic_exam_srd_question", "Questions from exam_state", {"srd_questions_count": len(srd_questions)}, "F")

    if not srd_questions:
        # Try to get from bot_data first
        exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id)
        if not exam_data:
            # Reload exam data if not in memory
            debug_log("send_dynamic_exam_srd_question", "No exam_data in bot_data, reloading", {"exam_id": exam_id}, "F")
            conn = context.bot_data.get('db_conn')
            exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
            if not exam or not exam_data:
                debug_log("send_dynamic_exam_srd_question", "Exam or exam_data not found", {"exam": exam is not None, "exam_data": exam_data is not None}, "F")
                if query:
                    await query.answer("❌ الاختبار غير موجود.", show_alert=True)
                return
            # Store in bot_data for future use
            if 'dynamic_exams_data' not in context.bot_data:
                context.bot_data['dynamic_exams_data'] = {}
            context.bot_data['dynamic_exams_data'][exam_id] = exam_data
        
        # Filter questions by current_id
        all_questions = exam_data.get('narrative_questions', [])
        current_id_str = str(current_id)
        srd_questions = [q for q in all_questions if str(q.get('id', '')) == current_id_str]
        debug_log("send_dynamic_exam_srd_question", "Filtered questions from exam_data", {"all_questions_count": len(all_questions), "filtered_count": len(srd_questions), "current_id_str": current_id_str}, "F")
        
        if not srd_questions:
            debug_log("send_dynamic_exam_srd_question", "No questions found after filtering", {}, "F")
            if query:
                await query.answer("عذراً، لا توجد أسئلة سردية لهذه الوحدة.", show_alert=True)
            return
        
        # Store filtered questions in exam_state
        exam_state['narrative_questions'] = srd_questions
        context.user_data['dynamic_exam'] = exam_state
        debug_log("send_dynamic_exam_srd_question", "Saved filtered questions to exam_state", {"srd_questions_count": len(srd_questions)}, "F")

    debug_log("send_dynamic_exam_srd_question", "Final questions check", {"q_index": q_index, "total_questions": len(srd_questions), "q_index >= len": q_index >= len(srd_questions)}, "F")

    if q_index >= len(srd_questions):
        debug_log("send_dynamic_exam_srd_question", "All questions finished, calling finish_dynamic_exam", {}, "F")
        await finish_dynamic_exam(update, context, exam_id)
        return

    question_data = srd_questions[q_index]
    debug_log("send_dynamic_exam_srd_question", "Question data extracted", {"question_keys": list(question_data.keys()) if question_data else None}, "F")

    question_text = f"💬 **السؤال السردي {q_index + 1}/{len(srd_questions)}**\n\n{question_data['question']}"

    keyboard = [[InlineKeyboardButton("🤔 اظهر الاجابة", callback_data=f"dynamic_exam_show_srd_{exam_id}")]]
    
    # Get user_id from context or update
    user_id = context.user_data.get('user_id')
    if not user_id:
        if hasattr(update, 'effective_user') and update.effective_user:
            user_id = update.effective_user.id
        elif hasattr(update, 'effective_chat') and update.effective_chat:
            user_id = update.effective_chat.id
        elif query:
            user_id = query.from_user.id
        else:
            debug_log("send_dynamic_exam_srd_question", "No user_id found", {}, "F")
            logging.error("No way to get user_id in send_dynamic_exam_srd_question!")
            return

    debug_log("send_dynamic_exam_srd_question", "About to send question", {"user_id": user_id, "has_query": query is not None}, "F")
    # #region agent log
    debug_log("send_dynamic_exam_srd_question", "Before sending message", {"user_id": user_id, "has_query": query is not None, "question_text_length": len(question_text)}, "C")
    # #endregion

    try:
        if query:
            # #region agent log
            debug_log("send_dynamic_exam_srd_question", "Attempting edit_message_text", {"user_id": user_id}, "C")
            # #endregion
            await query.edit_message_text(text=question_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            # #region agent log
            debug_log("send_dynamic_exam_srd_question", "edit_message_text succeeded", {"user_id": user_id}, "C")
            # #endregion
            debug_log("send_dynamic_exam_srd_question", "Question sent via edit_message_text", {}, "F")
        else:
            # #region agent log
            debug_log("send_dynamic_exam_srd_question", "Attempting send_message", {"user_id": user_id}, "C")
            # #endregion
            # Send new message if no callback query
            await context.bot.send_message(chat_id=user_id, text=question_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            # #region agent log
            debug_log("send_dynamic_exam_srd_question", "send_message succeeded", {"user_id": user_id}, "C")
            # #endregion
            debug_log("send_dynamic_exam_srd_question", "Question sent via send_message", {}, "F")
    except Exception as e:
        # #region agent log
        debug_log("send_dynamic_exam_srd_question", "Exception sending message", {"error": str(e), "error_type": type(e).__name__}, "C")
        # #endregion
        debug_log("send_dynamic_exam_srd_question", "Error sending question", {"error": str(e)}, "F")
        logging.error(f"Error sending narrative question: {e}")
        # Fallback: try to send without markdown
        try:
            await context.bot.send_message(chat_id=user_id, text=question_text.replace("**", ""), reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e2:
            logging.error(f"Error sending fallback message: {e2}")

async def show_dynamic_exam_srd_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Show answer to narrative question."""
    logging.info(f"show_dynamic_exam_srd_answer called with exam_id={exam_id}")
    debug_log("show_dynamic_exam_srd_answer", "Function called", {"exam_id": exam_id}, "F")

    query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
    logging.info(f"show_dynamic_exam_srd_answer: query={query is not None}")
    exam_state = context.user_data.get('dynamic_exam', {})
    current_id = exam_state.get('current_id', 1)
    q_index = exam_state.get('srd_q_index', 0)
    logging.info(f"show_dynamic_exam_srd_answer: exam_state keys={list(exam_state.keys())}, current_id={current_id}, q_index={q_index}")

    debug_log("show_dynamic_exam_srd_answer", "State extracted", {"current_id": current_id, "q_index": q_index, "exam_state_keys": list(exam_state.keys())}, "F")

    # Use filtered questions if available
    srd_questions = exam_state.get('narrative_questions', [])
    debug_log("show_dynamic_exam_srd_answer", "Questions from exam_state", {"srd_questions_count": len(srd_questions)}, "F")

    if not srd_questions:
        # Try to get from bot_data first
        logging.info(f"show_dynamic_exam_srd_answer: No questions in state, checking bot_data for exam_id={exam_id}")
        debug_log("show_dynamic_exam_srd_answer", "No questions in state, checking bot_data", {"exam_id": exam_id}, "F")
        exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id)
        logging.info(f"show_dynamic_exam_srd_answer: exam_data from bot_data={exam_data is not None}")
        if exam_data:
            debug_log("show_dynamic_exam_srd_answer", "Found exam_data in bot_data", {"narrative_questions_count": len(exam_data.get('narrative_questions', [])), "exam_data_keys": list(exam_data.keys()) if exam_data else [], "sample_questions": [{"id": str(q.get('id', '')), "has_question": 'question' in q, "has_answer": 'answer' in q} for q in exam_data.get('narrative_questions', [])[:3]] if exam_data else []}, "F")
        else:
            # Reload exam data if not in memory
            debug_log("show_dynamic_exam_srd_answer", "No exam_data in bot_data, reloading exam data", {"exam_id": exam_id}, "F")
            conn = context.bot_data.get('db_conn')
            exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
            debug_log("show_dynamic_exam_srd_answer", "load_dynamic_exam returned", {"exam": exam is not None, "exam_data": exam_data is not None, "narrative_questions_count": len(exam_data.get('narrative_questions', [])) if exam_data else 0}, "F")
            
            if not exam or not exam_data:
                logging.error(f"show_dynamic_exam_srd_answer: Exam or exam_data not found! exam={exam is not None}, exam_data={exam_data is not None}")
                debug_log("show_dynamic_exam_srd_answer", "Exam or exam_data not found", {"exam": exam is not None, "exam_data": exam_data is not None}, "F")
                if query:
                    await query.answer("❌ الاختبار غير موجود.", show_alert=True)
                return
            
            # Store in bot_data for future use
            if 'dynamic_exams_data' not in context.bot_data:
                context.bot_data['dynamic_exams_data'] = {}
            context.bot_data['dynamic_exams_data'][exam_id] = exam_data
        
        # Filter questions by current_id
        all_questions = exam_data.get('narrative_questions', [])
        current_id_str = str(current_id)
        debug_log("show_dynamic_exam_srd_answer", "Before filtering", {"all_questions_count": len(all_questions), "current_id": current_id, "current_id_str": current_id_str, "exam_id": exam_id, "sample_questions": [{"id": str(q.get('id', '')), "has_question": 'question' in q, "has_answer": 'answer' in q} for q in all_questions[:3]] if all_questions else []}, "F")
        srd_questions = [q for q in all_questions if str(q.get('id', '')) == current_id_str]
        debug_log("show_dynamic_exam_srd_answer", "Filtered questions", {"all_questions_count": len(all_questions), "filtered_count": len(srd_questions), "current_id_str": current_id_str, "sample_ids": [str(q.get('id', '')) for q in all_questions[:5]] if all_questions else []}, "F")
        
        if not srd_questions:
            debug_log("show_dynamic_exam_srd_answer", "No questions found after filtering", {"all_questions_count": len(all_questions), "current_id_str": current_id_str}, "F")
            if query:
                await query.answer("عذراً، لا توجد أسئلة سردية لهذه الوحدة.", show_alert=True)
            return
        
        exam_state['narrative_questions'] = srd_questions
        context.user_data['dynamic_exam'] = exam_state
        debug_log("show_dynamic_exam_srd_answer", "Reloaded and filtered questions", {"srd_questions_count": len(srd_questions), "current_id_str": current_id_str}, "F")

    if q_index >= len(srd_questions):
        debug_log("show_dynamic_exam_srd_answer", "All questions finished, calling finish_dynamic_exam", {}, "F")
        await finish_dynamic_exam(update, context, exam_id)
        return

    question_data = srd_questions[q_index]
    debug_log("show_dynamic_exam_srd_answer", "Question data extracted", {"question_keys": list(question_data.keys()) if question_data else None}, "F")

    full_text = f"💬 **السؤال:** {question_data['question']}\n\n💡 **الإجابة:** {question_data['answer']}"

    context.user_data['dynamic_exam']['srd_q_index'] += 1

    if context.user_data['dynamic_exam']['srd_q_index'] < len(srd_questions):
        keyboard = [[InlineKeyboardButton("السؤال التالي ⬅️", callback_data=f"dynamic_exam_next_srd_{exam_id}")]]
    else:
        keyboard = [[InlineKeyboardButton("إنهاء الاختبار ✅", callback_data=f"dynamic_exam_finish_srd_{exam_id}")]]

    # Get user_id
    user_id = context.user_data.get('user_id')
    if not user_id:
        if hasattr(update, 'effective_user') and update.effective_user:
            user_id = update.effective_user.id
        elif hasattr(update, 'effective_chat') and update.effective_chat:
            user_id = update.effective_chat.id
        elif query:
            user_id = query.from_user.id
        else:
            debug_log("show_dynamic_exam_srd_answer", "No user_id found", {}, "F")
            logging.error("No way to get user_id in show_dynamic_exam_srd_answer!")
            return

    debug_log("show_dynamic_exam_srd_answer", "About to send answer", {"user_id": user_id, "has_query": query is not None}, "F")

    try:
        if query:
            await query.edit_message_text(text=full_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            debug_log("show_dynamic_exam_srd_answer", "Answer sent via edit_message_text", {}, "F")
        else:
            await context.bot.send_message(chat_id=user_id, text=full_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            debug_log("show_dynamic_exam_srd_answer", "Answer sent via send_message", {}, "F")
    except Exception as e:
        debug_log("show_dynamic_exam_srd_answer", "Error sending answer", {"error": str(e)}, "F")
        logging.error(f"Error sending narrative answer: {e}")
        # Fallback: try to send without markdown
        try:
            await context.bot.send_message(chat_id=user_id, text=full_text.replace("**", ""), reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e2:
            logging.error(f"Error sending fallback message: {e2}")

async def finish_dynamic_exam(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Finish dynamic exam - similar to move_to_next_mazen_id."""
    query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
    user_id = query.from_user.id if query else update.effective_user.id if hasattr(update, 'effective_user') else update.effective_chat.id if hasattr(update, 'effective_chat') else None
    
    # Delete ALL instructional messages from the previous unit before moving to next unit
    # This includes: explanation messages, quiz messages, result messages, etc.
    
    # Check if this exam started without explanation
    exam_state = context.user_data.get('dynamic_exam', {})
    no_explanation = exam_state.get('no_explanation', False)
    
    if not no_explanation:
        # Only delete messages if there was explanation
        # 1. Delete all explanation messages (stored in cleanup_msgs)
        await clear_cleanup_msgs(context, user_id)
        debug_log("finish_dynamic_exam", "Cleared all cleanup messages (explanations)", {}, "F")
        
        # 2. Delete "no cheating" message (from MCQ quiz)
        if 'no_cheating_msg_id' in context.user_data and context.user_data['no_cheating_msg_id']:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['no_cheating_msg_id'])
                del context.user_data['no_cheating_msg_id']
                debug_log("finish_dynamic_exam", "Deleted 'no cheating' message", {}, "F")
            except Exception as e:
                logging.warning(f"Could not delete 'no cheating' message: {e}")
    else:
        # No explanation, so no messages to delete
        debug_log("finish_dynamic_exam", "No explanation mode - skipping cleanup", {}, "F")
    
    # 3. Delete result message from narrative quiz if exists
    if 'result_msg_id' in context.user_data and context.user_data['result_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['result_msg_id'])
            del context.user_data['result_msg_id']
            debug_log("finish_dynamic_exam", "Deleted narrative result message", {}, "F")
        except Exception as e:
            logging.warning(f"Could not delete narrative result message: {e}")
    
    # 4. Delete question and status messages from quiz (if they exist)
    if 'question_msg_id' in context.user_data and context.user_data['question_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['question_msg_id'])
            del context.user_data['question_msg_id']
            debug_log("finish_dynamic_exam", "Deleted question message", {}, "F")
        except Exception as e:
            logging.warning(f"Could not delete question message: {e}")
    
    if 'status_msg_id' in context.user_data and context.user_data['status_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['status_msg_id'])
            del context.user_data['status_msg_id']
            debug_log("finish_dynamic_exam", "Deleted status message", {}, "F")
        except Exception as e:
            logging.warning(f"Could not delete status message: {e}")
    
    # 5. Delete any completion messages (like "انتهى شرح الوحدة")
    # These are typically the last messages sent before quiz starts
    # We'll try to delete messages that might be completion messages
    # Note: We can't track all messages, but we'll delete the ones we track
    
    exam = load_exams().get(exam_id, {})
    exam_name = exam.get('button_text', 'الاختبار')
    exam_state = context.user_data.get('dynamic_exam', {})
    current_id = exam_state.get('current_id', 1)

    # Check if this exam started without explanation
    no_explanation = exam_state.get('no_explanation', False)
    
    # In no_explanation mode, always finish the test (no texts to check)
    if no_explanation:
        # All questions are finished, end the test
        final_text = f"🎉🎉🎉\n\n**لقد أكملت {exam_name} بنجاح!**\n\nأحسنت صنعاً، يمكنك الآن العودة للبداية."
        keyboard = [[InlineKeyboardButton("العودة للبداية ↩️", callback_data="main_menu")]]

        if 'dynamic_exam' in context.user_data:
            del context.user_data['dynamic_exam']

        if query:
            await query.edit_message_text(text=final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            chat_id = update.effective_chat.id
            await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # Check if there are more IDs (for future multi-ID support)
    exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id, {})
    all_texts = exam_data.get('texts', {})
    next_id = current_id + 1

    if next_id in all_texts:
        # There is a next ID to move to (like Mazen test)
        context.user_data['dynamic_exam'] = {
            'exam_id': exam_id,
            'current_id': next_id,
            'text_level': 1,
            'state': 'intro_text'
        }
        final_text = f"✅ أحسنت! لقد أكملت الوحدة رقم {current_id}."
        keyboard = [[InlineKeyboardButton("نكمل شرح للوحدة التالية 📖", callback_data=f"dynamic_exam_continue_{exam_id}")]]
        if query:
            await query.edit_message_text(text=final_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            chat_id = update.effective_chat.id
            await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # All IDs are finished, end the test
        final_text = f"🎉🎉🎉\n\n**لقد أكملت {exam_name} بنجاح!**\n\nأحسنت صنعاً، يمكنك الآن العودة للبداية."
        keyboard = [[InlineKeyboardButton("العودة للبداية ↩️", callback_data="main_menu")]]

        if 'dynamic_exam' in context.user_data:
            del context.user_data['dynamic_exam']

        if query:
            await query.edit_message_text(text=final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            chat_id = update.effective_chat.id
            await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ------------------- Quiz Features (Retry, Resume, Leaderboard, Share) -------------------

async def handle_retry_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, difficulty: str):
    """Handle quiz retry - start fresh but keep best score."""
    query = update.callback_query
    user = query.from_user
    conn = context.bot_data['db_conn']
    
    # Get best score info
    best_info = get_best_score(user.id, difficulty, conn)
    best_msg = ""
    if best_info:
        best_msg = f"\n🏆 أفضل نتيجة سابقة: {best_info['best_score']} من {best_info['total_questions']} ({best_info['attempts']} محاولة)\n"
    
    await query.answer()
    # Delete the retry message after user choice
    try:
        await query.delete_message()
    except Exception as e:
        logging.warning(f"Could not delete retry message: {e}")
    
    # Delete incomplete quiz message if exists
    if 'incomplete_quiz_msg_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user.id, message_id=context.user_data['incomplete_quiz_msg_id'])
            del context.user_data['incomplete_quiz_msg_id']
        except Exception as e:
            logging.warning(f"Could not delete incomplete quiz message: {e}")
    
    # Reset progress and start fresh
    reset_user_progress(user.id, difficulty, conn)
    context.user_data.clear()
    state = get_user_state(user.id, user.first_name, conn)
    context.user_data.update(state)
    context.user_data['difficulty'] = difficulty
    context.user_data['quiz_start_time'] = time.time()
    
    # Handle dynamic exam state if needed
    if difficulty.startswith('dynamic_exam_'):
        exam_id = difficulty.replace('dynamic_exam_', '')
        exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id, {})
        context.user_data['dynamic_exam'] = {
            'exam_id': exam_id,
            'current_id': 1,
            'text_level': 1,
            'state': 'intro_text'
        }
    
    await send_question_view(update, context, is_new_quiz=True)

async def handle_resume_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, difficulty: str):
    """Resume incomplete quiz."""
    query = update.callback_query
    user = query.from_user
    conn = context.bot_data['db_conn']
    
    # Get saved state
    state = get_user_state(user.id, user.first_name, conn)
    if state['difficulty'] != difficulty or state['q_index'] == 0:
        await query.answer("❌ لا يوجد اختبار غير مكتمل.", show_alert=True)
        return
    
    await query.answer()
    # Delete the resume message after user choice
    try:
        await query.delete_message()
    except Exception as e:
        logging.warning(f"Could not delete resume message: {e}")
    
    # Delete incomplete quiz message if exists
    if 'incomplete_quiz_msg_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user.id, message_id=context.user_data['incomplete_quiz_msg_id'])
            del context.user_data['incomplete_quiz_msg_id']
        except Exception as e:
            logging.warning(f"Could not delete incomplete quiz message: {e}")
    
    # Restore state
    context.user_data.clear()
    context.user_data.update(state)
    context.user_data['difficulty'] = difficulty
    context.user_data['quiz_start_time'] = time.time()
    
    # Handle dynamic exam state if needed
    if difficulty.startswith('dynamic_exam_'):
        exam_id = difficulty.replace('dynamic_exam_', '')
        context.user_data['dynamic_exam'] = {
            'exam_id': exam_id,
            'current_id': 1,
            'text_level': 1,
            'state': 'mcq_quiz'
        }
    
    await send_question_view(update, context, is_new_quiz=False)

async def handle_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, difficulty: str):
    """Show leaderboard for a difficulty."""
    query = update.callback_query
    user = query.from_user
    conn = context.bot_data['db_conn']
    
    leaderboard = get_leaderboard(difficulty, limit=10, conn=conn)
    
    if not leaderboard:
        await query.answer("لا توجد نتائج بعد.", show_alert=True)
        return
    
    lines = ["🏆 **أفضل 10 مستخدمين:**\n"]
    for idx, (uid, best_score, total, attempts) in enumerate(leaderboard, 1):
        try:
            user_info = await context.bot.get_chat(uid)
            name = user_info.first_name or "مجهول"
            percentage = int((best_score / total * 100)) if total > 0 else 0
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
            lines.append(f"{medal} {name}: {best_score}/{total} ({percentage}%) - {attempts} محاولة")
        except:
            lines.append(f"{idx}. مستخدم {uid}: {best_score}/{total} ({attempts} محاولة)")
    
    # Get user's rank
    user_best = get_best_score(user.id, difficulty, conn)
    user_rank = None
    for idx, (uid, _, _, _) in enumerate(leaderboard, 1):
        if uid == user.id:
            user_rank = idx
            break
    
    if user_best and user_rank:
        user_percentage = int((user_best['best_score'] / user_best['total_questions'] * 100)) if user_best['total_questions'] > 0 else 0
        lines.append(f"\n📍 ترتيبك: #{user_rank}\nنتيجتك: {user_best['best_score']}/{user_best['total_questions']} ({user_percentage}%)")
    
    text = "\n".join(lines)
    keyboard = [[InlineKeyboardButton("↩️ رجوع", callback_data="restart_quiz")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_share_result(update: Update, context: ContextTypes.DEFAULT_TYPE, difficulty: str, score: int, total: int):
    """Share quiz result."""
    query = update.callback_query
    user = query.from_user
    percentage = int((score / total * 100)) if total > 0 else 0
    
    share_text = (
        f"🎉 لقد أكملت الاختبار!\n\n"
        f"📊 النتيجة: {score} من {total} ({percentage}%)\n\n"
        f"جرب الاختبار بنفسك! 🚀"
    )
    
    # Create share button
    share_url = f"https://t.me/share/url?url={TOKEN}&text={share_text}"
    keyboard = [
        [InlineKeyboardButton("📤 مشاركة النتيجة", url=share_url)],
        [InlineKeyboardButton("↩️ رجوع", callback_data="restart_quiz")]
    ]
    
    await query.answer("✅ يمكنك مشاركة النتيجة الآن!")
    await query.edit_message_text(
        f"📤 مشاركة النتيجة\n\n{share_text}\n\nاضغط على الزر أدناه للمشاركة:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ------------------- Admin Exam Preview & Statistics -------------------

async def handle_admin_exam_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of exams for preview."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    if not exams:
        await update.callback_query.edit_message_text("لا توجد اختبارات للمعاينة.", reply_markup=admin_back_markup())
        return
    
    buttons = []
    for exam_id, exam in exams.items():
        buttons.append([InlineKeyboardButton(
            f"👁️ {exam.get('button_text', exam_id)}",
            callback_data=f"admin_exam_preview_{exam_id}"
        )])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")])
    
    await update.callback_query.edit_message_text(
        "👁️ اختر اختبار للمعاينة:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def start_exam_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Start exam preview for admin."""
    query = update.callback_query
    user = query.from_user
    if not is_admin_user(user.id):
        await query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    conn = context.bot_data.get('db_conn')
    exam, exam_data, question_type = await load_dynamic_exam(exam_id, conn, context.bot)
    if not exam:
        await query.edit_message_text("❌ الاختبار غير موجود.", reply_markup=admin_back_markup())
        return
    
    # Initialize preview state (similar to real exam but marked as preview)
    context.user_data['exam_preview'] = {
        'exam_id': exam_id,
        'current_id': 1,
        'text_level': 1,
        'state': 'intro_text',
        'is_preview': True
    }
    
    # Store exam data
    if 'dynamic_exams_data' not in context.bot_data:
        context.bot_data['dynamic_exams_data'] = {}
    context.bot_data['dynamic_exams_data'][exam_id] = exam_data
    
    await query.edit_message_text(
        f"👁️ معاينة الاختبار: {exam.get('button_text', exam_id)}\n\n"
        f"⚠️ هذا معاينة - لن يتم حفظ النتائج.\n\n"
        f"ابدأ المعاينة:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ بدء المعاينة", callback_data=f"dynamic_exam_{exam_id}")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")]
        ])
    )

async def handle_admin_exam_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show exam statistics."""
    user = update.effective_user
    if not is_admin_user(user.id):
        await update.callback_query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    exams = load_exams()
    if not exams:
        await update.callback_query.edit_message_text("لا توجد اختبارات.", reply_markup=admin_back_markup())
        return
    
    buttons = []
    for exam_id, exam in exams.items():
        buttons.append([InlineKeyboardButton(
            f"📊 {exam.get('button_text', exam_id)}",
            callback_data=f"admin_exam_stats_{exam_id}"
        )])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_exams_manage")])
    
    await update.callback_query.edit_message_text(
        "📊 اختر اختبار لعرض الإحصائيات:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_admin_exam_stats_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, exam_id: str):
    """Show detailed statistics for an exam."""
    query = update.callback_query
    user = update.effective_user
    if not is_admin_user(user.id):
        await query.answer("❌ غير مسموح.", show_alert=True)
        return
    
    conn = context.bot_data['db_conn']
    stats = get_exam_statistics(exam_id, conn)
    
    if not stats:
        await query.edit_message_text(
            f"📊 إحصائيات الاختبار: {exam_id}\n\nلا توجد إحصائيات بعد.",
            reply_markup=admin_back_markup()
        )
        return
    
    # Get leaderboard
    difficulty = f"dynamic_exam_{exam_id}"
    leaderboard = get_leaderboard(difficulty, limit=5, conn=conn)
    
    leaderboard_text = ""
    if leaderboard:
        leaderboard_text = "\n\n🏆 أفضل 5:\n"
        for idx, (uid, best_score, total, attempts) in enumerate(leaderboard, 1):
            try:
                user_info = await context.bot.get_chat(uid)
                name = user_info.first_name or "مجهول"
                percentage = int((best_score / total * 100)) if total > 0 else 0
                leaderboard_text += f"{idx}. {name}: {best_score}/{total} ({percentage}%)\n"
            except:
                leaderboard_text += f"{idx}. مستخدم {uid}: {best_score}/{total}\n"
    
    text = (
        f"📊 إحصائيات الاختبار: {exam_id}\n\n"
        f"📈 إجمالي المحاولات: {stats['total_attempts']}\n"
        f"📊 متوسط النتيجة: {stats['avg_score']:.1f}%\n"
        f"🏆 أعلى نتيجة: {stats['max_score']}\n"
        f"📉 أقل نتيجة: {stats['min_score']}\n"
        f"⏱️ متوسط الوقت: {stats['avg_time']:.1f} ثانية"
        f"{leaderboard_text}"
    )
    
    await query.edit_message_text(text, reply_markup=admin_back_markup(), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAINTENANCE_MODE
    query = update.callback_query
    user = query.from_user

    data = query.data

    debug_log("button_handler", "Callback received", {"data": data, "user_id": user.id}, "F")
    conn = context.bot_data['db_conn']
    
    await query.answer()
    
    # Maintenance gate for non-admins
    if MAINTENANCE_MODE and not is_admin_user(user.id):
        await query.answer("🚧 وضع الصيانة مفعل.", show_alert=True)
        return

    # Global gate for test mode: block non-admins
    if TEST_MODE and not is_admin_user(user.id):
        await query.answer("⚠️ البوت في وضع اختبار حالياً.", show_alert=True)
        return

    if data == "admin_simulate_user":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data['admin_simulate_user'] = True
        await send_main_menu(update, context)
        return

    if data == "admin_stats":
        await handle_admin_stats(update, context)
        return
    if data == "admin_results_view":
        await handle_admin_results_view(update, context)
        return
    if data == "admin_results_browse":
        await handle_admin_results_browse(update, context)
        return
    if data == "admin_edit_main":
        await handle_admin_edit_main(update, context)
        return
    if data == "admin_main_add":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "add_main"}
        await context.bot.send_message(chat_id=user.id, text="أرسل: النص|الإجراء (callback) أو الرابط")
        await query.answer("✏️ أرسل التفاصيل الآن.")
        return
    if data == "admin_main_add_submenu":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        buttons = main_menu_buttons(context)
        if not buttons:
            await query.answer("⚠️ لا توجد أزرار لإضافة قائمة فرعية لها.", show_alert=True)
            return
        # Show list of buttons to choose from
        kb = []
        for idx, btn in enumerate(buttons):
            kb.append([InlineKeyboardButton(f"{idx}: {btn.get('text', '')}", callback_data=f"admin_submenu_select_{idx}")])
        kb.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_edit_main")])
        await query.edit_message_text(
            "اختر الزر الذي تريد إضافة قائمة فرعية له:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if data.startswith("admin_submenu_select_"):
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        button_index = int(data.replace("admin_submenu_select_", ""))
        buttons = main_menu_buttons(context)
        if button_index >= len(buttons):
            await query.answer("❌ رقم غير صالح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "add_submenu", "button_index": button_index}
        await query.edit_message_text(
            f"أرسل: رقم|نص الزر الرئيسي\n\n"
            f"مثال: {button_index}|الاختبارات\n\n"
            f"سيتم إنشاء قائمة فرعية للزر رقم {button_index}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_edit_main")]])
        )
        return
    if data.startswith("show_submenu_"):
        # Show submenu when user clicks on a button with submenu
        submenu_id = data.replace("show_submenu_", "")
        menus = context.bot_data.get("menus", default_menus())
        menu = menus.get("main_menu") or default_menus()["main_menu"]
        buttons = menu.get("buttons", [])
        
        # Find button with this submenu_id
        submenu_buttons = None
        submenu_title = ""
        for btn in buttons:
            if btn.get("submenu_id") == submenu_id:
                submenu_buttons = btn.get("submenu", [])
                submenu_title = btn.get("text", "")
                break
        
        if not submenu_buttons:
            await query.answer("❌ القائمة الفرعية غير موجودة.", show_alert=True)
            return
        
        # Build submenu keyboard
        kb = []
        for sub_btn in submenu_buttons:
            text = sub_btn.get("text", "")
            cb = sub_btn.get("callback")
            url = sub_btn.get("url")
            if cb:
                kb.append([InlineKeyboardButton(text, callback_data=cb)])
            elif url:
                kb.append([InlineKeyboardButton(text, url=url)])
        kb.append([InlineKeyboardButton("↩️ رجوع", callback_data="main_menu")])
        
        await query.edit_message_text(
            f"📁 {submenu_title}\n\nاختر من القائمة:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if data == "admin_main_rename":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "rename_main"}
        await context.bot.send_message(chat_id=user.id, text="أرسل: رقم|نص جديد")
        await query.answer("✏️ أرسل التفاصيل الآن.")
        return
    if data == "admin_main_action":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "action_main"}
        await context.bot.send_message(chat_id=user.id, text="أرسل: رقم|الإجراء الجديد أو الرابط")
        await query.answer("✏️ أرسل التفاصيل الآن.")
        return
    if data == "admin_main_delete":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "delete_main"}
        await context.bot.send_message(chat_id=user.id, text="أرسل: رقم الزر للحذف")
        await query.answer("✏️ أرسل التفاصيل الآن.")
        return
    if data == "admin_main_move":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data["admin_edit_pending"] = {"action": "move_main"}
        await context.bot.send_message(chat_id=user.id, text="أرسل: رقمحالي|رقمجديد")
        await query.answer("✏️ أرسل التفاصيل الآن.")
        return
    if data == "admin_main_move_to_submenu":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        buttons = main_menu_buttons(context)
        if len(buttons) < 2:
            await query.answer("⚠️ يجب أن يكون هناك زرين على الأقل.", show_alert=True)
            return
        # Show list of buttons to choose which one to move
        kb = []
        for idx, btn in enumerate(buttons):
            kb.append([InlineKeyboardButton(f"{idx}: {btn.get('text', '')}", callback_data=f"admin_move_to_submenu_select_{idx}")])
        kb.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_edit_main")])
        await query.edit_message_text(
            "اختر الزر الذي تريد نقله إلى داخل زر آخر:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if data.startswith("admin_move_to_submenu_select_"):
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        source_index = int(data.replace("admin_move_to_submenu_select_", ""))
        buttons = main_menu_buttons(context)
        if source_index >= len(buttons):
            await query.answer("❌ رقم غير صالح.", show_alert=True)
            return
        source_btn = buttons[source_index]
        # Show list of target buttons (excluding the source button)
        kb = []
        for idx, btn in enumerate(buttons):
            if idx != source_index:
                kb.append([InlineKeyboardButton(f"{idx}: {btn.get('text', '')}", callback_data=f"admin_move_to_submenu_target_{source_index}_{idx}")])
        kb.append([InlineKeyboardButton("↩️ رجوع", callback_data="admin_edit_main")])
        await query.edit_message_text(
            f"اختر الزر الذي تريد نقل '{source_btn.get('text', '')}' إليه:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if data.startswith("admin_move_to_submenu_target_"):
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        # Format: admin_move_to_submenu_target_{source_index}_{target_index}
        parts = data.replace("admin_move_to_submenu_target_", "").split("_")
        source_index = int(parts[0])
        target_index = int(parts[1])
        buttons = main_menu_buttons(context)
        if source_index >= len(buttons) or target_index >= len(buttons) or source_index == target_index:
            await query.answer("❌ أرقام غير صالحة.", show_alert=True)
            return
        
        # Move source button to target button's submenu
        source_btn = buttons.pop(source_index)
        target_btn = buttons[target_index]
        
        # Initialize submenu if not exists
        if "submenu" not in target_btn:
            target_btn["submenu"] = []
        # Generate submenu_id if not exists
        if "submenu_id" not in target_btn:
            import hashlib
            target_btn["submenu_id"] = f"submenu_{hashlib.md5(str(target_btn).encode()).hexdigest()[:8]}"
        
        # Add source button to target's submenu (preserve its name and callback/url)
        submenu_item = {
            "text": source_btn.get("text", ""),
        }
        if source_btn.get("callback"):
            submenu_item["callback"] = source_btn.get("callback")
        elif source_btn.get("url"):
            submenu_item["url"] = source_btn.get("url")
        
        target_btn["submenu"].append(submenu_item)
        set_main_menu_buttons(context, buttons)
        
        await query.edit_message_text(
            f"✅ تم نقل '{source_btn.get('text', '')}' إلى داخل '{target_btn.get('text', '')}' بنجاح!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="admin_edit_main")]])
        )
        return
    if data == "admin_results_table_user":
        await handle_admin_results_table(update, context, "user")
        return
    if data == "admin_results_table_lab":
        await handle_admin_results_table(update, context, "lab")
        return
    if data == "admin_results_table_mazen":
        await handle_admin_results_table(update, context, "mazen")
        return
    if data == "admin_results_prev":
        context.user_data['admin_results']['page'] = max(0, context.user_data['admin_results'].get('page', 0) - 1)
        await render_admin_results_page(update, context)
        return
    if data == "admin_results_next":
        context.user_data['admin_results']['page'] = context.user_data['admin_results'].get('page', 0) + 1
        await render_admin_results_page(update, context)
        return
    if data in ("admin_ps_5", "admin_ps_10", "admin_ps_20"):
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        size_map = {"admin_ps_5": 5, "admin_ps_10": 10, "admin_ps_20": 20}
        if 'admin_results' not in context.user_data:
            context.user_data['admin_results'] = {}
        context.user_data['admin_results']['page_size'] = size_map[data]
        context.user_data['admin_results']['page'] = 0
        await render_admin_results_page(update, context)
        return
    if data == "admin_results_search":
        await handle_admin_results_search(update, context)
        return
    if data == "admin_reload_data":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        # Reload data from database/files
        conn = context.bot_data.get('db_conn')
        context.bot_data['exams'] = load_exams(conn)
        context.bot_data['menus'] = load_menus(conn)

        # Clear cached dynamic exam data to force reload
        if 'dynamic_exams_data' in context.bot_data:
            context.bot_data['dynamic_exams_data'] = {}

        await query.edit_message_text("✅ تم إعادة تحميل البيانات من الملفات!", reply_markup=build_admin_keyboard())
        return
    if data == "admin_toggle_maint":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        MAINTENANCE_MODE = not MAINTENANCE_MODE
        status = "مفعّل" if MAINTENANCE_MODE else "متوقف"
        await query.edit_message_text(f"🛠️ وضع الصيانة: {status}", reply_markup=build_admin_keyboard())
        return
    if data == "admin_broadcast_prompt":
        await handle_admin_broadcast_prompt(update, context)
        return
    if data == "admin_export_db":
        await handle_admin_export_db(update, context)
        return
    if data == "admin_import_db":
        await handle_admin_import_db(update, context)
        return
    if data == "admin_menu":
        await send_admin_menu(update, context)
        return
    if data == "admin_mode":
        if not is_admin_user(user.id):
            await query.answer("❌ غير مسموح.", show_alert=True)
            return
        context.user_data['admin_simulate_user'] = False
        await send_admin_menu(update, context)
        return
    if data == "admin_exams_manage":
        await handle_admin_exams_manage(update, context)
        return
    if data == "admin_exam_create":
        await handle_admin_exam_create(update, context)
        return
    if data == "admin_exam_toggle_visibility":
        await handle_admin_exam_toggle_visibility(update, context)
        return
    if data.startswith("admin_exam_visibility_select_"):
        exam_id = data.replace("admin_exam_visibility_select_", "")
        await handle_admin_exam_visibility_select(update, context, exam_id)
        return
    if data.startswith("admin_exam_hide_"):
        exam_id = data.replace("admin_exam_hide_", "")
        await handle_admin_exam_hide(update, context, exam_id)
        return
    if data.startswith("admin_exam_show_"):
        exam_id = data.replace("admin_exam_show_", "")
        await handle_admin_exam_show(update, context, exam_id)
        return
    if data.startswith("admin_exam_notify_yes_"):
        exam_id = data.replace("admin_exam_notify_yes_", "")
        await handle_admin_exam_notify_users(update, context, exam_id)
        return
    if data.startswith("admin_exam_notify_no_"):
        exam_id = data.replace("admin_exam_notify_no_", "")
        await handle_admin_exam_show_final(update, context, exam_id, notify=False)
        return
    if data == "admin_exam_media_yes":
        await handle_admin_exam_media_yes(update, context)
        return
    if data == "admin_exam_media_no":
        await handle_admin_exam_media_no(update, context)
        return
    if data == "admin_exam_type_mcq":
        await handle_admin_exam_type_choice(update, context, "mcq")
        return
    if data == "admin_exam_type_narrative":
        await handle_admin_exam_type_choice(update, context, "narrative")
        return
    if data == "admin_exam_type_both":
        await handle_admin_exam_type_choice(update, context, "both")
        return
    if data.startswith("admin_exam_mcq_id_"):
        # Handle MCQ ID selection
        try:
            question_id = int(data.replace("admin_exam_mcq_id_", ""))
            context.user_data['admin_exam_create']['current_question_id'] = question_id
            context.user_data['admin_exam_create']['step'] = "mcq_questions"
            await query.edit_message_text(
                f"✅ تم اختيار ID {question_id} لأسئلة MCQ.\n\n"
                f"أرسل ملف CSV للأسئلة من متعدد:\n"
                f"يجب أن يحتوي على: question, option_a, option_b, option_c, option_d, correct_answer, "
                f"correct_explanation, concept_explanation, explanation_a, explanation_b, explanation_c, explanation_d",
                reply_markup=admin_back_markup()
            )
        except ValueError:
            await query.answer("❌ ID غير صحيح.", show_alert=True)
        return
    if data.startswith("admin_exam_narrative_id_"):
        # Handle Narrative ID selection
        try:
            question_id = int(data.replace("admin_exam_narrative_id_", ""))
            context.user_data['admin_exam_create']['current_question_id'] = question_id
            context.user_data['admin_exam_create']['step'] = "narrative_questions"
            await query.edit_message_text(
                f"✅ تم اختيار ID {question_id} لأسئلة Narrative.\n\n"
                f"أرسل ملف CSV للأسئلة المقالية:\n"
                f"يجب أن يحتوي على: question, answer",
                reply_markup=admin_back_markup()
            )
        except ValueError:
            await query.answer("❌ ID غير صحيح.", show_alert=True)
        return
    if data == "admin_exam_add_narrative_yes":
        # User wants to add narrative questions
        structure = context.user_data['admin_exam_create'].get('explanation_structure', {})
        if structure:
            # Show ID buttons
            keyboard = []
            for id_val in sorted(structure.keys()):
                keyboard.append([InlineKeyboardButton(f"✍️ ID {id_val}", callback_data=f"admin_exam_narrative_id_{id_val}")])
            keyboard.append(admin_back_markup().inline_keyboard[0])
            
            context.user_data['admin_exam_create']['step'] = "select_narrative_id"
            await query.edit_message_text(
                "✅ سأضيف أسئلة مقالية.\n\n"
                "اختر ID لإضافة ملف Narrative له:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['admin_exam_create']['step'] = "narrative_questions"
            context.user_data['admin_exam_create']['current_question_id'] = None
            await query.edit_message_text(
                "✅ سأضيف أسئلة مقالية.\n\n"
                "أرسل ملف CSV للأسئلة المقالية:\n"
                "يجب أن يحتوي على: question, answer",
                reply_markup=admin_back_markup()
            )
        return
    if data == "admin_exam_add_narrative_no":
        # User doesn't want narrative, finalize
        await query.edit_message_text("✅ سيتم إنهاء إنشاء الاختبار بدون أسئلة مقالية...")
        await finalize_exam_creation(update, context)
        return
    if data == "admin_exam_add_no_explanation":
        await handle_admin_exam_add_no_explanation(update, context)
        return
    if data.startswith("admin_exam_no_explanation_select_"):
        exam_id = data.replace("admin_exam_no_explanation_select_", "")
        await handle_admin_exam_select_no_explanation(update, context, exam_id)
        return
    if data.startswith("dynamic_exam_no_explanation_"):
        exam_id = data.replace("dynamic_exam_no_explanation_", "")
        await start_dynamic_exam_no_explanation(update, context, exam_id)
        return
    if data.startswith("dynamic_exam_continue_"):
        exam_id = data.replace("dynamic_exam_continue_", "")
        debug_log("button_handler", "Handling dynamic_exam_continue", {"exam_id": exam_id}, "F")
        logging.info(f"Handling dynamic_exam_continue_ with exam_id: {exam_id}")
        await send_dynamic_exam_text(update, context, exam_id)
        debug_log("button_handler", "send_dynamic_exam_text completed for continue", {}, "F")
        return
    if data.startswith("dynamic_exam_start_mcq_"):
        # Handle both formats: dynamic_exam_start_mcq_{exam_id} and dynamic_exam_start_mcq_{exam_id}_{question_id}
        # Use rsplit to handle exam_id that may contain underscores (e.g., "exam_1766360708")
        remaining = data.replace("dynamic_exam_start_mcq_", "")
        # Try to extract question_id from the end (last part after last underscore)
        parts = remaining.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            # Has question_id: exam_id is everything before last underscore, question_id is last part
            exam_id = parts[0]
            question_id = int(parts[1])
        else:
            # No question_id: entire remaining string is exam_id
            exam_id = remaining
            question_id = None
        await handle_dynamic_exam_mcq_start(update, context, exam_id, question_id)
        return
    if data.startswith("dynamic_exam_start_srd_"):
        # #region agent log
        debug_log("button_handler", "Processing dynamic_exam_start_srd_", {"data": data, "user_id": user.id}, "A")
        # #endregion
        # Handle both formats: dynamic_exam_start_srd_{exam_id} and dynamic_exam_start_srd_{exam_id}_{question_id}
        # Use rsplit to handle exam_id that may contain underscores (e.g., "exam_1766360708")
        remaining = data.replace("dynamic_exam_start_srd_", "")
        # Try to extract question_id from the end (last part after last underscore)
        parts = remaining.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            # Has question_id: exam_id is everything before last underscore, question_id is last part
            exam_id = parts[0]
            question_id = int(parts[1])
        else:
            # No question_id: entire remaining string is exam_id
            exam_id = remaining
            question_id = None
        # #region agent log
        debug_log("button_handler", "Parsed exam_id and question_id", {"exam_id": exam_id, "question_id": question_id, "remaining": remaining, "parts": parts}, "A")
        # #endregion
        await handle_dynamic_exam_srd_start(update, context, exam_id, question_id)
        # #region agent log
        debug_log("button_handler", "handle_dynamic_exam_srd_start completed", {"exam_id": exam_id}, "A")
        # #endregion
        return
    if data.startswith("dynamic_exam_next_srd_"):
        exam_id = data.replace("dynamic_exam_next_srd_", "")
        await send_dynamic_exam_srd_question(update, context, exam_id)
        return
    if data.startswith("dynamic_exam_show_srd_"):
        exam_id = data.replace("dynamic_exam_show_srd_", "")
        logging.info(f"button_handler: Handling dynamic_exam_show_srd, exam_id={exam_id}, data={data}")
        debug_log("button_handler", "Handling dynamic_exam_show_srd", {"exam_id": exam_id, "data": data}, "F")
        await show_dynamic_exam_srd_answer(update, context, exam_id)
        return
    if data.startswith("dynamic_exam_finish_srd_"):
        exam_id = data.replace("dynamic_exam_finish_srd_", "")
        await finish_dynamic_exam(update, context, exam_id)
        return
    if data.startswith("retry_quiz_"):
        difficulty = data.replace("retry_quiz_", "")
        await handle_retry_quiz(update, context, difficulty)
        return
    if data.startswith("leaderboard_"):
        difficulty = data.replace("leaderboard_", "")
        await handle_leaderboard(update, context, difficulty)
        return
    if data.startswith("share_result_"):
        parts = data.replace("share_result_", "").split("_")
        if len(parts) >= 3:
            difficulty = parts[0]
            score = int(parts[1])
            total = int(parts[2])
            await handle_share_result(update, context, difficulty, score, total)
        return
    if data.startswith("resume_quiz_"):
        difficulty = data.replace("resume_quiz_", "")
        await handle_resume_quiz(update, context, difficulty)
        return
    if data == "admin_exam_preview":
        await handle_admin_exam_preview(update, context)
        return
    if data == "admin_exam_stats":
        await handle_admin_exam_stats(update, context)
        return
    if data.startswith("admin_exam_preview_"):
        exam_id = data.replace("admin_exam_preview_", "")
        await start_exam_preview(update, context, exam_id)
        return
    
    if data == "mazin_test":
        await start_mazen_test(update, context)
        return

    if data == "mazin_continue_text":
        await send_mazen_text(update, context)
        return

    if data == "mazin_start_mcq":
        await handle_mazen_mcq_start(update, context)
        return

    if data == "mazin_start_srd":
        context.user_data['mazen_test']['srd_q_index'] = 0
        await send_mazen_srd_question(update, context)
        return
    
    if data == "mazin_show_srd_answer":
        await show_mazen_srd_answer(update, context)
        return

    if data == "mazin_next_srd_q":
        await send_mazen_srd_question(update, context)
        return

    if data == "mazin_finish_srd":
        await move_to_next_mazen_id(update, context)
        return

    if data == "check_sub":
        if await check_subscription(user.id, context):
            await send_main_menu(update, context) # Changed from send_level_choice
        else:
            await query.answer("❌ لم يتم العثور على اشتراكك.", show_alert=True)

    elif data == "main_menu":
        await send_main_menu(update, context)

    elif data == "lab_test_menu":
        await send_lab_test_menu(update, context)

    elif data == "previous_tests":
        await send_previous_tests_menu(update, context)
    
    elif data == "previous_tests_back":
        await send_previous_tests_menu(update, context)

    elif data == "start_theory_test":
        await send_level_choice(update, context)

    elif data == "video_1":
        await query.answer()
        await query.edit_message_text(text="⏳ جارٍ إرسال الفيديو...")
        
        video_message = await context.bot.send_video(
            chat_id=query.message.chat_id,
            video='BAACAgIAAxkBAAIB8Wk_-czpt_JWHcCPF6Zmzi8Sj7hbAAJNRgAChOZISjl6fBKGRD1CNgQ',
            caption="Frame relay 1"
        )
        context.user_data['video_1_video_msg_id'] = video_message.message_id

        ready_keyboard = [[InlineKeyboardButton("✅ جاهز", callback_data="video_1_ready")]]

        ready_keyboard_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟", reply_markup=InlineKeyboardMarkup(ready_keyboard)
        )
        context.user_data['video_1_ready_keyboard_msg_id'] = ready_keyboard_msg.message_id
        await query.delete_message()

    elif data == "video_1_ready":
        await query.answer()
        await query.delete_message()

        first_session_text = """المحور الأول: تشريح الجلسة هذه التجربة تحاكي عمل شبكة واسعة (WAN) باستخدام بروتوكول Frame Relay.

الهدف: ربط فرع الشركة (A) بالفرع (D) عبر شبكة معقدة (السحابة).

المعدات:

DTE (أجهزتك): هي الكمبيوترات أو الراوترات الطرفية (مثل A و D).

DCE (الشبكة): هي المقسمات (Switches) التي تنقل البيانات (مثل E, F, G, H)."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_1_part2")]]

        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"{first_session_text}\n\nتمام لهون ؟ نكمل ؟",
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_1_part1_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

        await context.bot.pin_chat_message(
            chat_id=query.message.chat_id,
            message_id=sent_message.message_id
        )

    elif data == "video_1_part2":

        await query.answer()

        second_session_text = "المحور الثاني: كيف تقرأ شاشة الـ LCD؟ (فك الشيفرة) الشاشة هي \"الصندوق الأسود\" الذي يخبرنا بما يحدث. لنأخذ لقطة للشاشة ونشرح كل خانة:"

        image_path = os.path.join("video1", "image1.png")

        if os.path.exists(image_path):

            with open(image_path, "rb") as image_file:

                sent_photo = await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=image_file,
                    caption=second_session_text
                )
                context.user_data['video_1_part2_msg_id'] = sent_photo.message_id
                add_cleanup_msg(context, sent_photo.message_id)

        else:

            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")

            return

        session_keyboard = [[InlineKeyboardButton("تمام 👍", callback_data="video_1_part3")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="تمام 👍", reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_1_part2_button_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)
        await context.bot.pin_chat_message(
            chat_id=query.message.chat_id,
            message_id=context.user_data['video_1_part2_msg_id']
        )

    elif data == "video_1_part3":

        await query.answer()

        third_session_text = """المحور الثالث: سيناريو الجلسة (القصة الكاملة)

الحالة الطبيعية (الطريق السالك) بدأت الجلسة والبيانات تنتقل بسلاسة عبر "أقصر مسار".
خرجت من A برقم DLCI 20.

دخلت الشبكة (المقسم E) وتحولت لـ DLCI 100.

وصلت للمستقبل D برقم DLCI 24.

النتيجة: الدارة الافتراضية (PVC) تعمل بمسارها القياسي.

لحظة الانقطاع (Route Failure) في منتصف الفيديو، حدث "قطع فيزيائي" في الكابل المباشر.
رد فعل البروتوكول: لم تتوقف الشاشة عن العمل! بل لاحظنا ظهور أرقام جديدة غريبة (105, 103).

التفسير: اكتشف النظام أن الطريق (100) مقطوع، فقام تلقائياً بالبحث في "جدول التوجيه" عن طريق بديل. وجد طريقاً أطول (عبر المقسم H أو G)، فقام بتحويل البيانات إليه.

المصطلح العلمي: هذا يسمى Fault Tolerance (التسامح مع الخطأ) أو Rerouting.

العودة (Recovery) في نهاية الفيديو، عادت الأرقام القديمة للظهور. هذا يعني أن العطل تم إصلاحه، والشبكة "الذكية" فضلت العودة للمسار الأقصر والأسرع فور توفره."""

        session_keyboard = [[InlineKeyboardButton("كمل 👍", callback_data="video_1_part4")]]

        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"{third_session_text}",
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )

        context.user_data['video_1_part3_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

        await context.bot.pin_chat_message(
            chat_id=query.message.chat_id,
            message_id=sent_message.message_id
        )

    elif data == "video_1_part4":

        await query.answer()

        fourth_session_text = "المحور الرابع: دليل حالات تغير الأرقام (Table of Cases) هذا الجدول يلخص لك \"كل حركة\" ظهرت أو يمكن أن تظهر على الشاشة:"

        image_path = os.path.join("video1", "image2.png")

        if os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                sent_photo = await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=image_file,
                    caption=fourth_session_text
                )
                context.user_data['video_1_part4_msg_id'] = sent_photo.message_id
                add_cleanup_msg(context, sent_photo.message_id)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")
            return

        session_keyboard = [[InlineKeyboardButton("جاهز للإختبار 👍", callback_data="video_1_finish")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="جاهز للإختبار ؟", reply_markup=InlineKeyboardMarkup(session_keyboard)
        )

        context.user_data['video_1_part4_button_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

        await context.bot.pin_chat_message(
            chat_id=query.message.chat_id,
            message_id=context.user_data['video_1_part4_msg_id']
        )
    
    elif data == "video_1_finish":
        
        await query.answer()
        
        # Collect all message IDs to delete
        message_ids = [
            context.user_data.get('video_1_video_msg_id'),
            context.user_data.get('video_1_ready_keyboard_msg_id'),
            context.user_data.get('video_1_part1_msg_id'),
            context.user_data.get('video_1_part2_msg_id'),
            context.user_data.get('video_1_part2_button_msg_id'),
            context.user_data.get('video_1_part3_msg_id'),
            context.user_data.get('video_1_part4_msg_id'),
            context.user_data.get('video_1_part4_button_msg_id'),
            query.message.message_id, # This is the message with the "finish" button
        ]
        
        chat_id = query.message.chat_id
        await clear_cleanup_msgs(context, chat_id)
        for msg_id in filter(None, message_ids):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id}: {e}")

        # Unpin messages silently
        try:
            await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        except Exception as e:
            logging.warning(f"Could not unpin all messages: {e}")

        # Send the "no cheating" message
        await context.bot.send_message(chat_id=chat_id, text="حذفتلك كلشي حتى ما تغش 😉\nيلا نبلش الاختبار!")
        
        # Start the video 1 quiz
        difficulty = 'video1'
        if not context.bot_data['questions'].get(difficulty):
            await context.bot.send_message(chat_id=chat_id, text=f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
            return

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty
        
        await send_question_view(update, context, is_new_quiz=True)







    



    elif data == "video_2":
        await query.answer()
        await query.edit_message_text(text="⏳ جارٍ إرسال الفيديو...")
        
        video_message = await context.bot.send_video(
            chat_id=query.message.chat_id,
            video='BAACAgIAAxkBAAE_RLBpQAfzC-po-5ZE8gABo0_3SZx5yjAAAj5yAALGpzlKDZ4xiSB1rTs2BA',
            caption="Frame relay 2"
        )
        context.user_data['video_2_video_msg_id'] = video_message.message_id

        ready_keyboard = [[InlineKeyboardButton("✅ جاهز", callback_data="video_2_ready")]]

        ready_keyboard_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟", reply_markup=InlineKeyboardMarkup(ready_keyboard)
        )
        context.user_data['video_2_ready_keyboard_msg_id'] = ready_keyboard_msg.message_id
        await query.delete_message()

    elif data == "video_3":
        await query.answer()
        await query.edit_message_text(text="⏳ جارٍ إرسال الفيديو...")
        
        video_message = await context.bot.send_video(
            chat_id=query.message.chat_id,
            video='BAACAgIAAxkBAAICmGlAG7u0cGV3j1ix507-seaRRxnUAAI1cgACxqc5St3PiJEhvUrSNgQ',
            caption="HDLC 1"
        )
        context.user_data['video_3_video_msg_id'] = video_message.message_id

        ready_keyboard = [[InlineKeyboardButton("✅ جاهز", callback_data="video_3_ready")]]

        ready_keyboard_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟", reply_markup=InlineKeyboardMarkup(ready_keyboard)
        )
        context.user_data['video_3_ready_keyboard_msg_id'] = ready_keyboard_msg.message_id
        await query.delete_message()


    elif data == "video_2_ready":
        await query.answer()
        await query.delete_message()
        
        part1_text = """أولاً: شرح قراءة الشاشة (اللوحة الرقمية LCD)
هذه أهم مهارة عملية. الشاشة في الفيديو تعرض سطرين، كل سطر يمثل "إطار بيانات" (Frame) يمر في لحظة معينة. التقسيمات (الأعمدة) المكتوبة فوق الشاشة هي مفتاح الحل:"""
        
        msg1 = await context.bot.send_message(chat_id=query.message.chat_id, text=part1_text)
        context.user_data['video_2_part1_msg_id'] = msg1.message_id
        add_cleanup_msg(context, msg1.message_id)
        
        image_path = os.path.join("video2", "image1.png")
        if os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                msg2 = await context.bot.send_photo(chat_id=query.message.chat_id, photo=image_file)
                context.user_data['video_2_image1_msg_id'] = msg2.message_id
                add_cleanup_msg(context, msg2.message_id)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_2_part2")]]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="تمام لهون ؟ نكمل ؟",
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )

    elif data == "video_3_ready":
        await query.answer()
        # Delete video message and ready keyboard message
        chat_id = query.message.chat_id
        message_ids = [
            context.user_data.get('video_3_video_msg_id'),
            context.user_data.get('video_3_ready_keyboard_msg_id'),
            query.message.message_id # The message with the "ready" button
        ]
        
        for msg_id in filter(None, message_ids):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id} in video 3 ready flow: {e}")

        part1_text = """أولاً: التشريح الدقيق لما تراه على الشاشة (مفتاح الفهم)
الجهاز الذي أمامك هو محاكي بروتوكول (Protocol Analyzer). كل سطر يظهر على الشاشة هو "إطار" (Frame). لفهم الجلسة، يجب أن تعرف قراءة الأعمدة:

Address (العنوان): يشير دائماً إلى المستقبل. إذا كان الرقم 10، فالإطار ذاهب للمحطة 10.

Code (نوع الإطار):

SABM: أمر تأسيس الاتصال (Set Asynchronous Balanced Mode).

UA: إطار الموافقة غير المرقم (Unnumbered Acknowledgment).

RR: جاهز للاستقبال (Receive Ready).

INFO: إطار يحمل بيانات فعلية (Information).

DISC: أمر قطع الاتصال (Disconnect).

N(S) (عداد الإرسال): رقم الإطار الذي أرسله "أنا".

N(R) (عداد الاستقبال): أخطر حقل؛ معناه: "لقد استلمت كل شيء بنجاح حتى الرقم السابق، وأنا أنتظر منك الآن الرقم المكتوب هنا".

P/F (بت التحكم):

P (Poll): عندما يكون 1، أنا الآمر وأطلب منك رداً فورياً.

F (Final): عندما يكون 1، هذا ردي النهائي على طلبك.

FCS: فحص الأخطاء، الحرف G يعني Good (الإطار سليم)."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_3_part2")]]

        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part1_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_3_part1_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

    elif data == "video_2_part2":
        await query.answer()
        await query.delete_message()

        part2_text = """سنقسم الشرح إلى: قراءة الشاشة، المفهوم العلمي، سيناريو الازدحام، وبنك الأسئلة.

**أولاً: شرح قراءة الشاشة (اللوحة الرقمية LCD)**
هذه أهم مهارة عملية. الشاشة في الفيديو تعرض سطرين، كل سطر يمثل "إطار بيانات" (`Frame`) يمر في لحظة معينة.
التقسيمات (الأعمدة) المكتوبة فوق الشاشة هي مفتاح الحل:
`INFO,DE,FECN,BECN,DLCI,SOURCE,PATH`
`معلومات,حذف,إشعار أمامي,إشعار خلفي,العنوان,المصدر,المسار`

**كيف تقرأ السطر الذي يظهر على الشاشة؟**
لنأخذ مثالاً من الفيديو: `a DTE 20 0 0 0`

- `PATH` (a/e/d): الحرف الأول (مثل `a`) يرمز للمسار الفيزيائي أو المنفذ الذي دخلت منه البيانات.
- `SOURCE` (DTE/DCE):
  - `DTE`: تعني `Data Terminal Equipment`. أي أن هذه الرسالة قادمة من جهاز المستخدم (الكمبيوتر أو الراوتر الطرفي) ذاهبة إلى الشبكة.
  - `DCE`: تعني `Data Circuit-terminating Equipment`. أي أن الرسالة قادمة من مقسم الشبكة (`Switch`) ذاهبة إلى المستخدم أو مقسم آخر.
- `DLCI` (مثل 20): هو `Data Link Connection Identifier`. اعتبره "رقم الرحلة" أو "العنوان". هو الرقم الذي يميز الدارة الظاهرية (المسار) الذي تسير فيه البيانات.
- `BECN` (0 أو 1): هل يوجد ازدحام في الخلف؟ (1 = نعم، 0 = لا).
- `FECN` (0 أو 1): هل واجهت هذه البيانات ازدحاماً في طريقها إليك؟ (1 = نعم، 0 = لا).
- `DE` (0 أو 1): هل هذه البيانات قابلة للحذف/التأجيل؟ (1 = نعم/غير مهمة، 0 = لا/مهمة جداً)."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_2_part3")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part2_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        add_cleanup_msg(context, sent_message.message_id)
        context.user_data['video_2_part2_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

    elif data == "video_3_part2":
        await query.answer()

        part2_text = """ثانياً: الشرح التفصيلي لسيناريو الجلسة (Step-by-Step)
الجلسة مرت بـ 4 مراحل حاسمة، إليك تحليلها كخوارزمية:

المرحلة 1: المصافحة وتأسيس الاتصال (Handshake)
الحدث: المحطة 20 تريد التحدث مع 10.

الإطار: Address:10 | Code:SABM | P:1

التحليل: المحطة 20 تقول: "يا 10، لنضبط الاتصال بوضع التوازن اللامتزامن، وأنا أنتظر ردك الآن (P=1)".

الرد: Address:20 | Code:UA | F:1

التحليل: المحطة 10 تقول: "وافقت (UA)، وهذا ردي عليك (F=1)".

النتيجة التقنية: تصفير جميع العدادات (V(S)=0, V(R)=0) لدى الطرفين استعداداً لبدء النقل.

المرحلة 2: التأكد من الجاهزية (Keep-Alive)
الحدث: تبادل إطارات RR بين الطرفين.

التحليل: كل طرف يرسل RR ليقول: "أنا مستيقظ، جاهز، عداداتي صفر، ولا يوجد لدي بيانات لأرسلها حالياً". هذه المرحلة تضمن أن الخط الفيزيائي يعمل قبل ضخ البيانات."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_3_part3")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part2_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
    elif data == "video_3_part3":
        await query.answer()
        await query.delete_message()

        part3_text = """المرحلة 3: تدفق البيانات (The Sliding Window)
هنا يكمن "لب" البروتوكول. لاحظ أن المحطة 10 أرسلت عدة إطارات دون انتظار رد فوري لكل واحد (هذا هو مبدأ النافذة المنزلقة).

الإرسال المتتابع:

أرسلت 10 الإطار INFO رقم 0.

ثم الإطار INFO رقم 1.

ثم INFO رقم 2.

ثم INFO رقم 3.

لاحظ: عداد N(S) يزداد (0, 1, 2, 3)، بينما N(R) ثابت على 0 (لأن المحطة 20 لم ترسل شيئاً لـ 10 لتؤكده).

الإقرار الجماعي (Piggybacking/Ack):

المحطة 20 ردت بإطار RR وفيه N(R)=4.

ماذا يعني الرقم 4 هنا؟ "يا محطة 10، لقد استلمت الإطارات 0، 1، 2، و3 بنجاح تام، وأنا الآن أنتظر منك الإطار رقم 4".

هذا يسمى "الإقرار التراكمي".

استكمال الإرسال:

المحطة 10 فهمت الرسالة، وبدأت الإرسال من الرقم 4، 5، 6، وصولاً لـ 7.

عندما امتلأت النافذة أو انتهت البيانات، توقف الإرسال وتم تبادل التأكيدات النهائية.

المرحلة 4: الهدم (Teardown)
الحدث: المحطة 10 انتهت. ترسل DISC مع P=1.

الرد: المحطة 20 ترد UA مع F=1.

النتيجة: قطع الاتصال المنطقي، وأي إطارات تأتي بعد ذلك سيتم تجاهلها إلا بطلب تأسيس جديد."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_3_part4")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part3_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_3_part3_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)
    elif data == "video_3_part4":
        await query.answer()

        part4_text = """ثالثاً: شرح الحالات الافتراضية (ماذا لو؟) - لرفع مستوى فهمك بصفتك طالباً متميزاً، يجب أن تعرف ماذا يحدث لو تغير السيناريو:

حالة 1: ماذا لو وصل الإطار وفيه FCS Error؟

المحطة المستقبلة ستتجاهل الإطار (تعتبره لم يصل).

عندما تنتهي مهلة الانتظار (Time-out) لدى المرسل، أو يأتيه رد REJ (Reject) يطلب إعادة الإرسال، سيقوم المرسل بإعادة إرسال الإطارات بدءاً من الإطار التالف.

حالة 2: ماذا لو أرسلت المحطة 20 إطار RR وفيه N(R)=2 بينما المحطة 10 أرسلت حتى 3؟

هذا يعني أن الإطارين 0 و 1 وصلا بسلام، لكن الإطار 2 وما بعده لم يتم تأكيدهم. المحطة 10 ستفهم أن عليها إعادة إرسال من عند 2.

حالة 3: لماذا استخدمنا SABM وليس SNRM؟

لأننا في الوضع "المتوازن" (Balanced). لو كنا في وضع SNRM (Normal Response Mode)، لكانت هناك محطة "سيد" (Master) وأخرى "عبد" (Slave)، ولا يمكن للعبد الإرسال إلا بإذن السيد. في تجربتنا، المحطتان متساويتان (Combined)."""

        session_keyboard = [[InlineKeyboardButton("جاهز للإختبار 👍", callback_data="video_3_finish")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part4_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_3_part4_msg_id'] = sent_message.message_id

    elif data == "video_2_part3":
        await query.answer()
        await query.delete_message()

        part3_text = """ثانياً: شرح السيناريو الكامل (الازدحام والمعالجة)
هنا "القصة" التي يرويها الفيديو، وسأجيب عن طلبك بخصوص "انقطاع الطريق" و "تغيير المسار" ضمن سياق الـ Frame Relay:

1. حدوث الازدحام (Congestion)
الشبكة (الغيمة التي فيها عقد E, F, H) تستقبل بيانات أكثر مما تستطيع معالجته. المخازن المؤقتة (Buffers) في المقاسم امتلأت.

المشكلة: البيانات ستتأخر أو تضيع.

الحل: يجب إخبار الأطراف بهذه المشكلة.

2. الإشعارات (آلية التنبيه)
المقسم (Switch) يقوم بتغيير البتات في الإطارات المارة:

يرسل BECN=1 للمرسل: "يا مرسل، الطريق أمامي مزدحم، خفف الإرسال".

يرسل FECN=1 للمستقبل: "يا مستقبل، هذه البيانات وصلت متأخرة بسبب الزحمة"."""

        session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_2_part4")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part3_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_2_part3_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

    elif data == "video_2_part4":
        await query.answer()
        await query.delete_message()

        part4_text = """3. آلية المعالجة وتغيير المسار (Buffering)
أنت سألت عن "تغيير المسارات". في هذا الفيديو، التغيير ليس "جغرافياً" (أي تغيير الطريق من مدينة أ إلى ب)، بل هو "تغيير زمني" أو تحويل لمسار تخزين:
البيانات المهمة ($DE=0$) تستمر في طريقها (المسار السريع).
البيانات غير المهمة ($DE=1$) يقوم المقسم بتحويل مسارها إلى Buffer (ذاكرة انتظار) جانبية.
انقطاع الطريق (مجازياً هنا): بالنسبة للبيانات ذات $DE=1$، الطريق "انقطع" مؤقتاً، وتم ركنها على الجانب.

4. العودة للوضع الطبيعي
عندما ينتهي الازدحام، المقسم يفتح بوابة الـ Buffer، ويعيد إرسال البيانات المؤجلة إلى المستقبل، لكنها ستصل متأخرة عن وقتها الأصلي."""

        session_keyboard = [[InlineKeyboardButton("ابدأ اختبار بسيط وسريع ✅", callback_data="video_2_mini_quiz_start")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=part4_text,
            reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_2_part4_msg_id'] = sent_message.message_id
        add_cleanup_msg(context, sent_message.message_id)

    elif data == "video_2_mini_quiz_start":
        await query.answer()

        # Collect all message IDs to delete
        chat_id = query.message.chat_id
        message_ids = [
            context.user_data.get('video_2_video_msg_id'),
            context.user_data.get('video_2_ready_keyboard_msg_id'),
            context.user_data.get('video_2_part1_msg_id'),
            context.user_data.get('video_2_image1_msg_id'),
            context.user_data.get('video_2_part2_msg_id'),
            context.user_data.get('video_2_part3_msg_id'),
            context.user_data.get('video_2_part4_msg_id'),
            query.message.message_id, # This is the message with the "start quiz" button
        ]
        
        await clear_cleanup_msgs(context, chat_id)
        for msg_id in filter(None, message_ids):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id} in video 2 flow: {e}")

        # Send the "no cheating" message
        await context.bot.send_message(chat_id=chat_id, text="حذفتلك كلشي حتى ما تغش 😉\nيلا نبلش الاختبار!")
        
        caption = "تخيل أنك تنظر إلى جهاز الفحص (Analyzer) وظهر لك السطران التاليان في نفس اللحظة:"
        image_path = os.path.join("video2", "image2.png")
        if os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=image_file, caption=caption)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")

        difficulty = 'video2_mini'
        if not context.bot_data['questions'].get(difficulty):
            await context.bot.send_message(chat_id=chat_id, text=f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
            return

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty
        
        await send_question_view(update, context, is_new_quiz=True)


    elif data == "video_3_finish":
        await query.answer()

        # Collect all message IDs to delete
        message_ids = [
            context.user_data.get('video_3_video_msg_id'),
            context.user_data.get('video_3_ready_keyboard_msg_id'),
            context.user_data.get('video_3_part1_msg_id'),
            context.user_data.get('video_3_part2_msg_id'),
            context.user_data.get('video_3_part3_msg_id'),
            context.user_data.get('video_3_part4_msg_id'),
            query.message.message_id, # This is the message with the "finish" button
        ]
        
        chat_id = query.message.chat_id
        await clear_cleanup_msgs(context, chat_id)
        for msg_id in filter(None, message_ids):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id}: {e}")

        # Unpin messages silently
        try:
            await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        except Exception as e:
            logging.warning(f"Could not unpin all messages: {e}")

        # Send the "no cheating" message
        await context.bot.send_message(chat_id=chat_id, text="حذفتلك كلشي حتى ما تغش 😉\nيلا نبلش الاختبار!")
        
        # Start the video 3 quiz
        difficulty = 'video3'
        if not context.bot_data['questions'].get(difficulty):
            await context.bot.send_message(chat_id=chat_id, text=f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
            return

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty
        
        await send_question_view(update, context, is_new_quiz=True)

    elif data == "start_video_2_main_quiz":
        await query.answer()
        await query.delete_message()
        
        chat_id = query.message.chat_id
        
        await context.bot.send_message(chat_id=chat_id, text="🚀 لنبدأ الاختبار الرئيسي للفيديو الثاني!")
        
        # Start the video 2 main quiz
        difficulty = 'video2'
        if not context.bot_data['questions'].get(difficulty):
            await context.bot.send_message(chat_id=chat_id, text=f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
            return

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty
        
        await send_question_view(update, context, is_new_quiz=True)
                        
    elif data == "video_4":
                await query.answer()
                await query.edit_message_text(text="⏳ جارٍ إرسال الفيديو...")
                context.user_data['video_4_message_history'] = []
                video_message = await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video='BAACAgIAAxkBAAICqmlAHfwzPMC06R1MjQ9eLOOCAAEtvgACHkYAAoTmSEqg525x2-1VszYE',
                    caption="HDLC 2"
                )
                context.user_data['video_4_video_msg_id'] = video_message.message_id
                context.user_data['video_4_message_history'].append(video_message.message_id)
                        
                ready_keyboard = [[InlineKeyboardButton("✅ جاهز", callback_data="video_4_ready")]]
                ready_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟",
                    reply_markup=InlineKeyboardMarkup(ready_keyboard)
                )
                context.user_data['video_4_ready_keyboard_msg_id'] = ready_msg.message_id
                context.user_data['video_4_message_history'].append(ready_msg.message_id)

    # --- legacy flows disabled ---
    elif data in ("video_4_ready_legacy", "video_4_part1_legacy", "video_4_part2_legacy", 
                  "video_4_part3_legacy", "video_4_part1_legacy2", "video_4_part2_legacy2", 
                  "video_4_part3_legacy2", "video_4_ready_legacy2"):
        await query.answer("⚠️ هذا التدفق غير مدعوم حالياً.", show_alert=True)

    elif data == "video_4_finish":
        video_message = await context.bot.send_video(
            chat_id=query.message.chat_id,
            video='BAACAgIAAxkBAAICqmlAHfwzPMC06R1MjQ9eLOOCAAEtvgACHkYAAoTmSEqg525x2-1VszYE',
            caption="HDLC 2"
        )
        context.user_data['video_4_video_msg_id'] = video_message.message_id
        context.user_data['video_4_message_history'].append(video_message.message_id)
        
        ready_keyboard = [[InlineKeyboardButton("✅ جاهز", callback_data="video_4_ready")]]
        ready_keyboard_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟", reply_markup=InlineKeyboardMarkup(ready_keyboard)
                )
        context.user_data['video_4_ready_keyboard_msg_id'] = ready_keyboard_msg.message_id
        context.user_data['video_4_message_history'].append(ready_keyboard_msg.message_id)
                        
                # Removed query.delete_message()
        
    elif data == "video_4_ready_legacy2":
                await query.answer()
                # Removed message deletion logic
            
                part1_text = """هذه التجربة تندرج تحت عنوان "التحكم بالأخطاء في طبقة ربط البيانات" (Data Link Layer Error Control)، وتحديداً بروتوكولات ARQ (Automatic Repeat Request).
            
        أولاً: شرح "لوحة القيادة" (ماذا تعني الرموز على الشاشة؟)
            
        قبل أن ندخل في السيناريوهات، يجب أن تفهم لغة الآلة التي ظهرت في الفيديو. الجهاز الظاهر يحاكي بروتوكول HDLC، وهذه رموز الشاشة:
            
        *INFO*: تعني أن هذا الإطار هو "إطار معلومات" (I-Frame) يحمل بيانات حقيقية.
            
        *REJ (Reject)*: إطار تحكم يعني "رفض"، ويطلب إعادة الإرسال (سنشرحه بالتفصيل).
            
        *SREJ (Selective Reject)*: إطار تحكم يعني "رفض انتقائي".
            
        *$N(S)$*: رقم تسلسل الإطار المُرسل (العداد الخاص بالمرسل).
            
        *$N(R)$*: رقم الإطار الذي يتوقع المستقبل استلامه تالياً (العداد الخاص بالمستقبل).
            
        *FCS (Frame Check Sequence)*: خانة تدقيق الخطأ.
            
        *B (Bad)*: الإطار وصل تالفاً (نتيجة حساب CRC لم تتطابق).
            
        *G (Good)*: الإطار وصل سليماً."""
            
                session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_4_part2")]]
            
                sent_message = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=part1_text,
                    reply_markup=InlineKeyboardMarkup(session_keyboard),
                    parse_mode="Markdown"
        )
            
                context.user_data['video_4_part1_msg_id'] = sent_message.message_id
                context.user_data['video_4_message_history'].append(sent_message.message_id)
            
    elif data == "video_4_part2_legacy2":
                await query.answer()
                # Removed message deletion logic
            
                part2_text = """ثانياً: التفصيل الكامل لمجريات التجربة (خطوة بخطوة)
            
        الهدف من التجربة هو محاكاة حدوث "تشويش" أو ضياع لبيانات أثناء النقل، وكيف يتصرف النظام.
            
        *الحالة 1: اكتشاف الخطأ (The Error Detection)*
            
        ماذا حدث؟ المستقبل استلم إطاراً، لكن نظام الفحص (FCS) أعطى Bad.
            
        التحليل: البيانات وصلت مشوهة (ربما تغيرت بت من 0 إلى 1 بسبب ضجيج في القناة). في هذه اللحظة، طبقة ربط البيانات ترفض استلام الحزمة ولا تمررها للطبقات الأعلى، وتنتظر الحل.
            
        *الحالة 2: الحل القديم/المكلف (REJ - Go-Back-N)*
            
        السيناريو: حدث خطأ في الإطار رقم 1. المستقبل أرسل إطار تحكم نوعه REJ وقيمة *$N(R)=1$*.
            
        التفسير: المستقبل يقول للمرسل: "لقد استلمت الإطار 0 بنجاح، لكن الإطار 1 وصل تالفاً. أنا أرفض 1 وأرفض أي شيء أرسلته لي بعد 1. عُد إلى الخلف وأعد إرسال كل شيء بدءاً من 1".
            
        النتيجة: إذا كان المرسل قد أرسل الإطارات (1، 2، 3، 4)، سيضطر لإعادة إرسال (1، 2، 3، 4) مجدداً.
            
        العيوب: هدر هائل للباندويث (Bandwidth)، خاصة إذا كانت الشبكة بطيئة أو الإطارات كبيرة.
            
        *الحالة 3: الحل الحديث/الذكي (SREJ - Selective Repeat)*
            
        السيناريو: حدث خطأ في الإطار رقم 1. المستقبل أرسل إطار تحكم نوعه SREJ وقيمة *$N(R)=1$*.
            
        التفسير: المستقبل يقول للمرسل: "الإطار رقم 1 وصل تالفاً. من فضلك أعد إرسال رقم 1 فقط. بالمناسبة، إذا كنت قد أرسلت 2 و 3 ووصلوا سليمين، سأحتفظ بهم عندي في الذاكرة المؤقتة (Buffer) ولن أطلبهم مرة أخرى".
            
        النتيجة: المرسل يعيد إرسال الإطار 1 فقط، ثم يكمل إرسال إطارات جديدة (مثل 4، 5...).
            
        المميزات: كفاءة عالية جداً وتوفير للوقت."""
            
                session_keyboard = [[InlineKeyboardButton("نكمل ؟ ✅", callback_data="video_4_part3")]]
            
                sent_message = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=part2_text,
                    reply_markup=InlineKeyboardMarkup(session_keyboard),
                    parse_mode="Markdown"
                )
            
                context.user_data['video_4_part2_msg_id'] = sent_message.message_id
                context.user_data['video_4_message_history'].append(sent_message.message_id)
            
            
            
    elif data == "video_4_part3_legacy2":
                await query.answer()
                # Removed message deletion logic
            
                part3_text = """ثالثاً: شرح الحالات الافتراضية (ماذا لو؟) - لتعميق الفهم بصفتي أستاذك، سأطرح عليك حالات لم تظهر في الفيديو لكنها في صلب الموضوع وقد تأتي في الامتحان:
            
        *ماذا لو فُقد إطار الـ REJ أو SREJ نفسه؟*
            
        الشرح: المرسل لديه "مؤقت" (Timer). إذا أرسل إطاراً ولم يصله أي رد (لا إيجابي ولا سلبي) ونفد الوقت، سيقوم بإعادة إرسال الإطار تلقائياً (Timeout).
            
        *ماذا لو كانت نافذة الاستقبال (Window Size) ممتلئة؟*
            
        الشرح: في حالة SREJ، المستقبل يحتاج لذاكرة ليحفظ الإطارات اللاحقة (2، 3) بينما ينتظر وصول الإطار المصحح (1). إذا كانت الذاكرة صغيرة، قد يضطر للعودة لنظام Go-Back-N.
            
        *متى نستخدم REJ بدلاً من SREJ؟*
            
        الشرح: رغم أن SREJ أفضل، لكنه أعقد برمجياً ويتطلب ذاكرة أكبر في المستقبل. نستخدم REJ في الأجهزة البسيطة جداً أو الشبكات التي نادراً ما تحدث فيها أخطاء."""
            
                session_keyboard = [[InlineKeyboardButton("جاهز للإختبار 👍", callback_data="video_4_finish")]]
            
                sent_message = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=part3_text,
                    reply_markup=InlineKeyboardMarkup(session_keyboard),
                    parse_mode="Markdown"
                )
            
                context.user_data['video_4_part3_msg_id'] = sent_message.message_id
                context.user_data['video_4_message_history'].append(sent_message.message_id)
            
    elif data == "video_4_finish":
        await query.answer()

        # Collect all message IDs to delete from the history list and the current button message
        message_ids_to_delete = context.user_data.get('video_4_message_history', [])
        message_ids_to_delete.append(query.message.message_id) # Add the message with the "finish" button

        chat_id = query.message.chat_id
        await clear_cleanup_msgs(context, chat_id)
        for msg_id in filter(None, message_ids_to_delete):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id}: {e}")

        # Send the "no cheating" message
        await context.bot.send_message(chat_id=chat_id, text="حذفتلك كلشي حتى ما تغش 😉\nيلا نبلش الاختبار!")

        # Start the video 4 quiz
        difficulty = 'video4'
        if not context.bot_data['questions'].get(difficulty):
            await context.bot.send_message(chat_id=chat_id, text=f"عذراً، أسئلة هذا الاختبار غير متاحة حالياً.")
            return

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty

        await send_question_view(update, context, is_new_quiz=True)

    elif data.startswith("level_"):
        if not await check_subscription(user.id, context):
            await send_subscription_prompt(update, context)
            return

        difficulty = data.split("_")[1]

        if not context.bot_data['questions'].get(difficulty):
            await query.edit_message_text(f"عذراً، الأسئلة لمستوى '{difficulty}' غير متاحة حالياً.")
            return

        await query.delete_message()

        reset_user_progress(user.id, difficulty, conn)
        context.user_data.clear()
        state = get_user_state(user.id, user.first_name, conn)
        context.user_data.update(state)
        context.user_data['difficulty'] = difficulty

        await send_question_view(update, context, is_new_quiz=True)

    elif data.startswith("ans_"):
        difficulty = context.user_data.get('difficulty')

        if difficulty is None:
            await query.answer("⚠️ انتهت صلاحية الجلسة.", show_alert=True)
            await query.edit_message_text("⚠️ **حدث تحديث للسيرفر وتم إعادة ضبط البيانات.**\n\nيرجى الضغط على /start للبدء من جديد.", parse_mode="Markdown")
            return

        questions_for_level = context.bot_data['questions'][difficulty]
        
        _, q_idx, ans_idx = data.split("_")
        q_idx = int(q_idx)
        ans_idx = int(ans_idx)
        
        if q_idx != context.user_data.get('q_index', 0):
            return

        current_q = questions_for_level[q_idx]
        if str(q_idx) in context.user_data.get('answers', {}):
            return
            
        await context.bot.edit_message_reply_markup(chat_id=user.id, message_id=context.user_data['question_msg_id'], reply_markup=None)

        correct_ans = current_q['correct']
        correct_phrases = context.bot_data.get('correct_phrases', [""])
        wrong_phrases = context.bot_data.get('wrong_phrases', [""])

        if ans_idx == correct_ans:
            context.user_data['score'] += 1
            phrase = random.choice(correct_phrases)
            result_text = f"✅ **إجابة صحيحة!**\n\n{phrase}"
            context.user_data['answers'][str(q_idx)] = True
        else:
            phrase = random.choice(wrong_phrases)
            result_text = f"❌ **إجابة خاطئة!**\n\n{phrase}\n\nالصحيح هو: *{current_q['options'][correct_ans]}*"
            context.user_data['answers'][str(q_idx)] = False

        context.user_data['q_index'] += 1
        
        save_user_state(user.id, user.first_name, difficulty, context.user_data['q_index'], context.user_data['score'], context.user_data['answers'], conn, context.user_data['question_msg_id'], context.user_data['status_msg_id'])

        explanation = ""
        if ans_idx == correct_ans:
            # User was correct
            escaped_explanation = escape_v1_markdown(current_q.get('expl', ''))
            explanation = f"\n\n💡 **التفسير:** {escaped_explanation}"
        else:
            # User was wrong, get the specific explanation for their choice
            if 'option_explanations' in current_q and len(current_q['option_explanations']) > ans_idx:
                specific_wrong_expl = current_q['option_explanations'][ans_idx]
                escaped_wrong = escape_v1_markdown(specific_wrong_expl)
                explanation = f"\n\n🔍 **لماذا إجابتك خاطئة:** {escaped_wrong}"
            else:
                # Fallback to general explanation if specific one isn't available
                escaped_explanation = escape_v1_markdown(current_q.get('expl', ''))
                explanation = f"\n\n💡 **التفسير:** {escaped_explanation}"

        # Add the 'concept explanation' regardless of answer
        if current_q.get('idea_expl'):
            escaped_idea = escape_v1_markdown(current_q['idea_expl'])
            explanation += f"\n\n🧠 **فكرة السؤال:** {escaped_idea}"

        next_btn = InlineKeyboardButton("السؤال التالي ⬅️", callback_data="next_q")
        full_text = f"{result_text}{explanation}"

        await context.bot.edit_message_text(chat_id=user.id, message_id=context.user_data['status_msg_id'], text=full_text, reply_markup=InlineKeyboardMarkup([[next_btn]]), parse_mode="Markdown")

    elif data == "next_q":
        await send_question_view(update, context)

    elif data == "restart_quiz":
        await start(update, context)
    
    elif data.startswith("retry_quiz_"):
        difficulty = data.replace("retry_quiz_", "")
        await handle_retry_quiz(update, context, difficulty)
    
    elif data.startswith("resume_quiz_"):
        difficulty = data.replace("resume_quiz_", "")
        await handle_resume_quiz(update, context, difficulty)
    
    elif data.startswith("leaderboard_"):
        difficulty = data.replace("leaderboard_", "")
        await handle_leaderboard(update, context, difficulty)
        return
    elif data.startswith("share_result_"):
        parts = data.replace("share_result_", "").split("_")
        if len(parts) >= 3:
            difficulty = parts[0]
            score = int(parts[1])
            total = int(parts[2])
            await handle_share_result(update, context, difficulty, score, total)
        return
    
    # General dynamic_exam handler (must be last to avoid catching specific handlers)
    if data.startswith("dynamic_exam_"):
        exam_id = data.replace("dynamic_exam_", "")
        await start_dynamic_exam(update, context, exam_id)
        return

async def send_question_view(update: Update, context: ContextTypes.DEFAULT_TYPE, is_new_quiz: bool = False):
    debug_log("send_question_view", "Function called", {"is_new_quiz": is_new_quiz, "update_type": type(update).__name__, "has_callback_query": hasattr(update, 'callback_query') and update.callback_query is not None, "has_effective_user": hasattr(update, 'effective_user') and update.effective_user is not None}, "F")

    logging.info(f"send_question_view called with is_new_quiz={is_new_quiz}")

    debug_log("send_question_view", "Starting function execution", {}, "F")

    # Get user_id from context.user_data first (dynamic exam case)
    user_id = context.user_data.get('user_id')
    debug_log("send_question_view", "Checking user_id sources", {"user_id_from_context": user_id, "context_keys": list(context.user_data.keys())}, "F")

    if not user_id:
        # Fallback to update object
        if hasattr(update, 'effective_user') and update.effective_user:
            debug_log("send_question_view", "Checking effective_user", {"effective_user": update.effective_user}, "F")
            user_id = update.effective_user.id
            debug_log("send_question_view", "Got user_id from effective_user", {"user_id": user_id}, "F")
            logging.info(f"user_id from effective_user: {user_id}")
        elif hasattr(update, 'callback_query') and update.callback_query:
            user_id = update.callback_query.from_user.id
            debug_log("send_question_view", "Got user_id from callback_query", {"user_id": user_id}, "F")
            logging.info(f"user_id from callback_query: {user_id}")
        else:
            debug_log("send_question_view", "No user_id found", {}, "F")
            logging.error("No way to get user_id!")
            return

    difficulty = context.user_data.get('difficulty')
    logging.info(f"difficulty: {difficulty}")
    if not difficulty:
        logging.error("No difficulty in context.user_data!")
        return

    questions = context.bot_data.get('questions', {}).get(difficulty)
    if not questions:
        logging.error(f"No questions found for difficulty {difficulty}")
        logging.info(f"Available difficulties: {list(context.bot_data.get('questions', {}).keys())}")
        return

    logging.info(f"questions count: {len(questions)}")
    q_idx = context.user_data.get('q_index', 0)
    logging.info(f"q_idx: {q_idx}")

    if q_idx >= len(questions):
        await finish_quiz(update, context)
        return

    q_data = questions[q_idx]
    
    keyboard = []
    # Each option in its own row (4 rows instead of 2 rows with 2 columns)
    for i, option in enumerate(q_data['options']):
        keyboard.append([InlineKeyboardButton(option, callback_data=f"ans_{q_idx}_{i}")])
    
    question_text = escape_v1_markdown(str(q_data.get('q', '')))
    
    # Add progress bar
    total = len(questions)
    current = q_idx + 1
    progress_percent = int((current / total) * 100)
    progress_bar_length = 10
    filled = int((progress_percent / 100) * progress_bar_length)
    progress_bar = "█" * filled + "░" * (progress_bar_length - filled)
    
    q_message_text = (
        f"📊 التقدم: [{progress_bar}] {progress_percent}%\n"
        f"❓ **السؤال {current} من {total}**\n\n"
        f"{question_text}"
    )

    thinking_phrases = context.bot_data.get('thinking_phrases', ["🤔"])
    thinking_phrase = random.choice(thinking_phrases) if thinking_phrases else "🤔"
    status_message_text = f"_{escape_v1_markdown(thinking_phrase)}_"

    try:
        if is_new_quiz:
            # Clear any prior explanation messages when starting a new quiz
            await clear_cleanup_msgs(context, user_id)
            q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text, parse_mode="Markdown")
            context.user_data['question_msg_id'] = q_msg.message_id
            context.user_data['status_msg_id'] = status_msg.message_id
        else:
            # Try to edit existing messages, but if they don't exist, send new ones
            try:
                await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data.get('question_msg_id'), text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except (BadRequest, KeyError):
                # Message doesn't exist, send a new one
                q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                context.user_data['question_msg_id'] = q_msg.message_id
            
            try:
                await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data.get('status_msg_id'), text=status_message_text, reply_markup=None, parse_mode="Markdown")
            except (BadRequest, KeyError):
                # Message doesn't exist, send a new one
                status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text, parse_mode="Markdown")
                context.user_data['status_msg_id'] = status_msg.message_id
    except BadRequest:
        if is_new_quiz:
            q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard))
            status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text)
            context.user_data['question_msg_id'] = q_msg.message_id
            context.user_data['status_msg_id'] = status_msg.message_id
        else:
            # Fallback: send new messages if edit fails
            q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard))
            status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text)
            context.user_data['question_msg_id'] = q_msg.message_id
            context.user_data['status_msg_id'] = status_msg.message_id

    save_user_state(user_id, update.effective_user.first_name, difficulty, q_idx, context.user_data['score'], context.user_data['answers'], context.bot_data['db_conn'], context.user_data['question_msg_id'], context.user_data['status_msg_id'])


async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id if hasattr(update, 'effective_chat') else update.effective_user.id if hasattr(update, 'effective_user') else None
    
    # Delete old quiz messages individually to avoid stopping if one fails
    if 'question_msg_id' in context.user_data and context.user_data['question_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['question_msg_id'])
            del context.user_data['question_msg_id']
        except (BadRequest, Exception) as e:
    # Message might already be deleted, ignore silently
            if 'not found' not in str(e).lower():
                logging.debug(f"Could not delete question message: {e}")
            if 'question_msg_id' in context.user_data:
                del context.user_data['question_msg_id']
    
    if 'status_msg_id' in context.user_data and context.user_data['status_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['status_msg_id'])
            del context.user_data['status_msg_id']
        except (BadRequest, Exception) as e:
        # Message might already be deleted, ignore silently
            if 'not found' not in str(e).lower():
                logging.debug(f"Could not delete status message: {e}")
            if 'status_msg_id' in context.user_data:
                del context.user_data['status_msg_id']
    
    # Delete "no cheating" message
    if 'no_cheating_msg_id' in context.user_data and context.user_data['no_cheating_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['no_cheating_msg_id'])
            del context.user_data['no_cheating_msg_id']
        except (BadRequest, Exception) as e:
            # Message might already be deleted, ignore silently
            if 'not found' not in str(e).lower():
                logging.debug(f"Could not delete 'no cheating' message: {e}")
            if 'no_cheating_msg_id' in context.user_data:
                del context.user_data['no_cheating_msg_id']
    
    # Delete old result message if exists
    if 'result_msg_id' in context.user_data and context.user_data['result_msg_id']:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['result_msg_id'])
            del context.user_data['result_msg_id']
        except (BadRequest, Exception) as e:
            # Message might already be deleted, ignore silently
            if 'not found' not in str(e).lower():
                logging.debug(f"Could not delete result message: {e}")
            if 'result_msg_id' in context.user_data:
                del context.user_data['result_msg_id']

    score = context.user_data.get('score', 0)
    difficulty = context.user_data.get('difficulty', '')
    
    if not difficulty or not context.bot_data['questions'].get(difficulty):
        # Avoids error if quiz data is missing
        await context.bot.send_message(chat_id=update.effective_chat.id, text="انتهى الاختبار. شكراً لمشاركتك!")
        return

    total = len(context.bot_data['questions'][difficulty])
    update_lab_score(update.effective_user.id, update.effective_user.first_name, difficulty, score, context.bot_data['db_conn'])
    
    # Check if this was a Mazen test MCQ quiz
    if difficulty.startswith('mazin_id'):
        mazen_state = context.user_data.get('mazen_test', {})
        mazen_state['state'] = 'srd_quiz'
        mazen_state['srd_q_index'] = 0 # Reset for the narrative part
        context.user_data['mazen_test'] = mazen_state # Save state back

        final_msg = f"🎉 **انتهى اختبار الأسئلة المتعددة!**\n📊 نتيجتك: {score} من {total}"
        
        keyboard = [[InlineKeyboardButton("نكمل للاختبار السردي 💬", callback_data="mazin_start_srd")]]

        await context.bot.send_message(chat_id=update.effective_chat.id, text=final_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        # We don't reset user progress because the mazen_test flow is not over
        return
    
    # Check if this was a dynamic exam MCQ quiz (with narrative questions)
    if difficulty.startswith('dynamic_exam_'):
        exam_id = difficulty.replace('dynamic_exam_', '')
        exam_state = context.user_data.get('dynamic_exam', {})
        exam_data = context.bot_data.get('dynamic_exams_data', {}).get(exam_id, {})
        
        # Check if there are narrative questions for this ID
        all_narrative_questions = exam_data.get('narrative_questions', [])
        current_quiz_id = exam_state.get('current_quiz_id', exam_state.get('current_id', 1))

        # Filter narrative questions for current ID - convert both to string for comparison
        debug_log("finish_quiz", "Checking for narrative questions", {"current_quiz_id": current_quiz_id, "all_narrative_count": len(all_narrative_questions)}, "F")

        if all_narrative_questions and len(all_narrative_questions) > 0 and isinstance(all_narrative_questions[0], dict) and 'id' in all_narrative_questions[0]:
            current_quiz_id_str = str(current_quiz_id)
            narrative_questions = [q for q in all_narrative_questions if str(q.get('id', '')) == current_quiz_id_str]
            debug_log("finish_quiz", "Filtered narrative questions", {"filtered_count": len(narrative_questions), "current_quiz_id_str": current_quiz_id_str}, "F")
        else:
            narrative_questions = all_narrative_questions
            debug_log("finish_quiz", "Using all narrative questions", {"narrative_count": len(narrative_questions)}, "F")

        if narrative_questions:
            debug_log("finish_quiz", "Found narrative questions, transitioning to SRD automatically", {"narrative_count": len(narrative_questions)}, "F")
            exam_state['state'] = 'srd_quiz'
            exam_state['srd_q_index'] = 0
            context.user_data['dynamic_exam'] = exam_state

            # Get user_id for sending message
            user_id = update.effective_chat.id if hasattr(update, 'effective_chat') else update.effective_user.id if hasattr(update, 'effective_user') else context.user_data.get('user_id')
            debug_log("finish_quiz", "About to start narrative quiz automatically", {"user_id": user_id}, "F")

            # Send result message briefly, then start narrative quiz automatically
            final_msg = f"🎉 **انتهى اختبار الأسئلة المتعددة!**\n📊 نتيجتك: {score} من {total}\n\n📝 الآن سنبدأ اختبار الأسئلة السردية..."
            try:
                result_msg = await context.bot.send_message(chat_id=user_id, text=final_msg, parse_mode="Markdown")
                context.user_data['result_msg_id'] = result_msg.message_id
                debug_log("finish_quiz", "Result message sent, starting narrative quiz", {"message_id": result_msg.message_id}, "F")
            except Exception as e:
                debug_log("finish_quiz", "Error sending result message", {"error": str(e)}, "F")
                logging.error(f"Error sending result message: {e}")
            
            # Start narrative quiz automatically
            # Create a mock update object for handle_dynamic_exam_srd_start
            class MockUpdate:
                def __init__(self, user):
                    self.effective_user = user
                    self.callback_query = None
            mock_update = MockUpdate(update.effective_user)
            await handle_dynamic_exam_srd_start(mock_update, context, exam_id, current_quiz_id)
            return
    
    elif difficulty == 'video2_mini':
        final_msg = f"🎉 **انتهى الاختبار (مستوى: {escape_v1_markdown(difficulty)})!**\n📊 نتيجتك النهائية: {score} من {total}\n\nشكراً لمشاركتك!"
        keyboard = [[
            InlineKeyboardButton("الاختبار الرئيسي للفيديو 2 📝", callback_data="start_video_2_main_quiz"),
            InlineKeyboardButton("العودة لقائمة الفيديوهات ⬅️", callback_data="lab_test_menu")
        ]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text=final_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        reset_user_progress(update.effective_user.id, None, context.bot_data['db_conn'])

    else:
        user = update.effective_user
        conn = context.bot_data['db_conn']
        
        # Update best score
        best_info = update_best_score(user.id, difficulty, score, total, conn)
        best_score = best_info[0]
        attempts = best_info[1]
        
        # Check and award badges
        check_and_award_badges(user.id, difficulty, score, total, conn)
        
        # Save exam statistics (if it's a dynamic exam)
        if difficulty.startswith('dynamic_exam_'):
            exam_id = difficulty.replace('dynamic_exam_', '')
            time_taken = context.user_data.get('quiz_start_time', 0)
            if time_taken:
                time_taken = int(time.time() - time_taken)
            save_exam_statistics(exam_id, user.id, score, total, time_taken, conn)
        
        # Build final message with best score and retry option
        percentage = int((score / total * 100)) if total > 0 else 0
        final_msg = (
            f"🎉 **انتهى الاختبار!**\n\n"
            f"📊 نتيجتك الحالية: {score} من {total} ({percentage}%)\n"
        )
        
        if attempts > 1:
            final_msg += f"🏆 أفضل نتيجة: {best_score} من {total}\n"
            final_msg += f"🔄 عدد المحاولات: {attempts}\n"
        
        # Get badges
        badges = get_user_badges(user.id, conn)
        if badges:
            badge_names = [b[0] for b in badges if difficulty in b[0]]
            if badge_names:
                final_msg += f"\n🏅 الإنجازات: {', '.join(badge_names[:3])}\n"
        
        final_msg += "\nشكراً لمشاركتك!"
        
        keyboard = [
            [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"retry_quiz_{difficulty}")],
            [InlineKeyboardButton("🏆 لوحة المتصدرين", callback_data=f"leaderboard_{difficulty}")],
            [InlineKeyboardButton("📤 مشاركة النتيجة", callback_data=f"share_result_{difficulty}_{score}_{total}")],
            [InlineKeyboardButton("↩️ العودة للبداية", callback_data="main_menu")]
        ]
        
        await context.bot.send_message(chat_id=update.effective_chat.id, text=final_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        reset_user_progress(update.effective_user.id, None, context.bot_data['db_conn'])



async def handle_video_and_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles messages to extract file_id.
    Triggered by /getid command or by directly sending a video.
    """
    target_message = update.message.reply_to_message or update.message
    file_id = None
    file_type = None
    
    if target_message.video:
        file_id = target_message.video.file_id
        file_type = "Video"
    elif target_message.photo:
        file_id = target_message.photo[-1].file_id
        file_type = "Photo"
    elif target_message.audio:
        file_id = target_message.audio.file_id
        file_type = "Audio"
    elif target_message.document:
        file_id = target_message.document.file_id
        file_type = "Document"
    elif target_message.animation:
        file_id = target_message.animation.file_id
        file_type = "Animation/GIF"
        
    if file_id:
        reply_text = f"✅ **{file_type} File ID Extracted**\n\n`{file_id}`\n\nيمكنك استخدام هذا الـ ID في الكود."
        await update.message.reply_text(reply_text, parse_mode="Markdown")
    else:
        # This part is for the /getid command when it doesn't find media
        if update.message.text and update.message.text.startswith('/getid'):
            await update.message.reply_text(
                "الرجاء الرد على رسالة تحتوي على وسائط (فيديو، صورة...) بهذا الأمر، أو إرسال الفيديو مباشرة."
            )



def setup_course_files():
    """
    تقوم هذه الدالة بفك ضغط ملفات الفيديو والصور عند بدء التشغيل
    """
    # قائمة بملفاتك المضغوطة
    zip_files = ['video1.zip', 'video2.zip', 'video3.zip', 'video4.zip']
    
    print("--- Starting File Extraction ---")
    for zip_file in zip_files:
        # اسم المجلد المتوقع (بدون .zip)
        folder_name = zip_file.replace('.zip', '')
        
        # 1. هل الملف المضغوط موجود؟
        if os.path.exists(zip_file):
            # 2. هل المجلد غير موجود؟ (عشان ما يفك الضغط مرتين)
            if not os.path.exists(folder_name):
                print(f"Extracting {zip_file}...")
                try:
                    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                        zip_ref.extractall('.') # فك في المسار الحالي
                    print(f"✅ {folder_name} is ready.")
                except zipfile.BadZipFile:
                    print(f"❌ Error: {zip_file} is corrupted.")
            else:
                print(f"ℹ️ {folder_name} already exists. Skipping.")
        else:
            print(f"⚠️ Warning: {zip_file} not found in root directory.")
    print("--- File Extraction Finished ---")

# #region agent log
import json
import time

def debug_log(location, message, data=None, hypothesis_id=None):
    
    try:
        log_entry = {
            "id": f"log_{int(time.time()*1000)}_{hash(message) % 1000}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data or {},
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": hypothesis_id
        }
        with open(r"c:\Users\T3lab tiz\Desktop\بوت الاختبار\.cursor\debug.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
           print(f"Failed to write debug log: {e}")
    
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):

    # هذه الدالة لـ UptimeRobot

    def do_HEAD(self):

        self.send_response(200)

        self.end_headers()


    # هذه الدالة لأي شخص يفتح الرابط (لن يرى أي بيانات)

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(b"Bot is active and running.")



def start_web_server():

    # Render يعطينا رقم المنفذ هنا

    port = int(os.environ.get("PORT", 8080))

    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)

    print(f"Dummy server listening on port {port}")

    server.serve_forever()
# #endregion
    
def main():
    # 1. التحقق من التوكن
    if not TOKEN:
        print("Error: Please set BOT_TOKEN in environment variables.")
        return

    # 2. إعداد الملفات وقاعدة البيانات
    setup_course_files()
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    init_db(conn)

    # 3. بناء التطبيق
    application = Application.builder().token(TOKEN).build()

    # 4. تحميل كافة البيانات (يجب أن يتم قبل التشغيل)
    application.bot_data['db_conn'] = conn
    application.bot_data['questions'] = load_all_questions()
    
    # تحميل بيانات مازن
    try:
        mazen_texts, mazen_srd = load_mazen_test_data()
        application.bot_data['mazen_texts'] = mazen_texts
        application.bot_data['mazen_srd'] = mazen_srd
    except Exception as e:
        logging.warning(f"Could not load Mazen data: {e}")

    # تحميل العبارات
    application.bot_data['correct_phrases'] = load_phrases('Correct_Phrases.csv')
    application.bot_data['wrong_phrases'] = load_phrases('Wrong_Phrases.csv')
    application.bot_data['thinking_phrases'] = load_phrases('Thinking_Phrases.csv')

    # 5. إضافة كل الهاندلرز (Handlers)
    application.add_handler(CommandHandler("start", start))
    
    # التحقق من وجود معرف الأدمن لإضافة أوامره
    if ADMIN_TELEGRAM_ID:
        application.add_handler(CommandHandler("admin", handle_admin_command))
        application.add_handler(CommandHandler("getid", handle_video_and_get_id))
        application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video_and_get_id))
        # أمر النصوص الخاص بالأدمن (يجب أن يكون قبل معالج الأزرار العادية)
        application.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_TELEGRAM_ID) & ~filters.COMMAND, handle_admin_text))
        application.add_handler(MessageHandler(filters.Document.ALL & filters.User(ADMIN_TELEGRAM_ID) & ~filters.COMMAND, handle_admin_document))
        application.add_handler(MessageHandler((filters.PHOTO | filters.VIDEO) & filters.User(ADMIN_TELEGRAM_ID) & ~filters.COMMAND, handle_admin_media))
    
    # الهاندلرز العامة لباقي المستخدمين
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_button))
    application.add_handler(CallbackQueryHandler(button_handler))

    # 6. تحميل الاختبارات والقوائم من قاعدة البيانات
    application.bot_data['exams'] = load_exams(conn)
    application.bot_data['menus'] = load_menus(conn)
    
    # --- كود استعادة الأزرار المفقودة (Restoring Buttons Logic) ---
    try:
        exams = application.bot_data.get('exams', {})
        menus = application.bot_data.get('menus', default_menus())
        if "main_menu" not in menus:
            menus["main_menu"] = {"columns": 2, "buttons": []}
            
        buttons = menus.get("main_menu", {}).get("buttons", [])
        existing_callbacks = {btn.get('callback', '') for btn in buttons}
        
        # 6-أ: استعادة أزرار الاختبارات العادية
        added_count = 0
        for exam_id, exam in exams.items():
            callback = f"dynamic_exam_{exam_id}"
            if callback not in existing_callbacks and not exam.get('is_hidden', False):
                buttons.append({
                    "text": exam.get("button_text", f"اختبار {exam_id}"),
                    "callback": callback
                })
                existing_callbacks.add(callback)
                added_count += 1
        
        if added_count > 0:
            menus["main_menu"]["buttons"] = buttons
            application.bot_data["menus"] = menus
            save_menus(menus, conn)
            logging.info(f"Restored {added_count} missing exam buttons")

        # 6-ب: استعادة أزرار (بدون شرح) exam_no_explanation
        cursor = conn.cursor()
        cursor.execute("SELECT exam_id, button_text FROM exam_no_explanation_buttons")
        rows = cursor.fetchall()
        if rows:
            added_no_exp = 0
            for exam_id, button_text in rows:
                callback = f"dynamic_exam_no_explanation_{exam_id}"
                if callback not in existing_callbacks:
                    buttons.append({
                        "text": button_text,
                        "callback": callback
                    })
                    existing_callbacks.add(callback)
                    added_no_exp += 1
            if added_no_exp > 0:
                menus["main_menu"]["buttons"] = buttons
                application.bot_data["menus"] = menus
                save_menus(menus, conn)
                logging.info(f"Restored {added_no_exp} no-explanation buttons")

    except Exception as e:
        logging.error(f"Error restoring buttons: {e}")

    # 7. جدولة المهام (Job Queue) - تصدير البيانات كل ساعة
    if ADMIN_TELEGRAM_ID and application.job_queue:
        try:
            application.job_queue.run_repeating(
                scheduled_user_progress_job,
                interval=3600,
                first=60
            )
            logging.info("Scheduled user_progress export job.")
        except Exception as e:
            logging.warning(f"Job queue error: {e}")

    # 8. تشغيل السيرفر الوهمي (Web Dashboard)
    # ملاحظة: تأكد أن start_web_server معرفة خارج الـ main كما اتفقنا
    print("Starting Web Dashboard...")
    threading.Thread(target=start_web_server, daemon=True).start()

    # 9. تشغيل البوت أخيراً (هذا السطر هو الذي يبقي البوت يعمل)
    print("Bot is running...")
    
    # drop_pending_updates=True: يتجاهل الرسائل القديمة أثناء التوقف لتجنب التكرار
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
