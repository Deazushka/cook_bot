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
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Load environment variables
load_dotenv()

# Recipe source URL
RECIPE_SOURCE_URL = "https://www.iamcook.ru/event/everyday/everyday-dilt"

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
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
# Health Check Server
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/healthz', '/']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default HTTP server logs
        pass


def start_health_check_server():
    """Start a simple HTTP server for health checks in a background thread."""
    try:
        port = int(os.getenv("PORT", 8000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"Health check server started on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health check server error: {e}")


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
    soup_keywords = ['суп', 'борщ', 'щиьи', 'солянка', 'тушон', 'жабий', 'крем-соуп']
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


async def filter_recipes_by_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
    """Filter recipes by category and send a random one."""
    try:
        db = context.user_data.get('db')
        if not db:
            from database import Database
            db = Database()
            context.user_data['db'] = db
        
        # Get recipes filtered by category
        recipes = db.get_recipes_by_category(category)
        
        if not recipes:
            await update.message.reply_text(
                f"📭 В категории {CATEGORY_TRANSLATIONS.get(category, category)} пока нет рецептов.\n"
                "Попробуйте другую категорию или добавьте рецепты командой /add_recipe.",
                reply_markup=main_keyboard
            )
            return
        
        # Select a random recipe from filtered results
        import random
        recipe = random.choice(recipes)
        
        category_name = CATEGORY_TRANSLATIONS.get(recipe["category"], recipe["category"])
        url = recipe.get("url", "#")
        message = (
            f"🍽️ <b>{recipe['title']}</b>\n"
            f"📂 Категория: {category_name}\n"
            f"🔗 <a href='{url}'>Читать полный рецепт</a>\n\n"
            "Что дальше?"
        )
        
        await update.message.reply_text(
            message, 
            parse_mode='HTML', 
            reply_markup=main_keyboard,
            disable_web_page_preview=False
        )
    except Exception as e:
        logger.error(f"Error in filter_recipes_by_keyword: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке запроса. Попробуйте позже.")
        except:
            pass


async def add_recipe_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle manual recipe addition request."""
    try:
        user = update.effective_user
        db = context.user_data.get('db')
        if not db:
            from database import Database
            db = Database()
            context.user_data['db'] = db
        
        # Ensure user exists in database
        db.ensure_user(user.id, user.username or "unknown")
        
        await update.message.reply_text(
            "📝 Чтобы добавить свой рецепт, отправьте мне сообщение в следующем формате:\n\n"
            "<b>Название рецепта</b>\n"
            "<b>Ингредиенты:</b> список ингредиентов через запятую\n"
            "<b>Время приготовления:</b> в минутах (число)\n"
            "<b>Категория:</b> мясо/гарнир/вегетарианское/суп/десерт\n"
            "<b>Источник:</b> (опционально) URL или описание источника\n\n"
            "Пример:\n"
            "Борщ украинский\n"
            "Ингредиенты: свекла, капуста, картофель, лук, морковь, томатная паста\n"
            "Время приготовления: 90\n"
            "Категория: суп\n"
            "Источник: мамина рецептная тетрадь\n\n"
            "Отправьте рецепт одним сообщением, и я сохраню его для вас!",
            parse_mode='HTML',
            reply_markup=main_keyboard
        )
    except Exception as e:
        logger.error(f"Error in add_recipe_manual: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке запроса. Попробуйте позже.")
        except:
            pass


# ============================================================
# Command Handlers
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    try:
        logger.info(f"Received /start command from user {update.effective_user.id}")
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
    except Exception as e:
        logger.error(f"Error in start_command: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке команды /start. Попробуйте позже.")
        except:
            pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    try:
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
            "🌐 Источник рецептов: https://www.iamcook.ru/event/everyday/everyday-dilt"
        )
        
        await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=main_keyboard)
    except Exception as e:
        logger.error(f"Error in help_command: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке команды /help. Попробуйте позже.")
        except:
            pass


async def choose_dish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /choose_dish command - get random recipe."""
    try:
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
    except Exception as e:
        logger.error(f"Error in choose_dish_command: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке команды /choose_dish. Попробуйте позже.")
        except:
            pass


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /filter command - show category filtering options."""
    try:
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
    except Exception as e:
        logger.error(f"Error in filter_command: {e}", exc_info=True)
        try:
            await update.message.reply_text("Произошла ошибка при обработке команды /filter. Попробуйте позже.")
        except:
            pass


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for filtering."""
    try:
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
        
        category_name = CATEGORY_TRANSLATIONS.get(recipe["category"], recipe["category"])
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
    except Exception as e:
        logger.error(f"Error in button_callback: {e}", exc_info=True)
        try:
            await query.edit_message_text("Произошла ошибка при обработке запроса. Попробуйте позже.")
        except:
            pass


# ============================================================
# Main Application
# ============================================================

def main() -> None:
    """Start the bot with health check server."""
    if not BOT_TOKEN:
        logger.error("No BOT_TOKEN found in environment variables!")
        return
    
    # Start health check server in background thread
    health_thread = threading.Thread(target=start_health_check_server, daemon=True)
    health_thread.start()
    logger.info("Health check server thread started")
    
    # Create application using Application.builder() pattern (PTB 20+)
    # This is the recommended pattern for python-telegram-bot 20+
    application = Application.builder().token(BOT_TOKEN).build()
    logger.info(f"Application created: {application}")
    logger.info(f"Error handlers before adding: {application.error_handlers}")
    
    # Define error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("Error handler called")
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Add error handler
    application.add_error_handler(error_handler)
    logger.info(f"Error handlers after adding: {application.error_handlers}")
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("choose_dish", choose_dish_command))
    application.add_handler(CommandHandler("filter", filter_command))
    
    # Add callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^filter_"))
    
    # Add message handler for keyboard buttons
    application.add_handler(MessageHandler(filters.Regex("🥩 Мясо"), lambda u, c: filter_recipes_by_keyword(u, c, "meat")))
    application.add_handler(MessageHandler(filters.Regex("🥦 Гарнир"), lambda u, c: filter_recipes_by_keyword(u, c, "garnish")))
    application.add_handler(MessageHandler(filters.Regex("🎲 Случайное"), lambda u, c: choose_dish_command(u, c)))
    application.add_handler(MessageHandler(filters.Regex("➕ Добавить рецепт"), lambda u, c: add_recipe_manual(u, c)))
    application.add_handler(MessageHandler(filters.Regex("❓ Помощь"), lambda u, c: help_command(u, c)))
    
    # Start the bot with polling mode
    logger.info("Starting bot with polling mode...")
    
    # Run polling - this will handle the event loop internally
    application.run_polling(drop_pending_updates=True, timeout=20)


if __name__ == "__main__":
    main()