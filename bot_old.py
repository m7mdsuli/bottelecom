import logging
import json
import os
import random
import sqlite3
import pandas as pd
import zipfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import BadRequest
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ------------------- إعدادات البوت الآمنة -------------------
# سيأخذ البوت التوكن من متغيرات البيئة في Render
TOKEN = os.environ.get("BOT_TOKEN") 

CHANNEL_USERNAME = "@mishalinitiative"
CHANNEL_ID = "@mishalinitiative"

# ملف قاعدة البيانات
DB_FILE = "user_progress.db"

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
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        progress_rows_html = ""
        lab_rows_html = ""
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

                conn.close()
            else:
                progress_rows_html = "<tr><td colspan='5' style='text-align:center'>لم يتم إنشاء قاعدة البيانات بعد.</td></tr>"
                lab_rows_html = "<tr><td colspan='7' style='text-align:center'>لم يتم إنشاء قاعدة البيانات بعد.</td></tr>"
        except Exception as e:
            progress_rows_html = f"<tr><td colspan='5'>خطأ في القراءة: {e}</td></tr>"
            lab_rows_html = f"<tr><td colspan='7'>خطأ في القراءة: {e}</td></tr>"

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
                <a href="/" class="refresh-btn">🔄 تحديث القائمة</a>
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
        
    return question_sets

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
    cursor.execute("INSERT OR IGNORE INTO lab_results (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    cursor.execute("UPDATE lab_results SET first_name = ? WHERE user_id = ?", (first_name, user_id))
    
    valid_columns = ['video1', 'video2', 'video2_mini', 'video3', 'video4']
    if difficulty in valid_columns:
        query = f"UPDATE lab_results SET {difficulty} = ? WHERE user_id = ?"
        cursor.execute(query, (score, user_id))
        conn.commit()

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
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_msg = f"أهلاً بك يا {user.first_name}! 👋\n\nاختر من القائمة للبدء."
    keyboard = [
        [
            InlineKeyboardButton("اختبار مخبر الاتصالات 🔬", callback_data="lab_test_menu"),
            InlineKeyboardButton("إختبارات سابقة 📚", callback_data="previous_tests")
        ]
    ]
    
    # Check if it's a callback query or a new message
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))

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

AUTHORIZED_ID = 659622432

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    conn = context.bot_data['db_conn']
    context.user_data.update(get_user_state(user.id, user.first_name, conn))
    
    if await check_subscription(user.id, context):
        await send_main_menu(update, context) # Changed this line
    else:
        await send_subscription_prompt(update, context)

async def send_previous_tests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = "اختر أحد الاختبارات التالية:"
    keyboard = [
        [InlineKeyboardButton("اختبار نظري الاتصالات حديثة", callback_data="start_theory_test")],
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    data = query.data
    conn = context.bot_data['db_conn']
    
    await query.answer()
    
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

        else:

            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")

            return

        session_keyboard = [[InlineKeyboardButton("تمام 👍", callback_data="video_1_part3")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="تمام 👍", reply_markup=InlineKeyboardMarkup(session_keyboard)
        )
        context.user_data['video_1_part2_button_msg_id'] = sent_message.message_id
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

        else:

            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Error: Image not found at {image_path}")

            return

        session_keyboard = [[InlineKeyboardButton("جاهز للإختبار 👍", callback_data="video_1_finish")]]
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="جاهز للإختبار ؟", reply_markup=InlineKeyboardMarkup(session_keyboard)
        )

        context.user_data['video_1_part4_button_msg_id'] = sent_message.message_id

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
        
        image_path = os.path.join("video2", "image1.png")
        if os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                msg2 = await context.bot.send_photo(chat_id=query.message.chat_id, photo=image_file)
                context.user_data['video_2_image1_msg_id'] = msg2.message_id
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
        context.user_data['video_2_part2_msg_id'] = sent_message.message_id

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
        

                        
        
        

                        
                ready_keyboard_msg = await context.bot.send_message(
        

                        
                    chat_id=query.message.chat_id,
        

                        
                    text="حضرت الفيديو وجاهز لنبلش بشرح أكثر ؟", reply_markup=InlineKeyboardMarkup(ready_keyboard)
        

                        
                )
        

                        
                context.user_data['video_4_ready_keyboard_msg_id'] = ready_keyboard_msg.message_id
        

                        
                context.user_data['video_4_message_history'].append(ready_keyboard_msg.message_id)
        

                        
                # Removed query.delete_message()
        

                        
        
            
            
         
    elif data == "video_4_ready":
        

                        
        
            
            
            
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
            
            
            
    elif data == "video_4_part2":
            
            
            
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
            
            
            
    elif data == "video_4_part3":
            
            
            
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

async def send_question_view(update: Update, context: ContextTypes.DEFAULT_TYPE, is_new_quiz: bool = False):
    user_id = update.effective_user.id
    difficulty = context.user_data['difficulty']
    questions = context.bot_data['questions'][difficulty]
    q_idx = context.user_data.get('q_index', 0)

    if q_idx >= len(questions):
        await finish_quiz(update, context)
        return

    q_data = questions[q_idx]
    
    keyboard = []
    row = []
    for i, option in enumerate(q_data['options']):
        row.append(InlineKeyboardButton(option, callback_data=f"ans_{q_idx}_{i}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    
    question_text = escape_v1_markdown(str(q_data.get('q', '')))
    q_message_text = f"❓ **السؤال {q_idx + 1} من {len(questions)} (مستوى: {difficulty})**:\n\n{question_text}"

    thinking_phrases = context.bot_data.get('thinking_phrases', ["🤔"])
    thinking_phrase = random.choice(thinking_phrases) if thinking_phrases else "🤔"
    status_message_text = f"_{escape_v1_markdown(thinking_phrase)}_"

    try:
        if is_new_quiz:
            q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text, parse_mode="Markdown")
            context.user_data['question_msg_id'] = q_msg.message_id
            context.user_data['status_msg_id'] = status_msg.message_id
        else:
            await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data['question_msg_id'], text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data['status_msg_id'], text=status_message_text, reply_markup=None, parse_mode="Markdown")
    except BadRequest:
        if is_new_quiz:
            q_msg = await context.bot.send_message(chat_id=user_id, text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard))
            status_msg = await context.bot.send_message(chat_id=user_id, text=status_message_text)
            context.user_data['question_msg_id'] = q_msg.message_id
            context.user_data['status_msg_id'] = status_msg.message_id
        else:
            await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data['question_msg_id'], text=q_message_text, reply_markup=InlineKeyboardMarkup(keyboard))
            await context.bot.edit_message_text(chat_id=user_id, message_id=context.user_data['status_msg_id'], text=status_message_text, reply_markup=None)

    save_user_state(user_id, update.effective_user.first_name, difficulty, q_idx, context.user_data['score'], context.user_data['answers'], context.bot_data['db_conn'], context.user_data['question_msg_id'], context.user_data['status_msg_id'])


