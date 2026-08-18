#!/usr/bin/env python3
"""
Telegram Bot: Cooking Assistant
Бот помогает выбрать блюдо для готовки, отслеживает предыдущие рецепты и позволяет добавлять новые.
Источники рецептов: https://www.iamcook.ru/event/everyday/everyday-dilt
"""

import os
import logging
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(level)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Import Telegram bot components
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, Message
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
)

# Import database functions
import sys
sys.path.insert(0, os.path.dirname(__file__))
from database import Database

# ============================================================
# Configuration
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://your-app.onrender.com")
WEBHOOK_PATH = f"/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Recipe source URL
RECIPE_SOURCE_URL = "https://www.iamcook.ru/event/everyday/everyday-diet"

# Category mapping for filtering
CATEGORY_TRANSLATIONS = {
    "meat": "Мясо",
    "garnish": "Гарнир", 
    "vegetarian": "Вегетарианское",
    "soup": "Суп",
    "dessert": "Десерт",
    "all": "Все рецепты"
}

# Quick reply keyboard categories
CATEGORY_KEYBOARD = [
    ["🥩 Мясо", "🥦 Гарнир"],
    ["🥗 Вегетарианское", "🍲 Суп"],
    ["🎲 Случайное", "➕ Добавить рецепт"],
    ["❓ Помощь"]
]

main_keyboard = ReplyKeyboardMarkup(CATEGORY_KEYBOARD, resize_keyboard=True, one_time_keyboard=False)


# ============================================================
# Helper Functions
# ============================================================

def get_category_buttons(categories: List[str]) -> InlineKeyboardMarkup:
    """Create inline keyboard buttons for category filtering."""
    buttons = []
    for cat in categories:
        cat_key = cat.lower().replace(" ", "").replace("ё", "e")
        buttons.append([InlineKeyboardButton(CATEGORY_TRANSLATIONS.get(cat, cat), callback_data=f"filter_{cat}")])
    return InlineKeyboardMarkup(buttons)


async def fetch_recipes_from_source() -> List[Dict[str, Any]]:
    """Fetch recipes from iamcook.ru source."""
    import requests
    from bs4 import BeautifulSoup
    
    try:
        logger.info(f"Fetching recipes from {RECIPE_SOURCE_URL}")
        response = requests.get(RECIPE_SOURCE_URL, timeout=15, verify=False)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        recipes = []
        
        # The everyday-diet page is a catalog of categories, not individual recipes.
        # Find category/ingredient links that users can browse
        category_links = soup.find_all('a', href=True)
        
        # Extract recipe-relevant links (those pointing to /event/ or with recipe context)
        seen_links = set()
        for link in category_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            # Filter for meaningful recipe categories/links
            if (text and len(text) > 2 and 
                not text.startswith(('http', 'www', '@', 'Книга', 'Планнер', 'Журнал', 'Авторы', 'Расширен')) and
                '/' in href and href.startswith('/')):
                full_url = f"{RECIPE_SOURCE_URL.rsplit('/', 3)[0]}{href}"
                if full_url not in seen_links:
                    seen_links.add(full_url)
                    # Try to determine category from link text
                    category = determine_category_from_text(text)
                    
                    recipes.append({
                        "title": text,
                        "url": full_url,
                        "category": category,
                        "source": "iamcook.ru",
                        "date_added": datetime.now()
                    })
                
                # Limit to 20 recipes per fetch
                if len(recipes) >= 20:
                    break
        
        logger.info(f"Fetched {len(recipes)} recipe categories/links from source")
        return recipes
        
    except Exception as e:
        logger.error(f"Error fetching recipes from source: {e}")
        return []


def determine_category_from_text(text: str) -> str:
    """Determine recipe category from link text."""
    text_lower = text.lower()
    
    # Meat keywords
    meat_keywords = ['мясо', 'говядина', 'свинина', 'курица', 'Индейка', 'лечо', 'баранина']
    # Garnish/side keywords
    garnish_keywords = ['картофель', 'рис', 'гречка', 'макароны', 'овощи']
    # Soup keywords
    soup_keywords = ['суп', 'борщ', 'солянка']
    # Vegetarian keywords
    veg_keywords = ['салат', 'вегетариан', 'салаты']
    # Dessert keywords
    dessert_keywords = ['десерт', 'пирог', 'торт', 'сладкое']
    
    if any(kw in text_lower for kw in meat_keywords):
        return "meat"
    elif any(kw in text_lower for kw in garnish_keywords):
        return "garnish"
    elif any(kw in text_lower for kw in soup_keywords):
        return "soup"
    elif any(kw in text_lower for kw in veg_keywords):
        return "vegetarian"
    elif any(kw in text_lower for kw in dessert_keywords):
        return "dessert"
    else:
        return "all"


