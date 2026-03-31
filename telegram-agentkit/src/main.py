# src/main.py
# Punto de entrada del bot de Telegram.
#
# DIFERENCIA con el repo whatsapp-agentkit (main.py original):
#   El original levanta un servidor FastAPI que escucha webhooks HTTP.
#   Requiere URL pública (Railway, ngrok, etc.) para recibir eventos de WhatsApp.
#
#   Este main.py usa polling de python-telegram-bot:
#   - El bot pregunta a Telegram cada N segundos si hay mensajes nuevos
#   - No necesita URL pública ni configuración de webhook
#   - Ideal para desarrollo local y bots de uso interno/personal
#   - Para producción se puede migrar a webhook con un cambio mínimo
#     (reemplazar run_polling() por run_webhook())
#
# FLUJO DE ARRANQUE:
#   1. Cargar config (valida .env, crea directorios)
#   2. Configurar logging (archivo + consola)
#   3. Verificar que los módulos core carguen sin error
#   4. Construir Application de Telegram
#   5. Registrar handlers
#   6. Iniciar polling

import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram.ext import Application

# La importación de config dispara la validación del .env.
# Si falta alguna variable crítica, falla aquí con mensaje claro
# antes de llegar a Telegram.
from src.config import config


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def configurar_logging():
    """
    Configura logging con dos destinos:
    - Consola (stdout): nivel INFO, formato compacto
    - Archivo rotativo (data/bot.log): nivel DEBUG, formato completo

    RotatingFileHandler limita el archivo a 5MB y mantiene 3 backups,
    así bot.log no crece indefinidamente.
    """
    log_format_consola = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_format_archivo = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Handler de consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format_consola, datefmt=date_format))

    # Handler de archivo rotativo
    config.data_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format_archivo, datefmt=date_format))

    # Logger raíz — captura todo
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Reducir verbosidad de librerías externas
    # (python-telegram-bot y httpx son muy verbosos en DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ─────────────────────────────────────────────
# VERIFICACIÓN DE MÓDULOS
# ─────────────────────────────────────────────

def verificar_modulos() -> bool:
    """
    Importa los módulos core y verifica que carguen sin error.
    Si knowledge_dir está vacío, avisa pero no bloquea el arranque.

    Returns:
        True si todo está OK, False si hay un error crítico.
    """
    logger = logging.getLogger("agentkit.startup")

    try:
        from src.agent.knowledge import knowledge_base
        archivos = knowledge_base.listar_archivos()
        if not archivos:
            logger.warning(
                "knowledge base vacía — el bot responderá que no tiene información.\n"
                f"  Agregá archivos .txt o .md en: {config.knowledge_dir}"
            )
        else:
            logger.info(f"Knowledge base OK — {len(archivos)} archivo(s): {archivos}")
    except Exception as e:
        logger.error(f"Error cargando knowledge base: {e}")
        return False

    try:
        from src.agent.memory import memory
        stats = memory.stats()
        logger.info(
            f"Memory OK — {stats['conversaciones_activas']} conversación(es) "
            f"en historial"
        )
    except Exception as e:
        logger.error(f"Error cargando memory: {e}")
        return False

    try:
        from src.agent.brain import generar_respuesta  # noqa: F401
        logger.info(f"Brain OK — modelo: {config.llm_model}")
    except Exception as e:
        logger.error(f"Error cargando brain: {e}")
        return False

    return True


# ─────────────────────────────────────────────
# CONSTRUCCIÓN DE LA APP
# ─────────────────────────────────────────────

def construir_app() -> Application:
    """
    Construye la Application de python-telegram-bot.

    Parámetros de la Application:
    - token: el TELEGRAM_BOT_TOKEN del .env
    - connect_timeout / read_timeout: segundos antes de dar error de red.
      30s es generoso — suficiente para conexiones lentas.
    - pool_timeout: tiempo máximo esperando un worker del pool de conexiones.
    """
    logger = logging.getLogger("agentkit.startup")

    app = (
        Application.builder()
        .token(config.telegram_token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Registrar todos los handlers (comandos + mensajes de texto)
    from src.bot.handlers import registrar_handlers
    registrar_handlers(app)

    logger.info("Application de Telegram construida")
    return app


# ─────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────

def main():
    """
    Arranca el bot en modo polling.

    Modo polling vs webhook:
    - Polling: el bot consulta a Telegram cada pocos segundos.
      Sin infraestructura extra. Ideal para desarrollo y bots internos.
    - Webhook: Telegram envía eventos HTTP a una URL pública.
      Más eficiente. Requiere servidor con HTTPS (Railway, VPS, etc.)

    Para migrar a webhook en el futuro, reemplazar:
        app.run_polling(...)
    por:
        app.run_webhook(
            listen="0.0.0.0",
            port=8000,
            webhook_url="https://tu-dominio.com/webhook"
        )
    """
    # 1. Logging primero — necesitamos logs desde el primer momento
    configurar_logging()
    logger = logging.getLogger("agentkit")

    logger.info("=" * 50)
    logger.info("  Telegram AgentKit arrancando...")
    logger.info("=" * 50)
    logger.info(f"Modelo LLM:      {config.llm_model}")
    logger.info(f"Admin user ID:   {config.admin_user_id}")
    logger.info(f"Knowledge dir:   {config.knowledge_dir}")
    logger.info(f"Max history:     {config.max_history} mensajes")
    logger.info(f"Log file:        {config.log_file}")

    # 2. Verificar que los módulos core carguen sin error
    logger.info("Verificando módulos...")
    if not verificar_modulos():
        logger.error("Fallo en la verificación de módulos. El bot no arrancará.")
        sys.exit(1)

    # 3. Construir la Application de Telegram
    logger.info("Construyendo Application de Telegram...")
    app = construir_app()

    # 4. Iniciar polling
    logger.info("Bot iniciado en modo polling. Esperando mensajes...")
    logger.info("Presioná CTRL+C para detener.")
    logger.info("=" * 50)

    app.run_polling(
        # Tipos de updates que el bot va a procesar.
        # Por defecto procesa todo, pero acá limitamos a mensajes
        # y edited_messages para no recibir eventos innecesarios.
        allowed_updates=["message", "edited_message"],

        # Si hay updates acumulados de cuando el bot estaba apagado,
        # drop_pending_updates=True los ignora y arranca limpio.
        # Útil durante desarrollo para no procesar mensajes viejos.
        drop_pending_updates=True,

        # Cada cuántos segundos loguear que el bot sigue vivo.
        # 0 = desactivado (para no spamear el log en producción)
        poll_interval=0,
    )

    # Cuando run_polling() termina (CTRL+C o señal), llega acá
    logger.info("Bot detenido correctamente.")


if __name__ == "__main__":
    main()