async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data['question_msg_id'])
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data['status_msg_id'])
    except Exception as e:
        logging.warning(f"Could not delete old quiz messages: {e}")

    score = context.user_data['score']
    difficulty = context.user_data['difficulty']
    total = len(context.bot_data['questions'][difficulty])
    update_lab_score(update.effective_user.id, update.effective_user.first_name, difficulty, score, context.bot_data['db_conn'])
    
    final_msg = f"🎉 **انتهى الاختبار (مستوى: {escape_v1_markdown(difficulty)})!**\n📊 نتيجتك النهائية: {score} من {total}\n\nشكراً لمشاركتك!"
    
    if difficulty == 'video2_mini':
        keyboard = [[
            InlineKeyboardButton("الاختبار الرئيسي للفيديو 2 📝", callback_data="start_video_2_main_quiz"),
            InlineKeyboardButton("العودة لقائمة الفيديوهات ⬅️", callback_data="lab_test_menu")
        ]]
    else:
        keyboard = [[InlineKeyboardButton("العودة للبداية ↩️", callback_data="restart_quiz")]]
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=final_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    reset_user_progress(update.effective_user.id, None, context.bot_data['db_conn'])



async def handle_video_and_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles messages to extract file_id.
    Triggered by /getid command or by directly sending a video.
    """
    if update.message.from_user.id != AUTHORIZED_ID:
        return
        
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


""" async def post_init(application: Application):
    """
    Sends a broadcast message to all users when the bot starts.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    user_ids = set()
    try:
        # Get users from user_progress
        cursor.execute("SELECT user_id FROM user_progress")
        user_ids.update(row[0] for row in cursor.fetchall())
        
        # Get users from lab_results if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lab_results'")
        if cursor.fetchone():
            cursor.execute("SELECT user_id FROM lab_results")
            user_ids.update(row[0] for row in cursor.fetchall())
    except Exception as e:
        logging.error(f"Error fetching users for broadcast: {e}")
    finally:
        conn.close()

    message = "تم حل مشكلة اسئلة الفيديو الثاني , بالتوفيق !\n\nيرجى الضغط على /start للبدء من جديد."
    
    for user_id in user_ids:
        try:
            await application.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logging.warning(f"Failed to send broadcast to {user_id}: {e}")
    """
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

def main():
    if not TOKEN:
        print("Error: Please set BOT_TOKEN in environment variables.")
        return
    setup_course_files()
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    init_db(conn)

   ## application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    application.bot_data['db_conn'] = conn
    application.bot_data['questions'] = load_all_questions()

    application.bot_data['correct_phrases'] = load_phrases('Correct_Phrases.csv')
    application.bot_data['wrong_phrases'] = load_phrases('Wrong_Phrases.csv')
    application.bot_data['thinking_phrases'] = load_phrases('Thinking_Phrases.csv')

    application.add_handler(CommandHandler("start", start))
    # Command to get file ID
    application.add_handler(CommandHandler("getid", handle_video_and_get_id))
    # Handler for direct video messages
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video_and_get_id))
    application.add_handler(CallbackQueryHandler(button_handler))

    # بدء السيرفر الوهمي في خيط منفصل
    print("Starting Web Dashboard...")
    threading.Thread(target=start_web_server, daemon=True).start()

    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
