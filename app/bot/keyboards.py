from telegram import KeyboardButton, ReplyKeyboardMarkup

QUICK_BUTTONS = [["😄 Tell me a joke", "📋 My tasks"], ["⏰ My reminders", "🧠 What do you remember?"]]
QUICK_TEXT = {
    "😄 Tell me a joke": "Tell me a joke",
    "📋 My tasks": "What are my open tasks?",
    "⏰ My reminders": "What reminders do I have coming up?",
    "🧠 What do you remember?": "What do you remember about me?",
}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(b) for b in row] for row in QUICK_BUTTONS], resize_keyboard=True)
