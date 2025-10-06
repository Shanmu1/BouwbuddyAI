import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# --- Configuration ---
# Load environment variables from .env file
load_dotenv()

# Securely get tokens from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found. Please add it to your .env file.")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found. Please add it to your .env file.")

# Configure the Gemini AI
try:
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    raise ValueError(f"Failed to configure Gemini AI. Check your API key. Error: {e}")

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- In-memory Database (for MVP) ---
db = {"updates": []}

# --- Conversation States ---
(
    NAME,
    FUNCTION,
    COMPANY,
    LOCATION,
    HOURS_WORKED,
    UPDATE_TEXT,
    PLANNING,
    PHOTO,
) = range(8)


# --- Bot Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Hi! I'm BouwBuddy AI. I'm here to help you log your daily construction progress.\n\n"
        "You can use the following commands:\n"
        "/update - Start logging a new progress update.\n"
        "/daily_report - Generate a report for today's updates.\n"
        "/weekly_report - Generate a report for the last 7 days.\n"
        "/help - Show this message again."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the help message."""
    await start(update, context)


# --- Update Logging Conversation ---
async def start_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to log a new update."""
    await update.message.reply_text(
        "Let's log a new update. First, what is your full name?"
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the name and asks for the function."""
    context.user_data["update_data"] = {"name": update.message.text}
    await update.message.reply_text("Got it. What is your function or role? (e.g., Electrician, Plumber)")
    return FUNCTION


async def get_function(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the function and asks for the company."""
    context.user_data["update_data"]["function"] = update.message.text
    await update.message.reply_text("Great. What company do you work for?")
    return COMPANY


async def get_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the company and asks for the location."""
    context.user_data["update_data"]["company"] = update.message.text
    await update.message.reply_text("Where on the site did you work today? (e.g., Second Floor, West Wing)")
    return LOCATION


async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the location and asks for hours worked."""
    context.user_data["update_data"]["location"] = update.message.text
    await update.message.reply_text("How many hours did you work?")
    return HOURS_WORKED


async def get_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the hours and asks for the task update."""
    try:
        hours = float(update.message.text.replace(',', '.'))
        context.user_data["update_data"]["hours_worked"] = hours
        await update.message.reply_text("Thanks. Please describe the work you completed today.")
        return UPDATE_TEXT
    except ValueError:
        await update.message.reply_text("That doesn't look like a valid number. Please enter the hours again.")
        return HOURS_WORKED


async def get_update_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the task update and asks for planning notes."""
    context.user_data["update_data"]["update_text"] = update.message.text
    await update.message.reply_text("Any notes on planning? (e.g., 'Blocked by other team', 'Ready for inspection')")
    return PLANNING


async def get_planning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the planning notes and asks for a photo."""
    context.user_data["update_data"]["planning"] = update.message.text
    await update.message.reply_text("Perfect. Now, please send one photo of your work.")
    return PHOTO


async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the photo and ends the conversation."""
    photo_file = await update.message.photo[-1].get_file()

    context.user_data["update_data"]["photo_id"] = photo_file.file_id
    context.user_data["update_data"]["date"] = datetime.now().isoformat()

    db["updates"].append(context.user_data["update_data"])

    await update.message.reply_text(
        "Thank you! Your update has been logged successfully. "
        "Use /daily_report or /weekly_report to see the summary."
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    context.user_data.clear()
    await update.message.reply_text(
        "Update cancelled. You can start a new one with /update.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# --- Report Generation ---
async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetches today's data and generates a report."""
    today = datetime.now().date()
    todays_updates = [
        u for u in db["updates"]
        if datetime.fromisoformat(u["date"]).date() == today
    ]
    if not todays_updates:
        await update.message.reply_text("No updates were logged today. Nothing to report.")
        return
    await generate_and_send_report(update, context, todays_updates, "Daily")


async def weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetches last 7 days of data and generates a report."""
    seven_days_ago = datetime.now() - timedelta(days=7)
    weekly_updates = [
        u for u in db["updates"]
        if datetime.fromisoformat(u["date"]) >= seven_days_ago
    ]
    if not weekly_updates:
        await update.message.reply_text("No updates were logged in the last 7 days.")
        return
    await generate_and_send_report(update, context, weekly_updates, "Weekly")


def build_prompt(updates_data, report_type):
    """Constructs the prompt to be sent to the Gemini AI."""
    prompt = (
        "You are BouwBuddy AI, an expert construction project manager. "
        f"You will be given a series of raw {report_type.lower()} progress updates from a construction team. "
        "Your task is to analyze all the updates and generate three distinct, professional reports in English.\n\n"
        "Here are the raw updates:\n"
        "-------------------------\n"
    )

    for i, u in enumerate(updates_data, 1):
        prompt += (
            f"Update {i}:\n"
            f"- Name: {u.get('name', 'N/A')}\n"
            f"- Function: {u.get('function', 'N/A')}\n"
            f"- Company: {u.get('company', 'N/A')}\n"
            f"- Location: {u.get('location', 'N/A')}\n"
            f"- Hours Worked: {u.get('hours_worked', 'N/A')}\n"
            f"- Task Description: {u.get('update_text', 'N/A')}\n"
            f"- Planning Notes: {u.get('planning', 'N/A')}\n"
            f"- A photo was submitted for this update (ID: {u.get('photo_id', 'N/A')}).\n\n"
        )

    prompt += (
        "-------------------------\n"
        "Based on ALL the information above, please generate the following reports. Be concise and professional.\n\n"
        "1.  **Hours by Company:** Calculate the total hours worked for each company and list them.\n\n"
        "2.  **Technical Supervisor's Report:** Synthesize all updates into a technical summary. Mention specific tasks, progress, locations, and any potential blockers or delays mentioned in the planning notes. Refer to photos as evidence where relevant (e.g., '...as documented in the photo from [Name]').\n\n"
        "3.  **Non-Technical Client Update:** Create a friendly, high-level summary for the client. Avoid technical jargon. Focus on visible progress and milestones. Be positive and reassuring."
    )
    return prompt


async def generate_and_send_report(update: Update, context: ContextTypes.DEFAULT_TYPE, updates_data,
                                   report_type) -> None:
    """Calls the Gemini AI and sends the formatted report back to the user."""
    await update.message.reply_text(f"Generating the {report_type} Report... The AI is thinking, this may take a moment.")

    try:
        prompt = build_prompt(updates_data, report_type)
        # --- THE FIX IS HERE ---
        # Use the stable model name, which will work with the upgraded library.
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        ai_report = response.text

        await update.message.reply_text(f"--- *{report_type} Report* ---", parse_mode="Markdown")
        await update.message.reply_text(ai_report, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error generating AI report: {e}")
        await update.message.reply_text("Sorry, there was an error while generating the AI report. Please try again later.")
        return

    # Send the photos with captions
    if updates_data:
        await update.message.reply_text("*Submitted Photos for this period:*", parse_mode="Markdown")
        media_group = []
        for u in updates_data:
            if 'photo_id' in u:
                caption = f"{u['name']} ({u['company']}) - {u['update_text']}"
                media_group.append(InputMediaPhoto(media_id=u['photo_id'], caption=caption[:1024])) # Caption limit

        if media_group:
            # Telegram allows a maximum of 10 items in a media group
            for i in range(0, len(media_group), 10):
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group[i:i+10])
        else:
            await update.message.reply_text("No photos were submitted in this period.")


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("update", start_update)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            FUNCTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_function)],
            COMPANY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_company)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_location)],
            HOURS_WORKED: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_hours)],
            UPDATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_update_text)],
            PLANNING: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_planning)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("daily_report", daily_report))
    application.add_handler(CommandHandler("weekly_report", weekly_report))

    logger.info("Starting bot...")
    application.run_polling()


if __name__ == "__main__":
    main()