def determine_category(title: str) -> str:
    """Automatically determine recipe category from title."""
    title_lower = title.lower()
    
    # Meat keywords
    meat_keywords = ['говядина', 'свинина', 'курица', 'индейка', 'лечо', 'баранина', 'мясо', 'стейк', 'котлета']
    # Garnish/side keywords
    garnish_keywords = ['картофель', 'рис', 'гречка', 'гречка', 'макароны', 'макаронные', 'вязка', 'запекание', 'булка']
    # Soup keywords
    soup_keywords = ['суп', 'борщ', 'щьи', 'солянка', 'тушون', 'жабий', 'крем-соуп']
    # Vegetarian keywords
    veg_keywords = ['салат', 'вегетариан', 'овощи', 'зелень']
    # Dessert keywords
    dessert_keywords = ['пирог', 'торт', 'десерт', 'сладость', 'печенье', 'пирожное', 'конфет']
    
    if any(kw in title_lower for kw in meat_keywords):
        return "meat"
    elif any(kw in title_lower for kw in garnish_keywords):
        return "garnish"
    elif any(kw in title_lower for kw in soup_keywords):
        return "soup"
    elif any(kw in title_lower for kw in veg_keywords):
        return "vegetarian"
    elif any(kw in title_lower for kw in dessert_keywords):
        return "dessert"
    else:
        # Default: try to guess from common patterns
        if 'салат' in title_lower:
            return "vegetarian"
        elif 'борщ' in title_lower:
            return "soup"
        else:
            return "all"


# ============================================================
# Command Handlers
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    db = context.user_data.get('db')
    if not db:
        from database import Database
        db = Database()
        context.user_data['db'] = db
    
    # Ensure user exists in database
    db.ensure_user(user.id, user.username or "unknown")
    
    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "Я твой кулинарный помощник! Я могу:\n"
        "• Предлагать случайные рецепты\n"
        "• Фильтровать блюда по категориям (мясо, гарнир и т.д.)\n"
        "• Сохранять рецепты в твою личную коллекцию\n"
        "• Показывать историю приготовленных блюд\n\n"
        "Выбери действие ниже или напиши команду:"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=main_keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "🗒️ <b>Список команд:</b>\n\n"
        "/start - Начать работу с ботом\n"
        "/choose_dish - Получить случайный рецепт\n"
        "/filter - Фильтровать рецепты по категориям\n"
        "/my_recipes - Показать мои сохраненные рецепты\n"
        "/add_recipe - Добавить свой рецепт\n"
        "/history - История приготовления\n\n"
        "🔍 <b>Быстрые фильтры:</b>\n"
        "• Нажмите кнопку '🥩 Мясо' — покажет только мясные блюда\n"
        "• Нажмите кнопку '🥦 Гарнир' — покажет гарниры\n"
        "• Нажмите '🎲 Случайное' — случайный рецепт из всех категорий\n\n"
        "🌐 Источник рецептов: https://www.iamcook.ru/event/everyday/everyday-diet"
    )
    
    await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=main_keyboard)


