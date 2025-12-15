import logging
import json
import os
import random
import sqlite3
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ------------------- إعدادات البوت -------------------
TOKEN = "8003555082:AAHPSa3zLIhJkVhaIF471D_JDhglV5EfL2A"
CHANNEL_USERNAME = "@mishalinitiative"
CHANNEL_ID = "@mishalinitiative"

# ملف قاعدة البيانات
DB_FILE = "user_progress.db"

# إعداد السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ------------------- السيرفر الوهمي (لحل مشكلة Render) -------------------
# تم وضع الكلاس والدالة هنا بشكل صحيح مع المسافات المطلوبة
# ------------------- السيرفر الوهمي (لحل مشكلة Render) -------------------
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    # أضفنا هذه الدالة لكي نرد على UptimeRobot بدون أخطاء
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_web_server():
    # Render يوفر المنفذ عبر متغير البيئة PORT
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"Dummy server listening on port {port}")
    server.serve_forever()

# ------------------- دوال تحميل الأسئلة والعبارات -------------------

def load_phrases(file_path):
    """تحميل العبارات من ملف CSV."""
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
    """
    تحميل الأسئلة من ملفات CSV لكل مستوى صعوبة.
    """
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

async def send_level_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_msg = f"أهلاً بك يا {user.first_name}! 📡\n\nاختر مستوى الصعوبة للبدء."
    keyboard = [
        [
            InlineKeyboardButton("صعب 🔥", callback_data="level_hard"),
            InlineKeyboardButton("متوسط 🧠", callback_data="level_medium"),
            InlineKeyboardButton("سهل ✅", callback_data="level_easy")
        ]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data['db_conn']
    context.user_data.update(get_user_state(user.id, user.first_name, conn))
    
    if await check_subscription(user.id, context):
        await send_level_choice(update, context)
    else:
        await send_subscription_prompt(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    conn = context.bot_data['db_conn']
    
    await query.answer()
    
    if data == "check_sub":
        if await check_subscription(user.id, context):
            await send_level_choice(update, context)
        else:
            await query.answer("❌ لم يتم العثور على اشتراكك.", show_alert=True)

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
        correct_phrases = context.bot_data.get('correct_phrases', ["✅ **إجابة صحيحة!**"])
        wrong_phrases = context.bot_data.get('wrong_phrases', ["❌ **إجابة خاطئة!**"])

        if ans_idx == correct_ans:
            context.user_data['score'] += 1
            result_text = random.choice(correct_phrases) if correct_phrases else "✅ **إجابة صحيحة!**"
            context.user_data['answers'][str(q_idx)] = True
        else:
            wrong_phrase = random.choice(wrong_phrases) if wrong_phrases else "❌ **إجابة خاطئة!**"
            result_text = f"{wrong_phrase}\nالصحيح هو: *{current_q['options'][correct_ans]}*"
            context.user_data['answers'][str(q_idx)] = False

        context.user_data['q_index'] += 1
        
        save_user_state(user.id, user.first_name, difficulty, context.user_data['q_index'], context.user_data['score'], context.user_data['answers'], conn, context.user_data['question_msg_id'], context.user_data['status_msg_id'])

        escaped_explanation = escape_v1_markdown(current_q.get('expl', ''))
        explanation = f"\n\n💡 **التفسير:** {escaped_explanation}"
        next_btn = InlineKeyboardButton("السؤال التالي ⬅️", callback_data="next_q")
        full_text = f"{result_text}{explanation}"

        await context.bot.edit_message_text(chat_id=user.id, message_id=context.user_data['status_msg_id'], text=full_text, reply_markup=InlineKeyboardMarkup([[next_btn]]), parse_mode="Markdown")

    elif data == "next_q":
        await send_question_view(update, context)

    elif data == "restart_quiz":
        await query.delete_message()
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
    
    final_msg = f"🎉 **انتهى الاختبار (مستوى: {difficulty})!**\n📊 نتيجتك النهائية: {score} من {total}\n\nشكراً لمشاركتك!"
    
    keyboard = [[InlineKeyboardButton("🔄 ابدأ اختباراً جديداً", callback_data="restart_quiz")]]
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=final_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    reset_user_progress(update.effective_user.id, None, context.bot_data['db_conn'])

def main():
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Error: Please set your bot token in the code.")
        return

    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    init_db(conn)

    application = Application.builder().token(TOKEN).build()
    
    application.bot_data['db_conn'] = conn
    application.bot_data['questions'] = load_all_questions()

    application.bot_data['correct_phrases'] = load_phrases('Correct_Phrases.csv')
    application.bot_data['wrong_phrases'] = load_phrases('Wrong_Phrases.csv')
    application.bot_data['thinking_phrases'] = load_phrases('Thinking_Phrases.csv')

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # بدء السيرفر الوهمي في خيط منفصل
    print("Starting dummy web server...")
    threading.Thread(target=start_web_server, daemon=True).start()

    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
