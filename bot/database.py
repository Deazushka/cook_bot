#!/usr/bin/env python3
"""
Database module for the Cooking Telegram Bot.
Handles all data storage operations using SQLite (local) or PostgreSQL (production).
"""

import os
import logging
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Database:
    """Database handler for the cooking bot using SQLite."""
    
    def __init__(self):
        self.conn = None
        self._connect()
    
    def _connect(self):
        """Establish SQLite database connection."""
        # Determine the database path:
        # 1. If DATABASE_URL is a full SQLite path, use it directly.
        # 2. Otherwise, place the DB inside DATA_DIR (so it can live on the
        #    persistent disk/volume mounted by the hosting platform).
        db_path = os.getenv("DATABASE_URL")
        
        if not db_path or db_path.startswith("postgresql"):
            # Fall back to SQLite in the data directory
            data_dir = os.getenv("DATA_DIR", "data")
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "cooking_bot.db")
        
        try:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row  # Enable dict-like access
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            logger.info(f"SQLite database connected: {db_path}")
            self._create_tables()
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def _create_tables(self):
        """Create all necessary tables if they don't exist."""
        cursor = self.conn.cursor()
        try:
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username VARCHAR(128),
                    language VARCHAR(10) DEFAULT 'ru',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Recipe categories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(50) UNIQUE NOT NULL,
                    description TEXT
                )
            """)
            
            # Recipes table - stores recipes from source and user-added
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title VARCHAR(255) NOT NULL,
                    source_url TEXT,
                    category VARCHAR(50) DEFAULT 'all',
                    source VARCHAR(50) DEFAULT 'user_added',
                    ingredients TEXT,
                    cooking_time INTEGER DEFAULT 0,
                    added_by INTEGER,
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    times_cooked INTEGER DEFAULT 0
                )
            """)
            
            # User-recipe junction table - tracks which user saved which recipe
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_recipes (
                    user_id INTEGER REFERENCES users(user_id),
                    recipe_id INTEGER REFERENCES recipes(id),
                    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    times_cooked INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, recipe_id)
                )
            """)
            
            # Cooking history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cooking_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(user_id),
                    recipe_id INTEGER REFERENCES recipes(id),
                    cooked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    rating INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            
            # Insert default categories if not exist
            cursor.execute("""
                INSERT INTO categories (name, description) VALUES
                    ('meat', 'Мясные блюда: говядина, свинина, курица, баранина'),
                    ('garnish', 'Гарниры: картофель, рис, гречка, макароны'),
                    ('vegetarian', 'Вегетарианские блюда'),
                    ('soup', 'Супы и первые блюда'),
                    ('dessert', 'Десерты и сладкие блюда'),
                    ('all', 'Все рецепты')
                ON CONFLICT (name) DO NOTHING
            """)
        finally:
            cursor.close()
        
        self.conn.commit()
        logger.info("Database tables created/verified")
    
    def ensure_user(self, user_id: int, username: str = "unknown") -> None:
        """Ensure user exists in database, insert if not."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username)
            )
    
    def upsert_recipe(self, title: str, source_url: Optional[str], category: str, 
                      source: str = "iamcook.ru", ingredients: Optional[str] = None,
                      cooking_time: Optional[int] = None, added_by: Optional[int] = None) -> int:
        """Insert or update a recipe, return recipe id."""
        with self.conn:
            # Check if recipe already exists by title and source
            existing = self.conn.execute(
                "SELECT id FROM recipes WHERE title = ? AND source_url = ? AND source = ?",
                (title, source_url, source)
            ).fetchone()
            
            if existing:
                recipe_id = existing[0]
                # Update times_cooked
                self.conn.execute(
                    "UPDATE recipes SET times_cooked = times_cooked + 1 WHERE id = ?",
                    (recipe_id,)
                )
                return recipe_id
            else:
                self.conn.execute(
                    """INSERT INTO recipes 
                       (title, source_url, category, source, ingredients, cooking_time, added_by) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (title, source_url, category, source, ingredients, cooking_time, added_by)
                )
                recipe_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                return recipe_id
    
    def get_recipes_by_category(self, category: str = "all") -> List[Dict[str, Any]]:
        """Get recipes filtered by category."""
        category_map = {
            "all": "all",
            "meat": "meat", 
            "garnish": "garnish",
            "vegetarian": "vegetarian",
            "soup": "soup",
            "dessert": "dessert"
        }
        norm_category = category_map.get(category.lower(), "all")
        
        rows = self.conn.execute(
            "SELECT * FROM recipes WHERE category = ? ORDER BY RANDOM() LIMIT 10",
            (norm_category,)
        ).fetchall()
        
        return [dict(row) for row in rows] if rows else []
    
    def get_user_recipes(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all recipes saved by a specific user."""
        rows = self.conn.execute("""
            SELECT r.*, ur.times_cooked as user_times 
            FROM recipes r 
            JOIN user_recipes ur ON r.id = ur.recipe_id 
            WHERE ur.user_id = ? 
            ORDER BY ur.saved_at DESC
        """, (user_id,)).fetchall()
        
        return [dict(row) for row in rows] if rows else []
    
    def save_recipe_for_user(self, user_id: int, recipe_id: int) -> bool:
        """Save a recipe for a user (add to their collection)."""
        try:
            self.conn.execute(
                """INSERT INTO user_recipes (user_id, recipe_id) 
                   VALUES (?, ?) 
                   ON CONFLICT (user_id, recipe_id) 
                   DO UPDATE SET saved_at = CURRENT_TIMESTAMP""",
                (user_id, recipe_id)
            )
            return True
        except Exception as e:
            logger.error(f"Error saving recipe for user: {e}")
            return False
    
    def record_cooking(self, user_id: int, recipe_id: int, rating: int = 0, notes: str = "") -> int:
        """Record that a user cooked a recipe."""
        try:
            cursor = self.conn.execute(
                """INSERT INTO cooking_history (user_id, recipe_id, rating, notes) 
                   VALUES (?, ?, ?, ?)""",
                (user_id, recipe_id, rating, notes)
            )
            history_id = cursor.lastrowid
            
            # Update times_cooked in user_recipes
            self.conn.execute(
                "UPDATE user_recipes SET times_cooked = times_cooked + 1 WHERE user_id = ? AND recipe_id = ?",
                (user_id, recipe_id)
            )
            return history_id
        except Exception as e:
            logger.error(f"Error recording cooking: {e}")
            return 0
    
    def search_recipes(self, query: str, category: str = "all") -> List[Dict[str, Any]]:
        """Search recipes by keyword in title."""
        category_map = {
            "all": "all",
            "meat": "meat", 
            "garnish": "garnish",
            "vegetarian": "vegetarian",
            "soup": "soup",
            "dessert": "dessert"
        }
        norm_category = category_map.get(category.lower(), "all")
        search_query = f"%{query}%"
        
        rows = self.conn.execute(
            """SELECT * FROM recipes 
               WHERE (title LIKE ? OR ingredients LIKE ?) 
               AND (? = 'all' OR category = ?) 
               ORDER BY RANDOM() LIMIT 10""",
            (search_query, search_query, norm_category, norm_category)
        ).fetchall()
        
        return [dict(row) for row in rows] if rows else []
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("SQLite database connection closed")