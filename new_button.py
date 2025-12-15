from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_new_test_keyboard():
    keyboard = [
        [InlineKeyboardButton("اختبار جديد 📝", callback_data="new_test")]
    ]
    return InlineKeyboardMarkup(keyboard)