async def choose_dish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /choose_dish command - get random recipe."""
    db = context.user_data.get('db')
    if not db:
        from database import Database
        db = Database()
        context.user_data['db'] = db
    
    # Try to fetch new recipes from source
    fetched_recipes = await fetch_recipes_from_source()
    
    if fetched_recipes:
        # Save fetched recipes to database
        for recipe in fetched_recipes:
            db.upsert_recipe(
                title=recipe["title"],
                source_url=recipe["url"],
                category=recipe["category"],
                source="iamcook.ru"
            )
        
        # Get a random recipe (could be from fetched or existing)
        import random
        all_recipes = db.get_recipes_by_category("all")
        if all_recipes:
            recipe = random.choice(all_recipes)
        else:
            await update.message.reply_text("❌ Ошибка: не удалось загрузить рецепты. Попробуйте позже.")
            return
    else:
        # Get from existing database
        all_recipes = db.get_recipes_by_category("all")
        if not all_recipes:
            await update.message.reply_text(
                "📭 Рецепты еще не добавлены.\n"
                "Используйте кнопку '➕ Добавить рецепт' или команда /add_recipe, "
                "или яFETCH новый рецепт с iamcook.ru"
            )
            return
        recipe = random.choice(all_recipes)
    
    # Format recipe message
    category_name = CATEGORY_TRANSLATIONS.get(recipe["category"], recipe["category"])
    url = recipe.get("url", "#")
    message = (
        f"🍽️ <b>{recipe['title']}</b>\n"
        f"📂 Категория: {category_name}\n"
        f"🔗 <a href='{url}'>Читать полный рецепт на iamcook.ru</a>\n\n"
        "Что сделаем дальше?\n"
        "• Сохранить в мои рецепты ✅\n"
        "• Еще один случайный 🎲\n"
        "• Фильтровать по категории 🔍"
    )
    
    await update.message.reply_text(
        message, 
        parse_mode='HTML', 
        reply_markup=main_keyboard,
        disable_web_page_preview=False
    )


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /filter command - show category filtering options."""
    filter_text = (
        "🔍 <b>Фильтрация рецептов</b>\n\n"
        "Выберите категорию, чтобы narrow down рецепты:\n\n"
        "• <b>Мясо</b> — мясные блюда (говядина, свинина, курица)\n"
        "• <b>Гарнир</b> — гарниры (картофель, рис, гречка)\n"
        "• <b>Вегетарианское</b> — вегетарианские блюда\n"
        "• <b>Суп</b> — супы и первые блюда\n"
        "• <b>Десерт</b> — сладкие блюда и пироги\n"
        "• <b>Все рецепты</b> — показать все доступные рецепты\n\n"
        "Или нажмите одну из быстрых кнопок ниже:"
    )
    
    await update.message.reply_text(
        filter_text, 
        parse_mode='HTML', 
        reply_markup=get_category_buttons(["All", "meat", "garnish", "vegetarian", "soup", "dessert"])
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for filtering."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    category_map = {
        "filter_all": "all",
        "filter_meat": "meat", 
        "filter_garnish": "garnish",
        "filter_vegetarian": "vegetarian",
        "filter_soup": "soup",
        "filter_dessert": "dessert"
    }
    
    category = category_map.get(data, "all")
    db = context.user_data.get('db')
    if not db:
        from database import Database
        db = Database()
        context.user_data['db'] = db
    
    # Get recipes filtered by category
    recipes = db.get_recipes_by_category(category)
    
    if not recipes:
        await query.edit_message_text(
            f"📭 В категории {CATEGORY_TRANSLATIONS.get(category, category)} пока нет рецептов.\n"
            "Попробуйте другую категорию или добавьте рецепты командой /add_recipe.",
            reply_markup=main_keyboard
        )
        return
    
    # Select a random recipe from filtered results
    import random
    recipe = random.choice(recipes)
    
    category_name = CATEGORY_TRANSLATIONS.get(category, category)
    url = recipe.get("url", "#")
    message = (
        f"🍽️ <b>{recipe['title']}</b>\n"
        f"📂 Категория: {category_name}\n"
        f"🔗 <a href='{url}'>Читать полный рецепт</a>\n\n"
        "Что дальше?"
    )
    
    await query.edit_message_text(
        message, 
        parse_mode='HTML', 
        reply_markup=main_keyboard,
        disable_web_page_preview=False
    )


# ... (more handlers would continue: my_recipes, add_recipe, history, etc.)

# ============================================================
# Main Application
# ============================================================

