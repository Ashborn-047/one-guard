import os
import sys
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


@dataclass(frozen=True)
class Config:
    # Execution Mode
    mode: str = os.getenv("ONEGUARD_MODE", "sandbox").strip().lower()
    
    # Binance Exchange Credentials
    api_key: str = os.getenv("BINANCE_API_KEY", "").strip()
    secret_key: str = os.getenv("BINANCE_SECRET_KEY", "").strip()
    
    # Telegram Notifications Credentials
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    
    # Local Storage Database
    db_file: str = os.getenv("DATABASE_FILE", "one_guard_trading.db").strip()
    
    # Safety Override Guard
    emergency_halt: bool = os.getenv("EMERGENCY_HALT", "FALSE").strip().upper() == "TRUE"

    @property
    def is_sandbox(self) -> bool:
        return self.mode == "sandbox"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def db_path(self) -> Path:
        return BASE_DIR / self.db_file

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
