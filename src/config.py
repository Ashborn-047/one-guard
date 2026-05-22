import os
import sys
from typing import Any
import logging
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("OneGuard.Config")

# Root directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    logger.info(f"Loaded configuration from environment file: {env_path}")
else:
    logger.warning(f"No .env file found at {env_path}. Defaulting to system environment variables.")


class Config:
    def __init__(self):
        # Static settings (always read from env or defaults)
        self.api_key: str = os.getenv("BINANCE_API_KEY", "").strip()
        self.secret_key: str = os.getenv("BINANCE_SECRET_KEY", "").strip()
        self.telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.db_file: str = os.getenv("DATABASE_FILE", "one_guard_trading.db").strip()

    @property
    def db_path(self) -> Path:
        return BASE_DIR / self.db_file

    def _get_db_value(self, key: str, default: Any) -> Any:
        # Check if value has been overridden in instance dictionary (e.g. via object.__setattr__ in tests)
        if key in self.__dict__:
            return self.__dict__[key]

        import sqlite3
        db_path = self.db_path
        if not db_path.exists():
            return default
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cursor = conn.cursor()
            # Verify if table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_config'")
            if not cursor.fetchone():
                conn.close()
                return default
            cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
            row = cursor.fetchone()
            conn.close()
            if row:
                val = row[0]
                if isinstance(default, bool):
                    return val.strip().upper() in ("TRUE", "1", "YES")
                elif isinstance(default, int):
                    return int(val)
                elif isinstance(default, float):
                    return float(val)
                return val
        except Exception:
            pass
        return default

    def _set_db_value(self, key: str, value: Any) -> bool:
        # Update instance dictionary as well to keep in-sync if modified (and for tests)
        self.__dict__[key] = value

        import sqlite3
        db_path = self.db_path
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cursor.execute(
                "INSERT INTO system_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to set database configuration key '{key}' to '{value}': {e}")
            return False

    @property
    def mode(self) -> str:
        return self._get_db_value("mode", os.getenv("ONEGUARD_MODE", "sandbox").strip().lower())

    @mode.setter
    def mode(self, val: str):
        self._set_db_value("mode", val)

    @property
    def is_sandbox(self) -> bool:
        return self.mode == "sandbox"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def emergency_halt(self) -> bool:
        return self._get_db_value("emergency_halt", os.getenv("EMERGENCY_HALT", "FALSE").strip().upper() == "TRUE")

    @emergency_halt.setter
    def emergency_halt(self, val: bool):
        self._set_db_value("emergency_halt", val)

    @property
    def max_position_size(self) -> float:
        return self._get_db_value("max_position_size", float(os.getenv("MAX_POSITION_SIZE", "10.0").strip()))

    @max_position_size.setter
    def max_position_size(self, val: float):
        self._set_db_value("max_position_size", val)

    @property
    def max_open_trades(self) -> int:
        return self._get_db_value("max_open_trades", int(os.getenv("MAX_OPEN_TRADES", "3").strip()))

    @max_open_trades.setter
    def max_open_trades(self, val: int):
        self._set_db_value("max_open_trades", val)

    @property
    def weekly_drawdown_limit(self) -> float:
        return self._get_db_value("weekly_drawdown_limit", float(os.getenv("WEEKLY_DRAWDOWN_LIMIT", "15.0").strip()))

    @weekly_drawdown_limit.setter
    def weekly_drawdown_limit(self, val: float):
        self._set_db_value("weekly_drawdown_limit", val)

    @property
    def loss_cooldown_minutes(self) -> int:
        return self._get_db_value("loss_cooldown_minutes", int(os.getenv("LOSS_COOLDOWN_MINUTES", "30").strip()))

    @loss_cooldown_minutes.setter
    def loss_cooldown_minutes(self, val: int):
        self._set_db_value("loss_cooldown_minutes", val)

    def validate(self) -> bool:
        """
        Validates the configuration properties.
        Returns:
            bool: True if configuration is valid and safe, False otherwise.
        """
        logger.info("Initializing system validation checks...")
        
        # 1. Validate Mode
        if self.mode not in ("sandbox", "live"):
            logger.error(
                f"CRITICAL: Invalid ONEGUARD_MODE set to '{self.mode}'. "
                "Must be either 'sandbox' or 'live'."
            )
            return False

        # 2. Check Emergency Halt
        if self.emergency_halt:
            logger.warning(
                "SAFETY GUARD TRIGGERED: EMERGENCY_HALT is set to TRUE in configuration. "
                "All trading executions are suspended."
            )

        # 3. Check Exchange Credentials
        if not self.api_key or not self.secret_key:
            logger.warning(
                "CREDENTIAL WARNING: Binance API Key or Secret Key is missing from configuration. "
                "Bot execution will fail on authenticated endpoints (like placing orders)."
            )
        else:
            logger.info("Binance API credentials loaded successfully.")

        # 4. Check Telegram Config
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning(
                "TELEMETRY WARNING: Telegram Bot Token or Chat ID is missing. "
                "Push alert notifications will be disabled."
            )
        else:
            logger.info("Telegram telemetry config loaded successfully.")

        # 5. Check database file extension
        if not self.db_file.endswith((".db", ".sqlite", ".sqlite3")):
            logger.warning(
                f"DATABASE WARNING: Database file '{self.db_file}' does not use a typical extension (.db, .sqlite)."
            )

        logger.info(f"System validation complete. Mode: {self.mode.upper()}")
        return True


# Global configuration instance
settings = Config()

if __name__ == "__main__":
    # Test validation when run directly
    success = settings.validate()
    print(f"\nConfiguration Validated: {success}")
    print(f"Database Path: {settings.db_path}")
    print(f"Sandbox Mode Active: {settings.is_sandbox}")
    print(f"Emergency Halt Active: {settings.emergency_halt}")