def main() -> None:
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("No BOT_TOKEN found in environment variables!")
        return
    
    # Create application
    # Using Application.builder() for PTB 20+ - this is the recommended pattern
    application = Application.builder().token(BOT_TOKEN).build()
    
    dispatcher = updater.dispatcher
    
    # Add command handlers
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("choose_dish", choose_dish_command))
    dispatcher.add_handler(CommandHandler("filter", filter_command))
    
    # Add callback query handler for inline buttons
    dispatcher.add_handler(CallbackQueryHandler(button_callback, pattern=r"^filter_"))
    
    # Add message handler for keyboard buttons
    dispatcher.add_handler(MessageHandler(filters.Regex("🥩 Мясо"), lambda u, c: filter_recipes_by_keyword(u, c, "meat")))
    dispatcher.add_handler(MessageHandler(filters.Regex("🥦 Гарнир"), lambda u, c: filter_recipes_by_keyword(u, c, "garnish")))
    dispatcher.add_handler(MessageHandler(filters.Regex("🎲 Случайное"), lambda u, c: choose_dish_command(u, c)))
    dispatcher.add_handler(MessageHandler(filters.Regex("➕ Добавить рецепт"), lambda u, c: add_recipe_manual(u, c)))
    dispatcher.add_handler(MessageHandler(filters.Regex("❓ Помощь"), lambda u, c: help_command(u, c)))
    
    # Start the bot with webhook
    # For Render, we'll use webhook mode
    logger.info("Starting bot...")
    
    # Webhook setup
    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://your-app.onrender.com")
    WEBHOOK_PATH = f"/{BOT_TOKEN}"
    WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    
    # Remove webhook first to avoid conflicts
    updater.bot.delete_webhook()
    
    # Set webhook
    updater.bot.set_webhook(url=WEBHOOK_URL)
    
    # Start webhook listener
    updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN
    )
    
    # Keep the bot running
    updater.idle()


# --- Handler Functions ---

async def filter_recipes_by_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
    """Handle keyboard button clicks for category filtering."""
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    recipes = db.get_recipes_by_category(category)
    
    if not recipes:
        category_name = CATEGORY_TRANSLATIONS.get(category, category)
        text = f"📭 В категории {category_name} пока нет рецептов."
        if update.message:
            await update.message.reply_text(text, reply_markup=main_keyboard)
        else:
            await update.callback_query.edit_message_text(text, reply_markup=main_keyboard)
        return
    
    import random
    recipe = random.choice(recipes)
    
    category_name = CATEGORY_TRANSLATIONS.get(category, category)
    message = (
        f"🍽️ <b>{recipe['title']}</b>\n"
        f"📂 Категория: {category_name}\n"
        f"🔗 <a href='{recipe.get('url', '#')}'>Читать полный рецепт</a>\n\n"
        "Что дальше?"
    )
    
    if update.message:
        await update.message.reply_text(message, parse_mode='HTML', reply_markup=main_keyboard)
    else:
        await update.callback_query.edit_message_text(message, parse_mode='HTML', reply_markup=main_keyboard)


async def add_recipe_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle manual recipe addition."""
    user = update.effective_user
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    db.ensure_user(user.id, user.username or "unknown")
    
    if update.message:
        await update.message.reply_text(
            "📝 <b>Добавление рецепта вручную</b>\n\n"
            "Пожалуйста, отправьте мне рецепт в следующем формате:\n"
            "Название рецепта\n"
            "Категория (мясо, гарнир, суп, десерт, вегетарианское)\n"
            "Ингредиенты (список через запятую)\n"
            "Время приготовления (минуты)\n\n"
            "Или просто отправьте название, и я попробую определить категорию автоматически.",
            parse_mode='HTML', reply_markup=main_keyboard
        )
    
    # Set state to wait for recipe info
    context.user_data['awaiting_recipe'] = True


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle general text messages from users."""
    user = update.effective_user
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    db.ensure_user(user.id, user.username or "unknown")
    
    text = update.message.text.strip()
    
    # Check if user is waiting to add a recipe
    if context.user_data.get('awaiting_recipe'):
        await _process_new_recipe_manual(update, context, text)
        return
    
    # Handle text commands as alternatives to keyboard
    if text in ["Мясо", "мясо", "Meat"]:
        await filter_recipes_by_keyword(update, context, "meat")
    elif text in ["Гарнир", "гарнир", "Garnish"]:
        await filter_recipes_by_keyword(update, context, "garnish")
    elif text in ["Случайное", "случайное", "Random"]:
        await choose_dish_command(update, context)
    elif text in ["Мои рецепты", "мои рецепты", "My Recipes"]:
        await my_recipes_command(update, context)
    elif text in ["История", "история", "History"]:
        await history_command(update, context)
    else:
        # Default: try to find a recipe or show help hint
        await update.message.reply_text(
            "Я не понял ваш запрос. Используйте кнопки меню ниже или команду /help.",
            reply_markup=main_keyboard
        )


