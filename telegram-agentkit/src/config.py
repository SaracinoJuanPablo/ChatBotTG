# src/config.py
# Configuración centralizada del bot.
# VERSIÓN CORREGIDA — path de knowledge anclado explícitamente.

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("agentkit")


class Config:
    """
    Singleton de configuración.
    Lee el .env una sola vez y expone todo como atributos tipados.
    Si falta alguna variable crítica, lanza un error claro al iniciar
    (no en producción cuando ya es tarde).
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        self._load()
        self._loaded = True

    def _load(self):
        # ── Telegram ──────────────────────────────────────────────
        self.telegram_token: str = self._require("TELEGRAM_BOT_TOKEN")

        # ID del admin que puede usar comandos de escritura (/agregar, /eliminar, /editar).
        # Debe ser un entero (tu user_id de Telegram).
        self.admin_user_id: int = int(self._require("ADMIN_USER_ID"))

        # ── Groq (LLM principal — gratuito, tool calling nativo) ─
        self.groq_api_key: str = self._require("GROQ_API_KEY")

        # Keys anteriores quedan opcionales para no romper .env viejos
        self.gemini_api_key: str     = os.getenv("GEMINI_API_KEY", "")
        self.openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
        self.llm_model: str          = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        self.app_name: str           = os.getenv("APP_NAME", "telegram-agentkit")
        self.app_url: str            = os.getenv("APP_URL", "http://localhost")

        # ── Memoria conversacional ────────────────────────────────
        # Cuántos mensajes del historial se envían al LLM en cada turno.
        # Más = más contexto, más tokens consumidos.
        self.max_history: int = int(os.getenv("MAX_HISTORY", "20"))

        # ── Paths ─────────────────────────────────────────────────
        # ESTRATEGIA DE PATH (en orden de prioridad):
        #
        # 1. Si KNOWLEDGE_DIR está en el .env → lo usamos directamente.
        #    Es el más explícito y nunca falla.
        #    Ejemplo en .env:
        #      KNOWLEDGE_DIR=C:\Users\JSaracino\telegram-agentkit\data\knowledge
        #
        # 2. Si no, calculamos relativo a este archivo (src/config.py).
        #    __file__ = C:\Users\JSaracino\telegram-agentkit\src\config.py
        #    .parent   = C:\Users\JSaracino\telegram-agentkit\src\
        #    .parent   = C:\Users\JSaracino\telegram-agentkit\       ← raíz
        #    / "data" / "knowledge" = ...telegram-agentkit\data\knowledge

        knowledge_dir_env = os.getenv("KNOWLEDGE_DIR")

        if knowledge_dir_env:
            # Opción 1: path explícito desde .env — el más seguro
            self.knowledge_dir = Path(knowledge_dir_env)
        else:
            # Opción 2: calculado relativo a src/config.py
            # .resolve() convierte a path absoluto real, sin ambigüedad
            self.knowledge_dir = Path(__file__).resolve().parent.parent / "data" / "knowledge"

        # El resto de los paths se derivan de knowledge_dir
        self.data_dir           = self.knowledge_dir.parent
        self.backups_dir        = self.knowledge_dir / "backups"
        self.conversations_file = self.data_dir / "conversations.json"
        self.log_file           = self.data_dir / "bot.log"

        # ── Límites y seguridad ───────────────────────────────────
        self.max_file_size_bytes: int = int(os.getenv("MAX_FILE_SIZE_KB", "100")) * 1024
        self.allowed_extensions: set  = {".txt", ".md", ".xlsx", ".xlsm", ".docx"}

        # ── Crear directorios si no existen ──────────────────────
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        # ── LOG DE DIAGNÓSTICO ────────────────────────────────────
        # Se imprime cada vez que arranca el bot.
        # Confirmá visualmente que los paths apuntan a donde esperás.
        print("=" * 60)
        print(f"[CONFIG] knowledge_dir  : {self.knowledge_dir}")
        print(f"[CONFIG] dir existe     : {self.knowledge_dir.exists()}")
        print(f"[CONFIG] conversations  : {self.conversations_file}")
        print(f"[CONFIG] log file       : {self.log_file}")
        print("=" * 60)

    @staticmethod
    def _require(key: str) -> str:
        """
        Lee una variable de entorno obligatoria.
        Si no existe, lanza un ValueError claro antes de que el bot arranque.
        Mucho mejor que un KeyError misterioso en producción.
        """
        value = os.getenv(key)
        if not value:
            raise ValueError(
                f"Variable de entorno requerida no encontrada: {key}\n"
                f"Revisá tu archivo .env y asegurate de que '{key}' esté definida."
            )
        return value


# Instancia global — importar esto en cada módulo en lugar de crear instancias nuevas.
# Uso: from src.config import config
config = Config()