async def _process_new_recipe_manual(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Process manually added recipe from text."""
    from bs4 import BeautifulSoup
    import requests
    
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    user = update.effective_user
    db.ensure_user(user.id, user.username or "unknown")
    
    # Parse the recipe info from text
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    if not lines:
        await update.message.reply_text("❌ Пустой текст. Пожалуйста, отправьте формат: название, категория, ингредиенты, время")
        return
    
    # Try to extract information
    title = lines[0]
    category = "all"
    ingredients = ""
    cooking_time = 0
    
    if len(lines) >= 2:
        cat_input = lines[1].lower()
        if cat_input in ['мясо', 'meat', 'meat dish']:
            category = 'meat'
        elif cat_input in ['гарнир', 'garnish', 'side dish']:
            category = 'garnish'
        elif cat_input in ['суп', 'soup']:
            category = 'soup'
        elif cat_input in ['десерт', 'dessert', ' dessert']:
            category = 'dessert'
        elif cat_input in ['вегетарианское', 'vegetarian']:
            category = 'vegetarian'
        else:
            category = cat_input
    
    if len(lines) >= 3:
        ingredients = lines[2]
    
    if len(lines) >= 4:
        try:
            cooking_time = int(lines[3])
        except ValueError:
            cooking_time = 0
    
    # Upsert the recipe
    recipe_id = db.upsert_recipe(
        title=title,
        source_url=None,
        category=category,
        source='user_added',
        ingredients=ingredients,
        cooking_time=cooking_time,
        added_by=user.id
    )
    
    # Save to user's collection
    db.save_recipe_for_user(user.id, recipe_id)
    
    # Mark as no longer awaiting recipe
    context.user_data['awaiting_recipe'] = False
    
    # Confirmation message
    category_name = CATEGORY_TRANSLATIONS.get(category, category)
    message = (
        f"✅ <b>Рецепт успешно добавлен!</b>\n\n"
        f"🍽️ <b>{title}</b>\n"
        f"📂 Категория: {category_name}\n"
        f"⏱️ Время: {cooking_time} мин\n"
        f"📝 Ингредиенты: {ingredients}\n\n"
        "Вы можете найти его в разделе 'Мои рецепты' или выбрать случайное блюдо."
    )
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=main_keyboard)


async def my_recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's saved recipes."""
    user = update.effective_user
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    db.ensure_user(user.id, user.username or "unknown")
    
    user_recipes = db.get_user_recipes(user.id)
    
    if not user_recipes:
        await update.message.reply_text(
            "📭 У вас еще нет сохраненных рецептов.\n"
            "Используйте кнопку '➕ Добавить рецепт', чтобы добавить свои, "
            "или я_fetch новый рецепт с iamcook.ru.",
            reply_markup=main_keyboard
        )
        return
    
    # Show first few recipes
    message = f"📋 <b>Ваши сохраненные рецепты ({len(user_recipes)})</b>\n\n"
    for i, recipe in enumerate(user_recipes[:5], 1):
        category_name = CATEGORY_TRANSLATIONS.get(recipe.get('category', 'all'), recipe.get('category', 'all'))
        message += f"{i}. <b>{recipe['title']}</b> ({category_name})\n"
        message += f"   🔗 <a href='{recipe.get('url', '#')}'>Читать</a>\n\n"
    
    if len(user_recipes) > 5:
        message += f"... и еще {len(user_recipes) - 5} рецептов."
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=main_keyboard)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show cooking history."""
    user = update.effective_user
    db = context.user_data.get('db')
    if not db:
        from bot.database import Database
        db = Database()
        context.user_data['db'] = db
    
    db.ensure_user(user.id, user.username or "unknown")
    
    # Get cooking history
    rows = db.conn.execute(
        "SELECT r.title, ch.rating, ch.cooked_at FROM cooking_history ch "
        "JOIN recipes r ON ch.recipe_id = r.id "
        "WHERE ch.user_id = ? ORDER BY ch.cooked_at DESC LIMIT 5",
        (user.id,)
    ).fetchall()
    
    if not rows:
        await update.message.reply_text(
            "📜 <b>История готовки</b>\n\n"
            "У вас еще нет записи о готовке рецептов.\n"
            "Начните готовить и отмечайте свои достижения!",
            parse_mode='HTML', reply_markup=main_keyboard
        )
        return
    
    message = "📜 <b>Ваша история готовки:</b>\n\n"
    for row in rows:
        rating_stars = "⭐" * row[1] if row[1] else "нет оценки"
        date = row[2].strftime("%d.%m.%Y") if row[2] else "недавно"
        message += f"• <b>{row[0]}</b> - {rating_stars} ({date})\n"
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=main_keyboard)


# ... (rest of the file continues)

if __name__ == "__main__":
    main()